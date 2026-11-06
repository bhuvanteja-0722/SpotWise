from __future__ import annotations

import csv
import io
import json
import math
import re
import posixpath
import zipfile
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET

import pyarrow.parquet as parquet

from .models import Observation

XLSX_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
XLSX_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
METRO_BOUNDS = {
    # Generous metro-region bounds used when a country-wide GTFS feed is imported.
    "ahmedabad": (71.7, 22.0, 73.4, 23.8),
    "bengaluru": (77.0, 12.3, 78.5, 13.8),
    "chennai": (79.5, 12.4, 81.0, 13.8),
    "delhi ncr": (75.8, 27.0, 78.8, 30.0),
    "hyderabad": (77.2, 16.3, 79.5, 18.5),
    "kolkata": (87.0, 21.8, 89.5, 23.8),
    "mumbai": (72.1, 17.8, 74.0, 20.8),
    "pune": (72.8, 17.7, 74.5, 19.3),
}


def _xlsx_rows(content: bytes, max_rows: int) -> list[dict]:
    """Read the first worksheet in an XLSX workbook, including shared strings."""
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        sheet = workbook.find(f"{{{XLSX_NS}}}sheets/{{{XLSX_NS}}}sheet")
        if sheet is None:
            raise ValueError("The Excel workbook has no worksheets.")
        rel_id = sheet.attrib.get(f"{{{XLSX_REL_NS}}}id")
        relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        target = next((item.attrib.get("Target", "") for item in relationships.findall(f"{{{PKG_REL_NS}}}Relationship") if item.attrib.get("Id") == rel_id), "")
        if not target:
            raise ValueError("The first Excel worksheet could not be located.")
        sheet_path = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join("xl", target))
        strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            with archive.open("xl/sharedStrings.xml") as shared:
                for _, item in ET.iterparse(shared, events=("end",)):
                    if item.tag == f"{{{XLSX_NS}}}si":
                        strings.append("".join(node.text or "" for node in item.iter(f"{{{XLSX_NS}}}t")))
                        item.clear()

        def column_index(reference: str) -> int:
            column = 0
            for character in re.match(r"[A-Z]+", reference.upper()).group(0):
                column = column * 26 + ord(character) - 64
            return column - 1

        def cell_value(cell: ET.Element) -> str:
            kind = cell.attrib.get("t")
            if kind == "inlineStr":
                return "".join(node.text or "" for node in cell.iter(f"{{{XLSX_NS}}}t"))
            value = cell.find(f"{{{XLSX_NS}}}v")
            raw = value.text if value is not None else ""
            if kind == "s" and raw:
                try:
                    return strings[int(raw)]
                except (ValueError, IndexError) as exc:
                    raise ValueError("The Excel workbook contains a broken shared-string reference.") from exc
            return raw or ""

        rows = []
        headers = None
        with archive.open(sheet_path) as source:
            for _, row_element in ET.iterparse(source, events=("end",)):
                if row_element.tag != f"{{{XLSX_NS}}}row":
                    continue
                cells = row_element.findall(f"{{{XLSX_NS}}}c")
                row = [""] * (max((column_index(cell.attrib.get("r", "A1")) for cell in cells), default=-1) + 1)
                for cell in cells:
                    row[column_index(cell.attrib.get("r", "A1"))] = cell_value(cell)
                row_element.clear()
                if headers is None:
                    headers = [_string(value) for value in row]
                    if not any(headers):
                        raise ValueError("The first worksheet needs a header row.")
                    continue
                if not any(row):
                    continue
                if len(rows) >= max_rows:
                    raise OverflowError(f"The file exceeds the {max_rows:,} row limit.")
                rows.append({headers[index]: row[index] if index < len(row) else "" for index in range(len(headers)) if headers[index]})
        if headers is None:
            raise ValueError("The Excel workbook is empty.")
        return rows


def normalize_header(header: object) -> str:
    return "".join(character for character in str(header or "").casefold().strip() if character.isalnum())


def _string(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value).strip()


def _rows_from_records(records: object) -> list[dict]:
    if isinstance(records, dict):
        for key in ("data", "records", "items", "results"):
            if isinstance(records.get(key), list):
                records = records[key]
                break
        else:
            records = [records]
    if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
        raise ValueError("JSON must contain an array of row objects, or an object with a data/records/items array.")
    return records


