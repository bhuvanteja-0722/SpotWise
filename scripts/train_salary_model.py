#!/usr/bin/env python3
"""Train an annual salary benchmark model from the Kaggle job snapshot.

The model estimates the midpoint of disclosed INR annual salary ranges from
job title, skill tags, experience, and broad Indian market. It is not a local
business-success or worker-availability model.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import date
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, median_absolute_error, r2_score
from sklearn.model_selection import GroupShuffleSplit
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

ROOT = Path(__file__).resolve().parents[1]
import sys
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_analysis import CITIES, classify_role, cities_for, read_xlsx_rows
DEFAULT_MODEL = ROOT / "models" / "salary_estimator.joblib"
DEFAULT_METRICS = ROOT / "models" / "salary_estimator_metrics.json"
SOURCE_URL = "https://www.kaggle.com/datasets/shivamshrivastava21/indian-job-market-dataset-2025-2026"
MIN_ANNUAL_SALARY = 20_000
MAX_ANNUAL_SALARY = 50_000_000


def canonical_city(value: str) -> str:
    found = cities_for(value)
    return found[0] if found else "Other India"


def parse_dataset(path: Path) -> tuple[pd.DataFrame, np.ndarray, np.ndarray, dict]:
    records = []
    targets = []
    title_groups = []
    rows_seen = salary_rows = invalid_rows = 0
    headers = None
    for row in read_xlsx_rows(path):
        if headers is None:
            headers = row
            continue
        rows_seen += 1
        record = {headers[i]: row[i] if i < len(row) else "" for i in range(len(headers))}
        currency = (record.get("currency") or "INR").strip().upper()
        try:
            minimum = float((record.get("minimumSalary") or "").replace(",", ""))
            maximum = float((record.get("maximumSalary") or "").replace(",", ""))
        except ValueError:
            invalid_rows += 1
            continue
        if currency != "INR" or minimum <= 0 or maximum < minimum:
            invalid_rows += 1
            continue
        target = (minimum + maximum) / 2
        if not MIN_ANNUAL_SALARY <= target <= MAX_ANNUAL_SALARY:
            invalid_rows += 1
            continue

        title = re.sub(r"\s+", " ", record.get("title", "")).strip()
        skills = re.sub(r"\s+", " ", record.get("tagsAndSkills", "")).strip()
        location = record.get("location", "")
        try:
            min_experience = float(record.get("minimumExperience") or "")
        except ValueError:
            min_experience = np.nan
        try:
            max_experience = float(record.get("maximumExperience") or "")
        except ValueError:
            max_experience = np.nan
        if not title:
            invalid_rows += 1
            continue

        city = canonical_city(location)
        records.append({
            "text": f"{title} {title} {skills}",
            "city": city,
            "role_group": classify_role(title),
            "minimum_experience": min_experience,
            "maximum_experience": max_experience,
        })
        targets.append(target)
        title_groups.append(title.casefold())
        salary_rows += 1

    if len(records) < 1_000:
        raise ValueError(f"Only {len(records)} salary rows passed cleaning; expected at least 1,000.")
    return pd.DataFrame.from_records(records), np.asarray(targets, dtype=np.float64), np.asarray(title_groups), {
        "source_rows": rows_seen,
        "usable_salary_rows": salary_rows,
        "excluded_rows": invalid_rows,
        "salary_coverage_percent": round(salary_rows / max(rows_seen, 1) * 100, 2),
    }


def make_pipeline() -> Pipeline:
    features = ColumnTransformer(
        transformers=[
            ("title_and_skills", TfidfVectorizer(
                max_features=16_000, min_df=2, ngram_range=(1, 2),
                sublinear_tf=True, strip_accents="unicode", dtype=np.float32,
            ), "text"),
            ("market", OneHotEncoder(handle_unknown="ignore"), ["city", "role_group"]),
            ("experience", Pipeline([
                ("imputer", SimpleImputer(strategy="median", add_indicator=True)),
                ("scale", StandardScaler()),
            ]), ["minimum_experience", "maximum_experience"]),
        ],
        remainder="drop",
        sparse_threshold=0.3,
    )
    return Pipeline([
        ("features", features),
        ("regressor", Ridge(alpha=5.0, solver="lsqr")),
    ])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=Path, required=True, help="Kaggle job-market XLSX path")
    parser.add_argument("--model-out", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--metrics-out", type=Path, default=DEFAULT_METRICS)
    args = parser.parse_args()

    frame, target, title_groups, clean_stats = parse_dataset(args.jobs)
    split = GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=42)
    train_indexes, holdout_indexes = next(split.split(frame, groups=title_groups))
    pipeline = make_pipeline()
    pipeline.fit(frame.iloc[train_indexes], np.log1p(target[train_indexes]))

    holdout_log = pipeline.predict(frame.iloc[holdout_indexes])
    observed_log = np.log1p(target[holdout_indexes])
    predicted = np.maximum(0, np.expm1(holdout_log))
    absolute_errors = np.abs(predicted - target[holdout_indexes])
    log_residuals = observed_log - holdout_log
    low_residual, high_residual = np.quantile(log_residuals, [0.05, 0.95])
    within_band = np.logical_and(log_residuals >= low_residual, log_residuals <= high_residual)

    metrics = {
        "model_name": "SpotWise disclosed salary benchmark",
        "trained_at": date.today().isoformat(),
        "source_url": SOURCE_URL,
        "source_license": "CC BY-NC-SA 4.0; non-commercial and share-alike terms apply",
        "target": "Annual INR midpoint of disclosed minimum/maximum salary ranges",
        "features": ["job title and skill tags", "broad metro/other-India market", "inferred job role group", "minimum and maximum experience years"],
        **clean_stats,
        "training_rows": int(len(train_indexes)),
        "holdout_rows": int(len(holdout_indexes)),
        "holdout_split": "Random 80/20 split grouped by normalized exact job title to keep identical titles out of both sets",
        "holdout_mae_inr": round(float(mean_absolute_error(target[holdout_indexes], predicted)), 2),
        "holdout_median_absolute_error_inr": round(float(median_absolute_error(target[holdout_indexes], predicted)), 2),
        "holdout_r2": round(float(r2_score(target[holdout_indexes], predicted)), 4),
        "holdout_median_absolute_percentage_error": round(float(np.median(absolute_errors / target[holdout_indexes])) * 100, 2),
        "holdout_log_residual_90pct_interval": [round(float(low_residual), 5), round(float(high_residual), 5)],
        "holdout_log_interval_coverage_percent": round(float(within_band.mean()) * 100, 2),
        "limitations": [
            "2025 online postings with disclosed INR ranges only; not a census of wages or worker availability.",
            "Broad city classification from listing location text; no neighborhood wage resolution.",
            "The empirical error band is from a random title-grouped holdout and is not a guaranteed confidence interval.",
            "Model estimates an annual posted-salary benchmark; use local offers and current pay surveys for hiring decisions.",
        ],
    }
    artifact = {
        "pipeline": pipeline,
        "metrics": metrics,
        "model_version": 1,
    }
    args.model_out.parent.mkdir(parents=True, exist_ok=True)
    args.metrics_out.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, args.model_out, compress=3)
    args.metrics_out.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"model": str(args.model_out), "metrics": str(args.metrics_out), **metrics}, indent=2))


if __name__ == "__main__":
    main()
