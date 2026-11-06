from __future__ import annotations

import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import ROOT
from .models import Observation, SignalBatch, Study

ANALYSIS_FILE = Path(__import__("os").getenv("SPOTWISE_ANALYSIS_FILE", str(ROOT / "data" / "analysis.json")))
CATEGORY_LABELS = {
    "footfall": "Footfall",
    "competition": "Business competition",
    "traffic": "Traffic",
    "parking": "Parking",
    "transit": "Public transport",
    "worker_supply": "Worker availability",
    "salary": "Salary and wage signals",
    "cost_of_living": "Cost of living",
    "rent": "Commercial rent input",
}


def load_analysis() -> dict:
    if not ANALYSIS_FILE.exists():
        raise FileNotFoundError(f"Analysis data file not found: {ANALYSIS_FILE}")
    return json.loads(ANALYSIS_FILE.read_text(encoding="utf-8"))


def market_context(data: dict, city: str) -> dict:
    job = next((item for item in data.get("jobs", {}).get("cities", []) if item["name"].casefold() == city.casefold()), None)
    rail = next((item for item in data.get("rail", {}).get("cities", []) if item["name"].casefold() == city.casefold()), None)
    rent = data.get("rentals", {}) if city.casefold() == "mumbai" else None
    return {
        "job_market": job,
        "rail_snapshot": rail,
        "rental_market": rent and {
            "city": "Mumbai",
            "median_monthly_asking_rent": rent.get("medianMonthlyRent"),
            "record_count": rent.get("validRentCount"),
            "property_type": "Residential listings; not commercial lease data",
            "snapshot": "2023",
        },
    }


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    value = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(value))


def summarize_signals(
    session: Session,
    city: str,
    area: str | None = None,
    radius_km: float | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
) -> dict:
    query = select(Observation).where(func.lower(Observation.city) == city.casefold())
    has_geo_radius = radius_km is not None and latitude is not None and longitude is not None
    if area and not has_geo_radius:
        query = query.where(func.lower(Observation.area) == area.casefold())
    rows = list(session.scalars(query))
    nearest_transit: float | None = None
    unlocated = 0
    if has_geo_radius:
        for row in rows:
            if row.category == "transit" and row.latitude is not None and row.longitude is not None:
                distance = haversine_km(latitude, longitude, row.latitude, row.longitude)
                nearest_transit = distance if nearest_transit is None else min(nearest_transit, distance)
        selected = []
        for row in rows:
            if row.latitude is None or row.longitude is None:
                unlocated += 1
                continue
            distance = haversine_km(latitude, longitude, row.latitude, row.longitude)
            if distance <= radius_km:
                selected.append((row, distance))
        rows = [pair[0] for pair in selected]

    grouped: dict[str, list[Observation]] = defaultdict(list)
    by_category: dict[str, Counter] = defaultdict(Counter)
    hourly: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        grouped[row.category].append(row)
        by_category[row.category][row.detail or row.metric] += 1
        if row.observed_at:
            hourly[row.category][row.observed_at.hour] += row.value

    categories = {}
    for category, observations in grouped.items():
        values = [row.value for row in observations]
        metrics: dict[str, list[Observation]] = defaultdict(list)
        for row in observations:
            metrics[row.metric].append(row)
        categories[category] = {
            "label": CATEGORY_LABELS.get(category, category),
            "record_count": len(observations),
            "total": round(sum(values), 2),
            "average": round(statistics.fmean(values), 2),
            "median": round(statistics.median(values), 2),
            "minimum": round(min(values), 2),
            "maximum": round(max(values), 2),
            "unit": observations[0].unit,
            "metrics": {
                metric: {
                    "record_count": len(items),
                    "total": round(sum(row.value for row in items), 2),
                    "average": round(statistics.fmean(row.value for row in items), 2),
                    "median": round(statistics.median(row.value for row in items), 2),
                    "unit": items[0].unit,
                }
                for metric, items in metrics.items()
            },
            "breakdown": [{"name": label, "records": count} for label, count in by_category[category].most_common(8)],
            "peak_hour": max(hourly[category], key=hourly[category].get) if hourly[category] else None,
        }

    batch_ids = sorted({row.batch_id for row in rows})
    batches = list(session.scalars(select(SignalBatch).where(SignalBatch.id.in_(batch_ids)))) if batch_ids else []
    source_counts = Counter(row.category for row in rows)
    return {
        "city": city,
        "area": area,
        "radius_km": radius_km,
        "record_count": len(rows),
        "unlocated_excluded": unlocated,
        "categories": categories,
        "nearest_transit_distance_km": round(nearest_transit, 3) if nearest_transit is not None else None,
        "sources": [{"id": batch.id, "name": batch.name, "category": batch.category, "row_count": batch.row_count} for batch in batches],
        "available_categories": sorted(source_counts),
        "warnings": _scope_warnings(area, radius_km, latitude, longitude, unlocated),
    }


