# Choosing the subsystem

Before the migration, the three subsystems of the User Communication bounded context — `message`,
`communication`, `sms` — were compared with a static analysis (PHPMetrics 2.10.0 on the `message/`,
`communication/` and `sms/` folders of `MOODLE_405_STABLE`) and one load test each, identical in
shape: 500 threads, ramp-up 30 s, 10 iterations, 5,000 requests, sent from VM3 to the monolith on VM1
with an administrator token. `message` came out worst on both counts and was the one migrated.
Chapter 3 of the thesis reports the comparison.

```
jmeter/                the three plans; the token arrives as the property wstoken
results/               the JMeter results files of the runs of 14 August 2026, 5,000 samples each
moodle/                local_smstest and smsgateway_mock, the two plugins the sms plan needs
mock-sms-provider.js   the SMS provider mock, for VM4
phpmetrics/            the PHPMetrics reports of the three folders (open index.html)
```

Each plan calls the REST web service of Moodle. `message.jmx` sends an instant message
(`core_message_send_instant_messages`) to a random user among the ten thousand generated;
`communication.jmx` enrols a random user in course 3 (`enrol_manual_enrol_users`), the
event that makes `communication` queue its Matrix tasks; `sms.jmx` sends an SMS through
`local_smstest_send`.

```bash
jmeter -n -t jmeter/message.jmx -Jwstoken=<token> -l message.jtl
jmeter -g message.jtl -o report-message
```

What the sms plan needs: `moodle/local/smstest` copied to `local/smstest` and `moodle/sms/gateway/mock`
to `sms/gateway/mock` of the served tree, then the upgrade (`sudo -u www-data php
admin/cli/upgrade.php --non-interactive`); the mock gateway enabled under Site administration →
Plugins → SMS, with the endpoint of VM4; the mock started there (`npm install express`, then
`PORT=8080 DELAY_MS=500 node mock-sms-provider.js`); a token of a user with `local/smstest:send`.
What the communication plan needs: the experimental `communication` feature enabled and Matrix
Synapse (Docker on VM4, port 8008, PostgreSQL on VM5) set as the provider of course 3. Its measure
has a second half, the queued tasks processed by hand on VM6 with cron disabled
(`admin/cli/adhoc_task.php`).

Results of 14 August 2026, from the JMeter reports of the files in `results/`:

| | message | communication | sms |
|---|---|---|---|
| throughput (req/s) | 79.62 | 34.48 | 101.70 |
| p90 (ms) | 2,557 | 17,161.8 | 2,374.7 |
| p95 (ms) | 10,553.75 | 38,811.7 | 7,331.9 |
| Apdex (T 500 ms, F 1,500 ms) | 0.164 | 0.054 | 0.389 |
| errors | 0 % | 0 % | 0 % |

A rerun today gives different figures: since these runs Moodle on VM1 has one core, the other is
k3s's.

The reports in `phpmetrics/` come from `phpmetrics --report-html=<dir> <folder>` (PHPMetrics 2.10.0)
on the untouched `message/`, `communication/` and `sms/` of Moodle 4.5.12+. The last two are the
ones in `moodle_4.5/`; `moodle_4.5/message` instead already carries the migration, so the report of
`message` is reproduced on an upstream tree (on VM1, `/var/www/moodle-4.5-pristine/message`).
