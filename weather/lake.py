"""Medallion data lake export (Bronze -> Silver -> Gold) using Pandas, NumPy and Parquet.

Bronze : raw provider responses, already archived by ``RawResponseArchive`` (gzip JSON, append-only).
Silver : cleaned, typed, conformed tables extracted from MySQL (one Parquet file per partition).
Gold   : business-ready aggregates: forecast errors, model accuracy, and model disagreement.

Layout (local folder ``LAKE_ROOT`` and, when configured, the Azure Data Lake container ``AZURE_LAKE_CONTAINER``)::

    silver/dim_location/dim_location.parquet
    silver/dim_model/dim_model.parquet
    silver/dim_station/dim_station.parquet
    silver/fact_forecast/issue_date=YYYY-MM-DD/part-0.parquet
    silver/fact_observation/observation_month=YYYY-MM/part-0.parquet
    gold/forecast_error/issue_date=YYYY-MM-DD/part-0.parquet
    gold/model_accuracy/model_accuracy.parquet          (rolling window, default 30 days)
    gold/model_disagreement/issue_date=YYYY-MM-DD/part-0.parquet

Only complete partitions inside the window are rewritten (filtering is by issue date), so re-running is
idempotent and older partitions are never truncated. Observation partitions are monthly and the window
reaches back far enough to rewrite whole months that it touches.
"""
from __future__ import annotations

import io
import logging
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from geo.config import Settings
from geo.db import cursor

LOGGER = logging.getLogger(__name__)
GOVERNORATE_LEVEL = 3


def _frame(settings: Settings, sql: str, params: tuple = ()) -> pd.DataFrame:
    with cursor(settings) as (_, cur):
        cur.execute(sql, params)
        rows = cur.fetchall()
    return pd.DataFrame(rows)


def governorate_lookup(units: pd.DataFrame) -> dict[int, int | None]:
    """Walk the parent hierarchy once per unit (memoised) to find its level-3 governorate ancestor."""
    parent = dict(zip(units["id"], units["parent_id"]))
    level = dict(zip(units["id"], units["admin_level"]))
    memo: dict[int, int | None] = {}

    def resolve(unit_id: int) -> int | None:
        path = []
        current = unit_id
        while current is not None and not pd.isna(current) and current not in memo:
            path.append(current)
            if level.get(current) == GOVERNORATE_LEVEL:
                memo[current] = current
                break
            current = parent.get(current)
            current = None if current is None or pd.isna(current) else int(current)
        answer = memo.get(current) if current is not None and not pd.isna(current) else None
        for item in path:
            memo.setdefault(item, answer)
        return memo.get(unit_id)

    return {int(unit_id): resolve(int(unit_id)) for unit_id in units["id"]}


def clean_forecasts(df: pd.DataFrame) -> pd.DataFrame:
    """Silver rules: typed columns, physically plausible values, tmax >= tmin, derived issue date."""
    if df.empty:
        return df
    df = df.copy()
    for column in ("temperature_max_c", "temperature_min_c", "latitude", "longitude"):
        df[column] = pd.to_numeric(df[column], errors="coerce").astype("float64")
    df["target_date"] = pd.to_datetime(df["target_date"]).dt.date
    df["lead_days"] = df["lead_days"].astype("int16")
    df["issue_date"] = (pd.to_datetime(df["target_date"]) - pd.to_timedelta(df["lead_days"], unit="D")).dt.date
    plausible = df["temperature_max_c"].between(-30, 60) & df["temperature_min_c"].between(-30, 60)
    ordered = df["temperature_max_c"] >= df["temperature_min_c"]
    df["is_valid"] = plausible & ordered
    return df


