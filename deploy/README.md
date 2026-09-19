# Deployment

Manifests and procedures for the laboratory (k3s on VM1 and VM6, MySQL on VM2, JMeter on VM3, the
delivery mock on VM4, Prometheus and Grafana on VM5). The manifests in `k8s/` are plain Kubernetes;
only two details are k3s-specific: services exposed on static node ports (no ingress) and images
imported into the containerd of each node.

```
k8s/          namespace quota, then one folder per service: configmap, secret.example, migration Job,
              deployment, service, smoke Job; seed and reconcile Jobs where data is copied; hpa for the gateway
experiments/  phase1, phase2 (also the scalability test), phase5 (the burst)
verify/       verify.sh and its tools
mocks/        the SMTP/Airnotifier mock for VM4
demo/         Grafana dashboard and Prometheus targets of the demo
```

Node ports: 30081 ConversationService, 30082 DeliveryGateway, 30083 NotificationService.

## 1. Databases (VM2)

Each service has a schema and three users: `<service>_svc` (the service, no privilege on
`moodle_db`), `<service>_seed` (seed and reconcile Jobs, reads the monolith), `<service>_view`
(definer of the views on the monolith tables, never connects). Passwords from `openssl rand -hex 24`,
never on disk or screen.

Notification and delivery come with a script with placeholders:

```bash
cd services/notification && read -rsp 'notification_svc: ' P_SVC; echo; read -rsp 'notification_seed: ' P_SEED; echo; read -rsp 'notification_view: ' P_VIEW; echo; sed -e "s/PASSWORD_SVC/$P_SVC/g" -e "s/PASSWORD_SEED/$P_SEED/g" -e "s/PASSWORD_VIEW/$P_VIEW/g" scripts/schema.sql | sudo mysql; unset P_SVC P_SEED P_VIEW
```

```bash
cd services/delivery && read -rsp 'delivery_svc: ' P_SVC; echo; read -rsp 'delivery_view: ' P_VIEW; echo; sed -e "s/PASSWORD_SVC/$P_SVC/g" -e "s/PASSWORD_VIEW/$P_VIEW/g" scripts/schema.sql | sudo mysql; unset P_SVC P_VIEW
```

For the conversation service, with an administrative user:

```sql
CREATE DATABASE IF NOT EXISTS conversation_service DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'conversation_svc'@'%' IDENTIFIED BY '...';
GRANT ALL PRIVILEGES ON conversation_service.* TO 'conversation_svc'@'%';
CREATE USER 'conversation_seed'@'%' IDENTIFIED BY '...';
GRANT SELECT ON moodle_db.* TO 'conversation_seed'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE, ALTER ON conversation_service.* TO 'conversation_seed'@'%';
CREATE USER IF NOT EXISTS 'conversation_view'@'localhost' IDENTIFIED BY '...';
GRANT SELECT ON moodle_db.mdl_user TO 'conversation_view'@'localhost';
CREATE ALGORITHM = MERGE DEFINER = 'conversation_view'@'localhost' SQL SECURITY DEFINER
    VIEW conversation_service.user_view AS
SELECT u.id AS user_id, CONCAT(u.firstname, ' ', u.lastname) AS full_name, NULL AS picture_url,
       u.deleted AS deleted, u.timemodified AS updated_at
  FROM moodle_db.mdl_user u;
```

Check that `mysql -u conversation_svc -p -e "SELECT COUNT(*) FROM moodle_db.mdl_user;"` fails with
error 1142.

## 2. Images (VM1)

Built with podman, tagged in full (`docker.io/library/<service>:<version>`, otherwise containerd
does not find them), imported on both nodes. Bump the tag at every rebuild and align the manifests;
`sudo k3s ctr images ls | grep <service>` shows the tags already imported.

```bash
V=0.2.14 && podman build -t docker.io/library/conversation-service:$V services/conversation
rm -f /tmp/conversation-service-$V.tar && podman save docker.io/library/conversation-service:$V -o /tmp/conversation-service-$V.tar && sudo k3s ctr images import /tmp/conversation-service-$V.tar
scp -P 2222 /tmp/conversation-service-$V.tar tesista@192.168.72.76:/tmp/ && ssh -t -p 2222 tesista@192.168.72.76 "sudo k3s ctr images import /tmp/conversation-service-$V.tar"
```

Same for `notification-service` and `delivery-gateway`.

## 3. Services

Quota first: `kubectl apply -f deploy/k8s/namespace/resourcequota.yaml`. Secrets are filled from the
`secret.example.yaml` files outside the repository. Then, per service, ConfigMap and Secret, the
migration Job, Deployment and Service:

```bash
kubectl apply -f deploy/k8s/conversation-service/configmap.yaml -f /path/outside/the/repo/conversation-secret.yaml
kubectl apply -f deploy/k8s/conversation-service/migration-job.yaml && kubectl -n messaging wait --for=condition=complete job/conversation-service-migrate --timeout=120s
kubectl apply -f deploy/k8s/conversation-service/deployment.yaml -f deploy/k8s/conversation-service/service.yaml
```

ConversationService and NotificationService also need the seed Job (with the seed Secret), rerun
with `--full` right before the switch, and the reconcile Job (`--check`, or `--apply` to repair):

```bash
kubectl apply -f /path/outside/the/repo/conversation-seed-secret.yaml -f deploy/k8s/conversation-service/seed-job.yaml && kubectl -n messaging logs -f job/conversation-service-seed
```

The NotificationService also needs the templates, sent from the monolith once the service answers
(`sudo -u www-data php message/cli/seed_notification_templates.php`), and `notification.created`
among the routing keys of the gateway ConfigMap.

The DeliveryGateway needs `sudo mysql < services/delivery/scripts/seed_processors.sql` on VM2 after
its migrations, `hpa.yaml` with the Deployment, and the mock on VM4 (`mocks/delivery-mock/README.md`).
In Moodle `email` and `airnotifier` stay disabled and `popup` enabled.

## 4. Monolith

Served tree: `cd /home/tesista/repo-tesi && git pull && sudo bash deploy/verify/sync-tree.sh` copies
the changed files onto `/var/www/moodle-4.5-branched` and purges the caches (the two Moodle trees
under `/var/www` are upstream clones: never `git pull` there).

`config.php`, which the sync does not touch:

```php
$CFG->conversationservice_mode = 'remote';        // legacy | remote
$CFG->conversationservice_url = 'http://192.168.72.71:30081';
$CFG->conversationservice_token = '...';
$CFG->conversationservice_timeout = 2;
$CFG->conversationservice_connecttimeout = 1;
$CFG->notificationservice_mode = 'remote';
$CFG->notificationservice_url = 'http://192.168.72.71:30083';
$CFG->notificationservice_token = '...';
$CFG->notificationservice_timeout = 2;
$CFG->notificationservice_connecttimeout = 1;
```

`deploy/verify/switch-mode.sh legacy|remote [conversations|notifications|both]` changes the modes.
There is no automatic fallback: an unreachable service is a visible error. If
`curlsecurityblockedhosts` is set, the service address must be allowed.

## 5. Checks

`curl -s http://192.168.72.71:30081/readyz` (and 30082, 30083); the `smoke-job.yaml` of each
service; `verify/verify.sh` for the criteria of all phases. The subsystem suite of Moodle:

```bash
cd /var/www/moodle-4.5-branched && sudo -u www-data vendor/bin/phpunit --testsuite core_message_testsuite
```

Metrics on `/metrics` of each service; Prometheus targets for VM5 in `demo/prometheus-demo.yml`.
