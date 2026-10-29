<?php
require_once __DIR__ . '/viewer-config.php';
require_same_origin_post();
require_panel_editing();
require_once __DIR__ . '/data-paths.php';
//notice this will put WHATEVER is in json into file
$filename = panel_filename($_POST["filename"] ?? null);
$target = data_path(__DIR__ . '/json-files', $filename);
if (!isset($_POST['json'])) { http_response_code(400); exit('Missing JSON'); }
if (isset($_POST["json"])) {

	if (!is_string($_POST['json'])) { http_response_code(400); exit('Invalid JSON'); }
	$json = json_decode($_POST['json']);
	if (json_last_error() !== JSON_ERROR_NONE) { http_response_code(400); exit('Invalid JSON'); }
	$filecontent = json_encode($json, JSON_PRETTY_PRINT);

    if ($filecontent === false) { http_response_code(500); exit('Panel JSON could not be encoded.'); }
    // Resolve existing links so saving updates the panel, not the link itself.
    $resolved_target = realpath($target);
    if ($resolved_target !== false) { $target = $resolved_target; }
    $exists = file_exists($target);
    $permissions = $exists ? @fileperms($target) : (0666 & ~umask());
    $directory = realpath(dirname($target));
    $temp_file = false;
    $written = false;

    // Write beside the panel so a complete replacement can be renamed atomically.
    // Respect read-only files even when their directory permits replacement.
    if ($directory !== false && $permissions !== false && (!$exists || is_writable($target))) {
        $temp_file = @tempnam($directory, '.panel-');
        // tempnam can fall back to the system temp directory; reject that fallback.
        if ($temp_file !== false && dirname($temp_file) === $directory) {
            $written = @file_put_contents($temp_file, $filecontent);
        }
    }

    // Publish only after the complete write and permission preservation succeed.
    if ($written === false || $written !== strlen($filecontent)
        || !@chmod($temp_file, $permissions & 0777)
        || !@rename($temp_file, $target)) {
        if ($temp_file !== false) { @unlink($temp_file); }
        error_log('Unable to save complete panel JSON: ' . $target);
        http_response_code(500);
        exit('Panel changes could not be saved completely.');
    }
}


//
// 

?>