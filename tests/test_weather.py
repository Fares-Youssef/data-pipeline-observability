import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from weather.analytics import calculate_metrics, matched_model_samples
from weather.observations import parse_ghcn_dly_line
from geo.config import Settings
from weather.archive import RawResponseArchive
from weather.providers.open_meteo import OpenMeteoProvider, ProviderError, RateLimitError, MODEL_CONFIGS
from weather.types import SamplingPoint


def payload(days=11):
    dates = [date(2026, 10, 31).fromordinal(date(2026, 10, 31).toordinal() + i).isoformat() for i in range(days)]
    return {
        "latitude": 30.0, "longitude": 31.25, "timezone": "Africa/Cairo",
        "daily_units": {"temperature_2m_max": "°C", "temperature_2m_min": "°C"},
        "daily": {"time": dates, "temperature_2m_max": [30 + i for i in range(days)], "temperature_2m_min": [20 + i for i in range(days)]},
    }


def test_open_meteo_parser_keeps_only_d1_through_d10():
    provider = OpenMeteoProvider()
    result = provider._parse(payload(), SamplingPoint(1, Decimal("30"), Decimal("31")),
                             __import__("weather.providers.open_meteo", fromlist=["MODEL_CONFIGS"]).MODEL_CONFIGS["gfs_global"],
                             date(2026, 10, 31), datetime.now(timezone.utc), json.dumps(payload()).encode())
    assert [item.lead_days for item in result.days] == list(range(1, 11))
    assert result.days[0].temperature_max_c == Decimal("31")
    assert result.days[-1].temperature_min_c == Decimal("30")


def test_open_meteo_parser_rejects_short_horizon_and_wrong_units():
    provider = OpenMeteoProvider()
    config = __import__("weather.providers.open_meteo", fromlist=["MODEL_CONFIGS"]).MODEL_CONFIGS["gfs_global"]
    with pytest.raises(ProviderError):
        provider._parse(payload(7), SamplingPoint(1, Decimal("30"), Decimal("31")), config, date(2026, 10, 31), datetime.now(timezone.utc), b"{}")
    bad = payload(); bad["daily_units"]["temperature_2m_max"] = "°F"
    with pytest.raises(ProviderError):
        provider._parse(bad, SamplingPoint(1, Decimal("30"), Decimal("31")), config, date(2026, 10, 31), datetime.now(timezone.utc), b"{}")


def test_accuracy_metrics_and_common_samples():
    result = calculate_metrics([(10, 8), (5, 6), (None, 4)])
    assert result.sample_count == 2
    assert result.mae == 1.5
    assert result.bias == 0.5
    rows = [
        {"sampling_point_id": 1, "target_date": "2026-11-01", "lead_days": 1, "model_slug": "a"},
        {"sampling_point_id": 1, "target_date": "2026-11-01", "lead_days": 1, "model_slug": "b"},
        {"sampling_point_id": 2, "target_date": "2026-11-01", "lead_days": 1, "model_slug": "a"},
    ]
    assert len(matched_model_samples(rows, ["a", "b"])) == 2


def test_ghcn_parser_converts_tenths_and_preserves_missing():
    header = "EG000000001202601TMAX"
    fields = ["00123   ", "-9999   "] + ["-9999   "] * 29
    rows = parse_ghcn_dly_line(header + "".join(fields))
    assert rows[0].temperature_max_c == Decimal("12.3")
    assert rows[1].temperature_max_c is None


class Fake429Response:
    status_code = 429
    headers = {"Retry-After": "7"}


class Fake429Session:
    def get(self, *args, **kwargs):
        return Fake429Response()


def test_rate_limit_exposes_retry_after_without_hammering():
    provider = OpenMeteoProvider(session=Fake429Session(), retries=1, minimum_interval_seconds=0)
    with pytest.raises(RateLimitError) as exc:
        provider._request(MODEL_CONFIGS["gfs_global"], {})
    assert exc.value.retry_after_seconds == 7


def test_raw_archive_is_content_addressed_and_compressed():
    archive = RawResponseArchive(Settings(db_user="test"), Path.cwd() / ".pytest_cache" / "archive-test")
    first = archive.store(b'{"real":true}', date(2026, 10, 31), "gfs_global")
    second = archive.store(b'{"real":true}', date(2026, 10, 31), "gfs_global")
    assert first == second
    assert first.uri.endswith(".json.gz")
