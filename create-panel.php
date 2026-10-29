<?php
require_once __DIR__ . '/viewer-config.php';
require_same_origin_post();
require_panel_editing();
require_once __DIR__ . '/data-paths.php';
header('Content-Type: application/json; charset=UTF-8');

function panel_creation_error($status, $message) {
    http_response_code($status);
    exit(json_encode(['ok' => false, 'error' => $message]));
}

if (!isset($_POST['name']) || !is_string($_POST['name'])) {
    panel_creation_error(400, 'Enter a panel name.');
}
$name = preg_replace('/\.json\z/i', '', trim($_POST['name']));
if ($name === '' || strlen($name) > 120
    || !preg_match('/\A[\p{L}\p{N}][\p{L}\p{N} _-]*\z/u', $name)) {
    panel_creation_error(400, 'Use a short name starting with a letter or number, containing only letters, numbers, spaces, hyphens and underscores.');
}

$filename = panel_filename($name);
$directory = realpath(__DIR__ . '/json-files');
if ($directory === false || !is_dir($directory)) {
    panel_creation_error(500, 'The panel directory is unavailable. Check json-files permissions.');
}
$target = data_path($directory, $filename);
// Extensionless panels and any case of .json share the same generated-data folder.
if (file_exists($target) || is_link($target)
    || data_glob($directory, $name . '.[jJ][sS][oO][nN]')) {
    panel_creation_error(409, 'A panel with this name already exists. Choose another name.');
}

$today = new DateTimeImmutable('today', new DateTimeZone(viewer_config()['viewer']['display_timezone']));
$content = json_encode([
    'header' => $name,
    'start' => $today->format('Y-m-d\TH:i'),
    'end' => '',
    'notes' => '',
    'graphs' => [],
], JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES) . "\n";

// Write the complete JSON beside its destination before publishing it.
$temporary = @tempnam($directory, '.panel-');
if ($temporary === false || dirname($temporary) !== $directory) {
    if ($temporary !== false) { @unlink($temporary); }
    error_log('Unable to create panel temporary file in: ' . $directory);
    panel_creation_error(500, 'The panel could not be created. Check json-files permissions.');
}
$written = @file_put_contents($temporary, $content);
if ($written === false || $written !== strlen($content)
    || !@chmod($temporary, 0666 & ~umask())) {
    @unlink($temporary);
    error_log('Unable to write complete new panel: ' . $target);
    panel_creation_error(500, 'The panel could not be saved completely.');
}

// A hard link publishes atomically and refuses replacement, even for simultaneous requests.
$created = @link($temporary, $target);
@unlink($temporary);
if (!$created) {
    clearstatcache(true, $target);
    if (file_exists($target) || is_link($target)) {
        panel_creation_error(409, 'A panel with this name already exists. Choose another name.');
    }
    error_log('Unable to publish new panel: ' . $target);
    panel_creation_error(500, 'The panel could not be created.');
}

http_response_code(201);
echo json_encode(['ok' => true, 'filename' => $filename]);
