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
 * Sends the notification templates to the extracted NotificationService.
 *
 *   sudo -u www-data php message/cli/seed_notification_templates.php [--dry-run]
 *
 * @package    core_message
 * @copyright  2026 Benedetta Pia Celano
 * @license    http://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

define('CLI_SCRIPT', true);

require(__DIR__ . '/../../config.php');
require_once($CFG->libdir . '/clilib.php');

[$options, $unrecognised] = cli_get_params(
    ['help' => false, 'dry-run' => false],
    ['h' => 'help', 'n' => 'dry-run']
);

if ($unrecognised) {
    cli_error(get_string('cliunknowoption', 'admin', implode(PHP_EOL . '  ', $unrecognised)));
}

if ($options['help']) {
    cli_writeln("Sends the notification templates to the extracted NotificationService.

Options:
  -h, --help     Print this help
  -n, --dry-run  Show what would be sent, send nothing

Example:
  sudo -u www-data php message/cli/seed_notification_templates.php
");
    exit(0);
}

/**
 * The notifications the service composes, and the strings each one needs.
 */
$communications = [
    [
        'component' => 'moodle',
        'eventtype' => 'messagecontactrequests',
        'subject' => ['messagecontactrequestsubject', 'core_message'],
        'body' => ['messagecontactrequest', 'core_message'],
        'customdata' => [
            'accept' => ['accept', 'moodle'],
            'reject' => ['reject', 'moodle'],
        ],
    ],
];

$stringmanager = get_string_manager();

// Every installed language.
$languages = array_keys($stringmanager->get_list_of_translations());

if (!in_array('en', $languages, true)) {
    // English is the fallback of the service and is always seeded.
    $languages[] = 'en';
}

$templates = [];

foreach ($communications as $communication) {
    foreach ($languages as $lang) {
        $customdata = [];
        foreach ($communication['customdata'] as $key => [$identifier, $component]) {
            $customdata[$key] = $stringmanager->get_string($identifier, $component, null, $lang);
        }

        $templates[] = [
            'component' => $communication['component'],
            'event_type' => $communication['eventtype'],
            'lang' => $lang,
            'subject' => $stringmanager->get_string(
                $communication['subject'][0], $communication['subject'][1], (object) [], $lang),
            'body' => $stringmanager->get_string(
                $communication['body'][0], $communication['body'][1], (object) [], $lang),
            'custom_data' => json_encode($customdata, JSON_UNESCAPED_UNICODE),
        ];
    }
}

cli_writeln('Languages found: ' . implode(', ', $languages));
cli_writeln('Templates prepared: ' . count($templates));

if ($options['dry-run']) {
    foreach ($templates as $template) {
        cli_writeln('');
        cli_writeln("  [{$template['lang']}] {$template['component']}/{$template['event_type']}");
        cli_writeln('    subject: ' . $template['subject']);
        cli_writeln('    body:    ' . $template['body']);
        cli_writeln('    buttons: ' . $template['custom_data']);
    }
    cli_writeln('');
    cli_writeln('Dry run: nothing was sent.');
    exit(0);
}

// The placeholders survive: get_string() with an empty object leaves {$a->user} untouched.
foreach ($templates as $template) {
    if (str_contains($template['subject'], '{$a') === false
            && str_contains($template['body'], '{$a') === false) {
        cli_problem("Warning: the [{$template['lang']}] template carries no placeholder. "
            . 'Check that the language pack has not resolved them away.');
    }
}

$transport = \core_message\notification\transport::create_from_config();

try {
    $response = $transport->post('/api/v1/templates', ['templates' => $templates]);
} catch (\core_message\conversation\service_exception $exception) {
    cli_error('The service refused or could not be reached: ' . $exception->getMessage());
}

cli_writeln('Templates written: ' . ($response['written'] ?? 0));
cli_writeln('Languages now known to the service: ' . implode(', ', $response['languages'] ?? []));
