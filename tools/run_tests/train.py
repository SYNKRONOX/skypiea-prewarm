"""Train the run_tests failure-risk model (random forest). Run at image build time."""

import sys

import joblib
import numpy as np
from sklearn.ensemble import RandomForestClassifier

from tools.common.model import model_path

FEATURES = ["lines_changed", "files_touched", "test_age_days", "past_failures", "duration_ms"]


def main(out: str | None = None) -> None:
    rng = np.random.default_rng(0)
    n = 20000
    X = np.column_stack([
        rng.poisson(40, n), rng.poisson(3, n), rng.integers(0, 900, n),
        rng.poisson(0.7, n), rng.lognormal(4, 1, n),
    ]).astype(np.float32)
    logit = 0.02 * X[:, 0] + 0.3 * X[:, 1] - 0.002 * X[:, 2] + 0.9 * X[:, 3] - 3.5
    y = rng.random(n) < 1 / (1 + np.exp(-logit))
    clf = RandomForestClassifier(n_estimators=150, max_depth=12, random_state=0, n_jobs=1)
    clf.fit(X, y)
    path = out or model_path("run_tests")
    joblib.dump({"model": clf, "features": FEATURES, "n_train": n}, path, compress=3)
    print(f"run_tests model: {n} rows, failure rate {y.mean():.3f} -> {path}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
