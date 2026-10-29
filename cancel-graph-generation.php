<?php
require_once __DIR__ . '/viewer-config.php';
require_same_origin_post();
require_panel_editing();
require_once __DIR__ . '/graph-generation.php';
header('Content-Type: application/json; charset=UTF-8');
header('Cache-Control: no-store');

try {
    graph_generation_token(true);
    echo json_encode(['ok' => true, 'message' =>
        'Cancellation requested for all running and waiting graphs. Reload a panel to generate its graphs again.']);
} catch (Throwable $error) {
    error_log('Graph cancellation failed: ' . $error->getMessage());
    http_response_code(503);
    echo json_encode(['ok' => false, 'error' => 'Graph generation could not be cancelled. Check the server error log.']);
}
