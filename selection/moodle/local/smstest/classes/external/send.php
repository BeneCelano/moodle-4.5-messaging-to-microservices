<?php
namespace local_smstest\external;

use core_external\external_api;
use core_external\external_function_parameters;
use core_external\external_single_structure;
use core_external\external_value;

defined('MOODLE_INTERNAL') || die();

/**
 * Web service function that sends one SMS through \core_sms\manager::send().
 *
 * Load testing only: no rate limit, no number verification.
 */
class send extends external_api {

    public static function execute_parameters(): external_function_parameters {
        return new external_function_parameters([
            'recipientnumber' => new external_value(
                PARAM_TEXT,
                'Recipient number in E.164 format, e.g. +391234567890',
                VALUE_REQUIRED,
            ),
            'content' => new external_value(
                PARAM_TEXT,
                'Message text, at most 480 characters',
                VALUE_DEFAULT,
                'Load test message from JMeter',
            ),
        ]);
    }

    public static function execute(string $recipientnumber, string $content = 'Load test message from JMeter'): array {
        [
            'recipientnumber' => $recipientnumber,
            'content' => $content,
        ] = self::validate_parameters(self::execute_parameters(), [
            'recipientnumber' => $recipientnumber,
            'content' => $content,
        ]);

        $context = \context_system::instance();
        self::validate_context($context);
        require_capability('local/smstest:send', $context);

        $message = \core\di::get(\core_sms\manager::class)->send(
            recipientnumber: $recipientnumber,
            content: $content,
            component: 'local_smstest',
            messagetype: 'loadtest',
            recipientuserid: 1,
            issensitive: false,  // The message is written to the sms log table.
            async: false,
        );

        return [
            'success'   => $message->is_sent(),
            'messageid' => (int) $message->id,
            'status'    => $message->status->value,
        ];
    }

    public static function execute_returns(): external_single_structure {
        return new external_single_structure([
            'success'   => new external_value(PARAM_BOOL, 'Whether the message was sent'),
            'messageid' => new external_value(PARAM_INT, 'Id of the stored message'),
            'status'    => new external_value(PARAM_TEXT, 'Status of the message'),
        ]);
    }
}
