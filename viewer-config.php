<?php

function viewer_config() {
    static $config;
    if ($config === null) {
        // Read on each request so changing the file needs no server restart.
        $config = @parse_ini_file(__DIR__ . '/config.ini', true, INI_SCANNER_TYPED);
        $read_only = $config['viewer']['read_only'] ?? null;
        // Keep the previous table timestamp interpretation for existing config files.
        $timezone = $config['viewer']['display_timezone'] ?? 'America/New_York';
        $max_graph_generators = $config['viewer']['max_graph_generators'] ?? 2;
        $waveforms = $config['waveforms'] ?? [];
        $url = $waveforms['url'] ?? '';
        $table = $waveforms['index_table'] ?? 'root_files';

        if (!is_bool($read_only)
            || !is_string($timezone) || !in_array($timezone, DateTimeZone::listIdentifiers(DateTimeZone::ALL_WITH_BC), true)
            || !is_int($max_graph_generators) || $max_graph_generators < 1 || $max_graph_generators > 32
            || !is_array($waveforms)
            || !is_string($url) || ($url !== '' && (
                filter_var($url, FILTER_VALIDATE_URL) === false
                || !in_array(strtolower(parse_url($url, PHP_URL_SCHEME) ?? ''), ['http', 'https'], true)))
            || !is_string($table) || !preg_match('/^[A-Za-z_][A-Za-z0-9_]*$/D', $table)) {
            http_response_code(503);
            header('Content-Type: application/json');
            exit(json_encode(['ok' => false, 'error' =>
                'Viewer configuration unavailable. Check config.ini against config.example.ini: read_only must be true or false, display_timezone a timezone name, max_graph_generators an integer from 1 to 32, and waveforms a valid HTTP(S) URL (or empty) and index table name.']));
        }
        $config['viewer']['display_timezone'] = $timezone;
        $config['viewer']['max_graph_generators'] = $max_graph_generators;
        $config['waveforms'] = ['url' => $url, 'index_table' => $table];
    }
    return $config;
}

// Read connection settings without connecting, so the homepage's Adminer link
// remains available while the separate table-status request is running.
function viewer_database_credentials() {
    $path = __DIR__ . '/psql_credentials.py';
    $out = [];
    if (!is_readable($path)) return $out;
    $lines = file($path, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES);
    if (!$lines) return $out;

    foreach ($lines as $line) {
        $line = trim($line);
        if ($line === '' || strpos($line, '#') === 0) continue;

        if (preg_match('/^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"(.*)"\s*$/', $line, $m)) {
            $out[$m[1]] = stripcslashes($m[2]); continue;
        }
        if (preg_match("/^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*'(.*)'\s*$/", $line, $m)) {
            $out[$m[1]] = $m[2]; continue;
        }
        if (preg_match('/^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([^\s#]+)\s*$/', $line, $m)) {
            $out[$m[1]] = $m[2]; continue;
        }
    }
    return $out;
}

function viewer_read_only() {
    return viewer_config()['viewer']['read_only'];
}

function viewer_homepage_links() {
    $links = [];
    foreach (viewer_config() as $section => $link) {
        if (strpos($section, 'homepage_link.') !== 0) { continue; }
        $title = $link['title'] ?? null;
        $label = $link['label'] ?? null;
        $url = $link['url'] ?? null;
        $description = $link['description'] ?? '';
        // Allow local relative links and full HTTP(S) URLs; exclude script URLs.
        $scheme = is_string($url) ? parse_url($url, PHP_URL_SCHEME) : false;
        $valid_url = is_string($url) && !preg_match('/[\x00-\x20\x7f]/', $url)
            && strpos($url, '\\') === false && substr($url, 0, 2) !== '//'
            && ($scheme === null || (in_array(strtolower($scheme ?: ''), ['http', 'https'], true)
                && filter_var($url, FILTER_VALIDATE_URL) !== false));

        if (!is_array($link) || !is_string($title) || trim($title) === ''
            || !is_string($label) || trim($label) === '' || !is_string($description)
            || !$valid_url) {
            http_response_code(503);
            header('Content-Type: application/json');
            exit(json_encode(['ok' => false, 'error' =>
                'Invalid homepage link configuration in [' . $section . ']. Set title, label, url and an optional description; use a relative or HTTP(S) URL.']));
        }
        if ($url !== '') {
            $links[] = ['title' => $title, 'label' => $label, 'url' => $url, 'description' => $description];
        }
    }
    return $links;
}

function require_panel_editing() {
    if (viewer_read_only()) {
        http_response_code(403);
        header('Content-Type: application/json');
        exit(json_encode(['ok' => false, 'error' => 'Panel editing is disabled in read-only mode.']));
    }
}