def _gtfs_rows(content: bytes, max_rows: int, city: str | None) -> list[dict]:
    """Turn a static GTFS ZIP into geocoded transit-stop signal rows."""
    if not city or not city.strip():
        raise ValueError("For a GTFS ZIP, choose the city/market in the import form first.")
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise ValueError("This is not a valid ZIP archive. Choose a static GTFS .zip feed.") from exc
    with archive:
        files = {}
        expanded_size = 0
        for info in archive.infolist():
            expanded_size += info.file_size
            if info.file_size < 0 or expanded_size > 512 * 1024 * 1024:
                raise ValueError("GTFS ZIP expands beyond the 512 MB safety limit.")
            if info.is_dir():
                continue
            base = Path(info.filename).name.casefold()
            if base in {"stops.txt", "routes.txt", "trips.txt", "stop_times.txt", "calendar.txt", "calendar_dates.txt", "feed_info.txt"}:
                files[base] = info
        if "stops.txt" not in files:
            raise ValueError("GTFS ZIP must contain stops.txt. Upload a static GTFS schedule feed, not GTFS-Realtime.")
        if "routes.txt" not in files or "trips.txt" not in files or "stop_times.txt" not in files:
            raise ValueError("GTFS ZIP needs routes.txt, trips.txt and stop_times.txt to calculate routes serving each stop.")

        def rows(filename: str):
            with archive.open(files[filename]) as binary:
                wrapper = io.TextIOWrapper(binary, encoding="utf-8-sig", newline="")
                yield from csv.DictReader(wrapper)

        route_modes = {}
        for row in rows("routes.txt"):
            route_id = row.get("route_id", "").strip()
            if route_id:
                route_type = row.get("route_type", "").strip()
                basic_modes = {
                    "0": "tram", "1": "metro", "2": "rail", "3": "bus", "4": "ferry",
                    "5": "cable tram", "6": "gondola", "7": "funicular", "11": "trolleybus", "12": "monorail",
                }
                if route_type.isdigit() and 100 <= int(route_type) < 200:
                    mode = "rail"
                elif route_type.isdigit() and 400 <= int(route_type) < 500:
                    mode = "metro"
                elif route_type.isdigit() and 700 <= int(route_type) < 800:
                    mode = "bus"
                elif route_type.isdigit() and 1000 <= int(route_type) < 1100:
                    mode = "ferry"
                else:
                    mode = basic_modes.get(route_type, "transit")
                route_modes[route_id] = mode
        active_services = set()
        base_calendar = {}
        today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
        dates = [today + timedelta(days=offset) for offset in range(7)]
        if "calendar.txt" in files:
            weekdays = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
            for row in rows("calendar.txt"):
                try:
                    start = datetime.strptime(row.get("start_date", ""), "%Y%m%d").date()
                    end = datetime.strptime(row.get("end_date", ""), "%Y%m%d").date()
                except ValueError:
                    continue
                base_calendar[row.get("service_id", "").strip()] = (start, end, row)
        exceptions = {}
        if "calendar_dates.txt" in files:
            for row in rows("calendar_dates.txt"):
                try:
                    exception_date = datetime.strptime(row.get("date", ""), "%Y%m%d").date()
                except ValueError:
                    continue
                if today <= exception_date < today + timedelta(days=7):
                    exceptions[(row.get("service_id", "").strip(), exception_date)] = row.get("exception_type", "").strip()
        if "calendar.txt" in files or "calendar_dates.txt" in files:
            for service_id in set(base_calendar) | {service for service, _day in exceptions}:
                start_end_row = base_calendar.get(service_id)
                for day in dates:
                    base_active = bool(start_end_row and start_end_row[0] <= day <= start_end_row[1] and start_end_row[2].get(weekdays[day.weekday()], "0") == "1")
                    exception = exceptions.get((service_id, day))
                    if exception == "1" or (exception != "2" and base_active):
                        active_services.add(service_id)
                        break
        has_calendar = "calendar.txt" in files or "calendar_dates.txt" in files
        if has_calendar and not active_services:
            raise ValueError("This GTFS feed has no scheduled services active in the next 7 days; choose a current feed.")
        apply_calendar = has_calendar
        trip_routes = {}
        for row in rows("trips.txt"):
            trip_id, route_id = row.get("trip_id", "").strip(), row.get("route_id", "").strip()
            service_id = row.get("service_id", "").strip()
            if trip_id and route_id in route_modes and (not apply_calendar or service_id in active_services):
                trip_routes[trip_id] = route_id
        served_routes: dict[str, set[str]] = {}
        for row in rows("stop_times.txt"):
            stop_id = row.get("stop_id", "").strip()
            route_id = trip_routes.get(row.get("trip_id", "").strip())
            if stop_id and route_id:
                served_routes.setdefault(stop_id, set()).add(route_id)

        output = []
        for row in rows("stops.txt"):
            stop_id = row.get("stop_id", "").strip()
            routes = served_routes.get(stop_id, set())
            if not stop_id or not routes:
                continue
            try:
                latitude, longitude = float(row.get("stop_lat", "")), float(row.get("stop_lon", ""))
            except (ValueError, TypeError):
                continue
            if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
                continue
            bounds = METRO_BOUNDS.get(city.strip().casefold())
            if bounds and not (bounds[0] <= longitude <= bounds[2] and bounds[1] <= latitude <= bounds[3]):
                continue
            if len(output) >= max_rows:
                raise OverflowError(f"The GTFS feed contains more than {max_rows:,} usable stops.")
            modes = sorted({route_modes[route] for route in routes})
            output.append({
                "city": city.strip(), "area": "Citywide", "metric": "routes_serving_stop", "value": len(routes),
                "unit": "routes", "name": row.get("stop_name", "").strip() or stop_id,
                "category_label": ", ".join(modes), "latitude": latitude, "longitude": longitude,
            })
        if not output:
            raise ValueError("No geocoded stops with scheduled routes were found in this GTFS feed.")
        return output


