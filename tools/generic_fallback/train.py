"""Train the generic_fallback router: raw tool name -> coarse intent. Run at build time."""

import random
import sys

import joblib
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline

from tools.common.model import model_path

KEYWORDS = {
    "math": ["calculate", "solve", "integral", "derivative", "sum", "prime", "matrix"],
    "finance": ["stock", "loan", "mortgage", "tax", "interest", "invest", "currency"],
    "weather": ["weather", "forecast", "temperature", "rain", "humidity", "air_quality"],
    "science": ["physics", "chemistry", "dna", "gene", "protein", "velocity", "element"],
    "health": ["bmi", "calorie", "diet", "nutrition", "medical", "symptom"],
    "location": ["restaurant", "nearby", "map", "geocode", "address", "distance"],
    "events": ["concert", "game", "match", "league", "player", "ticket"],
    "legal": ["court", "lawsuit", "case", "patent", "statute"],
    "data_ml": ["predict", "model", "train", "database", "query", "dataset"],
    "media": ["music", "song", "movie", "video", "artist", "book_author"],
    "agent": ["spawn_agent", "task", "subagent", "send_message", "delegate"],
    "planning": ["plan", "todo", "update_plan", "think", "record_observation"],
    "web": ["fetch", "search_web", "url", "browse", "http_get"],
    "other": ["misc", "custom_tool", "unknown", "noop", "transfer_to_human"],
}
PREFIX = ["get_", "find_", "compute_", "lookup_", "", "fetch_", "calc_"]
SUFFIX = ["", "_info", "_details", "_v2", "_by_id", "s"]


def main(out: str | None = None) -> None:
    rng = random.Random(0)
    X, y = [], []
    for intent, words in KEYWORDS.items():
        for _ in range(600):
            w = rng.sample(words, k=min(2, len(words)))
            X.append(rng.choice(PREFIX) + "_".join(w) + rng.choice(SUFFIX))
            y.append(intent)
    pipe = make_pipeline(
        TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), sublinear_tf=True),
        LogisticRegression(max_iter=3000, C=4.0, random_state=0),
    )
    pipe.fit(X, y)
    path = out or model_path("generic_fallback")
    joblib.dump({"pipeline": pipe, "n_train": len(X)}, path)
    print(f"generic_fallback model: {len(X)} examples -> {path}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
