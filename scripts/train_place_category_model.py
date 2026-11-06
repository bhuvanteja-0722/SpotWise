#!/usr/bin/env python3
"""Train an Overture-labeled business-name category classifier.

This classifies place listings; it does not estimate profitability or business success.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from datetime import date
from pathlib import Path

import joblib
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import accuracy_score, f1_score, classification_report
from sklearn.model_selection import GroupShuffleSplit
from sklearn.pipeline import Pipeline
from sklearn.linear_model import SGDClassifier

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL = ROOT / "models" / "place_category_classifier.joblib"
DEFAULT_METRICS = ROOT / "models" / "place_category_classifier_metrics.json"
SOURCE_URL = "https://docs.overturemaps.org/guides/places/"


def clean_name(name: str) -> str:
    return re.sub(r"\s+", " ", name.casefold()).strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--places", type=Path, required=True, help="CSV from prepare_overture_places.py without --category")
    parser.add_argument("--model-out", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--metrics-out", type=Path, default=DEFAULT_METRICS)
    parser.add_argument("--minimum-class-size", type=int, default=20)
    args = parser.parse_args()

    with args.places.open(encoding="utf-8-sig", newline="") as source:
        raw = list(csv.DictReader(source))
    records = [(clean_name(row.get("name", "")), row.get("business_type", "").strip()) for row in raw]
    records = [(name, label) for name, label in records if name and label and label != "uncategorized"]
    label_counts = Counter(label for _, label in records)
    retained = [(name, label) for name, label in records if label_counts[label] >= args.minimum_class_size]
    if len({label for _, label in retained}) < 2 or len(retained) < 100:
        raise SystemExit("Need at least 100 named listings across at least two categories with the minimum class size.")

    names = np.asarray([name for name, _ in retained])
    labels = np.asarray([label for _, label in retained])
    groups = names.copy()
    split = GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=42)
    train_idx, test_idx = next(split.split(names, labels, groups=groups))
    pipeline = Pipeline([
        ("text", TfidfVectorizer(
            analyzer="char_wb", ngram_range=(2, 4), min_df=2,
            max_features=50_000, sublinear_tf=True, strip_accents="unicode",
        )),
        ("classifier", SGDClassifier(
            loss="log_loss", max_iter=40, tol=1e-3, class_weight="balanced",
            alpha=1e-5, n_jobs=-1, random_state=42,
        )),
    ])
    pipeline.fit(names[train_idx], labels[train_idx])
    predicted = pipeline.predict(names[test_idx])
    probabilities = pipeline.predict_proba(names[test_idx])
    top_three = np.argsort(probabilities, axis=1)[:, -min(3, probabilities.shape[1]):]
    top_three_accuracy = float(np.mean([
        labels[test_idx][index] in pipeline.classes_[top_three[index]]
        for index in range(len(test_idx))
    ]))
    report = classification_report(labels[test_idx], predicted, output_dict=True, zero_division=0)
    source_cities = sorted({row.get("city", "") for row in raw if row.get("city")})
    source_releases = sorted({
        match.group(0) for row in raw
        if (match := re.search(r"20\d{2}-\d{2}-\d{2}\.\d+", args.places.name))
    })
    metrics = {
        "model_name": "SpotWise Overture Places name-to-category classifier",
        "trained_at": date.today().isoformat(),
        "source_url": SOURCE_URL,
        "source_release": source_releases[0] if source_releases else "see input file / Overture release",
        "source_cities": source_cities,
        "source_rows": len(raw),
        "named_labeled_rows": len(records),
        "training_rows": int(len(train_idx)),
        "holdout_rows": int(len(test_idx)),
        "excluded_rare_or_unlabeled_rows": int(len(records) - len(retained) + len(raw) - len(records)),
        "minimum_examples_per_category": args.minimum_class_size,
        "categories": sorted(set(labels)),
        "holdout_split": "80/20 grouped by normalized exact place name; identical names do not cross the split",
        "holdout_accuracy": round(float(accuracy_score(labels[test_idx], predicted)), 4),
        "holdout_top_3_accuracy": round(top_three_accuracy, 4),
        "holdout_macro_f1": round(float(f1_score(labels[test_idx], predicted, average="macro", zero_division=0)), 4),
        "holdout_weighted_f1": round(float(f1_score(labels[test_idx], predicted, average="weighted", zero_division=0)), 4),
        "per_category": {
            label: {"precision": round(values["precision"], 4), "recall": round(values["recall"], 4), "f1": round(values["f1-score"], 4), "support": int(values["support"])}
            for label, values in report.items() if label in set(labels)
        },
        "limitations": [
            "Predicts Overture taxonomy labels from place names; it does not predict demand, revenue, profit, or business success.",
            "Training coverage is limited to the cities and release present in the input CSV; language and naming patterns may not generalize to other cities.",
            "Model probabilities are ranking signals, not calibrated confidence. Review low-confidence or consequential classifications manually.",
            "Overture source existence confidence is not a calibrated probability and does not measure category correctness.",
        ],
    }
    artifact = {"pipeline": pipeline, "metrics": metrics, "model_version": 1}
    args.model_out.parent.mkdir(parents=True, exist_ok=True)
    args.metrics_out.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, args.model_out, compress=3)
    args.metrics_out.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"model": str(args.model_out), "metrics": str(args.metrics_out)}, indent=2))


if __name__ == "__main__":
    main()
