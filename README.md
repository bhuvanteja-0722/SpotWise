# SpotWise

SpotWise is a full-stack big data analysis project for researching where to open a local business. A responsive dark-mode website calls a FastAPI backend, reads the prepared Kaggle analysis, stores user studies and uploaded local signals in SQLite, and calculates transparent break-even and location-readiness scenarios.

## What it does

- Compares Mumbai residential rental listings by inferred locality, analyzes India-wide job postings, and summarizes city-level rail schedule coverage.
- Filters uploaded local records by city, neighborhood, and—when coordinates are present—straight-line radius.
- Imports CSV, Excel (.xlsx), JSON, JSON Lines, or Parquet data for footfall, competitor businesses, traffic, parking, transit stops, worker availability, salary, cost of living, and commercial rent.
- Summarizes uploaded records, including average, median, totals, hourly peak, and nearest geocoded transit point.
- Saves, edits, lists, compares, and deletes location studies in a persistent SQLite database.
- Scores factors only when both an uploaded signal and a matching user target exist. A composite readiness score requires at least three scored factors.
- Calculates a user-entered monthly break-even scenario and exports a JSON research snapshot.
- Serves interactive API documentation at `/docs`.

## Interface and frontend design

SpotWise uses a dark, low-glare workspace with a lime accent, muted green surfaces, and readable type. The layout groups the work into a clear path: select a market and business, review local context, check workforce and transport signals, model break-even, then save or compare studies. Cards, data-source badges, freshness notes, and empty states help distinguish observed data from user assumptions. The interface adapts to desktop and mobile widths, supports an optional light theme, and includes visible keyboard focus and reduced-motion support.

The frontend is plain HTML, CSS, and JavaScript served by FastAPI; it has no Node build step. I kept the small locality/rent visualizations in CSS and native browser code because a chart framework would add more code and download weight than it would remove for these simple displays. The existing backend libraries handle the heavier work: FastAPI for routes, SQLAlchemy for persistence, scikit-learn/joblib for the fitted model, and PyArrow for Parquet. `requirements.txt` lists the Python packages.

## Run the full website and backend

Requires Python 3.10 or later.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
python3 run.py
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). The API is at `/api/v1`; interactive API docs are at [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs). The default database file is `data/spotwise.db` and is ignored by Git. The website and API run on the same origin, so no browser API key or CORS setup is needed.

Or run the app in a container with `docker compose up --build`; the `data` directory is mounted so the SQLite database persists across container restarts.

For a different database, set `DATABASE_URL`. SQLite is the default for a single-user class project; SQLAlchemy can connect to PostgreSQL when its database driver is installed and configured.

## Backend API

| Endpoint | Purpose |
| --- | --- |
| `GET /api/v1/health` | Backend, database, and analysis-data status |
| `GET /api/v1/markets` | Available markets and business types |
| `GET /api/v1/areas?city=Mumbai` | Rental neighborhoods plus uploaded areas |
| `GET /api/v1/dashboard?city=Mumbai&area=Powai` | Kaggle aggregates, market context, and matching local signals |
| `GET /api/v1/signals?city=Mumbai&area=Powai&radius_km=1&latitude=19.1&longitude=72.8` | Local imported signal aggregates and radius filtering |
| `GET /api/v1/data-sources` | Kaggle sources and locally imported batches |
| `POST /api/v1/datasets/preview` | Scan and preview CSV, XLSX, JSON, JSONL/NDJSON, or Parquet data |
| `POST /api/v1/datasets/import` | Clean, normalize, and persist a local signal dataset |
| `POST /api/v1/workforce/salary-estimate` | Estimate a role salary from the trained posting benchmark |
| `DELETE /api/v1/data-sources/{id}` | Remove an uploaded batch and its imported records |
| `GET/POST /api/v1/studies` | List or save a location study |
| `GET/PUT/DELETE /api/v1/studies/{id}` | Retrieve, update assumptions, or remove a study |
| `POST /api/v1/studies/compare` | Compare two to four saved studies |
| `GET /api/v1/coverage` | Show data coverage and missing signals |

