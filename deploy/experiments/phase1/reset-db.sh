#!/usr/bin/env bash
#
# Bring the monolith schema back to the restore point, before every run. To run on VM2, or via ssh
# from run-campaign.sh.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ ! -f "$SCRIPT_DIR/config.env" ]]; then
    echo "config.env missing: copy config.env.example and adapt it." >&2
    exit 1
fi

# shellcheck disable=SC1091
source "$SCRIPT_DIR/config.env"

if [[ ! -f "$DB_SNAPSHOT" ]]; then
    echo "Restore point missing: $DB_SNAPSHOT" >&2
    echo "Produce it once before the campaign, see README." >&2
    exit 1
fi

echo "==> Restoring $DB_NAME from $DB_SNAPSHOT"

# The instance is not restarted (buffer pool kept). The dump holds DROP TABLE before every
# CREATE TABLE.
gunzip -c "$DB_SNAPSHOT" | $MYSQL_CLIENT "$DB_NAME"

ROWS=$($MYSQL_CLIENT --skip-column-names --batch \
    --execute="SELECT COUNT(*) FROM ${DB_PREFIX}messages" "$DB_NAME")

USERS=$($MYSQL_CLIENT --skip-column-names --batch \
    --execute="SELECT COUNT(*) FROM ${DB_PREFIX}user WHERE deleted = 0" "$DB_NAME")

echo "==> Restored: ${DB_PREFIX}messages has $ROWS rows, ${DB_PREFIX}user has $USERS users"

# The count is printed at every run and ends up in the campaign log.
