from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal


@dataclass(frozen=True)
class SamplingPoint:
    id: int
    latitude: Decimal
    longitude: Decimal
    timezone_name: str = "Africa/Cairo"


@dataclass(frozen=True)
class DailyForecast:
    target_date: date
    lead_days: int
    temperature_max_c: Decimal | None
    temperature_min_c: Decimal | None


@dataclass(frozen=True)
class ForecastResponse:
    point: SamplingPoint
    model_slug: str
    collected_at_utc: datetime
    grid_latitude: Decimal | None
    grid_longitude: Decimal | None
    timezone_name: str
    days: tuple[DailyForecast, ...]
    raw_bytes: bytes


@dataclass(frozen=True)
class DailyObservation:
    station_identifier: str
    observation_date: date
    temperature_max_c: Decimal | None
    temperature_min_c: Decimal | None
    quality_flags: str | None = None

