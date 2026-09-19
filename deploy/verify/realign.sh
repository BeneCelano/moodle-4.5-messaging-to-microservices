#!/usr/bin/env bash
#
# Bring the service database back to a faithful copy of the monolith's, running the reconciliation
# in repair mode from the monolith to the service: rows present only in the service are DELETED,
# missing ones reinserted, differing ones rewritten. The monolith is not touched.
#
#   bash deploy/verify/realign.sh [--force]

set -uo pipefail

BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

K8S_NAMESPACE="${K8S_NAMESPACE:-messaging}"
SERVICE_DEPLOY="${SERVICE_DEPLOY:-conversation-service}"
MONOLITH_SCHEMA="${MONOLITH_SCHEMA:-moodle_db}"
MONOLITH_PREFIX="${MONOLITH_PREFIX:-mdl_}"

# The same values verify.sh uses, read from the same file.
if [[ -f "$(dirname "${BASH_SOURCE[0]}")/verify.env" ]]; then
    # shellcheck disable=SC1091
    source "$(dirname "${BASH_SOURCE[0]}")/verify.env"
fi

MANIFESTO="$BASE/deploy/k8s/conversation-service/reconcile-job.yaml"
FORZA=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --force|--forza) FORZA=true; shift ;;
        -h|--help)
            cat <<'USO'
Usage: bash realign.sh [--force]

Brings the service database back to being a faithful copy of the monolith's.

Needed after someone wrote into the system in remote mode: those rows live only in the
service, and the comparison between the two implementations reads them as divergences.

  --force   asks no confirmation. Rows present only in the service are deleted without anyone
            seeing them go: to be used only inside an automated procedure.

Before changing anything it always shows the table of differences, and if there are none it
exits without doing anything.
USO
            exit 0 ;;
        *) echo "Unknown option: $1. See --help." >&2; exit 1 ;;
    esac
done

if [[ -t 1 ]]; then
    C_OK=$'\033[32m'; C_KO=$'\033[31m'; C_T=$'\033[1m'; C_0=$'\033[0m'
else
    C_OK=""; C_KO=""; C_T=""; C_0=""
fi

titolo() { printf '\n%s== %s%s\n' "$C_T" "$1" "$C_0"; }

# The same two bars verify.sh draws, in plain ASCII.
barra_indeterminata() {
    local posizione=$(( ${1:-0} % 18 )) vuoto="--------------------"
    printf '[%s###%s]' "${vuoto:0:posizione}" "${vuoto:0:$(( 17 - posizione ))}"
}

durata() {
    local secondi="$1"
    (( secondi < 60 )) && { printf '%ds' "$secondi"; return; }
    printf '%dm%02ds' $(( secondi / 60 )) $(( secondi % 60 ))
}

# --- Prerequisites --------------------------------------------------------------------------------

if [[ ! -f "$MANIFESTO" ]]; then
    echo "Manifest not found: $MANIFESTO" >&2
    exit 1
fi

if ! command -v kubectl >/dev/null 2>&1 ||
   ! kubectl -n "$K8S_NAMESPACE" get deploy "$SERVICE_DEPLOY" >/dev/null 2>&1; then
    echo "The cluster does not answer: kubectl cannot find $SERVICE_DEPLOY in namespace $K8S_NAMESPACE." >&2
    echo "This tool runs on VM1, where the cluster server lives." >&2
    exit 1
fi

# The reconciliation reads the monolith database with the seed identity, kept in a separate
# Secret.
if ! kubectl -n "$K8S_NAMESPACE" get secret conversation-service-seed-secret >/dev/null 2>&1; then
    echo "conversation-service-seed-secret is missing: it is the only identity that can read both schemas." >&2
    echo "It must be re-applied for the duration of the operation. See deploy/README.md." >&2
    exit 1
fi

IMMAGINE="$(kubectl -n "$K8S_NAMESPACE" get deploy "$SERVICE_DEPLOY" \
    -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null)"
if [[ -z "$IMMAGINE" ]]; then
    echo "Cannot read the running image from deployment $SERVICE_DEPLOY." >&2
    exit 1
fi

# --- Running the Job ------------------------------------------------------------------------------

