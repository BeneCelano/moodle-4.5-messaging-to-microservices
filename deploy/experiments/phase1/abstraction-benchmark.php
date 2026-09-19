<?php
// Cost of the abstraction layer, measured inside the process.
//
// A load test cannot answer this question: the layer adds, to every API call, one resolution of the
// implementation and one extra stack frame, that is microseconds, while the latency of an HTTP request
// writing to the database varies by tens of milliseconds between requests. Here there is no network,
// no database and no delivery: only the mechanics the Branch by Abstraction pattern introduces.
//
//   sudo -u www-data php abstraction-benchmark.php /var/www/moodle-4.5-branched [folder]
//
// With a folder as second argument it also writes benchmark.json there, which analyze.py reads.
//
// @package    core_message
// @copyright  2026 Benedetta Pia Celano
// @license    http://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later

define('CLI_SCRIPT', true);

$root = $argv[1] ?? null;
$outdir = $argv[2] ?? null;

if (is_null($root) || !is_dir($root)) {
    fwrite(STDERR, "Usage: php abstraction-benchmark.php /path/of/the/moodle/tree [folder]\n");
    exit(2);
}

if (!file_exists("$root/message/classes/conversation/provider_factory.php")) {
    fwrite(STDERR, "The given tree has no abstraction layer: the measure only makes sense on the branched tree.\n");
    exit(2);
}

require("$root/config.php");

/** @var int Iterations of each measure. */
const ITERATIONS = 200000;

/** @var int Estimated API calls per send request, used to project the cost. */
const API_CALLS_PER_REQUEST = 5;

/**
 * Trivial interface, to measure the cost of the extra stack frame alone.
 *
 * The real delegation does the same work as the monolith implementation: what the pattern adds is the
 * call of one extra method. An empty body isolates that cost from the work done anyway.
 */
interface benchmark_target {
    /**
     * Method with no body, whose only cost is the call itself.
     *
     * @param int $value Value returned untouched
     * @return int
     */
    public function passthrough(int $value): int;
}

/**
 * Implementation returning its own argument.
 *
 * @package core_message
 */
class benchmark_implementation implements benchmark_target {
    #[\Override]
    public function passthrough(int $value): int {
        return $value;
    }
}

/**
 * Mean time per iteration of a function, in nanoseconds, net of the empty loop.
 *
 * @param callable $subject Operation to measure
 * @param int $iterations Number of repetitions
 * @return float Nanoseconds per iteration
 */
function measure(callable $subject, int $iterations): float {
    // Warm-up: the first run pays symbol resolution and compilation, which belong to start-up.
    for ($i = 0; $i < 1000; $i++) {
        $subject();
    }

    $empty = hrtime(true);
    for ($i = 0; $i < $iterations; $i++) {
        // Empty loop, subtracted below.
    }
    $emptyelapsed = hrtime(true) - $empty;

    $start = hrtime(true);
    for ($i = 0; $i < $iterations; $i++) {
        $subject();
    }
    $elapsed = hrtime(true) - $start;

    return max(0.0, ($elapsed - $emptyelapsed) / $iterations);
}

$mode = $CFG->conversationservice_mode ?? 'legacy';

echo "Cost of the abstraction layer\n";
echo "Tree:       $root\n";
echo "Mode:       $mode\n";
echo "Iterations: " . number_format(ITERATIONS, 0, '.', ',') . "\n\n";

if ($mode !== 'legacy') {
    echo "Warning: phase 1 is measured in legacy mode. The number below does not belong to it.\n\n";
}

$implementation = new benchmark_implementation();

$factory = measure(function() {
    \core_message\conversation\provider_factory::get(2);
}, ITERATIONS);

$dispatch = measure(function() use ($implementation) {
    $implementation->passthrough(1);
}, ITERATIONS);

$percall = $factory + $dispatch;
$perrequest = $percall * API_CALLS_PER_REQUEST;
$perrequestms = $perrequest / 1000000.0;

printf("  %-46s %9.1f ns\n", "resolution of the implementation", $factory);
printf("  %-46s %9.1f ns\n", "stack frame of the delegation", $dispatch);
printf("  %-46s %9.1f ns\n", "total per API call", $percall);
echo "\n";
printf("  %-46s %9.1f ns\n", "per request, with " . API_CALLS_PER_REQUEST . " calls", $perrequest);
printf("  %-46s %9.6f ms\n", "in milliseconds", $perrequestms);
echo "\n";

// The number is reported, not compared with a threshold.
printf("The cost is %.0f times smaller than a millisecond.\n", 1.0 / $perrequestms);

// The number also goes to disk, next to the campaign, where analyze.py looks for it.
if (!is_null($outdir)) {
    if (!is_dir($outdir)) {
        fwrite(STDERR, "The folder $outdir does not exist: the table was printed, not saved.\n");
        exit(2);
    }

    $path = rtrim($outdir, '/') . '/benchmark.json';
    $written = file_put_contents($path, json_encode([
        'per_call_ns' => $percall,
        'per_request_ns' => $perrequest,
        'per_request_ms' => $perrequestms,
        'api_calls_per_request' => API_CALLS_PER_REQUEST,
        'iterations' => ITERATIONS,
        'mode' => $mode,
        'tree' => $root,
        'measured_at' => date('c'),
    ], JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES) . "\n");

    if ($written === false) {
        fwrite(STDERR, "Could not write $path.\n");
        exit(2);
    }
    echo "\nWritten $path\n";
}

exit(0);
