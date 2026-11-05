#!/usr/bin/env python3
"""Build compact, attributed SpotWise aggregates from Kaggle source files.

The source archives are intentionally kept out of Git. This script streams the
97K-row workbook and the CSV inputs, then writes only neighborhood/city-level
aggregates consumed by the static dashboard.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
import zipfile
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Iterable
import xml.etree.ElementTree as ET

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
CITIES = {
    "Bengaluru": ("bengaluru", "bangalore"),
    "Mumbai": ("mumbai", "bombay"),
    "Hyderabad": ("hyderabad", "secunderabad"),
    "Pune": ("pune",),
    "Chennai": ("chennai", "madras"),
    "Delhi NCR": ("delhi", "gurugram", "gurgaon", "noida"),
    "Kolkata": ("kolkata", "calcutta"),
    "Ahmedabad": ("ahmedabad",),
}
STATION_CITY = {
    "Mumbai": ("MUMBAI", "BOMBAY", "DADAR", "BANDRA", "ANDHERI", "KURLA", "BORIVALI", "THANE"),
    "Bengaluru": ("BANGALORE", "BENGALURU", "YESVANTPUR", "YELAHANKA"),
    "Hyderabad": ("HYDERABAD", "SECUNDERABAD", "KACHEGUDA", "LINGAMPALLI"),
    "Pune": ("PUNE",),
    "Chennai": ("CHENNAI", "MADRAS", "TAMBARAM"),
    "Delhi NCR": ("DELHI", "NEW DELHI", "ANAND VIHAR", "HAZRAT NIZAMUDDIN"),
    "Kolkata": ("KOLKATA", "HOWRAH", "SEALDAH"),
    "Ahmedabad": ("AHMEDABAD",),
}


def median(values: list[float]) -> float | None:
    return round(float(statistics.median(values)), 2) if values else None


def parse_money(value: str) -> float | None:
    text = (value or "").strip().lower().replace(",", "").replace("₹", "").replace("inr", "")
    if not text or text in {"na", "n/a", "not disclosed", "none", "-"}:
        return None
    match = re.search(r"\d+(?:\.\d+)?", text)
    if not match:
        return None
    number = float(match.group())
    if "crore" in text or re.search(r"\bcr\b", text):
        number *= 10_000_000
    elif "lakh" in text or "lac" in text or re.search(r"\bl\b", text):
        number *= 100_000
    return number


def get_locality(title: str, description: str) -> str | None:
    text = title or ""
    match = re.search(r"\bfor\s+rent\s+(?:in|at)\s+(.+)$", text, re.I)
    if match:
        tail = match.group(1)
        if re.search(r"\bat\b", tail, re.I):
            tail = re.split(r"\bat\b", tail, flags=re.I)[-1]
    else:
        match = re.search(r"\b(?:available\s+for\s+rent\s+in|rent\s+in)\s+([^,]+)", description or "", re.I)
        if not match:
            return None
        tail = match.group(1)
    tail = re.split(r",\s*(?:mumbai|maharashtra)\b", tail, flags=re.I)[0]
    tail = re.sub(r"\b(?:mumbai|maharashtra)\b.*$", "", tail, flags=re.I).strip(" ,.-")
    tail = re.sub(r"\s+", " ", tail)
    if not tail or len(tail) > 60 or tail.lower() in {"mumbai", "maharashtra"}:
        return None
    return " ".join(part.capitalize() for part in tail.split())


def parse_area(value: str) -> float | None:
    match = re.search(r"\d+(?:\.\d+)?", value or "")
    if not match:
        return None
    area = float(match.group())
    return area if 100 <= area <= 100_000 else None


def read_rentals(path: Path) -> dict:
    localities: dict[str, list[dict]] = defaultdict(list)
    overall_rents: list[float] = []
    property_types: Counter = Counter()
    total_rows = rent_rows = valid_rents = valid_area = 0
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            total_rows += 1
            if (row.get("Transaction Type") or "Rent").strip().lower() not in {"rent", ""}:
                continue
            rent_rows += 1
            price = parse_money(row.get("Price", ""))
            area = parse_area(row.get("Area", ""))
            locality = get_locality(row.get("Title", ""), row.get("Description", ""))
            if price and price < 5_000_000:
                valid_rents += 1
                overall_rents.append(price)
                if area:
                    valid_area += 1
                if locality:
                    localities[locality].append({"rent": price, "area": area})
                property_types[(row.get("Property Type") or "Other").strip() or "Other"] += 1

    summary = []
    for name, items in localities.items():
        rents = [item["rent"] for item in items]
        per_sqft = [item["rent"] / item["area"] for item in items if item["area"]]
        if len(items) < 8:
            continue
        summary.append({
            "name": name,
            "listingCount": len(items),
            "medianMonthlyRent": median(rents),
            "medianRentPerSqFt": median(per_sqft),
        })
    summary.sort(key=lambda item: (-item["listingCount"], item["name"]))
    return {
        "city": "Mumbai",
        "recordCount": total_rows,
        "rentRecordCount": rent_rows,
        "validRentCount": valid_rents,
        "validAreaCount": valid_area,
        "medianMonthlyRent": median(overall_rents),
        "localities": summary[:80],
        "propertyTypes": [{"name": k, "count": v} for k, v in property_types.most_common(8)],
        "limitations": ["Mumbai listings only", "Snapshot published in 2023", "Listing asking rent is not signed lease rent", "Area names are inferred from listing titles"],
    }


def column_number(reference: str) -> int:
    letters = re.match(r"[A-Z]+", reference).group(0)
    out = 0
    for char in letters:
        out = out * 26 + ord(char) - 64
    return out - 1


def read_xlsx_rows(path: Path) -> Iterable[list[str]]:
    """Read a simple inline-string XLSX sheet using only Python's standard library."""
    with zipfile.ZipFile(path) as workbook:
        workbook_xml = ET.fromstring(workbook.read("xl/workbook.xml"))
        sheet_name = workbook_xml.find("m:sheets/m:sheet", NS)
        rel_id = sheet_name.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        relationships = ET.fromstring(workbook.read("xl/_rels/workbook.xml.rels"))
        target = next(item.attrib["Target"] for item in relationships if item.attrib.get("Id") == rel_id)
        sheet_path = target.lstrip("/") if target.startswith("/") else f"xl/{target}"
        with workbook.open(sheet_path) as source:
            for _, row_element in ET.iterparse(source, events=("end",)):
                if row_element.tag != f"{{{NS['m']}}}row":
                    continue
                cells = row_element.findall("m:c", NS)
                row = [""] * (max((column_number(cell.attrib["r"]) for cell in cells), default=-1) + 1)
                for cell in cells:
                    index = column_number(cell.attrib["r"])
                    inline = cell.find("m:is/m:t", NS)
                    value = cell.find("m:v", NS)
                    row[index] = (inline.text if inline is not None else value.text if value is not None else "") or ""
                yield row
                row_element.clear()


