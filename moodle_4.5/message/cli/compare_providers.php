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
 * Asks the same questions of both implementations of the conversation context and compares the answers.
 *
 *   sudo -u www-data php message/cli/compare_providers.php [--users=N] [--conversations=N] [--messages=N]
 *       [--skip-reads] [--skip-writes] [--keep]
 *
 * @package    core_message
 * @copyright  2026 Benedetta Pia Celano
 * @license    http://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

define('CLI_SCRIPT', true);

require(__DIR__ . '/../../config.php');
require_once($CFG->libdir . '/clilib.php');
require_once($CFG->dirroot . '/user/lib.php');

use core_message\api;
use core_message\conversation\provider;
use core_message\conversation\provider_factory;
use core_message\conversation\remote_provider;

/** Seconds between two writes whose relative order the reads will observe; see build_scenarios(). */
const CLOCK_SKEW_GUARD = 2;

list($options, $unrecognised) = cli_get_params([
    'help' => false,
    'users' => 10,
    'conversations' => 20,
    'messages' => 20,
    'search' => 'a',
    'skip-reads' => false,
    'skip-writes' => false,
    'keep' => false,
    'verbose' => false,
], ['h' => 'help', 'v' => 'verbose']);

if ($unrecognised) {
    cli_error(get_string('cliunknowoption', 'admin', implode(PHP_EOL . '  ', $unrecognised)));
}

if ($options['help']) {
    cli_writeln(<<<EOT
Compares the monolithic and the service implementations of the conversation context.

Options:
  -h, --help             Print this help.
      --users=N          How many users to draw the read probes from. Default 10.
      --conversations=N  How many conversations to draw the read probes from. Default 20.
      --messages=N       How many messages to draw the read probes from. Default 20.
      --search=TEXT      The text the message search probes look for. Default 'a'.
      --skip-reads       Do not run the read phase.
      --skip-writes      Do not run the write phase, which creates users and writes to both stores.
      --keep             Leave the users the write phase created instead of deleting them.
  -v, --verbose          Print every probe, not only the failing ones.

EOT
    );
    exit(0);
}

$legacy = provider_factory::get_legacy();
$remote = new remote_provider();

// One harmless question first: only a transport failure stops the run.
try {
    $remote->count_contacts(1);
} catch (\core_message\conversation\service_exception $e) {
    if ($e->is_transport_failure()) {
        cli_error("The service did not answer, so there is nothing to compare against.\n\n"
            . "  $e->debuginfo\n\n"
            . "Check that \$CFG->conversationservice_url and \$CFG->conversationservice_token are set,\n"
            . "and that the service answers on that address from this machine.");
    }
}

// ---------------------------------------------------------------------------------------------
// The sample the read phase draws on.
// ---------------------------------------------------------------------------------------------

/**
 * Draws the users the read probes will be asked about.
 *
 * @param int $limit How many users to return
 * @return int[]
 */
