#!/usr/bin/env python3
"""Query a city-sized subset of Overture Places into SpotWise import CSV.

The query reads Overture's public GeoParquet in cloud storage and applies bbox,
category, status, and confidence filters before transferring records locally.
"""
from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RELEASE = "2026-10-21.0"
CITY_BOUNDS = {
    "Ahmedabad": (72.35, 22.85, 72.95, 23.25),
    "Bengaluru": (77.30, 12.70, 77.90, 13.20),
    "Chennai": (79.95, 12.75, 80.45, 13.30),
    "Delhi NCR": (76.80, 28.35, 77.55, 28.95),
    "Hyderabad": (78.20, 17.15, 78.75, 17.65),
    "Kolkata": (88.20, 22.35, 88.65, 22.75),
    "Mumbai": (72.70, 18.80, 73.10, 19.30),
    "Pune": (73.70, 18.35, 74.05, 18.75),
}


def parse_bbox(raw: str) -> tuple[float, float, float, float]:
    try:
        west, south, east, north = (float(part.strip()) for part in raw.split(","))
    except (ValueError, TypeError) as exc:
        raise argparse.ArgumentTypeError("bbox must be west,south,east,north") from exc
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise argparse.ArgumentTypeError("bbox coordinates are invalid or reversed")
    return west, south, east, north


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--city", required=True, choices=CITY_BOUNDS)
    parser.add_argument("--bbox", type=parse_bbox, help="Optional west,south,east,north override")
    parser.add_argument("--release", default=DEFAULT_RELEASE, help="Pinned Overture release, e.g. 2026-10-21.0")
    parser.add_argument("--category", help="Optional exact Overture basic_category, e.g. restaurant")
    parser.add_argument("--minimum-confidence", type=float, default=0.70)
    parser.add_argument("--max-rows", type=int, default=95_000)
    parser.add_argument("--output", type=Path, help="Output CSV (defaults to data/processed/...) ")
    args = parser.parse_args()

    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}\.\d+", args.release):
        parser.error("release must look like 2026-10-21.0")
    if not 0 <= args.minimum_confidence <= 1:
        parser.error("minimum confidence must be between 0 and 1")
    if not 1 <= args.max_rows <= 100_000:
        parser.error("max rows must be between 1 and 100,000")
    if args.category and not re.fullmatch(r"[A-Za-z0-9_ -]{1,80}", args.category):
        parser.error("category may contain letters, digits, spaces, underscores, and hyphens only")

    west, south, east, north = args.bbox or CITY_BOUNDS[args.city]
    output = args.output or ROOT / "data" / "processed" / f"overture_{args.city.casefold().replace(' ', '_')}_{args.release}.csv"
    output.parent.mkdir(parents=True, exist_ok=True)

    try:
        import duckdb
    except ImportError as exc:
        raise SystemExit("Install the project requirements first: pip install -r requirements.txt") from exc

    source = f"s3://overturemaps-us-west-2/release/{args.release}/theme=places/type=place/*"
    query = f"""
        SELECT
            ? AS city,
            COALESCE(NULLIF(addresses[1].locality, ''), 'Citywide') AS area,
            'confidence_weighted_poi' AS metric,
            confidence AS value,
            'confidence-weighted listing' AS unit,
            COALESCE(NULLIF(names.primary, ''), id) AS name,
            COALESCE(NULLIF(basic_category, ''), taxonomy.primary, 'uncategorized') AS business_type,
            ST_Y(geometry) AS latitude,
            ST_X(geometry) AS longitude
        FROM read_parquet(?, filename=true, hive_partitioning=1)
        WHERE bbox.xmin BETWEEN ? AND ?
          AND bbox.ymin BETWEEN ? AND ?
          AND (addresses[1].country = 'IN' OR addresses[1].country IS NULL)
          AND confidence >= ?
          AND (operating_status IS NULL OR operating_status = 'open')
          AND (? IS NULL OR basic_category = ?)
        LIMIT ?
    """
    connection = duckdb.connect()
    try:
        connection.execute("LOAD spatial")
        connection.execute("LOAD httpfs")
        connection.execute("SET s3_region='us-west-2'")
        result = connection.execute(query, [
            args.city, source, west, east, south, north, args.minimum_confidence,
            args.category, args.category, args.max_rows + 1,
        ])
        rows = result.fetchall()
        columns = [item[0] for item in result.description]
    finally:
        connection.close()

    if len(rows) > args.max_rows:
        output.unlink(missing_ok=True)
        raise SystemExit(f"More than {args.max_rows:,} rows matched. Narrow --bbox or filter --category before export.")
    if not rows:
        raise SystemExit("No matching place records were found. Check the release, bbox, category, and confidence threshold.")

    with output.open("w", encoding="utf-8", newline="") as target:
        writer = csv.writer(target)
        writer.writerow(columns)
        writer.writerows(rows)
    print(f"Wrote {len(rows):,} confidence-filtered records to {output}")
    print(f"Source: Overture Places {args.release}; bbox={west},{south},{east},{north}; min confidence={args.minimum_confidence:.2f}")
    print("Import in SpotWise as Business competition. Values are confidence weights, not customer footfall or verified ground-truth counts.")


if __name__ == "__main__":
    main()
