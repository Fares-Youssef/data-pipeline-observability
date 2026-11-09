"""Automated data-quality and anomaly detection for collected forecasts ("AI for your pipeline").

Two statistical detectors run after each collection:

* Cross-model outlier: robust z-score (median / MAD) of one model against all models for the same
  point, target date and lead. A model far from the consensus is flagged.
* Run-to-run jump: the same model, point and target date changed by more than ``jump_threshold_c``
  between consecutive issue dates (lead N+1 -> lead N). Large jumps usually indicate a bad payload.

Findings are stored as ``warning`` rows in ``weather_quality_issues`` (never deleted, never blocking).
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import numpy as np
import pandas as pd

from geo.config import Settings
from geo.db import cursor

MAD_SCALE = 1.4826  # makes MAD comparable to a standard deviation for normal data


def robust_outliers(df: pd.DataFrame, threshold: float = 3.5, minimum_spread_c: float = 3.0) -> pd.DataFrame:
    if df.empty:
        return df
    keys = ["sampling_point_id", "target_date", "lead_days"]
    grouped = df.groupby(keys)["temperature_max_c"]
    df = df.assign(median=grouped.transform("median"), models=grouped.transform("count"))
    df["abs_dev"] = np.abs(df["temperature_max_c"] - df["median"])
    df["mad"] = df.groupby(keys)["abs_dev"].transform("median") * MAD_SCALE
    # With three models the MAD is often ~0; use a physical floor so tiny differences are not flagged.
    scale = np.maximum(df["mad"].to_numpy(), minimum_spread_c / threshold)
    df["z_score"] = df["abs_dev"] / scale
    return df[(df["models"] >= 3) & (df["z_score"] > threshold) & (df["abs_dev"] >= minimum_spread_c)]


def run_to_run_jumps(df: pd.DataFrame, jump_threshold_c: float = 8.0) -> pd.DataFrame:
    if df.empty:
        return df
    ordered = df.sort_values(["model_slug", "sampling_point_id", "target_date", "lead_days"], ascending=[True, True, True, False])
    ordered = ordered.assign(previous=ordered.groupby(["model_slug", "sampling_point_id", "target_date"])["temperature_max_c"].shift(1))
    ordered["jump_c"] = np.abs(ordered["temperature_max_c"] - ordered["previous"])
    return ordered[ordered["jump_c"] > jump_threshold_c]


def run_quality_checks(settings: Settings, days: int = 3) -> dict:
    since = date.today() - timedelta(days=days)
    with cursor(settings) as (_, cur):
        cur.execute(
            "SELECT f.id forecast_id,m.slug model_slug,f.sampling_point_id,f.target_date,f.lead_days,f.temperature_max_c "
            "FROM weather_daily_forecasts f JOIN weather_forecast_runs r ON r.id=f.forecast_run_id "
            "JOIN weather_models m ON m.id=r.model_id WHERE f.created_at >= %s AND f.temperature_max_c IS NOT NULL",
            (since,),
        )
        rows = cur.fetchall()
    df = pd.DataFrame(rows)
    if df.empty:
        return {"status": "no_recent_forecasts", "checked": 0, "outliers": 0, "jumps": 0, "issues_recorded": 0}
    df["temperature_max_c"] = pd.to_numeric(df["temperature_max_c"]).astype("float64")
    outliers = robust_outliers(df)
    jumps = run_to_run_jumps(df)
    issues = [("cross_model_outlier", int(r.forecast_id),
               f"{r.model_slug} Tmax {r.temperature_max_c:.1f}C is {r.abs_dev:.1f}C from the model median",
               {"z_score": round(float(r.z_score), 2), "median_c": float(r.median)})
              for r in outliers.itertuples()]
    issues += [("run_to_run_jump", int(r.forecast_id),
                f"{r.model_slug} Tmax changed {r.jump_c:.1f}C between consecutive runs",
                {"previous_c": float(r.previous), "current_c": float(r.temperature_max_c)})
               for r in jumps.itertuples()]
    recorded = 0
    with cursor(settings) as (connection, cur):
        for code, entity_id, message, details in issues:
            cur.execute("SELECT id FROM weather_quality_issues WHERE entity_type='weather_daily_forecast' "
                        "AND entity_id=%s AND issue_code=%s", (entity_id, code))
            if cur.fetchone():
                continue
            cur.execute("INSERT INTO weather_quality_issues(entity_type,entity_id,issue_code,severity,message,details_json) "
                        "VALUES('weather_daily_forecast',%s,%s,'warning',%s,%s)",
                        (entity_id, code, message, json.dumps(details)))
            recorded += 1
        connection.commit()
    return {"status": "complete", "checked": int(len(df)), "outliers": int(len(outliers)), "jumps": int(len(jumps)),
            "issues_recorded": recorded}
