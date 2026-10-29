<?php
require_once __DIR__ . '/data-paths.php';
$basePath = __DIR__ . '/csv-files';

if (!isset($_GET["filename"])) {
    http_response_code(400);
    echo json_encode(["error" => "Missing filename"]);
    exit;
}

$filename = panel_filename($_GET["filename"]);
$panelPath = pathinfo($filename, PATHINFO_FILENAME);
$subfolderPath = dirname($filename);
$fullPath = data_path($basePath, "$subfolderPath/$panelPath");

$snapshots = [];

if (!is_dir($fullPath)) {
    echo json_encode(["snapshots" => []]);
    exit;
}

foreach (scandir($fullPath) as $entry) {
    $entryPath = "$fullPath/$entry";
    if (!is_link($entryPath) && is_dir($entryPath) && preg_match('/^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_/', $entry)) {
        $folderMeta = [];

        foreach (data_glob($entryPath, '*.meta.json') as $metaFile) {
            if (is_link($metaFile) || !is_file($metaFile)) { continue; }
            $metaContent = json_decode(file_get_contents($metaFile), true);

            $csvFile = preg_replace('/\.meta\.json$/', '.csv', $metaFile);
            $csvSizeMB = !is_link($csvFile) && is_file($csvFile) ? round(filesize($csvFile) / (1024 * 1024), 2) : null;

            $folderMeta[] = [
                "file" => basename($csvFile),
                "queries" => $metaContent["queries"] ?? [],
                "row_count" => $metaContent["row_count"] ?? null,
                "csv_size_mb" => $csvSizeMB
            ];
        }

        $snapshots[] = [
            "folder" => $entry,
            "graphs" => $folderMeta
        ];
    }
}

header("Content-Type: application/json");
echo json_encode(["snapshots" => $snapshots]);