def read_rows(filename: str, content: bytes, max_rows: int, city: str | None = None) -> list[dict]:
    extension = Path(filename).suffix.casefold()
    if extension == ".csv":
        text = content.decode("utf-8-sig")
        try:
            dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(io.StringIO(text), dialect=dialect)
        if not reader.fieldnames:
            raise ValueError("The CSV needs a header row.")
        rows = []
        for row in reader:
            if len(rows) >= max_rows:
                raise OverflowError(f"The file exceeds the {max_rows:,} row limit.")
            rows.append(row)
        return rows

    if extension == ".xlsx":
        return _xlsx_rows(content, max_rows)

    if extension in {".jsonl", ".ndjson"}:
        rows = []
        for line_number, line in enumerate(content.decode("utf-8-sig").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"JSON Lines record {line_number} is invalid JSON.") from exc
            if not isinstance(item, dict):
                raise ValueError(f"JSON Lines record {line_number} must be an object.")
            if len(rows) >= max_rows:
                raise OverflowError(f"The file exceeds the {max_rows:,} row limit.")
            rows.append(item)
        return rows

    if extension == ".json":
        rows = _rows_from_records(json.loads(content.decode("utf-8-sig")))
        if len(rows) > max_rows:
            raise OverflowError(f"The file exceeds the {max_rows:,} row limit.")
        return rows

    if extension == ".parquet":
        parquet_file = parquet.ParquetFile(io.BytesIO(content))
        if parquet_file.metadata.num_rows > max_rows:
            raise OverflowError(f"The file exceeds the {max_rows:,} row limit.")
        return parquet_file.read().to_pylist()

    if extension == ".zip":
        return _gtfs_rows(content, max_rows, city)

    raise ValueError("Supported file types are CSV, XLSX, JSON, JSONL/NDJSON, Parquet, and static GTFS ZIP (transit category only).")


def _value(row: dict, *keys: str) -> str:
    normalized = {normalize_header(key): _string(value) for key, value in row.items() if key is not None}
    for key in keys:
        value = normalized.get(normalize_header(key), "")
        if value:
            return value
    return ""


def _number(raw: str, label: str, line: int) -> float | None:
    if not raw:
        return None
    try:
        value = float(raw.replace(",", "").strip())
    except ValueError as exc:
        raise ValueError(f"Record {line}: {label} must be numeric") from exc
    if not math.isfinite(value):
        raise ValueError(f"Record {line}: {label} must be finite")
    return value


def _datetime(raw: str, line: int) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.strip().replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError as exc:
        raise ValueError(f"Record {line}: observed_at must be an ISO date/time") from exc