The backend uses FastAPI with SQLAlchemy and SQLite by default. FastAPI documents SQLite as a simple local option and SQLAlchemy supports switching to other database engines; see the [FastAPI SQL database guide](https://fastapi.tiangolo.com/tutorial/sql-databases/) and [SQLAlchemy ORM quick start](https://docs.sqlalchemy.org/en/21/orm/quickstart.html).

## Import your own local datasets

Use the Data & methods section in the website, or send a multipart request to `POST /api/v1/datasets/import`. Select a signal category and upload CSV, Excel (.xlsx, first worksheet), JSON (array of objects or a `data`/`records`/`items`/`results` array), JSON Lines/NDJSON, or Parquet. Before importing, the website scans the selected format and previews sample rows, the usable/invalid/duplicate counts, coordinate and timestamp coverage, and common input issues. The backend rechecks every row, skips invalid rows, can remove exact duplicates, and can normalize repeated whitespace in city/area names. The application stores normalized rows in SQLite and does not retain the original uploaded file. Current limits are 20 MB and 100,000 rows per upload.

The neighborhood picker only suggests areas backed by data for the currently selected city. The included preset locality list is Mumbai-only; other cities show no preset areas until local signals or saved study areas exist. You can still type a neighborhood manually, but area-specific metrics remain empty until matching local evidence is imported.

Download the [CSV template](data/import-template.csv). Required columns are `city` and numeric `value`. Optional columns are:

```text
area, observed_at, metric, unit, name, category_label, latitude, longitude
```

`observed_at` should be an ISO date or datetime. Coordinates must be WGS84 latitude/longitude decimals. Coordinates enable straight-line radius filtering and nearest-stop calculations; they do not represent walking distance or travel time.

The upload scan checks UTF-8 CSVs (comma, semicolon, tab, or pipe separators), XLSX, JSON, JSONL/NDJSON, and Parquet. It maps common header aliases, validates numeric values, date/time strings, and coordinate pairs, and reports exact duplicate records. It does not silently remove unusual high/low values: review the reported range and source units before importing. Exact-row de-duplication is optional because repeated measurements can be valid when the timestamp or location differs.

Recommended metric labels:

| Import category | Example metric | Example unit |
| --- | --- | --- |
| Footfall | `daily_visitors` | `people/day` |
| Competition | `competitor_count` | `business` (one row per business) |
| Traffic | `average_speed_kmh` or `vehicles_per_hour` | `km/h` or `vehicles/hour` |
| Parking | `available_spaces` | `spaces` |
| Transit | `transit_stop` | `stop` (add coordinates) |
| Worker availability | `available_workers` | `workers` |
| Salary | `monthly_salary` | `INR/month` |
| Cost of living | `monthly_cost` | `INR/month` |
| Commercial rent | `commercial_rent` | `INR/month` |

Label rows consistently. Salary and cost targets must use the same units as their uploaded data. Each imported batch displays its source/notes and removal control in the website.

## Readiness score and business scenario

The readiness score is a transparent comparison heuristic, not a probability of success. A factor is scored only when both its uploaded records and a corresponding target are present. By default, observed factors have equal weight; scores are omitted until at least three factors can be evaluated.

Supported target assumptions include monthly rent vs budget, minimum footfall, maximum competitor count, maximum distance to transit, minimum road speed, minimum parking spaces, required workers, maximum monthly wage, and maximum local cost. For traffic, SpotWise only scores a speed metric; raw vehicle counts are shown as context because higher volume can mean either customer access or congestion.

The break-even calculator uses:

```text
monthly fixed costs = rent + staff count × monthly pay per staff + other fixed costs
monthly sales = average customer spend × expected customers per day × 30
estimated operating surplus = monthly sales × gross margin − monthly fixed costs
break-even customers per day = monthly fixed costs ÷ (average spend × gross margin × 30)
```

It excludes taxes, financing, owner pay, setup costs, seasonality, and other variable costs. Customer volume, rent, pay, margin, and most operational inputs must be provided by the user.

## Kaggle analysis sources

| Data | Kaggle source | Rows | License and scope |
| --- | --- | --- | --- |
| Mumbai rental listings | [Property Rental Listings Dataset](https://www.kaggle.com/datasets/harshalhonde/property-rental-listings-dataset) | 23,405 | CC BY 4.0. Attribution: Harshal H. Residential listings scraped from Magicbricks; snapshot last updated August 2023. Not commercial lease data. |
| India job postings | [Indian Job Market Dataset 2025](https://www.kaggle.com/datasets/shivamshrivastava21/indian-job-market-dataset-2025-2026) | 97,929 | CC BY-NC-SA 4.0. Non-commercial and share-alike terms apply to adapted data; 2025 snapshot. Postings are not a count of available workers. |
| Indian rail timetable | [Indian Railways Time Table for Trains Available](https://www.kaggle.com/datasets/harsh16/indian-railways-time-table-for-trains-available) | 69,006 | Kaggle labels the database as Open Database / Database Contents. Historical 2015 timetable; city matches are inferred from station names. Verify source license text for reuse. |

Prepared aggregates are included in `data/analysis.json`; raw source archives are excluded from Git. Rebuild the analysis with the standard-library pipeline:

```bash
python3 -m pip install kaggle
kaggle datasets download -d shivamshrivastava21/indian-job-market-dataset-2025-2026 -p data/raw/jobs --unzip
kaggle datasets download -d harshalhonde/property-rental-listings-dataset -p data/raw/rentals --unzip
kaggle datasets download -d harsh16/indian-railways-time-table-for-trains-available -p data/raw/rail --unzip
python3 scripts/build_analysis.py \
  --jobs data/raw/jobs/indian-job-market-dataset-2025.xlsx \
  --rentals data/raw/rentals/Mumbai_property_details.csv \
  --rail data/raw/rail/isl_wise_train_detail_03082015_v1.csv \
  --out data/analysis.json
```

## Trained salary benchmark

The repository includes a fitted scikit-learn Ridge pipeline at `models/salary_estimator.joblib`, trained from the included Kaggle job posting source. It predicts an annual salary midpoint from job title, skills, broad market, role group, and experience; the website exposes it as a monthly estimate and empirical holdout range. The source has 97,929 rows, of which 33,203 disclosed INR salary rows passed cleaning. Evaluation uses an 80/20 holdout grouped by exact normalized job title: MAE ₹274,469/year, median absolute error ₹102,409/year, R² 0.523, and median absolute percentage error 23.55%. See `models/salary_estimator_metrics.json` for split details and limitations. This is a posting benchmark, not available-worker counts or a local wage survey.

The source is CC BY-NC-SA 4.0. Non-commercial and share-alike terms apply to adapted data and the derived model. Keep this project academic/non-commercial unless you confirm rights for your intended use. Raw job data is excluded from Git. To retrain after obtaining the source file:

```bash
python3 scripts/train_salary_model.py --jobs data/raw/jobs/indian-job-market-dataset-2025.xlsx
```

The script writes a fitted model and its evaluation report under `models/`.

## Scope and deployment notes

The static Kaggle sources do not contain live footfall, current competitor locations, traffic, parking occupancy, current metro/bus access, or neighborhood-level workforce availability. Import appropriately licensed datasets to analyze those dimensions; external maps/mobility API credentials are not required for local operation. No API key is bundled.

## Recommended dataset expansion

These are candidate sources to inspect and preprocess; they are not presented as verified current truth. Check the dataset card, license, collection date, source terms, geographic precision, and redistribution rights before downloading or publishing derived data.

| Priority | Source | Useful fields | Fit and limitation |
| --- | --- | --- | --- |
| 1 | [Indian Business Listings Dataset](https://www.kaggle.com/datasets/japnoor69/indian-business-listings-dataset) | Business name, category, city, address, created date | Potential competitor counts across Indian cities. The data card notes duplicates and missing contact fields; verify coordinate availability and freshness before using for neighborhood scores. |
| 2 | [Swiggy restaurants dataset](https://www.kaggle.com/datasets/rrkcoder/swiggy-restaurants-dataset) | Restaurant category, area, price, ratings, location fields | A larger restaurant-sector competitor/price snapshot (reported around 150k listings). It is scraped and limited to one platform; validate its license and service terms, and never treat it as total market share. |
| 3 | [India PCA / socio-economic data](https://www.kaggle.com/datasets/webaccess/india-pca-data) | Population, housing, education and demographic measures | Adds demographic context but is based on 2001/2011 census data, district-level, and its Kaggle card lists no license. Keep it historical/context-only unless rights and geography are verified. |
| 4 | [Foot Traffic Würzburg retail forecasting data](https://www.kaggle.com/competitions/foot-traffic-wuerzburg-retail-forecasting-2-0/data) | Hourly pedestrian counters, weather, holidays and events | Excellent for demonstrating time-series/peak-hour preprocessing, but it is a German city and must never be joined into Indian location scores. Review competition rules before reuse. |
| Mobility | [Delhi Open Transit Data](https://otd.delhi.gov.in/documentation/) · [Chennai CUMTA open data](https://opendata.cumta.org/) | GTFS stops, routes, schedules | Better city transit evidence than the historical 2015 rail snapshot. Feed availability and real-time access differ by city; Delhi documents that its published bus schedule times are rough estimates. |

For a credible location score, prioritize recent, geocoded, local commercial listings/leases; a category-tagged POI/business dataset; official GTFS feeds; and licensed anonymized footfall or road-speed data. A large dataset is not automatically a better dataset: stale, synthetic, platform-scraped, coarse-geography, or mismatched-city rows can bias the result. The importer supports CSV, XLSX, JSON, JSONL/NDJSON, and Parquet signals; use the template and upload scan before importing new sources.

### API recommendation

APIs would improve specific missing signals, but require source-specific integration and usage limits. Google Places is a practical paid option for nearby competitors/POIs and coordinates; Google Maps Platform publishes India-specific pricing and free monthly thresholds, and nearby search can become billable. For transit, use official city GTFS feeds first. OpenStreetMap can add open POI/road context, but do not depend on the community Overpass endpoint for large repeated production queries; use a licensed regional extract or host the data yourself. Footfall is the hardest gap: it generally needs licensed mobility data or a local authority/field-count dataset, not a normal maps API. Keep API keys server-side in environment variables and cache permitted results. See [Google India pricing](https://developers.google.com/maps/billing-and-pricing/india), [Delhi GTFS docs](https://otd.delhi.gov.in/documentation/), and the [OpenStreetMap service policies](https://operations.osmfoundation.org/policies/).

This repository is set up for local development and a single-user academic project. It does not include user authentication, account isolation, deployment secrets, managed backups, or production migrations. Do not expose the local server to a public network with private datasets. Before a public multi-user deployment, add authentication, authorization, a production database, backup/retention policy, and review each source’s redistribution rights.
