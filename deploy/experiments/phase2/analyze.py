"""Analysis of the phase 2 comparison campaign."""

from __future__ import annotations

import argparse
import csv
import math
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path

TARGETS = ("M", "S")

TARGET_LABELS = {
    "M": "Monolith, core_message_send_instant_messages",
    "S": "ConversationService, POST /messages/instant",
}

MAX_ERROR_RATE = 0.01
"""Absolute error rate above which a side is not considered working under load."""

SIGMA_FACTOR = 2.0
"""Width, in standard deviations, beyond which a difference is considered distinguishable."""


@dataclass
class RunResult:
    """Metrics of a single run."""

    name: str
    samples: int
    errors: int
    apdex: float
    p95_ms: float
    p99_ms: float
    throughput: float

    @property
    def error_rate(self) -> float:
        """Share of failed samples."""
        return self.errors / self.samples if self.samples else 0.0


@dataclass
class TargetResult:
    """Aggregate metrics of a target."""

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
    """Percentile with the nearest-rank method, the same the load generator and the
    phase 1 analysis use.
    """
    if not sorted_values:
        return 0.0
    rank = max(1, math.ceil(p / 100.0 * len(sorted_values)))
    return sorted_values[rank - 1]


def read_run(path: Path, apdex_t_ms: float, apdex_f_ms: float) -> RunResult:
    """Compute the metrics of a results file."""
    elapsed: list[float] = []
    errors = 0
    first_start: float | None = None
    last_end: float | None = None

    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or "elapsed" not in reader.fieldnames:
            raise ValueError(f"{path}: does not look like a JMeter results file with a header.")

        for row in reader:
            value = float(row["elapsed"])
            elapsed.append(value)
            if row.get("success", "true").strip().lower() != "true":
                errors += 1

            # Throughput on the window the run really occupied, not on the nominal
            # duration.
            start = float(row["timeStamp"])
            end = start + value
            first_start = start if first_start is None else min(first_start, start)
            last_end = end if last_end is None else max(last_end, end)

    if not elapsed:
        raise ValueError(f"{path}: no sample.")

    satisfied = sum(1 for value in elapsed if value <= apdex_t_ms)
    tolerating = sum(1 for value in elapsed if apdex_t_ms < value <= apdex_f_ms)

    window_s = ((last_end or 0.0) - (first_start or 0.0)) / 1000.0
    throughput = len(elapsed) / window_s if window_s > 0 else 0.0

    elapsed.sort()

    return RunResult(
        name=path.stem,
        samples=len(elapsed),
        errors=errors,
        apdex=(satisfied + tolerating / 2.0) / len(elapsed),
        p95_ms=percentile(elapsed, 95.0),
        p99_ms=percentile(elapsed, 99.0),
        throughput=throughput,
    )


def read_campaign(directory: Path, apdex_t_ms: float, apdex_f_ms: float) -> dict[str, TargetResult]:
    """Read every run of a campaign."""
    results: dict[str, TargetResult] = {}

    for target in TARGETS:
        target_dir = directory / target
        if not target_dir.is_dir():
            continue

        result = TargetResult(name=target)
        for path in sorted(target_dir.glob("*.jtl")):
            result.runs.append(read_run(path, apdex_t_ms, apdex_f_ms))

        if result.runs:
            results[target] = result

    return results


def format_table(results: dict[str, TargetResult]) -> str:
    """Compose the table of the metrics per target."""
    # Plain ASCII output: read on consoles that may not render UTF-8.
    lines = [
        f"{'':<3} {'run':>4} {'samples':>9} {'Apdex':>18} {'p95 (ms)':>18}"
        f" {'p99 (ms)':>18} {'req/s':>16} {'errors':>9}",
        "-" * 103,
    ]

    for target in TARGETS:
        result = results.get(target)
        if result is None:
            lines.append(f"{target:<3} {'-':>4}  (no result)")
            continue

        lines.append(
            f"{target:<3} {len(result.runs):>4} {int(result.mean('samples')):>9}"
            f" {result.mean('apdex'):>9.4f} +/-{result.stdev('apdex'):<6.4f}"
            f" {result.mean('p95_ms'):>9.1f} +/-{result.stdev('p95_ms'):<6.1f}"
            f" {result.mean('p99_ms'):>9.1f} +/-{result.stdev('p99_ms'):<6.1f}"
            f" {result.mean('throughput'):>8.1f} +/-{result.stdev('throughput'):<5.1f}"
            f" {result.mean('error_rate') * 100:>8.3f}%"
        )

    lines.append("")
    for target in TARGETS:
        if target in results:
            lines.append(f"{target}  {TARGET_LABELS[target]}")

    return "\n".join(lines)


