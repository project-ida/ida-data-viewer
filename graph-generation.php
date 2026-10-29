<?php
// A private cancellation generation shared by this installation's PHP workers.
// Pages carry its token so even requests still waiting in the browser are cancelled.
function graph_generation_state() {
    static $state;
    if ($state === null) {
        $directory = rtrim(sys_get_temp_dir(), DIRECTORY_SEPARATOR) . '/ida-graph-jobs-'
            . substr(hash('sha256', __DIR__), 0, 24);
        if (!is_dir($directory)) { @mkdir($directory, 0700); }
        if (!is_dir($directory) || is_link($directory)
            || (fileperms($directory) & 0077) !== 0) {
            throw new RuntimeException('Graph cancellation storage is unavailable. Check the server temporary directory.');
        }
        $path = $directory . '/generation';
        if (is_link($path) || ($state = @fopen($path, 'c+')) === false
            || !@chmod($path, 0600)) {
            throw new RuntimeException('Graph cancellation storage could not be opened.');
        }
    }
    return $state;
}

function graph_generation_token($cancel = false) {
    $state = graph_generation_state();
    if (!flock($state, LOCK_EX)) { throw new RuntimeException('Graph cancellation storage could not be locked.'); }
    try {
        rewind($state);
        $token = stream_get_contents($state);
        if ($cancel || $token === '') {
            $token = bin2hex(random_bytes(16));
            rewind($state);
            if (!ftruncate($state, 0) || fwrite($state, $token) !== strlen($token) || !fflush($state)) {
                throw new RuntimeException('Graph cancellation could not be recorded.');
            }
        }
        if (!preg_match('/\A[0-9a-f]{32}\z/', $token)) {
            throw new RuntimeException('Graph cancellation storage is invalid.');
        }
        return $token;
    } finally {
        flock($state, LOCK_UN);
    }
}

function graph_generation_cancelled_response() {
    http_response_code(409);
    header('X-Graph-Generation: cancelled');
    exit('Graph generation cancelled. Reload the panel to try again.');
}

function graph_generation_waiting_response($retry_url = null) {
    http_response_code(202);
    header('X-Graph-Generation: waiting');
    header('Retry-After: 2');
    if ($retry_url === null) {
        exit('Waiting for a graph generation slot.');
    }
    // The query link opens a document, so let that document retry without
    // holding a PHP worker. Keep its original token so cancellation still applies.
    header('Content-Type: text/html; charset=UTF-8');
    echo '<p>Waiting for a graph generation slot. This page will retry automatically.</p>';
    echo '<script>setTimeout(() => location.replace('
        . json_encode($retry_url, JSON_HEX_TAG | JSON_HEX_AMP | JSON_HEX_APOS | JSON_HEX_QUOT)
        . '), 2000);</script>';
    exit;
}

function run_graph_generator($arguments, $cancelled) {
    // An array starts Python directly: termination targets this child, with no shell wrapper.
    // Descriptor 3 lets the child's watcher cancel its own SQL operation before it exits.
    $process = proc_open($arguments, [
        0 => ['file', '/dev/null', 'r'], 1 => ['pipe', 'w'],
        2 => ['redirect', 1], 3 => ['pipe', 'r'],
    ], $pipes);
    if ($process === false) { throw new RuntimeException('The graph generator could not be started.'); }
    stream_set_blocking($pipes[1], false);
    $output = '';
    $cancel_started = null;
    try {
        while (true) {
            $output .= stream_get_contents($pipes[1]);
            $status = proc_get_status($process);
            if (!$status['running']) {
                $output .= stream_get_contents($pipes[1]);
                return [$status['exitcode'], $output];
            }
            if ($cancel_started === null && $cancelled()) {
                $cancel_started = microtime(true);
                @fwrite($pipes[3], "cancel\n");
                fflush($pipes[3]);
            }
            if ($cancel_started !== null) {
                $elapsed = microtime(true) - $cancel_started;
                // Keep the slot until the child actually exits, including if its watcher stalls.
                if ($elapsed >= 4) { proc_terminate($process, 9); }
                elseif ($elapsed >= 2) { proc_terminate($process, 15); }
            }
            usleep(100000);
        }
    } finally {
        // EOF also asks the Python watcher to stop if PHP exits or encounters an error.
        fclose($pipes[3]);
        $deadline = microtime(true) + 2;
        while (proc_get_status($process)['running']) {
            proc_terminate($process, microtime(true) >= $deadline ? 9 : 15);
            stream_get_contents($pipes[1]);
            usleep(100000);
        }
        fclose($pipes[1]);
        proc_close($process);
    }
}
