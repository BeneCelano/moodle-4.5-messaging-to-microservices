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

use core_message\api;

/**
 * Implementation of the conversation context backed by the extracted ConversationService.
 *
 * @package    core_message
 * @copyright  2026 Benedetta Pia Celano
 * @license    http://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class remote_provider implements provider {

    /** @var string Root of the versioned service contract. */
    const API_ROOT = '/api/v1';

    /** @var transport The HTTP transport towards the service. */
    protected $transport;

    /**
     * Constructor.
     *
     * @param transport|null $transport The transport to use, built from the site configuration when omitted
     */
    public function __construct(?transport $transport = null) {
        $this->transport = $transport ?? transport::create_from_config();
    }

    // ---------------------------------------------------------------------------------------------
    // Conversation aggregate.
    // ---------------------------------------------------------------------------------------------

    #[\Override]
    public function create_conversation(int $type, array $userids, ?string $name = null, int $enabled = 1,
            ?string $component = null, ?string $itemtype = null, ?int $itemid = null, ?int $contextid = null) {

        $response = $this->transport->post(self::API_ROOT . '/conversations', [
            'type' => $type,
            'user_ids' => array_values(array_map('intval', $userids)),
            'name' => $name,
            'enabled' => $enabled,
            'component' => $component,
            'item_type' => $itemtype,
            'item_id' => $itemid,
            'context_id' => $contextid,
        ], 'create-conversation-' . sha1(implode('-', $userids) . "|$type|$component|$itemtype|$itemid|$contextid"));

        $conversation = $this->to_conversation_record($response);

        // The members just created are returned, as the legacy implementation does.
        $conversation->members = [];
        foreach ($response['member_ids'] ?? [] as $memberid) {
            $conversation->members[] = (object) [
                'conversationid' => $conversation->id,
                'userid' => (int) $memberid,
                'timecreated' => $conversation->timecreated,
            ];
        }

        return $conversation;
    }

    #[\Override]
    public function get_conversation_between_users(array $userids) {
        if (empty($userids)) {
            return false;
        }

        try {
            $response = $this->transport->get(self::API_ROOT . '/conversations/individual', [
                'user_ids' => implode(',', array_map('intval', $userids)),
            ]);
        } catch (service_exception $e) {
            if ($e->get_status() === 404) {
                return false;
            }
            throw $e;
        }

        return isset($response['id']) ? (int) $response['id'] : false;
    }

    #[\Override]
    public function get_self_conversation(int $userid) {
        try {
            $response = $this->transport->get(self::API_ROOT . '/conversations/self', ['user_id' => $userid]);
        } catch (service_exception $e) {
            if ($e->get_status() === 404) {
                return false;
            }
            throw $e;
        }

        return $this->to_conversation_record($response);
    }

    #[\Override]
    public function get_conversations_between_users(int $userid1, int $userid2, int $limitfrom = 0,
            int $limitnum = 20): array {

        if ($userid1 == $userid2) {
            return [];
        }

        $response = $this->transport->get(self::API_ROOT . '/conversations/shared', [
            'user_id' => $userid1,
            'other_user_id' => $userid2,
            'offset' => $limitfrom,
            'limit' => $limitnum,
        ]);

        $conversations = [];
        foreach ($response['conversations'] ?? [] as $data) {
            $record = $this->to_conversation_record($data);
            // Same reduced set of columns as the legacy query.
            $conversations[$record->id] = (object) [
                'id' => $record->id,
                'type' => $record->type,
                'name' => $record->name,
                'timecreated' => $record->timecreated,
            ];
        }

        return $conversations;
    }

    #[\Override]
    public function get_conversation_by_area(string $component, string $itemtype, int $itemid, int $contextid) {
        try {
            $response = $this->transport->get(self::API_ROOT . '/conversations/by-area', [
                'component' => $component,
                'item_type' => $itemtype,
                'item_id' => $itemid,
                'context_id' => $contextid,
            ]);
        } catch (service_exception $e) {
            if ($e->get_status() === 404) {
                return false;
            }
            throw $e;
        }

        return $this->to_conversation_record($response);
    }

    #[\Override]
    public function is_conversation_area_enabled(string $component, string $itemtype, int $itemid,
            int $contextid): bool {

        $conversation = $this->get_conversation_by_area($component, $itemtype, $itemid, $contextid);

        return $conversation && (int) $conversation->enabled === api::MESSAGE_CONVERSATION_ENABLED;
    }

    #[\Override]
    public function enable_conversation(int $conversationid) {
        $this->transport->patch(self::API_ROOT . "/conversations/$conversationid", [
            'enabled' => api::MESSAGE_CONVERSATION_ENABLED,
        ]);
    }

    #[\Override]
    public function disable_conversation(int $conversationid) {
        $this->transport->patch(self::API_ROOT . "/conversations/$conversationid", [
            'enabled' => api::MESSAGE_CONVERSATION_DISABLED,
        ]);
    }

    #[\Override]
    public function update_conversation_name(int $conversationid, string $name) {
        $this->transport->patch(self::API_ROOT . "/conversations/$conversationid", ['name' => $name]);
    }

    #[\Override]
    public function delete_all_conversation_data(int $conversationid) {
        $this->transport->delete(self::API_ROOT . "/conversations/$conversationid");
    }

    // ---------------------------------------------------------------------------------------------
    // Membership.
    // ---------------------------------------------------------------------------------------------

    #[\Override]
    public function is_user_in_conversation(int $userid, int $conversationid): bool {
        $response = $this->transport->get(self::API_ROOT . "/conversations/$conversationid/membership", [
            'user_id' => $userid,
        ]);

        return !empty($response['is_member']);
    }

    #[\Override]
    public function get_conversation_member_ids(int $conversationid, int $limitfrom = 0, int $limitnum = 0): array {
        $response = $this->transport->get(self::API_ROOT . "/conversations/$conversationid/members", [
            'offset' => $limitfrom,
            'limit' => $limitnum,
        ]);

        return array_map('intval', $response['member_ids'] ?? []);
    }

    #[\Override]
    public function add_members_to_conversation(array $userids, int $convid) {
        $this->transport->post(self::API_ROOT . "/conversations/$convid/members", [
            'user_ids' => array_values(array_map('intval', $userids)),
        ]);
    }

    #[\Override]
    public function remove_members_from_conversation(array $userids, int $convid) {
        $this->transport->delete(self::API_ROOT . "/conversations/$convid/members", [
            'user_ids' => implode(',', array_map('intval', $userids)),
        ]);
    }

    #[\Override]
    public function count_conversation_members(int $convid): int {
        $response = $this->transport->get(self::API_ROOT . "/conversations/$convid/members/count");

        return (int) ($response['count'] ?? 0);
    }

    // ---------------------------------------------------------------------------------------------
    // Messages.
    // ---------------------------------------------------------------------------------------------

    #[\Override]
    public function can_send_message(int $recipientid, int $senderid, bool $evenifblocked = false): bool {
        $systemcontext = \context_system::instance();

        if (!has_capability('moodle/site:sendmessage', $systemcontext, $senderid)) {
            return false;
        }

        if (has_capability('moodle/site:readallmessages', $systemcontext, $senderid)) {
            return true;
        }

        return $this->can_contact_user($recipientid, $senderid, $evenifblocked);
    }

    #[\Override]
    public function can_send_message_to_conversation(int $userid, int $conversationid): bool {
        $systemcontext = \context_system::instance();
        if (!has_capability('moodle/site:sendmessage', $systemcontext, $userid)) {
            return false;
        }

        $conversation = $this->get_conversation_by_id($conversationid);
        if (!$conversation) {
            return false;
        }

        // Members fetched once per call.
        $memberids = $this->get_conversation_member_ids($conversationid);

        if (!in_array($userid, $memberids)) {
            return false;
        }

        if ($conversation->type == api::MESSAGE_CONVERSATION_TYPE_GROUP ||
                $conversation->type == api::MESSAGE_CONVERSATION_TYPE_SELF) {
            return true;
        }

        if ($conversation->type == api::MESSAGE_CONVERSATION_TYPE_INDIVIDUAL) {
            $otheruserids = array_filter($memberids,
                function($memberid) use ($userid) {
                    return $memberid != $userid;
                });
            $otheruserid = reset($otheruserids);
            if (!$otheruserid || !\core\user::is_real_user($otheruserid, true)) {
                return false;
            }

            return $this->can_contact_user($otheruserid, $userid);
        }

        throw new \moodle_exception("Invalid conversation type '$conversation->type'.");
    }

    /**
     * Records of messages written through this instance, kept for the lifetime of the request.
     *
     * @var \stdClass[] Indexed by message id.
     */
    private array $writtenrecords = [];

    #[\Override]
    public function send_message_to_conversation(int $userid, int $conversationid, string $message,
            int $format): \stdClass {

        $payload = [
            'user_id_from' => $userid,
            'text' => $message,
            'format' => $format,
        ];

        // The idempotency key identifies the command, not the attempt: a retry gets the message
        // already persisted.
        $idempotencykey = sha1("$userid|$conversationid|$format|$message|" . floor(microtime(true) * 1000));

        $response = $this->transport->post(self::API_ROOT . "/conversations/$conversationid/messages",
            $payload, $idempotencykey);

        // Cached only if the answer is complete (an older service does not return small_message).
        if (array_key_exists('small_message', $response)) {
            $this->writtenrecords[(int) $response['id']] = (object) [
                'id' => (int) $response['id'],
                'conversationid' => $conversationid,
                'useridfrom' => (int) $response['user_id_from'],
                'smallmessage' => $response['small_message'],
                'fullmessageformat' => (int) ($response['full_message_format'] ?? 0),
                'fullmessagetrust' => (int) ($response['full_message_trust'] ?? 0),
                'timecreated' => (int) $response['time_created'],
            ];
        }

        return (object) [
            'id' => (int) $response['id'],
            'useridfrom' => (int) $response['user_id_from'],
            'text' => $response['text'],
            'timecreated' => (int) $response['time_created'],
            'fullmessagetrust' => (int) ($response['full_message_trust'] ?? 0),
        ];
    }

    #[\Override]
    public function get_conversation_message_records(int $userid, int $convid, int $timedeleted = 0,
            int $limitfrom = 0, int $limitnum = 0, string $sort = 'timecreated ASC', int $timefrom = 0,
            int $timeto = 0): array {

        $response = $this->transport->get(self::API_ROOT . "/conversations/$convid/messages", [
            'user_id' => $userid,
            'time_deleted' => $timedeleted,
            'offset' => $limitfrom,
            'limit' => $limitnum,
            'sort' => $sort,
            'time_from' => $timefrom,
            'time_to' => $timeto,
        ]);

        $messages = [];
        foreach ($response['messages'] ?? [] as $data) {
            $id = (int) $data['id'];
            $messages[$id] = (object) [
                'id' => $id,
                'useridfrom' => (int) $data['user_id_from'],
                'subject' => $data['subject'] ?? null,
                'fullmessage' => $data['full_message'] ?? null,
                'fullmessagehtml' => $data['full_message_html'] ?? null,
                'fullmessageformat' => (int) ($data['full_message_format'] ?? 0),
                'fullmessagetrust' => (int) ($data['full_message_trust'] ?? 0),
                'smallmessage' => $data['small_message'] ?? null,
                'timecreated' => (int) $data['time_created'],
                'contextid' => isset($data['context_id']) ? (int) $data['context_id'] : null,
                'timeread' => isset($data['time_read']) ? (int) $data['time_read'] : null,
            ];
        }

        return $messages;
    }

    #[\Override]
    public function mark_message_as_read(int $userid, \stdClass $message, ?int $timeread = null): int {
        $response = $this->transport->post(self::API_ROOT . "/messages/{$message->id}/read", [
            'user_id' => $userid,
            'time_read' => $timeread,
        ], "read-{$message->id}-$userid");

        return (int) ($response['action_id'] ?? 0);
    }

    #[\Override]
    public function mark_all_messages_as_read(int $userid, ?int $conversationid = null) {
        $response = $this->transport->post(self::API_ROOT . "/users/$userid/messages/read", [
            'conversation_id' => $conversationid,
        ]);

        $marked = [];
        foreach ($response['marked'] ?? [] as $entry) {
            $marked[] = (object) [
                'messageid' => (int) $entry['message_id'],
                'useridfrom' => (int) $entry['user_id_from'],
                'actionid' => (int) $entry['action_id'],
            ];
        }

        return $marked;
    }

    #[\Override]
    public function delete_message(int $userid, int $messageid) {
        $response = $this->transport->delete(self::API_ROOT . "/messages/$messageid", ['user_id' => $userid]);

        return empty($response['action_id']) ? false : (int) $response['action_id'];
    }

    #[\Override]
    public function delete_message_for_all_users(int $messageid) {
        $response = $this->transport->delete(self::API_ROOT . "/messages/$messageid/all");

        return $this->to_deletion_records($response['deleted'] ?? []);
    }

    #[\Override]
    public function delete_conversation_by_id(int $userid, int $conversationid) {
        $response = $this->transport->delete(self::API_ROOT . "/conversations/$conversationid/messages",
            ['user_id' => $userid]);

        return $this->to_deletion_records($response['deleted'] ?? []);
    }

    #[\Override]
    public function get_message_conversation_id(int $messageid) {
        try {
            $response = $this->transport->get(self::API_ROOT . "/messages/$messageid");
        } catch (service_exception $e) {
            if ($e->get_status() === 404) {
                return false;
            }
            throw $e;
        }

        return (int) $response['conversation_id'];
    }

    #[\Override]
    public function get_message_records(array $messageids): array {
        // One request per message: the service has no bulk form of this read.
        $records = [];

        foreach ($messageids as $messageid) {
            $messageid = (int) $messageid;

            // A message written by this instance is served from the cache.
            if (isset($this->writtenrecords[$messageid])) {
                $records[$messageid] = $this->writtenrecords[$messageid];
                continue;
            }

            try {
                $response = $this->transport->get(self::API_ROOT . '/messages/' . $messageid);
            } catch (service_exception $e) {
                // A missing message is absent from the answer, as in the legacy implementation.
                if ($e->get_status() === 404) {
                    continue;
                }
                throw $e;
            }

            $record = new \stdClass();
            $record->id = (int) $response['id'];
            $record->conversationid = (int) $response['conversation_id'];
            $record->useridfrom = (int) $response['user_id_from'];
            $record->smallmessage = $response['small_message'] ?? null;
            $record->fullmessageformat = (int) ($response['full_message_format'] ?? 0);
            $record->fullmessagetrust = (int) ($response['full_message_trust'] ?? 0);
            $record->timecreated = (int) $response['time_created'];

            $records[$record->id] = $record;
        }

        return $records;
    }

    #[\Override]
    public function count_unread_conversations(int $userid): int {
        $response = $this->transport->get(self::API_ROOT . "/users/$userid/conversations/unread-count");

        return (int) ($response['count'] ?? 0);
    }

    // ---------------------------------------------------------------------------------------------
    // Per-user conversation actions.
    // ---------------------------------------------------------------------------------------------

    #[\Override]
    public function mute_conversation(int $userid, int $conversationid): void {
        $this->transport->post(self::API_ROOT . "/conversations/$conversationid/mute", ['user_id' => $userid],
            "mute-$conversationid-$userid");
    }

    #[\Override]
    public function unmute_conversation(int $userid, int $conversationid): void {
        $this->transport->delete(self::API_ROOT . "/conversations/$conversationid/mute", ['user_id' => $userid]);
    }

    #[\Override]
    public function is_conversation_muted(int $userid, int $conversationid): bool {
        $response = $this->transport->get(self::API_ROOT . "/conversations/$conversationid/mute",
            ['user_id' => $userid]);

        return !empty($response['muted']);
    }

    // ---------------------------------------------------------------------------------------------
    // Contacts, contact requests and blocks.
    // ---------------------------------------------------------------------------------------------

    #[\Override]
    public function add_contact(int $userid, int $contactid): \stdClass {
        $response = $this->transport->post(self::API_ROOT . '/contacts', [
            'user_id' => $userid,
            'contact_id' => $contactid,
        ], "contact-$userid-$contactid");

        return $this->to_contact_record($response);
    }

    #[\Override]
    public function remove_contact(int $userid, int $contactid) {
        $response = $this->transport->delete(self::API_ROOT . '/contacts', [
            'user_id' => $userid,
            'contact_id' => $contactid,
        ]);

        return empty($response['id']) ? false : $this->to_contact_record($response);
    }

    #[\Override]
    public function is_contact(int $userid, int $contactid): bool {
        $response = $this->transport->get(self::API_ROOT . '/contacts/exists', [
            'user_id' => $userid,
            'contact_id' => $contactid,
        ]);

        return !empty($response['is_contact']);
    }

    #[\Override]
    public function get_contact(int $userid, int $contactid) {
        try {
            $response = $this->transport->get(self::API_ROOT . '/contacts', [
                'user_id' => $userid,
                'contact_id' => $contactid,
            ]);
        } catch (service_exception $e) {
            if ($e->get_status() === 404) {
                return false;
            }
            throw $e;
        }

        return $this->to_contact_record($response);
    }

    #[\Override]
    public function count_contacts(int $userid): int {
        $response = $this->transport->get(self::API_ROOT . "/users/$userid/contacts/count");

        return (int) ($response['count'] ?? 0);
    }

    #[\Override]
    public function get_contact_ids(int $userid, int $limitfrom = 0, int $limitnum = 0): array {
        $response = $this->transport->get(self::API_ROOT . "/users/$userid/contacts", [
            'offset' => $limitfrom,
            'limit' => $limitnum,
        ]);

        return array_map('intval', $response['user_ids'] ?? []);
    }

    #[\Override]
    public function get_conversation_summaries(int $userid, int $limitfrom = 0, int $limitnum = 20,
            ?int $type = null, bool $mergeself = false, array $favouriteids = [],
            ?bool $onlyfavourites = null): array {

        $query = [
            'offset' => $limitfrom,
            'limit' => $limitnum,
            'merge_self' => $mergeself ? 1 : 0,
        ];
        if (!is_null($type)) {
            $query['conversation_type'] = $type;
        }
        if (!is_null($onlyfavourites) && !empty($favouriteids)) {
            $query['only_favourites'] = $onlyfavourites ? 1 : 0;
            $query['favourite_ids'] = array_values($favouriteids);
        }

        $response = $this->transport->get(self::API_ROOT . "/users/$userid/conversations", $query);

        $summaries = [];
        foreach ($response['conversations'] ?? [] as $conversation) {
            $summary = new \stdClass();
            $summary->id = (int) $conversation['id'];
            $summary->name = $conversation['name'];
            $summary->type = (int) $conversation['type'];
            $summary->component = $conversation['component'];
            $summary->itemtype = $conversation['item_type'];
            $summary->itemid = $conversation['item_id'];
            $summary->contextid = $conversation['context_id'];
            $summary->ismuted = (bool) $conversation['is_muted'];
            $summary->membercount = (int) $conversation['member_count'];
            $summary->unreadcount = (int) $conversation['unread_count'];
            $summary->memberids = array_map('intval', $conversation['member_ids'] ?? []);
            $summary->lastmessage = null;

            if (!empty($conversation['last_message'])) {
                $message = $conversation['last_message'];
                $lastmessage = new \stdClass();
                $lastmessage->id = (int) $message['id'];
                $lastmessage->useridfrom = (int) $message['user_id_from'];
                $lastmessage->smallmessage = $message['small_message'];
                $lastmessage->fullmessage = $message['full_message'];
                $lastmessage->fullmessageformat = (int) $message['full_message_format'];
                $lastmessage->fullmessagehtml = $message['full_message_html'];
                $lastmessage->fullmessagetrust = (int) $message['full_message_trust'];
                $lastmessage->timecreated = (int) $message['time_created'];
                $summary->lastmessage = $lastmessage;
            }

            $summaries[] = $summary;
        }

        return $summaries;
    }

    #[\Override]
    public function search_message_rows(int $userid, string $search, int $limitfrom = 0,
            int $limitnum = 0): array {

        $response = $this->transport->get(self::API_ROOT . "/users/$userid/messages/search", [
            'text' => $search,
            'offset' => $limitfrom,
            'limit' => $limitnum,
        ]);

        $rows = [];
        foreach ($response['results'] ?? [] as $result) {
            $row = new \stdClass();
            $row->id = (int) $result['message_id'];
            $row->conversationid = (int) $result['conversation_id'];
            $row->useridfrom = (int) $result['user_id_from'];
            $row->useridto = (int) $result['user_id_to'];
            $row->subject = $result['subject'];
            $row->fullmessage = $result['full_message'];
            $row->fullmessagehtml = $result['full_message_html'];
            $row->fullmessageformat = (int) $result['full_message_format'];
            $row->smallmessage = $result['small_message'];
            $row->timecreated = (int) $result['time_created'];
            $row->userfromblocked = !empty($result['user_from_blocked']) ? 1 : null;
            $row->usertoblocked = !empty($result['user_to_blocked']) ? 1 : null;

            $rows[] = $row;
        }

        return $rows;
    }

    #[\Override]
    public function count_unread_in_conversation(int $userid, int $conversationid): int {
        $response = $this->transport->get(
            self::API_ROOT . "/users/$userid/conversations/$conversationid/unread-count");

        return (int) ($response['count'] ?? 0);
    }

    #[\Override]
    public function get_countable_conversation_types(int $userid): array {
        $response = $this->transport->get(self::API_ROOT . "/users/$userid/conversations/countable");

        $types = [];
        foreach ($response['conversations'] ?? [] as $conversation) {
            $types[(int) $conversation['conversation_id']] = (int) $conversation['type'];
        }

        return $types;
    }

    #[\Override]
    public function get_unread_conversation_types(int $userid): array {
        $response = $this->transport->get(self::API_ROOT . "/users/$userid/conversations/unread");

        $types = [];
        foreach ($response['conversations'] ?? [] as $conversation) {
            $types[(int) $conversation['conversation_id']] = (int) $conversation['type'];
        }

        return $types;
    }

    #[\Override]
    public function get_blocked_user_ids(int $userid): array {
        $response = $this->transport->get(self::API_ROOT . "/users/$userid/blocks");

        return array_map('intval', $response['user_ids'] ?? []);
    }

    #[\Override]
    public function get_received_contact_request_user_ids(int $userid, int $limitfrom = 0,
            int $limitnum = 0): array {
        $response = $this->transport->get(self::API_ROOT . "/users/$userid/contact-requests", [
            'offset' => $limitfrom,
            'limit' => $limitnum,
        ]);

        return array_map('intval', $response['user_ids'] ?? []);
    }

    #[\Override]
    public function block_user(int $userid, int $usertoblockid): \stdClass {
        $response = $this->transport->post(self::API_ROOT . '/blocks', [
            'user_id' => $userid,
            'blocked_user_id' => $usertoblockid,
        ], "block-$userid-$usertoblockid");

        return (object) [
            'id' => (int) $response['id'],
            'userid' => (int) $response['user_id'],
            'blockeduserid' => (int) $response['blocked_user_id'],
            'timecreated' => (int) $response['time_created'],
        ];
    }

    #[\Override]
    public function unblock_user(int $userid, int $usertounblockid) {
        $response = $this->transport->delete(self::API_ROOT . '/blocks', [
            'user_id' => $userid,
            'blocked_user_id' => $usertounblockid,
        ]);

        if (empty($response['id'])) {
            return false;
        }

        return (object) [
            'id' => (int) $response['id'],
            'userid' => (int) $response['user_id'],
            'blockeduserid' => (int) $response['blocked_user_id'],
            'timecreated' => (int) $response['time_created'],
        ];
    }

    #[\Override]
    public function is_blocked(int $userid, int $blockeduserid): bool {
        $response = $this->transport->get(self::API_ROOT . '/blocks/exists', [
            'user_id' => $userid,
            'blocked_user_id' => $blockeduserid,
        ]);

        return !empty($response['is_blocked']);
    }

    #[\Override]
    public function create_contact_request(int $userid, int $requesteduserid): \stdClass {
        $response = $this->transport->post(self::API_ROOT . '/contact-requests', [
            'user_id' => $userid,
            'requested_user_id' => $requesteduserid,
        ], "contactrequest-$userid-$requesteduserid");

        return $this->to_contact_request_record($response);
    }

    #[\Override]
    public function confirm_contact_request(int $userid, int $requesteduserid) {
        $response = $this->transport->post(self::API_ROOT . '/contact-requests/confirm', [
            'user_id' => $userid,
            'requested_user_id' => $requesteduserid,
        ], "confirmrequest-$userid-$requesteduserid");

        return empty($response['id']) ? false : $this->to_contact_record($response);
    }

    #[\Override]
    public function decline_contact_request(int $userid, int $requesteduserid) {
        $this->transport->post(self::API_ROOT . '/contact-requests/decline', [
            'user_id' => $userid,
            'requested_user_id' => $requesteduserid,
        ], "declinerequest-$userid-$requesteduserid");
    }

    #[\Override]
    public function get_contact_requests_between_users(int $userid, int $requesteduserid): array {
        $response = $this->transport->get(self::API_ROOT . '/contact-requests', [
            'user_id' => $userid,
            'requested_user_id' => $requesteduserid,
        ]);

        $requests = [];
        foreach ($response['requests'] ?? [] as $data) {
            $record = $this->to_contact_request_record($data);
            $requests[$record->id] = $record;
        }

        return $requests;
    }

    #[\Override]
    public function does_contact_request_exist(int $userid, int $requesteduserid): bool {
        $response = $this->transport->get(self::API_ROOT . '/contact-requests/exists', [
            'user_id' => $userid,
            'requested_user_id' => $requesteduserid,
        ]);

        return !empty($response['exists']);
    }

    #[\Override]
    public function get_received_contact_requests_count(int $userid): int {
        $response = $this->transport->get(self::API_ROOT . "/users/$userid/contact-requests/received/count");

        return (int) ($response['count'] ?? 0);
    }

    // ---------------------------------------------------------------------------------------------
    // Internal helpers.
    // ---------------------------------------------------------------------------------------------

    /**
     * Returns a single conversation from the service.
     *
     * @param int $conversationid
     * @return \stdClass|false
     */
    #[\Override]
    public function get_conversation_by_id(int $conversationid) {
        try {
            $response = $this->transport->get(self::API_ROOT . "/conversations/$conversationid");
        } catch (service_exception $e) {
            if ($e->get_status() === 404) {
                return false;
            }
            throw $e;
        }

        return $this->to_conversation_record($response);
    }

    #[\Override]
    public function get_conversation_by_message(int $messageid) {
        try {
            $response = $this->transport->get(self::API_ROOT . "/messages/$messageid/conversation");
        } catch (service_exception $e) {
            if ($e->get_status() === 404) {
                return false;
            }
            throw $e;
        }

        return $this->to_conversation_record($response);
    }

    /**
     * Composes the rule deciding whether the sender may contact the recipient.
     *
     * @param int $recipientid
     * @param int $senderid
     * @param bool $evenifblocked
     * @return bool
     */
    protected function can_contact_user(int $recipientid, int $senderid, bool $evenifblocked = false): bool {
        if (has_capability('moodle/site:messageanyuser', \context_system::instance(), $senderid) ||
                $recipientid == $senderid) {
            return true;
        }

        $cancontact = null;

        if ($this->is_blocked($recipientid, $senderid) || $evenifblocked) {
            $cancontact = false;
        }

        $sharedcourses = null;
        if (null === $cancontact) {
            $privacypreference = api::get_user_privacy_messaging_preference($recipientid);
            if (api::MESSAGE_PRIVACY_SITE === $privacypreference) {
                $cancontact = true;
            } else {
                $cancontact = $this->is_contact($senderid, $recipientid);

                if (!$cancontact && api::MESSAGE_PRIVACY_COURSEMEMBER === $privacypreference) {
                    $sharedcourses = enrol_get_shared_courses($recipientid, $senderid, true);
                    $cancontact = (!empty($sharedcourses));
                }
            }
        }

        if (false === $cancontact) {
            if (null === $sharedcourses) {
                $sharedcourses = enrol_get_shared_courses($recipientid, $senderid, true);
            }

            foreach ($sharedcourses as $course) {
                if (has_capability('moodle/site:messageanyuser', \context_course::instance($course->id), $senderid)) {
                    $cancontact = true;
                    break;
                }
            }
        }

        return $cancontact;
    }

    /**
     * Rebuilds a conversation record in the shape the rest of Moodle expects.
     *
     * @param array $data The service representation
     * @return \stdClass
     */
    protected function to_conversation_record(array $data): \stdClass {
        return (object) [
            'id' => (int) $data['id'],
            'type' => (int) $data['type'],
            'name' => $data['name'],
            'convhash' => $data['conversation_hash'] ?? null,
            'component' => $data['component'],
            'itemtype' => $data['item_type'],
            'itemid' => isset($data['item_id']) ? (int) $data['item_id'] : null,
            'contextid' => isset($data['context_id']) ? (int) $data['context_id'] : null,
            'enabled' => (int) $data['enabled'],
            'timecreated' => (int) $data['time_created'],
            'timemodified' => isset($data['time_modified']) ? (int) $data['time_modified'] : null,
        ];
    }

    /**
     * Rebuilds a contact record in the shape the rest of Moodle expects.
     *
     * @param array $data The service representation
     * @return \stdClass
     */
    protected function to_contact_record(array $data): \stdClass {
        return (object) [
            'id' => (int) $data['id'],
            'userid' => (int) $data['user_id'],
            'contactid' => (int) $data['contact_id'],
            'timecreated' => (int) $data['time_created'],
        ];
    }

    /**
     * Rebuilds a contact request record in the shape the rest of Moodle expects.
     *
     * @param array $data The service representation
     * @return \stdClass
     */
    protected function to_contact_request_record(array $data): \stdClass {
        return (object) [
            'id' => (int) $data['id'],
            'userid' => (int) $data['user_id'],
            'requesteduserid' => (int) $data['requested_user_id'],
            'timecreated' => (int) $data['time_created'],
        ];
    }

    /**
     * Rebuilds the list of recorded deletions returned by the service.
     *
     * @param array $entries
     * @return \stdClass[]
     */
    protected function to_deletion_records(array $entries): array {
        $deleted = [];
        foreach ($entries as $entry) {
            $deleted[] = (object) [
                'userid' => (int) $entry['user_id'],
                'messageid' => (int) $entry['message_id'],
                'actionid' => (int) $entry['action_id'],
            ];
        }

        return $deleted;
    }
}
