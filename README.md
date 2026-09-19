# Moodle `message`: from monolith to microservices

Code of the master's thesis *From Monolith to Microservices: Incremental Migration of an Open-Source
E-Learning System*. The `message` subsystem of Moodle 4.5 is migrated in five phases to three
services — ConversationService, DeliveryGateway, NotificationService — with success criteria
declared before each phase and checked by a single script at the end.

```
moodle_4.5/   the Moodle 4.5.12+ tree; the migration touches 29 files (see below)
services/     conversation/, delivery/, notification/ — one FastAPI service each
deploy/       Kubernetes manifests, experiments, verification script, mock, demo
```

Each folder has its own README with the details.

## What changed in Moodle

The whole tree is kept so that the monolith can be served from it; everything else is upstream.
The migration adds, under `message/`:

- `classes/conversation/` and `classes/notification/` — the abstraction of each context
  (`provider.php`), the monolith implementation (`legacy_provider.php`, code moved from `api.php`),
  the HTTP client of the service (`remote_provider.php`, `transport.php`) and the factory that picks
  one from `$CFG->conversationservice_mode` / `$CFG->notificationservice_mode`;
- `cli/` — `compare_providers.php` and `compare_notification_providers.php` (the same questions to
  both implementations), `smoke_api.php`, `seed_notification_templates.php`,
  `verify_notification_reroute.php`, with the shared `classes/cli/comparison.php`;

and modifies twelve files so that no read or write of the message tables bypasses the abstraction:
`message/classes/api.php`, `message/lib.php`, `message/externallib.php`, the popup processor
(`message/output/popup/`), the privacy provider, `get_unread_notification_count.php`,
`lib/messagelib.php`, `lib/classes/task/messaging_cleanup_task.php` and the `notification_helper.php`
of `mod/assign` and `mod/quiz`. `deploy/verify/sync-tree.sh` lists the five paths and copies them
onto the served tree.

## How it fits together

Branch by Abstraction in the monolith (`legacy` or `remote` per context, one line of `config.php`);
one schema and three database users per service on the same MySQL instance, with the monolith data
read through views (`SQL SECURITY DEFINER`) and the conversation and notification tables copied once
by a seed Job and checked by a reconcile Job; transactional outbox and a relay towards RabbitMQ in
every service, idempotent consumers; a two-stage gateway (accept the event, then one assignment per
recipient and channel) with retries and a dead letter. Events on the topic exchange
`moodle.messaging`: `conversation.message.sent` and `notification.created` are consumed by the
gateway, `conversation.contactrequest.created` by the NotificationService.

## The laboratory

Six virtual machines, ssh on port 2222: VM1 (192.168.72.71) Moodle on core 0 and the k3s server node
on core 1, also the image builder; VM2 (.72) MySQL with `moodle_db` and the three service schemas;
VM3 (.73) JMeter; VM4 (.74) the delivery mock; VM5 (.75) Prometheus and Grafana; VM6 (.76) the k3s
agent node. The services live in the `messaging` namespace with RabbitMQ (Bitnami chart) and are
exposed on node ports 30081, 30082, 30083. Versions: k3s 1.36, Podman 4.9, RabbitMQ 4.1, Python 3.12,
FastAPI 0.141.

Installing the application on that infrastructure is described step by step in `deploy/README.md`:
databases and views on VM2, images built with podman and imported on both nodes, then per service
ConfigMap, Secret, migration Job, Deployment, seed and reconcile Jobs, and finally the monolith
(`sync-tree.sh` for the served tree, `config.php` for the two routing modes). With both contexts in
`remote` the Moodle interface works as before, served by the services;
`deploy/verify/switch-mode.sh` moves each context between `legacy` and `remote`.

## Reproducing the results

Unit tests, without any environment (SQLite in memory):

```bash
cd services/conversation && python -m venv .venv && ./.venv/Scripts/pip install -e ".[dev]" && ./.venv/Scripts/python.exe -m pytest -q
```

(`.venv/bin/` on Linux and macOS; the same for `notification` and `delivery`.) The thesis cites the
50 tests of the conversation suite (35 of them for phase 2), the 73 of the notification suite and
the 44 of the delivery suite.

All the criteria of the five phases, from VM1:

```bash
bash deploy/verify/verify.sh --realign --switch
```

It runs the functional checks on the real system (suites, the comparisons between the two
implementations with 756 and 104 probes, data integrity, data boundary, full paths, retries and
continuity of the gateway, the rerouted `mod_assign` reminder, the 909 tests of the Moodle
subsystem) and replays the phase 1 and phase 2 campaigns from the archived raw data, then prints
the table of the 22 criteria. `--archive-only --prepare` runs the suites and the replays on any
computer. Details in `deploy/verify/README.md`.

The campaigns themselves, the scalability test and the burst are in `deploy/experiments/phase1`,
`phase2` and `phase5`, each with the commands, the files and the numbers obtained. The static
analysis of chapter 5 uses lizard on `message/` of both trees (tests and JavaScript excluded) and on
the `app` folder of the three services:

```bash
lizard -l php -x "*/tests/*" -x "*.js" /var/www/moodle-4.5-branched/message
lizard services/conversation/app services/notification/app services/delivery/app
```

The demo shown at the defence is described in `deploy/demo/README.md`.
