<?php
// Web service endpoint for the load test of the SMS API (core_sms). Load testing only.

defined('MOODLE_INTERNAL') || die();

$plugin->component = 'local_smstest';
$plugin->version   = 2026080700;
$plugin->requires  = 2024100700; // Moodle 4.5 LTS.
$plugin->maturity  = MATURITY_ALPHA;
$plugin->release   = '1.0 (load testing only)';
