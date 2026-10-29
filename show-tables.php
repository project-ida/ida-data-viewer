<?php

// How "fresh" is fresh (row coloring): green if last timestamp is within threshold, red otherwise.
$thresholdSec = 90 * 60;

require_once __DIR__ . '/viewer-config.php';
if (($_SERVER['REQUEST_METHOD'] ?? '') === 'POST') { require_same_origin_post(); }
date_default_timezone_set(viewer_config()['viewer']['display_timezone']);

$debug = isset($_GET['debug']);
if ($debug) {
    ini_set('display_errors', '1');
    ini_set('display_startup_errors', '1');
    error_reporting(E_ALL);
}

function _env_get($key, $default = null) {
    $val = getenv($key);
    return ($val !== false && $val !== null && $val !== '') ? $val : $default;
}

function _conninfo_escape($value) {
    $s = (string)$value;
    $s = str_replace("\\", "\\\\", $s);
    $s = str_replace("'", "\\'", $s);
    return "'" . $s . "'";
}

function _cache_read_json($path) {
    if (!is_readable($path)) return ['generated_at' => 0, 'entries' => []];
    $raw = file_get_contents($path);
    if ($raw === false || $raw === '') return ['generated_at' => 0, 'entries' => []];
    $data = json_decode($raw, true);
    if (!is_array($data)) return ['generated_at' => 0, 'entries' => []];
    if (!isset($data['entries']) || !is_array($data['entries'])) $data['entries'] = [];
    if (!isset($data['generated_at'])) $data['generated_at'] = 0;
    return $data;
}

function _cache_write_json_atomic($path, $data) {
    $dir = dirname($path);
    if (!is_dir($dir) || !is_writable($dir)) return false;

    $tmp = $path . '.tmp.' . getmypid();
    $raw = json_encode($data, JSON_UNESCAPED_SLASHES);
    if ($raw === false) return false;

    if (file_put_contents($tmp, $raw, LOCK_EX) === false) return false;
    return rename($tmp, $path);
}

