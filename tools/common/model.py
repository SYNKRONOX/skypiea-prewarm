"""Where heavy tools keep their scikit-learn model (trained at image build time)."""

import os
from pathlib import Path


def model_path(tool: str) -> Path:
    default = Path(__file__).resolve().parent.parent / tool / "model.joblib"
    return Path(os.environ.get("SKYPIEA_MODEL", default))
