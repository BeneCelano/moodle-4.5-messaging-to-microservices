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

namespace core_message\cli;

/**
 * The apparatus that compares two implementations of the same contract.
 *
 * @package    core_message
 * @copyright  2026 Benedetta Pia Celano
 * @license    http://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class comparison {

    /** @var string[] Fields holding an identifier each store assigns for itself. */
    protected $storeassigned;

    /** @var string[] Fields holding the moment a row was written. */
    protected $writtenat;

    /** @var int How many differences of one probe are printed before the rest are summarised. */
    const SHOWN_PER_PROBE = 5;

    /**
     * Constructor.
     *
     * @param string[] $storeassigned Fields holding an identifier each store assigns for itself
     * @param string[] $writtenat Fields holding the moment a row was written
     */
    public function __construct(array $storeassigned, array $writtenat) {
        $this->storeassigned = $storeassigned;
        $this->writtenat = $writtenat;
    }

    /**
     * Reduces an answer to the form in which the two implementations can be compared.
     *
     * @param mixed $value
     * @return mixed
     */
    public static function normalise($value) {
        if (is_object($value)) {
            $value = (array) $value;
        }

        if (is_array($value)) {
            $normalised = [];
            foreach ($value as $key => $item) {
                $normalised[$key] = self::normalise($item);
            }
            // Only the keys of a map are sorted. A list keeps the order it was given.
            if (!array_is_list($normalised)) {
                ksort($normalised);
            }
            return $normalised;
        }

        if (is_bool($value)) {
            return $value ? 1 : 0;
        }

        if (is_numeric($value)) {
            return 0 + $value;
        }

        return $value;
    }

    /**
     * Replaces every identifier a store assigned with the order in which it first appears.
     *
     * @param mixed $value A normalised answer
     * @param array $seen Identifiers already given a position, carried across the whole answer
     * @param string $key The field the value was found under
     * @return mixed
     */
    public function canonicalise($value, array &$seen, string $key = '') {
        if (is_array($value)) {
            $records = !array_is_list($value);

            $canonical = [];
            foreach ($value as $itemkey => $item) {
                $canonicalitem = $this->canonicalise($item, $seen, (string) $itemkey);

                // Map keys that are store-assigned identifiers are substituted too; lists are left
                // alone.
                if ($records && is_int($itemkey)) {
                    $canonical[$this->canonicalise($itemkey, $seen, 'id')] = $canonicalitem;
                    continue;
                }

                $canonical[$itemkey] = $canonicalitem;
            }

            return $canonical;
        }

        // Zero (a `false` normalised) is not an identifier and gets no position.
        if (in_array($key, $this->storeassigned, true) && is_int($value) && $value > 0) {
            if (!array_key_exists($value, $seen)) {
                $seen[$value] = '#' . count($seen);
            }
            return $seen[$value];
        }

        // Timestamps are substituted, not removed.
        if (in_array($key, $this->writtenat, true)) {
            return is_null($value) ? null : '<written>';
        }

        return $value;
    }

    /**
     * Renders a value compactly enough to sit on one line of the report.
     *
     * @param mixed $value
     * @return string
     */
    public static function describe($value): string {
        if (is_array($value)) {
            return sprintf('a structure of %d %s', count($value),
                count($value) === 1 ? 'entry' : 'entries');
        }

        $rendered = var_export($value, true);

        return strlen($rendered) > 160 ? substr($rendered, 0, 157) . '...' : $rendered;
    }

    /**
     * Lists the places where two normalised answers differ.
     *
     * @param mixed $expected The answer of the monolithic implementation
     * @param mixed $actual The answer of the service implementation
     * @param string $path Where in the structure the comparison currently is
     * @return string[] One line per difference, empty when the two agree
     */
    public static function differences($expected, $actual, string $path = ''): array {
        $where = $path === '' ? '' : $path . ': ';

        if (is_array($expected) !== is_array($actual)) {
            // Both sides are shown.
            return [$where . sprintf('%s != %s', self::describe($expected), self::describe($actual))];
        }

        if (!is_array($expected)) {
            if ($expected === $actual) {
                return [];
            }
            return [$where . sprintf('%s != %s', var_export($expected, true), var_export($actual, true))];
        }

        $found = [];
        foreach (array_diff(array_keys($expected), array_keys($actual)) as $key) {
            $found[] = $where . "missing key '$key'";
        }
        foreach (array_diff(array_keys($actual), array_keys($expected)) as $key) {
            $found[] = $where . "unexpected key '$key'";
        }

        foreach ($expected as $key => $item) {
            if (array_key_exists($key, $actual)) {
                $found = array_merge($found, self::differences($item, $actual[$key],
                    $path === '' ? (string) $key : $path . '.' . $key));
            }
        }

        return $found;
    }

    /**
     * Asks one question of one implementation, turning a failure into a comparable answer.
     *
     * @param callable $probe
     * @param object $implementation
     * @return mixed The answer, or a description of the failure
     */
    public static function ask(callable $probe, object $implementation) {
        try {
            return $probe($implementation);
        } catch (\Throwable $e) {
            // debuginfo holds the actual failure of a moodle_exception.
            $detail = ($e instanceof \moodle_exception && !empty($e->debuginfo))
                ? $e->debuginfo : $e->getMessage();

            return 'THREW ' . get_class($e) . ': ' . $detail;
        }
    }

    /**
     * Runs a set of probes against both implementations and reports where they disagreed.
     *
     * @param callable[] $probes Indexed by the name the report uses
     * @param object $legacy The monolithic implementation
     * @param object $remote The service implementation
     * @param bool $canonicalise Whether store-assigned identifiers must be reduced to their position
     * @param bool $verbose Whether to print the probes that agreed
     * @return int How many probes diverged
     */
    public function run_probes(array $probes, object $legacy, object $remote, bool $canonicalise,
            bool $verbose): int {
        $diverged = 0;

        foreach ($probes as $name => $probe) {
            $expected = self::normalise(self::ask($probe, $legacy));
            $actual = self::normalise(self::ask($probe, $remote));

            if ($canonicalise) {
                // Each answer gets its own positions.
                $expectedseen = [];
                $actualseen = [];
                $expected = $this->canonicalise($expected, $expectedseen);
                $actual = $this->canonicalise($actual, $actualseen);
            }

            $found = self::differences($expected, $actual);

            if (empty($found)) {
                if ($verbose) {
                    cli_writeln("  ok    $name");
                }
                continue;
            }

            $diverged++;
            cli_writeln("  DIFF  $name");
            foreach (array_slice($found, 0, self::SHOWN_PER_PROBE) as $line) {
                cli_writeln("          $line");
            }
            if (count($found) > self::SHOWN_PER_PROBE) {
                cli_writeln(sprintf('          ... and %d more',
                    count($found) - self::SHOWN_PER_PROBE));
            }
        }

        return $diverged;
    }
}
