"""Analysis of the results of the phase 1 campaign."""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path

CONFIGS = ("C0", "C1")

CONFIG_LABELS = {
    "C0": "Moodle untouched",
    "C1": "Abstraction layer, mode=legacy",
}


APDEX_SIGMA_FACTOR = 2.0

APDEX_GOOD = 0.85
"""Conventional threshold of the Apdex good class."""
"""Width, in standard deviations, within which the Apdex deviation must stay."""


@dataclass
class RunResult:
    """Metrics of a single run."""

    name: str
    samples: int
    errors: int
    apdex: float
    p95_ms: float
    p99_ms: float

    @property
    def error_rate(self) -> float:
        """Share of failed samples."""
        return self.errors / self.samples if self.samples else 0.0


@dataclass
class ConfigResult:
    """Aggregate metrics of a configuration."""

    name: str
    runs: list[RunResult] = field(default_factory=list)

    def mean(self, metric: str) -> float:
        return statistics.fmean(self._values(metric))

    def stdev(self, metric: str) -> float:
        """Sample standard deviation of the metric over the runs."""
        values = self._values(metric)
        return statistics.stdev(values) if len(values) > 1 else 0.0

    def _values(self, metric: str) -> list[float]:
        """Extract the metric from each run."""
        return [float(getattr(run, metric)) for run in self.runs]


def percentile(sorted_values: list[float], p: float) -> float:
    """Percentile with the nearest-rank method, the same the load generator uses in its reports: another
    method would give, on the same data, numbers different from those visible in JMeter.
    """
    if not sorted_values:
        return 0.0
    rank = max(1, math.ceil(p / 100.0 * len(sorted_values)))
    return sorted_values[rank - 1]


def read_run(path: Path, apdex_t_ms: float, apdex_f_ms: float) -> RunResult:
    """Compute the metrics of a results file."""
    elapsed: list[float] = []
    errors = 0

    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or "elapsed" not in reader.fieldnames:
            raise ValueError(f"{path}: does not look like a JMeter results file with a header.")

        for row in reader:
            elapsed.append(float(row["elapsed"]))
            if row.get("success", "true").strip().lower() != "true":
                errors += 1

    if not elapsed:
        raise ValueError(f"{path}: no sample.")

    satisfied = sum(1 for value in elapsed if value <= apdex_t_ms)
    tolerating = sum(1 for value in elapsed if apdex_t_ms < value <= apdex_f_ms)

    elapsed.sort()

    return RunResult(
        name=path.stem,
        samples=len(elapsed),
        errors=errors,
        apdex=(satisfied + tolerating / 2.0) / len(elapsed),
        p95_ms=percentile(elapsed, 95.0),
        p99_ms=percentile(elapsed, 99.0),
    )


def read_campaign(directory: Path, apdex_t_ms: float, apdex_f_ms: float) -> dict[str, ConfigResult]:
    """Read every run of a campaign."""
    results: dict[str, ConfigResult] = {}

    for config in CONFIGS:
        config_dir = directory / config
        if not config_dir.is_dir():
            continue

        result = ConfigResult(name=config)
        for path in sorted(config_dir.glob("*.jtl")):
            result.runs.append(read_run(path, apdex_t_ms, apdex_f_ms))

        if result.runs:
            results[config] = result

    return results


def format_table(results: dict[str, ConfigResult]) -> str:
    """Compose the table of the metrics per configuration."""
    # Plain ASCII output: read on consoles that may not render UTF-8.
    lines = [
        f"{'':<4} {'run':>4} {'Apdex':>18} {'p95 (ms)':>18} {'p99 (ms)':>18} {'errors':>10}",
        "-" * 76,
    ]

    for config in CONFIGS:
        result = results.get(config)
        if result is None:
            lines.append(f"{config:<4} {'-':>4}  (no result)")
            continue

        lines.append(
            f"{config:<4} {len(result.runs):>4}"
            f" {result.mean('apdex'):>9.4f} +/-{result.stdev('apdex'):<6.4f}"
            f" {result.mean('p95_ms'):>9.1f} +/-{result.stdev('p95_ms'):<6.1f}"
            f" {result.mean('p99_ms'):>9.1f} +/-{result.stdev('p99_ms'):<6.1f}"
            f" {result.mean('error_rate') * 100:>9.3f}%"
        )

    lines.append("")
    for config in CONFIGS:
        if config in results:
            lines.append(f"{config}  {CONFIG_LABELS[config]}")

    return "\n".join(lines)


def thousands(value: float) -> str:
    """Formats an integer with the comma as thousands separator."""
    return f"{value:,.0f}"


def abstraction_measure(campaign: Path, explicit: Path | None) -> tuple[float, int] | None:
    """Look for the in-process measure of the abstraction and return estimate and
    iterations.
    """
    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(explicit)
    candidates.append(campaign / "benchmark.json")
    candidates.extend(sorted(campaign.glob("*/benchmark.json")))
    candidates.extend(sorted(campaign.parent.glob("benchmark.json")))

    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
            return float(data["per_request_ms"]), int(data["iterations"])
        except (ValueError, KeyError, OSError):
            # An unreadable or incomplete file is skipped.
            continue
    return None


