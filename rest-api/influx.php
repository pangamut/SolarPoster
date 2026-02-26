<?php
/**
 * influx.php — Minimal InfluxDB line protocol proxy
 *
 * Accepts POST with InfluxDB line protocol body and forwards it
 * to a local InfluxDB v2 instance.
 *
 * POST /influx.php
 * Content-Type: text/plain
 * Body: <line protocol>
 */

define('INFLUX_URL',    'http://10.1.5.21:8086/api/v2/write');
define('INFLUX_ORG',    'solar');
define('INFLUX_BUCKET', 'solar');
define('INFLUX_TOKEN',  '0123456789abcdef0123456789abcdef');

// Only accept POST
if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    http_response_code(405);
    echo "Method Not Allowed\n";
    exit;
}

// Read body
$body = file_get_contents('php://input');
if (empty($body)) {
    http_response_code(400);
    echo "Empty body\n";
    exit;
}

// Forward to InfluxDB
$url = INFLUX_URL . '?' . http_build_query([
    'org'       => INFLUX_ORG,
    'bucket'    => INFLUX_BUCKET,
    'precision' => 'ns',
]);

$ctx = stream_context_create([
    'http' => [
        'method'  => 'POST',
        'header'  => implode("\r\n", [
            'Authorization: Token ' . INFLUX_TOKEN,
            'Content-Type: text/plain; charset=utf-8',
            'Content-Length: ' . strlen($body),
        ]),
        'content'         => $body,
        'timeout'         => 10,
        'ignore_errors'   => true,
    ],
]);

$response = file_get_contents($url, false, $ctx);
$status   = $http_response_header[0] ?? 'HTTP/1.1 502 No Response';

// Parse status code from response header
preg_match('/HTTP\/\S+\s+(\d+)/', $status, $m);
$code = isset($m[1]) ? (int)$m[1] : 502;

http_response_code($code);

if ($code >= 200 && $code < 300) {
    echo "ok\n";
} else {
    error_log("influx.php: InfluxDB returned $code: $response");
    echo "upstream error $code\n";
}
