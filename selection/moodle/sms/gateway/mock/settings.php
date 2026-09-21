<?php
defined('MOODLE_INTERNAL') || die();

if ($hassiteconfig) {
    $settings->add(new admin_setting_configtext(
        'smsgateway_mock/endpoint',
        get_string('endpoint', 'smsgateway_mock'),
        get_string('endpoint_desc', 'smsgateway_mock'),
        'http://192.168.72.74:8080/send',
        PARAM_URL,
    ));

    $settings->add(new admin_setting_configtext(
        'smsgateway_mock/timeout',
        get_string('timeout', 'smsgateway_mock'),
        get_string('timeout_desc', 'smsgateway_mock'),
        '10',
        PARAM_INT,
    ));
}
