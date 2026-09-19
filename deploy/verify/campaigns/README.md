# Raw data of the campaigns

`phase1.tar.gz` and `phase2.tar.gz`: the JMeter results files and the counter readings of the
definitive campaign of each phase, the one whose numbers are reported in the thesis. `verify.sh`
unpacks them and feeds them to the same `analyze.py` that judged them the first time, so anyone can
re-check the numbers without the laboratory:

```bash
bash deploy/verify/verify.sh --archive-only
```

Each archive holds the `results/<campaign>` folder of the experiment (phase 1: `20260819-174954`
with `benchmark.json`; phase 2: `20260822-130027`) without the JMeter logs. The phase 1 archive also
holds `calibrazione-20260819-170851`, the capacity curve of the monolith at 10, 25, 50, 100 and 200
threads from which the working point was chosen; `make-figures.py --calibration` of the experiment
redraws it. The `results/` folders of the experiments are not versioned: they hold every attempt, including
the interrupted campaigns. The other phases have no archive: their criteria are functional, and
`verify.sh` establishes them on the spot.
