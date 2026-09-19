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
 * Abstraction over the operations owned by the notification bounded context.
 *
 * @package    core_message
 * @copyright  2026 Benedetta Pia Celano
 * @license    http://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
interface provider {

    /**
     * Persists a notification and returns its identifier.
     *
     * @param \stdClass $notification The notification to persist
     * @param bool $inpopup Whether it must appear in the notification popover
     * @return int The identifier of the stored notification
     */
    public function send_notification(\stdClass $notification, bool $inpopup = true): int;

    /**
     * Returns a notification, or null when it does not exist.
     *
     * @param int $notificationid The notification identifier
     * @return \stdClass|null
     */
    public function get_notification(int $notificationid): ?\stdClass;

    /**
     * Marks one notification as read.
     *
     * @param int $notificationid The notification identifier
     * @param int $timeread The instant to record
     * @return bool Whether the state changed
     */
    public function mark_notification_as_read(int $notificationid, int $timeread): bool;

    /**
     * Marks every unread notification of a person as read.
     *
     * @param int $useridto The recipient
     * @param int|null $useridfrom Restrict to notifications from this sender
     * @param int|null $timecreatedto Restrict to notifications created up to this instant
     * @return int How many were marked
     */
    public function mark_all_notifications_as_read(int $useridto, ?int $useridfrom = null,
            ?int $timecreatedto = null): int;

    /**
     * Returns the notifications received by a person, or sent by a person to anybody.
     *
     * @param int $useridto The recipient, zero for any
     * @param int|null $useridfrom Restrict to notifications from this sender
     * @param bool|null $read Restrict to read (true) or unread (false) ones
     * @param string $sort Either 'ASC' or 'DESC'
     * @param int $limitfrom Skip this many rows
     * @param int $limitnum Return at most this many rows, zero meaning no limit
     * @return \stdClass[]
     */
    public function get_notifications(int $useridto, ?int $useridfrom = null, ?bool $read = null,
            string $sort = 'DESC', int $limitfrom = 0, int $limitnum = 0): array;

    /**
     * Counts the unread notifications received by a person.
     *
     * @param int $useridto The recipient
     * @param int|null $useridfrom Restrict to notifications from this sender
     * @return int
     */
    public function count_unread_notifications(int $useridto, ?int $useridfrom = null): int;

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
            int $limitnum = 0): array;

    /**
     * Counts the unread notifications shown in the popover.
     *
     * @param int $useridto The recipient
     * @return int
     */
    public function count_unread_popup_notifications(int $useridto): int;

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
            ?string $component = null, ?string $eventtype = null): bool;

    /**
     * Tells whether the site holds any notification concerning this person.
     *
     * @param int $userid The person
     * @return bool
     */
    public function user_has_notifications(int $userid): bool;

    /**
     * Returns every notification concerning this person, oldest first.
     *
     * @param int $userid The person
     * @return \stdClass[]
     */
    public function get_user_notifications(int $userid): array;

    /**
     * Deletes every notification concerning this person, and returns how many.
     *
     * @param int $userid The person
     * @return int
     */
    public function delete_user_notifications(int $userid): int;

    /**
     * Deletes one notification, and says whether it existed.
     *
     * @param int $notificationid The notification identifier
     * @return bool Whether it existed
     */
    public function delete_notification(int $notificationid): bool;

    /**
     * Deletes the notifications older than the given instant.
     *
     * @param int $timestamp The cut-off instant
     * @return int How many were deleted
     */
    public function delete_notifications_created_before(int $timestamp): int;

    /**
     * Deletes the notifications read before the given instant.
     *
     * @param int $timestamp The cut-off instant
     * @return int How many were deleted
     */
    public function delete_notifications_read_before(int $timestamp): int;
}
