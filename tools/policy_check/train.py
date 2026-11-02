"""Train the policy_check request -> policy-topic classifier. Run at image build time."""

import random
import sys

import joblib
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline

from tools.common.data import load_json
from tools.common.model import model_path

TEMPLATES = {
    "refund": ["i want my money back for {x}", "refund the {x} please", "return {x} for a refund",
               "can i get a refund on order {n}", "the {x} arrived broken, refund it"],
    "exchange": ["swap the {x} for a different size", "exchange my {x}",
                 "i need another colour of the {x}", "replace {x} with a new one"],
    "cancellation": ["cancel order {n}", "i no longer need the {x}, cancel it",
                     "cancel my flight", "please cancel the reservation {n}"],
    "modification": ["change the address on order {n}", "add a bag to my reservation",
                     "update payment for order {n}", "change my flight date"],
    "promo": ["apply code SKY{n}", "does my promo code work on the {x}", "discount code {n}",
              "why was my coupon rejected"],
    "escalation": ["let me talk to a human", "i want a manager", "transfer me to an agent",
                   "this is unacceptable, escalate"],
}
ITEMS = ["lamp", "speaker", "jacket", "headphones", "chair", "watch", "router", "kettle"]


def main(out: str | None = None) -> None:
    rng = random.Random(0)
    X, y = [], []
    for topic, temps in TEMPLATES.items():
        for _ in range(400):
            X.append(rng.choice(temps).format(x=rng.choice(ITEMS), n=rng.randrange(10**5)))
            y.append(topic)
    for p in load_json("policies"):
        X.append(p["text"].lower())
        y.append(p["topic"])
    pipe = make_pipeline(TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True),
                         LogisticRegression(max_iter=2000, C=4.0, random_state=0))
    pipe.fit(X, y)
    path = out or model_path("policy_check")
    joblib.dump({"pipeline": pipe, "n_train": len(X)}, path)
    print(f"policy_check model: {len(X)} examples -> {path}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
