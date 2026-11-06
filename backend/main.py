from __future__ import annotations

import csv
import io
import json
import math
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import delete, event, func, select
from sqlalchemy.orm import Session

from .analytics import break_even, load_analysis, market_context, score_study, serialize_study, summarize_signals
from .db import Base, ROOT, engine, get_session
from .models import Observation, SignalBatch, Study, utc_now
from .schemas import DatasetImportResult, StudyComparison, StudyCreate, StudyUpdate

MAX_UPLOAD_BYTES = int(os.getenv("SPOTWISE_MAX_UPLOAD_MB", "20")) * 1024 * 1024
MAX_UPLOAD_ROWS = int(os.getenv("SPOTWISE_MAX_UPLOAD_ROWS", "100000"))
ALLOWED_CATEGORIES = {
    "footfall", "competition", "traffic", "parking", "transit",
    "worker_supply", "salary", "cost_of_living", "rent",
}


@event.listens_for(engine, "connect")
def _enable_sqlite_foreign_keys(connection, _record):
    if engine.dialect.name == "sqlite":
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    (ROOT / "data").mkdir(exist_ok=True)
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="SpotWise API",
    description="Business location research API with saved studies and user-imported local market signals.",
    version="1.0.0",
    lifespan=lifespan,
)


def get_data() -> dict:
    try:
        return load_analysis()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def as_public_batch(batch: SignalBatch) -> dict:
    return {
        "id": batch.id,
        "name": batch.name,
        "category": batch.category,
        "filename": batch.filename,
        "source": batch.source,
        "row_count": batch.row_count,
        "imported_at": batch.imported_at.isoformat() + "Z",
    }


def _normalise_header(header: str) -> str:
    return "".join(character for character in (header or "").casefold().strip() if character.isalnum())


def _value(row: dict[str, str], *keys: str) -> str:
    normalized = {_normalise_header(key): (value or "").strip() for key, value in row.items() if key}
    for key in keys:
        result = normalized.get(_normalise_header(key), "")
        if result:
            return result
    return ""


def _optional_float(raw: str, label: str, line: int) -> float | None:
    if not raw:
        return None
    try:
        value = float(raw.replace(",", "").strip())
    except ValueError as exc:
        raise ValueError(f"Line {line}: {label} must be numeric") from exc
    if not math.isfinite(value):
        raise ValueError(f"Line {line}: {label} must be a finite number")
    return value


def _parse_datetime(raw: str, line: int) -> datetime | None:
    if not raw:
        return None
    text = raw.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).replace(tzinfo=None)
    except ValueError as exc:
        raise ValueError(f"Line {line}: observed_at must be ISO date/time, for example 2026-06-01T08:00:00") from exc


@app.get("/api/v1/health", tags=["system"])
def health(session: Session = Depends(get_session)):
    session.execute(select(func.count(Study.id)))
    return {"status": "ok", "database": engine.dialect.name, "analysis_file": load_analysis() is not None}


@app.get("/api/v1/markets", tags=["analysis"])
def markets(data: dict = Depends(get_data)):
    return {
        "markets": [item["name"] for item in data.get("jobs", {}).get("cities", [])],
        "business_types": ["Cafe / restaurant", "Retail shop", "Salon / wellness", "Fitness studio", "Other local service"],
    }


@app.get("/api/v1/areas", tags=["analysis"])
def areas(city: str = Query(min_length=2, max_length=90), session: Session = Depends(get_session), data: dict = Depends(get_data)):
    values = set(session.scalars(select(Observation.area).where(func.lower(Observation.city) == city.casefold(), Observation.area != "Citywide")))
    if city.casefold() == "mumbai":
        values.update(item["name"] for item in data.get("rentals", {}).get("localities", []))
    return {"city": city, "areas": sorted(values, key=str.casefold)}


