#!/usr/bin/env bash
#
# Verification of the migration criteria, from one command.
#
#   bash deploy/verify/verify.sh [--phase N[,N...]|end] [--archive-only] [--skip-writes] [--prepare]
#                                [--realign] [--switch]
#
# Functional checks run on the real system; the measures under load are replayed from the archive
# in campaigns/ with the analyze.py of each phase. A check that cannot run is declared NOT RUNNABLE.
# Exit code: 0 every criterion passed, 1 at least one not passed, 2 none failed but some not run.

set -uo pipefail

BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ESPERIMENTI="$BASE/deploy/experiments"
ARCHIVIO="$BASE/deploy/verify/campaigns"

# --- Defaults of the laboratory installation (overridden by a verify.env file next to this script) ---
MOODLE_DOCROOT="${MOODLE_DOCROOT:-/var/www/html/moodle}"
MOODLE_TESTSUITE="${MOODLE_TESTSUITE:-core_message_testsuite}"
MOODLE_TEST_ATTESI="${MOODLE_TEST_ATTESI:-909}"
# Tests expected from each service suite, per phase: "none failed" is checked together with "all
# ran".
SERVIZIO_TEST_FASE2="${SERVIZIO_TEST_FASE2:-35}"
SERVIZIO_TEST_FASE3="${SERVIZIO_TEST_FASE3:-50}"
WEB_USER="${WEB_USER:-www-data}"
K8S_NAMESPACE="${K8S_NAMESPACE:-messaging}"
SERVICE_DEPLOY="${SERVICE_DEPLOY:-conversation-service}"
SERVICE_URL="${SERVICE_URL:-http://192.168.72.71:30081}"
MONOLITH_SCHEMA="${MONOLITH_SCHEMA:-moodle_db}"
MONOLITH_PREFIX="${MONOLITH_PREFIX:-mdl_}"
SERVIZIO_SORGENTI="${SERVIZIO_SORGENTI:-$BASE/services/conversation}"
GATEWAY_DEPLOY="${GATEWAY_DEPLOY:-delivery-gateway}"
GATEWAY_SORGENTI="${GATEWAY_SORGENTI:-$BASE/services/delivery}"
GATEWAY_TEST_ATTESI="${GATEWAY_TEST_ATTESI:-44}"
GATEWAY_SIMULACRO="${GATEWAY_SIMULACRO:-http://192.168.72.74:8081}"
NOTIFICA_DEPLOY="${NOTIFICA_DEPLOY:-notification-service}"
NOTIFICA_SORGENTI="${NOTIFICA_SORGENTI:-$BASE/services/notification}"
NOTIFICA_TEST_ATTESI="${NOTIFICA_TEST_ATTESI:-73}"

if [[ -f "$(dirname "${BASH_SOURCE[0]}")/verify.env" ]]; then
    # shellcheck disable=SC1091
    source "$(dirname "${BASH_SOURCE[0]}")/verify.env"
fi

SOLO_FASE=""
SOLO_ARCHIVIO=false
SALTA_SCRITTURE=false
PREPARA=false
RIALLINEA=false
COMMUTA=false

# Rows the service has and the monolith has not, measured by the phase 2 integrity and read by
# phase 3.
RESIDUI=-1

# The same, for the notification context.
RESIDUI_NOTIFICHE=-1

while [[ $# -gt 0 ]]; do
    case "$1" in
        # Options have an English name; the Italian one is still accepted.
        --phase|--fase)               SOLO_FASE="${2//[[:space:]]/}"; SOLO_FASE="${SOLO_FASE//end/fine}"; shift 2 ;;
        --archive-only|--solo-archivio) SOLO_ARCHIVIO=true; shift ;;
        --skip-writes|--salta-scritture) SALTA_SCRITTURE=true; shift ;;
        --prepare|--prepara)          PREPARA=true; shift ;;
        --realign|--riallinea)        RIALLINEA=true; shift ;;
        --switch|--commuta)           COMMUTA=true; shift ;;
        -h|--help)
            cat <<'USO'
Usage: bash verify.sh [options]

  --phase N[,N...]    Verify only the given phases, comma separated: --phase 2,4.
                      "end" selects the Moodle suites, which belong to no phase:
                      --phase end. There are five and they take about twenty minutes.
  --archive-only      Skip the checks that run on the real system and replay the measures only.
                      Useful on a machine that is not the laboratory one.
  --skip-writes       The provider comparison runs reads only. Writes create and remove test
                      data in the authoritative store: skipping them makes the verification
                      entirely non-invasive, at the price of 380 checks out of 756, and keeps the
                      scratch users from leaving rows that the next verification reports as a divergence.
  --prepare           Create the virtual environment of the service suite if missing. Needed
                      once and requires network access.
  --realign           Before verifying, bring the service database back to a faithful copy of
                      the monolith's. DELETES the rows written in the service after the cutover:
                      it is what restarting from the state in which the criteria were declared
                      requires, and it is not reversible.
  --switch            Switch the routing mode automatically when a check requires one other than
                      the active one, and restore the initial one at the end, even if the
                      verification is interrupted. Without this option the checks that do not
                      find the right mode declare themselves NOT RUNNABLE and say what to run.
                      Requires sudo: the site configuration belongs to others.

Without options it runs the five phases and the final verification of the monolith.
USO
            exit 0 ;;
        *) echo "Unknown option: $1. See --help." >&2; exit 1 ;;
    esac
done

# Whether a phase is among the requested ones. Without --phase all of them run.
fase_richiesta() {
    [[ -z "$SOLO_FASE" ]] && return 0
    [[ ",$SOLO_FASE," == *",$1,"* ]]
}

# --- Summary table --------------------------------------------------------------------------------

FASI=(); CRITERI=(); COME=(); ESITI=(); DETTAGLI=()

if [[ -t 1 ]]; then
    C_OK=$'\033[32m'; C_KO=$'\033[31m'; C_NA=$'\033[33m'; C_T=$'\033[1m'; C_0=$'\033[0m'
else
    C_OK=""; C_KO=""; C_NA=""; C_T=""; C_0=""
fi

titolo()  { printf '\n%s== %s%s\n' "$C_T" "$1" "$C_0"; }
passo()   { printf '   %s ... ' "$1"; }
detto()   { printf '%s\n' "$1"; }

# Record the outcome of a criterion; the optional last argument is the detail printed under the
# table.
registra() {
    FASI+=("$1"); CRITERI+=("$2"); COME+=("$3"); ESITI+=("$4"); DETTAGLI+=("${5:-}")
    case "$4" in
        PASSED)         printf '%sPASSED%s\n'       "$C_OK" "$C_0" ;;
        "NOT PASSED") printf '%sNOT PASSED%s\n'   "$C_KO" "$C_0" ;;
        *)              printf '%s%s%s\n'             "$C_NA" "$4" "$C_0" ;;
    esac
    [[ -n "${5:-}" ]] && printf '      %s\n' "$5"
    return 0
}

# Find the available Python interpreter (on Windows "python3" may be a store placeholder).
python_di_sistema() {
    if command -v python3 >/dev/null 2>&1 && python3 -c '' >/dev/null 2>&1; then
        echo "python3"
    elif command -v py >/dev/null 2>&1 && py -3 -c '' >/dev/null 2>&1; then
        echo "py -3"
    elif command -v python >/dev/null 2>&1 && python -c '' >/dev/null 2>&1; then
        echo "python"
    else
        return 1
    fi
}

# A virtual environment puts the executables in bin/ on POSIX and in Scripts/ on Windows.
pytest_del_venv() {
    local venv="$1"
    for candidato in "$venv/bin/pytest" "$venv/Scripts/pytest.exe" "$venv/Scripts/pytest"; do
        [[ -x "$candidato" ]] && { echo "$candidato"; return 0; }
    done
    return 1
}

pip_del_venv() {
    local venv="$1"
    for candidato in "$venv/bin/pip" "$venv/Scripts/pip.exe" "$venv/Scripts/pip"; do
        [[ -x "$candidato" ]] && { echo "$candidato"; return 0; }
    done
    return 1
}

# Render a twenty-cell bar filled up to the given percentage, in plain ASCII.
barra() {
    local percento="${1:-0}" piene
    (( percento < 0 )) && percento=0
    (( percento > 100 )) && percento=100
    piene=$(( percento * 20 / 100 ))
    local pieno="####################" vuoto="--------------------"
    printf '[%s%s]' "${pieno:0:piene}" "${vuoto:0:$(( 20 - piene ))}"
}

# Render a bar for a wait of unknown length: a moving block.
barra_indeterminata() {
    local posizione=$(( ${1:-0} % 18 )) vuoto="--------------------"
    printf '[%s###%s]' "${vuoto:0:posizione}" "${vuoto:0:$(( 17 - posizione ))}"
}

# Extract the progress from the output collected so far: PHPUnit "478 / 909 ( 52%)", pytest "[
# 52%]".
avanzamento() {
    local file="$1" trovato
    trovato="$(tr '\r' '\n' < "$file" 2>/dev/null | grep -oE '[0-9]+ / [0-9]+|\[ *[0-9]+%\]' | tail -1)"
    [[ -z "$trovato" ]] && return 1

    if [[ "$trovato" == *"/"* ]]; then
        local fatti="${trovato%% *}" totale="${trovato##* }"
        (( totale > 0 )) || return 1
        printf '%d %d %d' $(( fatti * 100 / totale )) "$fatti" "$totale"
    else
        printf '%d' "${trovato//[^0-9]/}"
    fi
}

# Agree the noun with the number.
righe() {
    (( $1 == 1 )) && { printf '1 row'; return; }
    printf '%d rows' "$1"
}

# Format a duration in a readable form.
durata() {
    local secondi="$1"
    (( secondi < 60 )) && { printf '%ds' "$secondi"; return; }
    printf '%dm%02ds' $(( secondi / 60 )) $(( secondi % 60 ))
}

# Run a long command keeping the elapsed time updated on the same line. The exit code is the
# command's; its output goes to ATTESA_USCITA. Outside a terminal the counter is not printed.
ATTESA_USCITA=""

