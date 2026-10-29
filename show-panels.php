<link href="phpFileTree/styles/default/default.css" rel="stylesheet" type="text/css" media="screen" />

<!-- Makes the file tree(s) expand/collapse dynamically -->
<script src="phpFileTree/php_file_tree.js" type="text/javascript"></script>

<?php

ini_set('display_errors', 1);
ini_set('display_startup_errors', 1);
error_reporting(E_ALL);


// Include the PHP File Tree script
include("phpFileTree/php_file_tree.php");

// Base path for JSON files
$base_path = "json-files/";

function get_start_end_from_json($file) {
    // Ensure the file exists and is readable
    if (!file_exists($file)) {
        return ['NA', 'NA'];
    }

    // Attempt to read the file
    $json_content = file_get_contents($file);
    if ($json_content === false) {
        return ['Unable to read file', ''];
    }

    // Attempt to decode the JSON content
    $json_data = json_decode($json_content, true);
    if (json_last_error() !== JSON_ERROR_NONE) {
        return ['JSON decode error: ' . json_last_error_msg(), ''];
    }

    // Extract the 'start' and 'end' values
    $start = isset($json_data['start']) && !empty($json_data['start']) ? $json_data['start'] : 'NA';
    $end = isset($json_data['end']) && !empty($json_data['end']) ? $json_data['end'] : 'NA';

    return [$start, $end];
}


// Generate the file tree HTML
$file_tree_html = php_file_tree($base_path, "load-panel.php?filename=[link]");

//echo htmlspecialchars($file_tree_html);
//die();

$file_tree_html = preg_replace_callback(
    '/<a\s+href=\'load-panel\.php\?filename=([^\']+)\'[^>]*>([^<]+)<\/a>/',
    function ($matches) use ($base_path) {
        // Extract the full path to the file
        $file_path = $base_path . '/' . urldecode($matches[1]);

        // Get start and end values from the JSON file
        list($start, $end) = get_start_end_from_json($file_path);

        // Extract only the date portion (first 10 characters) if applicable
        $start_date = htmlspecialchars($start !== 'N/A' ? substr($start, 0, 10) : $start, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
        $end_date = htmlspecialchars($end !== 'N/A' ? substr($end, 0, 10) : $end, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');

        // The file tree already escaped the visible filename; do not escape it twice.
        $filename = '<strong>' . $matches[2] . '</strong>';

        // Append start and end dates to the displayed filename
        $updated_text = "{$filename} ({$start_date} to {$end_date})";

        // Return the updated anchor tag
        return "<a href='load-panel.php?filename={$matches[1]}' target='_blank'>{$updated_text}</a>";
    },
    $file_tree_html
);



// Output the modified file tree
echo $file_tree_html;

?>

