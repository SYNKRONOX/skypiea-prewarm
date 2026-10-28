# Data

Raw and processed data are **never committed**. `data/raw/` and `data/processed/` are
git-ignored apart from their `.gitkeep` files. Each dataset keeps its own licence.

## Datasets

| Dataset | Use | Licence | Status |
|---|---|---|---|
| TraceLab | Real agent tool-call traces with timings; E2 | CC BY 4.0 | Downloaded (v0.0.2) |
| GitHub Copilot traces | Real coding-agent tool sequences | CC BY | A few shards only |
| tau-bench | Customer-service agent episodes; E1 | See upstream repo | To download |
| Toolathlon | Long multi-tool tasks; E1 | Benchmark-only, gated | Gated. Ask the instructor before using it for training |
| BFCL | Parallel tool calls; E2 | See upstream repo | To download (parallel split) |
| Own cold-start measurements | Real pod cold-start times on kind and Azure; E0 | Ours (MIT) | To measure |

## Pipeline

1. **Download** each dataset into `data/raw/<dataset>/`.
2. **Read** the native format (JSON, JSONL or Parquet).
3. **Convert** to the common schema in `traces/schema.py` (`Session` → `Batch` → `Call`).
4. **Map tools** to 12 to 15 shared tool classes so that datasets are comparable.
5. **Split** 70/30 train/test **by group** (task or user id) with
   `predictors.evaluate.group_split`, so that no task leaks across the split.
6. **Save** as Parquet under `data/processed/`.
