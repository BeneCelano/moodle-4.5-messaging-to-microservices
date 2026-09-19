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
 * Verifies that a component of the monolith which notifies people is diverted to the service and
 * keeps working.
 *
 *   sudo -u www-data php message/cli/verify_notification_reroute.php [--assignmentid=ID] [--dry-run] [--keep]
 *
 * @package    core_message
 * @copyright  2026 Benedetta Pia Celano
 * @license    http://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

define('CLI_SCRIPT', true);

require(__DIR__ . '/../../config.php');
require_once($CFG->libdir . '/clilib.php');

[$options, $unrecognised] = cli_get_params(
    [
        'help' => false,
        'assignmentid' => 0,
        'dry-run' => false,
        'keep' => false,
    ],
    ['h' => 'help', 'n' => 'dry-run']
);

if ($unrecognised) {
    cli_error(get_string('cliunknowoption', 'admin', implode(PHP_EOL . '  ', $unrecognised)));
}

if ($options['help']) {
    cli_writeln("Verifies that a monolith component which notifies people is diverted to the service.

Options:
  -h, --help          Print this help
      --assignmentid  Use this assignment instead of looking for one
  -n, --dry-run       Show what would be used, change nothing
      --keep          Keep the notification produced, so it can be looked at

Example:
  sudo -u www-data php message/cli/verify_notification_reroute.php
");
    exit(0);
}

// ---------------------------------------------------------------------------------------------------
// The register of checks, printed as it goes. The last line is the one verify.sh reads.
// ---------------------------------------------------------------------------------------------------

$controlli = [];

/**
 * Records a check and prints it immediately.
 *
 * @param bool $riuscito Whether the check passed
 * @param string $nome What was checked
 * @param string $dettaglio What was observed
 * @return bool The same value, so the caller can branch on it
 */
function controllo(bool $riuscito, string $nome, string $dettaglio = ''): bool {
    global $controlli;

    $controlli[] = [$riuscito, $nome, $dettaglio];
    cli_writeln(sprintf('%-7s  %-56s  %s', $riuscito ? 'OK' : 'FAILED', $nome, $dettaglio));

    return $riuscito;
}

/**
 * Prints the summary and returns the exit code.
 *
 * @return int
 */
function prospetto(): int {
    global $controlli;

    $superati = count(array_filter($controlli, fn($c) => $c[0]));
    $falliti = count($controlli) - $superati;

    cli_writeln('');
    cli_writeln(sprintf('RESULT %d checks, %d passed, %d failed',
        count($controlli), $superati, $falliti));

    return $falliti === 0 ? 0 : 1;
}

// ---------------------------------------------------------------------------------------------------
// 1. Which implementation is answering
// ---------------------------------------------------------------------------------------------------

$modalita = $CFG->notificationservice_mode ?? \core_message\notification\provider_factory::MODE_LEGACY;
$remota = ($modalita === \core_message\notification\provider_factory::MODE_REMOTE);

cli_writeln('');
cli_writeln('Test of the rerouting of a monolith component\'s notifications');
cli_writeln('Routing mode: ' . $modalita);
cli_writeln('');

$provider = \core_message\notification\provider_factory::get();
controllo(true, 'implementation serving the context', get_class($provider));

// ---------------------------------------------------------------------------------------------------
// 2. An assignment, and somebody who could be warned about it
// ---------------------------------------------------------------------------------------------------

$assignmentid = (int) $options['assignmentid'];

if (!$assignmentid) {
    // Any assignment with a due date and at least one participant; the window is moved below.
    $candidati = $DB->get_records_select('assign', 'duedate > 0', [], 'id ASC', 'id, name, duedate', 0, 20);
    foreach ($candidati as $candidato) {
        if (\mod_assign\notification_helper::get_users_within_assignment(
                $candidato->id, \mod_assign\notification_helper::TYPE_DUE_DIGEST)) {
            $assignmentid = (int) $candidato->id;
            break;
        }
        // The digest window is a week wide.
    }
}

if (!$assignmentid) {
    // Fallback to the plain enrolment question.
    $candidati = $DB->get_records_select('assign', 'duedate > 0', [], 'id ASC', 'id, name, duedate', 0, 20);
    foreach ($candidati as $candidato) {
        [$course, $cm] = get_course_and_cm_from_instance($candidato->id, 'assign');
        if (count_enrolled_users(\context_module::instance($cm->id), 'mod/assign:submit') > 0) {
            $assignmentid = (int) $candidato->id;
            break;
        }
    }
}

if (!$assignmentid) {
    // Not applicable: exit status 2, recorded by verify.sh as NOT RUNNABLE.
    cli_writeln('');
    cli_writeln('NOT APPLICABLE: no assignment with a due date and at least one enrolled student.');
    cli_writeln('');
    cli_writeln('To make the test runnable, any course needs:');
    cli_writeln('  - an assignment with a due date (duedate) in the future;');
    cli_writeln('  - at least one person enrolled in the course as a student.');
    cli_writeln('The test moves the due date and puts it back: it leaves no trace.');
    exit(2);
}

$compito = $DB->get_record('assign', ['id' => $assignmentid], 'id, name, duedate, cutoffdate', MUST_EXIST);
controllo(true, 'an assignment with a due date and at least one enrolled student',
    sprintf('id %d, "%s"', $compito->id, $compito->name));

if ($options['dry-run']) {
    cli_writeln('');
    cli_writeln('Dry run: the due date was not moved and nothing was sent.');
    exit(prospetto());
}

// ---------------------------------------------------------------------------------------------------
// 3. The run
// ---------------------------------------------------------------------------------------------------

$scadenzaoriginale = (int) $compito->duedate;
$cutoffcambiato = false;
$notificaprodotta = 0;
$utente = null;

try {
    // One day out, inside the two-day "due soon" window.
    $DB->set_field('assign', 'duedate', time() + DAYSECS, ['id' => $assignmentid]);

    // The cut-off date is pushed out too, and restored with the rest.
    if (!empty($compito->cutoffdate) && $compito->cutoffdate < time() + DAYSECS) {
        $DB->set_field('assign', 'cutoffdate', time() + (DAYSECS * 3), ['id' => $assignmentid]);
        $cutoffcambiato = true;
    }

    // -- the first read: who still needs warning -----------------------------------------------
    //
    // This is ordinary Moodle code, and it contains the query that was rerouted. Calling it is how
    // the undeclared dependency gets exercised without reaching into it.
    $daavvisare = \mod_assign\notification_helper::get_users_within_assignment(
        $assignmentid, \mod_assign\notification_helper::TYPE_DUE_SOON);

    if (!controllo((bool) $daavvisare,
            'the assignment has someone to warn about the due date',
            count($daavvisare) . ' participants')) {
        throw new \RuntimeException('without recipients the following checks would say nothing');
    }

    $utente = reset($daavvisare);
    $userid = (int) $utente->id;

    // -- the state before -----------------------------------------------------------------------
    $primaservizio = $provider->count_unread_notifications($userid);
    $primamonolite = (int) $DB->count_records_select('notifications',
        'useridto = :u AND eventtype = :e',
        ['u' => $userid, 'e' => \mod_assign\notification_helper::TYPE_DUE_SOON]);

    controllo(true, 'starting state recorded',
        sprintf('user %d: %d unread in the context, %d reminders in the monolith',
            $userid, $primaservizio, $primamonolite));

    // -- the write: ordinary Moodle code sends the reminder --------------------------------------
    // In production the reminder is an ad-hoc task queued with the recipient's id, and the cron
    // runner sets that user up before executing it: strings and dates come out in the recipient's
    // language. Calling the helper directly would compose them in the site language instead, so
    // the same setup is done here, and undone right after.
    \core\cron::setup_user(\core_user::get_user($userid, '*', MUST_EXIST));
    try {
        \mod_assign\notification_helper::send_due_soon_notification_to_user($assignmentid, $userid);
    } finally {
        \core\cron::setup_user();
    }

    $dopo = $provider->get_notifications($userid, null, null, 'DESC', 0, 5);
    $prodotta = null;
    foreach ($dopo as $candidata) {
        if ($candidata->eventtype === \mod_assign\notification_helper::TYPE_DUE_SOON
                && $candidata->component === 'mod_assign') {
            $prodotta = $candidata;
            break;
        }
    }

    if (!controllo($prodotta !== null,
            'the component produced a notification',
            $prodotta ? 'id ' . $prodotta->id : 'no notification of type assign_due_soon')) {
        throw new \RuntimeException('without the notification the following checks would say nothing');
    }

    $notificaprodotta = (int) $prodotta->id;

    // -- the shape ------------------------------------------------------------------------------
    //
    // Not decoration: the event type is the key with which the delivery context looks up the channel
    // preference, and the custom data is what the next read matches on. A notification with the right
    // text and the wrong type is delivered nowhere and warned about twice.
    $datipropri = json_decode((string) $prodotta->customdata, true) ?: [];
    controllo(
        ((int) $prodotta->useridto === $userid)
            && ($prodotta->component === 'mod_assign')
            && ($prodotta->eventtype === \mod_assign\notification_helper::TYPE_DUE_SOON)
            && isset($datipropri['assignmentid'], $datipropri['duedate'])
            && ((int) $datipropri['assignmentid'] === $assignmentid),
        'it carries the expected recipient, type and custom data',
        sprintf('%s/%s, custom data %s',
            $prodotta->component, $prodotta->eventtype, $prodotta->customdata)
    );

    controllo(
        !empty($prodotta->subject) && !empty($prodotta->fullmessagehtml),
        'the text was composed by the component',
        substr((string) $prodotta->subject, 0, 60)
    );

    // -- the assertion that matters --------------------------------------------------------------
    $dopomonolite = (int) $DB->count_records_select('notifications',
        'useridto = :u AND eventtype = :e',
        ['u' => $userid, 'e' => \mod_assign\notification_helper::TYPE_DUE_SOON]);

    if ($remota) {
        controllo(
            $dopomonolite === $primamonolite,
            'in remote mode the monolith wrote nothing',
            sprintf('reminders in its table: %d before, %d after', $primamonolite, $dopomonolite)
        );
        controllo(
            !$DB->record_exists('notifications', ['id' => $notificaprodotta]),
            'the notification does not exist in the monolith table',
            'id ' . $notificaprodotta
        );
    } else {
        controllo(
            $dopomonolite === $primamonolite + 1,
            'in legacy mode the monolith wrote the notification',
            sprintf('reminders in its table: %d before, %d after', $primamonolite, $dopomonolite)
        );
    }

    // -- the second read: the same question, answered differently --------------------------------
    //
    // The component asks again who still needs warning: the person just warned must have gone.
    $ancoradaavvisare = \mod_assign\notification_helper::get_users_within_assignment(
        $assignmentid, \mod_assign\notification_helper::TYPE_DUE_SOON);

    controllo(
        !array_key_exists($userid, $ancoradaavvisare),
        'asked again, the component does not warn them a second time',
        sprintf('%d participants before, %d after', count($daavvisare), count($ancoradaavvisare))
    );

    // -- and the count, read through the context --------------------------------------------------
    // The read state of the notification is reported with the count: send_message() marks read at
    // once a notification for someone with no active channel, and the count rises only if it stays
    // unread.
    $dopservizio = $provider->count_unread_notifications($userid);
    $riletta = $provider->get_notification($notificaprodotta);

    if (is_null($riletta)) {
        controllo(false, 'the notification reads back from the context',
            'id ' . $notificaprodotta . ': does not read back');
    } else if (is_null($riletta->timeread)) {
        controllo(
            $dopservizio === $primaservizio + 1,
            'the unread count rose by one',
            sprintf('%d -> %d, notification unread', $primaservizio, $dopservizio)
        );
    } else {
        controllo(
            $dopservizio === $primaservizio,
            'recipient without active channels: the read mark was diverted too',
            sprintf('count steady at %d, timeread %d written in the context and not in the monolith',
                $dopservizio, $riletta->timeread)
        );
    }

} catch (\Throwable $eccezione) {
    cli_writeln('');
    cli_writeln('The test stopped: ' . $eccezione->getMessage());
} finally {
    // The site goes back as it was, whatever happened.
    $DB->set_field('assign', 'duedate', $scadenzaoriginale, ['id' => $assignmentid]);
    if ($cutoffcambiato) {
        $DB->set_field('assign', 'cutoffdate', $compito->cutoffdate, ['id' => $assignmentid]);
    }

    if ($notificaprodotta && !$options['keep']) {
        $provider->delete_notification($notificaprodotta);
    }

    cli_writeln('');
    cli_writeln('Restored: assignment due date'
        . ($notificaprodotta && !$options['keep'] ? ' and notification removed' : '')
        . ($options['keep'] ? '; the notification was kept (id ' . $notificaprodotta . ')' : ''));
}

exit(prospetto());