def format_comparison(results: dict[str, TargetResult]) -> str:
    """Compose the comparison between the two targets, with its uncertainty."""
    mono, svc = results["M"], results["S"]
    lines = ["", "Comparison", "-" * 84]

    # With one run there is no dispersion: recognized by counting the runs.
    runs = min(len(mono.runs), len(svc.runs))

    for metric, label, unit in (
        ("p95_ms", "p95 latency", "ms"),
        ("p99_ms", "p99 latency", "ms"),
        ("throughput", "throughput", "req/s"),
    ):
        delta = svc.mean(metric) - mono.mean(metric)
        # The two deviations combine in quadrature (independent runs).
        spread = math.hypot(mono.stdev(metric), svc.stdev(metric))
        distinguishable = abs(delta) > SIGMA_FACTOR * spread

        if runs < 2:
            verdict = "uncertainty not estimable, fewer than two runs per target"
        else:
            verdict = "distinguishable" if distinguishable else "within the noise"

        lines.append(
            f"  {label:<14} service - monolith = {delta:+9.1f} {unit:<6}"
            f" (+/-{SIGMA_FACTOR * spread:.1f}, {verdict})"
        )

    # The comparison is reported as a ratio and as a percentage.
    for metric, label in (("p95_ms", "p95"), ("p99_ms", "p99")):
        if mono.mean(metric) > 0:
            gain = (mono.mean(metric) - svc.mean(metric)) / mono.mean(metric) * 100
            lines.append(f"  {'improvement':<14} {label}: {gain:+.1f}%")

    if mono.mean("throughput") > 0:
        ratio = svc.mean("throughput") / mono.mean("throughput")
        lines.append(f"  {'ratio':<14} throughput service / monolith = {ratio:.2f}x")

    return "\n".join(lines)


def evaluate(results: dict[str, TargetResult]) -> tuple[bool, list[str]]:
    """Apply the phase 2 completion criteria and return outcome and reasons."""
    missing = [target for target in TARGETS if target not in results]
    if missing:
        return False, [f"Targets missing from the campaign: {', '.join(missing)}."]

    mono, svc = results["M"], results["S"]
    lines: list[str] = []
    passed = True

    # No error under load, on both sides.
    for name, result in (("monolith", mono), ("service", svc)):
        rate = result.mean("error_rate")
        ok = rate <= MAX_ERROR_RATE
        passed = passed and ok
        lines.append(
            f"Errors {name}: {rate * 100:.3f}% "
            f"({'within' if ok else 'beyond'} the threshold of {MAX_ERROR_RATE * 100:.1f}%)"
        )

    # The error rate is read together with the number of samples.
    if int(mono.mean("samples")) != int(svc.mean("samples")):
        lines.append(
            f"   WARNING: different sample counts on the two sides "
            f"({int(mono.mean('samples'))} against {int(svc.mean('samples'))}). "
            f"The two loads were not equivalent."
        )
        passed = False

    lines.append("")

    # The service sustains at least the monolith's load.
    delta = svc.mean("throughput") - mono.mean("throughput")
    spread = math.hypot(mono.stdev("throughput"), svc.stdev("throughput"))
    # Equal throughput within the noise meets the criterion.
    ok = delta >= -SIGMA_FACTOR * spread
    passed = passed and ok
    lines.append(
        f"Service throughput against the monolith: {delta:+.1f} req/s "
        f"(+/-{SIGMA_FACTOR * spread:.1f}) -> {'satisfied' if ok else 'not satisfied'}"
    )

    return passed, lines


def main() -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("campaign", type=Path, help="Folder produced by run-campaign.sh")
    parser.add_argument("--apdex-t", type=float, default=500.0, help="T threshold in milliseconds")
    parser.add_argument("--apdex-f", type=float, default=1500.0, help="F threshold in milliseconds")
    args = parser.parse_args()

    if not args.campaign.is_dir():
        print(f"Folder does not exist: {args.campaign}", file=sys.stderr)
        return 2

    results = read_campaign(args.campaign, args.apdex_t, args.apdex_f)
    if not results:
        print(f"No result files under {args.campaign}", file=sys.stderr)
        return 2

    print(f"Campaign: {args.campaign}")
    print(f"Apdex with T={args.apdex_t:.0f} ms, F={args.apdex_f:.0f} ms")
    print("")
    print(format_table(results))

    if len(results) == len(TARGETS):
        print(format_comparison(results))

    print("")
    print("What this campaign establishes")
    print("-" * 84)
    passed, lines = evaluate(results)
    for line in lines:
        print(line)

    print("")
    print("")
    print("Functional correctness and data integrity are not measured under load: the other")
    print("checks establish them, each with its own outcome.")
    print("")
    print(f"Outcome: {'satisfied' if passed else 'NOT satisfied'}, within what the campaign can measure.")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
