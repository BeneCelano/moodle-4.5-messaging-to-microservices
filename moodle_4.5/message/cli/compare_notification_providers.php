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
 * Asks the same questions of both implementations of the notification context and compares the answers.
 *
 *   sudo -u www-data php message/cli/compare_notification_providers.php [--users=N] [--notifications=N]
 *       [--skip-reads] [--skip-writes] [--verbose]
 *
 * @package    core_message
 * @copyright  2026 Benedetta Pia Celano
 * @license    http://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

define('CLI_SCRIPT', true);

require(__DIR__ . '/../../config.php');
require_once($CFG->libdir . '/clilib.php');
require_once($CFG->dirroot . '/user/lib.php');

use core_message\cli\comparison;
use core_message\notification\legacy_provider;
use core_message\notification\provider;
use core_message\notification\remote_provider;

/** Seconds between two writes whose relative order the reads will observe; see build_write_scenarios(). */
const CLOCK_SKEW_GUARD = 2;

[$options, $unrecognised] = cli_get_params(
    [
        'help' => false,
        'verbose' => false,
        'skip-reads' => false,
        'skip-writes' => false,
        'users' => 6,
        'notifications' => 8,
    ],
    ['h' => 'help', 'v' => 'verbose']
);

if ($unrecognised) {
    cli_error(get_string('cliunknowoption', 'admin', implode(PHP_EOL . '  ', $unrecognised)));
}

