<?php

function invalid_data_path() {
    http_response_code(400);
    header('Content-Type: application/json');
    exit(json_encode(['ok' => false, 'error' => 'Invalid file path']));
}

function relative_data_path($path) {
    if (!is_string($path) || $path === '' || $path[0] === '/'
        || strpos($path, '\\') !== false || preg_match('/\A[A-Za-z]:/', $path)
        || preg_match('/[\x00-\x1f\x7f]/', $path)) {
        invalid_data_path();
    }
    $parts = explode('/', $path);
    if (in_array('..', $parts, true)) { invalid_data_path(); }
    // Keep nested folders and existing names; normalize harmless ./ and duplicate /.
    $parts = array_values(array_filter($parts, function ($part) {
        return $part !== '' && $part !== '.';
    }));
    if (!$parts) { invalid_data_path(); }
    return implode('/', $parts);
}

function panel_filename($filename) {
    $filename = relative_data_path($filename);
    $name = basename($filename);
    $extension = strtolower(pathinfo($name, PATHINFO_EXTENSION));
    // Panels currently use no extension or .json; never write executable/config files.
    if (($extension !== '' && $extension !== 'json') || $name[0] === '.'
        || substr($name, -1) === '.') {
        invalid_data_path();
    }
    return $filename;
}

function data_path($root, $relative) {
    $relative = relative_data_path($relative);
    clearstatcache(true); // Check current links, not a cached target from an earlier request.
    if (!file_exists($root) && !is_link($root)) {
        // An absent data directory is allowed to remain absent (e.g. no snapshots yet).
        $root = rtrim($root, '/');
    } else {
        $resolved_root = realpath($root);
        if ($resolved_root === false || !is_dir($resolved_root)) { invalid_data_path(); }
        $root = $resolved_root;
    }
    $target = $root . '/' . $relative;
    $existing = $target;
    // Check the nearest existing parent too: new files can escape through a folder link.
    while (!file_exists($existing) && !is_link($existing)) {
        if ($existing === $root) { return $target; }
        $existing = dirname($existing);
    }
    $resolved = realpath($existing);
    if ($resolved === false || ($resolved !== $root
        && strpos($resolved, $root . '/') !== 0)) {
        invalid_data_path();
    }
    return $target;
}

function data_glob($directory, $pattern) {
    // A folder name containing [] or * must not become part of the glob expression.
    $directory = str_replace(['\\', '*', '?', '[', ']'], ['\\\\', '\\*', '\\?', '\\[', '\\]'], $directory);
    return glob($directory . '/' . $pattern) ?: [];
}
