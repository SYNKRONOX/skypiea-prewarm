# sim

Owner: G. Parthav

A discrete-event simulator of the warm pool. It runs the same `predictors.base.Policy` code
as the live controller over 500 sessions, so we can sweep thresholds, keep-alive times and
baselines cheaply before running them on the cluster. It reports cold-start rate and idle
pod-seconds, which give the E3 Pareto plot.

Status: not started.
