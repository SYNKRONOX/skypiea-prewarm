# Data

Raw data lives in `data/raw/` and is **never committed**. Anyone who clones the repo
rebuilds it with the downloader:

```bash
python data/download.py --list                       # datasets and what you already have
python data/download.py --dataset tracelab --dry-run # show sizes, download nothing
python data/download.py --dataset tracelab           # download, verify, hash
```

The downloader checks every file's size before downloading. It refuses anything over 200 MB
unless you pass `--yes-large`, resumes interrupted downloads, verifies the size and records
the sha256 in [`MANIFEST.json`](MANIFEST.json). Re-running it is safe. It never logs in or
uses tokens: gated datasets print instructions instead.

## Datasets

| Dataset (`--dataset`) | Source | Licence | Size downloaded | Status | Use |
|---|---|---|---|---|---|
| `tracelab` | [uw-syfi/TraceLab v0.0.2](https://github.com/uw-syfi/TraceLab/releases/tag/v0.0.2) | CC BY 4.0 (code Apache-2.0) | 96 MB | Downloaded | Real coding-agent traces with per-call timings; E1, E2 |
| `copilot_2026` | [Azure Public Dataset, GHCP coding agent 2026](https://github.com/Azure/AzurePublicDataset/releases/tag/ghcp-coding-agent-2026) | CC BY | 119 MB (1 of 7 days; 3.0 GB total) | 2026-06-06 only | Production tool batches at scale; E1, E2 |
| `tau_bench` | [sierra-research/tau-bench](https://github.com/sierra-research/tau-bench) and [tau2-bench](https://github.com/sierra-research/tau2-bench) | MIT | 60 MB + 165 MB | v1 full; tau2 sparse | Customer-service task episodes; E1 |
| `bfcl` | [gorilla / berkeley-function-call-leaderboard](https://github.com/ShishirPatil/gorilla/tree/main/berkeley-function-call-leaderboard) | Apache-2.0 | 0.8 MB | Downloaded | Parallel tool calls (v4 parallel and parallel_multiple); E2 |
| `huawei_coldstart` | [sir-lab/data-release, Huawei 2025](https://github.com/sir-lab/data-release/blob/main/README_data_release_2025.md) (Google Drive) | CC BY 4.0 | 20 MB (R3 of 5 regions) | Region 3 only | Production cold-start durations; E0, simulator |
| `toolathlon` | [hkust-nlp/Toolathlon](https://huggingface.co/datasets/hkust-nlp/Toolathlon) (Hugging Face) | Benchmark-only, gated | – | **Not downloaded** | Long multi-tool tasks; E1 (evaluation only) |
| Own cold-start measurements | This repo | MIT | – | To measure | Real pod cold starts on kind and Azure; E0 |

What each dataset gives us:

| Dataset | Tool names | Emit / result times | Parallel calls | Errors | Group id for splits |
|---|---|---|---|---|---|
| tracelab | yes (129) | both, per call | yes (14% of rounds) | yes | `user` |
| copilot_2026 | yes; custom tools anonymised | batch time + per-call duration | yes (`tool_batches`) | yes (`status`) | session only |
| tau_bench v1 | yes | none | no | from reply text | `task_id` |
| tau2_bench | yes | both, per message | no (in the file we have) | yes | `task_id` |
| bfcl | yes | none | yes, by design | no | question `id` |
| huawei_coldstart | function ids | cold-start breakdown | n/a | n/a | `userID`, `funcName` |

Notes:

- **tau2-bench** is fetched as a sparse clone. The full checkout is 948 MB, mostly published
  results, so we keep the code and domains plus one trajectory file (o4-mini, airline).
- **Toolathlon** is gated. Run `python data/download.py --dataset toolathlon` for access
  steps. Ask the instructor whether fitting count tables on it counts as training before
  using it for anything other than evaluation.
- **Copilot**: the other six days can be added to `DATASETS` in `download.py` if needed
  (each is 120 to 590 MB and needs `--yes-large`).

## Samples (committed)

`data/samples/` holds small, text-free samples: up to 200 sessions per dataset, each file
under 5 MB. They are in the common schema (`traces/schema.py`) with only tool names, timing,
errors, batch structure and a group id. Tests and CI use them. To rebuild them:

```bash
python tools/make_sample.py --all
python tools/inspect_dataset.py tracelab   # read-only overview of a raw dataset
```

## Why raw data is not in git

GitHub rejects any file over 100 MB and warns above 50 MB, and the raw data alone is
several hundred MB. Keeping it out of git keeps the repo small and fast to clone, and
respects each dataset's own licence. A size guard enforces this:
`python tools/check_sizes.py` (also `make check-sizes`, the pre-commit hook from
`make install-hooks`, and CI) fails if any tracked file is over 50 MB.

## Pipeline

1. **Download** each dataset into `data/raw/<dataset>/` with `data/download.py`.
2. **Read** the native format (JSONL, JSON, tar of JSONL shards, zip of CSV).
3. **Convert** to the common schema in `traces/schema.py` (`Session` → `Batch` → `Call`).
4. **Map tools** to 12 to 15 shared tool classes so that datasets are comparable.
5. **Split** 70/30 train/test **by group** with `predictors.evaluate.group_split`.
6. **Save** as Parquet under `data/processed/`.
