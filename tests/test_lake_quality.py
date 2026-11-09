from datetime import date

import pandas as pd
import pytest

from weather.lake import clean_forecasts, forecast_errors, governorate_lookup, model_accuracy, model_disagreement
from weather.quality import robust_outliers, run_to_run_jumps


def forecasts_frame():
    rows = []
    for model, tmax in (("gfs_global", 30.0), ("ecmwf_ifs", 31.0), ("ecmwf_aifs025_single", 29.0)):
        for lead in (1, 2):
            rows.append({"model_slug": model, "sampling_point_id": 1, "target_date": date(2026, 11, 10), "lead_days": lead,
                         "temperature_max_c": tmax + lead, "temperature_min_c": 18.0, "latitude": 30.0, "longitude": 31.0})
    rows.append({"model_slug": "gfs_global", "sampling_point_id": 2, "target_date": date(2026, 11, 10), "lead_days": 1,
                 "temperature_max_c": 10.0, "temperature_min_c": 20.0, "latitude": 31.0, "longitude": 30.0})
    return pd.DataFrame(rows)


def test_clean_forecasts_derives_issue_date_and_flags_invalid_rows():
    df = clean_forecasts(forecasts_frame())
    assert df.loc[df["lead_days"] == 2, "issue_date"].iloc[0] == date(2026, 11, 8)
    assert df["is_valid"].sum() == 6  # point 2 has tmax < tmin


def test_governorate_lookup_walks_hierarchy():
    units = pd.DataFrame({"id": [1, 2, 3, 4], "parent_id": [None, 1, 2, 3], "admin_level": [0, 3, 5, 10]})
    assert governorate_lookup(units) == {1: None, 2: 2, 3: 2, 4: 2}


def test_errors_and_accuracy_use_only_eligible_observations():
    forecasts = clean_forecasts(forecasts_frame()).assign(governorate="Cairo")
    observations = pd.DataFrame({"station_id": [7], "observation_date": [date(2026, 11, 10)], "obs_max_c": [31.0],
                                 "obs_min_c": [17.0], "temporal_alignment": ["compatible"]})
    matches = pd.DataFrame({"station_id": [7], "sampling_point_id": [1], "distance_km": [10.0],
                            "temporal_alignment": ["compatible"]})
    errors = forecast_errors(forecasts, observations, matches)
    assert len(errors) == 6
    accuracy = model_accuracy(errors)
    ifs_lead1 = accuracy[(accuracy.model_slug == "ecmwf_ifs") & (accuracy.lead_days == 1)].iloc[0]
    assert ifs_lead1["mae_max_c"] == pytest.approx(1.0)
    assert ifs_lead1["bias_min_c"] == pytest.approx(1.0)
    matches["temporal_alignment"] = "uncertain"
    assert forecast_errors(forecasts, observations, matches).empty


def test_model_disagreement_spread():
    spread = model_disagreement(clean_forecasts(forecasts_frame()))
    assert set(spread["spread_max_c"]) == {2.0}


def test_robust_outlier_and_jump_detection():
    df = pd.DataFrame({"forecast_id": [1, 2, 3], "model_slug": ["a", "b", "c"], "sampling_point_id": [1, 1, 1],
                       "target_date": [date(2026, 11, 10)] * 3, "lead_days": [1, 1, 1],
                       "temperature_max_c": [30.0, 30.5, 45.0]})
    outliers = robust_outliers(df)
    assert list(outliers["forecast_id"]) == [3]
    jumps = pd.DataFrame({"forecast_id": [1, 2], "model_slug": ["a", "a"], "sampling_point_id": [1, 1],
                          "target_date": [date(2026, 11, 10)] * 2, "lead_days": [2, 1], "temperature_max_c": [30.0, 40.0]})
    assert list(run_to_run_jumps(jumps)["forecast_id"]) == [2]
