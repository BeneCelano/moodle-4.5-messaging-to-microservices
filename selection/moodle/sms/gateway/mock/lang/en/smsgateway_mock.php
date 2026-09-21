<?php
defined('MOODLE_INTERNAL') || die();

$string['pluginname'] = 'Mock SMS gateway (load testing)';
$string['endpoint'] = 'Mock provider endpoint';
$string['endpoint_desc'] = 'URL of the mock provider, e.g. http://192.168.72.74:8080/send';
$string['timeout'] = 'HTTP timeout (seconds)';
$string['timeout_desc'] = 'Timeout of the call to the mock provider; must exceed the delay configured on the provider.';
