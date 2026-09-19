#!/usr/bin/env bash
#
# Run the phase 1 measuring matrix: two configurations, N repetitions, order rotated at every
# repetition. To run from any machine that reaches VM1, VM2 and VM3 via ssh.
#
#   bash run-campaign.sh [--runs N] [--dry-run]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ ! -f "$SCRIPT_DIR/config.env" ]]; then
    echo "config.env missing: copy config.env.example and adapt it." >&2
    exit 1
fi

# shellcheck disable=SC1091
source "$SCRIPT_DIR/config.env"

RUNS=5
DRY_RUN=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --runs)    RUNS="$2"; shift 2 ;;
        --dry-run) DRY_RUN=true; shift ;;
        *) echo "Usage: $0 [--runs N] [--dry-run]" >&2; exit 1 ;;
    esac
done

if (( RUNS < 3 )); then
    # At least three repetitions, for the standard deviation.
    echo "At least 3 repetitions are needed for the standard deviation to mean anything." >&2
    exit 1
fi

CONFIGS=(C0 C1)
STAMP="$(date +%Y%m%d-%H%M%S)"
CAMPAIGN="$RESULTS_DIR/$STAMP"

run_remote() {
    local host="$1"; shift
    if [[ "$DRY_RUN" == true ]]; then
        echo "    [dry-run] ssh -p ${SSH_PORT:-22} $SSH_USER@$host $*"
        return 0
    fi
    ssh -p "${SSH_PORT:-22}" "$SSH_USER@$host" "$@"
}

# The two configurations share the address.
url_for() {
    echo "$URL_DIRECT"
}

# Split the address into host, port and base path.
url_parts() {
    local rest="${1#http://}"
    local hostport="${rest%%/*}"
    local path="/"
    local host="${hostport%%:*}"
    local port=80

    [[ "$rest" == */* ]] && path="/${rest#*/}"
    [[ "$hostport" == *:* ]] && port="${hostport##*:}"

    echo "$host $port $path"
}

mkdir -p "$CAMPAIGN"
echo "==> Campaign $STAMP, $RUNS repetitions per configuration"

{
    echo "campaign=$STAMP"
    echo "runs=$RUNS"
    echo "jmx=$JMX_PATH"
    echo "apdex_t_ms=$APDEX_T_MS"
    echo "apdex_f_ms=$APDEX_F_MS"
    echo "started_at=$(date -Is)"
} > "$CAMPAIGN/campaign.env"

for (( r = 1; r <= RUNS; r++ )); do
    # Rotation of the order: at repetition r the sequence starts from the r-th configuration.
    for (( i = 0; i < 3; i++ )); do
        cfg="${CONFIGS[$(( (i + r - 1) % 3 ))]}"
        url="$(url_for "$cfg")"
        read -r host port basepath <<< "$(url_parts "$url")"

        echo ""
        echo "==> Repetition $r/$RUNS, configuration $cfg ($url)"

        # The scripts are invoked with the path they have on the remote machine.
        run_remote "$VM1_HOST" "sudo bash $SCRIPT_DIR_VM1/switch-config.sh $cfg"

        # Warm-up of the bytecode cache, emptied by the PHP-FPM restart.
        echo "    warm-up (${WARMUP_THREADS} threads, discarded)"
        run_remote "$VM3_HOST" \
            "$JMETER_BIN -n -t $JMX_PATH -l /dev/null \
             -Jhost=$host -Jport=$port -Jbasepath=$basepath -Jwstoken=${WS_TOKEN:-} -Jthreads=$WARMUP_THREADS >/dev/null 2>&1 || true"

        # The restore comes after the warm-up: the warm-up writes messages.
        run_remote "$VM2_HOST" "bash $SCRIPT_DIR_VM2/reset-db.sh"

        outdir="$CAMPAIGN/$cfg"
        mkdir -p "$outdir"
        remote_jtl="/tmp/phase1-$cfg-run$r.jtl"

        echo "    measure"
        run_remote "$VM3_HOST" \
            "$JMETER_BIN -n -t $JMX_PATH -l $remote_jtl \
             -Jhost=$host -Jport=$port -Jbasepath=$basepath -Jwstoken=${WS_TOKEN:-} -Jthreads=$MEASURE_THREADS"

        if [[ "$DRY_RUN" == false ]]; then
            # scp takes the port with a capital -P.
            scp -q -P "${SSH_PORT:-22}" "$SSH_USER@$VM3_HOST:$remote_jtl" "$outdir/run$r.jtl"
            run_remote "$VM3_HOST" "rm -f $remote_jtl"
            # The state declared by VM1 is saved next to the results.
            run_remote "$VM1_HOST" "cat /var/lib/phase1/active-config" > "$outdir/run$r.meta"
        fi
    done
done

echo ""
echo "==> Collection completed in $CAMPAIGN"
echo "    python3 $SCRIPT_DIR/analyze.py $CAMPAIGN --apdex-t $APDEX_T_MS --apdex-f $APDEX_F_MS"
