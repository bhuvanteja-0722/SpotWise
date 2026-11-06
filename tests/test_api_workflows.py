"""End-to-end API checks run against a disposable SQLite database."""
from __future__ import annotations

import csv
import io
import json
import os
import tempfile
import unittest
import zipfile
from datetime import datetime
from zoneinfo import ZoneInfo

_test_dir = tempfile.TemporaryDirectory(prefix="spotwise-tests-")
os.environ["DATABASE_URL"] = f"sqlite:///{_test_dir.name}/workflows.db"

import pyarrow as pa
import pyarrow.parquet as parquet
from fastapi.testclient import TestClient
from sqlalchemy import delete

from backend.db import SessionLocal
from backend.main import app
from backend.models import Observation, SignalBatch, Study


def xlsx_bytes(rows: list[list[str]]) -> bytes:
    def cells(row: list[str], row_number: int) -> str:
        return "".join(
            f'<c r="{chr(65 + index)}{row_number}" t="inlineStr"><is><t>{value}</t></is></c>'
            for index, value in enumerate(row)
        )

    worksheet = "<worksheet xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\"><sheetData>" + "".join(
        f'<row r="{number}">{cells(row, number)}</row>' for number, row in enumerate(rows, 1)
    ) + "</sheetData></worksheet>"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>')
        archive.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/></Relationships>')
        archive.writestr("xl/worksheets/sheet1.xml", worksheet)
    return buffer.getvalue()


