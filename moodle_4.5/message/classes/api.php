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
 * Contains class used to return information to display for the message area.
 *
 * @package    core_message
 * @copyright  2016 Mark Nelson <markn@moodle.com>
 * @license    http://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace core_message;

use core_favourites\local\entity\favourite;
use core_message\conversation\provider_factory;
// The two contexts have a factory each, with the same short name: aliased.
use core_message\notification\provider_factory as notification_provider_factory;

defined('MOODLE_INTERNAL') || die();

require_once($CFG->dirroot . '/lib/messagelib.php');

/**
 * Class used to return information to display for the message area.
 *
 * @copyright  2016 Mark Nelson <markn@moodle.com>
 * @license    http://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class api {

    /**
     * The action for reading a message.
     */
    const MESSAGE_ACTION_READ = 1;

    /**
     * The action for deleting a message.
     */
    const MESSAGE_ACTION_DELETED = 2;

    /**
     * The action for reading a message.
     */
    const CONVERSATION_ACTION_MUTED = 1;

    /**
     * The privacy setting for being messaged by anyone within courses user is member of.
     */
    const MESSAGE_PRIVACY_COURSEMEMBER = 0;

    /**
     * The privacy setting for being messaged only by contacts.
     */
    const MESSAGE_PRIVACY_ONLYCONTACTS = 1;

    /**
     * The privacy setting for being messaged by anyone on the site.
     */
    const MESSAGE_PRIVACY_SITE = 2;

    /**
     * An individual conversation.
     */
    const MESSAGE_CONVERSATION_TYPE_INDIVIDUAL = 1;

    /**
     * A group conversation.
     */
    const MESSAGE_CONVERSATION_TYPE_GROUP = 2;

    /**
     * A self conversation.
     */
    const MESSAGE_CONVERSATION_TYPE_SELF = 3;

    /**
     * The state for an enabled conversation area.
     */
    const MESSAGE_CONVERSATION_ENABLED = 1;

    /**
     * The state for a disabled conversation area.
     */
    const MESSAGE_CONVERSATION_DISABLED = 0;

    /**
     * The max message length.
     */
    const MESSAGE_MAX_LENGTH = 4096;

    /**
     * Handles searching for messages in the message area.
     *
     * @param int $userid The user id doing the searching
     * @param string $search The string the user is searching
     * @param int $limitfrom
     * @param int $limitnum
     * @return array
     */
    public static function search_messages($userid, $search, $limitfrom = 0, $limitnum = 0) {
        global $DB;

        // Which messages match is asked of the provider; identifiers become people here. Deleted
        // accounts are excluded inside the query, as part of the paging.
        $rows = provider_factory::get($userid)->search_message_rows($userid, $search, $limitfrom,
            $limitnum);
        if (empty($rows)) {
            return [];
        }

        // The counterpart: the recipient when the reader wrote the message, the sender otherwise.
        $otherids = [];
        foreach ($rows as $row) {
            $otherids[] = ($userid == $row->useridfrom) ? $row->useridto : $row->useridfrom;
        }

        $userfieldsapi = \core_user\fields::for_userpic()->including('lastaccess');
        $userfields = $userfieldsapi->get_sql('u', false, '', '', false)->selects;
        list ($insql, $params) = $DB->get_in_or_equal(array_unique($otherids), SQL_PARAMS_NAMED, 'userid');
        $users = $DB->get_records_sql("SELECT $userfields
                                         FROM {user} u
                                        WHERE u.deleted = 0
                                          AND u.id $insql", $params);

        $conversations = [];
        foreach ($rows as $row) {
            $isown = ($userid == $row->useridfrom);
            $otherid = $isown ? $row->useridto : $row->useridfrom;
            if (!isset($users[$otherid])) {
                continue;
            }

            $message = clone $row;
            $prefix = $isown ? 'userto_' : 'userfrom_';
            // A message the reader wrote is read by definition, whether or not the recipient read
            // it.
            $message->isread = $isown ? true : 0;
            $message->blocked = ($isown ? $row->usertoblocked : $row->userfromblocked) ? 1 : 0;
            $message->messageid = $row->id;

            // The user fields are attached under the prefix create_contact() reads.
            foreach ((array) $users[$otherid] as $field => $value) {
                $prefixed = $prefix . $field;
                $message->$prefixed = $value;
            }

            // The same message arrives once per member: only the first occurrence becomes a result.
            if (!array_key_exists($message->messageid, $conversations)) {
                $conversations[$message->messageid] = helper::create_contact($message, $prefix);
            }
        }

        return array_values($conversations);
    }

    /**
     * @deprecated since 3.6
     */
    public static function search_users_in_course() {
        throw new \coding_exception('\core_message\api::search_users_in_course has been removed.');
    }

    /**
     * @deprecated since 3.6
     */
    public static function search_users() {
        throw new \coding_exception('\core_message\api::search_users has been removed.');
    }

    /**
     * Handles searching for user.
     *
     * @param int $userid The user id doing the searching
     * @param string $search The string the user is searching
     * @param int $limitfrom
     * @param int $limitnum
     * @return array
     */
    public static function message_search_users(int $userid, string $search, int $limitfrom = 0, int $limitnum = 20): array {
        global $CFG, $DB;

        // Check if messaging is enabled.
        if (empty($CFG->messaging)) {
            throw new \moodle_exception('disabled', 'message');
        }

        require_once($CFG->dirroot . '/user/lib.php');

        // Used to search for contacts.
        $fullname = $DB->sql_fullname();

        // Users not to include.
        $excludeusers = array($CFG->siteguest);
        if (!$selfconversation = self::get_self_conversation($userid)) {
            // Userid should only be excluded when she hasn't a self-conversation.
            $excludeusers[] = $userid;
        }
        list($exclude, $excludeparams) = $DB->get_in_or_equal($excludeusers, SQL_PARAMS_NAMED, 'param', false);

        $params = array('search' => '%' . $DB->sql_like_escape($search) . '%', 'userid1' => $userid, 'userid2' => $userid);

        // Which users are contacts is asked of the provider; the name match is applied here.
        $contactids = provider_factory::get($userid)->get_contact_ids($userid);

        $foundusers = [];
        if (!empty($contactids)) {
            list ($contactsql, $contactparams) = $DB->get_in_or_equal($contactids, SQL_PARAMS_NAMED,
                'contactid');
            $sql = "SELECT u.id
                      FROM {user} u
                     WHERE u.deleted = 0
                       AND u.confirmed = 1
                       AND " . $DB->sql_like($fullname, ':search', false) . "
                       AND u.id $exclude
                       AND u.id $contactsql
                  ORDER BY " . $DB->sql_fullname();
            $foundusers = $DB->get_records_sql_menu($sql, $params + $excludeparams + $contactparams,
                $limitfrom, $limitnum);
        }

        $contacts = [];
        if (!empty($foundusers)) {
            $contacts = helper::get_member_info($userid, array_keys($foundusers));
            foreach ($contacts as $memberuserid => $memberinfo) {
                $contacts[$memberuserid]->conversations = self::get_conversations_between_users($userid, $memberuserid, 0, 1000);
            }
        }

        // We need to get all the user details for a fullname in the visibility checks.
        $namefields = \core_user\fields::for_name()
            // Required by the visibility checks.
            ->including('deleted');

        // Let's get those non-contacts. Because we can't achieve all the required visibility checks
        // in SQL, we'll iterate through the non-contact records and stop once we have enough
        // matching the 'visible' criteria.

        // Use a local generator to achieve this iteration.
        $getnoncontactusers = function ($limitfrom = 0, $limitnum = 0) use (
            $fullname,
            $exclude,
            $params,
            $excludeparams,
            $userid,
            $selfconversation,
            $namefields,
            // Captured explicitly: the closure filters contacts out.
            $contactids
        ) {
            global $DB, $CFG;

            $joinenrolled = '';
            $enrolled = '';
            $unionself = '';
            $enrolledparams = [];

            // Since we want to order a UNION we need to list out all the user fields individually
            // this will allow us to reference the fullname correctly.
            $userfields = $namefields->get_sql('u')->selects;

            $select = "u.id, " . $DB->sql_fullname() . " AS sortingname" . $userfields;

            // When messageallusers is false valid non-contacts must be enrolled on one of the users
            // courses.
            if (empty($CFG->messagingallusers)) {
                $joinenrolled = "JOIN {user_enrolments} ue ON ue.userid = u.id
                                 JOIN {enrol} e ON e.id = ue.enrolid";
                $enrolled = "AND e.courseid IN (
                                SELECT e.courseid
                                  FROM {user_enrolments} ue
                                  JOIN {enrol} e ON e.id = ue.enrolid
                                 WHERE ue.userid = :enroluserid
                                )";

                if ($selfconversation !== false) {
                    // We must include the user themselves, when they have a self conversation, even
                    // if they are not enrolled on any courses.
                    $unionself = "UNION SELECT u.id FROM {user} u
                                         WHERE u.id = :self AND ". $DB->sql_like($fullname, ':selfsearch', false);
                }
                $enrolledparams = ['enroluserid' => $userid, 'self' => $userid, 'selfsearch' => $params['search']];
            }

            // Contacts are excluded by a membership test; with no contacts the fragment stays
            // empty.
            $noncontactsql = '';
            $noncontactparams = [];
            if (!empty($contactids)) {
                list ($insql, $noncontactparams) = $DB->get_in_or_equal($contactids,
                    SQL_PARAMS_NAMED, 'noncontactid', false);
                $noncontactsql = " AND u.id $insql ";
            }

            $sql = "SELECT $select
                      FROM (
                        SELECT DISTINCT u.id
                          FROM {user} u $joinenrolled
                         WHERE u.deleted = 0
                           AND u.confirmed = 1
                           AND " . $DB->sql_like($fullname, ':search', false) . "
                           AND u.id $exclude $enrolled
                           $noncontactsql $unionself
                         ) targetedusers
                      JOIN {user} u ON u.id = targetedusers.id
                  ORDER BY 2";
            while ($records = $DB->get_records_sql($sql, $params + $excludeparams + $enrolledparams + $noncontactparams, $limitfrom, $limitnum)) {
                yield $records;
                $limitfrom += $limitnum;
            }
        };

        // Fetch in batches of $limitnum * 2 to improve the chances of matching a user without going
        // back to the DB. The generator cannot function without a sensible limiter, so set one if
        // this is not set.
        $batchlimit = ($limitnum == 0) ? 20 : $limitnum;

        // We need to make the offset param work with the generator. Basically, if we want to get
        // say 10 records starting at the 40th record, we need to see 50 records and return only
        // those after the 40th record. We can never pass the method's offset param to the generator
        // as we need to manage the position within those valid records ourselves. See MDL-63983
        // dealing with performance improvements to this area of code.
        $noofvalidseenrecords = 0;
        $returnedusers = [];

        // Only fields that are also part of user_get_default_fields() are valid when passed into
        // user_get_user_details().
        $fields = array_intersect($namefields->get_required_fields(), user_get_default_fields());

        foreach ($getnoncontactusers(0, $batchlimit) as $users) {
            foreach ($users as $id => $user) {
                // User visibility checks: only return users who are visible to the user performing the search.
                // Which visibility check to use depends on the 'messagingallusers' (site wide messaging) setting:
                // - If enabled, return matched users whose profiles are visible to the current user anywhere (site or course).
                // - If disabled, only return matched users whose course profiles are visible to the current user.
                $userdetails = \core_message\helper::search_get_user_details($user, $fields);

                // Return the user only if the searched field is returned.
                // Otherwise it means that the $USER was not allowed to search the returned user.
                if (!empty($userdetails) and !empty($userdetails['fullname'])) {
                    // We know we've matched, but only save the record if it's within the offset
                    // area we need.
                    if ($limitfrom == 0) {
                        // No offset specified, so just save.
                        $returnedusers[$id] = $user;
                    } else {
                        // There is an offset in play. If we've passed enough records already (>
                        // offset value), then we can save this one.
                        if ($noofvalidseenrecords >= $limitfrom) {
                            $returnedusers[$id] = $user;
                        }
                    }
                    if (count($returnedusers) == $limitnum) {
                        break 2;
                    }
                    $noofvalidseenrecords++;
                }
            }
        }
        $foundusers = $returnedusers;

        $noncontacts = [];
        if (!empty($foundusers)) {
            $noncontacts = helper::get_member_info($userid, array_keys($foundusers));
            foreach ($noncontacts as $memberuserid => $memberinfo) {
                if ($memberuserid !== $userid) {
                    $noncontacts[$memberuserid]->conversations = self::get_conversations_between_users($userid, $memberuserid, 0,
                        1000);
                } else {
                    $noncontacts[$memberuserid]->conversations[$selfconversation->id] = $selfconversation;
                }
            }
        }

        return array(array_values($contacts), array_values($noncontacts));
    }

    /**
     * Gets extra fields, like image url and subname for any conversations linked to components.
     *
     * The subname is like a subtitle for the conversation, to compliment it's name.
     * The imageurl is the location of the image for the conversation, as might be seen on a listing of conversations for a user.
     *
     * @param array $conversations a list of conversations records.
     * @return array the array of subnames, index by conversation id.
     * @throws \coding_exception
     * @throws \dml_exception
     */
    public static function get_linked_conversation_extra_fields(array $conversations): array {
        global $DB, $PAGE;

        $renderer = $PAGE->get_renderer('core');

        $linkedconversations = [];
        foreach ($conversations as $conversation) {
            if (!is_null($conversation->component) && !is_null($conversation->itemtype)) {
                $linkedconversations[$conversation->component][$conversation->itemtype][$conversation->id]
                    = $conversation->itemid;
            }
        }
        if (empty($linkedconversations)) {
            return [];
        }

        // TODO: MDL-63814: Working out the subname for linked conversations should be done in a
        // generic way. Get the itemid, but only for course group linked conversation for now.
        $extrafields = [];
        if (!empty($linkeditems = $linkedconversations['core_group']['groups'])) { // Format: [conversationid => itemid].
            // Get the name of the course to which the group belongs.
            list ($groupidsql, $groupidparams) = $DB->get_in_or_equal(array_values($linkeditems), SQL_PARAMS_NAMED, 'groupid');
            $sql = "SELECT g.*, c.shortname as courseshortname
                      FROM {groups} g
                      JOIN {course} c
                        ON g.courseid = c.id
                     WHERE g.id $groupidsql";
            $courseinfo = $DB->get_records_sql($sql, $groupidparams);
            foreach ($linkeditems as $convid => $groupid) {
                if (array_key_exists($groupid, $courseinfo)) {
                    $group = $courseinfo[$groupid];
                    // Subname.
                    $extrafields[$convid]['subname'] = format_string($courseinfo[$groupid]->courseshortname);

                    // Imageurl.
                    $extrafields[$convid]['imageurl'] = $renderer->image_url('g/g1')->out(false); // default image.
                    if ($url = get_group_picture_url($group, $group->courseid, true)) {
                        $extrafields[$convid]['imageurl'] = $url->out(false);
                    }
                }
            }
        }
        return $extrafields;
    }


    /**
     * Returns the contacts and their conversation to display in the contacts area.
     *
     * ** WARNING **
     * It is HIGHLY recommended to use a sensible limit when calling this function. Trying
     * to retrieve too much information in a single call will cause performance problems.
     * ** WARNING **
     *
     * This function has specifically been altered to break each of the data sets it
     * requires into separate database calls. This is to avoid the performance problems
     * observed when attempting to join large data sets (e.g. the message tables and
     * the user table).
     *
     * While it is possible to gather the data in a single query, and it may even be
     * more efficient with a correctly tuned database, we have opted to trade off some of
     * the benefits of a single query in order to ensure this function will work on
     * most databases with default tunings and with large data sets.
     *
     * @param int $userid The user id
     * @param int $limitfrom
     * @param int $limitnum
     * @param int $type the type of the conversation, if you wish to filter to a certain type (see api constants).
     * @param bool $favourites whether to include NO favourites (false) or ONLY favourites (true), or null to ignore this setting.
     * @param bool $mergeself whether to include self-conversations (true) or ONLY private conversations (false)
     *             when private conversations are requested.
     * @return array the array of conversations
     * @throws \moodle_exception
     */
    public static function get_conversations($userid, $limitfrom = 0, $limitnum = 20, ?int $type = null,
            ?bool $favourites = null, bool $mergeself = false) {

        if (!is_null($type) && !in_array($type, [self::MESSAGE_CONVERSATION_TYPE_INDIVIDUAL,
                self::MESSAGE_CONVERSATION_TYPE_GROUP, self::MESSAGE_CONVERSATION_TYPE_SELF])) {
            throw new \moodle_exception("Invalid value ($type) for type param, please see api constants.");
        }

        self::lazy_create_self_conversation($userid);

        // Favourites (core_favourites) are handed to the provider as identifiers and a direction,
        // so that the filter is applied inside the query.
        $service = \core_favourites\service_factory::get_service_for_user_context(\context_user::instance($userid));
        $favouriteconversations = $service->find_favourites_by_type('core_message', 'message_conversations');
        $favouriteconversationids = array_column($favouriteconversations, 'itemid');
        if ($favourites && empty($favouriteconversationids)) {
            return []; // Only favourites were asked for and there are none: nothing more to do.
        }

        $summaries = provider_factory::get($userid)->get_conversation_summaries($userid, $limitfrom,
            $limitnum, $type, $mergeself, $favouriteconversationids, $favourites);
        if (empty($summaries)) {
            return [];
        }

        $conversations = [];
        foreach ($summaries as $summary) {
            $conversations[$summary->id] = $summary;
        }

        // Component-linked conversation fields (subtitle and image) stay here.
        $convextrafields = self::get_linked_conversation_extra_fields($conversations);

        // The provider has chosen the members to show; user records, contact requests and privacy
        // preferences are attached here.
        $members = [];
        $individualmembers = [];
        $groupmembers = [];
        $selfmembers = [];
        foreach ($conversations as $conversation) {
            $members[$conversation->id] = [];
            foreach ($conversation->memberids as $memberid) {
                $members[$conversation->id][$memberid] = $memberid;

                if ($conversation->type == self::MESSAGE_CONVERSATION_TYPE_INDIVIDUAL) {
                    $individualmembers[$memberid] = $memberid;
                } else if ($conversation->type == self::MESSAGE_CONVERSATION_TYPE_GROUP) {
                    $groupmembers[$memberid] = $memberid;
                } else if ($conversation->type == self::MESSAGE_CONVERSATION_TYPE_SELF) {
                    $selfmembers[$memberid] = $memberid;
                }
            }
        }

        if (!empty($individualmembers) || !empty($groupmembers) || !empty($selfmembers)) {
            // Contact requests and privacy information are needed for individual conversations
            // only.
            $diffgroupmembers = array_diff($groupmembers, $individualmembers);

            $individualmemberinfo = helper::get_member_info($userid, $individualmembers, true, true);
            $groupmemberinfo = helper::get_member_info($userid, $diffgroupmembers);
            $selfmemberinfo = helper::get_member_info($userid, $selfmembers);

            // Not array_merge, which would renumber the keys we depend on.
            $memberinfo = $individualmemberinfo + $groupmemberinfo + $selfmemberinfo;

            if (empty($memberinfo)) {
                return [];
            }

            foreach ($members as $convid => $memberarr) {
                foreach ($memberarr as $key => $memberid) {
                    if (array_key_exists($memberid, $memberinfo)) {
                        $members[$convid][$key] = clone $memberinfo[$memberid];

                        if ($conversations[$convid]->type == self::MESSAGE_CONVERSATION_TYPE_GROUP) {
                            // Data a group conversation has no use for.
                            $members[$convid][$key]->requirescontact = null;
                            $members[$convid][$key]->canmessage = null;
                            $members[$convid][$key]->contactrequests = [];
                        }
                    } else {
                        // An individual conversation whose member cannot be fetched leaves the
                        // list.
                        unset($members[$convid][$key]);

                        if ($conversations[$convid]->type == self::MESSAGE_CONVERSATION_TYPE_INDIVIDUAL) {
                            unset($conversations[$convid]);
                        }
                    }
                }
            }
        }

        // Preloading the contexts warms the cache.
        \context_helper::preload_contexts_by_id(array_column($conversations, 'contextid'));

        $arrconversations = [];
        foreach ($conversations as $conversation) {
            $conv = new \stdClass();
            $conv->id = $conversation->id;

            // The name is formatted in the context the conversation resides in. Without one, the
            // context is the user's own.
            if (is_null($conversation->contextid)) {
                $convcontext = \context_user::instance($userid);
                $contexttodeletemessageforall = \context_system::instance();
            } else {
                $convcontext = \context::instance_by_id($conversation->contextid);
                $contexttodeletemessageforall = $convcontext;
            }
            $conv->name = format_string($conversation->name, true, ['context' => $convcontext]);

            $conv->subname = $convextrafields[$conv->id]['subname'] ?? null;
            $conv->imageurl = $convextrafields[$conv->id]['imageurl'] ?? null;
            $conv->type = $conversation->type;
            $conv->membercount = $conversation->membercount;
            $conv->isfavourite = in_array($conv->id, $favouriteconversationids);
            $conv->isread = $conversation->unreadcount == 0;
            // Absent unread messages the field is null and not zero, as it has always been.
            $conv->unreadcount = $conversation->unreadcount > 0 ? $conversation->unreadcount : null;
            $conv->ismuted = $conversation->ismuted;
            $conv->members = $members[$conv->id];

            $conv->messages = [];
            $conv->candeletemessagesforallusers = has_capability('moodle/site:deleteanymessage',
                $contexttodeletemessageforall);

            if (!is_null($conversation->lastmessage) && strval($conversation->lastmessage->smallmessage) !== '') {
                $msg = new \stdClass();
                $msg->id = $conversation->lastmessage->id;
                $msg->text = message_format_message_text($conversation->lastmessage);
                $msg->useridfrom = $conversation->lastmessage->useridfrom;
                $msg->timecreated = $conversation->lastmessage->timecreated;
                $conv->messages[] = $msg;
            }

            $arrconversations[] = $conv;
        }

        return $arrconversations;
    }

    /**
     * Returns all conversations between two users
     *
     * @param int $userid1 One of the user's id
     * @param int $userid2 The other user's id
     * @param int $limitfrom
     * @param int $limitnum
     * @return array
     * @throws \dml_exception
     */
    public static function get_conversations_between_users(int $userid1, int $userid2,
                                                           int $limitfrom = 0, int $limitnum = 20): array {

        return provider_factory::get($userid1)->get_conversations_between_users($userid1, $userid2, $limitfrom,
            $limitnum);
    }

    /**
     * Return a conversation.
     *
     * @param int $userid The user id to get the conversation for
     * @param int $conversationid The id of the conversation to fetch
     * @param bool $includecontactrequests Should contact requests be included between members
     * @param bool $includeprivacyinfo Should privacy info be included between members
     * @param int $memberlimit Limit number of members to load
     * @param int $memberoffset Offset members by this amount
     * @param int $messagelimit Limit number of messages to load
     * @param int $messageoffset Offset the messages
     * @param bool $newestmessagesfirst Order messages by newest first
     * @return \stdClass
     */
    public static function get_conversation(
        int $userid,
        int $conversationid,
        bool $includecontactrequests = false,
        bool $includeprivacyinfo = false,
        int $memberlimit = 0,
        int $memberoffset = 0,
        int $messagelimit = 0,
        int $messageoffset = 0,
        bool $newestmessagesfirst = true
    ) {
        global $USER;

        $systemcontext = \context_system::instance();
        $canreadallmessages = has_capability('moodle/site:readallmessages', $systemcontext);
        if (($USER->id != $userid) && !$canreadallmessages) {
            throw new \moodle_exception('You do not have permission to perform this action.');
        }

        $conversation = provider_factory::get($userid)->get_conversation_by_id($conversationid);
        if (!$conversation) {
            return null;
        }

        // Get the context of the conversation. This will be used to check whether the conversation
        // is a favourite. This will be either 'user' (for individual conversations) or, in the case
        // of linked conversations, the context stored in the record.
        $userctx = \context_user::instance($userid);
        $conversationctx = empty($conversation->contextid) ? $userctx : \context::instance_by_id($conversation->contextid);

        $isconversationmember = self::is_user_in_conversation($userid, $conversationid);

        if (!$isconversationmember && !$canreadallmessages) {
            throw new \moodle_exception('You do not have permission to view this conversation.');
        }

        $members = self::get_conversation_members(
            $userid,
            $conversationid,
            $includecontactrequests,
            $includeprivacyinfo,
            $memberoffset,
            $memberlimit
        );
        if ($conversation->type != self::MESSAGE_CONVERSATION_TYPE_SELF) {
            // Strip out the requesting user to match what get_conversations does, except for self-
            // conversations.
            $members = array_filter($members, function($member) use ($userid) {
                return $member->id != $userid;
            });
        }

        $messages = self::get_conversation_messages(
            $userid,
            $conversationid,
            $messageoffset,
            $messagelimit,
            $newestmessagesfirst ? 'timecreated DESC' : 'timecreated ASC'
        );

        $service = \core_favourites\service_factory::get_service_for_user_context(\context_user::instance($userid));
        $isfavourite = $service->favourite_exists('core_message', 'message_conversations', $conversationid, $conversationctx);

        $convextrafields = self::get_linked_conversation_extra_fields([$conversation]);
        $subname = isset($convextrafields[$conversationid]) ? $convextrafields[$conversationid]['subname'] : null;
        $imageurl = isset($convextrafields[$conversationid]) ? $convextrafields[$conversationid]['imageurl'] : null;

        // Three facts through the abstraction; favourite flag, linked-component fields and
        // capability check stay here.
        $provider = provider_factory::get($userid);
        $unreadcount = $provider->count_unread_in_conversation($userid, $conversationid);
        $membercount = self::count_conversation_members($conversationid);
        $ismuted = self::is_conversation_muted($userid, $conversationid);

        // Get the context of the conversation. This will be used to check if the user can delete
        // all messages in the conversation.
        $deleteallcontext = empty($conversation->contextid) ? $systemcontext : \context::instance_by_id($conversation->contextid);

        return (object) [
            'id' => $conversation->id,
            'name' => $conversation->name,
            'subname' => $subname,
            'imageurl' => $imageurl,
            'type' => $conversation->type,
            'membercount' => $membercount,
            'isfavourite' => $isfavourite,
            'isread' => empty($unreadcount),
            'unreadcount' => $unreadcount,
            'ismuted' => $ismuted,
            'members' => $members,
            'messages' => $messages['messages'],
            'candeletemessagesforallusers' => has_capability('moodle/site:deleteanymessage', $deleteallcontext)
        ];
    }

    /**
     * Mark a conversation as a favourite for the given user.
     *
     * @param int $conversationid the id of the conversation to mark as a favourite.
     * @param int $userid the id of the user to whom the favourite belongs.
     * @return favourite the favourite object.
     * @throws \moodle_exception if the user or conversation don't exist.
     */
    public static function set_favourite_conversation(int $conversationid, int $userid): favourite {
        if (!self::is_user_in_conversation($userid, $conversationid)) {
            throw new \moodle_exception("Conversation doesn't exist or user is not a member");
        }

        // Read through the abstraction. The favourite itself belongs to core_favourites.
        $conversation = provider_factory::get($userid)->get_conversation_by_id($conversationid);
        $userctx = \context_user::instance($userid);
        if (empty($conversation->contextid)) {
            // When the conversation hasn't any contextid value defined, the favourite will be added
            // to the user context.
            $conversationctx = $userctx;
        } else {
            // If the contextid is defined, the favourite will be added there.
            $conversationctx = \context::instance_by_id($conversation->contextid);
        }

        $ufservice = \core_favourites\service_factory::get_service_for_user_context($userctx);

        if ($favourite = $ufservice->get_favourite('core_message', 'message_conversations', $conversationid, $conversationctx)) {
            return $favourite;
        } else {
            return $ufservice->create_favourite('core_message', 'message_conversations', $conversationid, $conversationctx);
        }
    }

    /**
     * Unset a conversation as a favourite for the given user.
     *
     * @param int $conversationid the id of the conversation to unset as a favourite.
     * @param int $userid the id to whom the favourite belongs.
     * @throws \moodle_exception if the favourite does not exist for the user.
     */
    public static function unset_favourite_conversation(int $conversationid, int $userid) {
        // Read through the abstraction.
        $conversation = provider_factory::get($userid)->get_conversation_by_id($conversationid);
        $userctx = \context_user::instance($userid);
        if (empty($conversation->contextid)) {
            // When the conversation hasn't any contextid value defined, the favourite will be added
            // to the user context.
            $conversationctx = $userctx;
        } else {
            // If the contextid is defined, the favourite will be added there.
            $conversationctx = \context::instance_by_id($conversation->contextid);
        }

        $ufservice = \core_favourites\service_factory::get_service_for_user_context($userctx);
        $ufservice->delete_favourite('core_message', 'message_conversations', $conversationid, $conversationctx);
    }

    /**
     * @deprecated since 3.6
     */
    public static function get_contacts() {
        throw new \coding_exception('\core_message\api::get_contacts has been removed.');
    }

    /**
     * Get the contacts for a given user.
     *
     * @param int $userid
     * @param int $limitfrom
     * @param int $limitnum
     * @return array An array of contacts
     */
    public static function get_user_contacts(int $userid, int $limitfrom = 0, int $limitnum = 0) {
        // Which users are contacts is asked of the provider; the user records are attached here.
        $userids = provider_factory::get($userid)->get_contact_ids($userid, $limitfrom, $limitnum);
        if (empty($userids)) {
            return [];
        }

        return helper::get_member_info($userid, $userids);
    }

    /**
     * Returns the contacts count.
     *
     * @param int $userid The user id
     * @return array
     */
    public static function count_contacts(int $userid): int {
        return provider_factory::get($userid)->count_contacts($userid);
    }

    /**
     * @deprecated since 3.10
     */
    public static function get_contacts_with_unread_message_count() {
        throw new \coding_exception('\core_message\api::get_contacts_with_unread_message_count has been removed.');
    }

    /**
     * @deprecated since 3.10
     */
    public static function get_non_contacts_with_unread_message_count() {
        throw new \coding_exception('\core_message\api::get_non_contacts_with_unread_message_count has been removed.');
    }

    /**
     * @deprecated since 3.6
     */
    public static function get_messages() {
        throw new \coding_exception('\core_message\api::get_messages has been removed.');
    }

    /**
     * Returns the messages for the defined conversation.
     *
     * @param  int $userid The current user.
     * @param  int $convid The conversation where the messages belong. Could be an object or just the id.
     * @param  int $limitfrom Return a subset of records, starting at this point (optional).
     * @param  int $limitnum Return a subset comprising this many records in total (optional, required if $limitfrom is set).
     * @param  string $sort The column name to order by including optionally direction.
     * @param  int $timefrom The time from the message being sent.
     * @param  int $timeto The time up until the message being sent.
     * @return array of messages
     */
    public static function get_conversation_messages(int $userid, int $convid, int $limitfrom = 0, int $limitnum = 0,
        string $sort = 'timecreated ASC', int $timefrom = 0, int $timeto = 0): array {

        if (!empty($timefrom)) {
            // Check the cache to see if we even need to do a DB query.
            $cache = \cache::make('core', 'message_time_last_message_between_users');
            $key = helper::get_last_message_time_created_cache_key($convid);
            $lastcreated = $cache->get($key);

            // The last known message time is earlier than the one being requested so we can
            // just return an empty result set rather than having to query the DB.
            if ($lastcreated && $lastcreated < $timefrom) {
                return helper::format_conversation_messages($userid, $convid, []);
            }
        }

        $messages = provider_factory::get($userid)->get_conversation_message_records($userid, $convid, 0, $limitfrom,
            $limitnum, $sort, $timefrom, $timeto);
        return helper::format_conversation_messages($userid, $convid, $messages);
    }

    /**
     * @deprecated since 3.6
     */
    public static function get_most_recent_message() {
        throw new \coding_exception('\core_message\api::get_most_recent_message has been removed.');
    }

    /**
     * Returns the most recent message in a conversation.
     *
     * @param int $convid The conversation identifier.
     * @param int $currentuserid The current user identifier.
     * @return \stdClass|null The most recent message.
     */
    public static function get_most_recent_conversation_message(int $convid, int $currentuserid = 0) {
        global $USER;

        if (empty($currentuserid)) {
            $currentuserid = $USER->id;
        }

        $provider = provider_factory::get($currentuserid);
        if ($messages = $provider->get_conversation_message_records($currentuserid, $convid, 0, 0, 1,
                'timecreated DESC')) {
            $convmessages = helper::format_conversation_messages($currentuserid, $convid, $messages);
            return array_pop($convmessages['messages']);
        }

        return null;
    }

    /**
     * @deprecated since 3.6
     */
    public static function get_profile() {
        throw new \coding_exception('\core_message\api::get_profile has been removed.');
    }

    /**
     * Checks if a user can delete messages they have either received or sent.
     *
     * @param int $userid The user id of who we want to delete the messages for (this may be done by the admin
     *  but will still seem as if it was by the user)
     * @param int $conversationid The id of the conversation
     * @return bool Returns true if a user can delete the conversation, false otherwise.
     */
    public static function can_delete_conversation(int $userid, ?int $conversationid = null): bool {
        global $USER;

        if (is_null($conversationid)) {
            debugging('\core_message\api::can_delete_conversation() now expects a \'conversationid\' to be passed.',
                DEBUG_DEVELOPER);
            return false;
        }

        $systemcontext = \context_system::instance();

        if (has_capability('moodle/site:deleteanymessage', $systemcontext)) {
            return true;
        }

        if (!provider_factory::get($userid)->is_user_in_conversation($userid, $conversationid)) {
            return false;
        }

        if (has_capability('moodle/site:deleteownmessage', $systemcontext) &&
                $USER->id == $userid) {
            return true;
        }

        return false;
    }

    /**
     * @deprecated since 3.6
     */
    public static function delete_conversation() {
        throw new \coding_exception('\core_message\api::delete_conversation() is deprecated, please use ' .
            '\core_message\api::delete_conversation_by_id() instead.');
    }

    /**
     * Deletes a conversation for a specified user.
     *
     * This function does not verify any permissions.
     *
     * @param int $userid The user id of who we want to delete the messages for (this may be done by the admin
     *  but will still seem as if it was by the user)
     * @param int $conversationid The id of the other user in the conversation
     */
    public static function delete_conversation_by_id(int $userid, int $conversationid) {
        global $USER;

        $deleted = provider_factory::get($userid)->delete_conversation_by_id($userid, $conversationid);

        foreach ($deleted as $deletion) {
            \core\event\message_deleted::create_from_ids($userid, $USER->id,
                $deletion->messageid, $deletion->actionid)->trigger();
        }
    }

    /**
     * Returns the count of unread conversations (collection of messages from a single user) for
     * the given user.
     *
     * @param \stdClass $user the user who's conversations should be counted
     * @return int the count of the user's unread conversations
     */
    public static function count_unread_conversations($user = null) {
        global $USER;

        if (empty($user)) {
            $user = $USER;
        }

        return provider_factory::get($user->id)->count_unread_conversations((int) $user->id);
    }

    /**
     * Checks if a user can mark all messages as read.
     *
     * @param int $userid The user id of who we want to mark the messages for
     * @param int $conversationid The id of the conversation
     * @return bool true if user is permitted, false otherwise
     * @since 3.6
     */
    public static function can_mark_all_messages_as_read(int $userid, int $conversationid): bool {
        global $USER;

        $systemcontext = \context_system::instance();

        if (has_capability('moodle/site:readallmessages', $systemcontext)) {
            return true;
        }

        if (!provider_factory::get($userid)->is_user_in_conversation($userid, $conversationid)) {
            return false;
        }

        if ($USER->id == $userid) {
            return true;
        }

        return false;
    }

    /**
     * Returns the count of conversations (collection of messages from a single user) for
     * the given user.
     *
     * @param int $userid The user whose conversations should be counted.
     * @return array the array of conversations counts, indexed by type.
     */
    public static function get_conversation_counts(int $userid): array {
        self::lazy_create_self_conversation($userid);

        // Which conversations are countable is asked of the provider; the favourite partition is
        // here.
        $conversations = provider_factory::get($userid)->get_countable_conversation_types($userid);

        $usercontext = \context_user::instance($userid);
        $favservice = \core_favourites\service_factory::get_service_for_user_context($usercontext);
        $favourites = $favservice->find_favourites_by_type('core_message', 'message_conversations');
        $favouriteids = array_flip(array_column($favourites, 'itemid'));

        // The buckets are exclusive: a starred conversation is counted among the favourites and
        // never again under its own type, as it was before.
        $counts = [
            'favourites' => 0,
            'types' => [
                self::MESSAGE_CONVERSATION_TYPE_INDIVIDUAL => 0,
                self::MESSAGE_CONVERSATION_TYPE_GROUP => 0,
                self::MESSAGE_CONVERSATION_TYPE_SELF => 0
            ]
        ];
        foreach ($conversations as $convid => $type) {
            if (isset($favouriteids[$convid])) {
                $counts['favourites']++;
                continue;
            }
            $counts['types'][$type]++;
        }

        return $counts;
    }

    /**
     * Marks all messages being sent to a user in a particular conversation.
     *
     * If $conversationdid is null then it marks all messages as read sent to $userid.
     *
     * @param int $userid
     * @param int|null $conversationid The conversation the messages belong to mark as read, if null mark all
     */
    public static function mark_all_messages_as_read($userid, $conversationid = null) {
        $marked = provider_factory::get($userid)->mark_all_messages_as_read((int) $userid,
            is_null($conversationid) ? null : (int) $conversationid);

        foreach ($marked as $message) {
            self::trigger_message_viewed_event((int) $userid, $message->messageid, $message->useridfrom,
                $message->actionid);
        }
    }

    /**
     * Marks all notifications being sent from one user to another user as read.
     *
     * If the from user is null then it marks all notifications as read sent to the to user.
     *
     * @param int $touserid the id of the message recipient
     * @param int|null $fromuserid the id of the message sender, null if all messages
     * @param int|null $timecreatedto mark notifications created before this time as read
     * @return void
     */
    public static function mark_all_notifications_as_read($touserid, $fromuserid = null, $timecreatedto = null) {
        $provider = notification_provider_factory::get();

        // Two calls: the rows are read first, so that the notification_viewed events can be raised
        // one by one, then marked in bulk.
        $unread = $provider->get_notifications($touserid, $fromuserid, false, 'ASC', 0, 0);

        if (!$unread) {
            return;
        }

        $provider->mark_all_notifications_as_read($touserid, $fromuserid, $timecreatedto);

        foreach ($unread as $notification) {
            if (!empty($timecreatedto) && $notification->timecreated > $timecreatedto) {
                continue;
            }
            \core\event\notification_viewed::create_from_ids(
                $notification->useridfrom,
                $notification->useridto,
                $notification->id
            )->trigger();
        }
    }

    /**
     * @deprecated since 3.5
     */
    public static function mark_all_read_for_user() {
        throw new \coding_exception('\core_message\api::mark_all_read_for_user has been removed. Please either use ' .
            '\core_message\api::mark_all_notifications_as_read or \core_message\api::mark_all_messages_as_read');
    }

    /**
     * Returns message preferences.
     *
     * @param array $processors
     * @param array $providers
     * @param \stdClass $user
     * @return \stdClass
     * @since 3.2
     */
    public static function get_all_message_preferences($processors, $providers, $user) {
        $preferences = helper::get_providers_preferences($providers, $user->id);
        $preferences->userdefaultemail = $user->email; // May be displayed by the email processor.

        // For every processors put its options on the form (need to get function from processor's
        // lib.php).
        foreach ($processors as $processor) {
            $processor->object->load_data($preferences, $user->id);
        }

        // Load general messaging preferences.
        $preferences->blocknoncontacts = self::get_user_privacy_messaging_preference($user->id);
        $preferences->mailformat = $user->mailformat;
        $preferences->mailcharset = get_user_preferences('mailcharset', '', $user->id);

        return $preferences;
    }

    /**
     * Count the number of users blocked by a user.
     *
     * @param \stdClass $user The user object
     * @return int the number of blocked users
     */
    public static function count_blocked_users($user = null) {
        global $USER;

        if (empty($user)) {
            $user = $USER;
        }

        // Fetched and counted.
        return count(provider_factory::get($user->id)->get_blocked_user_ids($user->id));
    }

    /**
     * @deprecated since 3.8
     */
    public static function can_post_message() {
        throw new \coding_exception(
            '\core_message\api::can_post_message is deprecated and no longer used, ' .
            'please use \core_message\api::can_send_message instead.'
        );
    }

    /**
     * Determines if a user is permitted to send another user a private message.
     *
     * @param int $recipientid The recipient user id.
     * @param int $senderid The sender user id.
     * @param bool $evenifblocked This lets the user know, that even if the recipient has blocked the user
     *        the user is still able to send a message.
     * @return bool true if user is permitted, false otherwise.
     */
    public static function can_send_message(int $recipientid, int $senderid, bool $evenifblocked = false): bool {
        return provider_factory::get($senderid)->can_send_message($recipientid, $senderid, $evenifblocked);
    }

    /**
     * Determines if a user is permitted to send a message to a given conversation.
     * If no sender is provided then it defaults to the logged in user.
     *
     * @param int $userid the id of the user on which the checks will be applied.
     * @param int $conversationid the id of the conversation we wish to check.
     * @return bool true if the user can send a message to the conversation, false otherwise.
     * @throws \moodle_exception
     */
    public static function can_send_message_to_conversation(int $userid, int $conversationid): bool {
        return provider_factory::get($userid)->can_send_message_to_conversation($userid, $conversationid);
    }

    /**
     * Send a message from a user to a conversation.
     *
     * @param int $userid the sender id.
     * @param int $conversationid the conversation id.
     * @param string $message the message to send.
     * @param int $format the format of the message to send.
     * @return \stdClass the message created.
     * @throws \coding_exception
     * @throws \moodle_exception if the user is not permitted to send a message to the conversation.
     */
    public static function send_message_to_conversation(int $userid, int $conversationid, string $message,
                                                        int $format): \stdClass {
        if (!self::can_send_message_to_conversation($userid, $conversationid)) {
            throw new \moodle_exception("User $userid cannot send a message to conversation $conversationid");
        }

        return provider_factory::get($userid)->send_message_to_conversation($userid, $conversationid, $message,
            $format);
    }

    /**
     * Get the messaging preference for a user.
     * If the user has not any messaging privacy preference:
     * - When $CFG->messagingallusers = false the default user preference is MESSAGE_PRIVACY_COURSEMEMBER.
     * - When $CFG->messagingallusers = true the default user preference is MESSAGE_PRIVACY_SITE.
     *
     * @param  int    $userid The user identifier.
     * @return int    The default messaging preference.
     */
    public static function get_user_privacy_messaging_preference(int $userid): int {
        global $CFG, $USER;

        // When $CFG->messagingallusers is enabled, default value for the messaging preference will
        // be "Anyone on the site"; otherwise, the default value will be "My contacts and anyone in
        // my courses".
        if (empty($CFG->messagingallusers)) {
            $defaultprefvalue = self::MESSAGE_PRIVACY_COURSEMEMBER;
        } else {
            $defaultprefvalue = self::MESSAGE_PRIVACY_SITE;
        }
        if ($userid == $USER->id) {
            $user = $USER;
        } else {
            $user = $userid;
        }
        $privacypreference = get_user_preferences('message_blocknoncontacts', $defaultprefvalue, $user);

        // When the $CFG->messagingallusers privacy setting is disabled, MESSAGE_PRIVACY_SITE is
        // also disabled, so it has to be replaced to MESSAGE_PRIVACY_COURSEMEMBER.
        if (empty($CFG->messagingallusers) && $privacypreference == self::MESSAGE_PRIVACY_SITE) {
            $privacypreference = self::MESSAGE_PRIVACY_COURSEMEMBER;
        }

        return $privacypreference;
    }

    /**
     * @deprecated since 3.6
     */
    public static function is_user_non_contact_blocked() {
        throw new \coding_exception('\core_message\api::is_user_non_contact_blocked() is deprecated');
    }

    /**
     * @deprecated since 3.6
     */
    public static function is_user_blocked() {
        throw new \coding_exception('\core_message\api::is_user_blocked is deprecated and should not be used.');
    }

    /**
     * Get specified message processor, validate corresponding plugin existence and
     * system configuration.
     *
     * @param string $name  Name of the processor.
     * @param bool $ready only return ready-to-use processors.
     * @return mixed $processor if processor present else empty array.
     * @since Moodle 3.2
     */
    public static function get_message_processor($name, $ready = false) {
        global $DB, $CFG;

        $processor = $DB->get_record('message_processors', array('name' => $name));
        if (empty($processor)) {
            // Processor not found, return.
            return array();
        }

        $processor = self::get_processed_processor_object($processor);
        if ($ready) {
            if ($processor->enabled && $processor->configured) {
                return $processor;
            } else {
                return array();
            }
        } else {
            return $processor;
        }
    }

    /**
     * Returns weather a given processor is enabled or not.
     * Note:- This doesn't check if the processor is configured or not.
     *
     * @param string $name Name of the processor
     * @return bool
     */
    public static function is_processor_enabled($name) {

        $cache = \cache::make('core', 'message_processors_enabled');
        $status = $cache->get($name);

        if ($status === false) {
            $processor = self::get_message_processor($name);
            if (!empty($processor)) {
                $cache->set($name, $processor->enabled);
                return $processor->enabled;
            } else {
                return false;
            }
        }

        return $status;
    }

    /**
     * Set status of a processor.
     *
     * @param \stdClass $processor processor record.
     * @param 0|1 $enabled 0 or 1 to set the processor status.
     * @return bool
     * @since Moodle 3.2
     */
    public static function update_processor_status($processor, $enabled) {
        global $DB;
        $cache = \cache::make('core', 'message_processors_enabled');
        $cache->delete($processor->name);
        return $DB->set_field('message_processors', 'enabled', $enabled, array('id' => $processor->id));
    }

    /**
     * Given a processor object, loads information about it's settings and configurations.
     * This is not a public api, instead use @see \core_message\api::get_message_processor()
     * or @see \get_message_processors()
     *
     * @param \stdClass $processor processor object
     * @return \stdClass processed processor object
     * @since Moodle 3.2
     */
    public static function get_processed_processor_object(\stdClass $processor) {
        global $CFG;

        $processorfile = $CFG->dirroot. '/message/output/'.$processor->name.'/message_output_'.$processor->name.'.php';
        if (is_readable($processorfile)) {
            include_once($processorfile);
            $processclass = 'message_output_' . $processor->name;
            if (class_exists($processclass)) {
                $pclass = new $processclass();
                $processor->object = $pclass;
                $processor->configured = 0;
                if ($pclass->is_system_configured()) {
                    $processor->configured = 1;
                }
                $processor->hassettings = 0;
                if (is_readable($CFG->dirroot.'/message/output/'.$processor->name.'/settings.php')) {
                    $processor->hassettings = 1;
                }
                $processor->available = 1;
            } else {
                throw new \moodle_exception('errorcallingprocessor', 'message');
            }
        } else {
            $processor->available = 0;
        }
        return $processor;
    }

    /**
     * Retrieve users blocked by $user1
     *
     * @param int $userid The user id of the user whos blocked users we are returning
     * @return array the users blocked
     */
    public static function get_blocked_users($userid) {
        global $DB;

        $blockedids = provider_factory::get($userid)->get_blocked_user_ids($userid);
        if (empty($blockedids)) {
            return [];
        }

        // Ordering by first name and excluding deleted accounts are applied here.
        $userfieldsapi = \core_user\fields::for_userpic()->including('lastaccess');
        $userfields = $userfieldsapi->get_sql('u', false, '', '', false)->selects;

        list($insql, $params) = $DB->get_in_or_equal($blockedids, SQL_PARAMS_NAMED, 'blocked');
        $blockeduserssql = "SELECT $userfields
                              FROM {user} u
                             WHERE u.deleted = 0
                               AND u.id $insql
                          ORDER BY u.firstname ASC";

        return $DB->get_records_sql($blockeduserssql, $params);
    }

    /**
     * Mark a single message as read.
     *
     * @param int $userid The user id who marked the message as read
     * @param \stdClass $message The message
     * @param int|null $timeread The time the message was marked as read, if null will default to time()
     */
    public static function mark_message_as_read($userid, $message, $timeread = null) {
        $actionid = provider_factory::get($userid)->mark_message_as_read((int) $userid, $message, $timeread);

        self::trigger_message_viewed_event((int) $userid, (int) $message->id, (int) $message->useridfrom, $actionid);
    }

    /**
     * Triggers the event announcing that a user has read a message.
     *
     * @param int $userid The user who read the message
     * @param int $messageid The message read
     * @param int $useridfrom The author of the message
     * @param int $actionid The id of the recorded action
     * @return void
     */
    protected static function trigger_message_viewed_event(int $userid, int $messageid, int $useridfrom,
            int $actionid): void {
        // Get the context for the user who received the message.
        $context = \context_user::instance($userid, IGNORE_MISSING);
        // If the user no longer exists the context value will be false, in this case use the system
        // context.
        if ($context === false) {
            $context = \context_system::instance();
        }

        // Trigger event for reading a message.
        $event = \core\event\message_viewed::create([
            'objectid' => $actionid,
            'userid' => $userid, // Using the user who read the message as they are the ones performing the action.
            'context' => $context,
            'relateduserid' => $useridfrom,
            'other' => [
                'messageid' => $messageid,
            ],
        ]);
        $event->trigger();
    }

    /**
     * Mark a single notification as read.
     *
     * @param \stdClass $notification The notification
     * @param int|null $timeread The time the message was marked as read, if null will default to time()
     */
    public static function mark_notification_as_read($notification, $timeread = null) {
        if (is_null($timeread)) {
            $timeread = time();
        }

        if (is_null($notification->timeread)) {
            notification_provider_factory::get()
                ->mark_notification_as_read($notification->id, $timeread);

            // Trigger event for reading a notification.
            \core\event\notification_viewed::create_from_ids(
                $notification->useridfrom,
                $notification->useridto,
                $notification->id
            )->trigger();
        }
    }

    /**
     * Checks if a user can delete a message.
     *
     * @param int $userid the user id of who we want to delete the message for (this may be done by the admin
     *  but will still seem as if it was by the user)
     * @param int $messageid The message id
     * @return bool Returns true if a user can delete the message, false otherwise.
     */
    public static function can_delete_message($userid, $messageid) {
        global $USER;

        $systemcontext = \context_system::instance();

        $provider = provider_factory::get($userid);
        $conversationid = $provider->get_message_conversation_id($messageid);
        if ($conversationid === false) {
            throw new \dml_missing_record_exception('messages');
        }

        if (has_capability('moodle/site:deleteanymessage', $systemcontext)) {
            return true;
        }

        if (!$provider->is_user_in_conversation($userid, $conversationid)) {
            return false;
        }

        if (has_capability('moodle/site:deleteownmessage', $systemcontext) &&
                $USER->id == $userid) {
            return true;
        }

        return false;
    }

    /**
     * Deletes a message.
     *
     * This function does not verify any permissions.
     *
     * @param int $userid the user id of who we want to delete the message for (this may be done by the admin
     *  but will still seem as if it was by the user)
     * @param int $messageid The message id
     * @return bool
     */
    public static function delete_message($userid, $messageid) {
        global $USER;

        $actionid = provider_factory::get($userid)->delete_message((int) $userid, (int) $messageid);

        if ($actionid === false) {
            return false;
        }

        // Trigger event for deleting a message.
        \core\event\message_deleted::create_from_ids($userid, $USER->id, $messageid, $actionid)->trigger();

        return true;
    }

    /**
     * Returns the conversation between two users.
     *
     * @param array $userids
     * @return int|bool The id of the conversation, false if not found
     */
    public static function get_conversation_between_users(array $userids) {
        return provider_factory::get(empty($userids) ? null : (int) reset($userids))
            ->get_conversation_between_users($userids);
    }

    /**
     * @deprecated since 3.8
     */
    public static function get_individual_conversations_between_users() {
        throw new \coding_exception('\core_message\api::get_individual_conversations_between_users ' .
            ' is deprecated and no longer used.');
    }

    /**
     * Returns the self conversation for a user.
     *
     * @param int $userid The user id to get the self-conversations
     * @return \stdClass|false The self-conversation object or false if it doesn't exist
     * @since Moodle 3.7
     */
    public static function get_self_conversation(int $userid) {
        self::lazy_create_self_conversation($userid);

        return provider_factory::get($userid)->get_self_conversation($userid);
    }

    /**
     * @deprecated since 3.6
     */
    public static function create_conversation_between_users() {
        throw new \coding_exception('\core_message\api::create_conversation_between_users is deprecated, please use ' .
            '\core_message\api::create_conversation instead.');
    }

    /**
     * Creates a conversation with selected users and messages.
     *
     * @param int $type The type of conversation
     * @param int[] $userids The array of users to add to the conversation
     * @param string|null $name The name of the conversation
     * @param int $enabled Determines if the conversation is created enabled or disabled
     * @param string|null $component Defines the Moodle component which the conversation belongs to, if any
     * @param string|null $itemtype Defines the type of the component
     * @param int|null $itemid The id of the component
     * @param int|null $contextid The id of the context
     * @return \stdClass
     */
    public static function create_conversation(int $type, array $userids, ?string $name = null,
            int $enabled = self::MESSAGE_CONVERSATION_ENABLED, ?string $component = null,
            ?string $itemtype = null, ?int $itemid = null, ?int $contextid = null) {

        return provider_factory::get(empty($userids) ? null : (int) reset($userids))
            ->create_conversation($type, $userids, $name, $enabled, $component, $itemtype, $itemid, $contextid);
    }

    /**
     * Checks if a user can create a group conversation.
     *
     * @param int $userid The id of the user attempting to create the conversation
     * @param \context $context The context they are creating the conversation from, most likely course context
     * @return bool
     */
    public static function can_create_group_conversation(int $userid, \context $context): bool {
        global $CFG;

        // If we can't message at all, then we can't create a conversation.
        if (empty($CFG->messaging)) {
            return false;
        }

        // We need to check they have the capability to create the conversation.
        return has_capability('moodle/course:creategroupconversations', $context, $userid);
    }

    /**
     * Checks if a user can create a contact request.
     *
     * @param int $userid The id of the user who is creating the contact request
     * @param int $requesteduserid The id of the user being requested
     * @return bool
     */
    public static function can_create_contact(int $userid, int $requesteduserid): bool {
        global $CFG;

        // If we can't message at all, then we can't create a contact.
        if (empty($CFG->messaging)) {
            return false;
        }

        // If we can message anyone on the site then we can create a contact.
        if ($CFG->messagingallusers) {
            return true;
        }

        // We need to check if they are in the same course.
        return enrol_sharing_course($userid, $requesteduserid);
    }

    /**
     * Handles creating a contact request.
     *
     * @param int $userid The id of the user who is creating the contact request
     * @param int $requesteduserid The id of the user being requested
     * @return \stdClass the request
     */
    public static function create_contact_request(int $userid, int $requesteduserid): \stdClass {
        $request = provider_factory::get($userid)->create_contact_request($userid, $requesteduserid);

        // On the remote branch the notification is written by the notification context, which
        // consumes the ContactRequestCreated event; on the legacy branch it is written here.
        if (!provider_factory::routes_to_service()) {
            self::send_contact_request_notification($userid, $requesteduserid);
        }

        return $request;
    }

    /**
     * Notifies a user that somebody asked to be added as a contact.
     *
     * @param int $userid The id of the user who created the contact request
     * @param int $requesteduserid The id of the user being requested
     * @return void
     */
    protected static function send_contact_request_notification(int $userid, int $requesteduserid): void {
        global $PAGE, $SITE;

        $userfrom = \core_user::get_user($userid);
        $userfromfullname = fullname($userfrom);
        $userto = \core_user::get_user($requesteduserid);
        $url = new \moodle_url('/message/index.php', ['view' => 'contactrequests']);

        $subject = get_string_manager()->get_string('messagecontactrequestsubject', 'core_message', (object) [
            'sitename' => format_string($SITE->fullname, true, ['context' => \context_system::instance()]),
            'user' => $userfromfullname,
        ], $userto->lang);

        $fullmessage = get_string_manager()->get_string('messagecontactrequest', 'core_message', (object) [
            'url' => $url->out(),
            'user' => $userfromfullname,
        ], $userto->lang);

        $message = new \core\message\message();
        $message->courseid = SITEID;
        $message->component = 'moodle';
        $message->name = 'messagecontactrequests';
        $message->notification = 1;
        $message->userfrom = $userfrom;
        $message->userto = $userto;
        $message->subject = $subject;
        $message->fullmessage = text_to_html($fullmessage);
        $message->fullmessageformat = FORMAT_HTML;
        $message->fullmessagehtml = $fullmessage;
        $message->smallmessage = '';
        $message->contexturl = $url->out(false);
        $userpicture = new \user_picture($userfrom);
        $userpicture->size = 1; // Use f1 size.
        $userpicture->includetoken = $userto->id; // Generate an out-of-session token for the user receiving the message.
        $message->customdata = [
            'notificationiconurl' => $userpicture->get_url($PAGE)->out(false),
            'actionbuttons' => [
                'accept' => get_string_manager()->get_string('accept', 'moodle', null, $userto->lang),
                'reject' => get_string_manager()->get_string('reject', 'moodle', null, $userto->lang),
            ],
        ];

        message_send($message);
    }


    /**
     * Handles confirming a contact request.
     *
     * @param int $userid The id of the user who created the contact request
     * @param int $requesteduserid The id of the user confirming the request
     */
    public static function confirm_contact_request(int $userid, int $requesteduserid) {
        $contact = provider_factory::get($userid)->confirm_contact_request($userid, $requesteduserid);

        if ($contact) {
            self::trigger_contact_added_event($contact);
        }
    }

    /**
     * Handles declining a contact request.
     *
     * @param int $userid The id of the user who created the contact request
     * @param int $requesteduserid The id of the user declining the request
     */
    public static function decline_contact_request(int $userid, int $requesteduserid) {
        provider_factory::get($userid)->decline_contact_request($userid, $requesteduserid);
    }

    /**
     * Handles returning the contact requests for a user.
     *
     * This also includes the user data necessary to display information
     * about the user.
     *
     * It will not include blocked users.
     *
     * @param int $userid
     * @param int $limitfrom
     * @param int $limitnum
     * @return array The list of contact requests
     */
    public static function get_contact_requests(int $userid, int $limitfrom = 0, int $limitnum = 0): array {
        $userids = provider_factory::get($userid)
            ->get_received_contact_request_user_ids($userid, $limitfrom, $limitnum);
        if (empty($userids)) {
            return [];
        }

        return helper::get_member_info($userid, $userids);
    }

    /**
     * Returns the number of contact requests the user has received.
     *
     * @param int $userid The ID of the user we want to return the number of received contact requests for
     * @return int The count
     */
    public static function get_received_contact_requests_count(int $userid): int {
        return provider_factory::get($userid)->get_received_contact_requests_count($userid);
    }

    /**
     * Handles adding a contact.
     *
     * @param int $userid The id of the user who requested to be a contact
     * @param int $contactid The id of the contact
     */
    public static function add_contact(int $userid, int $contactid) {
        $contact = provider_factory::get($userid)->add_contact($userid, $contactid);

        self::trigger_contact_added_event($contact);
    }

    /**
     * Triggers the event announcing that a contact relationship was created.
     *
     * @param \stdClass $contact The contact record
     * @return void
     */
    protected static function trigger_contact_added_event(\stdClass $contact): void {
        $event = \core\event\message_contact_added::create([
            'objectid' => $contact->id,
            'userid' => $contact->userid,
            'relateduserid' => $contact->contactid,
            'context' => \context_user::instance($contact->userid),
        ]);
        $event->add_record_snapshot('message_contacts', $contact);
        $event->trigger();
    }

    /**
     * Handles removing a contact.
     *
     * @param int $userid The id of the user who is removing a user as a contact
     * @param int $contactid The id of the user to be removed as a contact
     */
    public static function remove_contact(int $userid, int $contactid) {
        if ($contact = provider_factory::get($userid)->remove_contact($userid, $contactid)) {
            $event = \core\event\message_contact_removed::create([
                'objectid' => $contact->id,
                'userid' => $userid,
                'relateduserid' => $contactid,
                'context' => \context_user::instance($userid),
            ]);
            $event->add_record_snapshot('message_contacts', $contact);
            $event->trigger();
        }
    }

    /**
     * Handles blocking a user.
     *
     * @param int $userid The id of the user who is blocking
     * @param int $usertoblockid The id of the user being blocked
     */
    public static function block_user(int $userid, int $usertoblockid) {
        $blocked = provider_factory::get($userid)->block_user($userid, $usertoblockid);

        // Trigger event for blocking a contact.
        $event = \core\event\message_user_blocked::create([
            'objectid' => $blocked->id,
            'userid' => $userid,
            'relateduserid' => $usertoblockid,
            'context' => \context_user::instance($userid),
        ]);
        $event->add_record_snapshot('message_users_blocked', $blocked);
        $event->trigger();
    }

    /**
     * Handles unblocking a user.
     *
     * @param int $userid The id of the user who is unblocking
     * @param int $usertounblockid The id of the user being unblocked
     */
    public static function unblock_user(int $userid, int $usertounblockid) {
        if ($blockeduser = provider_factory::get($userid)->unblock_user($userid, $usertounblockid)) {
            // Trigger event for unblocking a contact.
            $event = \core\event\message_user_unblocked::create([
                'objectid' => $blockeduser->id,
                'userid' => $userid,
                'relateduserid' => $usertounblockid,
                'context' => \context_user::instance($userid),
            ]);
            $event->add_record_snapshot('message_users_blocked', $blockeduser);
            $event->trigger();
        }
    }

    /**
     * Checks if users are already contacts.
     *
     * @param int $userid The id of one of the users
     * @param int $contactid The id of the other user
     * @return bool Returns true if they are a contact, false otherwise
     */
    public static function is_contact(int $userid, int $contactid): bool {
        return provider_factory::get($userid)->is_contact($userid, $contactid);
    }

    /**
     * Returns the row in the database table message_contacts that represents the contact between two people.
     *
     * @param int $userid The id of one of the users
     * @param int $contactid The id of the other user
     * @return mixed A fieldset object containing the record, false otherwise
     */
    public static function get_contact(int $userid, int $contactid) {
        return provider_factory::get($userid)->get_contact($userid, $contactid);
    }

    /**
     * Checks if a user is already blocked.
     *
     * @param int $userid
     * @param int $blockeduserid
     * @return bool Returns true if they are a blocked, false otherwise
     */
    public static function is_blocked(int $userid, int $blockeduserid): bool {
        return provider_factory::get($userid)->is_blocked($userid, $blockeduserid);
    }

    /**
     * Get contact requests between users.
     *
     * @param int $userid The id of the user who is creating the contact request
     * @param int $requesteduserid The id of the user being requested
     * @return \stdClass[]
     */
    public static function get_contact_requests_between_users(int $userid, int $requesteduserid): array {
        return provider_factory::get($userid)->get_contact_requests_between_users($userid, $requesteduserid);
    }

    /**
     * Checks if a contact request already exists between users.
     *
     * @param int $userid The id of the user who is creating the contact request
     * @param int $requesteduserid The id of the user being requested
     * @return bool Returns true if a contact request exists, false otherwise
     */
    public static function does_contact_request_exist(int $userid, int $requesteduserid): bool {
        return provider_factory::get($userid)->does_contact_request_exist($userid, $requesteduserid);
    }

    /**
     * Checks if a user is already in a conversation.
     *
     * @param int $userid The id of the user we want to check if they are in a group
     * @param int $conversationid The id of the conversation
     * @return bool Returns true if a contact request exists, false otherwise
     */
    public static function is_user_in_conversation(int $userid, int $conversationid): bool {
        return provider_factory::get($userid)->is_user_in_conversation($userid, $conversationid);
    }

    /**
     * Add some new members to an existing conversation.
     *
     * @param array $userids User ids array to add as members.
     * @param int $convid The conversation id. Must exists.
     * @throws \dml_missing_record_exception If convid conversation doesn't exist
     * @throws \dml_exception If there is a database error
     * @throws \moodle_exception If trying to add a member(s) to a non-group conversation
     */
    public static function add_members_to_conversation(array $userids, int $convid) {
        provider_factory::get()->add_members_to_conversation($userids, $convid);
    }

    /**
     * Remove some members from an existing conversation.
     *
     * @param array $userids The user ids to remove from conversation members.
     * @param int $convid The conversation id. Must exists.
     * @throws \dml_exception
     * @throws \moodle_exception If trying to remove a member(s) from a non-group conversation
     */
    public static function remove_members_from_conversation(array $userids, int $convid) {
        provider_factory::get()->remove_members_from_conversation($userids, $convid);
    }

    /**
     * Count conversation members.
     *
     * @param int $convid The conversation id.
     * @return int Number of conversation members.
     * @throws \dml_exception
     */
    public static function count_conversation_members(int $convid): int {
        return provider_factory::get()->count_conversation_members($convid);
    }

    /**
     * Checks whether or not a conversation area is enabled.
     *
     * @param string $component Defines the Moodle component which the area was added to.
     * @param string $itemtype Defines the type of the component.
     * @param int $itemid The id of the component.
     * @param int $contextid The id of the context.
     * @return bool Returns if a conversation area exists and is enabled, false otherwise
     */
    public static function is_conversation_area_enabled(string $component, string $itemtype, int $itemid, int $contextid): bool {
        return provider_factory::get()->is_conversation_area_enabled($component, $itemtype, $itemid, $contextid);
    }

    /**
     * Get conversation by area.
     *
     * @param string $component Defines the Moodle component which the area was added to.
     * @param string $itemtype Defines the type of the component.
     * @param int $itemid The id of the component.
     * @param int $contextid The id of the context.
     * @return \stdClass
     */
    public static function get_conversation_by_area(string $component, string $itemtype, int $itemid, int $contextid) {
        return provider_factory::get()->get_conversation_by_area($component, $itemtype, $itemid, $contextid);
    }

    /**
     * Enable a conversation.
     *
     * @param int $conversationid The id of the conversation.
     * @return void
     */
    public static function enable_conversation(int $conversationid) {
        provider_factory::get()->enable_conversation($conversationid);
    }

    /**
     * Disable a conversation.
     *
     * @param int $conversationid The id of the conversation.
     * @return void
     */
    public static function disable_conversation(int $conversationid) {
        provider_factory::get()->disable_conversation($conversationid);
    }

    /**
     * Update the name of a conversation.
     *
     * @param int $conversationid The id of a conversation.
     * @param string $name The main name of the area
     * @return void
     */
    public static function update_conversation_name(int $conversationid, string $name) {
        provider_factory::get()->update_conversation_name($conversationid, $name);
    }

    /**
     * Returns a list of conversation members.
     *
     * @param int $userid The user we are returning the conversation members for, used by helper::get_member_info.
     * @param int $conversationid The id of the conversation
     * @param bool $includecontactrequests Do we want to include contact requests with this data?
     * @param bool $includeprivacyinfo Do we want to include privacy requests with this data?
     * @param int $limitfrom
     * @param int $limitnum
     * @return array
     */
    public static function get_conversation_members(int $userid, int $conversationid, bool $includecontactrequests = false,
                                                    bool $includeprivacyinfo = false, int $limitfrom = 0,
                                                    int $limitnum = 0): array {
        $userids = provider_factory::get($userid)->get_conversation_member_ids($conversationid, $limitfrom, $limitnum);

        if (empty($userids)) {
            return [];
        }

        // Profile data of the members are attached here.
        return helper::get_member_info($userid, $userids, $includecontactrequests, $includeprivacyinfo);
    }

    /**
     * Get the unread counts for all conversations for the user, sorted by type, and including favourites.
     *
     * @param int $userid the id of the user whose conversations we'll check.
     * @return array the unread counts for each conversation, indexed by type.
     */
    public static function get_unread_conversation_counts(int $userid): array {
        // Which conversations hold unread messages is asked of the provider; the favourite
        // partition is here.
        $unread = provider_factory::get($userid)->get_unread_conversation_types($userid);

        $service = \core_favourites\service_factory::get_service_for_user_context(\context_user::instance($userid));
        $favouriteconversations = $service->find_favourites_by_type('core_message', 'message_conversations');
        $favouriteconvids = array_flip(array_column($favouriteconversations, 'itemid'));

        // A favourite conversation is counted once, among the favourites, and never again under its
        // own type: the buckets are exclusive, as they were before.
        $counts = ['favourites' => 0, 'types' => [
            self::MESSAGE_CONVERSATION_TYPE_INDIVIDUAL => 0,
            self::MESSAGE_CONVERSATION_TYPE_GROUP => 0,
            self::MESSAGE_CONVERSATION_TYPE_SELF => 0
        ]];
        foreach ($unread as $convid => $type) {
            if (isset($favouriteconvids[$convid])) {
                $counts['favourites']++;
                continue;
            }
            $counts['types'][$type]++;
        }

        return $counts;
    }

    /**
     * Handles muting a conversation.
     *
     * @param int $userid The id of the user
     * @param int $conversationid The id of the conversation
     */
    public static function mute_conversation(int $userid, int $conversationid): void {
        provider_factory::get($userid)->mute_conversation($userid, $conversationid);
    }

    /**
     * Handles unmuting a conversation.
     *
     * @param int $userid The id of the user
     * @param int $conversationid The id of the conversation
     */
    public static function unmute_conversation(int $userid, int $conversationid): void {
        provider_factory::get($userid)->unmute_conversation($userid, $conversationid);
    }

    /**
     * Checks whether a conversation is muted or not.
     *
     * @param int $userid The id of the user
     * @param int $conversationid The id of the conversation
     * @return bool Whether or not the conversation is muted or not
     */
    public static function is_conversation_muted(int $userid, int $conversationid): bool {
        return provider_factory::get($userid)->is_conversation_muted($userid, $conversationid);
    }

    /**
     * Completely removes all related data in the DB for a given conversation.
     *
     * @param int $conversationid The id of the conversation
     */
    public static function delete_all_conversation_data(int $conversationid) {
        $provider = provider_factory::get();

        $conv = $provider->get_conversation_by_id($conversationid);
        $convcontext = (!empty($conv) && !empty($conv->contextid)) ? \context::instance_by_id($conv->contextid) : null;

        $provider->delete_all_conversation_data($conversationid);

        // Favourites (core_favourites) are removed by this facade.
        $service = \core_favourites\service_factory::get_service_for_component('core_message');
        $service->delete_favourites_by_type_and_item('message_conversations', $conversationid, $convcontext);
    }

    /**
     * Checks if a user can delete a message for all users.
     *
     * @param int $userid the user id of who we want to delete the message for all users
     * @param int $messageid The message id
     * @return bool Returns true if a user can delete the message for all users, false otherwise.
     */
    public static function can_delete_message_for_all_users(int $userid, int $messageid): bool {
        // Which conversation a message belongs to is asked of the provider; the capability is
        // checked here.
        $conversation = provider_factory::get($userid)->get_conversation_by_message($messageid);

        if (!empty($conversation->contextid)) {
            return has_capability('moodle/site:deleteanymessage',
                \context::instance_by_id($conversation->contextid), $userid);
        }

        return has_capability('moodle/site:deleteanymessage', \context_system::instance(), $userid);
    }
    /**
     * Delete a message for all users.
     *
     * This function does not verify any permissions.
     *
     * @param int $messageid The message id
     * @return void
     */
    public static function delete_message_for_all_users(int $messageid) {
        global $USER;

        $deleted = provider_factory::get()->delete_message_for_all_users($messageid);

        foreach ($deleted as $deletion) {
            \core\event\message_deleted::create_from_ids($deletion->userid, $USER->id, $deletion->messageid,
                $deletion->actionid)->trigger();
        }
    }

    /**
     * Create a self conversation for a user, only if one doesn't already exist.
     *
     * @param int $userid the user to whom the conversation belongs.
     */
    protected static function lazy_create_self_conversation(int $userid): void {
        // Check if the self-conversation for this user exists.
        // If not, create and star it for the user.
        // Don't use the API methods here, as they in turn may rely on
        // lazy creation and we'll end up with recursive loops of doom.
        $provider = provider_factory::get($userid);

        if (empty($provider->get_self_conversation($userid))) {
            $selfconversation = $provider->create_conversation(self::MESSAGE_CONVERSATION_TYPE_SELF, [$userid]);
            self::set_favourite_conversation($selfconversation->id, $userid);
        }
    }
}