function pick_users(int $limit): array {
    global $DB;

    // Keyed on the grouping key, which is unique; get_fieldset_sql accepts no limits.
    $records = $DB->get_records_sql("SELECT userid, COUNT(*) AS conversations
                                       FROM {message_conversation_members}
                                   GROUP BY userid
                                   ORDER BY conversations DESC, userid ASC", null, 0, $limit);

    return array_map('intval', array_keys($records));
}

/**
 * Draws the conversations the read probes will be asked about.
 *
 * @param int $limit How many conversations to return
 * @return int[]
 */
function pick_conversations(int $limit): array {
    global $DB;

    $records = $DB->get_records_sql("SELECT mc.id, COUNT(mcm.id) AS members
                                       FROM {message_conversations} mc
                                  LEFT JOIN {message_conversation_members} mcm
                                         ON mcm.conversationid = mc.id
                                   GROUP BY mc.id
                                   ORDER BY members DESC, mc.id ASC", null, 0, $limit);

    return array_map('intval', array_keys($records));
}

/**
 * Draws the messages the read probes will be asked about, most recent first.
 *
 * @param int $limit How many messages to return
 * @return int[]
 */
function pick_messages(int $limit): array {
    global $DB;

    $records = $DB->get_records_sql("SELECT id FROM {messages} ORDER BY id DESC", null, 0, $limit);

    return array_map('intval', array_keys($records));
}

// ---------------------------------------------------------------------------------------------
// Comparison.
// ---------------------------------------------------------------------------------------------
//
// The apparatus lives in \core_message\cli\comparison, which knows how to ask both implementations
// the same question and read the difference, and nothing about conversations. The two lists below are
// the only thing it has to be told about this context: which fields hold an identifier each store
// assigns for itself, and which hold the moment a row was written.
//
// The identifiers are substituted only in the write phase, where the two stores number independently.
// The read phase compares them as they are, because the bulk copy preserved them and that is itself
// to check.

$confronto = new \core_message\cli\comparison(
    ['id', 'messageid', 'conversationid', 'actionid'],
    ['timecreated', 'timemodified', 'timeread', 'timeuserfromdeleted', 'timeusertodeleted']
);

// ---------------------------------------------------------------------------------------------
// The probes.
// ---------------------------------------------------------------------------------------------

/**
 * Builds the questions that are anchored on a user.
 *
 * @param int[] $users
 * @param string $search The text the search probes look for
 * @return callable[] Indexed by the name the report will use
 */
function build_user_probes(array $users, string $search): array {
    $probes = [];

    foreach ($users as $userid) {
        $probes["count_contacts($userid)"] =
            fn(provider $p) => $p->count_contacts($userid);
        $probes["get_contact_ids($userid)"] =
            fn(provider $p) => $p->get_contact_ids($userid, 0, 20);
        $probes["get_blocked_user_ids($userid)"] =
            fn(provider $p) => $p->get_blocked_user_ids($userid);
        $probes["get_received_contact_request_user_ids($userid)"] =
            fn(provider $p) => $p->get_received_contact_request_user_ids($userid, 0, 20);
        $probes["get_received_contact_requests_count($userid)"] =
            fn(provider $p) => $p->get_received_contact_requests_count($userid);
        $probes["count_unread_conversations($userid)"] =
            fn(provider $p) => $p->count_unread_conversations($userid);
        $probes["get_unread_conversation_types($userid)"] =
            fn(provider $p) => $p->get_unread_conversation_types($userid);
        $probes["get_countable_conversation_types($userid)"] =
            fn(provider $p) => $p->get_countable_conversation_types($userid);
        $probes["get_self_conversation($userid)"] =
            fn(provider $p) => $p->get_self_conversation($userid);
        $probes["search_message_rows($userid, '$search')"] =
            fn(provider $p) => $p->search_message_rows($userid, $search, 0, 20);

        // The summaries, with the filters supplied from outside the context: the favourite
        // partition and the merging of the self conversation.
        $probes["get_conversation_summaries($userid)"] =
            fn(provider $p) => $p->get_conversation_summaries($userid, 0, 20);
        $probes["get_conversation_summaries($userid, mergeself)"] =
            fn(provider $p) => $p->get_conversation_summaries($userid, 0, 20, null, true);
        $probes["get_conversation_summaries($userid, page 2)"] =
            fn(provider $p) => $p->get_conversation_summaries($userid, 20, 20);
    }

    // Consecutive users of the sample, not every combination.
    for ($i = 0; $i + 1 < count($users); $i++) {
        [$first, $second] = [$users[$i], $users[$i + 1]];

        $probes["is_contact($first, $second)"] =
            fn(provider $p) => $p->is_contact($first, $second);
        $probes["get_contact($first, $second)"] =
            fn(provider $p) => $p->get_contact($first, $second);
        $probes["is_blocked($first, $second)"] =
            fn(provider $p) => $p->is_blocked($first, $second);
        $probes["does_contact_request_exist($first, $second)"] =
            fn(provider $p) => $p->does_contact_request_exist($first, $second);
        $probes["get_contact_requests_between_users($first, $second)"] =
            fn(provider $p) => $p->get_contact_requests_between_users($first, $second);
        $probes["can_send_message($first, $second)"] =
            fn(provider $p) => $p->can_send_message($first, $second);
        // The only probe answering with a bare identifier: wrapped under a name for the
        // canonicalisation.
        $probes["get_conversation_between_users([$first, $second])"] =
            fn(provider $p) => ['id' => $p->get_conversation_between_users([$first, $second])];
        $probes["get_conversations_between_users($first, $second)"] =
            fn(provider $p) => $p->get_conversations_between_users($first, $second, 0, 20);

        // The same call with the limit api.php uses.
        $probes["get_conversations_between_users($first, $second, limit 1000)"] =
            fn(provider $p) => $p->get_conversations_between_users($first, $second, 0, 1000);
    }

    return $probes;
}

/**
 * Builds the questions that are anchored on a conversation or a message.
 *
 * @param int[] $users
 * @param int[] $conversations
 * @param int[] $messages
 * @return callable[] Indexed by the name the report will use
 */
function build_row_probes(array $users, array $conversations, array $messages): array {
    $probes = [];

    foreach ($conversations as $convid) {
        $probes["get_conversation_by_id($convid)"] =
            fn(provider $p) => $p->get_conversation_by_id($convid);
        $probes["count_conversation_members($convid)"] =
            fn(provider $p) => $p->count_conversation_members($convid);
        $probes["get_conversation_member_ids($convid)"] =
            fn(provider $p) => $p->get_conversation_member_ids($convid, 0, 50);
    }

    // Paired off rather than crossed, like the users.
    foreach ($users as $index => $userid) {
        if (!isset($conversations[$index])) {
            break;
        }
        $convid = $conversations[$index];

        $probes["is_user_in_conversation($userid, $convid)"] =
            fn(provider $p) => $p->is_user_in_conversation($userid, $convid);
        $probes["count_unread_in_conversation($userid, $convid)"] =
            fn(provider $p) => $p->count_unread_in_conversation($userid, $convid);
        $probes["is_conversation_muted($userid, $convid)"] =
            fn(provider $p) => $p->is_conversation_muted($userid, $convid);
        $probes["can_send_message_to_conversation($userid, $convid)"] =
            fn(provider $p) => $p->can_send_message_to_conversation($userid, $convid);
        $probes["get_conversation_message_records($userid, $convid)"] =
            fn(provider $p) => $p->get_conversation_message_records($userid, $convid, 0, 0, 20);
    }

    foreach ($messages as $messageid) {
        $probes["get_message_conversation_id($messageid)"] =
            fn(provider $p) => $p->get_message_conversation_id($messageid);
        $probes["get_conversation_by_message($messageid)"] =
            fn(provider $p) => $p->get_conversation_by_message($messageid);
    }

    return $probes;
}

// ---------------------------------------------------------------------------------------------
// The write scenarios.
// ---------------------------------------------------------------------------------------------

/**
 * Builds the sequences of writes that each implementation will be made to perform.
 *
 * @param int[] $users The scratch users, at least four
 * @return callable[] Indexed by the name the report will use
 */
function build_scenarios(array $users): array {
    [$first, $second, $third, $fourth] = $users;

    return [
        'individual conversation, two messages, one read' => function (provider $p) use ($first, $second) {
            $conversation = $p->create_conversation(api::MESSAGE_CONVERSATION_TYPE_INDIVIDUAL,
                [$first, $second]);
            sleep(CLOCK_SKEW_GUARD);
            $opening = $p->send_message_to_conversation($first, $conversation->id, 'primo', FORMAT_MOODLE);
            sleep(CLOCK_SKEW_GUARD);
            $p->send_message_to_conversation($second, $conversation->id, 'secondo', FORMAT_MOODLE);
            $p->mark_message_as_read($second, $opening);
        },

        'group conversation, membership and rename' => function (provider $p) use ($first, $second, $third) {
            $conversation = $p->create_conversation(api::MESSAGE_CONVERSATION_TYPE_GROUP,
                [$first, $second], 'gruppo di prova');
            $p->add_members_to_conversation([$third], $conversation->id);
            sleep(CLOCK_SKEW_GUARD);
            $p->send_message_to_conversation($first, $conversation->id, 'ciao a tutti', FORMAT_MOODLE);
            $p->update_conversation_name($conversation->id, 'gruppo rinominato');
            $p->remove_members_from_conversation([$second], $conversation->id);
        },

        'contact requests, confirmed and declined' => function (provider $p) use ($first, $second, $third) {
            $p->create_contact_request($first, $second);
            $p->confirm_contact_request($first, $second);
            $p->create_contact_request($first, $third);
            $p->decline_contact_request($first, $third);
        },

        'blocking and unblocking' => function (provider $p) use ($first, $second, $fourth) {
            $p->block_user($first, $fourth);
            $p->unblock_user($first, $fourth);
            $p->block_user($second, $fourth);
        },

        'muting and deleting a message' => function (provider $p) use ($first, $fourth) {
            $conversation = $p->create_conversation(api::MESSAGE_CONVERSATION_TYPE_INDIVIDUAL,
                [$first, $fourth]);
            $p->mute_conversation($first, $conversation->id);
            sleep(CLOCK_SKEW_GUARD);
            $doomed = $p->send_message_to_conversation($first, $conversation->id, 'da cancellare',
                FORMAT_MOODLE);
            sleep(CLOCK_SKEW_GUARD);
            $p->send_message_to_conversation($fourth, $conversation->id, 'che resta', FORMAT_MOODLE);
            $p->delete_message($first, $doomed->id);
        },
    ];
}

/**
 * Creates the users the write phase acts on.
 *
 * @param int $count How many to create
 * @return \stdClass[] The user records
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
        $user->username = "cmp_{$stamp}_{$i}";
        $user->firstname = 'Confronto';
        $user->lastname = "Utente $i";
        $user->email = "cmp_{$stamp}_{$i}@invalid.example";

        // No password and no event for the temporary user.
        $user->id = user_create_user($user, false, false);
        $users[] = $user;
    }

    return $users;
}

// ---------------------------------------------------------------------------------------------
// The run.
// ---------------------------------------------------------------------------------------------

$diverged = 0;
$total = 0;

if (!$options['skip-reads']) {
    $users = pick_users((int) $options['users']);
    $conversations = pick_conversations((int) $options['conversations']);
    $messages = pick_messages((int) $options['messages']);

    if (empty($users)) {
        cli_error('No conversation members found: there is nothing to compare. Populate the site first.');
    }

    cli_writeln(sprintf('READS. Sample of %d users, %d conversations, %d messages.',
        count($users), count($conversations), count($messages)));
    cli_writeln('Identifiers must match exactly: the copy preserved them.');
    cli_writeln('');

    $probes = build_user_probes($users, (string) $options['search'])
        + build_row_probes($users, $conversations, $messages);

    $total += count($probes);
    $diverged += $confronto->run_probes($probes, $legacy, $remote, false, (bool) $options['verbose']);

    cli_writeln('');
}

/**
 * Removes from both stores the conversational data the write phase produced.
 *
 * @param int[] $userids The scratch users
 * @param provider[] $implementations The stores to clear, keyed by the name shown to the reader
 * @return int How many conversations were removed, across both stores
 */
function purge_scratch_conversations(array $userids, array $implementations): int {
    $removed = 0;

    foreach ($implementations as $side => $implementation) {
        $conversationids = [];

        foreach ($userids as $first) {
            foreach ($userids as $second) {
                if ($first >= $second) {
                    continue;
                }
                try {
                    foreach ($implementation->get_conversations_between_users($first, $second) as $id => $unused) {
                        $conversationids[(int) $id] = true;
                    }
                } catch (\Throwable $e) {
                    // A store that cannot answer has nothing to remove; the other store is still
                    // cleared.
                    cli_writeln(sprintf('          %s could not be asked about %d and %d: %s',
                        $side, $first, $second, $e->getMessage()));
                }
            }
        }

        foreach (array_keys($conversationids) as $conversationid) {
            try {
                $implementation->delete_all_conversation_data($conversationid);
                $removed++;
            } catch (\Throwable $e) {
                cli_writeln(sprintf('          %s could not remove conversation %d: %s',
                    $side, $conversationid, $e->getMessage()));
            }
        }
    }

    return $removed;
}

/**
 * Removes from both stores the contacts, blocks and pending requests the write phase produced.
 *
 * @param int[] $userids The scratch users
 * @param provider[] $implementations The stores to clear, keyed by the name shown to the reader
 * @return int How many rows were removed, across both stores
 */
function purge_scratch_relations(array $userids, array $implementations): int {
    $removed = 0;

    foreach ($implementations as $side => $implementation) {
        foreach ($userids as $first) {
            foreach ($userids as $second) {
                if ($first === $second) {
                    continue;
                }
                try {
                    if ($implementation->get_contact($first, $second)) {
                        $implementation->remove_contact($first, $second);
                        $removed++;
                    }
                    if ($implementation->is_blocked($first, $second)) {
                        $implementation->unblock_user($first, $second);
                        $removed++;
                    }
                    if ($implementation->does_contact_request_exist($first, $second)) {
                        $implementation->decline_contact_request($first, $second);
                        $removed++;
                    }
                } catch (\Throwable $e) {
                    cli_writeln(sprintf('          %s could not clear %d -> %d: %s',
                        $side, $first, $second, $e->getMessage()));
                }
            }
        }
    }

    return $removed;
}

if (!$options['skip-writes']) {
    cli_writeln('WRITES. Each sequence is applied to both implementations, which are then asked the');
    cli_writeln('same read questions. Identifiers each store assigned are reduced to their position.');
    cli_writeln('');

    $scratch = create_scratch_users(4);
    $scratchids = array_map(fn($user) => (int) $user->id, $scratch);
    cli_writeln('Scratch users: ' . implode(', ', $scratchids));
    cli_writeln('');

    try {
        foreach (build_scenarios($scratchids) as $name => $scenario) {
            cli_writeln("  scenario: $name");

            // A scenario that fails on one side only is reported by the probes that follow.
            foreach (['monolith' => $legacy, 'service' => $remote] as $side => $implementation) {
                try {
                    $scenario($implementation);
                } catch (\Throwable $e) {
                    cli_writeln(sprintf('          %s THREW %s: %s', $side, get_class($e), $e->getMessage()));
                }
            }

            $probes = build_user_probes($scratchids, 'a');
            $total += count($probes);
            $diverged += $confronto->run_probes($probes, $legacy, $remote, true, (bool) $options['verbose']);
        }
    } finally {
        if ($options['keep']) {
            cli_writeln('');
            cli_writeln('Scratch users and their conversations kept, as asked. The rows each store wrote');
            cli_writeln('differ between the two, so the next integrity comparison will report them as a');
            cli_writeln('divergence until they are removed: reconcile.py --apply does it.');
        } else {
            // Conversations first, then users: the search is by user pair.
            $implementazioni = ['monolith' => $legacy, 'service' => $remote];
            $rimosse = purge_scratch_conversations($scratchids, $implementazioni);
            $relazioni = purge_scratch_relations($scratchids, $implementazioni);

            foreach ($scratch as $user) {
                // The record is read back: delete_user expects the stored user.
                if ($stored = $DB->get_record('user', ['id' => $user->id])) {
                    delete_user($stored);
                }
            }

            cli_writeln('');
            cli_writeln(sprintf('Cleaned up: %d conversations and %d relations removed across both stores,'
                . ' scratch users deleted.', $rimosse, $relazioni));
            cli_writeln('The write phase leaves no residue behind: conversations, contacts, blocks and');
            cli_writeln('pending requests are all carried back out, so the next integrity comparison does');
            cli_writeln('not read them as a divergence between the two stores.');
        }
    }

    cli_writeln('');
}

// ---------------------------------------------------------------------------------------------
// The verdict.
// ---------------------------------------------------------------------------------------------

cli_writeln(sprintf('%d probes, %d agreed, %d diverged.', $total, $total - $diverged, $diverged));

if ($diverged === 0) {
    cli_writeln('The two implementations answered identically. The cutover has nothing to object to.');
    exit(0);
}

cli_writeln('');
cli_writeln('The cutover must not proceed until every divergence above is either corrected or');
cli_writeln('explained. A divergence is not always a defect of the service: it can also be a');
cli_writeln('difference in shape that this comparison does not know how to reduce.');
exit(1);
