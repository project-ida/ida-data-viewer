<?php
require_once __DIR__ . '/viewer-config.php';
// Reject cross-origin POSTs before the homepage sends any HTML.
if (($_SERVER['REQUEST_METHOD'] ?? '') === 'POST') { require_same_origin_post(); }
$read_only = viewer_read_only();
$homepage_links = viewer_homepage_links();
?>

<style>
a {
    color: inherit;
}
</style>


<?php

echo "<h2>Tables</h2>";

?>
<div id="table-status" role="status">Loading table status…</div>
<div id="table-list" aria-busy="true"></div>
<script>
{
    const tables = document.getElementById('table-list');
    const status = document.getElementById('table-status');
    // Keep the same refresh settings as the previous server-side include.
    const tablesUrl = 'show-tables.php?' + new URLSearchParams({
        force_refresh: '1', cache_ttl_sec: '0', max_refresh: '1000000',
        error_backoff_sec: '0', time_budget_sec: '120', timeout_ms: '30000', batch_size: '20'
    });
    async function loadTables(url, options = {}) {
        status.textContent = 'Loading table status…';
        tables.setAttribute('aria-busy', 'true');
        tables.querySelectorAll('button').forEach(button => button.disabled = true);
        try {
            const response = await fetch(url, { cache: 'no-store', ...options });
            if (!response.ok) throw new Error('Request failed (HTTP ' + response.status + ').');
            tables.innerHTML = await response.text();
            status.textContent = '';
        } catch (error) {
            status.textContent = 'Table status could not be loaded. ' + error.message + ' Refresh the page to try again.';
        } finally {
            tables.setAttribute('aria-busy', 'false');
            tables.querySelectorAll('button').forEach(button => button.disabled = false);
        }
    }
    // Refresh and visibility controls update just the table section, keeping the homepage open.
    tables.addEventListener('submit', event => {
        event.preventDefault();
        const form = event.target;
        const data = new URLSearchParams(new FormData(form));
        const url = new URL(tablesUrl, window.location.href);
        if (form.method.toLowerCase() === 'post') {
            loadTables(url, { method: 'POST', body: data });
        } else {
            data.forEach((value, key) => url.searchParams.set(key, value));
            loadTables(url);
        }
    });
    loadTables(tablesUrl);
}
</script>
<?php
echo "<br><br>";

