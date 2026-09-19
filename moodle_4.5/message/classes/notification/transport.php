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
 * HTTP transport towards the extracted NotificationService.
 *
 * @package    core_message
 * @copyright  2026 Benedetta Pia Celano
 * @license    http://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class transport extends \core_message\conversation\transport {

    /**
     * Builds a transport from the site configuration.
     *
     * @return transport
     */
    public static function create_from_config(): \core_message\conversation\transport {
        global $CFG;

        return new self(
            $CFG->notificationservice_url ?? 'http://127.0.0.1:8080',
            $CFG->notificationservice_token ?? '',
            (int) ($CFG->notificationservice_timeout ?? 2),
            (int) ($CFG->notificationservice_connecttimeout ?? 1),
            (int) ($CFG->notificationservice_retries ?? 1)
        );
    }
}
