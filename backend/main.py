from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import delete, event, func, select
from sqlalchemy.orm import Session

from .analytics import break_even, load_analysis, market_context, score_study, serialize_study, summarize_signals
from .dataset_import import normalize_rows, read_rows
from .db import Base, ROOT, engine, get_session
from .models import Observation, SignalBatch, Study, utc_now
from .place_category_model import classify_place_names
from .salary_model import estimate_salary
from .schemas import DatasetImportResult, DatasetPreview, PlaceCategoryRequest, SalaryEstimateRequest, StudyComparison, StudyCreate, StudyUpdate
from .transit_sources import sources_for_city

MAX_UPLOAD_BYTES = int(os.getenv("SPOTWISE_MAX_UPLOAD_MB", "50")) * 1024 * 1024
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


@app.get("/api/v1/health", tags=["system"])
def health(session: Session = Depends(get_session)):
    session.execute(select(func.count(Study.id)))
    return {"status": "ok", "database": engine.dialect.name, "analysis_file": load_analysis() is not None}


@app.get("/api/v1/markets", tags=["analysis"])
def markets(session: Session = Depends(get_session), data: dict = Depends(get_data)):
    default_businesses = [
        "Cafe / restaurant", "Retail shop", "Bakery / dessert shop", "Grocery / convenience store",
        "Clothing / fashion retail", "Pharmacy", "Salon / wellness", "Fitness studio",
        "Healthcare clinic", "Tutoring / education center", "Electronics shop",
        "Home goods / furniture", "Pet care / veterinary", "Coworking space",
        "Hotel / guesthouse", "Repair / local service", "Other local service",
    ]
    uploaded_markets = set(session.scalars(select(Observation.city).distinct()))
    imported_businesses = sorted({
        value.strip() for value in session.scalars(
            select(Observation.detail).where(Observation.category == "competition", Observation.detail.is_not(None))
        ) if value and value.strip()
    }, key=str.casefold)
    jobs = data.get("jobs", {}).get("cities", [])
    rail = data.get("rail", {}).get("cities", [])
    market_names = {item["name"] for item in jobs} | uploaded_markets
    coverage = []
    for city in sorted(market_names, key=str.casefold):
        signals = []
        if any(item["name"].casefold() == city.casefold() for item in jobs):
            signals.append("city-level jobs")
        if any(item["name"].casefold() == city.casefold() for item in rail):
            signals.append("2015 rail")
        if city.casefold() == "mumbai":
            signals.append("Mumbai-only residential rent")
        local_categories = sorted(set(session.scalars(
            select(Observation.category).where(func.lower(Observation.city) == city.casefold())
        )))
        if local_categories:
            signals.append(f"{len(local_categories)} imported local signal(s)")
        coverage.append({"city": city, "signals": signals or ["user-uploaded data only"]})
    return {
        "markets": sorted(market_names, key=str.casefold),
        "market_coverage": coverage,
        "business_types": default_businesses + [item for item in imported_businesses if item.casefold() not in {name.casefold() for name in default_businesses}],
        "dataset_business_types": imported_businesses,
    }


@app.get("/api/v1/areas", tags=["analysis"])
def areas(city: str = Query(min_length=2, max_length=90), session: Session = Depends(get_session), data: dict = Depends(get_data)):
    values = set(session.scalars(select(Observation.area).where(func.lower(Observation.city) == city.casefold(), Observation.area != "Citywide")))
    values.update(session.scalars(select(Study.area).where(func.lower(Study.city) == city.casefold(), Study.area.is_not(None))))
    if city.casefold() == "mumbai":
        values.update(item["name"] for item in data.get("rentals", {}).get("localities", []))
    return {"city": city, "areas": sorted(values, key=str.casefold)}


@app.post("/api/v1/workforce/salary-estimate", tags=["workforce"])
def workforce_salary_estimate(body: SalaryEstimateRequest):
    try:
        return estimate_salary(body.city, body.job_title, body.skills, body.experience_years)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail="Salary model has not been trained. See the README training instructions.") from exc


@app.post("/api/v1/business/classify", tags=["business intelligence"])
def business_category_suggestions(body: PlaceCategoryRequest):
    try:
        return classify_place_names([name.strip() for name in body.names], body.city)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail="Place category model is not available. See the README model training instructions.") from exc


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
        "transit_sources": sources_for_city(city),
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


