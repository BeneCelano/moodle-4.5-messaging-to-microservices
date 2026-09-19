#!/usr/bin/env bash
#
# Run the phase 2 comparison campaign: two targets, N repetitions, order inverted at every
# repetition. To run from any machine that reaches VM1, VM2 and VM3 via ssh.
#
#   bash run-campaign.sh [--runs N] [--dry-run] [--different-environment]

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
AMBIENTE_DIVERSO=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --runs)              RUNS="$2"; shift 2 ;;
        --dry-run)           DRY_RUN=true; shift ;;
        --different-environment|--ambiente-diverso) AMBIENTE_DIVERSO=true; shift ;;
        *) echo "Usage: $0 [--runs N] [--dry-run] [--different-environment]" >&2; exit 1 ;;
    esac
done

if (( RUNS < 3 )); then
    # At least three repetitions, for the standard deviation.
    echo "At least 3 repetitions are needed for the standard deviation to mean anything." >&2
    exit 1
fi

TARGETS=(M S)
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

# Split an address into host, port and base path.
url_parts() {
    local rest="${1#http://}"
    local hostport="${rest%%/*}"
    local path=""
    local host="${hostport%%:*}"
    local port=80

    [[ "$rest" == */* ]] && path="/${rest#*/}"
    [[ "$hostport" == *:* ]] && port="${hostport##*:}"

    echo "$host $port $path"
}

read -r MONO_HOST MONO_PORT MONO_PATH <<< "$(url_parts "$MONOLITH_URL")"
read -r SVC_HOST SVC_PORT SVC_PATH <<< "$(url_parts "$SERVICE_URL")"

# The two tokens are placed on VM3 in a properties file with restricted permissions, not on the
# JMeter command line.
PROPS_REMOTE="$JMX_DIR/campaign.properties"

# --- The environment must be the one the archived campaign measured -------------------------------
#
# The comparison rests on one exclusive core for the monolith and one for the service; the shape
# of the deployment is checked, and --different-environment skips the check.
if [[ "$DRY_RUN" == false && "$AMBIENTE_DIVERSO" == false ]]; then
    echo "==> Checking that the service is in the campaign configuration"
    FORMA="$(ssh -p "${SSH_PORT:-22}" "$SSH_USER@$VM1_HOST" "kubectl -n ${K8S_NAMESPACE:-messaging} get deploy ${SERVICE_DEPLOY:-conversation-service} -o jsonpath='{.spec.replicas} {.spec.template.spec.containers[0].resources.requests.cpu} {.spec.template.spec.containers[0].resources.limits.cpu}'" 2>/dev/null)"
    read -r REPLICHE RICHIESTA LIMITE <<< "$FORMA"

    if [[ -z "$FORMA" ]]; then
        echo "WARNING: cannot read the service configuration from $VM1_HOST." >&2
        echo "The campaign goes on, but nobody checked that the environment is the right one." >&2
    elif [[ "$REPLICHE" != "1" || "$RICHIESTA" != "1" || "$LIMITE" != "1" ]]; then
        echo "" >&2
        echo "STOP: the service is not in the configuration this campaign measured." >&2
        echo "" >&2
        echo "  expected  1 replica, request 1, limit 1   (one exclusive core, Guaranteed class)" >&2
        echo "  found     $REPLICHE replicas, request $RICHIESTA, limit $LIMITE" >&2
        echo "" >&2
        echo "The comparison rests on the symmetry between one core for the monolith and one for the" >&2
        echo "service: without it, allocations are measured instead of architectures, and the numbers" >&2
        echo "are not comparable with those reported in the experimental chapter." >&2
        echo "" >&2
        echo "To bring the service back to the campaign configuration:" >&2
        echo "  kubectl -n ${K8S_NAMESPACE:-messaging} patch deploy ${SERVICE_DEPLOY:-conversation-service} --patch-file deploy/experiments/phase2/deployment-campaign.yaml" >&2
        echo "" >&2
        echo "And to return to the production one, once the campaign is over:" >&2
        echo "  kubectl -n ${K8S_NAMESPACE:-messaging} apply -f deploy/k8s/conversation-service/deployment.yaml" >&2
        echo "" >&2
        echo "To measure the current configuration anyway, knowing it is another experiment:" >&2
        echo "  $0 --different-environment" >&2
        exit 1
    else
        echo "    1 replica, one exclusive core"
    fi
fi

if [[ "$DRY_RUN" == false ]]; then
    echo "==> Placing the restricted properties on VM3"
    ssh -p "${SSH_PORT:-22}" "$SSH_USER@$VM3_HOST" \
        "umask 077 && mkdir -p $JMX_DIR && cat > $PROPS_REMOTE" <<PROPS
wstoken=$WS_TOKEN
token=$CS_API_TOKEN
PROPS
fi

jmeter_cmd() {
    # Compose the invocation for the requested target; the tokens come from the properties file.
    local target="$1" jtl="$2" threads="$3" loops="$4"

    case "$target" in
        M) echo "$JMETER_BIN -n -t $JMX_DIR/monolith.jmx -l $jtl -q $PROPS_REMOTE" \
                "-Jhost=$MONO_HOST -Jport=$MONO_PORT -Jbasepath=$MONO_PATH" \
                "-Jrecipientmin=$RECIPIENT_ID_MIN -Jrecipientmax=$RECIPIENT_ID_MAX" \
                "-Jthreads=$threads -Jloops=$loops -Jrampup=$RAMPUP_SECONDS" ;;
        S) echo "$JMETER_BIN -n -t $JMX_DIR/service.jmx -l $jtl -q $PROPS_REMOTE" \
                "-Jhost=$SVC_HOST -Jport=$SVC_PORT" \
                "-Jsender=$SENDER_USER_ID" \
                "-Jrecipientmin=$RECIPIENT_ID_MIN -Jrecipientmax=$RECIPIENT_ID_MAX" \
                "-Jthreads=$threads -Jloops=$loops -Jrampup=$RAMPUP_SECONDS" ;;
    esac
}

