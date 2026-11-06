from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class SignalBatch(Base):
    __tablename__ = "signal_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(180), nullable=False)
    source: Mapped[str] = mapped_column(String(240), default="User supplied CSV", nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)

    observations: Mapped[list["Observation"]] = relationship(back_populates="batch", cascade="all, delete-orphan")


class Observation(Base):
    __tablename__ = "observations"
    __table_args__ = (
        Index("ix_observations_city_area_category", "city", "area", "category"),
        Index("ix_observations_city_metric", "city", "metric"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("signal_batches.id", ondelete="CASCADE"), nullable=False)
    city: Mapped[str] = mapped_column(String(90), nullable=False)
    area: Mapped[str] = mapped_column(String(120), default="Citywide", nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    metric: Mapped[str] = mapped_column(String(80), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str] = mapped_column(String(40), default="count", nullable=False)
    name: Mapped[str | None] = mapped_column(String(180))
    detail: Mapped[str | None] = mapped_column(String(100))
    observed_at: Mapped[datetime | None] = mapped_column(DateTime)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)

    batch: Mapped[SignalBatch] = relationship(back_populates="observations")


class Study(Base):
    __tablename__ = "studies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    city: Mapped[str] = mapped_column(String(90), nullable=False, index=True)
    area: Mapped[str | None] = mapped_column(String(120))
    business_type: Mapped[str] = mapped_column(String(80), nullable=False)
    radius_km: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    assumptions_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    weights_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    results_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now, nullable=False)