function _html($s) { return htmlspecialchars((string)$s, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8'); }

function _read_list_file($path) {
    if (!is_readable($path)) return [];
    $lines = file($path, FILE_IGNORE_NEW_LINES);
    if (!$lines) return [];
    $out = [];
    foreach ($lines as $line) {
        $line = trim($line);
        if ($line === '' || strpos($line, '#') === 0) continue;
        $out[] = $line;
    }
    return $out;
}

function _write_list_file_atomic($path, $lines) {
    $dir = dirname($path);
    if (!is_dir($dir) || !is_writable($dir)) return false;

    $tmp = $path . '.tmp.' . getmypid();
    $raw = implode("\n", $lines) . "\n";
    if (file_put_contents($tmp, $raw, LOCK_EX) === false) return false;
    return rename($tmp, $path);
}

function _redirect_self_preserve() {
    $keepKeys = ['debug', 'format', 'force_refresh', 'cache_ttl_sec', 'max_refresh',
        'error_backoff_sec', 'time_budget_sec', 'timeout_ms', 'batch_size'];
    $qs = [];
    foreach ($keepKeys as $k) {
        if (isset($_GET[$k])) $qs[$k] = (string)$_GET[$k];
    }
    $url = $_SERVER['PHP_SELF'];
    if (!empty($qs)) $url .= '?' . http_build_query($qs);
    header('Location: ' . $url, true, 303);
    exit;
}

// ------------------------- Modes / output -------------------------

$isWarm = isset($_GET['warm']);
if ($isWarm) {
    $expected = _env_get('SHOW_TABLES_WARM_TOKEN', '');
    if ($expected !== '') {
        $got = $_GET['token'] ?? ($_SERVER['HTTP_X_SHOW_TABLES_TOKEN'] ?? '');
        if (!is_string($got) || $got !== $expected) {
            http_response_code(403);
            echo "Forbidden\n";
            exit;
        }
    }
}

// Allow GET-based overrides (timeout/budget/ttl/etc) even when debug=0,
// when explicitly requested via force_refresh=1 (or warm=1).
// This fixes the "include() sets $_GET but page is still stale" issue.
$forceRefresh = isset($_GET['force_refresh']);
$allowOverrides = $debug || $isWarm || $forceRefresh;

$format = isset($_GET['format']) ? strtolower((string)$_GET['format']) : 'html';
if (!in_array($format, ['html', 'json'], true)) $format = 'html';

// ------------------------- Archived & Inactive tables -------------------------

$archivedTablesFilePath = __DIR__ . DIRECTORY_SEPARATOR . 'archived_tables.txt';
$archivedTables = _read_list_file($archivedTablesFilePath);
$archivedLookup = array_fill_keys($archivedTables, true);

$inactiveTablesFilePath = __DIR__ . DIRECTORY_SEPARATOR . 'inactive_tables.txt';
$inactiveTables = _read_list_file($inactiveTablesFilePath);
$inactiveLookup = array_fill_keys($inactiveTables, true);

// ------------------------- DB connection -------------------------

// Use the same project credentials file as the Python generator.
$creds = viewer_database_credentials();
$missing = [];
foreach (['PGHOST', 'PGPORT', 'PGDATABASE', 'PGUSER', 'PGPASSWORD'] as $key) {
    // An explicitly empty password is valid for passwordless authentication.
    if (!array_key_exists($key, $creds) || ($key !== 'PGPASSWORD' && trim($creds[$key]) === '')) {
        $missing[] = $key;
    }
}
if ($missing) {
    http_response_code(503);
    $message = 'Database configuration unavailable. Check that psql_credentials.py is readable and defines: '
        . implode(', ', $missing) . '.';
    if ($format === 'json') {
        header('Content-Type: application/json');
        exit(json_encode(['error' => $message]));
    }
    exit('<p>' . _html($message) . '</p>');
}

$pgHost = $creds['PGHOST'];
$pgPort = $creds['PGPORT'];
$pgDb   = $creds['PGDATABASE'];
$pgUser = $creds['PGUSER'];
$pgPass = $creds['PGPASSWORD'];

$conninfo =
    "host=" . _conninfo_escape($pgHost) . " " .
    "port=" . _conninfo_escape($pgPort) . " " .
    "dbname=" . _conninfo_escape($pgDb) . " " .
    "user=" . _conninfo_escape($pgUser) . " " .
    "password=" . _conninfo_escape($pgPass) . " " .
    "connect_timeout=3";

$db = pg_connect($conninfo);
if (!$db) {
    http_response_code(500);
    exit;
}

@pg_query($db, "SET lock_timeout = '3s'");

// Defaults
$statementTimeoutMs = (int)_env_get('PG_STATEMENT_TIMEOUT_MS', $isWarm ? 30000 : 5000);
$timeBudgetSec      = (float)_env_get('SHOW_TABLES_TIME_BUDGET_SEC', $isWarm ? 120 : ($debug ? 20 : 3));
$cacheFile          = _env_get('SHOW_TABLES_CACHE_FILE', rtrim(sys_get_temp_dir(), DIRECTORY_SEPARATOR) . DIRECTORY_SEPARATOR . 'ida_show_tables_cache.json');
$cacheTtlSec        = (int)_env_get('SHOW_TABLES_CACHE_TTL_SEC', $isWarm ? 0 : 300);
$errorBackoffSec    = (int)_env_get('SHOW_TABLES_ERROR_BACKOFF_SEC', 300);
$maxRefreshPerRequest = (int)_env_get('SHOW_TABLES_MAX_REFRESH_PER_REQUEST', $isWarm ? 1000000 : 15);
$batchSize          = (int)_env_get('SHOW_TABLES_BATCH_SIZE', 20);

// Honor GET overrides when allowed (debug OR warm OR force_refresh)
if ($allowOverrides && isset($_GET['timeout_ms']))         $statementTimeoutMs = (int)$_GET['timeout_ms'];
if ($allowOverrides && isset($_GET['time_budget_sec']))    $timeBudgetSec = (float)$_GET['time_budget_sec'];
if ($allowOverrides && isset($_GET['cache_ttl_sec']))      $cacheTtlSec = (int)$_GET['cache_ttl_sec'];
if ($allowOverrides && isset($_GET['error_backoff_sec']))  $errorBackoffSec = (int)$_GET['error_backoff_sec'];
if ($allowOverrides && isset($_GET['max_refresh']))        $maxRefreshPerRequest = (int)$_GET['max_refresh'];
if ($allowOverrides && isset($_GET['batch_size']))         $batchSize = (int)$_GET['batch_size'];

// Clamp
$statementTimeoutMs = max(500, min(60000, $statementTimeoutMs));
$timeBudgetSec = max(0.5, min(300, $timeBudgetSec));
$cacheTtlSec = max(0, min(3600, $cacheTtlSec));
$errorBackoffSec = max(0, min(3600, $errorBackoffSec));
$maxRefreshPerRequest = max(0, min(1000000, $maxRefreshPerRequest));
$batchSize = max(1, min(200, $batchSize));

// Apply timeouts
@pg_query($db, "SET statement_timeout = '" . $statementTimeoutMs . "ms'");

@pg_query($db, "SET timescaledb.enable_ordered_append = on");
@pg_query($db, "SET timescaledb.enable_chunk_append = on");

// Cache load
$cache = _cache_read_json($cacheFile);
$cacheDirty = false;

$startWall = microtime(true);
$now = time();

// ------------------------- Schema privilege check -------------------------

$schemaPrivRes = @pg_query($db, "SELECT has_schema_privilege(current_user, 'public', 'USAGE') AS ok;");
$canUsePublic = false;
if ($schemaPrivRes) {
    $ok = pg_fetch_result($schemaPrivRes, 0, 0);
    $canUsePublic = ($ok === 't' || $ok === true || $ok === 1 || $ok === '1');
}
if (!$canUsePublic) {
    http_response_code(403);
    if ($format === 'json') {
        header('Content-Type: application/json');
        echo json_encode(['error' => 'No USAGE on schema public'], JSON_UNESCAPED_SLASHES);
    } else {
        echo "DB user lacks USAGE on schema public\n";
    }
    exit;
}

// ------------------------- Discover tables -------------------------

$tablesSql = "
SELECT
    c.relname AS table_name,
    t.typname AS time_typname,
    has_table_privilege(current_user, quote_ident(n.nspname) || '.' || quote_ident(c.relname), 'SELECT') AS can_select
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
LEFT JOIN pg_attribute a
    ON a.attrelid = c.oid
    AND a.attname = 'time'
    AND a.attnum > 0
    AND NOT a.attisdropped
LEFT JOIN pg_type t ON t.oid = a.atttypid
WHERE n.nspname = 'public'
  AND c.relkind = 'r'
ORDER BY c.relname ASC;
";

$tablesRes = @pg_query($db, $tablesSql);
$tables = $tablesRes ? (pg_fetch_all($tablesRes) ?: []) : [];

$allTableNames = [];
foreach ($tables as $r) $allTableNames[] = $r['table_name'];
$allTableSet = array_fill_keys($allTableNames, true);

// ------------------------- Handle toggle actions (POST) -------------------------

$actionError = null;
if ($_SERVER['REQUEST_METHOD'] === 'POST' && isset($_POST['action'], $_POST['table'])) {
    $action = (string)$_POST['action'];
    $table = (string)$_POST['table'];

    if (!preg_match('/^[A-Za-z0-9_]+$/', $table) || !isset($allTableSet[$table])) {
        $actionError = "Invalid table.";
    } else {
        // re-read lists
        $inactiveTables = _read_list_file($inactiveTablesFilePath);
        $inactiveLookup = array_fill_keys($inactiveTables, true);

        $archivedTables = _read_list_file($archivedTablesFilePath);
        $archivedLookup = array_fill_keys($archivedTables, true);

        if ($action === 'make_inactive') {
            $inactiveLookup[$table] = true;
        } elseif ($action === 'make_active') {
            unset($inactiveLookup[$table]);
        } elseif ($action === 'archive_table') {
            $archivedLookup[$table] = true;
            unset($inactiveLookup[$table]);
        } else {
            $actionError = "Invalid action.";
        }

        if ($actionError === null) {
            $ok = true;

            if ($action === 'archive_table') {
                $newArchived = array_keys($archivedLookup);
                sort($newArchived, SORT_STRING);
                if (!_write_list_file_atomic($archivedTablesFilePath, $newArchived)) $ok = false;

                $newInactive = array_keys($inactiveLookup);
                sort($newInactive, SORT_STRING);
                if (_write_list_file_atomic($inactiveTablesFilePath, $newInactive) === false) $ok = false;
            } else {
                $newInactive = array_keys($inactiveLookup);
                sort($newInactive, SORT_STRING);
                if (!_write_list_file_atomic($inactiveTablesFilePath, $newInactive)) $ok = false;
            }

            if (!$ok) {
                $actionError = "Failed to write inactive/archived txt file(s). Check folder permissions.";
            } else {
                // Keep cache when making inactive (so "Last known time" remains visible).
                // Only clear cache when re-activating or archiving.
                if ($action === 'make_active' || $action === 'archive_table') {
                    if (isset($cache['entries'][$table])) {
                        unset($cache['entries'][$table]);
                        $cache['generated_at'] = time();
                        _cache_write_json_atomic($cacheFile, $cache);
                    }
                }
                _redirect_self_preserve();
            }
        }
    }
}

// Refresh lists after possible action/redirect
$archivedTables = _read_list_file($archivedTablesFilePath);
$archivedLookup = array_fill_keys($archivedTables, true);

$inactiveTables = _read_list_file($inactiveTablesFilePath);
$inactiveLookup = array_fill_keys($inactiveTables, true);

// ------------------------- Filter tables into active/inactive (excluding archived) -------------------------

$activeRows = [];
$inactiveRows = [];

foreach ($tables as $row) {
    $name = $row['table_name'];
    if (isset($archivedLookup[$name])) continue;

    if (isset($inactiveLookup[$name])) $inactiveRows[] = $row;
    else $activeRows[] = $row;
}

// Dropdown mode: list active table names only (queried set)
if (isset($_GET['tables_dropdown'])) {
    foreach ($activeRows as $row) echo _html($row['table_name']) . "<br>";
    exit;
}

// ------------------------- Choose ACTIVE tables with usable time column -------------------------

$tablesToQuery = [];
foreach ($activeRows as $row) {
    $typ = $row['time_typname'] ?? '';
    $canSelect = ($row['can_select'] === 't' || $row['can_select'] === true || $row['can_select'] === 1 || $row['can_select'] === '1');
    if (!$canSelect) continue;
    if (in_array($typ, ['timestamp', 'timestamptz', 'date'], true)) $tablesToQuery[] = $row['table_name'];
}

$lastTimeByTable = [];
$cacheHits = 0;

// Pre-fill from cache (stale-while-revalidate)
$toFetch = [];
foreach ($tablesToQuery as $tableName) {
    $entry = $cache['entries'][$tableName] ?? null;
    $updatedAt = is_array($entry) ? (int)($entry['updated_at'] ?? 0) : 0;
    $last = is_array($entry) ? ($entry['last_time'] ?? null) : null;

    $errorAt = is_array($entry) ? (int)($entry['error_at'] ?? 0) : 0;
    if (!$isWarm && !$forceRefresh && $errorBackoffSec > 0 && $errorAt > 0 && ($now - $errorAt) < $errorBackoffSec) {
        if ($last !== null && $last !== '') $lastTimeByTable[$tableName] = $last;
        continue;
    }

    $isFresh = ($cacheTtlSec > 0) && ($updatedAt > 0) && (($now - $updatedAt) <= $cacheTtlSec);
    if ($isFresh && $last !== null && $last !== '') {
        $lastTimeByTable[$tableName] = $last;
        $cacheHits++;
        continue;
    }

    if ($last !== null && $last !== '') $lastTimeByTable[$tableName] = $last;
    $toFetch[] = $tableName;
}

if (!$isWarm && !$forceRefresh && $maxRefreshPerRequest >= 0 && count($toFetch) > $maxRefreshPerRequest) {
    $toFetch = array_slice($toFetch, 0, $maxRefreshPerRequest);
}

// ------------------------- Fetch latest timestamps in batches -------------------------

$errors = [];
$queue = array_values($toFetch);

while (count($queue) > 0) {
    if ((microtime(true) - $startWall) > $timeBudgetSec) break;

    $batch = array_slice($queue, 0, $batchSize);

    $unionParts = [];
    foreach ($batch as $tableName) {
        $tableIdent = pg_escape_identifier($db, $tableName);
        $tableLabel = pg_escape_literal($db, $tableName);
        $unionParts[] = "SELECT {$tableLabel} AS table_name, max(time)::text AS last_time FROM public.{$tableIdent}";
    }

    $sql = implode(" UNION ALL ", $unionParts) . ";";
    $res = @pg_query($db, $sql);

    if ($res) {
        $rows = pg_fetch_all($res) ?: [];
        foreach ($rows as $r) {
            $t = $r['table_name'];
            $v = $r['last_time'] ?? null;
            $lastTimeByTable[$t] = $v;

            $cache['entries'][$t] = [
                'last_time'  => $v,
                'updated_at' => $now,
            ];
            $cacheDirty = true;
        }
        $queue = array_slice($queue, count($batch));
        continue;
    }

    $err = pg_last_error($db);
    $isTimeout = (stripos($err, 'statement timeout') !== false) || (stripos($err, 'canceling statement') !== false);

    if ($isTimeout && $batchSize > 1) {
        $batchSize = max(1, (int)floor($batchSize / 2));
        continue;
    }

    $badTable = $batch[0] ?? '(unknown)';
    $errors[] = $badTable . ": " . $err;

    $prev = $cache['entries'][$badTable] ?? [];
    $cache['entries'][$badTable] = array_merge(
        is_array($prev) ? $prev : [],
        [
            'updated_at' => $now,
            'last_error' => $err,
            'error_at'   => $now,
        ]
    );
    $cacheDirty = true;

    $queue = array_slice($queue, 1);
}

if ($cacheDirty) {
    $cache['generated_at'] = $now;
    _cache_write_json_atomic($cacheFile, $cache);
}

// ------------------------- Output data -------------------------

$activeOut = [];
foreach ($activeRows as $row) {
    $tableName = $row['table_name'];
    $last = $lastTimeByTable[$tableName] ?? null;

    $ts = null;
    $lastStr = '-';
    if (!empty($last)) {
        $parsed = strtotime($last);
        if ($parsed !== false) {
            $ts = $parsed;
            $lastStr = date('Y-m-d H:i:s', $parsed);
        } else {
            $lastStr = (string)$last;
        }
    }

    $activeOut[] = ['table' => $tableName, 'last_time' => $lastStr, 'ts' => $ts];
}

$inactiveOut = [];
foreach ($inactiveRows as $row) {
    $tableName = $row['table_name'];
    $entry = $cache['entries'][$tableName] ?? null;
    $cachedLast = is_array($entry) ? ($entry['last_time'] ?? null) : null;

    $lastStr = '-';
    if (!empty($cachedLast)) {
        $parsed = strtotime($cachedLast);
        $lastStr = ($parsed !== false) ? date('Y-m-d H:i:s', $parsed) : (string)$cachedLast;
    }

    $inactiveOut[] = ['table' => $tableName, 'last_time' => $lastStr];
}

if ($format === 'json') {
    header('Content-Type: application/json');
    echo json_encode([
        'mode' => $isWarm ? 'warm' : ($forceRefresh ? 'force_refresh' : 'normal'),
        'cache_ttl_sec' => $cacheTtlSec,
        'cache_hits' => $cacheHits,
        'refreshed' => count($toFetch) - count($queue),
        'errors' => $errors,
        'active' => array_map(function($r){ return ['table'=>$r['table'], 'last_time'=>$r['last_time']]; }, $activeOut),
        'inactive' => $inactiveOut,
    ], JSON_UNESCAPED_SLASHES);
    exit;
}

// ------------------------- HTML render -------------------------

echo '<style>
  /* very specific + scoped */
  .ida-showtables-wrap table,
  .ida-showtables-wrap table td,
  .ida-showtables-wrap table th,
  .ida-showtables-wrap table caption,
  .ida-showtables-wrap button,
  .ida-showtables-wrap input,
  .ida-showtables-wrap select {
    font-family: Arial, sans-serif !important;
    font-size: 11px !important;
  }

  .ida-showtables-wrap .row-fresh { background: #d9f7d9; }
  .ida-showtables-wrap .row-stale { background: #f7d9d9; }

  .ida-showtables-wrap table { border-collapse: collapse; }
  .ida-showtables-wrap td { padding: 3px 6px; border: 1px solid #ddd; }
  .ida-showtables-wrap form { display:inline; margin:0; }
  .ida-showtables-wrap button { padding: 1px 6px; }

  .ida-showtables-wrap table.ida-showtables-table {
    width: 100%;
    table-layout: fixed;
  }

  .ida-showtables-wrap table.ida-showtables-table td:nth-child(3),
  .ida-showtables-wrap table.ida-showtables-table th:nth-child(3) {
    white-space: nowrap;
  }

  .ida-showtables-wrap {
    width: 800px;
    max-width: 800px;
  }
</style>';

if ($actionError !== null) {
    echo "<div style='color:#a00; margin:8px 0;'><b>Error:</b> " . _html($actionError) . "</div>";
}

echo '<div class="ida-showtables-wrap">';

// Force-refresh button (reloads page with params that refresh everything)
echo '<div style="margin:8px 0;">
  <form method="get" style="display:inline;">
    ' . (isset($_GET['debug'])  ? '<input type="hidden" name="debug" value="1">' : '') . '
    ' . (isset($_GET['format']) ? '<input type="hidden" name="format" value="' . _html($_GET['format']) . '">' : '') . '
    <input type="hidden" name="force_refresh" value="1">
    <input type="hidden" name="cache_ttl_sec" value="0">
    <input type="hidden" name="max_refresh" value="1000000">
    <input type="hidden" name="error_backoff_sec" value="0">
    <input type="hidden" name="timeout_ms" value="30000">
    <input type="hidden" name="time_budget_sec" value="120">
    <input type="hidden" name="batch_size" value="20">
    <button type="submit">Force refresh all timestamps</button>
  </form>
</div>';

echo '<table class="ida-showtables-table">
  <colgroup>
    <col style="width: 50%;">
    <col style="width: 25%;">
    <col style="width: 25%;">
  </colgroup>
  <tr>
    <td><b>Tablename</b></td>
    <td><b>Date of last row</b></td>
    <td><b>Action</b></td>
  </tr>';

foreach ($activeOut as $r) {
    $t = $r['table'];
    $rowClass = '';
    if ($r['ts'] !== null) {
        $age = $now - (int)$r['ts'];
        $rowClass = ($age <= $thresholdSec) ? 'row-fresh' : 'row-stale';
    }

    echo '<tr class="' . $rowClass . '">
        <td>' . _html($t) . '</td>
        <td>' . _html($r['last_time']) . '</td>
        <td>
          <form method="post">
            <input type="hidden" name="action" value="make_inactive">
            <input type="hidden" name="table" value="' . _html($t) . '">
            <button type="submit">Make inactive</button>
          </form>
        </td>
      </tr>';
}
echo '</table>';

echo '<h3>Inactive tables</h3>';

if (count($inactiveOut) === 0) {
    echo '<div><i>(none)</i></div>';
} else {
    echo '<table class="ida-showtables-table">
      <colgroup>
        <col style="width: 50%;">
        <col style="width: 25%;">
        <col style="width: 25%;">
      </colgroup>
      <tr>
        <td><b>Tablename</b></td>
        <td><b>Last known time</b></td>
        <td><b>Action</b></td>
      </tr>';

    foreach ($inactiveOut as $r) {
        $t = $r['table'];
        echo '<tr>
            <td>' . _html($t) . '</td>
            <td>' . _html($r['last_time']) . '</td>
            <td>
              <form method="post">
                <input type="hidden" name="action" value="make_active">
                <input type="hidden" name="table" value="' . _html($t) . '">
                <button type="submit">Make active</button>
              </form>
              <form method="post" style="margin-left:6px;">
                <input type="hidden" name="action" value="archive_table">
                <input type="hidden" name="table" value="' . _html($t) . '">
                <button type="submit">Archive</button>
              </form>
            </td>
          </tr>';
    }
    echo '</table>';
}

echo '</div>';

if ($debug && count($errors) > 0) {
    echo "<br>Some tables could not be queried. First few errors:<br>";
    $shown = array_slice($errors, 0, 10);
    echo "<pre>" . _html(implode("\n", $shown)) . "</pre>";
}

if ($debug) {
    $totalMs = (microtime(true) - $startWall) * 1000.0;
    echo "<br>total_ms=" . _html(number_format($totalMs, 1)) . "<br>";
}
?>
