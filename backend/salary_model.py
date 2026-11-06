from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from .db import ROOT

MODEL_PATH = Path(__import__("os").getenv("SPOTWISE_SALARY_MODEL", str(ROOT / "models" / "salary_estimator.joblib")))
CITY_ALIASES = {
    "bengaluru": ("bengaluru", "bangalore"), "mumbai": ("mumbai", "bombay"),
    "hyderabad": ("hyderabad", "secunderabad"), "pune": ("pune",),
    "chennai": ("chennai", "madras"), "delhi ncr": ("delhi", "gurugram", "gurgaon", "noida"),
    "kolkata": ("kolkata", "calcutta"), "ahmedabad": ("ahmedabad",),
}


def canonical_city(city: str) -> str:
    normalized = city.casefold()
    for known, aliases in CITY_ALIASES.items():
        if any(alias in normalized for alias in aliases):
            return {"bengaluru": "Bengaluru", "mumbai": "Mumbai", "hyderabad": "Hyderabad", "pune": "Pune", "chennai": "Chennai", "delhi ncr": "Delhi NCR", "kolkata": "Kolkata", "ahmedabad": "Ahmedabad"}[known]
    return "Other India"


def role_group(title: str) -> str:
    value = title.casefold()
    if any(word in value for word in ("restaurant", "chef", "cook", "barista", "hotel", "hospitality", "food service")):
        return "Food & hospitality"
    if any(word in value for word in ("retail", "store", "shop", "cashier", "merchandiser")):
        return "Retail"
    if any(word in value for word in ("sales", "business development", "account executive")):
        return "Sales"
    if any(word in value for word in ("customer service", "customer support", "call center", "front desk", "receptionist")):
        return "Customer service"
    if any(word in value for word in ("operation", "logistics", "warehouse", "delivery", "supply chain")):
        return "Operations & logistics"
    if any(word in value for word in ("software", "developer", "engineer", "data", "product", "technology", "it ")):
        return "Technology"
    return "Other"


@lru_cache(maxsize=1)
def load_salary_model() -> dict:
    if not MODEL_PATH.exists():
        raise FileNotFoundError(f"Salary model not found: {MODEL_PATH}")
    return joblib.load(MODEL_PATH)


def estimate_salary(city: str, title: str, skills: str, experience_years: float) -> dict:
    artifact = load_salary_model()
    features = pd.DataFrame([{
        "text": f"{title.strip()} {title.strip()} {skills.strip()}",
        "city": canonical_city(city),
        "role_group": role_group(title),
        "minimum_experience": experience_years,
        "maximum_experience": experience_years,
    }])
    pipeline = artifact["pipeline"]
    predicted_log = float(pipeline.predict(features)[0])
    predicted_annual = max(0.0, float(np.expm1(predicted_log)))
    residual_low, residual_high = artifact["metrics"]["holdout_log_residual_90pct_interval"]
    low_annual = max(0.0, float(np.expm1(predicted_log + residual_low)))
    high_annual = max(low_annual, float(np.expm1(predicted_log + residual_high)))
    return {
        "city": city,
        "role_group": role_group(title),
        "annual_salary_inr": round(predicted_annual),
        "monthly_salary_inr": round(predicted_annual / 12),
        "annual_range_inr": [round(low_annual), round(high_annual)],
        "monthly_range_inr": [round(low_annual / 12), round(high_annual / 12)],
        "model_metrics": artifact["metrics"],
        "note": "Model estimate from disclosed 2025 job postings; not a guaranteed offer or neighborhood wage survey.",
    }
