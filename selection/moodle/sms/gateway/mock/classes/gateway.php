<?php
namespace smsgateway_mock;

use core_sms\message;

defined('MOODLE_INTERNAL') || die();

class gateway extends \core_sms\gateway {

    #[\Override]
    public function send(message $message): message {
        $endpoint = get_config('smsgateway_mock', 'endpoint') ?: 'http://192.168.72.74:8080/send';
        $timeout  = (int) get_config('smsgateway_mock', 'timeout') ?: 5;

        $curl = curl_init($endpoint);
        curl_setopt_array($curl, [
            CURLOPT_POST           => true,
            CURLOPT_POSTFIELDS     => json_encode([
                'to'      => $message->recipientnumber,
                'content' => $message->content,
            ]),
            CURLOPT_HTTPHEADER     => ['Content-Type: application/json'],
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_TIMEOUT        => $timeout,
        ]);

        $response = curl_exec($curl);
        $httpcode = curl_getinfo($curl, CURLINFO_HTTP_CODE);
        $curlerror = curl_error($curl);
        curl_close($curl);

        $status = ($httpcode === 200 && $curlerror === '')
            ? \core_sms\message_status::GATEWAY_SENT
            : \core_sms\message_status::GATEWAY_FAILED;

        return $message->with(
            status: $status,
        );
    }

    #[\Override]
    public function get_send_priority(message $message): int {
        return 100;
    }
}
