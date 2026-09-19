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

namespace core_message\notification;

use core_message\conversation\service_exception;

/**
 * The client of the extracted NotificationService.
 *
 * @package    core_message
 * @copyright  2026 Benedetta Pia Celano
 * @license    http://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class remote_provider implements provider {

    /** @var \core_message\conversation\transport The HTTP transport. */
    protected $transport;

    /**
     * Constructor.
     *
     * @param \core_message\conversation\transport|null $transport The transport, built from config when omitted
     */
    public function __construct(?\core_message\conversation\transport $transport = null) {
        $this->transport = $transport ?? transport::create_from_config();
    }

    /**
     * Persists a notification and returns its identifier.
     *
     * @param \stdClass $notification The notification to persist
     * @param bool $inpopup Whether it must appear in the notification popover
     * @return int The identifier of the stored notification
     */
    public function send_notification(\stdClass $notification, bool $inpopup = true): int {
        $payload = [
            'user_id_from' => (int) $notification->useridfrom,
            'user_id_to' => (int) $notification->useridto,
            'component' => (string) ($notification->component ?? ''),
            'event_type' => (string) ($notification->eventtype ?? ''),
            'subject' => (string) ($notification->subject ?? ''),
            'full_message' => (string) ($notification->fullmessage ?? ''),
            'full_message_format' => (int) ($notification->fullmessageformat ?? FORMAT_MOODLE),
            'full_message_html' => (string) ($notification->fullmessagehtml ?? ''),
            'small_message' => (string) ($notification->smallmessage ?? ''),
            'context_url' => $notification->contexturl ?? null,
            'context_url_name' => $notification->contexturlname ?? null,
            'custom_data' => $notification->customdata ?? null,
            'in_popup' => $inpopup,
        ];

        // The idempotency key is unique per call: it identifies the command, not the notification.
        $key = bin2hex(random_bytes(16));

        $response = $this->transport->post('/api/v1/notifications', $payload, $key);

        return (int) ($response['id'] ?? 0);
    }

    /**
     * Returns a notification, or null when it does not exist.
     *
     * @param int $notificationid The notification identifier
     * @return \stdClass|null
     */
    public function get_notification(int $notificationid): ?\stdClass {
        try {
            $response = $this->transport->get('/api/v1/notifications/' . $notificationid);
        } catch (service_exception $exception) {
            // get_status(), not getCode(): service_exception keeps the HTTP status in its own
            // field.
            if ($exception->get_status() === 404) {
                return null;
            }
            throw $exception;
        }

        return self::to_record($response);
    }

    /**
     * Marks one notification as read.
     *
     * @param int $notificationid The notification identifier
     * @param int $timeread The instant to record
     * @return bool Whether the state changed
     */
    public function mark_notification_as_read(int $notificationid, int $timeread): bool {
        $response = $this->transport->post(
            '/api/v1/notifications/' . $notificationid . '/read?time_read=' . $timeread,
            []
        );

        return (bool) ($response['changed'] ?? false);
    }

    /**
     * Marks every unread notification of a person as read.
     *
     * @param int $useridto The recipient
     * @param int|null $useridfrom Restrict to notifications from this sender
     * @param int|null $timecreatedto Restrict to notifications created up to this instant
     * @return int How many were marked
     */
    public function mark_all_notifications_as_read(int $useridto, ?int $useridfrom = null,
            ?int $timecreatedto = null): int {
        $query = [];
        if (!empty($useridfrom)) {
            $query['user_id_from'] = $useridfrom;
        }
        if (!empty($timecreatedto)) {
            $query['time_created_to'] = $timecreatedto;
        }

        $path = '/api/v1/users/' . $useridto . '/notifications/read-all';
        if (!empty($query)) {
            $path .= '?' . http_build_query($query, '', '&');
        }

        $response = $this->transport->post($path, []);

        return (int) ($response['marked'] ?? 0);
    }

    /**
     * Returns the notifications received by a person.
     *
     * @param int $useridto The recipient
     * @param int|null $useridfrom Restrict to notifications from this sender
     * @param bool|null $read Restrict to read (true) or unread (false) ones
     * @param string $sort Either 'ASC' or 'DESC'
     * @param int $limitfrom Skip this many rows
     * @param int $limitnum Return at most this many rows, zero meaning no limit
     * @return \stdClass[]
     */
    public function get_notifications(int $useridto, ?int $useridfrom = null, ?bool $read = null,
            string $sort = 'DESC', int $limitfrom = 0, int $limitnum = 0): array {
        $query = ['sort' => $sort, 'limit_from' => $limitfrom, 'limit_num' => $limitnum];

        if (!empty($useridfrom)) {
            $query['user_id_from'] = $useridfrom;
        }
        if (!is_null($read)) {
            $query['read'] = $read ? 'true' : 'false';
        }

        // Two routes: the one with the user in the path for the self-service read, the broad one
        // for notifications sent by somebody to anybody.
        if (empty($useridto)) {
            $path = '/api/v1/notifications';
        } else {
            $path = '/api/v1/users/' . $useridto . '/notifications';
        }

        $response = $this->transport->get($path, $query);

        return array_map([self::class, 'to_record'], $response['notifications'] ?? []);
    }

    /**
     * Counts the unread notifications received by a person.
     *
     * @param int $useridto The recipient
     * @param int|null $useridfrom Restrict to notifications from this sender
     * @return int
     */
    public function count_unread_notifications(int $useridto, ?int $useridfrom = null): int {
        $query = empty($useridfrom) ? [] : ['user_id_from' => $useridfrom];

        $response = $this->transport->get(
            '/api/v1/users/' . $useridto . '/notifications/unread-count', $query);

        return (int) ($response['count'] ?? 0);
    }

    /**
     * Returns the notifications shown in the popover.
     *
     * @param int $useridto The recipient
     * @param string $sort Either 'ASC' or 'DESC'
     * @param int $limitfrom Skip this many rows
     * @param int $limitnum Return at most this many rows, zero meaning no limit
     * @return \stdClass[]
     */
    public function get_popup_notifications(int $useridto, string $sort = 'DESC', int $limitfrom = 0,
            int $limitnum = 0): array {
        $response = $this->transport->get('/api/v1/users/' . $useridto . '/notifications/popup', [
            'sort' => $sort,
            'limit_from' => $limitfrom,
            'limit_num' => $limitnum,
        ]);

        return array_map([self::class, 'to_record'], $response['notifications'] ?? []);
    }

    /**
     * Counts the unread notifications shown in the popover.
     *
     * @param int $useridto The recipient
     * @return int
     */
    public function count_unread_popup_notifications(int $useridto): int {
        $response = $this->transport->get(
            '/api/v1/users/' . $useridto . '/notifications/popup/unread-count');

        return (int) ($response['count'] ?? 0);
    }

    /**
     * Tells whether a person has already received a notification carrying this custom data.
     *
     * @param int $useridto The recipient
     * @param string $customdata The serialised custom data to match
     * @param string|null $component Restrict to this component
     * @param string|null $eventtype Restrict to this event type
     * @return bool
     */
    public function notification_exists_with_custom_data(int $useridto, string $customdata,
            ?string $component = null, ?string $eventtype = null): bool {
        $query = ['custom_data' => $customdata];

        if (!is_null($component)) {
            $query['component'] = $component;
        }
        if (!is_null($eventtype)) {
            $query['event_type'] = $eventtype;
        }

        $response = $this->transport->get(
            '/api/v1/users/' . $useridto . '/notifications/exists', $query);

        return (bool) ($response['exists'] ?? false);
    }

    /**
     * Tells whether the site holds any notification concerning this person.
     *
     * @param int $userid The person
     * @return bool
     */
    public function user_has_notifications(int $userid): bool {
        $response = $this->transport->get('/api/v1/users/' . $userid . '/personal-data/exists');

        return (bool) ($response['exists'] ?? false);
    }

    /**
     * Returns every notification concerning this person, oldest first.
     *
     * @param int $userid The person
     * @return \stdClass[]
     */
    public function get_user_notifications(int $userid): array {
        $response = $this->transport->get(
            '/api/v1/users/' . $userid . '/personal-data/notifications');

        return array_map([self::class, 'to_record'], $response['notifications'] ?? []);
    }

    /**
     * Deletes every notification concerning this person, and returns how many.
     *
     * @param int $userid The person
     * @return int
     */
    public function delete_user_notifications(int $userid): int {
        $response = $this->transport->delete(
            '/api/v1/users/' . $userid . '/personal-data/notifications');

        return (int) ($response['deleted'] ?? 0);
    }

    /**
     * Deletes one notification, and says whether it existed.
     *
     * @param int $notificationid The notification identifier
     * @return bool Whether it existed
     */
    public function delete_notification(int $notificationid): bool {
        $esisteva = !is_null($this->get_notification($notificationid));

        $this->transport->delete('/api/v1/notifications/' . $notificationid);

        return $esisteva;
    }

    /**
     * Deletes the notifications older than the given instant.
     *
     * @param int $timestamp The cut-off instant
     * @return int How many were deleted
     */
    public function delete_notifications_created_before(int $timestamp): int {
        $response = $this->transport->post('/api/v1/maintenance/cleanup', [
            'created_before' => $timestamp,
            'read_before' => 0,
        ]);

        return (int) ($response['deleted_by_age'] ?? 0);
    }

    /**
     * Deletes the notifications read before the given instant.
     *
     * @param int $timestamp The cut-off instant
     * @return int How many were deleted
     */
    public function delete_notifications_read_before(int $timestamp): int {
        $response = $this->transport->post('/api/v1/maintenance/cleanup', [
            'created_before' => 0,
            'read_before' => $timestamp,
        ]);

        return (int) ($response['deleted_by_read'] ?? 0);
    }

    /**
     * Turns a notification of the service contract into the record Moodle expects.
     *
     * @param array $data A notification as the service returns it
     * @return \stdClass
     */
    protected static function to_record(array $data): \stdClass {
        return (object) [
            'id' => (int) $data['id'],
            'useridfrom' => (int) $data['user_id_from'],
            'useridto' => (int) $data['user_id_to'],
            // `?? null`: these four columns are nullable, and NULL is returned as such.
            'subject' => $data['subject'] ?? null,
            'fullmessage' => $data['full_message'] ?? null,
            'fullmessageformat' => (int) ($data['full_message_format'] ?? FORMAT_MOODLE),
            'fullmessagehtml' => $data['full_message_html'] ?? null,
            'smallmessage' => $data['small_message'] ?? null,
            'component' => $data['component'] ?? null,
            'eventtype' => $data['event_type'] ?? null,
            'contexturl' => $data['context_url'] ?? null,
            'contexturlname' => $data['context_url_name'] ?? null,
            'timeread' => is_null($data['time_read'] ?? null) ? null : (int) $data['time_read'],
            'timecreated' => (int) $data['time_created'],
            'customdata' => $data['custom_data'] ?? null,
        ];
    }
}
