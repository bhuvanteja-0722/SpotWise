from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


SignalCategory = Literal[
    "footfall",
    "competition",
    "traffic",
    "parking",
    "transit",
    "worker_supply",
    "salary",
    "cost_of_living",
    "rent",
]


class StudyCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    city: str = Field(min_length=2, max_length=90)
    area: str | None = Field(default=None, max_length=120)
    business_type: str = Field(min_length=2, max_length=80)
    radius_km: float = Field(default=1.0, ge=0.1, le=50)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    assumptions: dict[str, float] = Field(default_factory=dict)
    weights: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def coordinate_pair(self):
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("Latitude and longitude must be supplied together")
        if any(value < 0 for value in self.weights.values()):
            raise ValueError("Factor weights cannot be negative")
        if any(value < 0 for value in self.assumptions.values()):
            raise ValueError("Business assumptions cannot be negative")
        if self.assumptions.get("gross_margin_percent", 0) > 100:
            raise ValueError("Gross margin must be at most 100 percent")
        return self


class StudyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    assumptions: dict[str, float] | None = None
    weights: dict[str, float] | None = None

    @model_validator(mode="after")
    def validate_values(self):
        if self.weights and any(value < 0 for value in self.weights.values()):
            raise ValueError("Factor weights cannot be negative")
        if self.assumptions and any(value < 0 for value in self.assumptions.values()):
            raise ValueError("Business assumptions cannot be negative")
        if self.assumptions and self.assumptions.get("gross_margin_percent", 0) > 100:
            raise ValueError("Gross margin must be at most 100 percent")
        return self


class StudyComparison(BaseModel):
    study_ids: list[int] = Field(min_length=2, max_length=4)


class DatasetImportResult(BaseModel):
    id: int
    name: str
    category: str
    row_count: int
    imported_at: str
    warnings: list[str] = Field(default_factory=list)