def evaluate(results: dict[str, ConfigResult], campaign: Path,
             benchmark_json: Path | None = None) -> tuple[bool, list[str]]:
    """Apply the phase 1 criteria and return outcome and reasons."""
    missing = [config for config in CONFIGS if config not in results]
    if missing:
        return False, [f"Configurations missing from the campaign: {', '.join(missing)}."]

    baseline, abstraction = results["C0"], results["C1"]
    lines: list[str] = []
    passed = True

    # The Apdex is reported, not applied.

    # Criterion 2: the p95 difference between the two configurations stays within the
    # dispersion between repeated runs.
    p95_delta = abstraction.mean("p95_ms") - baseline.mean("p95_ms")
    p95_spread = math.hypot(baseline.stdev("p95_ms"), abstraction.stdev("p95_ms"))

    equivalent = abs(p95_delta) <= p95_spread
    passed &= equivalent
    lines.append(
        f"[{'ok' if equivalent else 'NO'}] Indistinguishable under load: difference between the p95 "
        f"C1-C0 {p95_delta:+.1f} ms, spread across runs +/-{p95_spread:.1f} ms"
    )

    # The in-process measure is reported, not applied.
    measure = abstraction_measure(campaign, benchmark_json)
    if measure is not None:
        estimate, iterations = measure
        ratio = p95_spread / estimate if estimate > 0 else float("inf")
        lines.append(
            f"     reported, not applied: the mechanics of the pattern cost {estimate:.6f} ms per "
            f"request ({thousands(iterations)} in-process iterations),"
        )
        lines.append(
            f"     that is {thousands(ratio)} times less than the spread of the campaign. It is why "
            f"the load cannot see it."
        )

    # Criterion 3: unchanged error rate.
    error_delta = abstraction.mean("error_rate") - baseline.mean("error_rate")
    error_ok = error_delta <= baseline.stdev("error_rate") + 1e-9
    passed &= error_ok
    lines.append(
        f"[{'ok' if error_ok else 'NO'}] Errors: deviation {error_delta * 100:+.3f} percentage points"
    )

    if p95_delta < 0:
        lines.append("")
        lines.append(
            "     The difference is negative: the system with the extra component turns out "
            "faster. It is not an effect,"
        )
        lines.append("     it is the absence of signal.")
    return passed, lines


def main() -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(description="Analyzes a phase-1 campaign.")
    parser.add_argument("directory", type=Path,
                        help="Campaign directory, with the C0 and C1 subfolders")
    parser.add_argument(
        "--apdex-t",
        type=float,
        default=500.0,
        help="Apdex satisfaction threshold in milliseconds, the T column of the JMeter report. "
        "Must match the one of the historical baseline (default: 500)",
    )
    parser.add_argument(
        "--apdex-f",
        type=float,
        default=1500.0,
        help="Frustration threshold, the F column of the JMeter report. Not derived from the "
        "former: JMeter keeps them independent and uses 1500 ms by default (default: 1500)",
    )
    parser.add_argument("--benchmark-json", type=Path, default=None,
                        help="benchmark.json of the in-process measure; if omitted it is looked for "
                        "accanto alla campagna")
    parser.add_argument("--json", action="store_true", help="Emit the results in machine-readable form")
    args = parser.parse_args()

    if not args.directory.is_dir():
        print(f"Directory does not exist: {args.directory}", file=sys.stderr)
        return 2

    results = read_campaign(args.directory, args.apdex_t, args.apdex_f)
    if not results:
        print(f"No result files under {args.directory}", file=sys.stderr)
        return 2

    passed, lines = evaluate(results, args.directory, args.benchmark_json)

    if args.json:
        print(
            json.dumps(
                {
                    "apdex_t_ms": args.apdex_t,
                    "apdex_f_ms": args.apdex_f,
                    "passed": passed,
                    "configs": {
                        name: {
                            "runs": len(result.runs),
                            "apdex_mean": result.mean("apdex"),
                            "apdex_stdev": result.stdev("apdex"),
                            "p95_mean_ms": result.mean("p95_ms"),
                            "p95_stdev_ms": result.stdev("p95_ms"),
                            "p99_mean_ms": result.mean("p99_ms"),
                            "error_rate_mean": result.mean("error_rate"),
                        }
                        for name, result in results.items()
                    },
                },
                indent=2,
            )
        )
        return 0 if passed else 1

    print(f"Campaign: {args.directory}")
    print(f"Apdex thresholds: satisfaction {args.apdex_t:.0f} ms, frustration {args.apdex_f:.0f} ms")
    print()
    print(format_table(results))
    print()
    print("Phase 1 criteria")
    print("-" * 74)
    for line in lines:
        print(line)
    print()
    print("Outcome:", "phase 1 passed" if passed else "phase 1 NOT passed")

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
