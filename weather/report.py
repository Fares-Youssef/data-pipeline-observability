"""Static accuracy/coverage report built from the Gold layer with Matplotlib and Seaborn."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless: works in containers and scheduled jobs
import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns

from geo.config import Settings
from geo.db import cursor


def _frame(settings: Settings, sql: str) -> pd.DataFrame:
    with cursor(settings) as (_, cur):
        cur.execute(sql)
        return pd.DataFrame(cur.fetchall())


def build_report(settings: Settings, lake_root: Path | None = None, output_dir: Path | None = None) -> dict:
    lake_root = lake_root or Path(settings.lake_root)
    output_dir = output_dir or Path("reports") / date.today().isoformat()
    output_dir.mkdir(parents=True, exist_ok=True)
    sns.set_theme(style="whitegrid")
    charts = []

    volume = _frame(settings, "SELECT DATE(r.collected_at_utc) collected_on,m.slug model_slug,COUNT(*) records "
                              "FROM weather_daily_forecasts f JOIN weather_forecast_runs r ON r.id=f.forecast_run_id "
                              "JOIN weather_models m ON m.id=r.model_id GROUP BY collected_on,m.slug ORDER BY collected_on")
    if not volume.empty:
        fig, ax = plt.subplots(figsize=(9, 4))
        sns.barplot(data=volume, x="collected_on", y="records", hue="model_slug", ax=ax)
        ax.set_title("Forecast records collected per day")
        ax.tick_params(axis="x", rotation=45)
        fig.tight_layout()
        path = output_dir / "collection_volume.png"
        fig.savefig(path, dpi=120)
        plt.close(fig)
        charts.append(str(path))

    accuracy_file = lake_root / "gold" / "model_accuracy" / "model_accuracy.parquet"
    accuracy = pd.read_parquet(accuracy_file) if accuracy_file.exists() else pd.DataFrame()
    if not accuracy.empty:
        overall = (accuracy.assign(weighted=accuracy["mae_max_c"] * accuracy["samples"])
                   .groupby(["model_slug", "lead_days"]).agg(weighted=("weighted", "sum"), samples=("samples", "sum"))
                   .reset_index())
        overall["mae_max_c"] = overall["weighted"] / overall["samples"]
        fig, ax = plt.subplots(figsize=(8, 4))
        sns.lineplot(data=overall, x="lead_days", y="mae_max_c", hue="model_slug", marker="o", ax=ax)
        ax.set_title("Tmax mean absolute error by lead time")
        ax.set_xlabel("Lead (days)")
        ax.set_ylabel("MAE (°C)")
        fig.tight_layout()
        path = output_dir / "mae_by_lead.png"
        fig.savefig(path, dpi=120)
        plt.close(fig)
        charts.append(str(path))

    spread_dir = lake_root / "gold" / "model_disagreement"
    if spread_dir.exists():
        spread = pd.read_parquet(spread_dir)
        if not spread.empty:
            fig, ax = plt.subplots(figsize=(8, 4))
            sns.boxplot(data=spread, x="lead_days", y="spread_max_c", ax=ax, color="#7aa6c2", fliersize=1)
            ax.set_title("Disagreement between models (Tmax spread)")
            ax.set_xlabel("Lead (days)")
            ax.set_ylabel("Max - min across models (°C)")
            fig.tight_layout()
            path = output_dir / "model_disagreement.png"
            fig.savefig(path, dpi=120)
            plt.close(fig)
            charts.append(str(path))
    return {"status": "complete", "charts": charts, "accuracy_rows": int(len(accuracy))}