def forecast_errors(forecasts: pd.DataFrame, observations: pd.DataFrame, matches: pd.DataFrame) -> pd.DataFrame:
    """Join forecasts to eligible station observations through the primary station match."""
    if forecasts.empty or observations.empty or matches.empty:
        return pd.DataFrame()
    eligible = matches[matches["temporal_alignment"].isin(["verified", "compatible"])]
    obs = observations[observations["temporal_alignment"].isin(["verified", "compatible"])]
    joined = (forecasts[forecasts["is_valid"]]
              .merge(eligible[["sampling_point_id", "station_id", "distance_km"]], on="sampling_point_id")
              .merge(obs[["station_id", "observation_date", "obs_max_c", "obs_min_c"]],
                     left_on=["station_id", "target_date"], right_on=["station_id", "observation_date"]))
    if joined.empty:
        return joined
    joined["error_max_c"] = joined["temperature_max_c"] - joined["obs_max_c"]
    joined["error_min_c"] = joined["temperature_min_c"] - joined["obs_min_c"]
    joined["abs_error_max_c"] = np.abs(joined["error_max_c"])
    joined["abs_error_min_c"] = np.abs(joined["error_min_c"])
    return joined.dropna(subset=["error_max_c", "error_min_c"])


def model_accuracy(errors: pd.DataFrame) -> pd.DataFrame:
    if errors.empty:
        return pd.DataFrame(columns=["model_slug", "lead_days", "governorate", "samples", "mae_max_c", "rmse_max_c",
                                     "bias_max_c", "mae_min_c", "rmse_min_c", "bias_min_c"])
    grouped = errors.groupby(["model_slug", "lead_days", "governorate"], dropna=False)
    result = grouped.agg(
        samples=("error_max_c", "size"),
        mae_max_c=("abs_error_max_c", "mean"),
        rmse_max_c=("error_max_c", lambda s: float(np.sqrt(np.mean(np.square(s))))),
        bias_max_c=("error_max_c", "mean"),
        mae_min_c=("abs_error_min_c", "mean"),
        rmse_min_c=("error_min_c", lambda s: float(np.sqrt(np.mean(np.square(s))))),
        bias_min_c=("error_min_c", "mean"),
    ).reset_index()
    result["rank_in_lead"] = result.groupby(["lead_days", "governorate"], dropna=False)["mae_max_c"].rank(method="min")
    return result


def model_disagreement(forecasts: pd.DataFrame) -> pd.DataFrame:
    """Spread between models for the same point/target/lead; large spread = low forecast confidence."""
    valid = forecasts[forecasts["is_valid"]] if not forecasts.empty else forecasts
    if valid.empty:
        return pd.DataFrame()
    pivot = valid.pivot_table(index=["sampling_point_id", "issue_date", "target_date", "lead_days"],
                              columns="model_slug", values="temperature_max_c", aggfunc="first")
    out = pd.DataFrame(index=pivot.index)
    out["models"] = pivot.notna().sum(axis=1)
    out["spread_max_c"] = pivot.max(axis=1) - pivot.min(axis=1)
    out["mean_max_c"] = pivot.mean(axis=1)
    return out[out["models"] >= 2].reset_index()


class LakeWriter:
    def __init__(self, settings: Settings, root: Path | None = None):
        self.settings = settings
        self.root = root or Path(settings.lake_root)
        self._container = None
        if settings.azure_storage_connection_string:
            from azure.storage.blob import BlobServiceClient

            service = BlobServiceClient.from_connection_string(settings.azure_storage_connection_string)
            self._container = service.get_container_client(settings.azure_lake_container)
            if not self._container.exists():
                self._container.create_container()

    def write(self, df: pd.DataFrame, relative_path: str) -> str:
        buffer = io.BytesIO()
        df.to_parquet(buffer, index=False, engine="pyarrow", compression="snappy")
        payload = buffer.getvalue()
        if self._container is not None:
            self._container.upload_blob(relative_path, payload, overwrite=True)
            return f"azure://{self.settings.azure_lake_container}/{relative_path}"
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return str(path)

    def write_partitioned(self, df: pd.DataFrame, table: str, column: str, fmt=str) -> list[str]:
        written = []
        if df.empty:
            return written
        for value, part in df.groupby(column):
            written.append(self.write(part.drop(columns=[column]), f"{table}/{column}={fmt(value)}/part-0.parquet"))
        return written


