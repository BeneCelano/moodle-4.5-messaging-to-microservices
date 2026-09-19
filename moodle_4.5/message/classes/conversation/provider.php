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
 * Abstraction over the operations owned by the conversation bounded context.
 *
 * @package    core_message
 * @copyright  2026 Benedetta Pia Celano
 * @license    http://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
interface provider {

    // ---------------------------------------------------------------------------------------------
    // Conversation aggregate.
    // ---------------------------------------------------------------------------------------------

    /**
     * Creates a conversation with the given users.
     *
     * @param int $type One of the \core_message\api::MESSAGE_CONVERSATION_TYPE_* values
     * @param int[] $userids The users to add as members
     * @param string|null $name The name of the conversation
     * @param int $enabled Whether the conversation is created enabled or disabled
     * @param string|null $component The Moodle component the conversation belongs to, if any
     * @param string|null $itemtype The type of the component item
     * @param int|null $itemid The id of the component item
     * @param int|null $contextid The id of the context of the component item
     * @return \stdClass The conversation, with a 'members' property when newly created
     */
    public function create_conversation(int $type, array $userids, ?string $name = null, int $enabled = 1,
            ?string $component = null, ?string $itemtype = null, ?int $itemid = null, ?int $contextid = null);

    /**
     * Returns a conversation by its id.
     *
     * @param int $conversationid
     * @return \stdClass|false The conversation record, false when it does not exist
     */
    public function get_conversation_by_id(int $conversationid);

    /**
     * Returns the conversation a message belongs to.
     *
     * @param int $messageid
     * @return \stdClass|false The conversation record, or false when the message does not exist
     */
    public function get_conversation_by_message(int $messageid);

    /**
     * Returns the id of the individual conversation between the given users.
     *
     * @param int[] $userids
     * @return int|bool The conversation id, false when there is none
     */
    public function get_conversation_between_users(array $userids);

    /**
     * Returns the self conversation of a user, without creating it.
     *
     * @param int $userid
     * @return \stdClass|false The conversation record, false when the user has none yet
     */
    public function get_self_conversation(int $userid);

    /**
     * Returns every enabled conversation both users are members of.
     *
     * @param int $userid1 One of the users
     * @param int $userid2 The other user
     * @param int $limitfrom
     * @param int $limitnum
     * @return array Conversation records indexed by id
     */
    public function get_conversations_between_users(int $userid1, int $userid2, int $limitfrom = 0,
            int $limitnum = 20): array;

    /**
     * Returns the conversation attached to a component item, regardless of its enabled state.
     *
     * @param string $component
     * @param string $itemtype
     * @param int $itemid
     * @param int $contextid
     * @return \stdClass|false
     */
    public function get_conversation_by_area(string $component, string $itemtype, int $itemid, int $contextid);

    /**
     * Whether a conversation attached to a component item exists and is enabled.
     *
     * @param string $component
     * @param string $itemtype
     * @param int $itemid
     * @param int $contextid
     * @return bool
     */
    public function is_conversation_area_enabled(string $component, string $itemtype, int $itemid,
            int $contextid): bool;

    /**
     * Enables a conversation.
     *
     * @param int $conversationid
     * @return void
     */
    public function enable_conversation(int $conversationid);

    /**
     * Disables a conversation.
     *
     * @param int $conversationid
     * @return void
     */
    public function disable_conversation(int $conversationid);

    /**
     * Renames a conversation.
     *
     * @param int $conversationid
     * @param string $name
     * @return void
     */
    public function update_conversation_name(int $conversationid, string $name);

    /**
     * Removes every piece of data owned by this context for the given conversation.
     *
     * @param int $conversationid
     * @return void
     */
    public function delete_all_conversation_data(int $conversationid);

    // ---------------------------------------------------------------------------------------------
    // Membership.
    // ---------------------------------------------------------------------------------------------

    /**
     * Whether the user is a member of the conversation.
     *
     * @param int $userid
     * @param int $conversationid
     * @return bool
     */
    public function is_user_in_conversation(int $userid, int $conversationid): bool;

    /**
     * Returns the ids of the members of a conversation, oldest membership first.
     *
     * @param int $conversationid
     * @param int $limitfrom
     * @param int $limitnum
     * @return int[]
     */
    public function get_conversation_member_ids(int $conversationid, int $limitfrom = 0, int $limitnum = 0): array;

    /**
     * Adds members to an existing group conversation, ignoring users who are already members.
     *
     * @param int[] $userids
     * @param int $convid
     * @return void
     */
    public function add_members_to_conversation(array $userids, int $convid);

    /**
     * Removes members from an existing group conversation.
     *
     * @param int[] $userids
     * @param int $convid
     * @return void
     */
    public function remove_members_from_conversation(array $userids, int $convid);

    /**
     * Counts the members of a conversation.
     *
     * @param int $convid
     * @return int
     */
    public function count_conversation_members(int $convid): int;

    // ---------------------------------------------------------------------------------------------
    // Messages.
    // ---------------------------------------------------------------------------------------------

