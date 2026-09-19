<?php
// This file is part of Moodle - http://moodle.org/
//
// Moodle is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// Moodle is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU General Public License for more details.
//
// You should have received a copy of the GNU General Public License
// along with Moodle.  If not, see <http://www.gnu.org/licenses/>.

namespace core_message\conversation;

/**
 * HTTP transport towards the extracted ConversationService.
 *
 * @package    core_message
 * @copyright  2026 Benedetta Pia Celano
 * @license    http://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class transport {

    /** @var string Base URL of the service, without a trailing slash. */
    protected $baseurl;

    /** @var string Bearer token shared with the service. */
    protected $token;

    /** @var int Seconds allowed for the whole request. */
    protected $timeout;

    /** @var int Seconds allowed to establish the connection. */
    protected $connecttimeout;

    /** @var int How many times a request may be retried after a connection level failure. */
    protected $retries;

    /**
     * Constructor.
     *
     * @param string $baseurl Base URL of the service
     * @param string $token Bearer token shared with the service
     * @param int $timeout Seconds allowed for the whole request
     * @param int $connecttimeout Seconds allowed to establish the connection
     * @param int $retries Retries allowed after a connection level failure
     */
    public function __construct(string $baseurl, string $token, int $timeout = 2, int $connecttimeout = 1,
            int $retries = 1) {
        $this->baseurl = rtrim($baseurl, '/');
        $this->token = $token;
        $this->timeout = $timeout;
        $this->connecttimeout = $connecttimeout;
        $this->retries = $retries;
    }

    /**
     * Builds a transport from the site configuration.
     *
     * @return transport
     */
    public static function create_from_config(): transport {
        global $CFG;

        return new self(
            $CFG->conversationservice_url ?? 'http://127.0.0.1:8080',
            $CFG->conversationservice_token ?? '',
            (int) ($CFG->conversationservice_timeout ?? 2),
            (int) ($CFG->conversationservice_connecttimeout ?? 1),
            (int) ($CFG->conversationservice_retries ?? 1)
        );
    }

    /**
     * Performs a GET request.
     *
     * @param string $path Path relative to the service root, starting with a slash
     * @param array $query Query string parameters
     * @return array The decoded response body
     */
    public function get(string $path, array $query = []): array {
        return $this->request('GET', $path, null, $query);
    }

    /**
     * Performs a POST request.
     *
     * @param string $path Path relative to the service root, starting with a slash
     * @param array $payload The request body
     * @param string|null $idempotencykey Lets the service recognise a retry of the same command
     * @return array The decoded response body
     */
    public function post(string $path, array $payload, ?string $idempotencykey = null): array {
        return $this->request('POST', $path, $payload, [], $idempotencykey);
    }

    /**
     * Performs a PATCH request.
     *
     * @param string $path Path relative to the service root, starting with a slash
     * @param array $payload The request body
     * @return array The decoded response body
     */
    public function patch(string $path, array $payload): array {
        return $this->request('PATCH', $path, $payload);
    }

    /**
     * Performs a DELETE request.
     *
     * @param string $path Path relative to the service root, starting with a slash
     * @param array $query Query string parameters
     * @return array The decoded response body
     */
    public function delete(string $path, array $query = []): array {
        return $this->request('DELETE', $path, null, $query);
    }

    /**
     * Sends the request and decodes the answer.
     *
     * @param string $method The HTTP method
     * @param string $path Path relative to the service root
     * @param array|null $payload The request body, if any
     * @param array $query Query string parameters
     * @param string|null $idempotencykey Lets the service recognise a retry of the same command
     * @return array The decoded response body
     * @throws service_exception When the service cannot be reached or rejects the request
     */
    protected function request(string $method, string $path, ?array $payload = null, array $query = [],
            ?string $idempotencykey = null): array {
        global $CFG, $USER;

        require_once($CFG->libdir . '/filelib.php');

        $url = $this->baseurl . $path;
        if (!empty($query)) {
            $url .= '?' . http_build_query($query, '', '&');
        }

        $headers = [
            'Accept: application/json',
            'Content-Type: application/json',
            'Authorization: Bearer ' . $this->token,
            'X-Request-Id: ' . self::get_request_id(),
        ];
        if (!empty($USER->id)) {
            $headers[] = 'X-Moodle-Actor-Id: ' . (int) $USER->id;
        }
        if (!is_null($idempotencykey)) {
            $headers[] = 'Idempotency-Key: ' . $idempotencykey;
        }

        $body = is_null($payload) ? null : json_encode($payload, JSON_UNESCAPED_UNICODE);
        $attempts = $this->retries + 1;
        $lasterror = '';

        for ($attempt = 1; $attempt <= $attempts; $attempt++) {
            // The security helper is bypassed: its defaults block 192.168.0.0/16 and ports other
            // than 80/443. The address comes from config.php and no other host can be reached
            // through this class.
            $curl = new \curl(['ignoresecurity' => true]);
            $curl->setHeader($headers);

            $options = [
                'CURLOPT_TIMEOUT' => $this->timeout,
                'CURLOPT_CONNECTTIMEOUT' => $this->connecttimeout,
                'CURLOPT_FOLLOWLOCATION' => 0,
            ];

            switch ($method) {
                case 'GET':
                    $response = $curl->get($url, [], $options);
                    break;
                case 'POST':
                    $response = $curl->post($url, $body, $options);
                    break;
                case 'PATCH':
                    $response = $curl->patch($url, $body, $options);
                    break;
                case 'DELETE':
                    $response = $curl->delete($url, [], $options);
                    break;
                default:
                    throw new \coding_exception("Unsupported HTTP method '$method'.");
            }

            $info = $curl->get_info();
            $status = (int) ($info['http_code'] ?? 0);

            if ($curl->get_errno()) {
                // No response: the command may not have reached the service.
                $lasterror = $curl->error;
                continue;
            }

            return $this->decode($response, $status, $method, $path);
        }

        throw new service_exception("$method $path could not be completed after $attempts attempts: $lasterror");
    }

    /**
     * Turns the raw response into an array, mapping unsuccessful statuses to exceptions.
     *
     * @param string $response The raw response body
     * @param int $status The HTTP status code
     * @param string $method The HTTP method, for diagnostics
     * @param string $path The requested path, for diagnostics
     * @return array
     * @throws service_exception
     */
    protected function decode($response, int $status, string $method, string $path): array {
        if ($status === 204 || $response === '') {
            return [];
        }

        $decoded = json_decode($response, true);

        if ($status >= 500 || $status === 0) {
            $detail = is_array($decoded) ? ($decoded['detail'] ?? '') : '';
            throw new service_exception("$method $path failed with status $status: $detail", $status, true);
        }

        if ($status >= 400) {
            $detail = is_array($decoded) ? ($decoded['detail'] ?? '') : '';
            // A 4xx is an answer, not an outage.
            throw new service_exception("$method $path was rejected with status $status: $detail", $status, false);
        }

        if (!is_array($decoded)) {
            throw new service_exception("$method $path returned a body that is not valid JSON.", $status, true);
        }

        return $decoded;
    }

    /**
     * Returns an identifier shared by every service call made while serving the current request.
     *
     * @return string
     */
    protected static function get_request_id(): string {
        static $requestid = null;

        if (is_null($requestid)) {
            $requestid = bin2hex(random_bytes(8));
        }

        return $requestid;
    }
}
