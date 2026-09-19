# The burst (scenario 4.1)

`burst.py` writes N notifications into the NotificationService, one per recipient, as a forum post
to N subscribers would; the DeliveryGateway then works off the deliveries. Measured: the duration of
the burst and the latency of each write (from the script), the time the gateway takes to absorb the
backlog and whether every delivery arrives (from its table). Run before the final `verify.sh
--realign`, which deletes the notifications written after the switch.

1. On VM2, check that the 2000 recipients are deliverable (an address, not suspended), that the site
   declares the provider and wants the email, and that the mock answers:

   ```bash
   sudo mysql -e "SELECT COUNT(*) AS deliverable FROM delivery_gateway.recipient_view r WHERE r.user_id IN (SELECT user_id FROM (SELECT user_id FROM notification_service.user_view WHERE deleted=0 AND email_stop=0 AND user_id>2 ORDER BY user_id LIMIT 2000) s) AND r.email <> '' AND r.deleted=0 AND r.suspended=0 AND r.email_stop=0 AND r.auth_method <> 'nologin';"
   sudo mysql -e "SELECT name, value FROM moodle_db.mdl_config_plugins WHERE plugin='message' AND name IN ('email_provider_mod_forum_posts_locked','message_provider_mod_forum_posts_enabled');"
   sudo mysql -e "SELECT name, enabled FROM delivery_gateway.processor;"
   curl -s http://192.168.72.74:8081/health
   ```

   The first must give 2000; both rows of the second must exist and the second must contain `email`.

2. On VM1, run the script as a Job (mounted from a ConfigMap, the image is the service's):

   ```bash
   cd /home/tesista/repo-tesi/deploy/experiments/phase5 && kubectl -n messaging create configmap burst-script --from-file=burst.py --dry-run=client -o yaml | kubectl apply -f - && kubectl -n messaging delete job notification-burst --ignore-not-found && kubectl apply -f burst-job.yaml
   kubectl -n messaging logs -f job/notification-burst
   ```

   Keep the last line: `BURST marker=... recipients=2000 sent=2000 failed=0 start=... end=...
   duration_s=... p50_ms=... p95_ms=... max_ms=...`. Meanwhile `kubectl -n messaging get hpa
   delivery-gateway` shows the second replica.

3. On VM2, with START and END from that line:

   ```bash
   sudo mysql -e "SELECT status, COUNT(*) AS n, FROM_UNIXTIME(MIN(created_at)) AS first, FROM_UNIXTIME(MAX(updated_at)) AS last, MAX(updated_at) - END AS absorbed_s FROM delivery_gateway.delivery WHERE created_at >= START - 5 GROUP BY status; SELECT COUNT(*) AS dead_letters FROM delivery_gateway.dead_letter WHERE created_at >= START - 5;"
   ```

   Expected one row `delivered` with `n = 2000`, no dead letter; `absorbed_s` is the number of the
   scenario. A `pending` row means the gateway has not finished.

4. Cleanup, with MARKER from the BURST line: on VM2 delete the deliveries, dead letters and events
   with `created_at >= START - 5` from `delivery_gateway`, and the notifications with subject
   `'Burst MARKER %'` (and their `popup_notifications`) from `notification_service`; on VM1 delete
   the Job and the ConfigMap.

Result of 11 September 2026: 2000 notifications in 168 s, p95 2.5 s per write, backlog absorbed
146 s after the end, 2000 delivered, no dead letter, gateway from one to two replicas.
