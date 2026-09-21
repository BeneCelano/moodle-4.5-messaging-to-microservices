<?php
defined('MOODLE_INTERNAL') || die();

$capabilities = [
    'local/smstest:send' => [
        'captype'      => 'write',
        'contextlevel' => CONTEXT_SYSTEM,
        'archetypes'   => [
            'manager' => CAP_ALLOW,
        ],
    ],
];