// Build the Adminer link without waiting for a database connection.
$creds = viewer_database_credentials();
$pgHost = $creds['PGHOST'] ?? '';
$pgPort = $creds['PGPORT'] ?? '';
$pgDb = $creds['PGDATABASE'] ?? '';
$adminer_host = filter_var($pgHost, FILTER_VALIDATE_IP, FILTER_FLAG_IPV6) ? '[' . $pgHost . ']' : $pgHost;
$adminer_url = 'postgres/adminer-6.2.0-en.php';
if ($pgHost !== '' && $pgPort !== '' && $pgDb !== '') {
    $adminer_url .= '?' . http_build_query([
        'pgsql' => $adminer_host . ':' . $pgPort,
        'db' => $pgDb,
        'ns' => 'public',
    ]);
}
echo "<a href='" . htmlspecialchars($adminer_url, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8') . "' target='_blank' rel='noopener'>manage tables</a><br><br>
<small>To create a new table run those two queries as 'SQL command' (change tablename to your preferred name):<br><code>CREATE TABLE tablename (time TIMESTAMP NOT NULL, channels FLOAT[] NOT NULL, id BIGSERIAL);</code><br><code>SELECT create_hypertable('tablename', 'time');</code>";
echo "<br>";
echo "Note: to add ps timestamp data to a new table just change the new table query to use '<code>(time TIMESTAMP NOT NULL, channels FLOAT[] NOT NULL, ps BIGINT, id BIGSERIAL)</code>' instead</small>";
echo "<br><br>";

echo "
	<small>To create empty padding values, run (for example):<br><code>INSERT INTO tablename (time, channels)
VALUES ('2025-02-26 17:00:01', '{NULL}');</code><br>
If you use granularity, you may need to generate multiple padding values, e.g. 10:<br>
<code>INSERT INTO tablename (time, channels) SELECT '2025-02-26 17:00:01'::timestamp + INTERVAL '1 second' * gs, '{NULL}' FROM generate_series(0, 9) AS gs;</code>
</small>";
echo "<br><br>";

echo "
	<small>To replace nonsensical values with NULL (for example):<br><code>UPDATE usbtempdaq1 SET channels = array_replace(channels, -9999, NULL);</code></small>";
echo "<br><br>";

echo "
	<small>To backfill from CSV files, go to the imports_temp table, make sure it is empty, and use the Adminer import function to upload your .csv. Then run:<br><code>INSERT INTO tablename (time, channels) SELECT time, channels FROM imports_temp ON CONFLICT (time) DO NOTHING;</code></small>";
echo "<br><br>";

echo "
<small>To see how much disk space a table is taking up, use this query:<br>
<code>
SELECT pg_size_pretty(SUM(pg_total_relation_size(quote_ident(ch.schema_name) || '.' || quote_ident(ch.table_name)))) AS total_size,<br>
&nbsp;&nbsp;&nbsp;&nbsp;SUM(pg_total_relation_size(quote_ident(ch.schema_name) || '.' || quote_ident(ch.table_name))) AS total_size_byte<br>
FROM _timescaledb_catalog.chunk ch<br>
JOIN _timescaledb_catalog.hypertable ht ON ht.id = ch.hypertable_id<br>
WHERE ht.table_name = 'TABLE_NAME';
</code>
</small>";
echo "<br><br>";

echo "
<small>To copy an existing table and turn it into a hypertable:<br>
<code>
CREATE TABLE new_table (LIKE existing_table INCLUDING ALL);<br>
SELECT create_hypertable('new_table', 'time');
</code>
</small>";
echo "<br><br>";

echo "
<small>When you change the name of a dataset in the surface viewer, you need to run this to move annotations over to the new name:<br>
<code>
UPDATE annotations SET source='NEW_NAME' WHERE source='OLD_NAME';
</code>
</small>";
echo "<br><br>";


echo "<h2>Panels</h2>";

echo '<div id="panel-list">';
include("show-panels.php");
echo '</div>';

echo "<br><br>";
if (!$read_only) {
    echo '<button type="button" id="create-panel">Create new panel</button>
        <span id="create-panel-error" role="alert" style="color: #a94442;"></span><br><br>';
    echo "<a href='onefilecms/onefilecms.php' target='_blank'>manage panels</a><br><br>";
    ?>
    <script>
    document.getElementById('create-panel').addEventListener('click', async function () {
        const name = window.prompt('New panel name (letters, numbers, spaces, hyphens and underscores):');
        if (name === null) return;
        const error = document.getElementById('create-panel-error');
        error.textContent = '';
        // Open during the click handler, before awaiting the server, to avoid popup blocking.
        const panelWindow = window.open('about:blank', '_blank');
        if (panelWindow) panelWindow.opener = null;
        this.disabled = true;
        try {
            const response = await fetch('create-panel.php', {
                method: 'POST',
                body: new URLSearchParams({ name })
            });
            const result = await response.json();
            if (!response.ok || !result.ok) throw new Error(result.error || 'The panel could not be created.');
            if (panelWindow && !panelWindow.closed) {
                panelWindow.location.replace('load-panel.php?filename=' + encodeURIComponent(result.filename));
            }
            // Refresh only the file tree, without repeating the homepage's database queries.
            try {
                const listing = await fetch('show-panels.php', { cache: 'no-store' });
                if (!listing.ok) throw new Error('Panel list request failed.');
                const page = new DOMParser().parseFromString(await listing.text(), 'text/html');
                const tree = page.querySelector('ul.php-file-tree');
                const currentTree = document.querySelector('#panel-list ul.php-file-tree');
                if (!tree || !currentTree) throw new Error('Panel list was missing.');
                // Keep the stylesheet and script included beside the existing tree.
                currentTree.replaceWith(tree);
                init_php_file_tree();
            } catch (refreshError) {
                error.textContent = 'Panel created, but the panel list could not be updated. Refresh the homepage to see it.';
            }
        } catch (failure) {
            if (panelWindow && !panelWindow.closed) panelWindow.close();
            error.textContent = failure.message;
        } finally {
            this.disabled = false;
        }
    });
    </script>
    <?php
}
echo "<br><br>";

if (!$read_only) {
    ?>
    <h2>Cancel graph loading</h2>
    <p>Use this if a demanding query puts too much load on the server. It cancels running and waiting graph requests across all panels and users. Graphs already displayed stay visible.</p>
    <button type="button" id="cancel-graph-generation" aria-live="polite">Cancel all graph generation</button>
    <span id="cancel-graph-generation-status" role="status"></span><br><br>
    <script>
    {
        const button = document.getElementById('cancel-graph-generation');
        const label = button.textContent;
        const status = document.getElementById('cancel-graph-generation-status');
        let feedbackTimer;
        button.addEventListener('click', async function () {
            if (!window.confirm('Cancel all running and waiting graphs for everyone using this viewer? Graphs already loaded will stay visible.')) return;
            clearTimeout(feedbackTimer);
            status.textContent = '';
            button.disabled = true;
            button.textContent = 'Requesting cancellation…';
            try {
                const response = await fetch('cancel-graph-generation.php', { method: 'POST' });
                const result = await response.json();
                if (!response.ok || !result.ok) throw new Error(result.error || 'Cancellation failed.');
                button.textContent = 'Cancellation requested';
                feedbackTimer = setTimeout(() => { button.textContent = label; }, 3000);
            } catch (error) {
                button.textContent = label;
                status.textContent = error.message;
            } finally {
                button.disabled = false;
            }
        });
    }
    </script>
    <?php
}

// Custom installation links are plain text; escape every value before rendering.
foreach ($homepage_links as $link) {
    echo '<h2>' . htmlspecialchars($link['title'], ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8') . '</h2>';
    echo '<a href="' . htmlspecialchars($link['url'], ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8')
        . '" target="_blank" rel="noopener noreferrer">'
        . htmlspecialchars($link['label'], ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8') . '</a><br><br>';
    if ($link['description'] !== '') {
        echo '<small>' . nl2br(htmlspecialchars($link['description'], ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8')) . '</small>';
    }
    echo '<br><br>';
}

?>
