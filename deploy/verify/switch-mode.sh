#!/usr/bin/env bash
#
# Switch the site between the two implementations of a context, or of both.
#
#   sudo bash deploy/verify/switch-mode.sh legacy                   both contexts to the monolith
#   sudo bash deploy/verify/switch-mode.sh remote                   both to their services
#   sudo bash deploy/verify/switch-mode.sh legacy notifications     the notification context only
#   sudo bash deploy/verify/switch-mode.sh remote conversations     the conversation context only
#
# The two switches are `conversationservice_mode` and `notificationservice_mode`. The production
# configuration is `remote`.

set -uo pipefail

MOODLE_DOCROOT="${MOODLE_DOCROOT:-/var/www/html/moodle}"
WEB_USER="${WEB_USER:-www-data}"
# The override file included by config.php.
SOVRASCRITTURE="${SOVRASCRITTURE:-/etc/moodle/phase3-overrides.php}"

MODALITA="${1:-}"
CONTESTO="${2:-both}"
# Contexts have an English name; the Italian one is still accepted.
case "$CONTESTO" in
    conversazioni) CONTESTO=conversations ;;
    notifiche)     CONTESTO=notifications ;;
    entrambi)      CONTESTO=both ;;
esac

if [[ ! "$MODALITA" =~ ^(legacy|remote)$ ]] || [[ ! "$CONTESTO" =~ ^(conversations|notifications|both)$ ]]; then
    echo "Usage: sudo bash $0 legacy|remote [conversations|notifications|both]" >&2
    echo "" >&2
    echo "  legacy   the context is served in-process by the monolith" >&2
    echo "  remote   it is served by its own service. This is the production configuration" >&2
    echo "" >&2
    echo "  conversations   acts on conversationservice_mode" >&2
    echo "  notifications   acts on notificationservice_mode" >&2
    echo "  both            on both (default)" >&2
    exit 1
fi

if [[ "$(id -u)" != "0" ]]; then
    echo "Administrator privileges are needed: the Moodle configuration is not writable by others." >&2
    echo "  sudo bash $0 $MODALITA $CONTESTO" >&2
    exit 1
fi

IMPOSTAZIONI=()
case "$CONTESTO" in
    conversations) IMPOSTAZIONI=(conversationservice_mode) ;;
    notifications) IMPOSTAZIONI=(notificationservice_mode) ;;
    both)          IMPOSTAZIONI=(conversationservice_mode notificationservice_mode) ;;
esac

# Read a mode as the site reads it: config.php and the files it includes.
modalita_attiva() {
    sudo -u "$WEB_USER" php -r \
        'define("CLI_SCRIPT", true); define("ABORT_AFTER_CONFIG", true); require($argv[1]);
         echo $CFG->{$argv[2]} ?? "legacy";' \
        "$MOODLE_DOCROOT/config.php" "$1" 2>/dev/null
}

# The override file, when it exists and is included, wins over config.php.
bersaglio_di() {
    local impostazione="$1"
    if [[ -f "$SOVRASCRITTURE" ]] &&
       grep -q "$(basename "$SOVRASCRITTURE")" "$MOODLE_DOCROOT/config.php" 2>/dev/null &&
       grep -q "$impostazione" "$SOVRASCRITTURE"; then
        printf '%s' "$SOVRASCRITTURE"
    elif grep -q "$impostazione" "$MOODLE_DOCROOT/config.php" 2>/dev/null; then
        printf '%s' "$MOODLE_DOCROOT/config.php"
    fi
}

CAMBIATO=false
ERRORI=0

for IMPOSTAZIONE in "${IMPOSTAZIONI[@]}"; do
    PRIMA="$(modalita_attiva "$IMPOSTAZIONE")"
    echo "==> $IMPOSTAZIONE: ${PRIMA:-not readable}"

    if [[ "$PRIMA" == "$MODALITA" ]]; then
        echo "    already in $MODALITA, nothing to do."
        continue
    fi

    BERSAGLIO="$(bersaglio_di "$IMPOSTAZIONE")"
    if [[ -z "$BERSAGLIO" ]]; then
        # Not an error when the requested context is 'both': an absent setting means that phase
        # is not on this site.
        if [[ "$CONTESTO" == "both" ]]; then
            echo "    not declared on this site: skipping it."
            continue
        fi
        echo "ERROR: setting $IMPOSTAZIONE was not found." >&2
        echo "Looked for in $SOVRASCRITTURE and in $MOODLE_DOCROOT/config.php." >&2
        ERRORI=$(( ERRORI + 1 ))
        continue
    fi

    echo "    writing to $BERSAGLIO"
    cp -a "$BERSAGLIO" "$BERSAGLIO.before-verification"
    sed -i "s/${IMPOSTAZIONE}\( *\)=\( *\)'[a-z]*'/${IMPOSTAZIONE}\1=\2'$MODALITA'/" "$BERSAGLIO"
    CAMBIATO=true
done

# Moodle caches are purged as the web server user, once.
if [[ "$CAMBIATO" == true ]]; then
    sudo -u "$WEB_USER" php "$MOODLE_DOCROOT/admin/cli/purge_caches.php" >/dev/null
fi

# The check comes after the purge.
for IMPOSTAZIONE in "${IMPOSTAZIONI[@]}"; do
    BERSAGLIO="$(bersaglio_di "$IMPOSTAZIONE")"
    [[ -z "$BERSAGLIO" ]] && continue

    DOPO="$(modalita_attiva "$IMPOSTAZIONE")"
    if [[ "$DOPO" != "$MODALITA" ]]; then
        echo "" >&2
        echo "ERROR: $IMPOSTAZIONE still reads '$DOPO'." >&2
        echo "" >&2
        echo "The word was changed in $BERSAGLIO, but something overwrites it later. Look for the" >&2
        echo "subsequent assignments and their order:" >&2
        echo "" >&2
        echo "  grep -n '$IMPOSTAZIONE\\|require' $MOODLE_DOCROOT/config.php" >&2
        echo "" >&2
        echo "The previous copy is in $BERSAGLIO.before-verification." >&2
        ERRORI=$(( ERRORI + 1 ))
        continue
    fi
    echo "==> Active: $IMPOSTAZIONE = $DOPO"
done

(( ERRORI > 0 )) && exit 1

if [[ "$MODALITA" == "legacy" ]]; then
    echo ""
    echo "Remember to go back to remote when done: it is the production configuration, the one in which"
    echo "the services are the authoritative store of their contexts."
    echo "  sudo bash $0 remote $CONTESTO"
fi

exit 0