def export_lake(settings: Settings, days: int = 30, writer: LakeWriter | None = None) -> dict:
    writer = writer or LakeWriter(settings)
    since = date.today() - timedelta(days=days)

    units = _frame(settings, "SELECT u.id,u.parent_id,u.admin_level,u.admin_type,u.name,u.name_local,u.iso_code,"
                             "m.sampling_point_id FROM geo_unit u LEFT JOIN weather_geo_unit_points m "
                             "ON m.geo_unit_id=u.id AND m.role='primary' AND m.active=1 WHERE u.country_iso3='EGY'")
    governorates = governorate_lookup(units)
    names = dict(zip(units["id"], units["name"]))
    units["governorate_id"] = units["id"].map(governorates)
    units["governorate"] = units["governorate_id"].map(names)
    dim_location = units.drop(columns=["parent_id"])
    point_governorate = (units.dropna(subset=["sampling_point_id"]).sort_values("admin_level")
                         .drop_duplicates("sampling_point_id")[["sampling_point_id", "governorate"]])

    dim_model = _frame(settings, "SELECT id model_id,slug model_slug,display_name,organization,provider_model_id,"
                                 "grid_resolution_km FROM weather_models")
    dim_station = _frame(settings, "SELECT id station_id,source_station_id,name,latitude,longitude,elevation_m,"
                                   "first_observation_date,last_observation_date FROM weather_stations")
    forecasts = _frame(settings,
                       "SELECT f.id forecast_id,m.slug model_slug,r.source_run_key,r.run_kind,f.sampling_point_id,"
                       "f.target_date,f.lead_days,f.temperature_max_c,f.temperature_min_c,p.latitude,p.longitude "
                       "FROM weather_daily_forecasts f JOIN weather_forecast_runs r ON r.id=f.forecast_run_id "
                       "JOIN weather_models m ON m.id=r.model_id JOIN weather_sampling_points p ON p.id=f.sampling_point_id "
                       # Filter on issue date so every issue_date partition written is complete.
                       "WHERE DATE_SUB(f.target_date, INTERVAL f.lead_days DAY) >= %s", (since,))
    forecasts = clean_forecasts(forecasts)
    if not forecasts.empty:
        forecasts = forecasts.merge(point_governorate, on="sampling_point_id", how="left")
    observations = _frame(settings,
                          "SELECT station_id,observation_date,temperature_max_c obs_max_c,temperature_min_c obs_min_c,"
                          "quality_flags,temporal_alignment FROM weather_daily_observations "
                          "WHERE is_current=1 AND observation_date >= %s", (since.replace(day=1),))
    if not observations.empty:
        observations["observation_date"] = pd.to_datetime(observations["observation_date"]).dt.date
        for column in ("obs_max_c", "obs_min_c"):
            observations[column] = pd.to_numeric(observations[column], errors="coerce").astype("float64")
        observations["observation_month"] = pd.to_datetime(observations["observation_date"]).dt.strftime("%Y-%m")
    matches = _frame(settings, "SELECT station_id,sampling_point_id,distance_km,temporal_alignment "
                               "FROM weather_station_point_matches WHERE is_primary=1")
    if not matches.empty:
        matches["distance_km"] = pd.to_numeric(matches["distance_km"]).astype("float64")

    errors = forecast_errors(forecasts, observations, matches)
    accuracy = model_accuracy(errors)
    disagreement = model_disagreement(forecasts)

    files = [writer.write(dim_location, "silver/dim_location/dim_location.parquet"),
             writer.write(dim_model, "silver/dim_model/dim_model.parquet"),
             writer.write(dim_station, "silver/dim_station/dim_station.parquet"),
             writer.write(accuracy, "gold/model_accuracy/model_accuracy.parquet")]
    files += writer.write_partitioned(forecasts, "silver/fact_forecast", "issue_date")
    files += writer.write_partitioned(observations, "silver/fact_observation", "observation_month")
    if not errors.empty:
        files += writer.write_partitioned(errors, "gold/forecast_error", "issue_date")
    files += writer.write_partitioned(disagreement, "gold/model_disagreement", "issue_date")
    return {
        "status": "complete", "window_days": days, "files_written": len(files),
        "silver_forecast_rows": int(len(forecasts)), "invalid_forecast_rows": int((~forecasts["is_valid"]).sum()) if not forecasts.empty else 0,
        "silver_observation_rows": int(len(observations)), "gold_error_rows": int(len(errors)),
        "gold_accuracy_rows": int(len(accuracy)), "gold_disagreement_rows": int(len(disagreement)),
        "destination": f"azure://{settings.azure_lake_container}" if settings.azure_storage_connection_string else str(writer.root),
    }
