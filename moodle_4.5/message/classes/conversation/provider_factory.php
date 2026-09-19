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
 * Decides which implementation of the conversation context serves each call.
 *
 * @package    core_message
 * @copyright  2026 Benedetta Pia Celano
 * @license    http://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class provider_factory {

    /** @var string Every call is served by the monolithic implementation. */
    const MODE_LEGACY = 'legacy';

    /** @var string Every call is served by the extracted service. */
    const MODE_REMOTE = 'remote';

    /** @var provider[] Implementations already built during this request, keyed by routing decision. */
    protected static $instances = [];

    /**
     * Returns the implementation that must serve the calls of the conversation context.
     *
     * @param int|null $routinguserid The user the call is about, kept for the callers' expressiveness
     * @return provider
     */
    public static function get(?int $routinguserid = null): provider {
        $useservice = self::routes_to_service();
        $key = $useservice ? 'remote' : 'legacy';

        if (!isset(self::$instances[$key])) {
            self::$instances[$key] = $useservice ? self::build_remote() : new legacy_provider();
        }

        return self::$instances[$key];
    }

    /**
     * Returns the monolithic implementation, whatever the current routing rule is.
     *
     * @return provider
     */
    public static function get_legacy(): provider {
        return new legacy_provider();
    }

    /**
     * Discards the implementations built during this request.
     *
     * @return void
     */
    public static function reset(): void {
        self::$instances = [];
    }

    /**
     * Applies the routing rule.
     *
     * @return bool Whether the calls must be served by the service
     */
    public static function routes_to_service(): bool {
        global $CFG;

        return ($CFG->conversationservice_mode ?? self::MODE_LEGACY) === self::MODE_REMOTE;
    }

    /**
     * Builds the service backed implementation.
     *
     * @return provider
     */
    protected static function build_remote(): provider {
        return new remote_provider();
    }
}