    /**
     * Decides whether the user may send a private message to the recipient.
     *
     * @param int $recipientid
     * @param int $senderid
     * @param bool $evenifblocked Report the block explicitly instead of silently allowing the send
     * @return bool
     */
    public function can_send_message(int $recipientid, int $senderid, bool $evenifblocked = false): bool;

    /**
     * Decides whether the user may send a message to the given conversation.
     *
     * @param int $userid
     * @param int $conversationid
     * @return bool
     */
    public function can_send_message_to_conversation(int $userid, int $conversationid): bool;

    /**
     * Persists a message sent by a user to a conversation.
     *
     * @param int $userid The sender
     * @param int $conversationid
     * @param string $message The message body
     * @param int $format One of the FORMAT_* constants
     * @return \stdClass The persisted message: id, useridfrom, text, timecreated, fullmessagetrust
     */
    public function send_message_to_conversation(int $userid, int $conversationid, string $message,
            int $format): \stdClass;

    /**
     * Returns the message records of a conversation as seen by a user.
     *
     * @param int $userid The user reading the conversation
     * @param int $convid
     * @param int $timedeleted When set, only messages deleted by the user at that time are returned
     * @param int $limitfrom
     * @param int $limitnum
     * @param string $sort Ordering clause, for example 'timecreated ASC'
     * @param int $timefrom Only messages created at or after this time
     * @param int $timeto Only messages created at or before this time
     * @return array Message records indexed by id
     */
    public function get_conversation_message_records(int $userid, int $convid, int $timedeleted = 0,
            int $limitfrom = 0, int $limitnum = 0, string $sort = 'timecreated ASC', int $timefrom = 0,
            int $timeto = 0): array;

    /**
     * Records that a user has read a message.
     *
     * @param int $userid The user who read the message
     * @param \stdClass $message The message record, needing at least an id and a useridfrom
     * @param int|null $timeread Defaults to now
     * @return int The id of the recorded action
     */
    public function mark_message_as_read(int $userid, \stdClass $message, ?int $timeread = null): int;

    /**
     * Records that a user has read every message they can see, optionally within one conversation.
     *
     * @param int $userid
     * @param int|null $conversationid
     * @return \stdClass[] One entry per message marked, with messageid, useridfrom and actionid
     */
    public function mark_all_messages_as_read(int $userid, ?int $conversationid = null);

    /**
     * Records that a user has deleted a message for themselves.
     *
     * @param int $userid
     * @param int $messageid
     * @return int|false The id of the recorded action, false when already deleted or unknown message
     */
    public function delete_message(int $userid, int $messageid);

    /**
     * Records the deletion of a message for every member of its conversation.
     *
     * @param int $messageid
     * @return \stdClass[] One entry per deletion recorded, with userid, messageid and actionid
     */
    public function delete_message_for_all_users(int $messageid);

    /**
     * Records the deletion of every message of a conversation for one user.
     *
     * @param int $userid
     * @param int $conversationid
     * @return \stdClass[] One entry per deletion recorded, with userid, messageid and actionid
     */
    public function delete_conversation_by_id(int $userid, int $conversationid);

    /**
     * Returns the conversation id a message belongs to.
     *
     * @param int $messageid
     * @return int|bool The conversation id, false when the message does not exist
     */
    public function get_message_conversation_id(int $messageid);

    /**
     * Counts the conversations holding at least one message the user has not read yet.
     *
     * @param int $userid
     * @return int
     */
    public function count_unread_conversations(int $userid): int;

    /**
     * Counts the unread messages of one conversation, ignoring those the user sent themselves.
     *
     * @param int $userid
     * @param int $conversationid
     * @return int
     */
    public function count_unread_in_conversation(int $userid, int $conversationid): int;

    /**
     * Returns the persisted form of the given messages, indexed by identifier.
     *
     * @param int[] $messageids
     * @return \stdClass[] Indexed by message id
     */
    public function get_message_records(array $messageids): array;

    /**
     * Searches the messages the user sent or received, most recent first.
     *
     * @param int $userid
     * @param string $search
     * @param int $limitfrom
     * @param int $limitnum
     * @return \stdClass[]
     */
    public function search_message_rows(int $userid, string $search, int $limitfrom = 0,
            int $limitnum = 0): array;

    // ---------------------------------------------------------------------------------------------
    // Per-user conversation actions.
    // ---------------------------------------------------------------------------------------------

    /**
     * Mutes a conversation for a user.
     *
     * @param int $userid
     * @param int $conversationid
     * @return void
     */
    public function mute_conversation(int $userid, int $conversationid): void;

    /**
     * Unmutes a conversation for a user.
     *
     * @param int $userid
     * @param int $conversationid
     * @return void
     */
    public function unmute_conversation(int $userid, int $conversationid): void;

    /**
     * Whether a conversation is muted for a user.
     *
     * @param int $userid
     * @param int $conversationid
     * @return bool
     */
    public function is_conversation_muted(int $userid, int $conversationid): bool;

    // ---------------------------------------------------------------------------------------------
    // Contacts, contact requests and blocks.
    // ---------------------------------------------------------------------------------------------

