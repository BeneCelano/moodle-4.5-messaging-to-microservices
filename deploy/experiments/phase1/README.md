# Phase 1

Does the Branch by Abstraction layer cost anything measurable? Two configurations of the same site,
C0 (`moodle-4.5-pristine`, untouched) and C1 (`moodle-4.5-branched`, layer in `legacy` mode), measured
with the same JMeter plan (`test_message.jmx`: `core_message_send_instant_messages` through the REST
web service, random recipient).

Result of 19 August 2026, five runs per configuration at 50 threads: p95 259.0 ± 66.0 ms (C0) against
246.6 ± 34.3 ms (C1), difference −12.4 ms on a dispersion of ±74.4, no error on either side. The
in-process cost of the layer, measured by `abstraction-benchmark.php`, is 275.5 ns per crossing.
The raw data of the campaign and of the calibration are in `deploy/verify/campaigns/phase1.tar.gz`;
`verify.sh` replays the campaign.

## How to run it

Setup, once: the two trees on VM1 (`sudo mv /var/www/html/moodle /var/www/moodle-4.5-branched &&
sudo cp -a /var/www/moodle-4.5-branched /var/www/moodle-4.5-pristine`, document root as a symbolic
link), the line `@include('/etc/moodle/phase1-overrides.php');` in both `config.php` before
`lib/setup.php`, a restore point of `moodle_db` on VM2
(`sudo mysqldump --single-transaction --routines moodle_db | gzip > /var/backups/moodle-phase1-baseline.sql.gz`),
`test_message.jmx` on VM3, passwordless sudo for `switch-config.sh` on VM1 and `mysql` on VM2, an ssh
key on the three machines.

```bash
cp config.env.example config.env        # addresses, paths, token
bash calibrate.sh 10 25 50 100 200      # capacity curve of C0, to pick the working point
sudo -u www-data php abstraction-benchmark.php /var/www/moodle-4.5-branched   # on VM1
bash run-campaign.sh --runs 5
python3 analyze.py results/<campaign> --apdex-t 500
python3 make-figures.py --calibration results/<calibration> --campaign results/<campaign> --out figures
```

Each run switches the configuration, warms up with few threads, restores the database, measures,
and saves the results with the configuration state declared by VM1. The Apdex is reported and not
applied. Phase 2 uses a different load shape, so its figures are not comparable with these.

Files: `test_message.jmx`, `config.env.example`, `switch-config.sh` (VM1, as root), `reset-db.sh`
(VM2), `calibrate.sh`, `run-campaign.sh`, `abstraction-benchmark.php`, `analyze.py`,
`make-figures.py`.
