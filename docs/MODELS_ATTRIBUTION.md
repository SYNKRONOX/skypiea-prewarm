# Skypiea predictor bundles: attribution and licences

This archive holds three predictor bundles built by the Skypiea project (MIT-licensed code,
https://github.com/SYNKRONOX/skypiea-prewarm). They contain aggregated statistics and models
DERIVED from public agent-trace datasets: transition counts, timing quantiles, a LightGBM text
model, a calibrator, the warm policy, a manifest and 20 validation request histories (tool
class names and times only). They contain no prompts, arguments, tool results or other text
from the datasets.

| Bundle | Derived from | Licence of the source |
|---|---|---|
| `lgbm-coding-af00023` | TraceLab v0.0.2 traces (uw-syfi/TraceLab) | CC BY 4.0 (dataset), Apache-2.0 (code) |
| `lgbm-support-aabdfd2` | tau2-bench traces (sierra-research/tau2-bench) | MIT |
| `lgbm-prd-a7642d0` | SYNTHETIC traces from the PRD scenario graphs; think and service times sampled from TraceLab and tau2-bench TRAIN splits | as above |

The coding profile's warm policy was tuned on TraceLab and on the Azure Public Dataset GitHub
Copilot coding-agent traces (CC BY); the coding model itself was trained on TraceLab only.

Attribution: TraceLab (University of Washington SyFI lab), tau2-bench (Sierra Research), Azure
Public Dataset (Microsoft). Sources and dataset licences: `data/README.md` in the repository.
The bundles are provided as-is for reproducing the Skypiea demo and results.
