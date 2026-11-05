Per-session cap, TraceLab native, top-1 at k=1 (fixed split with 95% group CI; GKF mean ± sd over 5 folds)

| Model | cap | fixed | GKF | fixed vs cap 3 | GKF vs cap 3 |
|---|---|---|---|---|---|
| graph_ngram | 3 | 0.455 [0.423,0.512] | 0.473 ± 0.041 | +0.0 pt | +0.0 pt |
| graph_ngram | 5 | 0.456 [0.423,0.512] | 0.473 ± 0.040 | +0.0 pt | +0.0 pt |
| graph_ngram | none | 0.465 [0.433,0.525] | 0.473 ± 0.036 | +0.9 pt | +0.0 pt |
| lgbm_ranker | 3 | 0.529 [0.484,0.569] | 0.535 ± 0.052 | +0.0 pt | +0.0 pt |
| lgbm_ranker | 5 | 0.530 [0.485,0.569] | 0.535 ± 0.052 | +0.1 pt | -0.0 pt |
| lgbm_ranker | none | 0.530 [0.485,0.569] | 0.534 ± 0.053 | +0.1 pt | -0.0 pt |