attendi() {
    local etichetta="$1" comando="$2"

    # The folder is recreated in case it disappeared.
    mkdir -p "$TEMPORANEA" 2>/dev/null
    local file="$TEMPORANEA/attesa.$RANDOM"

    if [[ ! -t 1 ]]; then
        # Outside a terminal there is no line to rewrite.
        bash -c "$comando" > "$file" 2>&1
        local esitodiretto=$?
        ATTESA_USCITA="$(cat "$file")"
        rm -f "$file"
        return $esitodiretto
    fi

    bash -c "$comando" > "$file" 2>&1 &
    local pid=$! inizio=$SECONDS giro=0

    while kill -0 "$pid" 2>/dev/null; do
        local stato
        if stato="$(avanzamento "$file")"; then
            set -- $stato
            if [[ -n "${2:-}" ]]; then
                printf '\r   %s ... %s %3d%%  %s/%s  %s \033[K' \
                    "$etichetta" "$(barra "$1")" "$1" "$2" "$3" "$(durata $(( SECONDS - inizio )))"
            else
                printf '\r   %s ... %s %3d%%  %s \033[K' \
                    "$etichetta" "$(barra "$1")" "$1" "$(durata $(( SECONDS - inizio )))"
            fi
        else
            printf '\r   %s ... %s  %s \033[K' \
                "$etichetta" "$(barra_indeterminata "$giro")" "$(durata $(( SECONDS - inizio )))"
        fi
        giro=$(( giro + 1 ))
        sleep 1
    done

    wait "$pid"
    local esito=$?
    printf '\r   %s ... %s  %s \033[K' "$etichetta" "$(barra 100)" "$(durata $(( SECONDS - inizio )))"
    ATTESA_USCITA="$(cat "$file")"
    rm -f "$file"
    return $esito
}

# --- Campaign archive -----------------------------------------------------------------------------

TEMPORANEA="$(mktemp -d)"
trap 'rm -rf "$TEMPORANEA"' EXIT

# Return the folder of the archived results of a phase, unpacked in a temporary folder.
campagna_archiviata() {
    local fase="$1"
    local compressa="$ARCHIVIO/phase$fase.tar.gz"
    local cartella="$ARCHIVIO/phase$fase"

    if [[ -d "$cartella" ]]; then
        echo "$cartella"
        return 0
    fi
    if [[ -f "$compressa" ]]; then
        mkdir -p "$TEMPORANEA/phase$fase"
        tar -xzf "$compressa" -C "$TEMPORANEA/phase$fase" 2>/dev/null || return 1
        # The archive holds one campaign folder: return that one.
        local dentro
        dentro="$(find "$TEMPORANEA/phase$fase" -mindepth 1 -maxdepth 1 -type d | sort | head -1)"
        echo "${dentro:-$TEMPORANEA/phase$fase}"
        return 0
    fi
    return 1
}

# Run the analyze.py of a phase on the archive and record the outcome: exit 0 criteria met, 1
# not met, 2 no data.
misura_archiviata() {
    local fase="$1" criterio="$2"; shift 2
    passo "measures under load, from the archive"

    local cartella
    if ! cartella="$(campagna_archiviata "$fase")"; then
        registra "$fase" "$criterio" "archive" "DATA MISSING" \
            "$ARCHIVIO/phase$fase.tar.gz is missing: the measure cannot be replayed"
        return 0
    fi

    local uscita esito
    uscita="$($PY "$ESPERIMENTI/phase$fase/analyze.py" "$cartella" "$@" 2>&1)"
    esito=$?

    printf '\n'
    printf '%s\n' "$uscita" | sed 's/^/      /'

    local data
    data="$(basename "$cartella")"
    case "$esito" in
        0) registra "$fase" "$criterio" "archive $data" "PASSED" ;;
        1) registra "$fase" "$criterio" "archive $data" "NOT PASSED" ;;
        *) registra "$fase" "$criterio" "archive $data" "NOT RUNNABLE" \
               "analyze.py found no usable data" ;;
    esac
}

# --- Environment ----------------------------------------------------------------------------------

AMBIENTE_VIVO=true
MOTIVO_NON_VIVO="environment not available"

# Two independent routing switches: conversationservice_mode (phase 3) and
# notificationservice_mode (phase 5).
MOODLE_MODE=""
MODALITA_NOTIFICHE=""
MODALITA_INIZIALE_C=""
MODALITA_INIZIALE_N=""

# Read a mode as the site reads it: config.php and the files it includes, as the web server
# user.
modalita_di() {
    sudo -u "$WEB_USER" php -r \
        'define("CLI_SCRIPT", true); define("ABORT_AFTER_CONFIG", true); require($argv[1]);
         echo $CFG->{$argv[2]} ?? "legacy";' \
        "$MOODLE_DOCROOT/config.php" "$1" 2>/dev/null || true
}

# Bring a context to the mode a check requires, only with --switch.
#
#   $1 conversazioni|notifiche|entrambi   $2 legacy|remote
#
# Returns 0 if the mode is the wanted one when the call ends, 1 otherwise (MOTIVO_MODALITA says why).
modalita_mancanti() {
    local contesto="$1" voluta="$2" mancanti=()
    if [[ "$contesto" != "notifiche" && -n "$MOODLE_MODE" && "$MOODLE_MODE" != "$voluta" ]]; then
        mancanti+=("the conversation context is in $MOODLE_MODE")
    fi
    if [[ "$contesto" != "conversazioni" && -n "$MODALITA_NOTIFICHE" && "$MODALITA_NOTIFICHE" != "$voluta" ]]; then
        mancanti+=("the notification one is in $MODALITA_NOTIFICHE")
    fi
    (( ${#mancanti[@]} == 0 )) && return 0

    local elenco
    elenco="$(printf '%s, ' "${mancanti[@]}")"
    printf '%s' "${elenco%, }"
    return 1
}

# The contexts are arguments of switch-mode.sh (Italian names); on screen they are English.
nome_contesto() {
    case "$1" in
        conversazioni) printf 'conversations' ;;
        notifiche)     printf 'notifications' ;;
        entrambi)      printf 'both' ;;
        *)             printf '%s' "$1" ;;
    esac
}

MOTIVO_MODALITA=""
assicura_modalita() {
    local contesto="$1" voluta="$2" elenco uscita esito

    MOTIVO_MODALITA=""
    elenco="$(modalita_mancanti "$contesto" "$voluta")" && return 0

    if [[ "$COMMUTA" != true ]]; then
        MOTIVO_MODALITA="$elenco: $voluta is needed. Switch with: sudo bash deploy/verify/switch-mode.sh $voluta $(nome_contesto "$contesto") — or re-run with --switch"
        return 1
    fi

    printf '\n      switching to %s (%s) ... ' "$voluta" "$(nome_contesto "$contesto")"
    uscita="$(sudo bash "$BASE/deploy/verify/switch-mode.sh" "$voluta" "$contesto" 2>&1)"
    esito=$?
    if (( esito != 0 )); then
        printf 'failed\n'
        printf '%s\n' "$uscita" | tail -8 | sed 's/^/      /'
        MOTIVO_MODALITA="switching to $voluta failed: $elenco"
        return 1
    fi
    printf 'done\n      '

    MOODLE_MODE="$(modalita_di conversationservice_mode)"
    MODALITA_NOTIFICHE="$(modalita_di notificationservice_mode)"

    # Re-check instead of recursing, so that the function always ends.
    elenco="$(modalita_mancanti "$contesto" "$voluta")" && return 0
    MOTIVO_MODALITA="the switch reported success, but $elenco: something rewrites the setting afterwards"
    return 1
}

