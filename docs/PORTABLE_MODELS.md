# Portable predictor bundles (NOT committed)

**Status (Phase A, 2026-11-06):** `make fetch-models` downloads from the GitHub Release asset of
tag **v1.2** of this repository:
`https://github.com/SYNKRONOX/skypiea-prewarm/releases/download/v1.2/skypiea-models-af000231.tar.gz`
plus `SHA256SUMS` in the same release. It verifies both checksums, unpacks into `models/` and
says clearly if the release is not there yet (exit 2). `make models-archive` builds the
archive locally under `dist/` (git-ignored): **1,003,524 bytes, sha256
`918b36c5409ffaf261e37326cc290125ab99a69b56569c215dff7687989bdf23`** (it now includes
`ATTRIBUTION.md` = `docs/MODELS_ATTRIBUTION.md`; reproducible: same bytes on rebuild).

`make live` needs the real `support` and `coding` predictor bundles. A fresh laptop can only
build them from the datasets (`make data-all model-all predictor-image`: several hundred MB of
downloads and a long training run). This page measures the bundles and proposes how another
laptop could get them.

## What the bundles are (measured 2026-11-06)

| Bundle | Profile | Trained on | Files | Size |
|---|---|---|---|---|
| `lgbm-coding-af00023` | coding | TraceLab (CC BY 4.0) | 7 | 3,172,884 B |
| `lgbm-support-aabdfd2` | support | tau2-bench (MIT) | 7 | 301,709 B |
| `lgbm-prd-a7642d0` | prd (SYNTHETIC) | PRD graphs, timing from TraceLab/tau2 TRAIN | 7 | 497,514 B |
| all three, `tar` + `gzip -9`, reproducible (`--sort=name --mtime=2026-11-04 --owner=0`) | | | 21 | **1,005,909 B** |

They are byte-identical to the bundles inside the shipped image
`skypiea/predictor:af000231bc7b` (MANIFEST sha256 checked). Archive sha256:
`bb0e4955b7a2b746ecb837d190e10c312bb9571f106ec04fd659d7f44b7c7cf3`. The per-file sha256 values
are in `configs/live/models_files.sha256`; these are checksums only, not the bundles.

Contents: aggregated transition counts and timing quantiles (`counts.json`), a LightGBM text
model, an isotonic calibrator, the decide policy, the manifest, and `golden.json` (20 VAL
request histories: tool class names and times, no text or arguments). There are no prompts,
arguments or tool payloads; the trace pipeline keeps none.

## Options

1. **GitHub Release asset + `make fetch-models` (recommended).** Upload the archive above to a
   release of this repository, with a `SHA256SUMS` file, then run:
   `make fetch-models MODELS_URL=<release asset URL> MODELS_SHA256=bb0e49...` followed by
   `make live BUNDLES_DIR=models`. `demo/fetch_models.sh` exists already. It has no default URL,
   accepts https only, caps the size at 50 MB, verifies the archive and every file, and refuses
   absolute or `..` paths. Pros: small (1 MB), versioned, the repo stays free of model files.
   Cons: needs a release, and the datasets' licences need attribution in the release notes
   (CC BY 4.0 / CC BY / MIT).
2. **Commit the bundles under `models/`.** 4 MB, far below the 50 MB guard. It is simplest for
   a fresh clone, but it puts derived artefacts of third-party datasets into git history
   forever, and every retrain adds another 4 MB.
3. **Ship the image.** `docker save skypiea/predictor:af000231bc7b | gzip` is about 200 MB and
   includes the Python runtime. It is larger and less transparent than the bundles.

Before publishing anything, confirm that redistributing models derived from TraceLab (CC BY 4.0),
the Azure Copilot traces (CC BY) and tau2-bench (MIT) is acceptable, with attribution. The
coding bundle uses TraceLab only.

## LLMs used by the console (not distributed by us)

The Live Console's language models are **not** part of the bundles or the release asset. Each
user pulls them from the Ollama library on their own machine (`ollama pull <model>`, one model
per call), under the model's own licence. Checked on 2026-11-07 with Ollama's local model
metadata (`POST /api/show`, field `license`, first line) for the three models we ran:

| Model (Ollama tag) | Role in the console | Licence (as shipped by Ollama) |
|---|---|---|
| `llama3.1:8b` | default | Llama 3.1 Community License Agreement (Meta) |
| `qwen2.5:7b` | optional, Model toggle | Apache License 2.0 |
| `qwen3:8b` | optional, Model toggle, thinking disabled | Apache License 2.0 |

Recheck with `curl -s localhost:11434/api/show -d '{"model":"qwen3:8b"}'` and read `license`.
Licences can differ between sizes of the same family (other Qwen 2.5 sizes have their own
licence), so this table covers these three tags only.
