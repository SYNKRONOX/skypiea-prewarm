# models/

Model bundles for the predictor service. **Bundles are not committed** (only this file is);
rebuild them with:

    nice -n 10 python -m services.predictor.export --source tracelab \
        --cold-start ~/skypiea-infra/configs/cold_start.yaml
    # -> models/lgbm-tracelab-<commit>/

A bundle is a folder of text files, verified as a whole before it is served
(`services/predictor/engine.py`):

| File | What |
|---|---|
| `MANIFEST.json` | `schema_version`, `model_version` (the folder name), `source`, `git_commit`, `vocabulary`, and the sha256 of every other file |
| `ranker.lgb` | LightGBM re-ranker (text format) |
| `calibrator.json` | isotonic map (fitted on val; unused when `calibration: none`) |
| `ranker_meta.json` | features, vocabulary codes, config, n-gram weights |
| `counts.json` | the transition graph (counts, timing quantiles, batch stats) |
| `decide.yaml` | the warm policy, a copy of configs/model/decide.yaml at export |
| `golden.json` | 20 val requests and the answers this bundle gives (checked on load and reload) |

No pickle or joblib: JSON, YAML and LightGBM text only. The same data and seeds give
byte-identical files (`tests/test_service.py::test_bundle_round_trip_is_deterministic`).
The cold-start file is not in the bundle; the service reads it from `COLD_START_PATH`.