@app.get("/api/v1/dashboard", tags=["analysis"])
def dashboard(
    city: str = Query(default="Mumbai", min_length=2, max_length=90),
    area: str | None = Query(default=None, max_length=120),
    session: Session = Depends(get_session),
    data: dict = Depends(get_data),
):
    return {
        "analysis": data,
        "selected_city": city,
        "selected_area": area,
        "market_context": market_context(data, city),
        "uploaded_signals": summarize_signals(session, city, area),
        "study_count": session.scalar(select(func.count(Study.id))) or 0,
        "dataset_count": session.scalar(select(func.count(SignalBatch.id))) or 0,
    }


@app.get("/api/v1/signals", tags=["analysis"])
def signals(
    city: str = Query(min_length=2, max_length=90),
    area: str | None = Query(default=None, max_length=120),
    radius_km: float | None = Query(default=None, gt=0, le=50),
    latitude: float | None = Query(default=None, ge=-90, le=90),
    longitude: float | None = Query(default=None, ge=-180, le=180),
    session: Session = Depends(get_session),
):
    if (latitude is None) != (longitude is None):
        raise HTTPException(status_code=422, detail="Latitude and longitude must be provided together")
    return summarize_signals(session, city, area, radius_km, latitude, longitude)


@app.get("/api/v1/data-sources", tags=["datasets"])
def data_sources(session: Session = Depends(get_session), data: dict = Depends(get_data)):
    uploaded = list(session.scalars(select(SignalBatch).order_by(SignalBatch.imported_at.desc())))
    return {"kaggle_sources": data.get("sources", []), "uploaded_sources": [as_public_batch(item) for item in uploaded]}


@app.post("/api/v1/datasets/import", status_code=status.HTTP_201_CREATED, response_model=DatasetImportResult, tags=["datasets"])
async def import_dataset(
    category: str = Form(...),
    name: str = Form(..., min_length=2, max_length=120),
    source: str = Form(default="User supplied CSV", max_length=240),
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
):
    category = category.strip().casefold()
    if category not in ALLOWED_CATEGORIES:
        raise HTTPException(status_code=422, detail=f"Choose a category from: {', '.join(sorted(ALLOWED_CATEGORIES))}")
    filename = Path(file.filename or "upload.csv").name[:180]
    if not filename.casefold().endswith(".csv"):
        raise HTTPException(status_code=415, detail="Upload a CSV file. Raw uploads are not retained after import.")
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    await file.close()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"The CSV exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB upload limit.")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=415, detail="The CSV must use UTF-8 encoding.") from exc

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise HTTPException(status_code=422, detail="The CSV needs a header row.")
    normalized_headers = {_normalise_header(header) for header in reader.fieldnames}
    if not normalized_headers.intersection({"city", "market", "cityname"}):
        raise HTTPException(status_code=422, detail="Add a city column. Supported fields are listed in the Data & methods section.")

    observations: list[Observation] = []
    errors: list[str] = []
    for line_number, row in enumerate(reader, start=2):
        if line_number - 1 > MAX_UPLOAD_ROWS:
            raise HTTPException(status_code=413, detail=f"The CSV exceeds the {MAX_UPLOAD_ROWS:,} row limit.")
        city = _value(row, "city", "market", "city_name")
        if not city:
            errors.append(f"Line {line_number}: city is required")
            continue
        area = _value(row, "area", "locality", "neighborhood", "neighbourhood", "district") or "Citywide"
        metric = _value(row, "metric", "measure", "field", "signal") or category
        raw_value = _value(row, "value", "count", "amount", "visitors", "vehicles", "speed", "available_spaces", "salary", "rent", "cost")
        if not raw_value and category in {"competition", "transit"}:
            raw_value = "1"
        if not raw_value:
            errors.append(f"Line {line_number}: add a numeric value/count column")
            continue
        try:
            numeric_value = _optional_float(raw_value, "value", line_number)
            if numeric_value is not None and numeric_value < 0:
                raise ValueError(f"Line {line_number}: value cannot be negative")
            latitude = _optional_float(_value(row, "latitude", "lat"), "latitude", line_number)
            longitude = _optional_float(_value(row, "longitude", "lon", "lng"), "longitude", line_number)
            observed_at = _parse_datetime(_value(row, "observed_at", "timestamp", "datetime", "date"), line_number)
            if (latitude is None) != (longitude is None):
                raise ValueError(f"Line {line_number}: latitude and longitude must appear together")
            if latitude is not None and not -90 <= latitude <= 90:
                raise ValueError(f"Line {line_number}: latitude must be between -90 and 90")
            if longitude is not None and not -180 <= longitude <= 180:
                raise ValueError(f"Line {line_number}: longitude must be between -180 and 180")
        except ValueError as exc:
            errors.append(str(exc))
            continue
        detail = _value(row, "business_type", "category_label", "mode", "kind", "type", "competitor_type")[:100] or None
        observations.append(Observation(
            city=city[:90], area=area[:120], category=category, metric=metric[:80], value=numeric_value,
            unit=(_value(row, "unit", "units", "measurement_unit") or "count")[:40],
            name=(_value(row, "name", "business_name", "station_name", "label") or "")[:180] or None,
            detail=detail, observed_at=observed_at, latitude=latitude, longitude=longitude,
            metadata_json="{}",
        ))

    if not observations:
        raise HTTPException(status_code=422, detail={"message": "No usable rows found.", "errors": errors[:10]})

    batch = SignalBatch(name=name.strip(), category=category, filename=filename, source=source.strip() or "User supplied CSV", row_count=len(observations))
    session.add(batch)
    session.flush()
    for observation in observations:
        observation.batch_id = batch.id
    session.add_all(observations)
    session.commit()
    session.refresh(batch)
    warnings = [f"{len(errors):,} invalid row(s) were skipped; first issue: {errors[0]}."] if errors else []
    warnings.append("Imported CSV rows are normalized into the local database; the original file is not retained.")
    return DatasetImportResult(id=batch.id, name=batch.name, category=batch.category, row_count=batch.row_count, imported_at=batch.imported_at.isoformat() + "Z", warnings=warnings)


