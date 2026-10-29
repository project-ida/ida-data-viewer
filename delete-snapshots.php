<?php
require_once __DIR__ . '/viewer-config.php';
require_same_origin_post();
require_panel_editing();
require_once __DIR__ . '/data-paths.php';
header("Content-Type: application/json");

if (!isset($_POST["filename"])) {
    http_response_code(400);
    echo json_encode(["ok" => false, "error" => "Missing filename"]);
    exit;
}

$basePath = __DIR__ . '/csv-files';
$filename = panel_filename($_POST["filename"]);

$panelPath = pathinfo($filename, PATHINFO_FILENAME);
$subfolderPath = dirname($filename);
$parentDir = data_path($basePath, "$subfolderPath/$panelPath");

if (!is_dir($parentDir)) {
    echo json_encode(["ok" => true, "deleted" => []]); // nothing to delete
    exit;
}

$deleted = [];
$entries = @scandir($parentDir);
if ($entries === false) {
    error_log('Unable to list snapshot directory: ' . $parentDir);
    http_response_code(500);
    exit(json_encode(['ok' => false, 'error' => 'Snapshots could not be listed.', 'deleted' => []]));
}
try {
    foreach ($entries as $entry) {
        $entryPath = "$parentDir/$entry";
        if (!is_link($entryPath) && is_dir($entryPath) && preg_match('/^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_/', $entry)) {
            $it = new RecursiveDirectoryIterator($entryPath, FilesystemIterator::SKIP_DOTS);
            $ri = new RecursiveIteratorIterator($it, RecursiveIteratorIterator::CHILD_FIRST);
            foreach ($ri as $f) {
                // Remove a link itself; never recurse into or delete its target.
                $removed = ($f->isLink() || !$f->isDir()) ? @unlink($f) : @rmdir($f);
                if (!$removed) {
                    throw new RuntimeException('Unable to remove snapshot item: ' . $f->getPathname());
                }
            }
            if (!@rmdir($entryPath)) {
                throw new RuntimeException('Unable to remove snapshot directory: ' . $entryPath);
            }
            $deleted[] = $entry;
        }
    }
} catch (RuntimeException $error) {
    error_log($error->getMessage());
    http_response_code(500);
    exit(json_encode(['ok' => false, 'error' =>
        'Snapshot deletion could not be completed. Some files may already have been removed.',
        'deleted' => $deleted]));
}

echo json_encode(["ok" => true, "deleted" => $deleted]);