# Why the comparison produced no log: pod not created (quota, see the Job events) or created and
# not scheduled (see its scheduling condition).
diagnosi_job() {
    local pod fase motivo
    pod="$(kubectl -n "$K8S_NAMESPACE" get pods -l app.kubernetes.io/name=conversation-service,app.kubernetes.io/component=reconcile --sort-by=.metadata.creationTimestamp -o name 2>/dev/null | tail -1)"

    if [[ -z "$pod" ]]; then
        echo "      The pod was not even created. From the Job events:"
        kubectl -n "$K8S_NAMESPACE" describe job conversation-service-reconcile 2>/dev/null | grep -iE "exceeded quota|forbidden|error creating" | tail -2 | sed 's/^/        /'
        echo "      Namespace quota:"
        kubectl -n "$K8S_NAMESPACE" describe quota 2>/dev/null | grep -iE "requests.cpu|requests.memory" | sed 's/^/        /'
        echo "      A rollout still in progress holds the quota of the previous ReplicaSet. Wait for:"
        echo "        kubectl -n $K8S_NAMESPACE rollout status deploy/$SERVICE_DEPLOY"
        return
    fi

    fase="$(kubectl -n "$K8S_NAMESPACE" get "$pod" -o jsonpath='{.status.phase}' 2>/dev/null)"
    motivo="$(kubectl -n "$K8S_NAMESPACE" get "$pod" -o jsonpath='{.status.conditions[?(@.type=="PodScheduled")].message}' 2>/dev/null)"
    echo "      The pod stayed in state ${fase:-unknown}."
    [[ -n "$motivo" ]] && echo "      $motivo"
}


# Run the reconciliation in the given mode and leave the log in REGISTRO. The image is the
# running one.
REGISTRO=""

esegui() {
    local modalita="$1" etichetta="$2"
    printf '   %s ... ' "$etichetta"

    kubectl -n "$K8S_NAMESPACE" delete job conversation-service-reconcile \
        --ignore-not-found >/dev/null 2>&1
    # Wait for the pod of the previous run to be gone. The selector carries the service name
    # too.
    kubectl -n "$K8S_NAMESPACE" wait --for=delete pod -l app.kubernetes.io/name=conversation-service,app.kubernetes.io/component=reconcile \
        --timeout=60s >/dev/null 2>&1

    # backoffLimit zero: the tool exits non-zero when it finds differences.
    sed -e "s|image: conversation-service:.*|image: $IMMAGINE|" \
        -e "s|^  backoffLimit: .*|  backoffLimit: 0|" \
        -e "s|- --check|- --$modalita|" \
        -e "s|- moodle_db|- $MONOLITH_SCHEMA|" \
        -e "s|- mdl_|- $MONOLITH_PREFIX|" \
        "$MANIFESTO" | kubectl apply -f - >/dev/null 2>&1

    # Wait for the pod to end, not for the Job to be complete; the bar only shows activity.
    local inizio=$SECONDS fase="" giro=0
    while (( SECONDS - inizio < 300 )); do
        fase="$(kubectl -n "$K8S_NAMESPACE" get pods -l app.kubernetes.io/name=conversation-service,app.kubernetes.io/component=reconcile \
            --sort-by=.metadata.creationTimestamp \
            -o jsonpath='{.items[-1:].status.phase}' 2>/dev/null)"
        [[ "$fase" == "Succeeded" || "$fase" == "Failed" ]] && break
        # A pod not scheduled after half a minute: quota or node resources. Distinct from a pod
        # still pulling the image.
        if (( SECONDS - inizio > 30 )) && [[ "$(kubectl -n "$K8S_NAMESPACE" get pods -l app.kubernetes.io/name=conversation-service,app.kubernetes.io/component=reconcile --sort-by=.metadata.creationTimestamp -o jsonpath='{.items[-1:].status.conditions[?(@.type=="PodScheduled")].status}' 2>/dev/null)" != "True" ]]; then
            break
        fi
        [[ -t 1 ]] && printf '\r   %s ... %s  %s \033[K' "$etichetta" "$(barra_indeterminata "$giro")" "$(durata $(( SECONDS - inizio )))"
        giro=$(( giro + 1 ))
        sleep 1
    done
    [[ -t 1 ]] && printf '\r   %s ... ' "$etichetta"

    # One pod only: with a selector kubectl prints the logs of every matching pod.
    local pod
    pod="$(kubectl -n "$K8S_NAMESPACE" get pods -l app.kubernetes.io/name=conversation-service,app.kubernetes.io/component=reconcile \
        --sort-by=.metadata.creationTimestamp -o name 2>/dev/null | tail -1)"
    REGISTRO="$(kubectl -n "$K8S_NAMESPACE" logs "$pod" --tail=40 2>/dev/null)"

    if [[ -z "$REGISTRO" ]]; then
        printf '%sno log%s\033[K\n' "$C_KO" "$C_0"
        diagnosi_job
        return 1
    fi
    printf '%sdone%s in %s\033[K\n\n' "$C_OK" "$C_0" "$(durata $(( SECONDS - inizio )))"
    printf '%s\n' "$REGISTRO" | sed 's/^/      /'
    return 0
}

