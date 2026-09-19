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

/**
 * Exercises the public messaging API the way the interface does, and reports what comes back.
 *
 *   sudo -u www-data php message/cli/smoke_api.php [--user=ID] [--search=TEXT] [--send] [--verbose]
 *
 * @package    core_message
 * @copyright  2026 Benedetta Pia Celano
 * @license    http://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

define('CLI_SCRIPT', true);

require(__DIR__ . '/../../config.php');
require_once($CFG->libdir . '/clilib.php');

use core_message\api;

list($options, $unrecognised) = cli_get_params([
    'help' => false,
    'user' => 0,
    'search' => 'a',
    'send' => false,
    'verbose' => false,
], ['h' => 'help', 'v' => 'verbose']);

if ($unrecognised) {
    cli_error(get_string('cliunknowoption', 'admin', implode(PHP_EOL . '  ', $unrecognised)));
}

if ($options['help']) {
    cli_writeln(<<<EOT
Exercises the public messaging API the way the interface does.

Options:
  -h, --help        Print this help.
      --user=N      Act as this user. Defaults to the one with the most conversations.
      --search=TEXT The text the search calls look for. Default 'a'.
      --send        Also send a message, which after the cutover is written to the service.
  -v, --verbose     Print the content of the answers, not only their shape.

EOT
    );
    exit(0);
}

/**
 * Chooses the user to act as: the one holding the most conversations.
 *
 * @return int
 */
function busiest_user(): int {
    global $DB;

    $records = $DB->get_records_sql("SELECT userid, COUNT(*) AS conversations
                                       FROM {message_conversation_members}
                                   GROUP BY userid
                                   ORDER BY conversations DESC, userid ASC", null, 0, 1);

    if (empty($records)) {
        cli_error('No conversation members found: there is nothing to exercise.');
    }

    return (int) array_key_first($records);
}

/**
 * Runs one call and reports what it returned, or how it failed.
 *
 * @param string $label What the report calls this call
 * @param callable $call
 * @param bool $verbose Whether to print the answer and not only its shape
 * @return bool Whether the call returned without throwing
 */
function exercise(string $label, callable $call, bool $verbose): bool {
    try {
        $answer = $call();
    } catch (\Throwable $e) {
        $detail = ($e instanceof \moodle_exception && !empty($e->debuginfo)) ? $e->debuginfo : $e->getMessage();
        cli_writeln(sprintf("  FAIL  %-46s %s: %s", $label, get_class($e), $detail));
        return false;
    }

    if (is_array($answer)) {
        $shape = sprintf('%d %s', count($answer), count($answer) === 1 ? 'entry' : 'entries');
    } else if (is_object($answer)) {
        $shape = 'a record';
    } else if (is_bool($answer)) {
        $shape = $answer ? 'true' : 'false';
    } else if (is_null($answer)) {
        $shape = 'null';
    } else {
        $shape = (string) $answer;
    }

    cli_writeln(sprintf("  ok    %-46s %s", $label, $shape));

    if ($verbose) {
        foreach (explode("\n", rtrim(var_export($answer, true))) as $line) {
            cli_writeln('          ' . $line);
        }
    }

    return true;
}

// ---------------------------------------------------------------------------------------------
// The run.
// ---------------------------------------------------------------------------------------------

$userid = (int) $options['user'] ?: busiest_user();
$search = (string) $options['search'];
$verbose = (bool) $options['verbose'];

// Act as the user being examined: the calls check capabilities against the user in session.
\core\session\manager::set_user($DB->get_record('user', ['id' => $userid], '*', MUST_EXIST));

$mode = $CFG->conversationservice_mode ?? 'legacy';
cli_writeln("Acting as user $userid, routing mode '$mode'.");
cli_writeln('');

$failures = 0;

$conversations = [];
$failures += exercise('get_conversations', function () use ($userid, &$conversations) {
    $conversations = api::get_conversations($userid, 0, 20);
    return $conversations;
}, $verbose) ? 0 : 1;

$failures += exercise('get_conversation_counts',
    fn() => api::get_conversation_counts($userid), $verbose) ? 0 : 1;

$failures += exercise('get_unread_conversation_counts',
    fn() => api::get_unread_conversation_counts($userid), $verbose) ? 0 : 1;

$failures += exercise('count_unread_conversations',
    fn() => api::count_unread_conversations($USER), $verbose) ? 0 : 1;

$failures += exercise('get_user_contacts',
    fn() => api::get_user_contacts($userid, 0, 20), $verbose) ? 0 : 1;

$failures += exercise('get_contact_requests',
    fn() => api::get_contact_requests($userid, 0, 20), $verbose) ? 0 : 1;

$failures += exercise('get_blocked_users',
    fn() => api::get_blocked_users($userid), $verbose) ? 0 : 1;

$failures += exercise("search_messages('$search')",
    fn() => api::search_messages($userid, $search, 0, 20), $verbose) ? 0 : 1;

$failures += exercise("message_search_users('$search')",
    fn() => api::message_search_users($userid, $search, 0, 20), $verbose) ? 0 : 1;

// The identifier comes from the list above.
$conversation = reset($conversations);

if (empty($conversation)) {
    cli_writeln('');
    cli_writeln('No conversation was returned, so the calls that need one are skipped.');
} else {
    $convid = (int) $conversation->id;
    cli_writeln('');
    cli_writeln("Conversation $convid, of type {$conversation->type}.");

    $failures += exercise("get_conversation($convid)",
        fn() => api::get_conversation($userid, $convid, true, true, 0, 0, 0, 0), $verbose) ? 0 : 1;

    $failures += exercise("get_conversation_messages($convid)",
        fn() => api::get_conversation_messages($userid, $convid, 0, 20), $verbose) ? 0 : 1;

    if ($options['send']) {
        cli_writeln('');
        cli_writeln('Sending a message. After the cutover this is written to the service, and returning');
        cli_writeln('to the monolithic implementation stops being free from here on.');

        $text = 'Phase 3 smoke test, ' . userdate(time(), '%d/%m/%Y %H:%M:%S');

        $sent = null;
        $failures += exercise('send_message_to_conversation',
            function () use ($userid, $convid, $text, &$sent) {
                $sent = api::send_message_to_conversation($userid, $convid, $text, FORMAT_MOODLE);
                return $sent;
            }, $verbose) ? 0 : 1;

        // The message is read back.
        $failures += exercise('the message is there when read back',
            function () use ($userid, $convid, $text) {
                $messages = api::get_conversation_messages($userid, $convid, 0, 5, 'timecreated DESC');
                foreach ($messages['messages'] ?? [] as $message) {
                    if (strpos($message->text, $text) !== false) {
                        return true;
                    }
                }
                return false;
            }, $verbose) ? 0 : 1;
    }
}

cli_writeln('');

if ($failures === 0) {
    cli_writeln('Every call answered. The layer above the boundary works in this routing mode.');
    exit(0);
}

cli_writeln("$failures calls failed. The routing mode can be reverted in config.php.");
exit(1);
