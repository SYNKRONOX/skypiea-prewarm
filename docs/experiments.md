# Experiments

| ID | Question | Data | Output |
|---|---|---|---|
| E0 | How long are real cold starts? | Own measurements, cached vs uncached image | Cold-start distribution |
| E1 | How accurate is next-tool prediction? | tau-bench, Toolathlon | Top-1 / top-3 at k = 1, 3, 5 |
| E2 | Can we warm a whole parallel batch? | BFCL, TraceLab | Whole-batch coverage |
| E3 | What does warming cost? | Simulator, 500 sessions | Pareto plot: cold rate vs idle pod-seconds |
| E4 | What does the user see? | Cluster replay | p50 / p99 latency CDFs |
| E5 | Does it hold under load? | 500-session spike and soak | Latency, cold rate, pod count over time |

## Baselines

1. **Scale-to-zero:** no prewarming. Every first call is cold.
2. **Always-warm:** one pod per tool, always on. Lower bound on latency, upper bound on cost.
3. **Fixed keep-alive:** keep a pod for 30 s to 10 min after its last use.
4. **Hybrid histogram:** Shahrad et al., *Serverless in the Wild*, USENIX ATC 2020.
5. **SpecBox-style Markov:** top-1 first-order Markov prewarming.
6. **Oracle:** warms exactly the next tools, just in time.

## Running

Each experiment has a YAML config in `experiments/`:

```bash
python -m experiments.run --config experiments/e1_predictor_accuracy.yaml
```

Results go to `results/<experiment>/`.