def _scope_warnings(area, radius_km, latitude, longitude, unlocated) -> list[str]:
    warnings = []
    if not area and not (radius_km is not None and latitude is not None and longitude is not None):
        warnings.append("Citywide upload totals may include records from many neighborhoods.")
    if radius_km is not None and (latitude is None or longitude is None):
        warnings.append("A radius was requested without coordinates; radius filtering was not applied.")
    if unlocated:
        warnings.append(f"{unlocated:,} records without coordinates were excluded from the radius filter.")
    return warnings


def _category_stat(signals: dict, category: str, metric_terms: tuple[str, ...] = ()) -> dict | None:
    block = signals["categories"].get(category)
    if not block:
        return None
    if metric_terms:
        for metric, stats in block.get("metrics", {}).items():
            if any(term in metric.casefold() for term in metric_terms):
                return stats
    return block


def break_even(assumptions: dict) -> dict:
    fixed_inputs = ("monthly_rent", "staff_count", "monthly_pay_per_staff", "other_fixed_costs")
    if not any(key in assumptions for key in fixed_inputs):
        return {"status": "incomplete", "note": "Add rent and operating assumptions to calculate a break-even scenario."}
    rent = max(0.0, assumptions.get("monthly_rent", 0.0))
    staff = max(0.0, assumptions.get("staff_count", 0.0))
    salary = max(0.0, assumptions.get("monthly_pay_per_staff", 0.0))
    other = max(0.0, assumptions.get("other_fixed_costs", 0.0))
    ticket = max(0.0, assumptions.get("average_ticket", 0.0))
    margin = min(100.0, max(0.0, assumptions.get("gross_margin_percent", 0.0))) / 100
    customers = max(0.0, assumptions.get("expected_customers_per_day", 0.0))
    fixed = rent + staff * salary + other
    sales = ticket * customers * 30
    gross_profit = sales * margin
    output = {
        "status": "ready" if ticket > 0 and margin > 0 else "incomplete",
        "monthly_fixed_costs": round(fixed, 2),
        "monthly_sales_at_assumptions": round(sales, 2),
        "estimated_operating_surplus": round(gross_profit - fixed, 2),
        "break_even_monthly_sales": round(fixed / margin, 2) if margin else None,
        "break_even_customers_per_day": round(fixed / (ticket * margin * 30), 2) if ticket and margin else None,
        "assumption_customer_volume": customers,
        "disclaimer": "Scenario arithmetic only; excludes taxes, financing, owner pay, setup costs, seasonality, and other variable costs.",
    }
    return output


