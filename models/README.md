# models/

Model bundles for the predictor service. **Bundles are not committed** (only this file is);
rebuild them with:

    nice -n 10 python -m services.predictor.export --source tracelab --profile coding \
        --cold-start configs/cold_start.yaml     # -> models/lgbm-coding-<commit>/
    nice -n 10 python -m services.predictor.export --source tau2_bench --profile support \
        --cold-start configs/cold_start.yaml     # -> models/lgbm-support-<commit>/
    nice -n 10 python -m traces.generate_synthetic --type prd       # SYNTHETIC PRD traces
    nice -n 10 python -m services.predictor.export --source synthetic_prd --profile prd \
        --cold-start configs/cold_start.yaml     # -> models/lgbm-prd-<commit>/

The image holds three bundles (`/models/coding`, `/models/support`, `/models/prd`);
`PREDICTOR_PROFILE` picks one. `prd` is SYNTHETIC (trained on traces sampled from the PRD
scenario graphs) for the demo; it is not evidence about real workloads (docs/model.md, "Image: build, push, run").

A bundle is a folder of text files, verified as a whole before it is served
(`services/predictor/engine.py`):

| File | What |
|---|---|
| `MANIFEST.json` | `schema_version`, `model_version` (the folder name), `source`, `profile`, `git_commit`, `cold_start_sha256` (the file used at export), `synthetic` (true for bundles from `synthetic_*` sources, with a `data_note`), `vocabulary`, and the sha256 of every other file |
| `ranker.lgb` | LightGBM re-ranker (text format) |
| `calibrator.json` | isotonic map (fitted on val; unused when `calibration: none`) |
| `ranker_meta.json` | features, vocabulary codes, config, n-gram weights |
| `counts.json` | the transition graph (counts, timing quantiles, batch stats) |
| `decide.yaml` | the warm policy: the profile's resolved settings (configs/model/profiles.yaml over decide.yaml) |
| `golden.json` | 20 val requests and the answers this bundle gives (checked on load and reload; with a different cold-start file only the ranking is checked) |

No pickle or joblib: JSON, YAML and LightGBM text only. The same data and seeds give
byte-identical files (`tests/test_service.py::test_bundle_round_trip_is_deterministic`).
The cold-start file is not in the bundle; the service reads it from `COLD_START_PATH`.