def classify_role(title: str) -> str:
    value = title.lower()
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


def cities_for(location: str) -> list[str]:
    normalized = location.lower()
    found = []
    for city, aliases in CITIES.items():
        if any(re.search(r"(?<![a-z])" + re.escape(alias) + r"(?![a-z])", normalized) for alias in aliases):
            found.append(city)
    return found


def read_jobs(path: Path) -> dict:
    headers = None
    city_jobs = {city: {"postings": 0, "salaryOffers": [], "roles": Counter(), "skills": Counter()} for city in CITIES}
    role_stats: dict[str, dict] = defaultdict(lambda: {"postings": 0, "salaryOffers": []})
    total_rows = salary_rows = 0
    for row in read_xlsx_rows(path):
        if headers is None:
            headers = row
            continue
        record = {headers[i]: row[i] if i < len(row) else "" for i in range(len(headers))}
        title = record.get("title", "")
        location = record.get("location", "")
        currency = (record.get("currency") or "INR").upper().strip()
        total_rows += 1
        low = parse_money(record.get("minimumSalary", ""))
        high = parse_money(record.get("maximumSalary", ""))
        salary = (low + high) / 2 if low and high and currency == "INR" and high >= low else None
        if salary:
            salary_rows += 1
        role = classify_role(title)
        role_stats[role]["postings"] += 1
        if salary:
            role_stats[role]["salaryOffers"].append(salary)
        skills = [item.strip().lower() for item in (record.get("tagsAndSkills") or "").split(",") if 2 <= len(item.strip()) <= 40]
        for city in cities_for(location):
            stats = city_jobs[city]
            stats["postings"] += 1
            stats["roles"][role] += 1
            if salary:
                stats["salaryOffers"].append(salary)
            stats["skills"].update(skills)
    cities = []
    for name, stats in city_jobs.items():
        if not stats["postings"]:
            continue
        cities.append({
            "name": name,
            "postingCount": stats["postings"],
            "salaryDisclosureCount": len(stats["salaryOffers"]),
            "salaryDisclosureRate": round(len(stats["salaryOffers"]) / stats["postings"] * 100, 1),
            "medianAnnualSalary": median(stats["salaryOffers"]),
            "topRoles": [{"name": role, "count": count} for role, count in stats["roles"].most_common(6)],
            "topSkills": [{"name": skill, "count": count} for skill, count in stats["skills"].most_common(12)],
        })
    cities.sort(key=lambda item: -item["postingCount"])
    roles = []
    for name, stats in sorted(role_stats.items(), key=lambda item: -item[1]["postings"]):
        roles.append({"name": name, "postingCount": stats["postings"], "medianAnnualSalary": median(stats["salaryOffers"])})
    return {
        "recordCount": total_rows,
        "salaryDisclosureCount": salary_rows,
        "salaryDisclosureRate": round(salary_rows / total_rows * 100, 1) if total_rows else 0,
        "cities": cities,
        "roleGroups": roles,
        "limitations": ["Snapshot of online job postings; not a count of available workers", "Salary summaries use disclosed INR minimum/maximum ranges", "City matches are inferred from the raw location text", "Dataset is a 2025 snapshot"],
    }


