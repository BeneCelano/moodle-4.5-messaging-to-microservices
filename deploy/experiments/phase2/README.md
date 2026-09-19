# Phase 2

Monolith against ConversationService on the same operation: `core_message_send_instant_messages`
through the REST web service on one side, `POST /api/v1/messages/instant` on the other, both
resolving or creating the individual conversation and writing the message. Delivery is switched off
on the monolith, so both sides do persistence only.

## Files

- `jmeter/monolith.jmx`, `jmeter/service.jmx` — the two plans; addresses and tokens arrive as
  properties;
- `config.env.example` — parameters (copy to `config.env`, not versioned: it holds two tokens);
- `reset-db.sh` — restores both schemas before every run, on VM2;
- `run-campaign.sh` — the campaign: N repetitions, the two targets alternated, warm-up before every
  run;
- `analyze.py` — metrics per target, comparison with its uncertainty, verdict;
- `deployment-campaign.yaml` — patch that gives the service the shape the campaign measured (one
  replica on one exclusive core); `run-campaign.sh` refuses to start otherwise;
- `cpu-usage.py` — CPU per replica from `kubectl top pod` readings, for the scalability test.

## Procedure

Before the campaign: `SENDER_USER_ID` in `config.env` is the user of the web service token; VM1 serves
the untouched tree (`switch-config.sh C0`); every delivery processor is disabled on the monolith
(`UPDATE mdl_message_processors SET enabled = 0;` then `purge_caches.php`); the two restore points
exist on VM2:

```bash
sudo mysqldump --single-transaction moodle_db | gzip > /var/backups/moodle-phase2-baseline.sql.gz
sudo mysqldump --single-transaction conversation_service | gzip > /var/backups/conversation-phase2-baseline.sql.gz
```

The service's must have an empty outbox. The copy is checked with the reconciliation
(`scripts/reconcile.py --schema moodle_db --check`, with the seed user). The plans go to VM3,
`reset-db.sh` and `config.env` to VM2.

```bash
bash run-campaign.sh --runs 5
python3 analyze.py results/<timestamp> --apdex-t 500 --apdex-f 1500
```

`analyze.py` exits 0 when the criteria hold (error rate within 1% on both sides, throughput of the
service not below the monolith's by more than two combined standard deviations), 1 otherwise, 2
without data. The Apdex is reported, not applied.

For the scalability test of chapter 5 the same campaign is run with the service in its production
shape (`--different-environment`), once with one replica and once with two, while `kubectl top pod`
is sampled every five seconds on VM1 (the loop is in the docstring of `cpu-usage.py`):

```bash
python3 cpu-usage.py results/<timestamp> --pod-csv /tmp/pod.csv
```

## Result

Campaign `20260822-130027` (22 August 2026), five runs per target, sender admin (id 2) as in
chapter 3 and phase 1: monolith Apdex 0.6004 ± 0.0192 and 41.6 ± 2.1 req/s, service 0.8362 ± 0.0986
and 55.6 ± 6.4 req/s, no error on either side. Archived in `deploy/verify/campaigns/phase2.tar.gz`.
