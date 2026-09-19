#!/usr/bin/env bash
#
# Copy the monolith files changed by this work onto the served tree, and purge the caches.
#
#   sudo bash deploy/verify/sync-tree.sh
#   sudo bash deploy/verify/sync-tree.sh --check    say what differs, without copying
#
# The list of what to copy lives here, and verify.sh reads it from this file.

set -uo pipefail

BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MOODLE_DOCROOT="${MOODLE_DOCROOT:-/var/www/html/moodle}"
WEB_USER="${WEB_USER:-www-data}"

# Each entry is "source|destination", relative to the repository and to the served tree. The trailing
# slash on a folder matters to rsync. No --delete: the served tree holds files the repository does not.
PERCORSI=(
    "moodle_4.5/message/|message/"
    "moodle_4.5/lib/messagelib.php|lib/messagelib.php"
    "moodle_4.5/lib/classes/task/messaging_cleanup_task.php|lib/classes/task/messaging_cleanup_task.php"
    "moodle_4.5/mod/assign/classes/notification_helper.php|mod/assign/classes/notification_helper.php"
    "moodle_4.5/mod/quiz/classes/notification_helper.php|mod/quiz/classes/notification_helper.php"
)

SOLO_CONTROLLO=false
[[ "${1:-}" == "--check" || "${1:-}" == "--controlla" ]] && SOLO_CONTROLLO=true

if [[ "$SOLO_CONTROLLO" != true && "$(id -u)" != "0" ]]; then
    echo "Administrator privileges are needed: the served tree belongs to $WEB_USER." >&2
    echo "  sudo bash $0" >&2
    exit 1
fi

# The comparison is on content, not on date.
DIFFERENZE=0
for VOCE in "${PERCORSI[@]}"; do
    ORIGINE="$BASE/${VOCE%%|*}"
    DESTINAZIONE="$MOODLE_DOCROOT/${VOCE##*|}"

    if [[ ! -e "${ORIGINE%/}" ]]; then
        echo "ERROR: $ORIGINE does not exist in the repository." >&2
        exit 1
    fi

    # The rsync exit code is checked: an unreadable destination would produce an empty list.
    ELENCO="$(rsync -rn --checksum --out-format='%n' "$ORIGINE" "$DESTINAZIONE" 2>&1)"
    if (( $? != 0 )); then
        echo "CANNOT COMPARE ${VOCE##*|}:" >&2
        printf '%s\n' "$ELENCO" | tail -3 | sed 's/^/  /' >&2
        exit 2
    fi
    ELENCO="$(printf '%s\n' "$ELENCO" | grep -v '/$' | grep -v '^$')"

    if [[ -n "$ELENCO" ]]; then
        QUANTI="$(printf '%s\n' "$ELENCO" | wc -l)"
        DIFFERENZE=$(( DIFFERENZE + QUANTI ))
        if [[ "$SOLO_CONTROLLO" == true ]]; then
            printf '%s\n' "$ELENCO" | sed "s|^|  ${VOCE##*|} |"
        else
            echo "==> ${VOCE##*|}: $QUANTI file(s) to update"
            rsync -a --chown="$WEB_USER:$WEB_USER" "$ORIGINE" "$DESTINAZIONE"
        fi
    fi
done

if [[ "$SOLO_CONTROLLO" == true ]]; then
    if (( DIFFERENZE == 0 )); then
        echo "The served tree matches the repository."
        exit 0
    fi
    echo ""
    echo "$DIFFERENZE file(s) differ. Synchronise with: sudo bash $0"
    exit 1
fi

if (( DIFFERENZE == 0 )); then
    echo "==> Already aligned, nothing to copy."
    exit 0
fi

# Moodle caches hold the class map: purged as the web server user.
echo "==> Purging the caches"
sudo -u "$WEB_USER" php "$MOODLE_DOCROOT/admin/cli/purge_caches.php" >/dev/null
echo "==> Done."
