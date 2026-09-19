#!/usr/bin/env python3
"""CPU consumption of the service replicas during a campaign, from `kubectl top pod` readings.

Collection on VM1 while the campaign runs, one line every five seconds:

    echo "epoch,pod,cpu" > /tmp/pod.csv
    while true; do t=$(date +%s); kubectl -n messaging top pod --no-headers \
        | awk -v t="$t" '{print t "," $1 "," $2}'; sleep 5; done >> /tmp/pod.csv

Usage:

    python3 cpu-usage.py results/<campaign> --pod-csv /tmp/pod.csv [--vm1-csv /tmp/vm1.csv]

The windows come from the .jtl files of each run; the dispersion is across runs, as in analyze.py.
--vm1-csv (columns epoch,core,usage) adds the usage of the VM1 cores during the M runs.
"""

import argparse
import csv
import statistics
import sys
from pathlib import Path


def read_pod(path, service):
    """Readings of one service only: (epoch, pod, millicores), sorted by time."""
    samples = []
    with path.open() as f:
        for row in csv.DictReader(f):
            if service not in row["pod"]:
                continue
            samples.append((int(row["epoch"]), row["pod"], int(row["cpu"].rstrip("m"))))
    samples.sort()
    return samples


def read_vm1(path):
    """Usage percentage of the VM1 cores, per core."""
    per_core = {}
    if path is None or not path.exists():
        return per_core
    with path.open() as f:
        for row in csv.DictReader(f):
            per_core.setdefault(int(row["core"]), []).append((int(row["epoch"]), float(row["usage"])))
    return per_core


def window(jtl):
    """First and last instant covered by a run, in seconds; the end includes the last
    sample's duration.
    """
    start = end = None
    with jtl.open() as f:
        for row in csv.DictReader(f):
            try:
                t = int(row["timeStamp"]) / 1000.0
                elapsed = float(row.get("elapsed") or 0) / 1000.0
            except (TypeError, ValueError, KeyError):
                continue
            start = t if start is None else min(start, t)
            end = t + elapsed if end is None else max(end, t + elapsed)
    return start, end


def summary(values):
    """Mean and sample standard deviation; with one run the deviation does not exist."""
    if not values:
        return None, None
    mean = statistics.fmean(values)
    dev = statistics.stdev(values) if len(values) > 1 else float("nan")
    return mean, dev


def analyze_service(folder, samples):
    """For every run of the S side: mean per replica and sum over the replicas."""
    per_replica, total, replicas, detail = [], [], set(), []

    for jtl in sorted((folder / "S").glob("*.jtl")):
        a, b = window(jtl)
        if a is None:
            continue
        inside = [(e, p, c) for e, p, c in samples if a <= e <= b]
        if not inside:
            detail.append((jtl.name, 0, None, None))
            continue

        replicas |= {p for _, p, _ in inside}
        mean_replica = statistics.fmean(c for _, _, c in inside)

        # Sum the replicas at each instant, then average over the instants.
        per_instant = {}
        for e, _, c in inside:
            per_instant[e] = per_instant.get(e, 0) + c
        mean_total = statistics.fmean(per_instant.values())

        per_replica.append(mean_replica)
        total.append(mean_total)
        detail.append((jtl.name, len(inside), mean_replica, mean_total))

    return replicas, per_replica, total, detail


def analyze_monolith(folder, per_core):
    """Usage of the VM1 cores during the runs of the M side."""
    result = {}
    for jtl in sorted((folder / "M").glob("*.jtl")):
        a, b = window(jtl)
        if a is None:
            continue
        for core, readings in per_core.items():
            inside = [u for e, u in readings if a <= e <= b]
            if inside:
                result.setdefault(core, []).append(statistics.fmean(inside))
    return result


def line(label, mean, dev, unit):
    if mean is None:
        return f"  {label:<34} --"
    if dev != dev:  # NaN
        return f"  {label:<34} {mean:7.1f} {unit}   (one run only)"
    return f"  {label:<34} {mean:7.1f} +/- {dev:.1f} {unit}"


def main():
    parser = argparse.ArgumentParser(description="CPU of the replicas during the runs of a campaign")
    parser.add_argument("campaigns", nargs="+", type=Path, help="Campaign folders (with an S/ subfolder)")
    parser.add_argument("--pod-csv", type=Path, required=True, help="kubectl top readings: epoch,pod,cpu")
    parser.add_argument("--vm1-csv", type=Path, help="VM1 core readings: epoch,core,usage (optional)")
    parser.add_argument("--service", default="conversation-service", help="Pod name substring")
    args = parser.parse_args()

    samples = read_pod(args.pod_csv, args.service)
    per_core = read_vm1(args.vm1_csv)
    if not samples:
        sys.exit(f"{args.pod_csv}: no reading of {args.service}.")

    print(f"Readings of {args.service} in {args.pod_csv}: {len(samples)}")
    print(f"Interval covered: {samples[0][0]} - {samples[-1][0]}\n")

    for folder in sorted(d for d in args.campaigns if (d / "S").is_dir()):
        replicas, per_replica, total, detail = analyze_service(folder, samples)
        print(f"=== {folder.name} ===")

        if not per_replica:
            print("  no reading inside the windows of this campaign\n")
            continue

        print(f"  replicas observed: {len(replicas)}")
        for name, count, mr, mt in detail:
            if mr is None:
                print(f"    {name:<10} no reading")
            else:
                print(f"    {name:<10} {count:4d} readings   per replica {mr:6.1f} m   total {mt:6.1f} m")

        m, s = summary(per_replica)
        print(line("mean CPU per replica", m, s, "m"))
        m, s = summary(total)
        print(line("total CPU", m, s, "m"))

        for core, means in sorted(analyze_monolith(folder, per_core).items()):
            m, s = summary(means)
            print(line(f"VM1 core {core}, during the M side", m, s, "%"))
        print()


if __name__ == "__main__":
    main()
