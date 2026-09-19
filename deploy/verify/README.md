# verify.sh

One command that checks every success criterion of the five phases, plus the regression suites of
the monolith, and prints a table with the outcome of each. Functional checks run on the real system;
the two load campaigns are replayed from `campaigns/` with the `analyze.py` of each phase. A check
that cannot run is reported as `NOT RUNNABLE`, which is not `NOT PASSED`.

On VM1:

```bash
cd ~/repo-tesi && git pull && sudo bash deploy/verify/sync-tree.sh
bash deploy/verify/verify.sh --realign --switch
```

About forty minutes. `--switch` lets the script change the routing mode where a check needs it
(PHPUnit needs `legacy`, the full-path smoke test needs `remote`) and restores it at the end;
`--realign` first brings the service databases back to a copy of the monolith's, deleting the rows
written after the cutover. `--prepare` creates the virtual environments of the service suites on the
first run. Exit code 0 when every criterion passed, 1 when one did not, 2 when some could not run.

Without the laboratory (git, bash, Python 3 with `venv`):

```bash
bash deploy/verify/verify.sh --archive-only --prepare
```

runs the three service suites and replays the two campaigns; the rest comes out `NOT RUNNABLE`.

Criteria, by phase: 1 — indistinguishable configurations and unchanged error rate (archive);
2 — full chain of the service, integrity of the migrated data, service suite, no error and no
bottleneck under load (archive); 3 — service suite, data boundary, consistency between the two
implementations (756 probes), full path in operation; 4 — full delivery chain, no delivery lost,
continuity with the service down, data boundary, channel resolution; 5 — full notification chain,
two extracted contexts talking, integrity, consistency (104 probes), data boundary, a rerouted
monolith component, notification suite; end — the Moodle suites of the touched components.

Other options: `--phase N[,N...]|end`, `--skip-writes` (read probes only in the provider
comparison). A `verify.env` file next to the script overrides the laboratory defaults.

Tools in this folder: `switch-mode.sh legacy|remote [conversations|notifications|both]`,
`sync-tree.sh [--check]` (copy the changed monolith files onto the served tree), `realign.sh`
(the reconciliation in repair mode, monolith to service). `campaigns/` holds the raw data of the
two archived campaigns.
