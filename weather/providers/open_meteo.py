from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Iterable
from zoneinfo import ZoneInfo

import requests

from weather.types import DailyForecast, ForecastResponse, SamplingPoint


@dataclass(frozen=True)
class ModelConfig:
    slug: str
    endpoint: str
    provider_model_id: str
    organization: str
    distributor: str = "Open-Meteo"


MODEL_CONFIGS = {
    "gfs_global": ModelConfig("gfs_global", "https://api.open-meteo.com/v1/gfs", "gfs_global", "NOAA/NCEP"),
    "ecmwf_ifs": ModelConfig("ecmwf_ifs", "https://api.open-meteo.com/v1/ecmwf", "ecmwf_ifs", "ECMWF"),
    "ecmwf_aifs025_single": ModelConfig(
        "ecmwf_aifs025_single", "https://api.open-meteo.com/v1/ecmwf", "ecmwf_aifs025_single", "ECMWF"
    ),
}


class ProviderError(RuntimeError):
    pass


class RateLimitError(ProviderError):
    def __init__(self, message: str, retry_after_seconds: int):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class OpenMeteoProvider:
    """Explicit-model Open-Meteo adapter; it never uses Best Match or seamless models."""

    def __init__(self, api_key: str | None = None, timeout_seconds: int = 30, retries: int = 3, session=None,
                 minimum_interval_seconds: float = 3.0, rate_limit_backoff_seconds: int = 60):
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.retries = retries
        self.session = session or requests.Session()
        self.minimum_interval_seconds = minimum_interval_seconds
        self.rate_limit_backoff_seconds = rate_limit_backoff_seconds
        self._last_request_monotonic = 0.0

    def _request(self, config: ModelConfig, params: dict, endpoint_override: str | None = None) -> bytes:
        endpoint = endpoint_override or config.endpoint
        if self.api_key:
            endpoint = endpoint.replace("https://api.", "https://customer-api.")
            params["apikey"] = self.api_key
        last_error: Exception | None = None
        for attempt in range(self.retries):
            try:
                remaining = self.minimum_interval_seconds - (time.monotonic() - self._last_request_monotonic)
                if remaining > 0:
                    time.sleep(remaining)
                response = self.session.get(endpoint, params=params, timeout=self.timeout_seconds)
                self._last_request_monotonic = time.monotonic()
                if response.status_code == 429:
                    raw_retry = response.headers.get("Retry-After", "")
                    retry_after = int(raw_retry) if raw_retry.isdigit() else self.rate_limit_backoff_seconds * (attempt + 1)
                    last_error = RateLimitError(f"Open-Meteo HTTP 429; retry after {retry_after}s", retry_after)
                    if attempt + 1 < self.retries:
                        time.sleep(retry_after)
                        continue
                    raise last_error
                if response.status_code >= 500:
                    raise ProviderError(f"Open-Meteo temporary HTTP {response.status_code}")
                response.raise_for_status()
                return response.content
            except (requests.RequestException, ProviderError) as exc:
                last_error = exc
                if isinstance(exc, RateLimitError):
                    raise
                if attempt + 1 < self.retries:
                    time.sleep(min(2**attempt, 8))
        raise ProviderError(f"Open-Meteo request failed after {self.retries} attempts: {last_error}")

    def fetch(self, model_slug: str, points: Iterable[SamplingPoint], collection_date: date | None = None,
              run_at_utc: datetime | None = None) -> list[ForecastResponse]:
        if model_slug not in MODEL_CONFIGS:
            raise ValueError(f"Unsupported explicit model: {model_slug}")
        points = list(points)
        if not points:
            return []
        config = MODEL_CONFIGS[model_slug]
        cairo_today = collection_date or datetime.now(ZoneInfo("Africa/Cairo")).date()
        params = {
            "latitude": ",".join(str(point.latitude) for point in points),
            "longitude": ",".join(str(point.longitude) for point in points),
            "daily": "temperature_2m_max,temperature_2m_min",
            "temperature_unit": "celsius",
            "timezone": "Africa/Cairo",
            "forecast_days": 11,
            "models": config.provider_model_id,
            "cell_selection": "land",
        }
        endpoint = None
        if run_at_utc is not None:
            if run_at_utc.tzinfo is None:
                run_at_utc = run_at_utc.replace(tzinfo=timezone.utc)
            params["run"] = run_at_utc.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M")
            endpoint = "https://single-runs-api.open-meteo.com/v1/forecast"
        raw = self._request(config, params, endpoint)
        payload = json.loads(raw)
        items = payload if isinstance(payload, list) else [payload]
        if len(items) != len(points):
            raise ProviderError(f"Expected {len(points)} location responses, received {len(items)}")
        collected = datetime.now(timezone.utc)
        return [self._parse(item, point, config, cairo_today, collected, raw) for item, point in zip(items, points)]

    @staticmethod
    def _parse(item: dict, point: SamplingPoint, config: ModelConfig, collection_date: date,
               collected: datetime, raw: bytes) -> ForecastResponse:
        daily = item.get("daily") or {}
        unit_max = (item.get("daily_units") or {}).get("temperature_2m_max")
        unit_min = (item.get("daily_units") or {}).get("temperature_2m_min")
        if unit_max != "°C" or unit_min != "°C":
            raise ProviderError(f"Unexpected temperature units: max={unit_max!r}, min={unit_min!r}")
        dates = daily.get("time") or []
        maxima = daily.get("temperature_2m_max") or []
        minima = daily.get("temperature_2m_min") or []
        if not (len(dates) == len(maxima) == len(minima)):
            raise ProviderError("Daily forecast arrays have inconsistent lengths")
        normalized: list[DailyForecast] = []
        for raw_date, high, low in zip(dates, maxima, minima):
            target = date.fromisoformat(raw_date)
            lead = (target - collection_date).days
            if 1 <= lead <= 10:
                normalized.append(DailyForecast(
                    target_date=target,
                    lead_days=lead,
                    temperature_max_c=Decimal(str(high)) if high is not None else None,
                    temperature_min_c=Decimal(str(low)) if low is not None else None,
                ))
        if len(normalized) != 10 or {item.lead_days for item in normalized} != set(range(1, 11)):
            raise ProviderError(f"Model {config.slug} did not return the complete D+1..D+10 horizon")
        return ForecastResponse(
            point=point,
            model_slug=config.slug,
            collected_at_utc=collected,
            grid_latitude=Decimal(str(item["latitude"])) if item.get("latitude") is not None else None,
            grid_longitude=Decimal(str(item["longitude"])) if item.get("longitude") is not None else None,
            timezone_name=item.get("timezone") or "Africa/Cairo",
            days=tuple(normalized),
            raw_bytes=raw,
        )

    @staticmethod
    def request_hash(model_slug: str, point_ids: Iterable[int], logical_date: date) -> bytes:
        canonical = json.dumps({"model": model_slug, "points": sorted(point_ids), "date": logical_date.isoformat()}, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).digest()

