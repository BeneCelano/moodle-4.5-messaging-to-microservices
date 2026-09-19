#!/usr/bin/env bash
#
# Bring both schemas back to their restore points, before every run. To run on VM2, or via ssh from
# run-campaign.sh.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ ! -f "$SCRIPT_DIR/config.env" ]]; then
    echo "config.env missing: copy config.env.example and adapt it." >&2
    exit 1
fi

# shellcheck disable=SC1091
source "$SCRIPT_DIR/config.env"

for snapshot in "$DB_SNAPSHOT" "$CS_DB_SNAPSHOT"; do
    if [[ ! -f "$snapshot" ]]; then
        echo "Restore point missing: $snapshot" >&2
        echo "Produce them once before the campaign, see README." >&2
        exit 1
    fi
done

# The instance is not restarted (buffer pool kept).

echo "==> Restoring $DB_NAME from $DB_SNAPSHOT"
gunzip -c "$DB_SNAPSHOT" | $MYSQL_CLIENT "$DB_NAME"

echo "==> Restoring $CS_DB_NAME from $CS_DB_SNAPSHOT"
# The service stays up during the restore. The dump holds the user_view view with its DEFINER:
# the restore runs as an administrative user.
gunzip -c "$CS_DB_SNAPSHOT" | $MYSQL_CLIENT "$CS_DB_NAME"

MONO_MSG=$($MYSQL_CLIENT --skip-column-names --batch \
    --execute="SELECT COUNT(*) FROM ${DB_PREFIX}messages" "$DB_NAME")

SVC_MSG=$($MYSQL_CLIENT --skip-column-names --batch \
    --execute="SELECT COUNT(*) FROM messages" "$CS_DB_NAME")

# Only the events still to deliver count.
SVC_OUTBOX=$($MYSQL_CLIENT --skip-column-names --batch \
    --execute="SELECT COUNT(*) FROM outbox WHERE published_at IS NULL" "$CS_DB_NAME")

echo "==> Restored: monolith $MONO_MSG messages, service $SVC_MSG messages, $SVC_OUTBOX events to deliver"

# The counts are printed at every run. The InnoDB buffer pool must hold both databases in full.
POOL_MB=$($MYSQL_CLIENT --skip-column-names --batch     --execute="SELECT ROUND(@@innodb_buffer_pool_size/1024/1024)")

DATI_MB=$($MYSQL_CLIENT --skip-column-names --batch --execute="
    SELECT ROUND(COALESCE(SUM(data_length + index_length), 0) / 1024 / 1024)
      FROM information_schema.tables
     WHERE table_schema IN ('$DB_NAME', '$CS_DB_NAME')")

echo "==> InnoDB buffer pool: ${POOL_MB} MB for ${DATI_MB} MB of data"

if (( POOL_MB < DATI_MB )); then
    echo ""
    echo "WARNING: the InnoDB buffer pool does not hold the two databases." >&2
    echo "The order of the restores becomes a variable of the comparison, and the results cannot" >&2
    echo "be defended. Raise innodb_buffer_pool_size above ${DATI_MB} MB and repeat." >&2
fi

if (( SVC_OUTBOX != 0 )); then
    echo ""
    echo "WARNING: $SVC_OUTBOX events are still to deliver after the restore." >&2
    echo "The restore point of the service was taken while it held pending events." >&2
    echo "Take it again with the service stopped, or after the relay has emptied the table." >&2
fi
