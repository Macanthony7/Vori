<?php
// Create a payment and verify webhooks, PHP 7.4+ with the curl extension.
// Keep the API key on your server: set GATEWAY_API_KEY (and GATEWAY_URL) in its environment.
$GATEWAY_URL = getenv('GATEWAY_URL') ?: 'https://pay.example.com';
$API_KEY = getenv('GATEWAY_API_KEY');

function create_payment($amount, $chain, $asset, $reference) {
    global $GATEWAY_URL, $API_KEY;
    $ch = curl_init("$GATEWAY_URL/payments");
    curl_setopt_array($ch, [
        CURLOPT_POST => true,
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_TIMEOUT => 15,
        CURLOPT_HTTPHEADER => ["Authorization: Bearer $API_KEY", 'Content-Type: application/json'],
        CURLOPT_POSTFIELDS => json_encode(['chain' => $chain, 'asset' => $asset, 'amount' => $amount, 'reference' => $reference]),
    ]);
    $body = curl_exec($ch);
    $status = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);
    if ($status !== 201) throw new Exception("HTTP $status: $body");
    return json_decode($body, true); // send the customer to $payment['checkout_url']
}

// $raw_body = file_get_contents('php://input'); $header = $_SERVER['HTTP_GATEWAY_SIGNATURE'];
function verify_webhook($secret, $raw_body, $header, $tolerance = 300) {
    $fields = [];
    foreach (explode(',', $header) as $part) {
        $kv = explode('=', $part, 2);
        if (count($kv) === 2) $fields[$kv[0]] = $kv[1];
    }
    if (!isset($fields['t'], $fields['v1']) || !ctype_digit($fields['t'])) return false;
    if (abs(time() - (int)$fields['t']) > $tolerance) return false;
    $expected = hash_hmac('sha256', $fields['t'] . '.' . $raw_body, $secret);
    return hash_equals($expected, $fields['v1']);
}

if (PHP_SAPI === 'cli' && realpath($argv[0] ?? '') === __FILE__) {
    if (($argv[1] ?? '') === 'verify') { // used by the tests: php php.php verify SECRET HEADER < body
        echo verify_webhook($argv[2], stream_get_contents(STDIN), $argv[3]) ? "valid\n" : "invalid\n";
    } else {
        echo create_payment('0.001', 'BTC', 'BTC', 'order-1042')['checkout_url'], "\n";
    }
}
