#!/usr/bin/env bash
#
# Find the concurrency at which to run the phase 1 campaign: configuration C0 only, at growing
# concurrencies, reporting latency and error rate for each.
#
#   bash calibrate.sh [levels...]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ ! -f "$SCRIPT_DIR/config.env" ]]; then
    echo "config.env missing: copy config.env.example and adapt it." >&2
    exit 1
fi

# shellcheck disable=SC1091
source "$SCRIPT_DIR/config.env"

LEVELS="${*:-10 25 50 100 200}"

STAMP="$(date +%Y%m%d-%H%M%S)"
OUTDIR="$RESULTS_DIR/calibrazione-$STAMP"
mkdir -p "$OUTDIR"

on() {
    ssh -p "${SSH_PORT:-22}" "$SSH_USER@$1" "${@:2}"
}

url="$URL_DIRECT"
rest="${url#http://}"
hostport="${rest%%/*}"
basepath="/"
[[ "$rest" == */* ]] && basepath="/${rest#*/}"
host="${hostport%%:*}"
port=80
[[ "$hostport" == *:* ]] && port="${hostport##*:}"

echo "==> Calibration $STAMP on configuration C0, concurrencies: $LEVELS"

# The configuration stays C0 for the whole calibration.
on "$VM1_HOST" "sudo bash $SCRIPT_DIR_VM1/switch-config.sh C0"

for threads in $LEVELS; do
    echo ""
    echo "==> $threads threads"

    # Restore before every level.
    on "$VM2_HOST" "bash $SCRIPT_DIR_VM2/reset-db.sh" >/dev/null

    remote_jtl="/tmp/phase1-cal-$threads.jtl"
    on "$VM3_HOST" \
        "$JMETER_BIN -n -t $JMX_PATH -l $remote_jtl \
         -Jhost=$host -Jport=$port -Jbasepath=$basepath -Jthreads=$threads" >/dev/null

    scp -q -P "${SSH_PORT:-22}" "$SSH_USER@$VM3_HOST:$remote_jtl" "$OUTDIR/threads-$threads.jtl"
    on "$VM3_HOST" "rm -f $remote_jtl"
done

echo ""
echo "==> Outcome"
echo ""

python3 - "$OUTDIR" "$APDEX_T_MS" "$APDEX_F_MS" "$SCRIPT_DIR" <<'PY'
"""Summarise the measured levels reusing the computation of the campaign."""

import sys
from pathlib import Path

outdir, apdex_t, apdex_f, script_dir = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), sys.argv[4]
sys.path.insert(0, script_dir)

from analyze import read_run  # noqa: E402

print(f"{'threads':>8} {'samples':>9} {'p50 (ms)':>10} {'p95 (ms)':>10} {'p99 (ms)':>10} {'Apdex':>8} {'errors':>8}")
print("-" * 68)

for path in sorted(Path(outdir).glob("*.jtl"), key=lambda p: int(p.stem.split("-")[1])):
    result = read_run(path, apdex_t, apdex_f)
    threads = path.stem.split("-")[1]
    # The median is reported too.
    values = sorted(float(line.split(",")[1]) for line in path.read_text().splitlines()[1:] if line)
    p50 = values[len(values) // 2] if values else 0.0
    print(
        f"{threads:>8} {result.samples:>9} {p50:>10.0f} {result.p95_ms:>10.0f} {result.p99_ms:>10.0f}"
        f" {result.apdex:>8.4f} {result.error_rate * 100:>7.2f}%"
    )

print()
print("Pick the level with a p95 in the order of hundreds of milliseconds, zero errors and an")
print("Apdex below 1 but above about 0.95.")
print()
print("The three conditions serve different purposes. A p95 of that order makes the 15 ms of the")
print("criterion a share of a few percent, a sensible budget instead of an imperceptible fraction.")
print("Zero errors exclude that a limit imposed by some timeout is already being measured. An Apdex")
print("below 1 is the condition for that metric to discriminate: if every response is under the")
print("satisfaction threshold, the Apdex is 1.0000 in every configuration with zero standard")
print("deviation, and the criterion using it degenerates.")
PY

echo ""
echo "Raw results in $OUTDIR"