def normalize_rows(rows: Iterable[dict], category: str, skip_duplicates: bool, normalize_labels: bool, max_rows: int) -> dict:
    observations: list[Observation] = []
    errors: list[str] = []
    seen: set[tuple] = set()
    duplicate_rows = raw_rows = 0

    def tidy(value: str) -> str:
        value = value.strip()
        return re.sub(r"\s+", " ", value) if normalize_labels else value

    for line_number, row in enumerate(rows, start=2):
        if line_number - 1 > max_rows:
            raise OverflowError(f"The file exceeds the {max_rows:,} row limit.")
        raw_rows += 1
        if None in row:
            errors.append(f"Record {line_number}: row has more columns than its header")
            continue
        city = tidy(_value(row, "city", "market", "city_name"))
        if not city:
            errors.append(f"Record {line_number}: city is required")
            continue
        area = tidy(_value(row, "area", "locality", "neighborhood", "neighbourhood", "district")) or "Citywide"
        metric = tidy(_value(row, "metric", "measure", "field", "signal") or category)
        raw_value = _value(row, "value", "count", "amount", "visitors", "vehicles", "speed", "available_spaces", "salary", "rent", "cost")
        if not raw_value and category in {"competition", "transit"}:
            raw_value = "1"
        if not raw_value:
            errors.append(f"Record {line_number}: add a numeric value/count column")
            continue
        try:
            numeric_value = _number(raw_value, "value", line_number)
            if numeric_value is None or numeric_value < 0:
                raise ValueError(f"Record {line_number}: value cannot be negative or empty")
            latitude = _number(_value(row, "latitude", "lat"), "latitude", line_number)
            longitude = _number(_value(row, "longitude", "lon", "lng"), "longitude", line_number)
            observed_at = _datetime(_value(row, "observed_at", "timestamp", "datetime", "date"), line_number)
            if (latitude is None) != (longitude is None):
                raise ValueError(f"Record {line_number}: latitude and longitude must appear together")
            if latitude is not None and not -90 <= latitude <= 90:
                raise ValueError(f"Record {line_number}: latitude must be between -90 and 90")
            if longitude is not None and not -180 <= longitude <= 180:
                raise ValueError(f"Record {line_number}: longitude must be between -180 and 180")
        except ValueError as exc:
            errors.append(str(exc))
            continue

        detail = tidy(_value(row, "business_type", "category_label", "mode", "kind", "type", "competitor_type"))[:100] or None
        unit = tidy(_value(row, "unit", "units", "measurement_unit") or "count")[:40]
        name = tidy(_value(row, "name", "business_name", "station_name", "label"))[:180] or None
        duplicate_key = (
            city.casefold(), area.casefold(), category, metric.casefold(), numeric_value, unit.casefold(),
            name.casefold() if name else None, detail.casefold() if detail else None,
            observed_at.isoformat() if observed_at else None, latitude, longitude,
        )
        if skip_duplicates and duplicate_key in seen:
            duplicate_rows += 1
            continue
        seen.add(duplicate_key)
        observations.append(Observation(
            city=city[:90], area=area[:120], category=category, metric=metric[:80], value=numeric_value,
            unit=unit, name=name, detail=detail, observed_at=observed_at, latitude=latitude, longitude=longitude,
            metadata_json="{}",
        ))

    values = sorted(row.value for row in observations)
    if values:
        midpoint = len(values) // 2
        median = values[midpoint] if len(values) % 2 else (values[midpoint - 1] + values[midpoint]) / 2
        minimum, maximum = values[0], values[-1]
    else:
        median = minimum = maximum = None
    sample = [{"city": row.city, "area": row.area, "metric": row.metric, "value": row.value, "unit": row.unit} for row in observations[:5]]
    return {
        "observations": observations,
        "scanned_rows": raw_rows,
        "clean_rows": len(observations),
        "skipped_invalid_rows": len(errors),
        "duplicates_removed": duplicate_rows,
        "geocoded_rows": sum(row.latitude is not None for row in observations),
        "timestamped_rows": sum(row.observed_at is not None for row in observations),
        "minimum_value": minimum,
        "median_value": median,
        "maximum_value": maximum,
        "errors": errors[:10],
        "sample_rows": sample,
    }
