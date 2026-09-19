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
use core_message\helper;

/**
 * In-process implementation of the conversation context, backed by the monolithic database.
 *
 * @package    core_message
 * @copyright  2026 Benedetta Pia Celano
 * @license    http://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class legacy_provider implements provider {

    // ---------------------------------------------------------------------------------------------
    // Conversation aggregate.
    // ---------------------------------------------------------------------------------------------

    #[\Override]
    public function create_conversation(int $type, array $userids, ?string $name = null, int $enabled = 1,
            ?string $component = null, ?string $itemtype = null, ?int $itemid = null, ?int $contextid = null) {
        global $DB;

        $validtypes = [
            api::MESSAGE_CONVERSATION_TYPE_INDIVIDUAL,
            api::MESSAGE_CONVERSATION_TYPE_GROUP,
            api::MESSAGE_CONVERSATION_TYPE_SELF,
        ];

        if (!in_array($type, $validtypes)) {
            throw new \moodle_exception('An invalid conversation type was specified.');
        }

        // Sanity check.
        if ($type == api::MESSAGE_CONVERSATION_TYPE_INDIVIDUAL) {
            if (count($userids) > 2) {
                throw new \moodle_exception('An individual conversation can not have more than two users.');
            }
            if ($userids[0] == $userids[1]) {
                throw new \moodle_exception('Trying to create an individual conversation instead of a self conversation.');
            }
        } else if ($type == api::MESSAGE_CONVERSATION_TYPE_SELF) {
            if (count($userids) != 1) {
                throw new \moodle_exception('A self conversation can not have more than one user.');
            }
        }

        $conversation = new \stdClass();
        $conversation->type = $type;
        $conversation->name = $name;
        $conversation->convhash = null;
        if ($type == api::MESSAGE_CONVERSATION_TYPE_INDIVIDUAL || $type == api::MESSAGE_CONVERSATION_TYPE_SELF) {
            $conversation->convhash = helper::get_conversation_hash($userids);

            // Don't blindly create a conversation between 2 users if there is already one present -
            // return that. This stops us making duplicate self and individual conversations, which
            // is invalid.
            if ($record = $DB->get_record('message_conversations', ['convhash' => $conversation->convhash])) {
                return $record;
            }
        }
        $conversation->component = $component;
        $conversation->itemtype = $itemtype;
        $conversation->itemid = $itemid;
        $conversation->contextid = $contextid;
        $conversation->enabled = $enabled;
        $conversation->timecreated = time();
        $conversation->timemodified = $conversation->timecreated;
        $conversation->id = $DB->insert_record('message_conversations', $conversation);

        // Add users to this conversation.
        $arrmembers = [];
        foreach ($userids as $userid) {
            $member = new \stdClass();
            $member->conversationid = $conversation->id;
            $member->userid = $userid;
            $member->timecreated = time();
            $member->id = $DB->insert_record('message_conversation_members', $member);

            $arrmembers[] = $member;
        }

        $conversation->members = $arrmembers;

        return $conversation;
    }

    #[\Override]
    public function get_conversation_by_id(int $conversationid) {
        global $DB;

        return $DB->get_record('message_conversations', ['id' => $conversationid]);
    }

    #[\Override]
    public function get_conversation_by_message(int $messageid) {
        global $DB;

        $sql = "SELECT mc.*
                  FROM {message_conversations} mc
            INNER JOIN {messages} m
                    ON mc.id = m.conversationid
                 WHERE m.id = :messageid";

        return $DB->get_record_sql($sql, ['messageid' => $messageid]);
    }

    #[\Override]
    public function get_conversation_between_users(array $userids) {
        global $DB;

        if (empty($userids)) {
            return false;
        }

        $hash = helper::get_conversation_hash($userids);

        if ($conversation = $DB->get_record('message_conversations', ['type' => api::MESSAGE_CONVERSATION_TYPE_INDIVIDUAL,
                'convhash' => $hash])) {
            return $conversation->id;
        }

        return false;
    }

    #[\Override]
    public function get_self_conversation(int $userid) {
        global $DB;

        return $DB->get_record('message_conversations', [
            'type' => api::MESSAGE_CONVERSATION_TYPE_SELF,
            'convhash' => helper::get_conversation_hash([$userid]),
        ]);
    }

    #[\Override]
    public function get_conversations_between_users(int $userid1, int $userid2, int $limitfrom = 0,
            int $limitnum = 20): array {
        global $DB;

        if ($userid1 == $userid2) {
            return [];
        }

        // Get all conversation where both user1 and user2 are members.
        $sql = "SELECT mc.id, mc.type, mc.name, mc.timecreated
                  FROM {message_conversations} mc
            INNER JOIN {message_conversation_members} mcm1
                    ON mc.id = mcm1.conversationid
            INNER JOIN {message_conversation_members} mcm2
                    ON mc.id = mcm2.conversationid
                 WHERE mcm1.userid = :userid1
                   AND mcm2.userid = :userid2
                   AND mc.enabled != 0
              ORDER BY mc.timecreated DESC";

        return $DB->get_records_sql($sql, ['userid1' => $userid1, 'userid2' => $userid2], $limitfrom, $limitnum);
    }

    #[\Override]
    public function get_conversation_by_area(string $component, string $itemtype, int $itemid, int $contextid) {
        global $DB;

        return $DB->get_record('message_conversations',
            [
                'itemid' => $itemid,
                'contextid' => $contextid,
                'component' => $component,
                'itemtype'  => $itemtype,
            ]
        );
    }

    #[\Override]
    public function is_conversation_area_enabled(string $component, string $itemtype, int $itemid,
            int $contextid): bool {
        global $DB;

        return $DB->record_exists('message_conversations',
            [
                'itemid' => $itemid,
                'contextid' => $contextid,
                'component' => $component,
                'itemtype' => $itemtype,
                'enabled' => api::MESSAGE_CONVERSATION_ENABLED,
            ]
        );
    }

    #[\Override]
    public function enable_conversation(int $conversationid) {
        global $DB;

        $conversation = new \stdClass();
        $conversation->id = $conversationid;
        $conversation->enabled = api::MESSAGE_CONVERSATION_ENABLED;
        $conversation->timemodified = time();
        $DB->update_record('message_conversations', $conversation);
    }

    #[\Override]
    public function disable_conversation(int $conversationid) {
        global $DB;

        $conversation = new \stdClass();
        $conversation->id = $conversationid;
        $conversation->enabled = api::MESSAGE_CONVERSATION_DISABLED;
        $conversation->timemodified = time();
        $DB->update_record('message_conversations', $conversation);
    }

    #[\Override]
    public function update_conversation_name(int $conversationid, string $name) {
        global $DB;

        if ($conversation = $DB->get_record('message_conversations', ['id' => $conversationid])) {
            if ($name <> $conversation->name) {
                $conversation->name = $name;
                $conversation->timemodified = time();
                $DB->update_record('message_conversations', $conversation);
            }
        }
    }

    #[\Override]
    public function delete_all_conversation_data(int $conversationid) {
        global $DB;

        $DB->delete_records('message_conversations', ['id' => $conversationid]);
        $DB->delete_records('message_conversation_members', ['conversationid' => $conversationid]);
        $DB->delete_records('message_conversation_actions', ['conversationid' => $conversationid]);

        // Now, go through and delete any messages and related message actions for the conversation.
        if ($messages = $DB->get_records('messages', ['conversationid' => $conversationid])) {
            $messageids = array_keys($messages);

            list($insql, $inparams) = $DB->get_in_or_equal($messageids);
            $DB->delete_records_select('message_user_actions', "messageid $insql", $inparams);

            // Delete the messages now.
            $DB->delete_records('messages', ['conversationid' => $conversationid]);
        }
    }

    // ---------------------------------------------------------------------------------------------
    // Membership.
    // ---------------------------------------------------------------------------------------------

    #[\Override]
    public function is_user_in_conversation(int $userid, int $conversationid): bool {
        global $DB;

        return $DB->record_exists('message_conversation_members', ['conversationid' => $conversationid,
            'userid' => $userid]);
    }

    #[\Override]
    public function get_conversation_member_ids(int $conversationid, int $limitfrom = 0, int $limitnum = 0): array {
        global $DB;

        $members = $DB->get_records('message_conversation_members', ['conversationid' => $conversationid],
            'timecreated ASC, id ASC', 'userid', $limitfrom, $limitnum);

        return array_map('intval', array_keys($members));
    }

    #[\Override]
    public function add_members_to_conversation(array $userids, int $convid) {
        global $DB;

        $conversation = $DB->get_record('message_conversations', ['id' => $convid], '*', MUST_EXIST);

        // We can only add members to a group conversation.
        if ($conversation->type != api::MESSAGE_CONVERSATION_TYPE_GROUP) {
            throw new \moodle_exception('You can not add members to a non-group conversation.');
        }

        // Be sure we are not trying to add a non existing user to the conversation. Work only with
        // existing users.
        list($useridcondition, $params) = $DB->get_in_or_equal($userids, SQL_PARAMS_NAMED);
        $existingusers = $DB->get_fieldset_select('user', 'id', "id $useridcondition", $params);

        // Be sure we are not adding a user is already member of the conversation. Take all the
        // members.
        $memberuserids = array_values($DB->get_records_menu(
            'message_conversation_members', ['conversationid' => $convid], 'id', 'id, userid')
        );

        // Work with existing new members.
        $members = [];
        $newuserids = array_diff($existingusers, $memberuserids);
        foreach ($newuserids as $userid) {
            $member = new \stdClass();
            $member->conversationid = $convid;
            $member->userid = $userid;
            $member->timecreated = time();
            $members[] = $member;
        }

        $DB->insert_records('message_conversation_members', $members);
    }

    #[\Override]
    public function remove_members_from_conversation(array $userids, int $convid) {
        global $DB;

        $conversation = $DB->get_record('message_conversations', ['id' => $convid], '*', MUST_EXIST);

        if ($conversation->type != api::MESSAGE_CONVERSATION_TYPE_GROUP) {
            throw new \moodle_exception('You can not remove members from a non-group conversation.');
        }

        list($useridcondition, $params) = $DB->get_in_or_equal($userids, SQL_PARAMS_NAMED);
        $params['convid'] = $convid;

        $DB->delete_records_select('message_conversation_members',
            "conversationid = :convid AND userid $useridcondition", $params);
    }

    #[\Override]
    public function count_conversation_members(int $convid): int {
        global $DB;

        return $DB->count_records('message_conversation_members', ['conversationid' => $convid]);
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

        // Check if the recipient can be messaged by the sender.
        return $this->can_contact_user($recipientid, $senderid, $evenifblocked);
    }

    #[\Override]
    public function can_send_message_to_conversation(int $userid, int $conversationid): bool {
        global $DB;

        $systemcontext = \context_system::instance();
        if (!has_capability('moodle/site:sendmessage', $systemcontext, $userid)) {
            return false;
        }

        if (!$this->is_user_in_conversation($userid, $conversationid)) {
            return false;
        }

        // User can post messages and is in the conversation, but we need to check the conversation
        // type to know whether or not to check the user privacy settings via can_contact_user().
        $conversation = $DB->get_record('message_conversations', ['id' => $conversationid], '*', MUST_EXIST);
        if ($conversation->type == api::MESSAGE_CONVERSATION_TYPE_GROUP ||
            $conversation->type == api::MESSAGE_CONVERSATION_TYPE_SELF) {
            return true;
        } else if ($conversation->type == api::MESSAGE_CONVERSATION_TYPE_INDIVIDUAL) {
            // Get the other user in the conversation.
            $memberids = $this->get_conversation_member_ids($conversationid);
            $otheruserids = array_filter($memberids, function($memberid) use ($userid) {
                return $memberid != $userid;
            });
            $otheruserid = reset($otheruserids);
            // Check if the user still exists in the system.
            if (!$otheruserid || !\core\user::is_real_user($otheruserid, true)) {
                return false;
            }
            return $this->can_contact_user($otheruserid, $userid);
        } else {
            throw new \moodle_exception("Invalid conversation type '$conversation->type'.");
        }
    }

    #[\Override]
    public function send_message_to_conversation(int $userid, int $conversationid, string $message,
            int $format): \stdClass {
        global $CFG, $DB, $PAGE;

        require_once($CFG->dirroot . '/lib/messagelib.php');

        $eventdata = new \core\message\message();
        $eventdata->courseid         = 1;
        $eventdata->component        = 'moodle';
        $eventdata->name             = 'instantmessage';
        $eventdata->userfrom         = \core_user::get_user($userid);
        $eventdata->convid           = $conversationid;

        if ($format == FORMAT_HTML) {
            $eventdata->fullmessagehtml  = $message;
            // Some message processors may revert to sending plain text even if html is supplied,
            // so we keep both plain and html versions if we're intending to send html.
            $eventdata->fullmessage = html_to_text($eventdata->fullmessagehtml);
        } else {
            $eventdata->fullmessage      = $message;
            $eventdata->fullmessagehtml  = '';
        }

        $eventdata->fullmessageformat = $format;
        $eventdata->smallmessage = $message; // Store the message unfiltered. Clean up on output.

        $eventdata->timecreated     = time();
        $eventdata->notification    = 0;
        // Custom data for event.
        $customdata = [
            'actionbuttons' => [
                'send' => get_string('send', 'message'),
            ],
            'placeholders' => [
                'send' => get_string('writeamessage', 'message'),
            ],
        ];

        $userpicture = new \user_picture($eventdata->userfrom);
        $userpicture->size = 1; // Use f1 size.
        $userpicture = $userpicture->get_url($PAGE)->out(false);

        $conv = $DB->get_record('message_conversations', ['id' => $conversationid]);
        if ($conv->type == api::MESSAGE_CONVERSATION_TYPE_GROUP) {
            $convextrafields = api::get_linked_conversation_extra_fields([$conv]);
            // Conversation images.
            $customdata['notificationsendericonurl'] = $userpicture;
            $imageurl = isset($convextrafields[$conv->id]) ? $convextrafields[$conv->id]['imageurl'] : null;
            if ($imageurl) {
                $customdata['notificationiconurl'] = $imageurl;
            }
            // Conversation name.
            if (is_null($conv->contextid)) {
                $convcontext = \context_user::instance($userid);
            } else {
                $convcontext = \context::instance_by_id($conv->contextid);
            }
            $customdata['conversationname'] = format_string($conv->name, true, ['context' => $convcontext]);
        } else if ($conv->type == api::MESSAGE_CONVERSATION_TYPE_INDIVIDUAL) {
            $customdata['notificationiconurl'] = $userpicture;
        }
        $eventdata->customdata = $customdata;

        $messageid = message_send($eventdata);

        if (!$messageid) {
            throw new \moodle_exception('messageundeliveredbynotificationsettings', 'moodle');
        }

        $messagerecord = $DB->get_record('messages', ['id' => $messageid], 'id, useridfrom, fullmessage,
                timecreated, fullmessagetrust');

        return (object) [
            'id' => $messagerecord->id,
            'useridfrom' => $messagerecord->useridfrom,
            'text' => $messagerecord->fullmessage,
            'timecreated' => $messagerecord->timecreated,
            'fullmessagetrust' => $messagerecord->fullmessagetrust,
        ];
    }

    #[\Override]
    public function get_conversation_message_records(int $userid, int $convid, int $timedeleted = 0,
            int $limitfrom = 0, int $limitnum = 0, string $sort = 'timecreated ASC', int $timefrom = 0,
            int $timeto = 0): array {

        return helper::get_conversation_messages($userid, $convid, $timedeleted, $limitfrom, $limitnum, $sort,
            $timefrom, $timeto);
    }

    #[\Override]
    public function mark_message_as_read(int $userid, \stdClass $message, ?int $timeread = null): int {
        global $DB;

        if (is_null($timeread)) {
            $timeread = time();
        }

        $mua = new \stdClass();
        $mua->userid = $userid;
        $mua->messageid = $message->id;
        $mua->action = api::MESSAGE_ACTION_READ;
        $mua->timecreated = $timeread;

        return $DB->insert_record('message_user_actions', $mua);
    }

    #[\Override]
    public function mark_all_messages_as_read(int $userid, ?int $conversationid = null) {
        global $DB;

        $messagesql = "SELECT m.*
                         FROM {messages} m
                   INNER JOIN {message_conversations} mc
                           ON mc.id = m.conversationid
                   INNER JOIN {message_conversation_members} mcm
                           ON mcm.conversationid = mc.id
                    LEFT JOIN {message_user_actions} mua
                           ON (mua.messageid = m.id AND mua.userid = ? AND mua.action = ?)
                        WHERE mua.id is NULL
                          AND mcm.userid = ?
                          AND m.useridfrom != ?";
        $messageparams = [];
        $messageparams[] = $userid;
        $messageparams[] = api::MESSAGE_ACTION_READ;
        $messageparams[] = $userid;
        $messageparams[] = $userid;
        if (!is_null($conversationid)) {
            $messagesql .= " AND mc.id = ?";
            $messageparams[] = $conversationid;
        }

        $marked = [];
        $messages = $DB->get_recordset_sql($messagesql, $messageparams);
        foreach ($messages as $message) {
            $marked[] = (object) [
                'messageid' => (int) $message->id,
                'useridfrom' => (int) $message->useridfrom,
                'actionid' => $this->mark_message_as_read($userid, $message),
            ];
        }
        $messages->close();

        return $marked;
    }

    #[\Override]
    public function delete_message(int $userid, int $messageid) {
        global $DB;

        if (!$DB->record_exists('messages', ['id' => $messageid])) {
            return false;
        }

        // Check if the user has already deleted this message.
        if ($DB->record_exists('message_user_actions', ['userid' => $userid,
                'messageid' => $messageid, 'action' => api::MESSAGE_ACTION_DELETED])) {
            return false;
        }

        $mua = new \stdClass();
        $mua->userid = $userid;
        $mua->messageid = $messageid;
        $mua->action = api::MESSAGE_ACTION_DELETED;
        $mua->timecreated = time();

        return $DB->insert_record('message_user_actions', $mua);
    }

    #[\Override]
    public function delete_message_for_all_users(int $messageid) {
        global $DB;

        if (!$DB->record_exists('messages', ['id' => $messageid])) {
            return [];
        }

        // Get all members in the conversation where the message belongs.
        $membersql = "SELECT mcm.id, mcm.userid
                        FROM {message_conversation_members} mcm
                  INNER JOIN {messages} m
                          ON mcm.conversationid = m.conversationid
                       WHERE m.id = :messageid";
        $members = $DB->get_records_sql($membersql, ['messageid' => $messageid]);

        $deleted = [];
        foreach ($members as $member) {
            if ($actionid = $this->delete_message((int) $member->userid, $messageid)) {
                $deleted[] = (object) [
                    'userid' => (int) $member->userid,
                    'messageid' => $messageid,
                    'actionid' => $actionid,
                ];
            }
        }

        return $deleted;
    }

    #[\Override]
    public function delete_conversation_by_id(int $userid, int $conversationid) {
        global $DB;

        // Get all messages belonging to this conversation that have not already been deleted by
        // this user.
        $sql = "SELECT m.*
                 FROM {messages} m
           INNER JOIN {message_conversations} mc
                   ON m.conversationid = mc.id
            LEFT JOIN {message_user_actions} mua
                   ON (mua.messageid = m.id AND mua.userid = ? AND mua.action = ?)
                WHERE mua.id is NULL
                  AND mc.id = ?
             ORDER BY m.timecreated ASC";
        $messages = $DB->get_records_sql($sql, [$userid, api::MESSAGE_ACTION_DELETED, $conversationid]);

        // Ok, mark these as deleted.
        $deleted = [];
        foreach ($messages as $message) {
            $mua = new \stdClass();
            $mua->userid = $userid;
            $mua->messageid = $message->id;
            $mua->action = api::MESSAGE_ACTION_DELETED;
            $mua->timecreated = time();
            $mua->id = $DB->insert_record('message_user_actions', $mua);

            $deleted[] = (object) [
                'userid' => $userid,
                'messageid' => (int) $message->id,
                'actionid' => $mua->id,
            ];
        }

        return $deleted;
    }

    #[\Override]
    public function get_message_conversation_id(int $messageid) {
        global $DB;

        return $DB->get_field('messages', 'conversationid', ['id' => $messageid]);
    }

    #[\Override]
    public function count_unread_conversations(int $userid): int {
        global $DB;

        $sql = "SELECT COUNT(DISTINCT(m.conversationid))
                  FROM {messages} m
            INNER JOIN {message_conversations} mc
                    ON m.conversationid = mc.id
            INNER JOIN {message_conversation_members} mcm
                    ON mc.id = mcm.conversationid
             LEFT JOIN {message_user_actions} mua
                    ON (mua.messageid = m.id AND mua.userid = ? AND mua.action = ?)
                 WHERE mcm.userid = ?
                   AND mc.enabled = ?
                   AND mcm.userid != m.useridfrom
                   AND mua.id is NULL";

        return $DB->count_records_sql($sql, [$userid, api::MESSAGE_ACTION_READ, $userid,
            api::MESSAGE_CONVERSATION_ENABLED]);
    }

    // ---------------------------------------------------------------------------------------------
    // Per-user conversation actions.
    // ---------------------------------------------------------------------------------------------

    #[\Override]
    public function mute_conversation(int $userid, int $conversationid): void {
        global $DB;

        $mutedconversation = new \stdClass();
        $mutedconversation->userid = $userid;
        $mutedconversation->conversationid = $conversationid;
        $mutedconversation->action = api::CONVERSATION_ACTION_MUTED;
        $mutedconversation->timecreated = time();

        $DB->insert_record('message_conversation_actions', $mutedconversation);
    }

    #[\Override]
    public function unmute_conversation(int $userid, int $conversationid): void {
        global $DB;

        $DB->delete_records('message_conversation_actions',
            [
                'userid' => $userid,
                'conversationid' => $conversationid,
                'action' => api::CONVERSATION_ACTION_MUTED,
            ]
        );
    }

    #[\Override]
    public function is_conversation_muted(int $userid, int $conversationid): bool {
        global $DB;

        return $DB->record_exists('message_conversation_actions',
            [
                'userid' => $userid,
                'conversationid' => $conversationid,
                'action' => api::CONVERSATION_ACTION_MUTED,
            ]
        );
    }

    // ---------------------------------------------------------------------------------------------
    // Contacts, contact requests and blocks.
    // ---------------------------------------------------------------------------------------------

    #[\Override]
    public function add_contact(int $userid, int $contactid): \stdClass {
        global $DB;

        $messagecontact = new \stdClass();
        $messagecontact->userid = $userid;
        $messagecontact->contactid = $contactid;
        $messagecontact->timecreated = time();
        $messagecontact->id = $DB->insert_record('message_contacts', $messagecontact);

        return $messagecontact;
    }

    #[\Override]
    public function remove_contact(int $userid, int $contactid) {
        global $DB;

        if ($contact = $this->get_contact($userid, $contactid)) {
            $DB->delete_records('message_contacts', ['id' => $contact->id]);

            return $contact;
        }

        return false;
    }

    #[\Override]
    public function is_contact(int $userid, int $contactid): bool {
        global $DB;

        $sql = "SELECT id
                  FROM {message_contacts} mc
                 WHERE (mc.userid = ? AND mc.contactid = ?)
                    OR (mc.userid = ? AND mc.contactid = ?)";
        return $DB->record_exists_sql($sql, [$userid, $contactid, $contactid, $userid]);
    }

    #[\Override]
    public function get_contact(int $userid, int $contactid) {
        global $DB;

        $sql = "SELECT mc.*
                  FROM {message_contacts} mc
                 WHERE (mc.userid = ? AND mc.contactid = ?)
                    OR (mc.userid = ? AND mc.contactid = ?)";
        return $DB->get_record_sql($sql, [$userid, $contactid, $contactid, $userid]);
    }

    #[\Override]
    public function count_contacts(int $userid): int {
        global $DB;

        $sql = "SELECT COUNT(id)
                  FROM {message_contacts}
                 WHERE userid = ? OR contactid = ?";
        return $DB->count_records_sql($sql, [$userid, $userid]);
    }

    #[\Override]
    public function get_contact_ids(int $userid, int $limitfrom = 0, int $limitnum = 0): array {
        global $DB;

        $sql = "SELECT *
                  FROM {message_contacts} mc
                 WHERE mc.userid = ? OR mc.contactid = ?
              ORDER BY timecreated DESC, id ASC";
        $contacts = $DB->get_records_sql($sql, [$userid, $userid], $limitfrom, $limitnum);

        $userids = [];
        foreach ($contacts as $contact) {
            // The relationship is symmetrical and recorded once: the other user may be in either
            // column.
            $userids[] = ($contact->userid == $userid) ? $contact->contactid : $contact->userid;
        }

        return $userids;
    }

    #[\Override]
    public function get_conversation_summaries(int $userid, int $limitfrom = 0, int $limitnum = 20,
            ?int $type = null, bool $mergeself = false, array $favouriteids = [],
            ?bool $onlyfavourites = null): array {
        global $DB;

        // The favourite filter (identifiers and a direction) is applied inside the query.
        $favouritesql = "";
        $favouriteparams = [];
        if (null !== $onlyfavourites && !empty($favouriteids)) {
            list ($insql, $favouriteparams) =
                    $DB->get_in_or_equal($favouriteids, SQL_PARAMS_NAMED, 'favouriteids', $onlyfavourites);
            $favouritesql = " AND mc.id {$insql} ";
        }

        $typesql = "";
        $typeparams = [];
        if (!is_null($type)) {
            if ($mergeself && $type == api::MESSAGE_CONVERSATION_TYPE_INDIVIDUAL) {
                // Self conversations are returned alongside individual ones when asked.
                $typesql = " AND (mc.type = :convtype1 OR mc.type = :convtype2) ";
                $typeparams = ['convtype1' => $type, 'convtype2' => api::MESSAGE_CONVERSATION_TYPE_SELF];
            } else {
                $typesql = " AND mc.type = :convtype ";
                $typeparams = ['convtype' => $type];
            }
        }

        // Last visible message: the most recent instant among the messages the reader has not
        // deleted, then the highest id at that instant. Conversations without messages are
        // presorted.
        $sql = "SELECT m.id as messageid, mc.id as id, mc.name as conversationname, mc.type as conversationtype,
                       m.useridfrom, m.smallmessage, m.fullmessage, m.fullmessageformat, m.fullmessagetrust,
                       m.fullmessagehtml, m.timecreated, mc.component, mc.itemtype, mc.itemid, mc.contextid,
                       mca.action as ismuted
                  FROM {message_conversations} mc
            INNER JOIN {message_conversation_members} mcm
                    ON (mcm.conversationid = mc.id AND mcm.userid = :userid3)
             LEFT JOIN (
                           SELECT m.conversationid, MAX(m.id) AS messageid
                             FROM {messages} m
                       INNER JOIN (
                                       SELECT m.conversationid, MAX(m.timecreated) as maxtime
                                         FROM {messages} m
                                   INNER JOIN {message_conversation_members} mcm
                                           ON mcm.conversationid = m.conversationid
                                    LEFT JOIN {message_user_actions} mua
                                           ON (mua.messageid = m.id AND mua.userid = :userid AND
                                               mua.action = :action)
                                        WHERE mua.id is NULL
                                          AND mcm.userid = :userid2
                                     GROUP BY m.conversationid
                                  ) maxmessage
                                ON maxmessage.maxtime = m.timecreated
                               AND maxmessage.conversationid = m.conversationid
                         GROUP BY m.conversationid
                       ) lastmessage
                    ON lastmessage.conversationid = mc.id
             LEFT JOIN {messages} m
                    ON m.id = lastmessage.messageid
             LEFT JOIN {message_conversation_actions} mca
                    ON (mca.conversationid = mc.id AND mca.userid = :userid4 AND mca.action = :convaction)
                 WHERE mc.id IS NOT NULL
                   AND mc.enabled = 1 $typesql $favouritesql
              ORDER BY (CASE WHEN m.timecreated IS NULL THEN 0 ELSE 1 END) DESC, m.timecreated DESC, id DESC";

        $params = array_merge($favouriteparams, $typeparams, [
            'userid' => $userid,
            'userid2' => $userid,
            'userid3' => $userid,
            'userid4' => $userid,
            'action' => api::MESSAGE_ACTION_DELETED,
            'convaction' => api::CONVERSATION_ACTION_MUTED,
        ]);

        // A recordset: the first column (message id) is null for conversations without messages.
        $recordset = $DB->get_recordset_sql($sql, $params, $limitfrom, $limitnum);
        $rows = [];
        foreach ($recordset as $row) {
            $rows[(int) $row->id] = $row;
        }
        $recordset->close();

        if (empty($rows)) {
            return [];
        }

        $ids = array_keys($rows);
        $membercounts = $this->count_members_of($ids);
        $unreadcounts = $this->count_unread_of($userid, $ids);
        $memberids = $this->members_to_show($userid, $rows);
        $selfmessagestotal = $this->count_self_conversation_messages($userid);

        $summaries = [];
        foreach ($rows as $row) {
            $novisiblemessage = empty($row->messageid);

            // An emptied individual or self conversation leaves the list; a never used self
            // conversation and a group stay.
            if ($row->conversationtype == api::MESSAGE_CONVERSATION_TYPE_INDIVIDUAL && $novisiblemessage) {
                continue;
            }
            if ($row->conversationtype == api::MESSAGE_CONVERSATION_TYPE_SELF && $novisiblemessage
                    && $selfmessagestotal > 0) {
                continue;
            }

            $summary = new \stdClass();
            $summary->id = (int) $row->id;
            $summary->name = $row->conversationname;
            $summary->type = (int) $row->conversationtype;
            $summary->component = $row->component;
            $summary->itemtype = $row->itemtype;
            $summary->itemid = $row->itemid;
            $summary->contextid = $row->contextid;
            $summary->ismuted = !empty($row->ismuted);
            $summary->membercount = (int) ($membercounts[$row->id]->membercount ?? 0);
            $summary->unreadcount = (int) ($unreadcounts[$row->id]->unreadcount ?? 0);
            $summary->memberids = $memberids[(int) $row->id] ?? [];
            $summary->lastmessage = null;

            if (!$novisiblemessage) {
                $lastmessage = new \stdClass();
                $lastmessage->id = (int) $row->messageid;
                $lastmessage->useridfrom = (int) $row->useridfrom;
                $lastmessage->smallmessage = $row->smallmessage;
                $lastmessage->fullmessage = $row->fullmessage;
                $lastmessage->fullmessageformat = (int) $row->fullmessageformat;
                $lastmessage->fullmessagehtml = $row->fullmessagehtml;
                $lastmessage->fullmessagetrust = (int) $row->fullmessagetrust;
                $lastmessage->timecreated = (int) $row->timecreated;
                $summary->lastmessage = $lastmessage;
            }

            $summaries[] = $summary;
        }

        return $summaries;
    }

    /**
     * Counts the members of each of the given conversations.
     *
     * @param int[] $conversationids
     * @return array Keyed by conversation identifier
     */
    protected function count_members_of(array $conversationids): array {
        global $DB;

        list ($insql, $params) = $DB->get_in_or_equal($conversationids, SQL_PARAMS_NAMED, 'convid');
        $sql = "SELECT conversationid, count(DISTINCT userid) AS membercount
                  FROM {message_conversation_members} mcm
                 WHERE mcm.conversationid $insql
              GROUP BY mcm.conversationid";

        return $DB->get_records_sql($sql, $params);
    }

    /**
     * Counts the unread messages of each of the given conversations, ignoring the reader's own.
     *
     * @param int $userid
     * @param int[] $conversationids
     * @return array Keyed by conversation identifier
     */
    protected function count_unread_of(int $userid, array $conversationids): array {
        global $DB;

        list ($insql, $inparams) = $DB->get_in_or_equal($conversationids, SQL_PARAMS_NAMED, 'convid');
        $sql = "SELECT m.conversationid, count(m.id) as unreadcount
                  FROM {messages} m
            INNER JOIN {message_conversations} mc
                    ON mc.id = m.conversationid
            INNER JOIN {message_conversation_members} mcm
                    ON m.conversationid = mcm.conversationid
             LEFT JOIN {message_user_actions} mua
                    ON (mua.messageid = m.id AND mua.userid = :userid AND
                        (mua.action = :readaction OR mua.action = :deletedaction))
                 WHERE mcm.userid = :userid2
                   AND m.useridfrom != :userid3
                   AND mua.id is NULL
                   AND m.conversationid $insql
              GROUP BY m.conversationid";

        $params = array_merge($inparams, [
            'userid' => $userid,
            'userid2' => $userid,
            'userid3' => $userid,
            'readaction' => api::MESSAGE_ACTION_READ,
            'deletedaction' => api::MESSAGE_ACTION_DELETED,
        ]);

        return $DB->get_records_sql($sql, $params);
    }

    /**
     * Chooses, for each conversation, the members the list should show.
     *
     * @param int $userid
     * @param array $rows Rows of the conversation query
     * @return array Conversation identifier to member identifiers
     */
    protected function members_to_show(int $userid, array $rows): array {
        global $DB;

        $members = [];
        $toresolve = [];

        foreach ($rows as $row) {
            $conversationid = (int) $row->id;
            $author = is_null($row->useridfrom) ? null : (int) $row->useridfrom;

            if ($row->conversationtype == api::MESSAGE_CONVERSATION_TYPE_SELF) {
                $members[$conversationid] = [$userid];
            } else if ($row->conversationtype == api::MESSAGE_CONVERSATION_TYPE_GROUP) {
                $members[$conversationid] = is_null($author) ? [] : [$author];
            } else if (!is_null($author) && $author != $userid) {
                $members[$conversationid] = [$author];
            } else {
                // Individual conversation whose last message is the reader's own, or without
                // messages: the other user is looked up among the members.
                $members[$conversationid] = [];
                $toresolve[] = $conversationid;
            }
        }

        if (!empty($toresolve)) {
            list ($insql, $inparams) = $DB->get_in_or_equal($toresolve, SQL_PARAMS_NAMED, 'convid');
            $sql = "SELECT mcm.id, mcm.conversationid, mcm.userid
                      FROM {message_conversation_members} mcm
                     WHERE mcm.conversationid $insql
                       AND mcm.userid != :userid
                  ORDER BY mcm.id";

            $found = $DB->get_records_sql($sql, array_merge($inparams, ['userid' => $userid]));
            foreach ($found as $member) {
                $members[(int) $member->conversationid][] = (int) $member->userid;
            }
        }

        return $members;
    }

    /**
     * Counts the messages ever written in the reader's personal conversation.
     *
     * @param int $userid
     * @return int
     */
    protected function count_self_conversation_messages(int $userid): int {
        global $DB;

        return $DB->count_records_sql(
            "SELECT COUNT(m.id)
               FROM {messages} m
         INNER JOIN {message_conversations} mc
                 ON mc.id = m.conversationid
              WHERE mc.type = ? AND convhash = ?",
            [api::MESSAGE_CONVERSATION_TYPE_SELF, helper::get_conversation_hash([$userid])]
        );
    }

    #[\Override]
    public function search_message_rows(int $userid, string $search, int $limitfrom = 0,
            int $limitnum = 0): array {
        global $DB;

        // The unique column keeps the rows distinct: the same message appears once per member.
        $uniqueidsql = $DB->sql_concat('m.id', "'_'", 'm.useridfrom', "'_'", 'mcm.userid');

        // Tie-break on the three columns, which together are unique.

        $sql = "SELECT $uniqueidsql AS uniqueid, m.id, m.useridfrom, mcm.userid as useridto, m.subject,
                       m.fullmessage, m.fullmessagehtml, m.fullmessageformat, m.smallmessage,
                       m.conversationid, m.timecreated,
                       mub.id as userfromblocked, mub2.id as usertoblocked
                  FROM (
                        SELECT m2.id AS id
                          FROM {messages} m2
                         WHERE m2.useridfrom = ?
                         UNION
                        SELECT m3.id AS id
                          FROM {message_conversation_members} mcm3
                    INNER JOIN {messages} m3 ON mcm3.conversationid = m3.conversationid
                         WHERE mcm3.userid = ?
                       ) der
            INNER JOIN {messages} m
                    ON der.id = m.id
            INNER JOIN {user} u
                    ON u.id = m.useridfrom
            INNER JOIN {message_conversations} mc
                    ON mc.id = m.conversationid
            INNER JOIN {message_conversation_members} mcm
                    ON mcm.conversationid = m.conversationid
            INNER JOIN {user} u2
                    ON u2.id = mcm.userid
             LEFT JOIN {message_users_blocked} mub
                    ON (mub.blockeduserid = u.id AND mub.userid = ?)
             LEFT JOIN {message_users_blocked} mub2
                    ON (mub2.blockeduserid = u2.id AND mub2.userid = ?)
             LEFT JOIN {message_user_actions} mua
                    ON (mua.messageid = m.id AND mua.userid = ? AND mua.action = ?)
                 WHERE (m.useridfrom = ? OR mcm.userid = ?)
                   AND (m.useridfrom != mcm.userid OR mc.type = ?)
                   AND u.deleted = 0
                   AND u2.deleted = 0
                   AND mua.id is NULL
                   AND " . $DB->sql_like('smallmessage', '?', false) . "
              ORDER BY timecreated DESC, id DESC, useridto ASC";

        $params = [$userid, $userid, $userid, $userid, $userid, api::MESSAGE_ACTION_DELETED, $userid,
            $userid, api::MESSAGE_CONVERSATION_TYPE_SELF, '%' . $search . '%'];

        $rows = array_values($DB->get_records_sql($sql, $params, $limitfrom, $limitnum));

        // The unique column is dropped from the result.
        foreach ($rows as $row) {
            unset($row->uniqueid);
        }

        return $rows;
    }

    #[\Override]
    public function get_message_records(array $messageids): array {
        global $DB;

        if (empty($messageids)) {
            return [];
        }

        return $DB->get_records_list('messages', 'id', $messageids, '',
            'id, conversationid, useridfrom, smallmessage, fullmessageformat, fullmessagetrust, timecreated');
    }

    #[\Override]
    public function count_unread_in_conversation(int $userid, int $conversationid): int {
        global $DB;

        $sql = "SELECT count(m.id)
                  FROM {messages} m
            INNER JOIN {message_conversations} mc
                    ON mc.id = m.conversationid
             LEFT JOIN {message_user_actions} mua
                    ON (mua.messageid = m.id AND mua.userid = ? AND
                        (mua.action = ? OR mua.action = ?))
                 WHERE m.conversationid = ?
                   AND m.useridfrom != ?
                   AND mua.id is NULL";

        return $DB->count_records_sql($sql, [$userid, api::MESSAGE_ACTION_READ,
            api::MESSAGE_ACTION_DELETED, $conversationid, $userid]);
    }

    #[\Override]
    public function get_countable_conversation_types(int $userid): array {
        global $DB;

        $sql = "SELECT mc.id, mc.type, maxvisibleconvmessage.convid as maxconvid
                  FROM {message_conversations} mc
            INNER JOIN {message_conversation_members} mcm
                    ON mcm.conversationid = mc.id
             LEFT JOIN (
                              SELECT m.conversationid as convid
                                FROM {messages} m
                          INNER JOIN {message_conversation_members} mcmvisible
                                  ON mcmvisible.conversationid = m.conversationid
                           LEFT JOIN {message_user_actions} mua
                                  ON (mua.messageid = m.id AND mua.userid = :userid AND
                                      mua.action = :action)
                               WHERE mua.id is NULL
                                 AND mcmvisible.userid = :userid2
                            GROUP BY m.conversationid
                       ) maxvisibleconvmessage
                    ON maxvisibleconvmessage.convid = mc.id
                 WHERE mcm.userid = :userid3
                   AND mc.enabled = :enabled
                   AND (
                          (mc.type = :individualtype AND maxvisibleconvmessage.convid IS NOT NULL) OR
                          (mc.type = :grouptype) OR
                          (mc.type = :selftype)
                       )
              ORDER BY mc.type ASC, mc.id ASC";

        $rows = $DB->get_records_sql($sql, [
            'userid' => $userid,
            'userid2' => $userid,
            'userid3' => $userid,
            'action' => api::MESSAGE_ACTION_DELETED,
            'enabled' => api::MESSAGE_CONVERSATION_ENABLED,
            'individualtype' => api::MESSAGE_CONVERSATION_TYPE_INDIVIDUAL,
            'grouptype' => api::MESSAGE_CONVERSATION_TYPE_GROUP,
            'selftype' => api::MESSAGE_CONVERSATION_TYPE_SELF,
        ]);

        // The total tells an emptied self conversation (not counted) from one never used (counted).
        $selfmessagestotal = $DB->count_records_sql(
            "SELECT COUNT(m.id)
               FROM {messages} m
         INNER JOIN {message_conversations} mc
                 ON mc.id = m.conversationid
              WHERE mc.type = ? AND convhash = ?",
            [api::MESSAGE_CONVERSATION_TYPE_SELF, helper::get_conversation_hash([$userid])]
        );

        $types = [];
        foreach ($rows as $row) {
            if ($row->type == api::MESSAGE_CONVERSATION_TYPE_SELF && empty($row->maxconvid)
                    && $selfmessagestotal > 0) {
                continue;
            }
            $types[(int) $row->id] = (int) $row->type;
        }

        return $types;
    }

    #[\Override]
    public function get_unread_conversation_types(int $userid): array {
        global $DB;

        // The inner query counts conversations, not unread messages.
        $sql = "SELECT conv.id, conv.type
                  FROM {message_conversations} conv
            INNER JOIN (
                          SELECT m.conversationid
                            FROM {messages} m
                      INNER JOIN {message_conversations} mc
                              ON mc.id = m.conversationid
                      INNER JOIN {message_conversation_members} mcm
                              ON m.conversationid = mcm.conversationid
                       LEFT JOIN {message_user_actions} mua
                              ON (mua.messageid = m.id AND mua.userid = ? AND
                                 (mua.action = ? OR mua.action = ?))
                           WHERE mcm.userid = ?
                             AND m.useridfrom != ?
                             AND mua.id is NULL
                        GROUP BY m.conversationid
                       ) indcounts
                    ON indcounts.conversationid = conv.id
                 WHERE conv.enabled = 1";

        $rows = $DB->get_records_sql($sql, [$userid, api::MESSAGE_ACTION_READ,
            api::MESSAGE_ACTION_DELETED, $userid, $userid]);

        $types = [];
        foreach ($rows as $row) {
            $types[(int) $row->id] = (int) $row->type;
        }

        return $types;
    }

    #[\Override]
    public function get_blocked_user_ids(int $userid): array {
        global $DB;

        $sql = "SELECT blockeduserid
                  FROM {message_users_blocked}
                 WHERE userid = ?
              ORDER BY id ASC";

        return array_map('intval', array_keys($DB->get_records_sql($sql, [$userid])));
    }

    #[\Override]
    public function get_received_contact_request_user_ids(int $userid, int $limitfrom = 0,
            int $limitnum = 0): array {
        global $DB;

        $sql = "SELECT mcr.userid
                  FROM {message_contact_requests} mcr
             LEFT JOIN {message_users_blocked} mub
                    ON (mub.userid = ? AND mub.blockeduserid = mcr.userid)
                 WHERE mcr.requesteduserid = ?
                   AND mub.id is NULL
              ORDER BY mcr.timecreated ASC";
        $requests = $DB->get_records_sql($sql, [$userid, $userid], $limitfrom, $limitnum);

        return array_map('intval', array_keys($requests));
    }

    #[\Override]
    public function block_user(int $userid, int $usertoblockid): \stdClass {
        global $DB;

        $blocked = new \stdClass();
        $blocked->userid = $userid;
        $blocked->blockeduserid = $usertoblockid;
        $blocked->timecreated = time();
        $blocked->id = $DB->insert_record('message_users_blocked', $blocked);

        return $blocked;
    }

    #[\Override]
    public function unblock_user(int $userid, int $usertounblockid) {
        global $DB;

        if ($blockeduser = $DB->get_record('message_users_blocked',
                ['userid' => $userid, 'blockeduserid' => $usertounblockid])) {
            $DB->delete_records('message_users_blocked', ['id' => $blockeduser->id]);

            return $blockeduser;
        }

        return false;
    }

    #[\Override]
    public function is_blocked(int $userid, int $blockeduserid): bool {
        global $DB;

        return $DB->record_exists('message_users_blocked', ['userid' => $userid, 'blockeduserid' => $blockeduserid]);
    }

    #[\Override]
    public function create_contact_request(int $userid, int $requesteduserid): \stdClass {
        global $DB;

        $request = new \stdClass();
        $request->userid = $userid;
        $request->requesteduserid = $requesteduserid;
        $request->timecreated = time();

        $request->id = $DB->insert_record('message_contact_requests', $request);

        return $request;
    }

    #[\Override]
    public function confirm_contact_request(int $userid, int $requesteduserid) {
        global $DB;

        if ($request = $DB->get_record('message_contact_requests', ['userid' => $userid,
                'requesteduserid' => $requesteduserid])) {
            $contact = $this->add_contact($userid, $requesteduserid);

            $DB->delete_records('message_contact_requests', ['id' => $request->id]);

            return $contact;
        }

        return false;
    }

    #[\Override]
    public function decline_contact_request(int $userid, int $requesteduserid) {
        global $DB;

        if ($request = $DB->get_record('message_contact_requests', ['userid' => $userid,
                'requesteduserid' => $requesteduserid])) {
            $DB->delete_records('message_contact_requests', ['id' => $request->id]);
        }
    }

    #[\Override]
    public function get_contact_requests_between_users(int $userid, int $requesteduserid): array {
        global $DB;

        $sql = "SELECT *
                  FROM {message_contact_requests} mcr
                 WHERE (mcr.userid = ? AND mcr.requesteduserid = ?)
                    OR (mcr.userid = ? AND mcr.requesteduserid = ?)";
        return $DB->get_records_sql($sql, [$userid, $requesteduserid, $requesteduserid, $userid]);
    }

    #[\Override]
    public function does_contact_request_exist(int $userid, int $requesteduserid): bool {
        global $DB;

        $sql = "SELECT id
                  FROM {message_contact_requests} mcr
                 WHERE (mcr.userid = ? AND mcr.requesteduserid = ?)
                    OR (mcr.userid = ? AND mcr.requesteduserid = ?)";
        return $DB->record_exists_sql($sql, [$userid, $requesteduserid, $requesteduserid, $userid]);
    }

    #[\Override]
    public function get_received_contact_requests_count(int $userid): int {
        global $DB;

        $sql = "SELECT COUNT(mcr.id)
                  FROM {message_contact_requests} mcr
             LEFT JOIN {message_users_blocked} mub
                    ON mub.userid = mcr.requesteduserid AND mub.blockeduserid = mcr.userid
                 WHERE mcr.requesteduserid = :requesteduserid
                   AND mub.id IS NULL";
        $params = ['requesteduserid' => $userid];
        return $DB->count_records_sql($sql, $params);
    }

    /**
     * Checks if the sender can message the recipient.
     *
     * @param int $recipientid
     * @param int $senderid
     * @param bool $evenifblocked This lets the user know, that even if the recipient has blocked the
     *        user the user is still able to send a message
     * @return bool true if recipient hasn't blocked sender and sender can contact to recipient
     */
    protected function can_contact_user(int $recipientid, int $senderid, bool $evenifblocked = false): bool {
        if (has_capability('moodle/site:messageanyuser', \context_system::instance(), $senderid) ||
            $recipientid == $senderid) {
            // The sender has the ability to contact any user across the entire site or themselves.
            return true;
        }

        // The initial value of $cancontact is null to indicate that a value has not been
        // determined.
        $cancontact = null;

        if ($this->is_blocked($recipientid, $senderid) || $evenifblocked) {
            // The recipient has specifically blocked this sender.
            $cancontact = false;
        }

        $sharedcourses = null;
        if (null === $cancontact) {
            // There are three user preference options:
            // - Site: Allow anyone not explicitly blocked to contact me;
            // - Course members: Allow anyone I am in a course with to contact me; and
            // - Contacts: Only allow my contacts to contact me.
            //
            // The Site option is only possible when the messagingallusers site setting is also enabled.

            $privacypreference = api::get_user_privacy_messaging_preference($recipientid);
            if (api::MESSAGE_PRIVACY_SITE === $privacypreference) {
                // The user preference is to allow any user to contact them.
                // No need to check anything else.
                $cancontact = true;
            } else {
                // This user only allows their own contacts, and possibly course peers, to contact
                // them. If the users are contacts then we can avoid the more expensive shared
                // courses check.
                $cancontact = $this->is_contact($senderid, $recipientid);

                if (!$cancontact && api::MESSAGE_PRIVACY_COURSEMEMBER === $privacypreference) {
                    // The users are not contacts and the user allows course member messaging.
                    // Check whether these two users share any course together.
                    $sharedcourses = enrol_get_shared_courses($recipientid, $senderid, true);
                    $cancontact = (!empty($sharedcourses));
                }
            }
        }

        if (false === $cancontact) {
            // At the moment the users cannot contact one another. Check whether the messageanyuser
            // capability applies in any of the shared courses. This is intended to allow teachers
            // to message students regardless of message settings.

            // Note: You cannot use empty($sharedcourses) here because this may be an empty array.
            if (null === $sharedcourses) {
                $sharedcourses = enrol_get_shared_courses($recipientid, $senderid, true);
            }

            foreach ($sharedcourses as $course) {
                // Note: enrol_get_shared_courses will preload any shared context.
                if (has_capability('moodle/site:messageanyuser', \context_course::instance($course->id), $senderid)) {
                    $cancontact = true;
                    break;
                }
            }
        }

        return $cancontact;
    }
}