def score_study(session: Session, study: Study, data: dict) -> dict:
    assumptions = json.loads(study.assumptions_json or "{}")
    weights = json.loads(study.weights_json or "{}")
    signals = summarize_signals(session, study.city, study.area, study.radius_km, study.latitude, study.longitude)
    scores: dict[str, dict] = {}

    rent = assumptions.get("monthly_rent")
    rent_budget = assumptions.get("rent_budget")
    if rent is not None and rent_budget and rent_budget > 0:
        scores["rent"] = {"score": round(min(100, 100 * rent_budget / max(rent, 1)), 1), "value": rent, "target": rent_budget, "unit": "INR / month", "method": "Compares your entered commercial rent quote with your budget."}

    footfall = _category_stat(signals, "footfall", ("daily", "visitors")) or _category_stat(signals, "footfall")
    min_footfall = assumptions.get("minimum_daily_footfall")
    if footfall and min_footfall and min_footfall > 0:
        actual = footfall.get("total", 0)
        scores["footfall"] = {"score": round(min(100, actual / min_footfall * 100), 1), "value": actual, "target": min_footfall, "unit": footfall.get("unit", "count"), "method": "Uploaded footfall total compared with your minimum daily target; use daily-level input rows."}

    competition = _category_stat(signals, "competition")
    max_competitors = assumptions.get("maximum_competitors")
    if competition and max_competitors and max_competitors > 0:
        count = competition.get("total", competition.get("record_count", 0))
        scores["competition"] = {"score": round(max(0, 100 * (1 - count / max_competitors)), 1), "value": count, "target": max_competitors, "unit": "businesses", "method": "Counts uploaded competitor records within the selected scope and compares with your cap."}

    max_transit = assumptions.get("max_transit_distance_km")
    if signals["nearest_transit_distance_km"] is not None and max_transit and max_transit > 0:
        distance = signals["nearest_transit_distance_km"]
        scores["transit"] = {"score": round(max(0, 100 * (1 - distance / max_transit)), 1), "value": distance, "target": max_transit, "unit": "km", "method": "Nearest uploaded geocoded transit stop by straight-line distance; not walking distance."}

    transit_connections = _category_stat(signals, "transit", ("routes_serving_stop",))
    min_route_connections = assumptions.get("minimum_route_stop_connections")
    if transit_connections and min_route_connections and min_route_connections > 0:
        connections = transit_connections.get("total", 0)
        scores["transit_service"] = {
            "score": round(min(100, connections / min_route_connections * 100), 1),
            "value": connections, "target": min_route_connections, "unit": "scheduled route-stop connections",
            "method": "Sums distinct GTFS routes serving geocoded stops within the selected radius during the next 7 service days. A route can count at multiple stops; it is not passenger frequency or ridership.",
        }

    traffic = _category_stat(signals, "traffic", ("speed",))
    min_speed = assumptions.get("minimum_traffic_speed_kmh")
    if traffic and min_speed and min_speed > 0:
        speed = traffic.get("average", 0)
        scores["traffic"] = {"score": round(min(100, speed / min_speed * 100), 1), "value": speed, "target": min_speed, "unit": "km/h", "method": "Average uploaded speed compared with your minimum acceptable road-speed assumption."}

    parking = _category_stat(signals, "parking", ("available", "spaces")) or _category_stat(signals, "parking")
    min_parking = assumptions.get("minimum_parking_spaces")
    if parking and min_parking and min_parking > 0:
        spaces = parking.get("total", 0)
        scores["parking"] = {"score": round(min(100, spaces / min_parking * 100), 1), "value": spaces, "target": min_parking, "unit": "spaces", "method": "Uploaded available parking spaces compared with your minimum target."}

    workers = _category_stat(signals, "worker_supply")
    required_workers = assumptions.get("required_workers")
    if workers and required_workers and required_workers > 0:
        available = workers.get("total", 0)
        scores["worker_supply"] = {"score": round(min(100, available / required_workers * 100), 1), "value": available, "target": required_workers, "unit": "workers", "method": "Uploaded worker availability compared with your staffing requirement."}

    salary = _category_stat(signals, "salary")
    max_salary = assumptions.get("maximum_monthly_salary")
    if salary and max_salary and max_salary > 0:
        value = salary.get("median", 0)
        scores["salary"] = {"score": round(min(100, 100 * max_salary / max(value, 1)), 1), "value": value, "target": max_salary, "unit": salary.get("unit", "INR / month"), "method": "Uploaded salary median compared with your monthly wage budget; keep units consistent."}

    living = _category_stat(signals, "cost_of_living")
    max_living = assumptions.get("maximum_cost_of_living")
    if living and max_living and max_living > 0:
        value = living.get("median", 0)
        scores["cost_of_living"] = {"score": round(min(100, 100 * max_living / max(value, 1)), 1), "value": value, "target": max_living, "unit": living.get("unit", "INR / month"), "method": "Uploaded cost-of-living median compared with your entered cap."}

    weighted = [(name, result, float(weights.get(name, 1.0))) for name, result in scores.items() if float(weights.get(name, 1.0)) > 0]
    weight_total = sum(item[2] for item in weighted)
    composite = round(sum(result["score"] * weight for _, result, weight in weighted) / weight_total, 1) if len(weighted) >= 3 and weight_total else None
    return {
        "readiness_score": composite,
        "readiness_status": "scored" if composite is not None else "insufficient-data",
        "scored_factor_count": len(weighted),
        "available_uploaded_categories": signals["available_categories"],
        "factor_scores": scores,
        "signals": signals,
        "market_context": market_context(data, study.city),
        "operating_scenario": break_even(assumptions),
        "score_note": "Composite readiness appears only when at least three factors have both usable uploaded data and your target assumptions. It is a transparent heuristic, not a prediction of success.",
    }


def serialize_study(session: Session, study: Study) -> dict:
    return {
        "id": study.id,
        "name": study.name,
        "city": study.city,
        "area": study.area,
        "business_type": study.business_type,
        "radius_km": study.radius_km,
        "latitude": study.latitude,
        "longitude": study.longitude,
        "assumptions": json.loads(study.assumptions_json or "{}"),
        "weights": json.loads(study.weights_json or "{}"),
        "results": json.loads(study.results_json or "{}"),
        "created_at": study.created_at.isoformat() + "Z",
        "updated_at": study.updated_at.isoformat() + "Z",
    }
