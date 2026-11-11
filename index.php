<?php
// Copyright 2025 Brigham Young University. All rights reserved.
header('Content-Type: application/json');
ini_set('display_errors', '0');

function fail_request($status, $message) {
    http_response_code($status);
    echo json_encode(['text' => $message]);
    exit;
}

function valid_slack_signature($secret, $timestamp, $signature, $body, $now) {
    if (!is_string($timestamp) || !ctype_digit($timestamp) || abs($now - (int)$timestamp) > 300) {
        return false;
    }
    $expected = 'v0=' . hash_hmac('sha256', 'v0:' . $timestamp . ':' . $body, $secret);
    return hash_equals($expected, $signature);
}

function run_python_slackbot() {
    $secret = getenv('SLACK_SIGNING_SECRET');
    if (!$secret) {
        error_log('Slack bot: SLACK_SIGNING_SECRET is missing');
        fail_request(503, 'The bot is not configured. Please contact a TA.');
    }
    $body = file_get_contents('php://input');
    if (!valid_slack_signature($secret, $_SERVER['HTTP_X_SLACK_REQUEST_TIMESTAMP'] ?? '',
                              $_SERVER['HTTP_X_SLACK_SIGNATURE'] ?? '', $body, time())) {
        fail_request(401, 'Invalid Slack request.');
    }
    // Parse exactly the body that was authenticated. Do not trust query parameters.
    parse_str($body, $post);
    $stderr = tmpfile();
    if ($stderr === false) {
        fail_request(500, 'The bot could not complete this request. Please contact a TA.');
    }
    $process = proc_open(
        ['python3', __DIR__ . '/slackbot.py', '--stdin'],
        [0 => ['pipe', 'r'], 1 => ['pipe', 'w'], 2 => $stderr],
        $pipes, __DIR__
    );
    if (!is_resource($process)) {
        fclose($stderr);
        error_log('Slack bot: could not start Python');
        fail_request(500, 'The bot could not complete this request. Please contact a TA.');
    }
    // Request tokens stay out of shell commands and process argument listings.
    fwrite($pipes[0], json_encode($post));
    fclose($pipes[0]);
    $output = stream_get_contents($pipes[1]);
    fclose($pipes[1]);
    $exitCode = proc_close($process);
    rewind($stderr);
    $errors = stream_get_contents($stderr, 16384);
    fclose($stderr);
    if ($errors !== '') {
        error_log('Slack bot Python: ' . $errors);
    }
    if ($exitCode !== 0) {
        fail_request(500, 'The bot could not complete this request. Please contact a TA.');
    }
    if (trim($output) !== '') {
        $decoded = json_decode($output);
        if (json_last_error() !== JSON_ERROR_NONE || !is_object($decoded)) {
            error_log('Slack bot: Python returned an invalid response');
            fail_request(500, 'The bot could not complete this request. Please contact a TA.');
        }
    }
    echo $output;
}

try {
    run_python_slackbot();
} catch (Throwable $error) {
    error_log('Slack bot endpoint failed: ' . get_class($error));
    fail_request(500, 'The bot could not complete this request. Please contact a TA.');
}
