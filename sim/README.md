# sim

Owner: G. Parthav

A discrete-event simulator of the warm pool. It runs the same decision code
(`predictors.decide.Decider`, a `predictors.base.ContextPolicy`) as the predictor service over
the test-split sessions with timing, so we can sweep thresholds, keep-alive times and baselines
cheaply before running them on the cluster. It reports cold-start rate and idle pod-seconds
(the E3 Pareto plot), cost per 1,000 calls, hideable fractions and the FR-2/FR-3/NFR-3 proxies.

    nice -n 10 python -m sim.offline --cold-start PATH   # results/sim_offline.csv.gz, sim_hideable.csv
    python -m sim.figures                                # sim_pareto.png, tau_curve.png, sim_report.md

Cold starts are read from the file at PATH at run time (default `cold_start_path` in
`configs/model/sim.yaml`); to use the AWS values, pass the AWS file. Definitions: the
docstring of `sim/offline.py`.

Status: Phase 6 done with the E0 kind file (lower bound).
