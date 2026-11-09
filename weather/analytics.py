from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import fmean


@dataclass(frozen=True)
class AccuracyMetrics:
    mae: float
    rmse: float
    bias: float
    sample_count: int


def calculate_metrics(pairs: list[tuple[float | None, float | None]]) -> AccuracyMetrics | None:
    errors = [forecast - actual for forecast, actual in pairs if forecast is not None and actual is not None]
    if not errors:
        return None
    return AccuracyMetrics(
        mae=fmean(abs(error) for error in errors),
        rmse=math.sqrt(fmean(error * error for error in errors)),
        bias=fmean(errors),
        sample_count=len(errors),
    )


def matched_model_samples(rows: list[dict], model_keys: list[str]) -> list[dict]:
    """Keep target/point/lead samples represented by every requested model."""
    groups: dict[tuple, set[str]] = {}
    for row in rows:
        key = (row["sampling_point_id"], row["target_date"], row["lead_days"])
        groups.setdefault(key, set()).add(row["model_slug"])
    valid = {key for key, models in groups.items() if set(model_keys).issubset(models)}
    return [row for row in rows if (row["sampling_point_id"], row["target_date"], row["lead_days"]) in valid]