def gtfs_bytes() -> bytes:
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date().strftime("%Y%m%d")
    files = {
        "stops.txt": "stop_id,stop_name,stop_lat,stop_lon\nS1,Central,12.9716,77.5946\n",
        "routes.txt": "route_id,route_type\nR1,1\n",
        "trips.txt": "route_id,service_id,trip_id\nR1,SVC,T1\n",
        "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,stop_sequence\nT1,08:00:00,08:00:00,S1,1\n",
        "calendar_dates.txt": f"service_id,date,exception_type\nSVC,{today},1\n",
    }
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return target.getvalue()


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client_context = TestClient(app)
        cls.client = cls.client_context.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client_context.__exit__(None, None, None)
        _test_dir.cleanup()

    def upload(self, filename: str, content: bytes, category: str = "footfall", city: str = "Bengaluru", **fields):
        data = {"category": category, "name": fields.pop("name", f"Test {filename}"), "source": "Workflow test source", "dataset_city": city, "skip_duplicates": "true", "normalize_labels": "true", **fields}
        return self.client.post("/api/v1/datasets/import", data=data, files={"file": (filename, content)})

    def setUp(self):
        with SessionLocal() as session:
            session.execute(delete(Observation))
            session.execute(delete(Study))
            session.execute(delete(SignalBatch))
            session.commit()

    def test_market_sources_import_formats_and_city_filtered_competition(self):
        self.assertEqual(self.client.get("/api/v1/health").status_code, 200)
        markets = self.client.get("/api/v1/markets").json()
        self.assertIn("Bengaluru", markets["markets"])
        self.assertEqual(self.client.get("/api/v1/dashboard?city=Hyderabad").json()["market_context"]["rental_market"], None)

        csv_data = b"city,area,metric,value,unit,latitude,longitude\nBengaluru,Indiranagar,daily_visitors,100,count,12.978,77.640\nBengaluru,Indiranagar,daily_visitors,100,count,12.978,77.640\nBengaluru,,daily_visitors,invalid,count,,,\n"
        preview = self.client.post("/api/v1/datasets/preview", data={"category": "footfall"}, files={"file": ("footfall.csv", csv_data)})
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertEqual((preview.json()["clean_rows"], preview.json()["duplicates_removed"], preview.json()["skipped_invalid_rows"]), (1, 1, 1))
        imported = self.upload("footfall.csv", csv_data)
        self.assertEqual(imported.status_code, 201, imported.text)
        self.assertEqual(imported.json()["row_count"], 1)

        json_data = json.dumps([{"city": "Bengaluru", "area": "Koramangala", "value": 10}]).encode()
        jsonl_data = b'{"city":"Bengaluru","area":"Jayanagar","value":11}\n'
        table = pa.Table.from_pylist([{"city": "Bengaluru", "area": "Whitefield", "value": 12}])
        parquet_file = io.BytesIO()
        parquet.write_table(table, parquet_file)
        format_cases = [
            ("rows.json", json_data),
            ("rows.jsonl", jsonl_data),
            ("rows.xlsx", xlsx_bytes([["city", "area", "value"], ["Bengaluru", "HSR Layout", "13"]])),
            ("rows.parquet", parquet_file.getvalue()),
        ]
        for filename, payload in format_cases:
            with self.subTest(filename=filename):
                result = self.upload(filename, payload)
                self.assertEqual(result.status_code, 201, result.text)
                self.assertEqual(result.json()["row_count"], 1)

        competition_csv = (
            "city,area,metric,value,unit,name,business_type,latitude,longitude\n"
            "Bengaluru,Indiranagar,confidence_weighted_poi,0.8,listing,Cafe A,restaurant,12.978,77.640\n"
            "Bengaluru,Indiranagar,confidence_weighted_poi,0.95,listing,Drug B,pharmacy_and_drug_store,12.979,77.641\n"
        ).encode()
        competition = self.upload("competitors.csv", competition_csv, category="competition", name="Tagged competitors")
        self.assertEqual(competition.status_code, 201, competition.text)
        cafe_dashboard = self.client.get("/api/v1/dashboard", params={"city": "Bengaluru", "area": "Indiranagar", "business_type": "Cafe / restaurant"}).json()
        cafe_signals = cafe_dashboard["uploaded_signals"]["categories"]["competition"]
        self.assertEqual(cafe_signals["record_count"], 1)
        self.assertEqual(cafe_signals["total"], 0.8)
        pharmacy_dashboard = self.client.get("/api/v1/dashboard", params={"city": "Bengaluru", "area": "Indiranagar", "business_type": "Pharmacy"}).json()
        self.assertEqual(pharmacy_dashboard["uploaded_signals"]["categories"]["competition"]["record_count"], 1)
        self.assertNotIn("Indiranagar", self.client.get("/api/v1/areas", params={"city": "Mumbai"}).json()["areas"])
        bengaluru_areas = self.client.get("/api/v1/areas", params={"city": "Bengaluru"}).json()["areas"]
        self.assertIn("Indiranagar", bengaluru_areas)
        self.assertNotIn("Bangalore", bengaluru_areas)
        self.assertNotIn("Banglore", bengaluru_areas)
        self.assertNotIn("Bengaluru", bengaluru_areas)
        coverage = self.client.get("/api/v1/coverage", params={"city": "Bengaluru"}).json()
        competition_card = next(item for item in coverage["items"] if item["name"] == "Business competition")
        self.assertEqual(competition_card["status"], "Local data")
        self.assertGreater(competition_card["record_count"], 0)

    def test_gtfs_salary_classifier_and_study_lifecycle(self):
        gtfs = gtfs_bytes()
        preview = self.client.post("/api/v1/datasets/preview", data={"category": "transit", "dataset_city": "Bengaluru"}, files={"file": ("feed.zip", gtfs)})
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertEqual(preview.json()["geocoded_rows"], 1)
        transit_import = self.upload("feed.zip", gtfs, category="transit", name="Synthetic GTFS feed")
        self.assertEqual(transit_import.status_code, 201, transit_import.text)
        self.assertEqual(transit_import.json()["geocoded_rows"], 1)
        competition_csv = b"city,area,metric,value,name,business_type,latitude,longitude\nBengaluru,Indiranagar,confidence_weighted_poi,0.8,Cafe,restaurant,12.978,77.640\n"
        competition_import = self.upload("cafe.csv", competition_csv, category="competition", name="Cafe competitor")
        self.assertEqual(competition_import.status_code, 201, competition_import.text)
        malformed = self.client.post("/api/v1/datasets/preview", data={"category": "transit", "dataset_city": "Bengaluru"}, files={"file": ("bad.zip", b"not a zip")})
        self.assertEqual(malformed.status_code, 422)
        self.assertEqual(self.client.post("/api/v1/datasets/preview", data={"category": "competition"}, files={"file": ("feed.zip", gtfs)}).status_code, 422)

        salary = self.client.post("/api/v1/workforce/salary-estimate", json={"city": "Bengaluru", "job_title": "Cafe manager", "skills": "inventory, staff training", "experience_years": 2})
        self.assertEqual(salary.status_code, 200, salary.text)
        self.assertGreater(salary.json()["monthly_salary_inr"], 0)
        categories = self.client.post("/api/v1/business/classify", json={"city": "Bengaluru", "names": ["Apollo Pharmacy", "Cafe Coffee Day"]})
        self.assertEqual(categories.status_code, 200, categories.text)
        self.assertIn("pharmacy", categories.json()["predictions"][0]["suggestions"][0]["category"])
        out_of_scope = self.client.post("/api/v1/business/classify", json={"city": "Mumbai", "names": ["Cafe Coffee Day"]}).json()
        self.assertIsNotNone(out_of_scope["scope_warning"])

        first = self.client.post("/api/v1/studies", json={"name": "Cafe study", "city": "Bengaluru", "area": "Indiranagar", "business_type": "Cafe / restaurant", "radius_km": 1, "assumptions": {"maximum_competitors": 10, "monthly_rent": 50000, "rent_budget": 70000, "average_ticket": 200, "gross_margin_percent": 60, "expected_customers_per_day": 80}})
        self.assertEqual(first.status_code, 201, first.text)
        study_id = first.json()["id"]
        self.assertIn("competition", first.json()["results"]["factor_scores"])
        self.assertEqual(first.json()["results"]["operating_scenario"]["status"], "ready")
        updated = self.client.put(f"/api/v1/studies/{study_id}", json={"name": "Updated cafe study", "assumptions": {"maximum_competitors": 8}})
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["name"], "Updated cafe study")
        second = self.client.post("/api/v1/studies", json={"name": "Retail study", "city": "Mumbai", "business_type": "Retail shop"}).json()
        compared = self.client.post("/api/v1/studies/compare", json={"study_ids": [study_id, second["id"]]})
        self.assertEqual(compared.status_code, 200, compared.text)
        self.assertEqual(len(compared.json()["studies"]), 2)
        self.assertEqual(self.client.get(f"/api/v1/studies/{study_id}").status_code, 200)
        for current_id in (study_id, second["id"]):
            self.assertEqual(self.client.delete(f"/api/v1/studies/{current_id}").status_code, 200)
        self.assertEqual(self.client.get(f"/api/v1/studies/{study_id}").status_code, 404)

    def test_export_sources_and_validation_errors(self):
        self.assertEqual(self.client.get("/api/v1/data-sources").status_code, 200)
        self.assertGreater(len(self.client.get("/api/v1/transport-sources", params={"city": "Bengaluru"}).json()["feeds"]), 0)
        self.assertEqual(self.client.get("/api/v1/signals", params={"city": "Bengaluru", "latitude": 12.9}).status_code, 422)
        invalid_study = self.client.post("/api/v1/studies", json={"name": "Bad coordinates", "city": "Mumbai", "business_type": "Retail", "latitude": 19.0})
        self.assertEqual(invalid_study.status_code, 422)
        invalid_file = self.client.post("/api/v1/datasets/preview", data={"category": "footfall"}, files={"file": ("rows.csv", b"city,value\nBengaluru,not-a-number\n")})
        self.assertEqual(invalid_file.status_code, 200)
        self.assertEqual(invalid_file.json()["clean_rows"], 0)
        self.assertGreater(invalid_file.json()["skipped_invalid_rows"], 0)


if __name__ == "__main__":
    unittest.main()
