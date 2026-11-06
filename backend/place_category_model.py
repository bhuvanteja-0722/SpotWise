from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import joblib
import numpy as np

from .db import ROOT

MODEL_PATH = Path(__import__("os").getenv(
    "SPOTWISE_PLACE_MODEL", str(ROOT / "models" / "place_category_classifier.joblib")
))


@lru_cache(maxsize=1)
def load_place_model() -> dict:
    if not MODEL_PATH.exists():
        raise FileNotFoundError(f"Place category model not found: {MODEL_PATH}")
    return joblib.load(MODEL_PATH)


def classify_place_names(names: list[str], city: str | None = None) -> dict:
    artifact = load_place_model()
    model = artifact["pipeline"]
    probabilities = model.predict_proba(names)
    classes = model.classes_
    top_n = min(3, len(classes))
    order = np.argsort(probabilities, axis=1)[:, -top_n:][:, ::-1]
    metrics = artifact["metrics"]
    scope_cities = metrics.get("source_cities", [])
    in_scope = not city or any(city.casefold() == item.casefold() for item in scope_cities)
    return {
        "model": metrics["model_name"],
        "training_scope": {"cities": scope_cities, "release": metrics.get("source_release")},
        "evaluation": {
            "holdout_accuracy": metrics.get("holdout_accuracy"),
            "holdout_macro_f1": metrics.get("holdout_macro_f1"),
            "holdout_weighted_f1": metrics.get("holdout_weighted_f1"),
        },
        "scope_warning": None if in_scope else "This name classifier was trained on Bengaluru listings only; treat results for another city as rough suggestions and review them manually.",
        "predictions": [
            {
                "name": name,
                "suggestions": [
                    {"category": str(classes[index]), "model_score": round(float(probabilities[row_index, index]), 4)}
                    for index in order[row_index]
                ],
            }
            for row_index, name in enumerate(names)
        ],
        "note": "Scores rank likely Overture taxonomy labels; they are not calibrated probabilities. This model classifies names only and does not predict demand, revenue, profit, or success.",
    }
