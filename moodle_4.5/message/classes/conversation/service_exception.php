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
 * Raised when a call to the extracted ConversationService cannot be completed.
 *
 * @package    core_message
 * @copyright  2026 Benedetta Pia Celano
 * @license    http://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class service_exception extends \moodle_exception {

    /** @var bool Whether the failure is a transport or availability problem rather than a rejection. */
    protected $transportfailure;

    /** @var int The HTTP status returned by the service, 0 when no response was received. */
    protected $status;

    /**
     * Constructor.
     *
     * @param string $message A developer readable description of the failure
     * @param int $status The HTTP status code, 0 when the service could not be reached
     * @param bool $transportfailure Whether the service could not be reached or did not answer in time
     */
    public function __construct(string $message, int $status = 0, bool $transportfailure = true) {
        $this->status = $status;
        $this->transportfailure = $transportfailure;

        parent::__construct('errorcallingconversationservice', 'message', '', null, $message);
    }

    /**
     * Whether the service could not be reached, timed out, or answered with a server error.
     *
     * @return bool
     */
    public function is_transport_failure(): bool {
        return $this->transportfailure;
    }

    /**
     * The HTTP status returned by the service.
     *
     * @return int Zero when no response was received at all
     */
    public function get_status(): int {
        return $this->status;
    }
}