def read_rail(path: Path) -> dict:
    stations = {city: set() for city in STATION_CITY}
    services = {city: set() for city in STATION_CITY}
    schedule_rows = Counter()
    total_rows = 0
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            total_rows += 1
            station = (row.get("Station Name") or "").strip().upper()
            train = (row.get("Train No.") or "").strip().strip("'")
            code = (row.get("station Code") or "").strip()
            if not station or not train:
                continue
            for city, tokens in STATION_CITY.items():
                if any(token in station for token in tokens):
                    stations[city].add((code, station))
                    services[city].add(train)
                    schedule_rows[city] += 1
    return {
        "recordCount": total_rows,
        "snapshot": "2015 timetable dataset",
        "cities": [{"name": city, "stationCount": len(stations[city]), "trainCount": len(services[city]), "scheduleRowCount": schedule_rows[city]} for city in stations],
        "limitations": ["Historical timetable snapshot from 2015", "City service counts are inferred from station-name matches", "Rail coverage only; no metro/bus stop locations or passenger counts", "Not suitable for block-level access or current journey times"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=Path, required=True, help="Path to indian-job-market-dataset-2025.xlsx")
    parser.add_argument("--rentals", type=Path, required=True, help="Path to Mumbai_property_details.csv")
    parser.add_argument("--rail", type=Path, required=True, help="Path to Indian Railways timetable CSV")
    parser.add_argument("--out", type=Path, default=Path("data/analysis.json"))
    args = parser.parse_args()
    result = {
        "generatedAt": date.today().isoformat(),
        "sources": [
            {"key": "rentals", "name": "Property Rental Listings Dataset", "url": "https://www.kaggle.com/datasets/harshalhonde/property-rental-listings-dataset", "license": "CC BY 4.0", "lastUpdated": "2023-08", "records": "23,405"},
            {"key": "jobs", "name": "Indian Job Market Dataset 2025", "url": "https://www.kaggle.com/datasets/shivamshrivastava21/indian-job-market-dataset-2025-2026", "license": "CC BY-NC-SA 4.0", "lastUpdated": "2025", "records": "97,929"},
            {"key": "rail", "name": "Indian Railways Time Table for Trains Available", "url": "https://www.kaggle.com/datasets/harsh16/indian-railways-time-table-for-trains-available", "license": "Open Database License / Database Contents License", "lastUpdated": "2015 snapshot", "records": "69,006"},
        ],
        "rentals": read_rentals(args.rentals),
        "jobs": read_jobs(args.jobs),
        "rail": read_rail(args.rail),
        "coverageGaps": [
            {"name": "Commercial rent", "status": "Partial", "need": "Commercial lease listings; current rent file covers residential property listings in Mumbai"},
            {"name": "Footfall", "status": "Missing", "need": "Hourly anonymized pedestrian counts or licensed mobility aggregates"},
            {"name": "Business competition", "status": "Missing", "need": "Geocoded POI/business registry with category and freshness fields"},
            {"name": "Road traffic and parking", "status": "Missing", "need": "Road-segment traffic and parking occupancy/availability data"},
            {"name": "Local transit access", "status": "Partial", "need": "Current GTFS stops/routes plus walking-distance geospatial join"},
            {"name": "Operating costs and profit", "status": "Scenario only", "need": "User-entered local costs, selling price, volume and margin assumptions"},
        ],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"Wrote {args.out} ({args.out.stat().st_size:,} bytes)")
    print(f"Rental records: {result['rentals']['recordCount']:,}; job postings: {result['jobs']['recordCount']:,}; rail schedule rows: {result['rail']['recordCount']:,}")


if __name__ == "__main__":
    main()
