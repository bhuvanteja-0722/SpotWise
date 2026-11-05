# SpotWise

SpotWise is a big data analysis project for researching where to open a local business. It processes 190,340 records from rental listings, job-market postings, and rail timetable data into a dark-mode interactive dashboard, then lets a user model their own break-even scenario.

## What the website includes

- Mumbai neighborhood asking-rent comparison, including median monthly asking rent and rent per square foot where area is usable.
- City-level job-posting counts, disclosed annual salary patterns, role groups, and recurring skill tags.
- Historical city-level railway station and train schedule coverage.
- A break-even calculator using editable rent, payroll, other fixed costs, average customer spend, gross margin, and customer-volume assumptions.
- Dataset coverage, age, licensing, and limitations alongside the analysis.
- Dark mode by default, with an optional light theme.
- JSON snapshot export for the selected market and business scenario.

## Data sources used

| Data | Kaggle source | Size / rows | License and use |
| --- | --- | --- | --- |
| Mumbai rental listings | [Property Rental Listings Dataset](https://www.kaggle.com/datasets/harshalhonde/property-rental-listings-dataset) | 23,405 listings; 6.6 MB CSV | CC BY 4.0. Attribution: Harshal H. The dataset describes listings scraped from Magicbricks and was last updated in August 2023. |
| India job postings | [Indian Job Market Dataset 2025](https://www.kaggle.com/datasets/shivamshrivastava21/indian-job-market-dataset-2025-2026) | 97,929 postings; 31.7 MB XLSX | CC BY-NC-SA 4.0. The dataset is a 2025 snapshot. Use and redistribute adapted data only under its non-commercial and share-alike terms, with attribution. |
| Indian rail timetable | [Indian Railways Time Table for Trains Available](https://www.kaggle.com/datasets/harsh16/indian-railways-time-table-for-trains-available) | 69,006 schedule rows; 8.1 MB CSV | Kaggle labels it “Database: Open Database, Contents: Database Contents.” The file is an older timetable snapshot (2015). Check the source’s full license text before reuse or redistribution. |

The raw files are not stored in this repository. `data/analysis.json` contains compact, attributed aggregates built from the three files; the data retains the applicable source terms. Project code and source data have different license terms.

## Data limits

These datasets do not provide anonymized pedestrian footfall, current traffic, parking occupancy, a current geocoded business/competitor registry, or reliable neighborhood-level workforce supply. The job market file measures postings, not the number of available workers. The rail timetable is historical and only supports broad city-level station-name matching. Rental records are Mumbai-only asking prices for residential property, not commercial leases; locality names are inferred from listing titles. These are directional research signals, not current quotes or a complete feasibility score.

The planner does not predict actual profit. It calculates a user-defined scenario:

```text
monthly fixed costs = rent + staff count × monthly pay per staff + other fixed costs
monthly sales = average customer spend × expected customers per day × 30
estimated operating surplus = monthly sales × gross margin − monthly fixed costs
break-even customers per day = monthly fixed costs ÷ (average spend × gross margin × 30)
```

It excludes taxes, financing, owner pay, setup costs, seasonality, and other variable costs. Customer volume and most operating assumptions must be supplied by the user.

## Rebuild the derived analysis

Python 3.10+ is sufficient for the aggregation script; it uses the standard library. To download the public Kaggle files, install Kaggle’s CLI and configure your own Kaggle API credentials. No private API key is required by the website itself.

```bash
python -m pip install kaggle
mkdir -p data/raw/jobs data/raw/rentals data/raw/rail
kaggle datasets download -d shivamshrivastava21/indian-job-market-dataset-2025-2026 -p data/raw/jobs --unzip
kaggle datasets download -d harshalhonde/property-rental-listings-dataset -p data/raw/rentals --unzip
kaggle datasets download -d harsh16/indian-railways-time-table-for-trains-available -p data/raw/rail --unzip
python scripts/build_analysis.py \
  --jobs data/raw/jobs/indian-job-market-dataset-2025.xlsx \
  --rentals data/raw/rentals/Mumbai_property_details.csv \
  --rail data/raw/rail/isl_wise_train_detail_03082015_v1.csv \
  --out data/analysis.json
```

The script streams the Excel rows and CSV records, cleans rent and salary fields, infers city/locality labels, aggregates roles and skill tags, and emits the compact dashboard file. Re-running it replaces the current aggregates with the selected source files.

## Run the website

Because the dashboard fetches `data/analysis.json`, serve the project through a local HTTP server instead of opening `index.html` directly:

```bash
python -m http.server 8000
```

Then open [http://localhost:8000](http://localhost:8000).

## Big data analysis work demonstrated

- Multi-source data integration and schema cleaning.
- Streaming ingestion of an XLSX workbook with nearly 98K rows and CSV files, without loading the full records into the browser.
- City/locality parsing, robust median summaries, role classification, token frequency counts, and schedule deduplication.
- Aggregated data delivery to a browser dashboard instead of shipping the raw multi-source datasets to users.
- A transparent what-if cost model for break-even planning.
