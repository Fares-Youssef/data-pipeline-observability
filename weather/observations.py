from __future__ import annotations

from datetime import date
from decimal import Decimal

from weather.types import DailyObservation

import requests


GHCN_BASE = "https://www.ncei.noaa.gov/pub/data/ghcn/daily"


def parse_ghcn_station_line(line: str) -> dict:
    if len(line) < 71:
        raise ValueError("Invalid GHCN station line")
    return {
        "source_station_id": line[0:11].strip(),
        "latitude": Decimal(line[12:20].strip()),
        "longitude": Decimal(line[21:30].strip()),
        "elevation_m": None if line[31:37].strip() == "-999.9" else Decimal(line[31:37].strip()),
        "name": line[41:71].strip(),
    }


def parse_ghcn_inventory_line(line: str) -> dict:
    parts = line.split()
    if len(parts) != 6:
        raise ValueError("Invalid GHCN inventory line")
    return {"source_station_id": parts[0], "element": parts[3], "first_year": int(parts[4]), "last_year": int(parts[5])}


class GhcnDailyProvider:
    def __init__(self, timeout_seconds: int = 30, session=None):
        self.timeout_seconds = timeout_seconds
        self.session = session or requests.Session()

    def _text(self, url: str) -> str:
        response = self.session.get(url, timeout=self.timeout_seconds)
        response.raise_for_status()
        return response.text

    def discover_egypt_stations(self) -> list[dict]:
        inventory: dict[str, dict] = {}
        for line in self._text(f"{GHCN_BASE}/ghcnd-inventory.txt").splitlines():
            if not line.startswith("EG"):
                continue
            item = parse_ghcn_inventory_line(line)
            if item["element"] not in {"TMAX", "TMIN"}:
                continue
            row = inventory.setdefault(item["source_station_id"], {"has_tmax": False, "has_tmin": False, "first_year": 9999, "last_year": 0})
            row[f"has_{item['element'].lower()}"] = True
            row["first_year"] = min(row["first_year"], item["first_year"])
            row["last_year"] = max(row["last_year"], item["last_year"])
        stations = []
        for line in self._text(f"{GHCN_BASE}/ghcnd-stations.txt").splitlines():
            station_id = line[0:11].strip()
            if station_id in inventory:
                stations.append({**parse_ghcn_station_line(line), **inventory[station_id]})
        return stations

    def station_observations(self, station_id: str, start_date: date, end_date: date) -> list[DailyObservation]:
        values: dict[date, dict] = {}
        for line in self._text(f"{GHCN_BASE}/all/{station_id}.dly").splitlines():
            if line[17:21] not in {"TMAX", "TMIN"}:
                continue
            for item in parse_ghcn_dly_line(line):
                if start_date <= item.observation_date <= end_date:
                    row = values.setdefault(item.observation_date, {"max": None, "min": None, "flags": []})
                    if item.temperature_max_c is not None:
                        row["max"] = item.temperature_max_c
                    if item.temperature_min_c is not None:
                        row["min"] = item.temperature_min_c
                    row["flags"].append(item.quality_flags or "")
        return [DailyObservation(station_id, day, row["max"], row["min"], "|".join(row["flags"])) for day, row in sorted(values.items())]


def parse_ghcn_dly_line(line: str) -> list[DailyObservation]:
    """Parse one fixed-width GHCN-Daily line, preserving only measured TMAX/TMIN values.

    GHCN temperatures are tenths of a degree Celsius. Missing -9999 values remain NULL.
    Each returned item carries source measurement, quality, and source flags for auditing.
    """
    if len(line) < 269:
        raise ValueError("Invalid GHCN-Daily line length")
    station, year, month, element = line[:11], int(line[11:15]), int(line[15:17]), line[17:21]
    if element not in {"TMAX", "TMIN"}:
        return []
    result = []
    for day in range(1, 32):
        offset = 21 + (day - 1) * 8
        raw = int(line[offset:offset + 5])
        mflag, qflag, sflag = line[offset + 5:offset + 8]
        try:
            observed = date(year, month, day)
        except ValueError:
            continue
        value = None if raw == -9999 else Decimal(raw) / Decimal(10)
        result.append(DailyObservation(
            station_identifier=station,
            observation_date=observed,
            temperature_max_c=value if element == "TMAX" else None,
            temperature_min_c=value if element == "TMIN" else None,
            quality_flags=f"M={mflag or '-'};Q={qflag or '-'};S={sflag or '-'}",
        ))
    return result

