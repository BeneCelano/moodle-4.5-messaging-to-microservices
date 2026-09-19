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

/**
 * The in-process implementation of the notification context.
 *
 * @package    core_message
 * @copyright  2026 Benedetta Pia Celano
 * @license    http://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class legacy_provider implements provider {

    /**
     * Persists a notification and returns its identifier.
     *
     * @param \stdClass $notification The notification to persist
     * @param bool $inpopup Whether it must appear in the notification popover
     * @return int The identifier of the stored notification
     */
    public function send_notification(\stdClass $notification, bool $inpopup = true): int {
        global $DB;

        $record = (object) [
            'useridfrom' => $notification->useridfrom,
            'useridto' => $notification->useridto,
            'subject' => $notification->subject ?? '',
            'fullmessage' => $notification->fullmessage ?? '',
            'fullmessageformat' => $notification->fullmessageformat ?? FORMAT_MOODLE,
            'fullmessagehtml' => $notification->fullmessagehtml ?? '',
            'smallmessage' => $notification->smallmessage ?? '',
            'component' => $notification->component ?? null,
            'eventtype' => $notification->eventtype ?? null,
            'contexturl' => $notification->contexturl ?? null,
            'contexturlname' => $notification->contexturlname ?? null,
            'timeread' => $notification->timeread ?? null,
            'timecreated' => $notification->timecreated ?? time(),
            'customdata' => $notification->customdata ?? null,
        ];

        $id = $DB->insert_record('notifications', $record);

        if ($inpopup && !$DB->record_exists('message_popup_notifications', ['notificationid' => $id])) {
            $DB->insert_record('message_popup_notifications', (object) ['notificationid' => $id]);
        }

        return $id;
    }

    /**
     * Returns a notification, or null when it does not exist.
     *
     * @param int $notificationid The notification identifier
     * @return \stdClass|null
     */
    public function get_notification(int $notificationid): ?\stdClass {
        global $DB;

        $record = $DB->get_record('notifications', ['id' => $notificationid]);

        return $record ?: null;
    }

    /**
     * Marks one notification as read.
     *
     * @param int $notificationid The notification identifier
     * @param int $timeread The instant to record
     * @return bool Whether the state changed
     */
    public function mark_notification_as_read(int $notificationid, int $timeread): bool {
        global $DB;

        $notification = $DB->get_record('notifications', ['id' => $notificationid]);

        if (!$notification || !is_null($notification->timeread)) {
            return false;
        }

        $DB->set_field('notifications', 'timeread', $timeread, ['id' => $notificationid]);

        return true;
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
        global $DB;

        $select = 'useridto = :useridto AND timeread IS NULL';
        $params = ['useridto' => $useridto];

        if (!empty($useridfrom)) {
            $select .= ' AND useridfrom = :useridfrom';
            $params['useridfrom'] = $useridfrom;
        }
        if (!empty($timecreatedto)) {
            $select .= ' AND timecreated <= :timecreatedto';
            $params['timecreatedto'] = $timecreatedto;
        }

        // Counted before the update: both implementations return the same number.
        $count = $DB->count_records_select('notifications', $select, $params);

        if ($count) {
            $DB->set_field_select('notifications', 'timeread', time(), $select, $params);
        }

        return $count;
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
        global $DB;

        $conditions = [];
        $params = [];

        if (!empty($useridto)) {
            $conditions[] = 'useridto = :useridto';
            $params['useridto'] = $useridto;
        }
        if (!empty($useridfrom)) {
            $conditions[] = 'useridfrom = :useridfrom';
            $params['useridfrom'] = $useridfrom;
        }
        if (!$conditions) {
            throw new \coding_exception('A notification read must name either the recipient or the sender.');
        }

        $select = implode(' AND ', $conditions);
        if (!is_null($read)) {
            $select .= $read ? ' AND timeread IS NOT NULL' : ' AND timeread IS NULL';
        }

        $records = $DB->get_records_select('notifications', $select, $params,
                self::order_by($sort), '*', $limitfrom, $limitnum);

        return array_values($records);
    }

    /**
     * Counts the unread notifications received by a person.
     *
     * @param int $useridto The recipient
     * @param int|null $useridfrom Restrict to notifications from this sender
     * @return int
     */
    public function count_unread_notifications(int $useridto, ?int $useridfrom = null): int {
        global $DB;

        $select = 'useridto = :useridto AND timeread IS NULL';
        $params = ['useridto' => $useridto];

        if (!empty($useridfrom)) {
            $select .= ' AND useridfrom = :useridfrom';
            $params['useridfrom'] = $useridfrom;
        }

        return $DB->count_records_select('notifications', $select, $params);
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
        global $DB;

        $sql = "SELECT n.*
                  FROM {notifications} n
                 WHERE n.id IN (SELECT notificationid FROM {message_popup_notifications})
                   AND n.useridto = :useridto
              ORDER BY " . self::order_by($sort);

        $records = $DB->get_records_sql($sql, ['useridto' => $useridto], $limitfrom, $limitnum);

        return array_values($records);
    }

    /**
     * Counts the unread notifications shown in the popover.
     *
     * @param int $useridto The recipient
     * @return int
     */
    public function count_unread_popup_notifications(int $useridto): int {
        global $DB;

        return $DB->count_records_sql(
            "SELECT COUNT(id)
               FROM {notifications}
              WHERE id IN (SELECT notificationid FROM {message_popup_notifications})
                AND useridto = :useridto
                AND timeread IS NULL",
            ['useridto' => $useridto]
        );
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
        global $DB;

        $select = $DB->sql_compare_text('customdata', 255) . ' = ' .
                $DB->sql_compare_text(':customdata', 255) . ' AND useridto = :useridto';
        $params = ['useridto' => $useridto, 'customdata' => $customdata];

        if (!is_null($component)) {
            $select .= ' AND component = :component';
            $params['component'] = $component;
        }
        if (!is_null($eventtype)) {
            $select .= ' AND eventtype = :eventtype';
            $params['eventtype'] = $eventtype;
        }

        return $DB->record_exists_select('notifications', $select, $params);
    }

    /**
     * Tells whether the site holds any notification concerning this person.
     *
     * @param int $userid The person
     * @return bool
     */
    public function user_has_notifications(int $userid): bool {
        global $DB;

        return $DB->record_exists_select('notifications', 'useridfrom = ? OR useridto = ?',
                [$userid, $userid]);
    }

    /**
     * Returns every notification concerning this person, oldest first.
     *
     * @param int $userid The person
     * @return \stdClass[]
     */
    public function get_user_notifications(int $userid): array {
        global $DB;

        return array_values($DB->get_records_select('notifications',
                'useridfrom = ? OR useridto = ?', [$userid, $userid], 'timecreated ASC, id ASC'));
    }

    /**
     * Deletes every notification concerning this person, and returns how many.
     *
     * @param int $userid The person
     * @return int
     */
    public function delete_user_notifications(int $userid): int {
        global $DB;

        $select = 'useridfrom = ? OR useridto = ?';
        $params = [$userid, $userid];

        $count = $DB->count_records_select('notifications', $select, $params);

        $DB->delete_records_select('message_popup_notifications',
                'notificationid IN (SELECT id FROM {notifications} WHERE useridfrom = ? OR useridto = ?)',
                $params);
        $DB->delete_records_select('notifications', $select, $params);

        return $count;
    }

    /**
     * Deletes one notification, and says whether it existed.
     *
     * @param int $notificationid The notification identifier
     * @return bool Whether it existed
     */
    public function delete_notification(int $notificationid): bool {
        global $DB;

        if (!$DB->record_exists('notifications', ['id' => $notificationid])) {
            return false;
        }

        $DB->delete_records('message_popup_notifications', ['notificationid' => $notificationid]);
        $DB->delete_records('notifications', ['id' => $notificationid]);

        return true;
    }

    /**
     * Deletes the notifications older than the given instant.
     *
     * @param int $timestamp The cut-off instant
     * @return int How many were deleted
     */
    public function delete_notifications_created_before(int $timestamp): int {
        global $DB;

        $count = $DB->count_records_select('notifications', 'timecreated < :timestamp',
                ['timestamp' => $timestamp]);

        $DB->delete_records_select('message_popup_notifications',
                'notificationid IN (SELECT id FROM {notifications} WHERE timecreated < :timestamp)',
                ['timestamp' => $timestamp]);
        $DB->delete_records_select('notifications', 'timecreated < :timestamp',
                ['timestamp' => $timestamp]);

        return $count;
    }

    /**
     * Deletes the notifications read before the given instant.
     *
     * @param int $timestamp The cut-off instant
     * @return int How many were deleted
     */
    public function delete_notifications_read_before(int $timestamp): int {
        global $DB;

        $select = 'timeread IS NOT NULL AND timeread < :timestamp';
        $count = $DB->count_records_select('notifications', $select, ['timestamp' => $timestamp]);

        $DB->delete_records_select('message_popup_notifications',
                'notificationid IN (SELECT id FROM {notifications} WHERE timeread < :timestamp)',
                ['timestamp' => $timestamp]);
        $DB->delete_records_select('notifications', $select, ['timestamp' => $timestamp]);

        return $count;
    }

    /**
     * Returns the ordering clause used by every read of this context.
     *
     * @param string $sort Either 'ASC' or 'DESC'
     * @return string
     */
    protected static function order_by(string $sort): string {
        $direction = strtoupper($sort) === 'ASC' ? 'ASC' : 'DESC';

        return "timecreated $direction, timeread $direction, id $direction";
    }
}