if ($options['help']) {
    cli_writeln("Compares the two implementations of the notification context.

Options:
  -h, --help           Print this help
  -v, --verbose        Print the probes that agreed, not only those that did not
      --skip-reads     Skip the read phase
      --skip-writes    Skip the write phase, so that nothing is written anywhere
      --users N        How many users to sample for the read phase (default 6)
      --notifications N  How many notifications to sample (default 8)

Example:
  sudo -u www-data php message/cli/compare_notification_providers.php --verbose
");
    exit(0);
}

$legacy = new legacy_provider();
$remote = new remote_provider();

// The store-assigned identifiers of this context: the notification's own and the popover row's.
$confronto = new comparison(
    ['id', 'notificationid'],
    ['timecreated', 'timeread']
);

// ---------------------------------------------------------------------------------------------
// Choosing the data.
// ---------------------------------------------------------------------------------------------

/**
 * Returns the identifiers of users who actually have notifications.
 *
 * @param int $limit
 * @return int[]
 */
function pick_recipients(int $limit): array {
    global $DB;

    $records = $DB->get_records_sql(
        "SELECT useridto AS id, COUNT(*) AS howmany
           FROM {notifications}
          WHERE useridto > 0
       GROUP BY useridto
       ORDER BY howmany DESC, useridto ASC",
        null, 0, $limit
    );

    return array_map('intval', array_keys($records));
}

/**
 * Returns the identifiers of senders that appear in the notifications table.
 *
 * @param int $limit
 * @return int[]
 */
function pick_senders(int $limit): array {
    global $DB;

    $records = $DB->get_records_sql(
        "SELECT useridfrom AS id, COUNT(*) AS howmany
           FROM {notifications}
       GROUP BY useridfrom
       ORDER BY howmany DESC, useridfrom ASC",
        null, 0, $limit
    );

    return array_map('intval', array_keys($records));
}

/**
 * Returns the identifiers of recent notifications.
 *
 * @param int $limit
 * @return int[]
 */
function pick_notifications(int $limit): array {
    global $DB;

    $records = $DB->get_records_sql(
        "SELECT id FROM {notifications} ORDER BY id DESC", null, 0, $limit);

    return array_map('intval', array_keys($records));
}

/**
 * Returns a recipient and a piece of custom data that actually belong together.
 *
 * @return array{0: int, 1: string, 2: string, 3: string}|null
 */
function pick_custom_data(): ?array {
    global $DB;

    $record = $DB->get_record_sql(
        "SELECT useridto, customdata, component, eventtype
           FROM {notifications}
          WHERE customdata IS NOT NULL AND " . $DB->sql_isnotempty('notifications', 'customdata', true, false) . "
       ORDER BY id DESC",
        null, IGNORE_MULTIPLE
    );

    if (!$record) {
        return null;
    }

    return [
        (int) $record->useridto,
        (string) $record->customdata,
        (string) $record->component,
        (string) $record->eventtype,
    ];
}

// ---------------------------------------------------------------------------------------------
// The probes of the read phase.
// ---------------------------------------------------------------------------------------------

/**
 * Builds the questions anchored on a recipient.
 *
 * @param int[] $recipients
 * @param int[] $senders
 * @return callable[] Indexed by the name the report will use
 */
function build_recipient_probes(array $recipients, array $senders): array {
    $probes = [];
    $sender = $senders ? reset($senders) : 0;

    foreach ($recipients as $userid) {
        $probes["count unread for $userid"] =
            fn(provider $p) => ['count' => $p->count_unread_notifications($userid)];

        $probes["count unread in popup for $userid"] =
            fn(provider $p) => ['count' => $p->count_unread_popup_notifications($userid)];

        $probes["has personal data $userid"] =
            fn(provider $p) => ['has' => $p->user_has_notifications($userid)];

        // The three windows of the same list, both directions, and an offset without a limit.
        $probes["notifications of $userid, newest first"] =
            fn(provider $p) => $p->get_notifications($userid, null, null, 'DESC', 0, 5);

        $probes["notifications of $userid, oldest first"] =
            fn(provider $p) => $p->get_notifications($userid, null, null, 'ASC', 0, 5);

        $probes["notifications of $userid, offset 2"] =
            fn(provider $p) => $p->get_notifications($userid, null, null, 'DESC', 2, 3);

        $probes["read notifications of $userid"] =
            fn(provider $p) => $p->get_notifications($userid, null, true, 'DESC', 0, 5);

        $probes["unread notifications of $userid"] =
            fn(provider $p) => $p->get_notifications($userid, null, false, 'DESC', 0, 5);

        $probes["popup of $userid, newest first"] =
            fn(provider $p) => $p->get_popup_notifications($userid, 'DESC', 0, 5);

        $probes["popup of $userid, oldest first"] =
            fn(provider $p) => $p->get_popup_notifications($userid, 'ASC', 0, 5);

        // Both directions together, as the privacy provider asks.
        $probes["personal data of $userid"] =
            fn(provider $p) => $p->get_user_notifications($userid);

        if ($sender) {
            $probes["count unread for $userid from $sender"] =
                fn(provider $p) => ['count' => $p->count_unread_notifications($userid, $sender)];

            $probes["notifications of $userid from $sender"] =
                fn(provider $p) => $p->get_notifications($userid, $sender, null, 'DESC', 0, 5);
        }
    }

    return $probes;
}

/**
 * Builds the questions anchored on a sender, and those anchored on a notification.
 *
 * @param int[] $senders
 * @param int[] $notifications
 * @return callable[]
 */
function build_row_probes(array $senders, array $notifications): array {
    $probes = [];

    foreach ($senders as $sender) {
        // Zero as the recipient means "any".
        $probes["notifications sent by $sender to anybody"] =
            fn(provider $p) => $p->get_notifications(0, $sender, null, 'DESC', 0, 5);
    }

    foreach ($notifications as $id) {
        $probes["notification $id"] =
            fn(provider $p) => ['row' => $p->get_notification($id)];
    }

    // A notification that cannot exist: both must answer null.
    $probes['notification that does not exist'] =
        fn(provider $p) => ['row' => $p->get_notification(PHP_INT_MAX)];

    // The two cleanup calls, asked where they can do nothing.
    $probes['cleanup by age, nothing old enough'] =
        fn(provider $p) => ['deleted' => $p->delete_notifications_created_before(1)];

    $probes['cleanup by read time, nothing old enough'] =
        fn(provider $p) => ['deleted' => $p->delete_notifications_read_before(1)];

    return $probes;
}

/**
 * Builds the question the two activity modules ask, on a value that really exists.
 *
 * @param array|null $pair What pick_custom_data() found
 * @return callable[]
 */
function build_custom_data_probes(?array $pair): array {
    if (is_null($pair)) {
        return [];
    }

    [$userid, $customdata, $component, $eventtype] = $pair;

    return [
        'custom data, exact match' =>
            fn(provider $p) => ['exists' => $p->notification_exists_with_custom_data(
                $userid, $customdata, $component, $eventtype)],

        'custom data, without the component' =>
            fn(provider $p) => ['exists' => $p->notification_exists_with_custom_data(
                $userid, $customdata)],

        'custom data that nobody wrote' =>
            fn(provider $p) => ['exists' => $p->notification_exists_with_custom_data(
                $userid, '{"nothing":"here"}')],
    ];
}

// ---------------------------------------------------------------------------------------------
// The scenarios of the write phase.
// ---------------------------------------------------------------------------------------------

/**
 * Builds a notification in the shape message_send() hands to the context.
 *
 * @param int $to
 * @param int $from
 * @param string $marker Distinguishes what this run wrote from anything else
 * @param string $eventtype
 * @return \stdClass
 */
function notification_for(int $to, int $from, string $marker, string $eventtype = 'instantmessage'): \stdClass {
    return (object) [
        'useridfrom' => $from,
        'useridto' => $to,
        'subject' => "Confronto $marker",
        'fullmessage' => "Corpo del confronto $marker",
        'fullmessageformat' => FORMAT_PLAIN,
        'fullmessagehtml' => "<p>Corpo del confronto $marker</p>",
        'smallmessage' => "Confronto $marker",
        'component' => 'moodle',
        'eventtype' => $eventtype,
        'contexturl' => null,
        'contexturlname' => null,
        'customdata' => json_encode(['marker' => $marker]),
    ];
}

/**
 * Builds the write scenarios, each returning the effect it produced.
 *
 * @param \stdClass[] $users The two throwaway users
 * Every notification is preceded by a pause of CLOCK_SKEW_GUARD seconds, for the reason given in
 * compare_providers.php: the service stamps a notification with the clock of the replica that serves
 * it, the replicas sit on nodes whose clocks are not synchronised in this laboratory, and the probe
 * that lists the notifications oldest-first would otherwise depend on which replica served which
 * write. With synchronised clocks the pause is superfluous.
 *
 * @param string $marker
 * @return callable[]
 */
function build_write_scenarios(array $users, string $marker): array {
    [$mittente, $destinatario] = $users;
    $a = (int) $mittente->id;
    $b = (int) $destinatario->id;

    return [
        'write: one notification, in the popup' => function (provider $p) use ($a, $b, $marker) {
            sleep(CLOCK_SKEW_GUARD);
            $id = $p->send_notification(notification_for($b, $a, "$marker-1"), true);

            return [
                'id' => $id,
                'row' => $p->get_notification($id),
                'unread' => $p->count_unread_notifications($b),
                'popup' => $p->count_unread_popup_notifications($b),
            ];
        },

        'write: one notification, not in the popup' => function (provider $p) use ($a, $b, $marker) {
            sleep(CLOCK_SKEW_GUARD);
            $id = $p->send_notification(notification_for($b, $a, "$marker-2"), false);

            return [
                'id' => $id,
                'unread' => $p->count_unread_notifications($b),
                // The second notification must raise the first count and not the second.
                'popup' => $p->count_unread_popup_notifications($b),
                'in popup' => $p->get_popup_notifications($b, 'ASC', 0, 10),
            ];
        },

        'write: the two are listed in order' => fn(provider $p) => [
            'oldest first' => $p->get_notifications($b, null, null, 'ASC', 0, 10),
            'newest first' => $p->get_notifications($b, null, null, 'DESC', 0, 10),
        ],

        'write: mark the oldest as read' => function (provider $p) use ($b) {
            $tutte = $p->get_notifications($b, null, null, 'ASC', 0, 10);
            $prima = reset($tutte);

            return [
                'changed' => $p->mark_notification_as_read((int) $prima->id, 1760000000),
                // Marking it twice must not move the instant.
                'changed again' => $p->mark_notification_as_read((int) $prima->id, 1760009999),
                'row' => $p->get_notification((int) $prima->id),
                'unread' => $p->count_unread_notifications($b),
                'read ones' => $p->get_notifications($b, null, true, 'ASC', 0, 10),
            ];
        },

        'write: a third notification, from the other direction' => function (provider $p) use ($a, $b, $marker) {
            sleep(CLOCK_SKEW_GUARD);
            $id = $p->send_notification(notification_for($a, $b, "$marker-3"), true);

            return [
                'id' => $id,
                'unread of sender' => $p->count_unread_notifications($a),
                'unread of recipient' => $p->count_unread_notifications($b),
            ];
        },

        'write: custom data is findable' => fn(provider $p) => [
            'first' => $p->notification_exists_with_custom_data(
                $b, json_encode(['marker' => "$marker-1"]), 'moodle', 'instantmessage'),
            'third, wrong recipient' => $p->notification_exists_with_custom_data(
                $b, json_encode(['marker' => "$marker-3"])),
            'third, right recipient' => $p->notification_exists_with_custom_data(
                $a, json_encode(['marker' => "$marker-3"])),
        ],

        'write: mark all as read' => function (provider $p) use ($b) {
            return [
                'marked' => $p->mark_all_notifications_as_read($b),
                'unread' => $p->count_unread_notifications($b),
                'marked again' => $p->mark_all_notifications_as_read($b),
            ];
        },

        'write: personal data of both users' => fn(provider $p) => [
            'sender has' => $p->user_has_notifications($a),
            'recipient has' => $p->user_has_notifications($b),
            'sender export' => $p->get_user_notifications($a),
            'recipient export' => $p->get_user_notifications($b),
        ],

        'write: delete one notification' => function (provider $p) use ($b) {
            $tutte = $p->get_notifications($b, null, null, 'ASC', 0, 10);
            $prima = reset($tutte);

            return [
                'existed' => $p->delete_notification((int) $prima->id),
                'exists now' => $p->get_notification((int) $prima->id),
                'again' => $p->delete_notification((int) $prima->id),
                'left' => $p->get_notifications($b, null, null, 'ASC', 0, 10),
            ];
        },

        'write: erase the personal data of the recipient' => fn(provider $p) => [
            'deleted' => $p->delete_user_notifications($b),
            'has now' => $p->user_has_notifications($b),
            // The other user is left with nothing too: the erasure covers both directions.
            'sender has after' => $p->user_has_notifications($a),
        ],
    ];
}

// ---------------------------------------------------------------------------------------------
// Throwaway users.
// ---------------------------------------------------------------------------------------------

/**
 * Creates the users the write phase writes to.
 *
 * @param int $count
 * @return \stdClass[]
 */
function create_scratch_users(int $count): array {
    global $CFG;

    $stamp = time();
    $users = [];

    for ($i = 0; $i < $count; $i++) {
        $user = new \stdClass();
        $user->auth = 'manual';
        $user->confirmed = 1;
        $user->mnethostid = $CFG->mnet_localhost_id;
        $user->username = "cmpn_{$stamp}_{$i}";
        $user->firstname = 'Confronto';
        $user->lastname = "Notifiche $i";
        $user->email = "cmpn_{$stamp}_{$i}@invalid.example";

        // No password and no event for the temporary user.
        $user->id = user_create_user($user, false, false);
        $users[] = $user;
    }

    return $users;
}

/**
 * Removes from both stores everything the write phase produced.
 *
 * @param \stdClass[] $users
 * @param provider[] $implementations
 * @return int How many rows were removed, across both stores
 */
function purge_scratch_notifications(array $users, array $implementations): int {
    $removed = 0;

    foreach ($users as $user) {
        foreach ($implementations as $implementation) {
            try {
                $removed += $implementation->delete_user_notifications((int) $user->id);
            } catch (\Throwable $e) {
                cli_problem('Cleanup: ' . get_class($e) . ': ' . $e->getMessage());
            }
        }
    }

    return $removed;
}

// ---------------------------------------------------------------------------------------------
// The run.
// ---------------------------------------------------------------------------------------------

$diverged = 0;
$total = 0;

if (!$options['skip-reads']) {
    $recipients = pick_recipients((int) $options['users']);
    $senders = pick_senders(3);
    $notifications = pick_notifications((int) $options['notifications']);

    if (empty($recipients)) {
        cli_error('No notifications found: there is nothing to compare. Populate the site first.');
    }

    cli_writeln(sprintf('READS. Sample of %d recipients, %d senders, %d notifications.',
        count($recipients), count($senders), count($notifications)));
    cli_writeln('Identifiers must match exactly: the copy preserved them.');
    cli_writeln('');

    $probes = build_recipient_probes($recipients, $senders)
        + build_row_probes($senders, $notifications)
        + build_custom_data_probes(pick_custom_data());

    $total += count($probes);
    $diverged += $confronto->run_probes($probes, $legacy, $remote, false, (bool) $options['verbose']);

    cli_writeln('');
}

if (!$options['skip-writes']) {
    $marker = uniqid('c', false);
    $scratch = create_scratch_users(2);

    // Registered before anything is written: the handler also runs on an exit from inside a probe.
    core_shutdown_manager::register_function(function () use ($scratch, $legacy, $remote) {
        purge_scratch_notifications($scratch, [$legacy, $remote]);
    });

    cli_writeln(sprintf('WRITES. Two throwaway users, %d and %d, marker %s.',
        $scratch[0]->id, $scratch[1]->id, $marker));
    cli_writeln('Store-assigned identifiers are replaced by the order in which they appear:');
    cli_writeln('the copy aligned them once and nothing keeps them aligned.');
    cli_writeln('');

    $scenarios = build_write_scenarios($scratch, $marker);

    $total += count($scenarios);
    $diverged += $confronto->run_probes($scenarios, $legacy, $remote, true, (bool) $options['verbose']);

    cli_writeln('');

    $removed = purge_scratch_notifications($scratch, [$legacy, $remote]);
    cli_writeln("Cleanup: $removed rows removed across both stores.");

    foreach ($scratch as $user) {
        // The record is read back: delete_user() expects the stored user.
        if ($stored = $DB->get_record('user', ['id' => $user->id])) {
            delete_user($stored);
        }
    }
    cli_writeln('Cleanup: the two throwaway users were deleted.');
    cli_writeln('');
}

// ---------------------------------------------------------------------------------------------
// The report.
// ---------------------------------------------------------------------------------------------

cli_writeln(sprintf('RESULT %d checks, %d passed, %d failed',
    $total, $total - $diverged, $diverged));

exit($diverged === 0 ? 0 : 1);