def _read_and_clean_upload(category: str, filename: str, content: bytes, skip_duplicates: bool, normalize_labels: bool, dataset_city: str | None = None) -> dict:
    try:
        if Path(filename).suffix.casefold() == ".zip" and category != "transit":
            raise ValueError("Static GTFS ZIP files can only be imported as Public transport signals.")
        rows = read_rows(filename, content, MAX_UPLOAD_ROWS, dataset_city)
        return normalize_rows(rows, category, skip_duplicates, normalize_labels, MAX_UPLOAD_ROWS)
    except OverflowError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=415, detail="Text files must use UTF-8 encoding.") from exc
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(status_code=422, detail=f"Could not read this file: {exc}") from exc


def _validate_import_category(category: str) -> str:
    category = category.strip().casefold()
    if category not in ALLOWED_CATEGORIES:
        raise HTTPException(status_code=422, detail=f"Choose a category from: {', '.join(sorted(ALLOWED_CATEGORIES))}")
    return category


async def _read_upload_file(file: UploadFile) -> tuple[str, bytes]:
    filename = Path(file.filename or "upload.csv").name[:180]
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    await file.close()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"The file exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB upload limit.")
    if not Path(filename).suffix:
        raise HTTPException(status_code=415, detail="Include a supported file extension: CSV, XLSX, JSON, JSONL, or Parquet.")
    return filename, content


@app.post("/api/v1/datasets/preview", response_model=DatasetPreview, tags=["datasets"])
async def preview_dataset(
    category: str = Form(...),
    dataset_city: str = Form(default="", max_length=90),
    skip_duplicates: bool = Form(default=True),
    normalize_labels: bool = Form(default=True),
    file: UploadFile = File(...),
):
    category = _validate_import_category(category)
    filename, content = await _read_upload_file(file)
    scan = _read_and_clean_upload(category, filename, content, skip_duplicates, normalize_labels, dataset_city)
    return DatasetPreview(
        filename=filename, format=Path(filename).suffix.lstrip(".").upper(),
        **{key: scan[key] for key in ("scanned_rows", "clean_rows", "skipped_invalid_rows", "duplicates_removed", "geocoded_rows", "timestamped_rows", "minimum_value", "median_value", "maximum_value", "errors", "sample_rows")},
    )


@app.post("/api/v1/datasets/import", status_code=status.HTTP_201_CREATED, response_model=DatasetImportResult, tags=["datasets"])
async def import_dataset(
    category: str = Form(...),
    name: str = Form(..., min_length=2, max_length=120),
    source: str = Form(default="User supplied dataset", max_length=240),
    dataset_city: str = Form(default="", max_length=90),
    skip_duplicates: bool = Form(default=True),
    normalize_labels: bool = Form(default=True),
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
):
    category = _validate_import_category(category)
    filename, content = await _read_upload_file(file)
    scan = _read_and_clean_upload(category, filename, content, skip_duplicates, normalize_labels, dataset_city)
    observations = scan["observations"]
    errors = scan["errors"]
    duplicates = scan["duplicates_removed"]
    if not observations:
        raise HTTPException(status_code=422, detail={"message": "No usable rows found.", "errors": errors[:10]})

    batch = SignalBatch(name=name.strip(), category=category, filename=filename, source=source.strip() or "User supplied dataset", row_count=len(observations))
    session.add(batch)
    session.flush()
    for observation in observations:
        observation.batch_id = batch.id
    session.add_all(observations)
    session.commit()
    session.refresh(batch)
    warnings = [f"{len(errors):,} invalid row(s) were skipped; first issue: {errors[0]}."] if errors else []
    if duplicates:
        warnings.append(f"{duplicates:,} exact duplicate row(s) were removed.")
    warnings.append("Rows were validated and normalized into the local database; the original file is not retained.")
    return DatasetImportResult(
        id=batch.id, name=batch.name, category=batch.category, row_count=batch.row_count,
        imported_at=batch.imported_at.isoformat() + "Z", warnings=warnings,
        scanned_rows=scan["scanned_rows"], skipped_invalid_rows=scan["skipped_invalid_rows"], duplicates_removed=duplicates,
        geocoded_rows=scan["geocoded_rows"], timestamped_rows=scan["timestamped_rows"],
        minimum_value=scan["minimum_value"], median_value=scan["median_value"], maximum_value=scan["maximum_value"],
    )