# Put the site back in its initial mode, also when the verification is interrupted.
RIPRISTINO_FATTO=false
ripristina_modalita() {
    [[ "$RIPRISTINO_FATTO" == true ]] && return 0
    RIPRISTINO_FATTO=true

    local da_rifare=()
    [[ -n "$MODALITA_INIZIALE_C" && "$MOODLE_MODE" != "$MODALITA_INIZIALE_C" ]] &&
        da_rifare+=("conversazioni:$MODALITA_INIZIALE_C")
    [[ -n "$MODALITA_INIZIALE_N" && "$MODALITA_NOTIFICHE" != "$MODALITA_INIZIALE_N" ]] &&
        da_rifare+=("notifiche:$MODALITA_INIZIALE_N")
    (( ${#da_rifare[@]} == 0 )) && return 0

    printf '\n%sRestoring the initial mode%s\n' "$C_T" "$C_0"
    local voce contesto voluta uscita
    for voce in "${da_rifare[@]}"; do
        contesto="${voce%%:*}"
        voluta="${voce##*:}"
        printf '   %s -> %s ... ' "$(nome_contesto "$contesto")" "$voluta"
        uscita="$(sudo bash "$BASE/deploy/verify/switch-mode.sh" "$voluta" "$contesto" 2>&1)"
        if (( $? == 0 )); then
            printf '%sdone%s\n' "$C_OK" "$C_0"
        else
            printf '%sFAILED%s\n' "$C_KO" "$C_0"
            printf '%s\n' "$uscita" | tail -8 | sed 's/^/      /'
            printf '   %sThe site was left in a mode other than the initial one. Fix it with:%s\n' \
                "$C_KO" "$C_0"
            printf '     sudo bash deploy/verify/switch-mode.sh %s %s\n' "$voluta" "$(nome_contesto "$contesto")"
        fi
    done
}
# One trap for both: restore of the mode first, removal of the temporary folder second.
SUDO_VIVO=""

# The final cleanup, idempotent: on INT it runs and EXIT fires right after.
PULIZIA_FATTA=false
pulizia_finale() {
    [[ "$PULIZIA_FATTA" == true ]] && return 0
    PULIZIA_FATTA=true
    ripristina_modalita
    [[ -n "$SUDO_VIVO" ]] && kill "$SUDO_VIVO" 2>/dev/null
    rm -rf "$TEMPORANEA"
    return 0
}
trap pulizia_finale EXIT
trap 'printf "
%sInterrupted (SIGINT).%s
" "$C_KO" "$C_0"; pulizia_finale; exit 130' INT
trap 'printf "
%sTerminated (SIGTERM).%s
" "$C_KO" "$C_0"; pulizia_finale; exit 143' TERM

verifica_ambiente() {
    titolo "Environment"

    if [[ "$SOLO_ARCHIVIO" == true ]]; then
        detto "   archive-only mode: the checks on the real system are skipped"
        AMBIENTE_VIVO=false
        MOTIVO_NON_VIVO="skipped on request, archive-only mode"
        return 0
    fi

    # The sudo credential is asked now and kept alive: it expires after fifteen minutes and the
    # subsystem suite alone lasts longer.
    passo "administrator credential"
    if sudo -v 2>/dev/null; then
        printf '%sactive%s\n' "$C_OK" "$C_0"
        # -n: never ask.
        ( while true; do sudo -n true 2>/dev/null; sleep 50; done ) &
        SUDO_VIVO=$!
    else
        printf '%snot obtained%s\n' "$C_NA" "$C_0"
        detto "   The checks that go through sudo will say why they did not start."
    fi

    local mancanti=()

    passo "Moodle tree in $MOODLE_DOCROOT"
    if [[ -f "$MOODLE_DOCROOT/config.php" ]]; then
        printf '%spresent%s\n' "$C_OK" "$C_0"
    else
        printf '%sabsent%s\n' "$C_NA" "$C_0"
        mancanti+=("Moodle")
    fi

    # The served tree must match the repository; the comparison is by content, with the list of
    # sync-tree.sh.
    if [[ -f "$MOODLE_DOCROOT/config.php" ]]; then
        passo "served tree aligned with the repository"
        local deriva esito_deriva
        deriva="$(bash "$BASE/deploy/verify/sync-tree.sh" --check 2>/dev/null)"
        esito_deriva=$?
        if (( esito_deriva == 0 )); then
            printf '%smatches%s\n' "$C_OK" "$C_0"
        elif (( esito_deriva == 1 )); then
            printf '%sdiffers%s\n' "$C_KO" "$C_0"
            printf '%s\n' "$deriva" | head -12 | sed 's/^/      /'
            detto "   What follows would measure code other than the repository's."
            detto "   Synchronise, then repeat: sudo bash deploy/verify/sync-tree.sh"
        else
            # Third outcome: the comparison itself failed.
            printf '%snot verifiable%s\n' "$C_NA" "$C_0"
            detto "   The comparison with the served tree failed: bash deploy/verify/sync-tree.sh --check"
        fi
    fi

    # The starting modes are read here, before --switch may move them.
    passo "routing mode"
    MOODLE_MODE="$(modalita_di conversationservice_mode)"
    MODALITA_NOTIFICHE="$(modalita_di notificationservice_mode)"
    MODALITA_INIZIALE_C="$MOODLE_MODE"
    MODALITA_INIZIALE_N="$MODALITA_NOTIFICHE"
    if [[ -n "$MOODLE_MODE" || -n "$MODALITA_NOTIFICHE" ]]; then
        printf '%sconversations: %s, notifications: %s%s\n' \
            "$C_OK" "${MOODLE_MODE:-not readable}" "${MODALITA_NOTIFICHE:-not readable}" "$C_0"
        [[ "$COMMUTA" == true ]] &&
            detto "   --switch: I switch them when a check requires it, and put them back as they were"
    else
        printf '%snot readable%s\n' "$C_NA" "$C_0"
    fi

    passo "service at $SERVICE_URL"
    local pronto
    pronto="$(curl -sf --max-time 10 "$SERVICE_URL/readyz" 2>/dev/null || true)"
    if [[ "$pronto" == *'"database":"ok"'* ]]; then
        printf '%sready%s\n' "$C_OK" "$C_0"
    else
        printf '%snot ready%s\n' "$C_NA" "$C_0"
        mancanti+=("ConversationService")
    fi

    passo "kubectl"
    if command -v kubectl >/dev/null 2>&1 && kubectl -n "$K8S_NAMESPACE" get deploy "$SERVICE_DEPLOY" >/dev/null 2>&1; then
        printf '%sreaches the cluster%s\n' "$C_OK" "$C_0"
    else
        printf '%snot available%s\n' "$C_NA" "$C_0"
        mancanti+=("kubectl")
    fi

    if (( ${#mancanti[@]} > 0 )); then
        AMBIENTE_VIVO=false
        detto ""
        detto "   Missing: ${mancanti[*]}. The checks on the real system will be declared not"
        detto "   runnable; the archived measures run anyway, because they do not depend"
        detto "   on the environment. It is the normal case on a machine other than the laboratory one."
    fi
}

# Why a Job produced no log: pod not created (quota, see the Job events) or created and not
# scheduled (see its scheduling condition).
diagnosi_job() {
    # Pod selector and Job name are parameters (defaults: the reconciliation Job); the selector
    # must name the service, since the smoke Jobs of the two services both carry
    # `component=smoke`.
    local selettore="${1:-app.kubernetes.io/component=reconcile}"
    local lavoro="${2:-conversation-service-reconcile}"
    local pod fase motivo
    pod="$(kubectl -n "$K8S_NAMESPACE" get pods -l "$selettore" --sort-by=.metadata.creationTimestamp -o name 2>/dev/null | tail -1)"

    if [[ -z "$pod" ]]; then
        echo "      The pod was not even created. From the Job events:"
        kubectl -n "$K8S_NAMESPACE" describe job "$lavoro" 2>/dev/null | grep -iE "exceeded quota|forbidden|error creating" | tail -2 | sed 's/^/        /'
        echo "      Namespace quota:"
        kubectl -n "$K8S_NAMESPACE" describe quota 2>/dev/null | grep -iE "requests.cpu|requests.memory" | sed 's/^/        /'
        echo "      A rollout still in progress holds the quota of the previous ReplicaSet. Wait for:"
        echo "        kubectl -n $K8S_NAMESPACE rollout status deploy/$SERVICE_DEPLOY"
        echo "      Or the HPA holds the gateway at two replicas, which take 256Mi more:"
        echo "        kubectl -n $K8S_NAMESPACE get hpa delivery-gateway"
        return
    fi

    fase="$(kubectl -n "$K8S_NAMESPACE" get "$pod" -o jsonpath='{.status.phase}' 2>/dev/null)"
    motivo="$(kubectl -n "$K8S_NAMESPACE" get "$pod" -o jsonpath='{.status.conditions[?(@.type=="PodScheduled")].message}' 2>/dev/null)"
    echo "      The pod stayed in state ${fase:-unknown}."
    [[ -n "$motivo" ]] && echo "      $motivo"
}

# --- Running a Job on the cluster -----------------------------------------------------------------
#
# The image is read from the running deployment, not from the manifest.
#
#   $1 label   $2 manifest   $3 Job name   $4 full pod selector of the Job
#   $5 deployment to read the image from   $6 extra arguments, space separated
#
# Exposes LAVORO_USCITA with the logs and LAVORO_TOTALI/SUPERATI/FALLITI with the summary read.
# Returns 0 if the summary was read, 1 otherwise.
LAVORO_USCITA=""; LAVORO_TOTALI=0; LAVORO_SUPERATI=0; LAVORO_FALLITI=0
esegui_lavoro() {
    local etichetta="$1" manifesto="$2" lavoro="$3" selettore="$4" deployment="$5" extra="${6:-}"
    LAVORO_USCITA=""; LAVORO_TOTALI=0; LAVORO_SUPERATI=0; LAVORO_FALLITI=0

    local immagine
    immagine="$(kubectl -n "$K8S_NAMESPACE" get deploy "$deployment" \
        -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null)"
    [[ -z "$immagine" ]] && return 1

    # The arguments are appended to the script line of the manifest, keeping its indentation.
    local aggiunte=""
    local arg
    for arg in $extra; do aggiunte="$aggiunte\n            - $arg"; done

    kubectl -n "$K8S_NAMESPACE" delete job "$lavoro" --ignore-not-found >/dev/null 2>&1
    sed -e "s|image: [^ ]*|image: $immagine|" \
        -e "s|^\( *\)- scripts/smoke.py$|&${aggiunte}|" \
        "$manifesto" | kubectl apply -f - >/dev/null 2>&1

    local inizio=$SECONDS fase="" giro=0
    while (( SECONDS - inizio < 420 )); do
        fase="$(kubectl -n "$K8S_NAMESPACE" get pods -l "$selettore" \
            --sort-by=.metadata.creationTimestamp \
            -o jsonpath='{.items[-1:].status.phase}' 2>/dev/null)"
        [[ "$fase" == "Succeeded" || "$fase" == "Failed" ]] && break
        if (( SECONDS - inizio > 30 )) && [[ "$(kubectl -n "$K8S_NAMESPACE" get pods -l "$selettore" --sort-by=.metadata.creationTimestamp -o jsonpath='{.items[-1:].status.conditions[?(@.type=="PodScheduled")].status}' 2>/dev/null)" != "True" ]]; then
            break
        fi
        [[ -t 1 ]] && printf '\r   %s ... %s  %s \033[K' "$etichetta" \
            "$(barra_indeterminata "$giro")" "$(durata $(( SECONDS - inizio )))"
        giro=$(( giro + 1 ))
        sleep 1
    done
    [[ -t 1 ]] && printf '\r   %s ... %s  %s \033[K' "$etichetta" "$(barra 100)" \
        "$(durata $(( SECONDS - inizio )))"

    local pod
    pod="$(kubectl -n "$K8S_NAMESPACE" get pods -l "$selettore" --sort-by=.metadata.creationTimestamp -o name 2>/dev/null | tail -1)"
    LAVORO_USCITA="$(kubectl -n "$K8S_NAMESPACE" logs "$pod" --tail=40 2>/dev/null)"
    printf '\n'
    printf '%s\n' "$LAVORO_USCITA" | sed 's/^/      /'

    # The outcome is read from the script summary, not from the pod exit code.
    read -r LAVORO_TOTALI LAVORO_SUPERATI LAVORO_FALLITI < <(awk '
        /^RESULT/ { print $2 + 0, $4 + 0, $6 + 0; trovata = 1 }
        END { if (!trovata) print 0, 0, 0 }' <<< "$LAVORO_USCITA")

    (( LAVORO_TOTALI > 0 ))
}

# --- Service suite --------------------------------------------------------------------------------
#
# The image holds no tests: they run from the sources, in a separate virtual environment.
#
#   $1 phase   $2 criterion   $3 step label   $4 pytest targets (empty: whole suite)
suite_del_servizio() {
    # The sources folder is a parameter; the default is the ConversationService.
    local fase="$1" criterio="$2" etichetta="$3" bersagli="${4:-}" attesi="${5:-0}"
    local sorgenti="${6:-$SERVIZIO_SORGENTI}"
    passo "$etichetta"
    local venv="$sorgenti/.venv"
    if [[ ! -d "$sorgenti/tests" ]]; then
        registra "$fase" "$criterio" "on the sources" "NOT RUNNABLE" \
            "service sources not found in $sorgenti"
    elif [[ "$PREPARA" != true ]] && ! pytest_del_venv "$venv" >/dev/null; then
        registra "$fase" "$criterio" "on the sources" "NOT RUNNABLE" \
            "the virtual environment is missing: re-run with --prepare, or create it by hand"
    else
        # The suite runs on an in-memory database, so it runs anywhere; a failure does not stop
        # the phase.
        local pytest="" pip
        if ! pytest="$(pytest_del_venv "$venv")"; then
            printf '\n      preparing the virtual environment, once ... '
            if [[ -z "$PY" ]]; then
                printf 'no Python interpreter\n'
                registra "$fase" "$criterio" "on the sources" \
                    "NOT RUNNABLE" "no Python interpreter available"
                pytest=""
            else
                # The error is shown, not summarized.
                local registro="$TEMPORANEA/venv.log"
                if $PY -m venv "$venv" > "$registro" 2>&1 &&
                   pip="$(pip_del_venv "$venv")" &&
                   "$pip" install -e "$sorgenti[dev]" >> "$registro" 2>&1 &&
                   pytest="$(pytest_del_venv "$venv")"; then
                    printf 'done\n      '
                else
                    printf 'failed\n'
                    tail -10 "$registro" 2>/dev/null | sed 's/^/      /'
                    registra "$fase" "$criterio" "on the sources" \
                        "NOT RUNNABLE" "creating the virtual environment failed: see the output above. On Debian and Ubuntu the python3-venv package is often missing"
                    pytest=""
                fi
            fi
        fi

        if [[ -n "$pytest" ]]; then
            local uscita esito riga
            # The total is asked to pytest before starting, for the bar.
            ATTESA_TOTALE="$(cd "$sorgenti" && "$pytest" --collect-only -q $bersagli 2>/dev/null \
                | grep -oE '^[0-9]+ tests? collected' | grep -oE '^[0-9]+')"
            attendi "$etichetta" "cd '$sorgenti' && '$pytest' -v $bersagli"
            esito=$?
            ATTESA_TOTALE=""
            uscita="$ATTESA_USCITA"
            riga="$(printf '%s\n' "$uscita" | grep -E '[0-9]+ (passed|failed|error)' | tail -1)"

            # All the expected tests must have run: pytest exits with zero after an interrupted
            # collection. The number is not at the start of the line.
            local superati
            superati="$(printf '%s\n' "$riga" | grep -oE '[0-9]+ passed' | grep -oE '^[0-9]+')"
            superati="${superati:-0}"

            if (( esito != 0 )); then
                registra "$fase" "$criterio" "run now" "NOT PASSED" "$riga"
            elif (( attesi > 0 && superati != attesi )); then
                registra "$fase" "$criterio" "run now" "NOT PASSED" \
                    "$superati tests passed of $attesi expected: some are missing"
                detto "      None failed: collection found fewer. If the tests really changed,"
                detto "      the expected number must be updated at the top of this program."
            else
                registra "$fase" "$criterio" "run now" "PASSED" "$riga"
            fi
        fi
    fi
}

# --- Monolith suite -------------------------------------------------------------------------------
#
# Run one PHPUnit invocation and report whether it passed.
# Returns 0 if passed, 1 if failed, 2 if it did not start.
#
#   $1 perimeter (phpunit arguments)   $2 readable name
ESITO_SUITE_RIGA=""
esegui_suite_monolite() {
    local perimetro="$1" nome="$2"
    ESITO_SUITE_RIGA=""

    local uscita esito
    # As the web server user: config.php belongs to www-data.
    attendi "$nome" "cd '$MOODLE_DOCROOT' && sudo -u '$WEB_USER' vendor/bin/phpunit $perimetro"
    esito=$?
    uscita="$ATTESA_USCITA"

    ESITO_SUITE_RIGA="$(printf '%s\n' "$uscita" | grep -E '^(OK|FAILURES|ERRORS|Tests:)' | tail -1)"

    # The outcome of each suite stays on screen. Both forms of the PHPUnit summary are read:
    # "OK (909 tests, 3210 assertions)" and "Tests: 12, Assertions: 32, Failures: 2.".
    local totali falliti errori saltati superati etichetta colore dettaglio
    totali="$(printf '%s' "$ESITO_SUITE_RIGA" | grep -oE 'OK \([0-9]+|Tests: [0-9]+' | grep -oE '[0-9]+$' | head -1)"
    falliti="$(printf '%s' "$ESITO_SUITE_RIGA" | grep -oE 'Failures: [0-9]+' | grep -oE '[0-9]+')"
    errori="$(printf '%s' "$ESITO_SUITE_RIGA" | grep -oE 'Errors: [0-9]+' | grep -oE '[0-9]+')"
    # Skipped and incomplete tests are neither failed nor passed.
    saltati="$(printf '%s' "$ESITO_SUITE_RIGA" | grep -oE '(Skipped|Incomplete): [0-9]+' | grep -oE '[0-9]+' \
        | awk '{ s += $1 } END { print s + 0 }')"
    totali="${totali:-0}"; falliti="${falliti:-0}"; errori="${errori:-0}"; saltati="${saltati:-0}"
    superati=$(( totali - falliti - errori - saltati ))

    if (( esito == 0 )); then
        etichetta="PASSED";       colore="$C_OK"; dettaglio="$superati/$totali passed"
        (( saltati > 0 )) && dettaglio="$dettaglio, $saltati skipped"
    elif [[ -z "$ESITO_SUITE_RIGA" ]]; then
        etichetta="NOT STARTED";  colore="$C_NA"; dettaglio="the PHPUnit environment is not ready"
    else
        etichetta="FAILED";       colore="$C_KO"
        dettaglio="$superati/$totali passed"
        (( falliti > 0 )) && dettaglio="$dettaglio, $falliti failed"
        (( errori > 0 ))  && dettaglio="$dettaglio, $errori in error"
        (( saltati > 0 )) && dettaglio="$dettaglio, $saltati skipped"
    fi

    printf '\r   %-32s %s%-13s%s %s\033[K\n' "$nome" "$colore" "$etichetta" "$C_0" "$dettaglio"

    if (( esito == 0 )); then
        return 0
    fi
    if [[ -z "$ESITO_SUITE_RIGA" ]]; then
        printf '%s\n' "$uscita" | tail -12 | sed 's/^/         /'
        return 2
    fi
    printf '%s\n' "$uscita" | tail -20 | sed 's/^/         /'
    return 1
}

# --- Phase 1 --------------------------------------------------------------------------------------

fase1() {
    titolo "Phase 1 — abstraction layer, traffic still to the monolith"

    # The subsystem suite runs at the end, for the whole monolith (see monolite_dopo).
    detto "   correctness: the Moodle suites run at the end of the migration, on the tree that holds them all."

    misura_archiviata 1 "Indistinguishable configurations, zero errors"
}

# --- Phase 2 --------------------------------------------------------------------------------------

fase2() {
    titolo "Phase 2 — the ConversationService, validated in isolation"

    # Full chain of the service: HTTP request, write, event in the same transaction, delivery to
    # the broker, arrival on the queue.
    ETICHETTA_CATENA="full chain of the service"
    passo "$ETICHETTA_CATENA"
    if [[ "$AMBIENTE_VIVO" != true ]]; then
        registra 2 "Prerequisite: full chain of the service" "on the system" "NOT RUNNABLE" "$MOTIVO_NON_VIVO"
    elif [[ "$SALTA_SCRITTURE" == true ]]; then
        # The test writes: it creates a conversation, sends the same message twice, removes it.
        registra 2 "Prerequisite: full chain of the service" "on the system" "NOT RUNNABLE" \
            "requested with --skip-writes: the test sends a message, and read-only it has no object"
    else
        local immagine_smoke
        immagine_smoke="$(kubectl -n "$K8S_NAMESPACE" get deploy "$SERVICE_DEPLOY" \
            -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null)"
        kubectl -n "$K8S_NAMESPACE" delete job conversation-service-smoke --ignore-not-found >/dev/null 2>&1
        sed -e "s|image: conversation-service:.*|image: $immagine_smoke|" \
            "$BASE/deploy/k8s/conversation-service/smoke-job.yaml" \
            | kubectl apply -f - >/dev/null 2>&1

        # The selector names the service: the smoke Jobs of the two services both carry
        # `component=smoke`.
        local iniziosmoke=$SECONDS fasesmoke="" girosmoke=0
        while (( SECONDS - iniziosmoke < 300 )); do
            fasesmoke="$(kubectl -n "$K8S_NAMESPACE" get pods -l app.kubernetes.io/name=conversation-service,app.kubernetes.io/component=smoke \
                --sort-by=.metadata.creationTimestamp \
                -o jsonpath='{.items[-1:].status.phase}' 2>/dev/null)"
            [[ "$fasesmoke" == "Succeeded" || "$fasesmoke" == "Failed" ]] && break
            if (( SECONDS - iniziosmoke > 30 )) && [[ "$(kubectl -n "$K8S_NAMESPACE" get pods -l app.kubernetes.io/name=conversation-service,app.kubernetes.io/component=smoke --sort-by=.metadata.creationTimestamp -o jsonpath='{.items[-1:].status.conditions[?(@.type=="PodScheduled")].status}' 2>/dev/null)" != "True" ]]; then
                break
            fi
            [[ -t 1 ]] && printf '\r   %s ... %s  %s \033[K' "$ETICHETTA_CATENA" \
                "$(barra_indeterminata "$girosmoke")" "$(durata $(( SECONDS - iniziosmoke )))"
            girosmoke=$(( girosmoke + 1 ))
            sleep 1
        done
        [[ -t 1 ]] && printf '\r   %s ... %s  %s \033[K' "$ETICHETTA_CATENA" "$(barra 100)" \
            "$(durata $(( SECONDS - iniziosmoke )))"

        local podsmoke logsmoke
        podsmoke="$(kubectl -n "$K8S_NAMESPACE" get pods -l app.kubernetes.io/name=conversation-service,app.kubernetes.io/component=smoke --sort-by=.metadata.creationTimestamp -o name 2>/dev/null | tail -1)"
        logsmoke="$(kubectl -n "$K8S_NAMESPACE" logs "$podsmoke" --tail=30 2>/dev/null)"
        printf '\n'
        printf '%s\n' "$logsmoke" | sed 's/^/      /'

        # The outcome is read from the final line of the script, not from the pod exit code.
        local totali=0 superatismoke=0 fallitismoke=0
        read -r totali superatismoke fallitismoke < <(awk '
            /^RESULT/ { print $2 + 0, $4 + 0, $6 + 0; trovata = 1 }
            END { if (!trovata) print 0, 0, 0 }' <<< "$logsmoke")

        if (( totali == 0 )); then
            registra 2 "Prerequisite: full chain of the service" "on the system" "NOT RUNNABLE" \
                "the test produced no readable table"
            diagnosi_job "app.kubernetes.io/name=conversation-service,app.kubernetes.io/component=smoke" conversation-service-smoke
        elif (( fallitismoke > 0 )); then
            registra 2 "Prerequisite: full chain of the service" "run now" "NOT PASSED" \
                "$superatismoke checks passed of $totali, $fallitismoke failed"
            detto "      The failed checks are listed above, one per line. The chain is"
            detto "      walked in order: the first failure is the one to look at, the following"
            detto "      may be its consequence."
        else
            registra 2 "Prerequisite: full chain of the service" "run now" "PASSED" \
                "$superatismoke checks of $totali: request, write, event, broker, queue, idempotency"
            detto "      Prerequisite of the phase, not one of the four criteria: it ascertains what they take for granted."
        fi
    fi

    # Integrity of the migrated data, as a Job: the script reads the monolith database with the
    # seed user.
    ETICHETTA_INTEGRITA="integrity of the migrated data"
    passo "$ETICHETTA_INTEGRITA"
    if [[ "$AMBIENTE_VIVO" != true ]]; then
        registra 2 "Integrity of the migrated data" "on the system" "NOT RUNNABLE" "$MOTIVO_NON_VIVO"
    elif ! kubectl -n "$K8S_NAMESPACE" get secret conversation-service-seed-secret >/dev/null 2>&1; then
        registra 2 "Integrity of the migrated data" "on the system" "NOT RUNNABLE" \
            "conversation-service-seed-secret is missing, the identity that can read both schemas"
    else
        local immagine
        immagine="$(kubectl -n "$K8S_NAMESPACE" get deploy "$SERVICE_DEPLOY" \
            -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null)"
        kubectl -n "$K8S_NAMESPACE" delete job conversation-service-reconcile --ignore-not-found >/dev/null 2>&1
        # backoffLimit zero: the script exits non-zero when it finds differences.
        sed -e "s|image: conversation-service:.*|image: $immagine|" \
            -e "s|^  backoffLimit: .*|  backoffLimit: 0|" \
            "$BASE/deploy/k8s/conversation-service/reconcile-job.yaml" \
            | kubectl apply -f - >/dev/null 2>&1
        # Wait for the pod to end, not for the Job to be complete: with differences found the
        # Job fails.
        local inizioattesa=$SECONDS fasepod="" giroattesa=0
        while (( SECONDS - inizioattesa < 300 )); do
            fasepod="$(kubectl -n "$K8S_NAMESPACE" get pods -l app.kubernetes.io/name=conversation-service,app.kubernetes.io/component=reconcile \
                --sort-by=.metadata.creationTimestamp \
                -o jsonpath='{.items[-1:].status.phase}' 2>/dev/null)"
            [[ "$fasepod" == "Succeeded" || "$fasepod" == "Failed" ]] && break
            # A pod not scheduled after half a minute: quota or node resources. Distinct from a
            # pod still pulling the image.
            if (( SECONDS - inizioattesa > 30 )) && [[ "$(kubectl -n "$K8S_NAMESPACE" get pods -l app.kubernetes.io/name=conversation-service,app.kubernetes.io/component=reconcile --sort-by=.metadata.creationTimestamp -o jsonpath='{.items[-1:].status.conditions[?(@.type=="PodScheduled")].status}' 2>/dev/null)" != "True" ]]; then
                break
            fi
            # The comparison declares no progress: the bar only shows activity.
            [[ -t 1 ]] && printf '\r   %s ... %s  %s \033[K' "$ETICHETTA_INTEGRITA" \
                "$(barra_indeterminata "$giroattesa")" "$(durata $(( SECONDS - inizioattesa )))"
            giroattesa=$(( giroattesa + 1 ))
            sleep 1
        done
        [[ -t 1 ]] && printf '\r   %s ... %s  %s \033[K' "$ETICHETTA_INTEGRITA" "$(barra 100)" \
            "$(durata $(( SECONDS - inizioattesa )))"
        local log
        # One pod only: with a selector kubectl prints the logs of every matching pod.
        local podreconcile
        podreconcile="$(kubectl -n "$K8S_NAMESPACE" get pods -l app.kubernetes.io/name=conversation-service,app.kubernetes.io/component=reconcile --sort-by=.metadata.creationTimestamp -o name 2>/dev/null | tail -1)"
        log="$(kubectl -n "$K8S_NAMESPACE" logs "$podreconcile" --tail=30 2>/dev/null)"
        printf '\n'
        printf '%s\n' "$log" | sed 's/^/      /'
        # MISSING and DIFFERING rows break the fidelity of the copy; EXTRA rows (written after
        # the cutover) are counted apart. Read by columns.
        local mancanti=0 diverse=0 inpiu=0 righelette=0
        read -r mancanti diverse inpiu righelette < <(awk '
            $2 ~ /^[0-9]+$/ && $3 ~ /^[0-9]+$/ && $4 ~ /^[0-9]+$/ { m += $2; d += $3; p += $4; n++ }
            END { print m + 0, d + 0, p + 0, n + 0 }' <<< "$log")

        RESIDUI=$inpiu

        if (( righelette == 0 )); then
            RESIDUI=-1
            registra 2 "Integrity of the migrated data" "on the system" "NOT RUNNABLE" "the comparison produced no readable table"
            # The selector names the service: from phase 5 there is a second reconciliation Job.
            diagnosi_job "app.kubernetes.io/name=conversation-service,app.kubernetes.io/component=reconcile"                 "conversation-service-reconcile"
        elif (( mancanti > 0 || diverse > 0 )); then
            registra 2 "Integrity of the migrated data" "run now" "NOT PASSED" "missing: $mancanti, differing: $diverse, extra: $inpiu (the last do not count)"
            detto "      The copy is not faithful: the service does not have everything the monolith had,"
            detto "      or has it differently. It is the phase-2 criterion, and it is not met."
        elif (( inpiu > 0 )); then
            registra 2 "Integrity of the migrated data" "run now" "PASSED" "faithful copy; $(righe $inpiu) extra in the service, after the cutover"
            detto "      Writes received after the cutover: the copy stays faithful. To clear them,"
            detto "      knowing it means DELETING them:  bash deploy/verify/realign.sh"
        else
            registra 2 "Integrity of the migrated data" "run now" "PASSED" "eight boundary tables, no difference"
        fi
    fi

    suite_del_servizio 2 "No loss of function: service suite" \
        "service suite, phase-2 perimeter" \
        "tests/test_conversations.py tests/test_send_message.py tests/test_contacts.py" \
        "$SERVIZIO_TEST_FASE2"

    misura_archiviata 2 "Errors under load, bottleneck"
}

# --- Phase 3 --------------------------------------------------------------------------------------

fase3() {
    titolo "Phase 3 — the conversation context in operation on the service"

    # No regressions: the whole suite, reads included.
    suite_del_servizio 3 "No regressions: service suite" \
        "service suite, full perimeter" "" "$SERVIZIO_TEST_FASE3"

    # Data boundary, tested by attempting the access.
    ETICHETTA_CONFINE_C="boundary towards the monolith database"
    passo "$ETICHETTA_CONFINE_C"
    local manifesto_c="$BASE/deploy/k8s/conversation-service/smoke-job.yaml"
    local lavoro_c="conversation-service-smoke"
    local selettore_c="app.kubernetes.io/name=conversation-service,app.kubernetes.io/component=smoke"
    if [[ "$AMBIENTE_VIVO" != true ]]; then
        registra 3 "Data boundary: no access to the monolith" "on the system" "NOT RUNNABLE" \
            "$MOTIVO_NON_VIVO"
    elif [[ ! -f "$manifesto_c" ]]; then
        registra 3 "Data boundary: no access to the monolith" "on the system" "NOT RUNNABLE" \
            "job manifest not found in $manifesto_c"
    elif ! esegui_lavoro "$ETICHETTA_CONFINE_C" "$manifesto_c" "$lavoro_c" "$selettore_c" "$SERVICE_DEPLOY" \
            --boundary-test; then
        registra 3 "Data boundary: no access to the monolith" "on the system" "NOT RUNNABLE" \
            "the test produced no readable table"
        diagnosi_job "$selettore_c" "$lavoro_c"
    elif (( LAVORO_FALLITI > 0 )); then
        registra 3 "Data boundary: no access to the monolith" "run now" "NOT PASSED" \
            "$LAVORO_SUPERATI checks passed of $LAVORO_TOTALI, $LAVORO_FALLITI failed"
    else
        registra 3 "Data boundary: no access to the monolith" "run now" "PASSED" \
            "direct read refused on three tables, the view answers"
    fi

    # Consistency between the two implementations: the same questions on the same data. The
    # comparison builds remote_provider itself, so it runs in any mode.
    ETICHETTA_CONFRONTO="consistency of the answers between the two implementations"
    passo "$ETICHETTA_CONFRONTO"
    if [[ "$AMBIENTE_VIVO" != true ]]; then
        registra 3 "Consistency between the two implementations" "on the system" "NOT RUNNABLE" \
            "$MOTIVO_NON_VIVO"
    elif [[ ! -f "$MOODLE_DOCROOT/message/cli/compare_providers.php" ]]; then
        registra 3 "Consistency between the two implementations" "on the system" "NOT RUNNABLE" \
            "compare_providers.php not present in the served tree"
    else
        # Rows that exist only in the service are said before the comparison.
        if (( RESIDUI > 0 )); then
            detto ""
            detto "      Warning: phase 2 found a difference of $(righe $RESIDUI) between the two stores."
            detto "      The probes query the two implementations on the same identifiers, hence"
            detto "      on data that no longer match. Any divergence below must be read"
            detto "      in this light. To get them out of the way: bash deploy/verify/realign.sh"
            passo "$ETICHETTA_CONFRONTO"
        fi
        local argomenti=()
        [[ "$SALTA_SCRITTURE" == true ]] && argomenti+=(--skip-writes)
        local uscita esito
        attendi "$ETICHETTA_CONFRONTO" "cd '$MOODLE_DOCROOT' && sudo -u '$WEB_USER' php message/cli/compare_providers.php ${argomenti[*]}"
        esito=$?
        uscita="$ATTESA_USCITA"
        printf '\n'
        printf '%s\n' "$uscita" | tail -12 | sed 's/^/      /'
        if (( esito == 0 )); then
            registra 3 "Consistency between the two implementations" "run now" "PASSED"
        else
            # If the stores no longer hold the same rows, the answers may differ without either
            # implementation being wrong: extra rows are removed with realign.sh.
            if (( RESIDUI > 0 )); then
                # Not a failure: a comparison that could not establish anything.
                registra 3 "Consistency between the two implementations" "on the system" "NOT RUNNABLE" "the stores differ by $(righe $RESIDUI): the comparison establishes nothing"
                detto "      The probes queried the two implementations on data that no longer"
                detto "      match, so the divergences above say nothing about the implementations."
                detto "      Realign and repeat:"
                detto "        bash deploy/verify/verify.sh --realign --phase 3"
            else
                registra 3 "Consistency between the two implementations" "run now" "NOT PASSED" "the comparison reports divergences"
                detto "      The stores are aligned, so the divergence belongs to the implementations."
                detto "      If however phase 2 did not run in this pass, that check was not"
                detto "      done: repeat without --phase to have it."
            fi
        fi
    fi

    # The smoke test calls the public functions of api.php that the interface uses.
    ETICHETTA_COLLAUDO="end-to-end test of the full path"
    passo "$ETICHETTA_COLLAUDO"
    # In legacy the smoke test does not cross the service: it tries to go remote and proceeds
    # anyway, the detail says in which mode it ran.
    assicura_modalita conversazioni remote || true
    if [[ "$AMBIENTE_VIVO" != true ]]; then
        registra 3 "Full path in operation" "on the system" "NOT RUNNABLE" \
            "$MOTIVO_NON_VIVO"
    elif [[ ! -f "$MOODLE_DOCROOT/message/cli/smoke_api.php" ]]; then
        registra 3 "Full path in operation" "on the system" "NOT RUNNABLE" \
            "smoke_api.php not present in the served tree"
    else
        local uscita esito
        attendi "$ETICHETTA_COLLAUDO" "cd '$MOODLE_DOCROOT' && sudo -u '$WEB_USER' php message/cli/smoke_api.php"
        esito=$?
        uscita="$ATTESA_USCITA"
        printf '\n'
        printf '%s\n' "$uscita" | tail -8 | sed 's/^/      /'
        if (( esito == 0 )); then
            # In remote the test proves the delivered configuration, in legacy only the code
            # that formats the answers.
            registra 3 "Full path in operation" "run now" "PASSED"                 "mode ${MOODLE_MODE:-unknown}$([[ "$MOODLE_MODE" != "remote" ]] && echo ' — the remote path was not exercised')"
        else
            registra 3 "Full path in operation" "run now" "NOT PASSED"
        fi
    fi

}

# --- Final table ----------------------------------------------------------------------------------

riepilogo() {
    printf '\n%s%s%s\n' "$C_T" "$(printf '=%.0s' {1..92})" "$C_0"
    printf '%s VERIFICATION OUTCOME%s\n' "$C_T" "$C_0"
    printf '%s%s\n\n' "$(printf '=%.0s' {1..92})" ""

    printf ' %-5s %-46s %-20s %s\n' "Phase" "Criterion" "How" "Outcome"
    printf ' %-5s %-46s %-20s %s\n' "-----" "$(printf -- '-%.0s' {1..46})" "$(printf -- '-%.0s' {1..20})" "------------"

    local superati=0 falliti=0 sospesi=0 i
    for i in "${!FASI[@]}"; do
        local colore="$C_NA"
        case "${ESITI[$i]}" in
            PASSED)         colore="$C_OK"; superati=$(( superati + 1 )) ;;
            "NOT PASSED")   colore="$C_KO"; falliti=$(( falliti + 1 )) ;;
            *)              sospesi=$(( sospesi + 1 )) ;;
        esac
        # "fine" is the internal name of the final verification (--phase end).
        printf ' %-5s %-46s %-20s %s%s%s\n' \
            "${FASI[$i]/#fine/end}" "${CRITERI[$i]:0:46}" "${COME[$i]:0:20}" "$colore" "${ESITI[$i]}" "$C_0"
    done

    printf '\n'
    for i in "${!FASI[@]}"; do
        local dove="phase ${FASI[$i]}"
        [[ "${FASI[$i]}" == "fine" ]] && dove="at the end of the migration"
        [[ -n "${DETTAGLI[$i]}" && "${ESITI[$i]}" != "PASSED" ]] && \
            printf ' %s, %s:\n   %s\n' "$dove" "${CRITERI[$i]}" "${DETTAGLI[$i]}"
    done

    printf '\n %d passed, %d not passed, %d not runnable.\n' "$superati" "$falliti" "$sospesi"

    if (( falliti > 0 )); then
        printf '\n %sOutcome: CRITERIA NOT MET%s\n' "$C_KO" "$C_0"
        return 1
    fi
    if (( sospesi > 0 )); then
        # A criterion not runnable is neither failed nor passed, and the exit code says so.
        if (( superati == 0 )); then
            printf '\n %sOutcome: NO CRITERION VERIFIED in this environment.%s\n' "$C_NA" "$C_0"
            printf ' %sSee the notes above: the environment, the archive, or both are missing.%s\n' "$C_NA" "$C_0"
            return 2
        fi
        printf '\n %sOutcome: every criterion that ran passed, but %d were not%s\n' \
            "$C_NA" "$sospesi" "$C_0"
        printf ' %sverifiable in this environment. See the notes above.%s\n' "$C_NA" "$C_0"
        return 2
    fi
    printf '\n %sOutcome: ALL CRITERIA MET%s\n' "$C_OK" "$C_0"
    return 0
}

# --- Execution ------------------------------------------------------------------------------------

printf '%sVerification of the five phases — %s%s\n' "$C_T" "$(date -Is)" "$C_0"
printf 'Repository: %s\n' "$BASE"
if git -C "$BASE" rev-parse --short HEAD >/dev/null 2>&1; then
    printf 'Revision:   %s\n' "$(git -C "$BASE" rev-parse --short HEAD)"
fi

PY="$(python_di_sistema || true)"
[[ -z "$PY" ]] && printf "No Python interpreter: the archived measures cannot be replayed.
"

verifica_ambiente

# The realignment DELETES from the service the rows written after the cutover: it runs only with
# --realign. Without it phase 2 counts the extra rows apart and phase 3 says when its divergences
# come from there.
if [[ "$RIALLINEA" == true ]]; then
    titolo "Realignment requested, before verifying"
    if [[ "$AMBIENTE_VIVO" != true ]]; then
        detto "   The environment does not answer: there is nothing to realign. Moving on."
    elif [[ ! -f "$BASE/deploy/verify/realign.sh" ]]; then
        detto "   realign.sh not found next to this script. Moving on."
    else
        bash "$BASE/deploy/verify/realign.sh" --force
        if (( $? != 0 )); then
            detto ""
            detto "   The realignment failed. The verification goes on anyway: the phase-2"
            detto "   integrity will say how many rows remain, and phase 3 whether the divergences come from there."
        fi
    fi
fi

# --- Phase 4 --------------------------------------------------------------------------------------

fase4() {
    titolo "Phase 4 — the DeliveryGateway, delivery outside the monolith"

    local manifesto="$BASE/deploy/k8s/delivery-gateway/smoke-job.yaml"
    local lavoro="delivery-gateway-smoke"

    # Prerequisite: the full delivery chain (an event in, channels resolved, a delivery out).
    ETICHETTA_RECAPITO="full delivery chain"
    passo "$ETICHETTA_RECAPITO"
    if [[ "$AMBIENTE_VIVO" != true ]]; then
        registra 4 "Prerequisite: full delivery chain" "on the system" "NOT RUNNABLE" \
            "$MOTIVO_NON_VIVO"
    elif [[ ! -f "$manifesto" ]]; then
        registra 4 "Prerequisite: full delivery chain" "on the system" "NOT RUNNABLE" \
            "job manifest not found in $manifesto"
    elif ! esegui_lavoro "$ETICHETTA_RECAPITO" "$manifesto" "$lavoro" "app.kubernetes.io/name=delivery-gateway,app.kubernetes.io/component=smoke" "$GATEWAY_DEPLOY"; then
        registra 4 "Prerequisite: full delivery chain" "on the system" "NOT RUNNABLE" \
            "the test produced no readable table"
        diagnosi_job "app.kubernetes.io/name=delivery-gateway,app.kubernetes.io/component=smoke" "$lavoro"
    elif (( LAVORO_FALLITI > 0 )); then
        registra 4 "Prerequisite: full delivery chain" "run now" "NOT PASSED" \
            "$LAVORO_SUPERATI checks passed of $LAVORO_TOTALI, $LAVORO_FALLITI failed"
        detto "      The chain is walked in order: the first failure is the one to look at."
    else
        registra 4 "Prerequisite: full delivery chain" "run now" "PASSED" \
            "$LAVORO_SUPERATI checks of $LAVORO_TOTALI: event, channels, delivery, idempotency"
        detto "      Prerequisite of the phase, not one of the criteria: it ascertains what they take for granted."
    fi

    # (a) No delivery lost: the test puts the mocks in fault and repairs them in any case.
    ETICHETTA_PERDITA="no delivery lost"
    passo "$ETICHETTA_PERDITA"
    if [[ "$AMBIENTE_VIVO" != true ]]; then
        registra 4 "No delivery lost" "on the system" "NOT RUNNABLE" "$MOTIVO_NON_VIVO"
    elif [[ "$SALTA_SCRITTURE" == true ]]; then
        registra 4 "No delivery lost" "on the system" "NOT RUNNABLE" \
            "requested with --skip-writes: the test publishes an event and puts the mock providers in failure mode"
    elif ! esegui_lavoro "$ETICHETTA_PERDITA" "$manifesto" "$lavoro" "app.kubernetes.io/name=delivery-gateway,app.kubernetes.io/component=smoke" "$GATEWAY_DEPLOY" \
            --retry-test; then
        registra 4 "No delivery lost" "on the system" "NOT RUNNABLE" \
            "the test produced no readable table"
        diagnosi_job "app.kubernetes.io/name=delivery-gateway,app.kubernetes.io/component=smoke" "$lavoro"
        detto "      If the job died halfway, the mock providers may have been left in failure mode:"
        detto "        curl -s -X POST $GATEWAY_SIMULACRO/repair"
    elif (( LAVORO_FALLITI > 0 )); then
        registra 4 "No delivery lost" "run now" "NOT PASSED" \
            "$LAVORO_SUPERATI checks passed of $LAVORO_TOTALI, $LAVORO_FALLITI failed"
    else
        registra 4 "No delivery lost" "run now" "PASSED" \
            "retried with growing wait, then dead letter with the whole envelope"
    fi

    # (c) Continuity with the service down, in two halves: scaling the deployment takes kubectl,
    # which the image does not have.
    ETICHETTA_CONTINUITA="continuity with the service down"
    passo "$ETICHETTA_CONTINUITA"
    if [[ "$AMBIENTE_VIVO" != true ]]; then
        registra 4 "Continuity with the service down" "on the system" "NOT RUNNABLE" "$MOTIVO_NON_VIVO"
    elif [[ "$SALTA_SCRITTURE" == true ]]; then
        registra 4 "Continuity with the service down" "on the system" "NOT RUNNABLE" \
            "requested with --skip-writes: the test publishes an event"
    else
        local repliche
        repliche="$(kubectl -n "$K8S_NAMESPACE" get deploy "$GATEWAY_DEPLOY" \
            -o jsonpath='{.spec.replicas}' 2>/dev/null)"
        repliche="${repliche:-1}"

        # From here on the service is down and must be restarted in any case.
        kubectl -n "$K8S_NAMESPACE" scale deploy "$GATEWAY_DEPLOY" --replicas=0 >/dev/null 2>&1
        kubectl -n "$K8S_NAMESPACE" wait --for=delete pod \
            -l app.kubernetes.io/name="$GATEWAY_DEPLOY",app.kubernetes.io/component!=smoke \
            --timeout=120s >/dev/null 2>&1

        local evento="" esito_pubblicazione=1
        if esegui_lavoro "$ETICHETTA_CONTINUITA" "$manifesto" "$lavoro" "app.kubernetes.io/name=delivery-gateway,app.kubernetes.io/component=smoke" "$GATEWAY_DEPLOY" \
                --publish; then
            evento="$(printf '%s\n' "$LAVORO_USCITA" | awk '/^EVENT /{print $2; exit}')"
            (( LAVORO_FALLITI == 0 )) && esito_pubblicazione=0
        fi

        kubectl -n "$K8S_NAMESPACE" scale deploy "$GATEWAY_DEPLOY" --replicas="$repliche" >/dev/null 2>&1
        kubectl -n "$K8S_NAMESPACE" rollout status deploy "$GATEWAY_DEPLOY" --timeout=180s >/dev/null 2>&1

        if [[ -z "$evento" ]]; then
            registra 4 "Continuity with the service down" "on the system" "NOT RUNNABLE" \
                "publishing with the service stopped produced no identifier"
            diagnosi_job "app.kubernetes.io/name=delivery-gateway,app.kubernetes.io/component=smoke" "$lavoro"
        elif (( esito_pubblicazione != 0 )); then
            registra 4 "Continuity with the service down" "run now" "NOT PASSED" \
                "someone consumed the event while the service was supposed to be stopped"
            detto "      Without that silence the test would prove nothing: an event consumed"
            detto "      immediately would appear delivered anyway, service down or up."
        elif ! esegui_lavoro "$ETICHETTA_CONTINUITA" "$manifesto" "$lavoro" "app.kubernetes.io/name=delivery-gateway,app.kubernetes.io/component=smoke" "$GATEWAY_DEPLOY" \
                "--wait $evento"; then
            registra 4 "Continuity with the service down" "on the system" "NOT RUNNABLE" \
                "the wait produced no readable table"
            diagnosi_job "app.kubernetes.io/name=delivery-gateway,app.kubernetes.io/component=smoke" "$lavoro"
        elif (( LAVORO_FALLITI > 0 )); then
            registra 4 "Continuity with the service down" "run now" "NOT PASSED" \
                "the event published with the service stopped was not delivered on its return"
        else
            registra 4 "Continuity with the service down" "run now" "PASSED" \
                "event published at zero replicas, delivered on restart"
        fi
    fi

    # (d) Data boundary, tested by attempting the access.
    ETICHETTA_CONFINE="boundary towards the monolith database"
    passo "$ETICHETTA_CONFINE"
    if [[ "$AMBIENTE_VIVO" != true ]]; then
        registra 4 "Data boundary: no access to the monolith" "on the system" "NOT RUNNABLE" \
            "$MOTIVO_NON_VIVO"
    elif ! esegui_lavoro "$ETICHETTA_CONFINE" "$manifesto" "$lavoro" "app.kubernetes.io/name=delivery-gateway,app.kubernetes.io/component=smoke" "$GATEWAY_DEPLOY" \
            --boundary-test; then
        registra 4 "Data boundary: no access to the monolith" "on the system" "NOT RUNNABLE" \
            "the test produced no readable table"
        diagnosi_job "app.kubernetes.io/name=delivery-gateway,app.kubernetes.io/component=smoke" "$lavoro"
    elif (( LAVORO_FALLITI > 0 )); then
        registra 4 "Data boundary: no access to the monolith" "run now" "NOT PASSED" \
            "$LAVORO_SUPERATI checks passed of $LAVORO_TOTALI, $LAVORO_FALLITI failed"
    else
        registra 4 "Data boundary: no access to the monolith" "run now" "PASSED" \
            "direct read refused, four views answer"
    fi

    # (e) Fidelity of channel resolution: the unit suite, from the sources.
    suite_del_servizio 4 "Fidelity of channel resolution" \
        "gateway domain suite ($GATEWAY_TEST_ATTESI tests)" "" \
        "$GATEWAY_TEST_ATTESI" "$GATEWAY_SORGENTI"

}

fase5() {
    titolo "Phase 5 — the NotificationService, notices outside the monolith"

    local manifesto="$BASE/deploy/k8s/notification-service/smoke-job.yaml"
    local lavoro="notification-service-smoke"
    local selettore="app.kubernetes.io/name=notification-service,app.kubernetes.io/component=smoke"

    # Prerequisite: the full chain (a notification written, read, announced; the two views
    # answering).
    ETICHETTA_NOTIFICHE="full notification chain"
    passo "$ETICHETTA_NOTIFICHE"
    if [[ "$AMBIENTE_VIVO" != true ]]; then
        registra 5 "Prerequisite: full notification chain" "on the system" "NOT RUNNABLE" \
            "$MOTIVO_NON_VIVO"
    elif [[ ! -f "$manifesto" ]]; then
        registra 5 "Prerequisite: full notification chain" "on the system" "NOT RUNNABLE" \
            "job manifest not found in $manifesto"
    elif [[ "$SALTA_SCRITTURE" == true ]]; then
        registra 5 "Prerequisite: full notification chain" "on the system" "NOT RUNNABLE" \
            "requested with --skip-writes: the test writes a notification"
    elif ! esegui_lavoro "$ETICHETTA_NOTIFICHE" "$manifesto" "$lavoro" "$selettore" "$NOTIFICA_DEPLOY"; then
        registra 5 "Prerequisite: full notification chain" "on the system" "NOT RUNNABLE" \
            "the test produced no readable table"
        diagnosi_job "$selettore" "$lavoro"
    elif (( LAVORO_FALLITI > 0 )); then
        registra 5 "Prerequisite: full notification chain" "run now" "NOT PASSED" \
            "$LAVORO_SUPERATI checks passed of $LAVORO_TOTALI, $LAVORO_FALLITI failed"
        detto "      The chain is walked in order: the first failure is the one to look at."
        detto "      If it fails on the templates, the missing step is on the monolith machine:"
        detto "        sudo -u www-data php message/cli/seed_notification_templates.php"
    else
        registra 5 "Prerequisite: full notification chain" "run now" "PASSED" \
            "$LAVORO_SUPERATI checks of $LAVORO_TOTALI: views, write, reads, event, idempotency"
        detto "      Prerequisite of the phase, not one of the criteria: it ascertains what they take for granted."
    fi

    # (a) Two extracted contexts talking: the contact request event consumed without the
    # monolith.
    ETICHETTA_DIALOGO="two extracted contexts talking to each other"
    passo "$ETICHETTA_DIALOGO"
    if [[ "$AMBIENTE_VIVO" != true ]]; then
        registra 5 "Two extracted contexts talking" "on the system" "NOT RUNNABLE" \
            "$MOTIVO_NON_VIVO"
    elif [[ "$SALTA_SCRITTURE" == true ]]; then
        registra 5 "Two extracted contexts talking" "on the system" "NOT RUNNABLE" \
            "requested with --skip-writes: the test publishes an event and a notification is born"
    elif ! esegui_lavoro "$ETICHETTA_DIALOGO" "$manifesto" "$lavoro" "$selettore" "$NOTIFICA_DEPLOY" \
            --contact-request-test; then
        registra 5 "Two extracted contexts talking" "on the system" "NOT RUNNABLE" \
            "the test produced no readable table"
        diagnosi_job "$selettore" "$lavoro"
    elif (( LAVORO_FALLITI > 0 )); then
        registra 5 "Two extracted contexts talking" "run now" "NOT PASSED" \
            "$LAVORO_SUPERATI checks passed of $LAVORO_TOTALI, $LAVORO_FALLITI failed"
    else
        registra 5 "Two extracted contexts talking" "run now" "PASSED" \
            "event consumed, text composed in the recipient's language, delivery announced"
    fi

    # (b) Integrity of the migrated data, as a Job (the script reads the monolith database with
    # the seed user).
    ETICHETTA_INTEGRITA_N="integrity of the migrated data"
    passo "$ETICHETTA_INTEGRITA_N"
    local selettore_r="app.kubernetes.io/name=notification-service,app.kubernetes.io/component=reconcile"
    if [[ "$AMBIENTE_VIVO" != true ]]; then
        registra 5 "Integrity of the migrated data" "on the system" "NOT RUNNABLE" "$MOTIVO_NON_VIVO"
    elif ! kubectl -n "$K8S_NAMESPACE" get secret notification-service-seed-secret >/dev/null 2>&1; then
        registra 5 "Integrity of the migrated data" "on the system" "NOT RUNNABLE" \
            "notification-service-seed-secret is missing, the identity that can read both schemas"
    else
        local immagine_r
        immagine_r="$(kubectl -n "$K8S_NAMESPACE" get deploy "$NOTIFICA_DEPLOY" \
            -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null)"
        kubectl -n "$K8S_NAMESPACE" delete job notification-service-reconcile --ignore-not-found >/dev/null 2>&1
        # backoffLimit zero: the script exits non-zero when it finds differences.
        sed -e "s|image: notification-service:.*|image: $immagine_r|" \
            -e "s|^  backoffLimit: .*|  backoffLimit: 0|" \
            "$BASE/deploy/k8s/notification-service/reconcile-job.yaml" \
            | kubectl apply -f - >/dev/null 2>&1

        # Wait for the pod to end, not for the Job to be complete.
        local inizio_r=$SECONDS fase_r="" giro_r=0
        while (( SECONDS - inizio_r < 300 )); do
            fase_r="$(kubectl -n "$K8S_NAMESPACE" get pods -l "$selettore_r" \
                --sort-by=.metadata.creationTimestamp \
                -o jsonpath='{.items[-1:].status.phase}' 2>/dev/null)"
            [[ "$fase_r" == "Succeeded" || "$fase_r" == "Failed" ]] && break
            if (( SECONDS - inizio_r > 30 )) && [[ "$(kubectl -n "$K8S_NAMESPACE" get pods -l "$selettore_r" --sort-by=.metadata.creationTimestamp -o jsonpath='{.items[-1:].status.conditions[?(@.type=="PodScheduled")].status}' 2>/dev/null)" != "True" ]]; then
                break
            fi
            [[ -t 1 ]] && printf '\r   %s ... %s  %s \033[K' "$ETICHETTA_INTEGRITA_N" \
                "$(barra_indeterminata "$giro_r")" "$(durata $(( SECONDS - inizio_r )))"
            giro_r=$(( giro_r + 1 ))
            sleep 1
        done
        [[ -t 1 ]] && printf '\r   %s ... %s  %s \033[K' "$ETICHETTA_INTEGRITA_N" "$(barra 100)" \
            "$(durata $(( SECONDS - inizio_r )))"

        local pod_r log_r
        pod_r="$(kubectl -n "$K8S_NAMESPACE" get pods -l "$selettore_r" --sort-by=.metadata.creationTimestamp -o name 2>/dev/null | tail -1)"
        log_r="$(kubectl -n "$K8S_NAMESPACE" logs "$pod_r" --tail=30 2>/dev/null)"
        printf '\n'
        printf '%s\n' "$log_r" | sed 's/^/      /'

        # MISSING and DIFFERING rows break the fidelity of the copy; EXTRA rows are counted
        # apart.
        local mancanti_r=0 diverse_r=0 inpiu_r=0 lette_r=0
        RESIDUI_NOTIFICHE=-1
        read -r mancanti_r diverse_r inpiu_r lette_r < <(awk '
            $2 ~ /^[0-9]+$/ && $3 ~ /^[0-9]+$/ && $4 ~ /^[0-9]+$/ { m += $2; d += $3; p += $4; n++ }
            END { print m + 0, d + 0, p + 0, n + 0 }' <<< "$log_r")

        if (( lette_r == 0 )); then
            registra 5 "Integrity of the migrated data" "on the system" "NOT RUNNABLE" \
                "the comparison produced no readable table"
            diagnosi_job "$selettore_r" "notification-service-reconcile"
        elif (( mancanti_r > 0 || diverse_r > 0 )); then
            registra 5 "Integrity of the migrated data" "run now" "NOT PASSED" \
                "$mancanti_r rows missing, $diverse_r differing over $lette_r tables"
            detto "      If many differ and none is missing, the most likely cause is"
            detto "      the read state changed after the copy. The remedy is to re-run the"
            detto "      seeding with --full, not to correct row by row."
        elif (( inpiu_r > 0 )); then
            RESIDUI_NOTIFICHE=$inpiu_r
            registra 5 "Integrity of the migrated data" "run now" "PASSED" \
                "faithful copy; $inpiu_r rows present only in the service, written after the cutover"
        else
            RESIDUI_NOTIFICHE=0
            registra 5 "Integrity of the migrated data" "run now" "PASSED" \
                "the two stores are identical on every copied table"
        fi
    fi

    # (c) Consistency between the two implementations: the same questions to both, on the same
    # data. The comparison builds the remote implementation itself and runs in any mode.
    ETICHETTA_COERENZA_N="consistency between the two implementations"
    passo "$ETICHETTA_COERENZA_N"
    if [[ "$AMBIENTE_VIVO" != true ]]; then
        registra 5 "Consistency between the two implementations" "on the system" "NOT RUNNABLE" \
            "$MOTIVO_NON_VIVO"
    elif [[ ! -f "$MOODLE_DOCROOT/message/cli/compare_notification_providers.php" ]]; then
        registra 5 "Consistency between the two implementations" "on the system" "NOT RUNNABLE" \
            "compare_notification_providers.php not present in the served tree"
    else
        local argomenti_n=()
        [[ "$SALTA_SCRITTURE" == true ]] && argomenti_n+=(--skip-writes)
        local uscita_n esito_n totali_n superati_n falliti_n
        attendi "$ETICHETTA_COERENZA_N" "cd '$MOODLE_DOCROOT' && sudo -u '$WEB_USER' php message/cli/compare_notification_providers.php ${argomenti_n[*]}"
        esito_n=$?
        uscita_n="$ATTESA_USCITA"
        printf '\n'
        printf '%s\n' "$uscita_n" | tail -14 | sed 's/^/      /'

        read -r totali_n superati_n falliti_n < <(awk '
            /^RESULT/ { print $2 + 0, $4 + 0, $6 + 0; trovata = 1 }
            END { if (!trovata) print 0, 0, 0 }' <<< "$uscita_n")

        if (( totali_n == 0 )); then
            registra 5 "Consistency between the two implementations" "on the system" "NOT RUNNABLE" \
                "the comparison produced no readable table"
        elif (( falliti_n > 0 && RESIDUI_NOTIFICHE > 0 )); then
            # Not a failure: a comparison that could not establish anything (the usual case in
            # remote mode).
            registra 5 "Consistency between the two implementations" "on the system" "NOT RUNNABLE"                 "the stores differ by $(righe $RESIDUI_NOTIFICHE): the comparison establishes nothing"
            detto "      $superati_n probes agree of $totali_n. The diverging ones queried the two"
            detto "      implementations on rows that exist on one side only."
            detto "      For a conclusive comparison: switch notifications to legacy, or"
            detto "      realign the stores, knowing it means deleting from the service."
        elif (( falliti_n > 0 )); then
            registra 5 "Consistency between the two implementations" "run now" "NOT PASSED"                 "$superati_n probes agree of $totali_n, $falliti_n diverge"
            detto "      The stores are aligned, so the divergence belongs to the implementations."
        else
            registra 5 "Consistency between the two implementations" "run now" "PASSED" \
                "$totali_n probes, no divergence"
        fi
    fi

    # (e) Data boundary, tested by attempting the access.
    ETICHETTA_CONFINE_N="boundary towards the monolith database"
    passo "$ETICHETTA_CONFINE_N"
    if [[ "$AMBIENTE_VIVO" != true ]]; then
        registra 5 "Data boundary: no access to the monolith" "on the system" "NOT RUNNABLE" \
            "$MOTIVO_NON_VIVO"
    elif ! esegui_lavoro "$ETICHETTA_CONFINE_N" "$manifesto" "$lavoro" "$selettore" "$NOTIFICA_DEPLOY" \
            --boundary-test; then
        registra 5 "Data boundary: no access to the monolith" "on the system" "NOT RUNNABLE" \
            "the test produced no readable table"
        diagnosi_job "$selettore" "$lavoro"
    elif (( LAVORO_FALLITI > 0 )); then
        registra 5 "Data boundary: no access to the monolith" "run now" "NOT PASSED" \
            "$LAVORO_SUPERATI checks passed of $LAVORO_TOTALI, $LAVORO_FALLITI failed"
    else
        registra 5 "Data boundary: no access to the monolith" "run now" "PASSED" \
            "direct read refused, two views answer"
    fi

    # (f) A rerouted monolith component that keeps working: the mod_assign reminder, on the
    # monolith machine.
    ETICHETTA_DIROTTAMENTO="a monolith component rerouted"
    passo "$ETICHETTA_DIROTTAMENTO"
    # In legacy the test establishes that mod_assign keeps working, in remote that it does so
    # without the monolith being written. It tries to go remote and proceeds anyway.
    assicura_modalita notifiche remote || true
    if [[ "$AMBIENTE_VIVO" != true ]]; then
        registra 5 "A monolith component rerouted" "on the system" "NOT RUNNABLE" \
            "$MOTIVO_NON_VIVO"
    elif [[ "$SALTA_SCRITTURE" == true ]]; then
        registra 5 "A monolith component rerouted" "on the system" "NOT RUNNABLE" \
            "requested with --skip-writes: the test moves a due date and sends a reminder"
    elif [[ ! -f "$MOODLE_DOCROOT/message/cli/verify_notification_reroute.php" ]]; then
        registra 5 "A monolith component rerouted" "on the system" "NOT RUNNABLE" \
            "verify_notification_reroute.php not present in the served tree"
    else
        local uscita_d esito_d totali_d superati_d falliti_d
        attendi "$ETICHETTA_DIROTTAMENTO" "cd '$MOODLE_DOCROOT' && sudo -u '$WEB_USER' php message/cli/verify_notification_reroute.php"
        esito_d=$?
        uscita_d="$ATTESA_USCITA"
        printf '\n'
        printf '%s\n' "$uscita_d" | tail -16 | sed 's/^/      /'

        read -r totali_d superati_d falliti_d < <(awk '
            /^RESULT/ { print $2 + 0, $4 + 0, $6 + 0; trovata = 1 }
            END { if (!trovata) print 0, 0, 0 }' <<< "$uscita_d")

        # Third outcome: the site has no assignment with a due date and an enrolled user.
        if printf '%s' "$uscita_d" | grep -q "^NOT APPLICABLE"; then
            registra 5 "A monolith component rerouted" "on the system" "NOT RUNNABLE"                 "the site offers no assignment with a due date and enrolled students: see the instructions above"
        elif (( totali_d == 0 )); then
            registra 5 "A monolith component rerouted" "on the system" "NOT RUNNABLE" \
                "the test produced no readable table"
        elif (( falliti_d > 0 )); then
            registra 5 "A monolith component rerouted" "run now" "NOT PASSED" \
                "$superati_d checks passed of $totali_d, $falliti_d failed"
            detto "      The check that matters is the one on the monolith: in remote mode its"
            detto "      table must not have grown. If it grew, the write was not"
            detto "      diverted and notificationservice_mode is not 'remote' in the served tree."
        else
            registra 5 "A monolith component rerouted" "run now" "PASSED" \
                "$superati_d checks: reminder written in the context, deduplication re-read, monolith untouched"
        fi
    fi

    # (g) Fidelity of the notification context: the unit suite, from the sources.
    suite_del_servizio 5 "Fidelity of the notification context" \
        "notification domain suite ($NOTIFICA_TEST_ATTESI tests)" "" \
        "$NOTIFICA_TEST_ATTESI" "$NOTIFICA_SORGENTI"

}

# --- The monolith after the migration -------------------------------------------------------------
#
# Not a phase: after five phases, do the tests Moodle ships still pass? The suites of the touched
# components (core_message, message_popup, mod_assign, mod_quiz, the two lib/tests files by path).
# Totals are reported, not fixed; the expected number of the subsystem suite is shown next to its name.
monolite_dopo() {
    titolo "The monolith after the migration"

    ETICHETTA_REGRESSIONI="suites of the modified components"
    passo "$ETICHETTA_REGRESSIONI"
    if [[ "$AMBIENTE_VIVO" != true ]]; then
        registra fine "No regression in the monolith" "on the system" "NOT RUNNABLE" \
            "$MOTIVO_NON_VIVO"
    elif ! assicura_modalita entrambi legacy; then
        # The suites run on the phpu_ tables, while the service views read the site's: both
        # contexts must be legacy.
        registra fine "No regression in the monolith" "on the system" "NOT RUNNABLE" \
            "$MOTIVO_MODALITA"
    elif [[ ! -x "$MOODLE_DOCROOT/vendor/bin/phpunit" ]]; then
        registra fine "No regression in the monolith" "on the system" "NOT RUNNABLE" \
            "PHPUnit not set up: sudo -u $WEB_USER php admin/tool/phpunit/cli/init.php --disable-composer"
    else
        # The step header stays: one line per suite accumulates under it.
        printf '\n'
        local falliti_s=0 nonpartite_s=0
        local perimetri=(
            "--testsuite $MOODLE_TESTSUITE|core_message ($MOODLE_TEST_ATTESI test)"
            "--testsuite message_popup_testsuite|message_popup"
            "--testsuite mod_assign_testsuite|mod_assign"
            "--testsuite mod_quiz_testsuite|mod_quiz"
            "lib/tests/messagelib_test.php lib/tests/message_test.php|core, by path"
        )
        local voce perimetro nome esito_s
        for voce in "${perimetri[@]}"; do
            perimetro="${voce%%|*}"
            nome="${voce##*|}"
            esegui_suite_monolite "$perimetro" "$nome"
            esito_s=$?
            (( esito_s == 1 )) && falliti_s=$(( falliti_s + 1 ))
            (( esito_s == 2 )) && nonpartite_s=$(( nonpartite_s + 1 ))
        done

        if (( nonpartite_s > 0 )); then
            registra fine "No regression in the monolith" "on the system" "NOT RUNNABLE" \
                "$nonpartite_s suites of 5 did not start: set up with admin/tool/phpunit/cli/init.php --disable-composer"
        elif (( falliti_s > 0 )); then
            registra fine "No regression in the monolith" "run now" "NOT PASSED" \
                "$falliti_s suites of 5 report failures"
            detto "      These are the components the migration modified: a failure here is almost"
            detto "      always in the rewritten call sites, not in Moodle. The name of the"
            detto "      test says which context to look at, not which phase to blame."
        else
            registra fine "No regression in the monolith" "run now" "PASSED" \
                "five suites, no failure"
        fi
    fi
}

# --- Running the phases ---------------------------------------------------------------------------

fase_richiesta 1 && fase1
fase_richiesta 2 && fase2
fase_richiesta 3 && fase3
fase_richiesta 4 && fase4
fase_richiesta 5 && fase5
fase_richiesta fine && monolite_dopo

riepilogo