mkdir -p "$CAMPAIGN"
echo "==> Campaign $STAMP, $RUNS repetitions per target"

{
    echo "campaign=$STAMP"
    echo "runs=$RUNS"
    echo "threads=$MEASURE_THREADS"
    echo "loops=$LOOPS"
    echo "monolith_url=$MONOLITH_URL"
    echo "service_url=$SERVICE_URL"
    echo "sender_user_id=$SENDER_USER_ID"
    echo "apdex_t_ms=$APDEX_T_MS"
    echo "apdex_f_ms=$APDEX_F_MS"
    echo "started_at=$(date -Is)"
} > "$CAMPAIGN/campaign.env"

for (( r = 1; r <= RUNS; r++ )); do
    # Odd repetitions start from the monolith, even ones from the service.
    for (( i = 0; i < 2; i++ )); do
        target="${TARGETS[$(( (i + r - 1) % 2 ))]}"

        echo ""
        echo "==> Repetition $r/$RUNS, target $target"

        # The warm-up refills the PHP bytecode cache on the monolith and the connection pool on
        # the service.
        echo "    warm-up (${WARMUP_THREADS} threads, discarded)"
        run_remote "$VM3_HOST" "$(jmeter_cmd "$target" /dev/null "$WARMUP_THREADS" 2) >/dev/null 2>&1 || true"

        # The restore comes after the warm-up: the warm-up writes messages.
        run_remote "$VM2_HOST" "bash $SCRIPT_DIR_VM2/reset-db.sh"

        outdir="$CAMPAIGN/$target"
        mkdir -p "$outdir"
        remote_jtl="/tmp/phase2-$target-run$r.jtl"

        echo "    measure"
        run_remote "$VM3_HOST" "$(jmeter_cmd "$target" "$remote_jtl" "$MEASURE_THREADS" "$LOOPS")"

        if [[ "$DRY_RUN" == false ]]; then
            # scp takes the port with a capital -P.
            scp -q -P "${SSH_PORT:-22}" "$SSH_USER@$VM3_HOST:$remote_jtl" "$outdir/run$r.jtl"
            run_remote "$VM3_HOST" "rm -f $remote_jtl"
        fi
    done
done

if [[ "$DRY_RUN" == false ]]; then
    # The properties file holds two tokens and is removed at the end.
    ssh -p "${SSH_PORT:-22}" "$SSH_USER@$VM3_HOST" "rm -f $PROPS_REMOTE"
fi

echo ""
echo "==> Collection completed in $CAMPAIGN"
echo "    python3 $SCRIPT_DIR/analyze.py $CAMPAIGN --apdex-t $APDEX_T_MS --apdex-f $APDEX_F_MS"
