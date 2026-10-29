<?php
// Bootstrap a private configuration compatible with OneFileCMS's login hashing.
if (PHP_SAPI !== 'cli') {
    http_response_code(404);
    exit;
}
if (PHP_VERSION_ID < 70200) {
    fwrite(STDERR, "Creating credentials requires PHP 7.2 or newer.\n");
    exit(1);
}

function read_credential($prompt, $hidden = false) {
    fwrite(STDERR, $prompt);
    $terminal = function_exists('stream_isatty') && stream_isatty(STDIN);
    $mode = null;
    if ($hidden && $terminal) {
        if (!function_exists('shell_exec') || !function_exists('exec')) {
            throw new RuntimeException('Cannot hide terminal input; use piped input instead.');
        }
        $mode = trim((string)shell_exec('stty -g < /dev/tty 2>/dev/null'));
        if ($mode === '') {
            throw new RuntimeException('Cannot read terminal settings.');
        }
        exec('stty -echo < /dev/tty', $output, $status);
        if ($status !== 0) {
            throw new RuntimeException('Cannot hide password input.');
        }
    }
    try {
        $value = fgets(STDIN);
        if ($value === false) {
            throw new RuntimeException('Input ended before credentials were complete.');
        }
        return trim($value);
    } finally {
        if ($mode !== null) {
            exec('stty ' . escapeshellarg($mode) . ' < /dev/tty');
            fwrite(STDERR, "\n");
        }
    }
}

$destination = getenv('ONEFILECMS_CREDENTIALS_FILE');
if ($destination === false || $destination === '') {
    $destination = __DIR__ . '/credentials.php';
}

try {
    if (file_exists($destination)) {
        throw new RuntimeException('Credentials file already exists; it will not be overwritten.');
    }
    $username = read_credential('Username: ');
    $password = read_credential('Password: ', true);
    $confirmation = read_credential('Confirm password: ', true);
    if ($username === '' || $password === '') {
        throw new RuntimeException('Username and password must not be empty.');
    }
    if ($password !== $confirmation) {
        throw new RuntimeException('Passwords do not match.');
    }
    $salt = bin2hex(random_bytes(16));
    $hash = $password;
    // Match the 10,000 client-side and 10,001 server-side SHA-256 rounds in the CMS.
    for ($round = 0; $round < 20001; $round++) {
        $hash = hash('sha256', $hash . $salt);
    }
    unset($password, $confirmation);
    $contents = "<?php\n// Private OneFileCMS credentials; do not commit this file.\n"
        . '$USERNAME = ' . var_export($username, true) . ";\n"
        . '$HASHWORD = ' . var_export($hash, true) . ";\n"
        . '$SALT = ' . var_export($salt, true) . ";\n";
    umask(0077);
    $file = @fopen($destination, 'x');
    if ($file === false) {
        throw new RuntimeException('Cannot create credentials file; check its directory and permissions.');
    }
    if (!chmod($destination, 0600)) {
        fclose($file);
        unlink($destination);
        throw new RuntimeException('Cannot restrict credentials file permissions.');
    }
    $written = fwrite($file, $contents);
    fclose($file);
    if ($written !== strlen($contents)) {
        unlink($destination);
        throw new RuntimeException('Could not write the complete credentials file.');
    }
    fwrite(STDOUT, "Created private OneFileCMS credentials.\n");
} catch (Throwable $error) {
    fwrite(STDERR, $error->getMessage() . "\n");
    exit(1);
}