# Sum the three columns of the table (missing, differing, extra) and count the rows read.
conta() {
    awk '
        $2 ~ /^[0-9]+$/ && $3 ~ /^[0-9]+$/ && $4 ~ /^[0-9]+$/ { m += $2; d += $3; p += $4; n++ }
        END { print m + 0, d + 0, p + 0, n + 0 }' <<< "$REGISTRO"
}

# --- First pass: look, do not touch ---------------------------------------------------------------

titolo "Differences between the two stores"
echo "   monolith '$MONOLITH_SCHEMA', prefix '$MONOLITH_PREFIX' - image $IMMAGINE"
echo ""

esegui check "comparison" || exit 1

read -r MANCANTI DIVERSE INPIU RIGHE < <(conta)

if (( RIGHE == 0 )); then
    echo ""
    echo "The comparison produced no readable table. Nothing was changed." >&2
    exit 1
fi

if (( MANCANTI == 0 && DIVERSE == 0 && INPIU == 0 )); then
    echo ""
    printf '%s==> The two stores are already identical. There is nothing to realign.%s\n' "$C_OK" "$C_0"
    exit 0
fi

# --- What is about to happen ----------------------------------------------------------------------

titolo "What would be done"
echo ""
printf '   rows present only in the service         %6d   will be DELETED\n'             "$INPIU"
printf '   rows the service does not have           %6d   will be reinserted from the monolith\n' "$MANCANTI"
printf '   rows the two stores hold differently     %6d   will be rewritten as in the monolith\n' "$DIVERSE"
echo ""
echo "   The direction is from the monolith to the service. The deleted rows are real messages,"
echo "   written through the service after the cutover: in this laboratory they are test data,"
echo "   and in production the same operation would be a loss of data."
echo ""

if [[ "$FORZA" != true ]]; then
    if [[ ! -t 0 ]]; then
        echo "No terminal to ask for confirmation. Re-run with --force if that is what is wanted." >&2
        exit 1
    fi
    read -r -p "   Proceed? [y/N] " risposta
    if [[ ! "$risposta" =~ ^[yYsS]$ ]]; then
        echo "   Cancelled. Nothing was changed."
        exit 1
    fi
fi

# --- Second pass: repair --------------------------------------------------------------------------

titolo "Repair"
esegui apply "realignment" || exit 1

# --- Third pass: check that nothing is left ---------------------------------------------------------

titolo "Final check"
esegui check "recount" || exit 1

read -r MANCANTI DIVERSE INPIU RIGHE < <(conta)

echo ""
if (( MANCANTI == 0 && DIVERSE == 0 && INPIU == 0 )); then
    printf '%s==> The two stores are identical: the service is again a faithful copy of the monolith.%s\n' \
        "$C_OK" "$C_0"
    echo ""
    echo "    The verification now runs on a clean state:"
    echo "      bash deploy/verify/verify.sh --skip-writes"
    exit 0
fi

printf '%s==> Differences remain: missing %s, differing %s, extra %s.%s\n' \
    "$C_KO" "$MANCANTI" "$DIVERSE" "$INPIU" "$C_0"
echo ""
echo "    The repair was not enough, and this deserves a look: the column mappings live"
echo "    in services/conversation/scripts/seed_from_monolith.py, and the log above says on"
echo "    which tables the disagreement persists."
exit 1
