<?php
require_once __DIR__ . '/viewer-config.php';
require_same_origin_post();
require_panel_editing();
require_once __DIR__ . '/data-paths.php';
header('Content-Type: text/plain; charset=UTF-8');
if (!isset($_POST["filename"]) || !isset($_POST["label"])) {
    http_response_code(400);
    echo "Missing parameters";
    exit;
}

$filename = panel_filename($_POST["filename"]);
if (!is_string($_POST["label"])) { invalid_data_path(); }
$label = preg_replace('/[^a-zA-Z0-9_\-]/', '_', $_POST["label"]);  // sanitize

$panelPath = pathinfo($filename, PATHINFO_FILENAME);
$subfolderPath = dirname($filename);
$sourceDir = data_path(__DIR__ . '/csv-files', "$subfolderPath/$panelPath");

$timestamp = date("Y-m-d_H-i-s");
$snapshotDir = data_path($sourceDir, "{$timestamp}_$label");
if (is_link($snapshotDir)) { invalid_data_path(); }

if (!is_dir($sourceDir)) {
    http_response_code(404);
    echo "Source directory not found";
    exit;
}

if (file_exists($snapshotDir)) {
    http_response_code(409);
    exit('A snapshot with this name already exists. Use another label or try again.');
}

$files = array_filter(data_glob($sourceDir, '*.csv'), function ($file) {
    return !is_link($file) && is_file($file);
});
if (!$files) {
    http_response_code(404);
    exit('No CSV files are available to snapshot.');
}
if (!@mkdir($snapshotDir, 0775, true)) {
    error_log('Unable to create snapshot directory: ' . $snapshotDir);
    http_response_code(500);
    exit('Snapshot directory could not be created.');
}

// Copy all matching files
foreach ($files as $file) {
    if (is_link($file) || !is_file($file) || !@copy($file, data_path($snapshotDir, basename($file)))) {
        error_log('Unable to copy snapshot CSV: ' . $file . ' to ' . $snapshotDir);
        http_response_code(500);
        exit('Snapshot incomplete: a CSV file could not be copied.');
    }
}
$metas = data_glob($sourceDir, '*.meta.json');
foreach ($metas as $file) {
    if (!is_link($file) && is_file($file)) {
        if (!@copy($file, data_path($snapshotDir, basename($file)))) {
            error_log('Unable to copy snapshot metadata: ' . $file . ' to ' . $snapshotDir);
            http_response_code(500);
            exit('Snapshot incomplete: a metadata file could not be copied.');
        }
    }
}

echo "Snapshot stored in: csv-files/$subfolderPath/$panelPath/{$timestamp}_$label";