@app.delete("/api/v1/data-sources/{batch_id}", tags=["datasets"])
def delete_dataset(batch_id: int, session: Session = Depends(get_session)):
    batch = session.get(SignalBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Dataset import not found")
    name = batch.name
    session.delete(batch)
    session.commit()
    return {"deleted": True, "name": name}


@app.get("/api/v1/studies", tags=["studies"])
def list_studies(session: Session = Depends(get_session)):
    studies = list(session.scalars(select(Study).order_by(Study.updated_at.desc())))
    return {"studies": [serialize_study(session, study) for study in studies]}


@app.post("/api/v1/studies", status_code=status.HTTP_201_CREATED, tags=["studies"])
def create_study(body: StudyCreate, session: Session = Depends(get_session), data: dict = Depends(get_data)):
    study = Study(
        name=body.name.strip(), city=body.city.strip(), area=body.area.strip() if body.area else None,
        business_type=body.business_type.strip(), radius_km=body.radius_km, latitude=body.latitude,
        longitude=body.longitude, assumptions_json=json.dumps(body.assumptions), weights_json=json.dumps(body.weights),
    )
    session.add(study)
    session.flush()
    study.results_json = json.dumps(score_study(session, study, data), ensure_ascii=False)
    session.commit()
    session.refresh(study)
    return serialize_study(session, study)


@app.get("/api/v1/studies/{study_id}", tags=["studies"])
def get_study(study_id: int, session: Session = Depends(get_session)):
    study = session.get(Study, study_id)
    if study is None:
        raise HTTPException(status_code=404, detail="Study not found")
    return serialize_study(session, study)


@app.put("/api/v1/studies/{study_id}", tags=["studies"])
def update_study(study_id: int, body: StudyUpdate, session: Session = Depends(get_session), data: dict = Depends(get_data)):
    study = session.get(Study, study_id)
    if study is None:
        raise HTTPException(status_code=404, detail="Study not found")
    updates = body.model_dump(exclude_unset=True)
    if "name" in updates:
        study.name = updates["name"].strip()
    if "assumptions" in updates:
        study.assumptions_json = json.dumps(updates["assumptions"] or {})
    if "weights" in updates:
        study.weights_json = json.dumps(updates["weights"] or {})
    study.updated_at = utc_now()
    study.results_json = json.dumps(score_study(session, study, data), ensure_ascii=False)
    session.commit()
    session.refresh(study)
    return serialize_study(session, study)


@app.delete("/api/v1/studies/{study_id}", tags=["studies"])
def delete_study(study_id: int, session: Session = Depends(get_session)):
    study = session.get(Study, study_id)
    if study is None:
        raise HTTPException(status_code=404, detail="Study not found")
    name = study.name
    session.delete(study)
    session.commit()
    return {"deleted": True, "name": name}


@app.post("/api/v1/studies/compare", tags=["studies"])
def compare_studies(body: StudyComparison, session: Session = Depends(get_session)):
    if len(set(body.study_ids)) != len(body.study_ids):
        raise HTTPException(status_code=422, detail="Study IDs must be unique")
    studies = list(session.scalars(select(Study).where(Study.id.in_(body.study_ids))))
    by_id = {study.id: study for study in studies}
    missing = [study_id for study_id in body.study_ids if study_id not in by_id]
    if missing:
        raise HTTPException(status_code=404, detail={"missing_study_ids": missing})
    result = []
    for study_id in body.study_ids:
        study = by_id[study_id]
        output = serialize_study(session, study)
        output["results"] = score_study(session, study, load_analysis())
        result.append(output)
    return {"studies": result, "factor_keys": list(dict.fromkeys(key for study in result for key in study["results"]["factor_scores"]))}


@app.get("/api/v1/coverage", tags=["analysis"])
def coverage(session: Session = Depends(get_session), data: dict = Depends(get_data)):
    categories = {row[0] for row in session.execute(select(SignalBatch.category).distinct())}
    items = []
    for entry in data.get("coverageGaps", []):
        match = {
            "Footfall": "footfall", "Business competition": "competition", "Road traffic": "traffic", "Parking availability": "parking",
            "Commercial rent": "rent", "Local transit access": "transit", "Operating costs and profit": None,
        }.get(entry["name"])
        if match and match in categories:
            items.append({**entry, "status": "Imported data"})
        else:
            items.append(entry)
    if "worker_supply" in categories:
        items.append({"name": "Worker availability", "status": "Imported data", "need": "Validate the uploaded worker source, coverage, and collection date"})
    if "salary" in categories:
        items.append({"name": "Local wage signals", "status": "Imported data", "need": "Confirm role match and salary units before comparing"})
    if "cost_of_living" in categories:
        items.append({"name": "Cost of living", "status": "Imported data", "need": "Check local coverage, period, and household/business relevance"})
    return {"items": items, "imported_categories": sorted(categories)}


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(ROOT / "index.html")


@app.get("/app.js", include_in_schema=False)
def frontend_script():
    return FileResponse(ROOT / "app.js", media_type="text/javascript")


@app.get("/styles.css", include_in_schema=False)
def frontend_styles():
    return FileResponse(ROOT / "styles.css", media_type="text/css")


@app.get("/favicon.svg", include_in_schema=False)
def favicon():
    return FileResponse(ROOT / "favicon.svg", media_type="image/svg+xml")


@app.get("/data/import-template.csv", include_in_schema=False)
def import_template():
    return FileResponse(ROOT / "data" / "import-template.csv", media_type="text/csv", filename="spotwise-import-template.csv")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.main:app", host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8000")), reload=True)