    /**
     * Adds a contact relationship between two users.
     *
     * @param int $userid
     * @param int $contactid
     * @return \stdClass The contact record
     */
    public function add_contact(int $userid, int $contactid): \stdClass;

    /**
     * Removes the contact relationship between two users.
     *
     * @param int $userid
     * @param int $contactid
     * @return \stdClass|false The removed record, false when there was none
     */
    public function remove_contact(int $userid, int $contactid);

    /**
     * Whether two users are contacts.
     *
     * @param int $userid
     * @param int $contactid
     * @return bool
     */
    public function is_contact(int $userid, int $contactid): bool;

    /**
     * Returns the contact record between two users, in either direction.
     *
     * @param int $userid
     * @param int $contactid
     * @return \stdClass|false
     */
    public function get_contact(int $userid, int $contactid);

    /**
     * Counts the contacts of a user, in either direction.
     *
     * @param int $userid
     * @return int
     */
    public function count_contacts(int $userid): int;

    /**
     * Lists the users this one is in contact with, most recently added first.
     *
     * @param int $userid
     * @param int $limitfrom
     * @param int $limitnum Zero returns the whole list
     * @return int[]
     */
    public function get_contact_ids(int $userid, int $limitfrom = 0, int $limitnum = 0): array;

    /**
     * Lists the enabled conversations holding at least one message the user has not read.
     *
     * @param int $userid
     * @return int[] Conversation identifier to conversation type
     */
    public function get_unread_conversation_types(int $userid): array;

    /**
     * Lists the conversations of a user that count towards the conversation totals, with their type.
     *
     * @param int $userid
     * @return int[] Conversation identifier to conversation type
     */
    public function get_countable_conversation_types(int $userid): array;

    /**
     * Composes a user's conversation list, most recently active first.
     *
     * @param int $userid
     * @param int $limitfrom
     * @param int $limitnum
     * @param int|null $type Restrict to one conversation type
     * @param bool $mergeself Return personal conversations alongside individual ones
     * @param int[] $favouriteids Conversations the caller considers favourites
     * @param bool|null $onlyfavourites Keep only favourites, exclude them, or do not filter
     * @return \stdClass[]
     */
    public function get_conversation_summaries(int $userid, int $limitfrom = 0, int $limitnum = 20,
            ?int $type = null, bool $mergeself = false, array $favouriteids = [],
            ?bool $onlyfavourites = null): array;

    /**
     * Lists the users this one has blocked.
     *
     * @param int $userid
     * @return int[]
     */
    public function get_blocked_user_ids(int $userid): array;

    /**
     * Lists the users who have asked this one for contact, oldest request first.
     *
     * @param int $userid
     * @param int $limitfrom
     * @param int $limitnum Zero returns the whole list
     * @return int[]
     */
    public function get_received_contact_request_user_ids(int $userid, int $limitfrom = 0,
            int $limitnum = 0): array;

    /**
     * Blocks a user.
     *
     * @param int $userid The blocking user
     * @param int $usertoblockid The blocked user
     * @return \stdClass The block record
     */
    public function block_user(int $userid, int $usertoblockid): \stdClass;

    /**
     * Unblocks a user.
     *
     * @param int $userid The blocking user
     * @param int $usertounblockid The unblocked user
     * @return \stdClass|false The removed record, false when there was none
     */
    public function unblock_user(int $userid, int $usertounblockid);

    /**
     * Whether a user has blocked another one.
     *
     * @param int $userid
     * @param int $blockeduserid
     * @return bool
     */
    public function is_blocked(int $userid, int $blockeduserid): bool;

    /**
     * Creates a contact request.
     *
     * @param int $userid The requesting user
     * @param int $requesteduserid The requested user
     * @return \stdClass The contact request record
     */
    public function create_contact_request(int $userid, int $requesteduserid): \stdClass;

    /**
     * Confirms a contact request, turning it into a contact relationship.
     *
     * @param int $userid The user who created the request
     * @param int $requesteduserid The user confirming it
     * @return \stdClass|false The contact record created, false when there was no pending request
     */
    public function confirm_contact_request(int $userid, int $requesteduserid);

    /**
     * Declines a contact request.
     *
     * @param int $userid The user who created the request
     * @param int $requesteduserid The user declining it
     * @return void
     */
    public function decline_contact_request(int $userid, int $requesteduserid);

    /**
     * Returns the contact requests between two users, in either direction.
     *
     * @param int $userid
     * @param int $requesteduserid
     * @return \stdClass[]
     */
    public function get_contact_requests_between_users(int $userid, int $requesteduserid): array;

    /**
     * Whether a contact request exists between two users, in either direction.
     *
     * @param int $userid
     * @param int $requesteduserid
     * @return bool
     */
    public function does_contact_request_exist(int $userid, int $requesteduserid): bool;

    /**
     * Counts the contact requests received by a user, excluding those from users they blocked.
     *
     * @param int $userid
     * @return int
     */
    public function get_received_contact_requests_count(int $userid): int;
}
