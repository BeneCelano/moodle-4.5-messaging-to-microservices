# The demo of the defence

Half of the screen is the Moodle interface, the other half the Grafana dashboard
[messaging-flow.json](messaging-flow.json), whose counters move in the order in which a contact request
crosses the contexts: ConversationService, broker, NotificationService, DeliveryGateway.

1. **One replica per service** during the recording. Every replica keeps its own counters, and the
   scrape target is the node port, which sends each scrape to one of the replicas: two
   consecutive samples come from two different processes, a value lower than the previous one is read
   by Prometheus as a restart of the counter, and the panels jump. With one replica the series belongs
   to one process.

   ```bash
   kubectl -n messaging scale deploy/conversation-service --replicas=1 && kubectl -n messaging scale deploy/notification-service --replicas=1
   ```

2. **Scrape targets.** The three targets of [prometheus-demo.yml](prometheus-demo.yml) must be in
   `scrape_configs` of Prometheus on VM5 (`/home/tesista/monitoring/prometheus.yml`, then
   `docker restart prometheus`): every dashboard query filters on the `service`
   label, which only they declare.

3. **Dashboard.** In Grafana: Dashboards → New → Import → Upload JSON file,
   [messaging-flow.json](messaging-flow.json), with Prometheus as data source. The dashboard must be
   re-imported whenever the JSON changes: the metric label `status` and the scrape label `service`
   are the current names.

4. **Both contexts in `remote`**, or the monolith serves the request and nothing moves. The two
   contexts are `conversations` (`$CFG->conversationservice_mode`) and `notifications`
   (`$CFG->notificationservice_mode`); `both` switches them together:

   ```bash
   sudo bash /home/tesista/repo-tesi/deploy/verify/switch-mode.sh remote both
   ```

5. **The clock of the computer that shows the dashboard.** Grafana asks Prometheus for the data up
   to "now" as read on the browser's clock: a laptop a couple of minutes behind the VMs shows every
   change with the same delay, on every panel. Synchronize the clock before recording, or set the
   time range to `now-2m` … `now+3m`.

6. **Rehearsal.** Make a contact request and watch the dashboard: the four blocks must move in
   sequence. If only the first moves, a scrape target is missing.

After the recording, back to two replicas:

```bash
kubectl -n messaging scale deploy/conversation-service --replicas=2 && kubectl -n messaging scale deploy/notification-service --replicas=2
```
