<?php
defined('MOODLE_INTERNAL') || die();

$functions = [
    'local_smstest_send' => [
        'classname'     => 'local_smstest\external\send',
        'methodname'    => 'execute',
        'description'   => 'Sends one SMS through core_sms\\manager::send(), for load testing',
        'type'          => 'write',
        'ajax'          => false,
        'capabilities'  => 'local/smstest:send',
    ],
];

// The service the test tokens belong to.
$services = [
    'SMS Load Test Service' => [
        'functions'       => ['local_smstest_send'],
        'restrictedusers' => 0,
        'enabled'         => 1,
        'shortname'       => 'local_smstest',
    ],
];
