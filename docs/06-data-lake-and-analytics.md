# 06 — Data Lake & Analytics

## 1. Medallion layout

`weather_cli.py export-lake` ([`weather/lake.py`](../weather/lake.py)) writes Parquet (Snappy) to a local folder
(`LAKE_ROOT`, default `lake/`) or, when `AZURE_STORAGE_CONNECTION_STRING` is set, to the Azure Data Lake container
`AZURE_LAKE_CONTAINER` (default `lake`).

```
Bronze  (weather-raw container / raw-weather/)
└── forecasts/YYYY/MM/DD/<model>/<sha256>.json.gz      raw provider responses, append-only

Silver  (cleaned, typed, conformed)
├── dim_location/dim_location.parquet                 polygons + governorate + sampling point
├── dim_model/dim_model.parquet
├── dim_station/dim_station.parquet
├── fact_forecast/issue_date=YYYY-MM-DD/part-0.parquet
└── fact_observation/observation_month=YYYY-MM/part-0.parquet

Gold  (business-ready)
├── forecast_error/issue_date=YYYY-MM-DD/part-0.parquet   one row per eligible forecast/observation pair
├── model_accuracy/model_accuracy.parquet                 MAE / RMSE / bias per model × lead × governorate
├── model_disagreement/issue_date=YYYY-MM-DD/part-0.parquet  spread between models
├── spark_model_accuracy/lead_days=N/…                    (PySpark job)
└── spark_governorate_ranking/…                           (PySpark job)
```

| Layer | Contents | Rules |
|---|---|---|
| **Bronze** | Exact bytes returned by the API, gzip, file name = SHA-256 | Immutable, content-addressed, `overwrite=False` |
| **Silver** | Star schema: 3 dimensions + 2 facts | Typed columns, `issue_date` derived, `is_valid` flag, governorate attached |
| **Gold** | Errors, accuracy, disagreement | Only `is_valid` forecasts and eligible (`verified`/`compatible`) observations |

**Idempotent partitions.** Forecasts are selected by *issue date* (`target_date − lead_days ≥ since`), so every
`issue_date` partition written is complete; observations are re-read from the first day of the month so whole
monthly partitions are rewritten. Re-running the export produces the same files and never truncates older
partitions.

**Governorate attribution.** `governorate_lookup` walks the polygon parent hierarchy (memoised) to find each unit's
level-3 ancestor, so every forecast can be aggregated by governorate.

## 2. Accuracy methodology

For each model *m*, lead *h* and governorate *g*, using only matched pairs (forecast *f*, observation *o*):

| Metric | Formula | Interpretation |
|---|---|---|
| MAE | mean(\|f − o\|) | Typical error size in °C (lower is better) |
| RMSE | √mean((f − o)²) | Penalises large errors more |
| Bias | mean(f − o) | > 0 forecasts too warm, < 0 too cold |
| N | number of pairs | Always reported next to the score |

Computed for both **Tmax** and **Tmin**; `rank_in_lead` ranks models by Tmax MAE within each lead and governorate.

Fair-comparison rules:
- Leads are **never mixed** — scores are stratified by `lead_days` (1…10).
- `N` is reported with every score so models with different coverage are not compared blindly. A helper,
  `analytics.matched_model_samples`, restricts a comparison to the samples every model has (common intersection);
  it is unit-tested but not yet applied inside the Gold aggregation — a planned improvement for strict paired
  ranking.
- The same scoring is available live from MySQL (`GET /api/weather/accuracy`), in Pandas (Gold), in PySpark and in
  Synapse SQL — all from the same eligibility rules.

**Model disagreement** (`gold/model_disagreement`): for each point/target/lead with ≥ 2 models,
`spread_max_c = max − min` across models. A large spread means low forecast confidence — useful even before
observations arrive.

## 3. Azure Synapse serverless SQL

Scripts in [`synapse/`](../synapse):

- `01_serverless_lake_views.sql` creates database `depi_lake`, a managed-identity credential, an external data source
  on the ADLS Gen2 `lake` container, and views `dim_location`, `dim_model`, `fact_forecast`, `fact_observation`,
  `model_accuracy`, `model_disagreement` using `OPENROWSET` (partition column read from the file path).
- `02_analysis_queries.sql` contains example analyses:

```sql
-- Best model per governorate and lead
SELECT governorate, lead_days, model_slug, samples, mae_max_c
FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY governorate, lead_days ORDER BY mae_max_c) AS rn
      FROM dbo.model_accuracy) ranked
WHERE rn = 1;

-- How the forecast for one target day evolved run by run
SELECT model_slug, sampling_point_id, target_date, lead_days, temperature_max_c,
       temperature_max_c - LAG(temperature_max_c) OVER (
           PARTITION BY model_slug, sampling_point_id, target_date ORDER BY lead_days DESC) AS change_vs_previous_run
FROM dbo.fact_forecast WHERE governorate = 'Cairo';
```

Serverless SQL bills per data scanned, so queries filter on the `issue_date` partition.

## 4. PySpark job

[`spark/accuracy_job.py`](../spark/accuracy_job.py) recomputes Gold accuracy from Silver/Gold at scale and picks the
best model per governorate and lead with a window function (`rank() over (partition by governorate, lead_days
order by mae_max_c)`).

```bash
# Synapse Spark pool
spark-submit spark/accuracy_job.py --lake abfss://lake@<storage-account>.dfs.core.windows.net
# Local (Java 17 + pip install pyspark)
spark-submit spark/accuracy_job.py --lake lake
```

Outputs `gold/spark_model_accuracy` (partitioned by `lead_days`) and `gold/spark_governorate_ranking`.

## 5. Static report

`weather_cli.py report` ([`weather/report.py`](../weather/report.py)) renders headless Matplotlib/Seaborn charts into
`reports/<date>/`:

| Chart | Source |
|---|---|
| `collection_volume.png` — forecast records per day per model | MySQL |
| `mae_by_lead.png` — sample-weighted Tmax MAE vs lead time per model | Gold `model_accuracy` |
| `model_disagreement.png` — box plot of Tmax spread per lead | Gold `model_disagreement` |

## 6. Honest-results note

Forecasts are collected **prospectively** (the free API does not provide a full archive of past runs for every
point), so accuracy numbers only exist for days after collection started *and* for which an eligible station
observation has been published by NOAA. Until then, the API and dashboard say "waiting for eligible observations"
instead of showing invented numbers.
