# SpotWise

SpotWise is a full-stack big data analysis project for researching where to open a local business. A responsive dark-mode website calls a FastAPI backend, reads the prepared Kaggle analysis, stores user studies and uploaded local signals in SQLite, and calculates transparent break-even and location-readiness scenarios.

## What it does

- Compares Mumbai residential rental listings by inferred locality, analyzes India-wide job postings, and summarizes city-level rail schedule coverage.
- Filters uploaded local records by city, neighborhood, and—when coordinates are present—straight-line radius.
- Imports user-owned CSV data for footfall, competitor businesses, traffic, parking, transit stops, worker availability, salary, cost of living, and commercial rent.
- Summarizes uploaded records, including average, median, totals, hourly peak, and nearest geocoded transit point.
- Saves, edits, lists, compares, and deletes location studies in a persistent SQLite database.
- Scores factors only when both an uploaded signal and a matching user target exist. A composite readiness score requires at least three scored factors.
- Calculates a user-entered monthly break-even scenario and exports a JSON research snapshot.
- Serves interactive API documentation at `/docs`.

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
| `POST /api/v1/datasets/import` | Normalize and persist a CSV signal dataset |
| `DELETE /api/v1/data-sources/{id}` | Remove an uploaded batch and its imported records |
| `GET/POST /api/v1/studies` | List or save a location study |
| `GET/PUT/DELETE /api/v1/studies/{id}` | Retrieve, update assumptions, or remove a study |
| `POST /api/v1/studies/compare` | Compare two to four saved studies |
| `GET /api/v1/coverage` | Show data coverage and missing signals |

The backend uses FastAPI with SQLAlchemy and SQLite by default. FastAPI documents SQLite as a simple local option and SQLAlchemy supports switching to other database engines; see the [FastAPI SQL database guide](https://fastapi.tiangolo.com/tutorial/sql-databases/) and [SQLAlchemy ORM quick start](https://docs.sqlalchemy.org/en/21/orm/quickstart.html).

## Import your own local datasets

Use the Data & methods section in the website, or send a multipart request to `POST /api/v1/datasets/import`. Select a signal category and include a CSV. The application stores normalized rows in SQLite and does not retain the original uploaded file. Current limits are 20 MB and 100,000 rows per upload.

Download the [CSV template](data/import-template.csv). Required columns are `city` and numeric `value`. Optional columns are:

```text
area, observed_at, metric, unit, name, category_label, latitude, longitude
```

`observed_at` should be an ISO date or datetime. Coordinates must be WGS84 latitude/longitude decimals. Coordinates enable straight-line radius filtering and nearest-stop calculations; they do not represent walking distance or travel time.

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

## Scope and deployment notes

The static Kaggle sources do not contain live footfall, current competitor locations, traffic, parking occupancy, current metro/bus access, or neighborhood-level workforce availability. Import appropriately licensed datasets to analyze those dimensions; external maps/mobility API credentials are not required for local operation. No API key is bundled.

This repository is set up for local development and a single-user academic project. It does not include user authentication, account isolation, deployment secrets, managed backups, or production migrations. Do not expose the local server to a public network with private datasets. Before a public multi-user deployment, add authentication, authorization, a production database, backup/retention policy, and review each source’s redistribution rights.
