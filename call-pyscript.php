<?php
require_once __DIR__ . '/data-paths.php';
require_once __DIR__ . '/viewer-config.php';
$read_only = viewer_read_only();

ini_set('memory_limit', '1000M');

header("Cache-Control: no-store, no-cache, must-revalidate, max-age=0");
header("Cache-Control: post-check=0, pre-check=0", false);
header("Pragma: no-cache");
header('X-Content-Type-Options: nosniff');
// Keep direct CSV responses readable without interpreting series labels as HTML.
if ($read_only || !isset($_GET['debug'])) {
    header('Content-Type: text/plain; charset=UTF-8');
}

$path = panel_filename($_GET["filename"] ?? null);
$graphno = $_GET["graphno"] ?? null;
if (!is_string($graphno) || !preg_match('/\A[1-9][0-9]*\z/', $graphno)
    || filter_var($graphno, FILTER_VALIDATE_INT) === false) { invalid_data_path(); }
data_path(__DIR__ . '/json-files', $path);
$panelname = pathinfo($path, PATHINFO_FILENAME);
$subfolder = dirname($path);
$csvfilename = $panelname . '-' . $graphno . '.csv';
$file = data_path(__DIR__ . '/csv-files', "$subfolder/$panelname/$csvfilename");

// In read-only mode, serve prepared data without starting the generator.
if ($read_only) {
    header("X-Snapshot-Mode: Enabled"); // Add debug header
    if (is_file($file)) {
        $csv = @file_get_contents($file);
        if ($csv === false) {
            error_log('Unable to read prepared graph CSV: ' . $file);
            http_response_code(500);
            exit('Error: Snapshot file could not be read.');
        }
        echo $csv;
    } else {
        http_response_code(404);
        echo "Error: Snapshot file not found.";
    }
    exit;
}

// Reject cross-site triggers before starting Python; prepared CSV reads above stay available.
require_graph_request();
require_once __DIR__ . '/graph-generation.php';

// Escape and prepare command-line arguments
$debug = isset($_GET["debug"]) ? '1' : '0';

// Public URL for diagnostic links; derive it from this request, not the disk path.
$base_url = '';
if (isset($_SERVER['HTTP_HOST'], $_SERVER['SCRIPT_NAME'])) {
    $scheme = (!empty($_SERVER['HTTPS']) && strtolower($_SERVER['HTTPS']) !== 'off') ? 'https' : 'http';
    $base_url = $scheme . '://' . $_SERVER['HTTP_HOST'] . rtrim(dirname($_SERVER['SCRIPT_NAME']), '/') . '/';
    if (filter_var($base_url, FILTER_VALIDATE_URL) === false) { $base_url = ''; }
}

$arguments = ['python3', __DIR__ . '/generate-graph-data.py', $path, $graphno, $debug,
    '--base-url', $base_url];
// Keep the diagnostic command runnable from a terminal; its cancellation pipe is PHP-only.
$command = implode(' ', array_map('escapeshellarg', $arguments)) . ' 2>&1';

// Capture the exit status too: an old CSV does not mean this run succeeded.
$output = [];
$exit_status = 1;
$generator_slot = false;
$was_cancelled = false;
try {
    $generation = $_GET['generation'] ?? graph_generation_token();
    if (!is_string($generation) || !preg_match('/\A[0-9a-f]{32}\z/', $generation)) { invalid_data_path(); }
    $cancelled = function () use ($generation, &$was_cancelled) {
        return $was_cancelled = !hash_equals($generation, graph_generation_token());
    };
    if ($cancelled()) { graph_generation_cancelled_response(); }
    $generator_slot = acquire_graph_generator_slot($cancelled);
    if ($generator_slot === false) {
        if ($cancelled()) { graph_generation_cancelled_response(); }
        $retry_url = $debug === '1' ? '?' . http_build_query([
            'filename' => $path, 'graphno' => $graphno, 'debug' => '1', 'generation' => $generation,
        ]) : null;
        graph_generation_waiting_response($retry_url);
    }
    [$exit_status, $diagnostics] = run_graph_generator([...$arguments, '--cancel-fd', '3'], $cancelled);
    $output = explode("\n", rtrim($diagnostics));
    $cancelled();
    $result = true;
} catch (Throwable $error) {
    $result = false;
    $output[] = $error->getMessage();
} finally {
    if ($generator_slot !== false) { sem_release($generator_slot); }
}
if ($was_cancelled) { graph_generation_cancelled_response(); }
clearstatcache(true, $file);
$failure = null;
if ($result === false || $exit_status !== 0) {
    $failure = 'Graph data could not be generated.';
} elseif (!is_file($file) || !is_readable($file)) {
    $failure = 'Generated CSV file is missing or unreadable.';
} elseif ($debug === '0') {
    $csv = @file_get_contents($file);
    if ($csv === false) { $failure = 'Generated CSV file could not be read.'; }
}
if ($failure !== null) {
    http_response_code(500);
    error_log("Graph generation failed for $path, graph $graphno (exit status $exit_status): "
        . $failure . "\n" . implode("\n", $output));
}

// Keep detailed, escaped diagnostics in the query view, including failed runs.
if ($debug === '1') {
    if ($failure !== null) {
        echo '<p>' . htmlspecialchars($failure, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8') . '</p>';
    }
    echo "<b>" . nl2br(htmlspecialchars($command, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8')) . "</b><br><br>";
    echo nl2br(htmlspecialchars(implode("\n", $output), ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8'));
} else {
    if ($failure !== null) { exit('Error: ' . $failure . ' Use the query link for details.'); }
    echo $csv;
}
?>