function viewer_origin_parts($url, $allow_path = false) {
    // Origin headers have no path/query; Referer URLs do, but share the same origin rules.
    if (!is_string($url) || preg_match('/[\x00-\x20\x7f]/', $url)
        || strpos($url, '\\') !== false || (!$allow_path && substr($url, -1) === ':')
        || filter_var($url, FILTER_VALIDATE_URL) === false) {
        return null;
    }
    $parts = parse_url($url);
    if ($parts === false || !isset($parts['scheme'], $parts['host'])
        || !in_array(strtolower($parts['scheme']), ['http', 'https'], true)
        || isset($parts['user']) || isset($parts['pass']) || isset($parts['fragment'])
        || (!$allow_path && (isset($parts['path']) || isset($parts['query'])))) {
        return null;
    }
    $scheme = strtolower($parts['scheme']);
    return [$scheme, strtolower($parts['host']), $parts['port'] ?? ($scheme === 'https' ? 443 : 80)];
}

function viewer_request_origin() {
    // A reverse proxy must expose the public host/HTTPS status through the web server.
    // Do not trust client-supplied forwarded headers.
    $scheme = (!empty($_SERVER['HTTPS']) && strtolower($_SERVER['HTTPS']) !== 'off') ? 'https' : 'http';
    return viewer_origin_parts($scheme . '://' . ($_SERVER['HTTP_HOST'] ?? ''));
}

function require_same_origin_post() {
    if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') {
        http_response_code(405);
        header('Allow: POST');
        header('Content-Type: application/json');
        exit(json_encode(['ok' => false, 'error' => 'This action requires POST.']));
    }

    $expected = viewer_request_origin();
    $origin = viewer_origin_parts($_SERVER['HTTP_ORIGIN'] ?? null);
    // Missing and "null" origins must fail closed, just like a different origin.
    if ($expected === null || $origin === null || $origin !== $expected) {
        http_response_code(403);
        header('Content-Type: application/json');
        exit(json_encode(['ok' => false, 'error' => 'POST requests must originate from this viewer.']));
    }
}

function require_graph_request() {
    $site = $_SERVER['HTTP_SEC_FETCH_SITE'] ?? null;
    if ($site === 'same-origin') { return; }
    // "none" means direct user navigation, not a missing header. Keep bookmarks working.
    if ($site === 'none' && ($_SERVER['REQUEST_METHOD'] ?? '') === 'GET'
        && ($_SERVER['HTTP_SEC_FETCH_MODE'] ?? '') === 'navigate'
        && ($_SERVER['HTTP_SEC_FETCH_DEST'] ?? '') === 'document') { return; }

    // Older browsers and HTTP installations may lack Fetch Metadata. Require proof
    // of the source origin instead of treating an absent header as trusted.
    if ($site === null) {
        $expected = viewer_request_origin();
        $referer = viewer_origin_parts($_SERVER['HTTP_REFERER'] ?? null, true);
        if ($expected !== null && $referer === $expected) { return; }
    }
    // "same-site" includes other subdomains, which need not belong to this team.
    http_response_code(403);
    header('Content-Type: application/json');
    exit(json_encode(['ok' => false, 'error' =>
        'Graph generation requires a request from this viewer or direct browser navigation.']));
}

function acquire_graph_generator_slot($cancelled = null) {
    // All web requests for this installation share the same semaphore. Keep its
    // key independent of the configured limit so changing the limit cannot
    // create a second pool while generators are still running.
    $key = hexdec(substr(hash('sha256', 'ida-graph-generators:' . __DIR__), 0, 7)) + 1;
    $slot = function_exists('sem_get')
        ? @sem_get($key, viewer_config()['viewer']['max_graph_generators'], 0600, true)
        : false;
    if ($slot === false) {
        error_log('Unable to acquire a graph generator slot. Check PHP sysvsem support and system semaphore limits.');
        http_response_code(503);
        header('Content-Type: text/plain; charset=UTF-8');
        exit('Graph generation queue unavailable. Check PHP sysvsem support and the server error log.');
    }
    // A busy pool must return promptly: waiting in PHP occupies a web worker
    // and an HTTP connection that may be needed for the cancellation request.
    if ($cancelled !== null && $cancelled()) { return false; }
    error_clear_last();
    $acquired = @sem_acquire($slot, true);
    if (!$acquired && error_get_last() !== null) {
        throw new RuntimeException('Unable to acquire a graph generator slot. Check the server semaphore limits.');
    }
    if (!$acquired) { return false; }
    if ($cancelled !== null && $cancelled()) {
        sem_release($slot);
        return false;
    }
    // PHP also releases the acquisition if the request exits unexpectedly.
    return $slot;
}