@app.get("/api/v1/transport-sources", tags=["datasets"])
def transport_sources(city: str = Query(min_length=2, max_length=90)):
    return {"city": city, "feeds": sources_for_city(city)}

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
def coverage(city: str = Query(default="Mumbai", min_length=1, max_length=90), session: Session = Depends(get_session), data: dict = Depends(get_data)):
    # A category uploaded for a different city must never make this city's card appear covered.
    source_options = {
        "Commercial rent": ("Commercial property research and commercial lease benchmarks", "https://www.iimb.ac.in/reri/", "Office lease indices can provide broad context; they are not current neighborhood retail asking rents."),
        "Footfall": ("City mobility data portals and licensed pedestrian counts", "https://delhi.transportstack.in/data-services", "Some city feeds expose station or hourly counts with approval. No free, current neighborhood-wide feed was verified for all selected cities."),
        "Business competition": ("Overture Maps Places", "https://docs.overturemaps.org/guides/places/", "Global geocoded place listings, released monthly. Filter and deduplicate; listings are not a complete or verified live competitor census."),
        "Road traffic": ("IUDX and city open-data traffic feeds", "https://catalogue.iudx.org.in/", "Catalogues include city traffic resources; data availability, freshness, and access differ by city."),
        "Parking availability": ("IUDX Smart Parking and city mobility feeds", "https://iudx.org.in/cities/", "Some cities publish parking locations or occupancy. Live availability is city-specific and may require access."),
        "Local transit access": ("City-specific GTFS and mobility feeds", "https://mobilitydatabase.org/feeds?q=india", "Choose the selected city's current feed below; schedule feeds describe published service, not live vehicles."),
        "Operating costs and profit": ("MoSPI Consumer Price Index", "https://cpi.mospi.gov.in/", "Monthly consumer price indices give broad inflation/cost context, not business-specific costs or profit. Scenario inputs remain user assumptions."),
        "Worker availability": ("PLFS and city labor datasets", "https://microdata.gov.in/", "Official labor statistics are useful at broader geographies; local worker availability requires more granular data."),
        "Local wage signals": ("SpotWise job-posting benchmark and official labor statistics", "https://microdata.gov.in/", "Job postings are not verified wages or available workers; imported city and role coverage is required."),
        "Cost of living": ("MoSPI CPI and Household Consumption Expenditure Survey", "https://www.mospi.gov.in/", "Official indicators provide broad household context, not neighborhood-specific business operating costs."),
    }
    category_by_name = {
        "Footfall": "footfall", "Business competition": "competition", "Road traffic": "traffic", "Parking availability": "parking",
        "Commercial rent": "rent", "Local transit access": "transit", "Worker availability": "worker_supply",
        "Local wage signals": "salary", "Cost of living": "cost_of_living",
    }
    entries = list(data.get("coverageGaps", [])) + [
        {"name": "Worker availability", "status": "Missing", "need": "City-specific worker supply data; job postings are not worker counts"},
        {"name": "Local wage signals", "status": "Missing", "need": "Recent local wages matched to the role and monthly/annual units"},
        {"name": "Cost of living", "status": "Missing", "need": "Current local prices; CPI is broad context rather than a neighborhood measure"},
    ]
    items = []
    imported_categories = set()
    for entry in entries:
        name = entry["name"]
        category = category_by_name.get(name)
        records = 0
        area_records = 0
        if category:
            records = session.scalar(select(func.count(Observation.id)).where(
                Observation.category == category, func.lower(Observation.city) == city.strip().lower()
            )) or 0
            area_records = session.scalar(select(func.count(Observation.id)).where(
                Observation.category == category, func.lower(Observation.city) == city.strip().lower(),
                Observation.area != "Citywide"
            )) or 0
        source_name, source_url, source_note = source_options[name]
        if name == "Local transit access":
            city_feeds = sources_for_city(city)
            imported_batches = list(session.scalars(
                select(SignalBatch).join(Observation).where(
                    Observation.category == "transit", func.lower(Observation.city) == city.strip().lower()
                ).distinct().order_by(SignalBatch.imported_at.desc())
            ))
            selected_feed = None
            if imported_batches:
                for batch in imported_batches:
                    selected_feed = next((feed for feed in city_feeds if any(
                        part.casefold() in batch.name.casefold()
                        for part in feed["name"].replace("·", " ").split()
                        if len(part) > 3 and part.casefold() not in {"gtfs", "schedule", "current", "feed", "transit"}
                    )), None)
                    if selected_feed:
                        break
            selected_feed = selected_feed or next((feed for feed in city_feeds if feed["kind"].startswith("GTFS")), city_feeds[0])
            source_name, source_url = selected_feed["name"], selected_feed["url"]
            source_note = f"{selected_feed['freshness']} {selected_feed['provenance']}"
            if imported_batches:
                source_note += f" Imported in SpotWise: {', '.join(batch.name for batch in imported_batches[:2])}."
        status = "Local data" if records else entry.get("status", "Missing")
        if records:
            imported_categories.add(category)
        items.append({
            **entry, "status": status, "source_name": source_name, "source_url": source_url,
            "source_note": source_note, "record_count": records, "area_record_count": area_records,
        })
    return {"city": city, "items": items, "imported_categories": sorted(imported_categories)}


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
