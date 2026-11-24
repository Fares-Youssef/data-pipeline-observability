# 10 — Requirements Traceability

This page maps the requirements in
[`project-proposal-and-requirements.pdf`](project-proposal-and-requirements.pdf) to what is implemented in this
repository. Status legend: ✅ implemented · 🟡 partially / adapted · ⏳ not yet implemented.

## 1. Scope decisions taken during implementation

| Proposal | Implementation | Reason |
|---|---|---|
| Hourly forecasts for Cairo and Aswan, then 5 pilot cities | **Daily Tmax/Tmin, D+1…D+10, for up to all 8,994 Egyptian places** (capped by `WEATHER_MAX_LOCATIONS`) | Daily extremes match the available measured ground truth (GHCN-Daily TMAX/TMIN); nationwide geography made broader coverage possible |
| Two providers or clearly labelled models | **Three explicit models**: NOAA GFS, ECMWF IFS, ECMWF AIFS (via Open-Meteo) — labelled as a *model comparison* | Proposal §14: models through an aggregator must be labelled as model comparison, not independent providers |
| NOAA Global Hourly / METAR observations | **NOAA GHCN-Daily** station observations | Daily station TMAX/TMIN directly comparable with daily forecasts |
| PostgreSQL | **MySQL 8 / MariaDB** (spatial), Azure Database for MySQL | Existing spatial geography database; same engine locally and on Azure |
| Prometheus + Grafana | **Azure Log Analytics + Azure Monitor alerts**, DB-backed run metrics, API health endpoints, dashboard panel | Native monitoring for Azure Container Apps |
| Docker Compose | **Single Docker image** + Azure Container Apps; Airflow DAG provided | Cloud deployment target |

## 2. Functional requirements

| ID | Requirement (short) | Status | Where |
|---|---|---|---|
| FR-01 | Maintain locations, stations, source configs, units, licences | ✅ | `geo_*`, `weather_sources`, `weather_models`, `weather_stations` |
| FR-02 | Collect forecasts periodically from ≥ 2 sources/labelled models | ✅ | `collect-forecasts`, 3 models |
| FR-03 | Store immutable issue/valid time, lead, model, raw metadata | ✅ | `weather_forecast_runs`, `weather_daily_forecasts`, Bronze archive |
| FR-04 | Ingest station observations with source, coordinates, QC flags | ✅ | `GhcnDailyProvider`, `weather_daily_observations` |
| FR-05 | Separate multi-year reanalysis dataset for tourism | ⏳ | Planned with the travel module |
| FR-06 | Validate nulls, duplicates, schema/types, ranges, integrity, counts | ✅ | [04 — Data quality](04-data-quality.md) |
| FR-07 | Retain invalid/suspicious records with reason, severity, run id | 🟡 | `weather_quality_issues`, Silver `is_valid`; rejected API payloads are recorded at batch level (error + checkpoint) rather than per record |
| FR-08 | Normalize UTC time and units; prevent mismatched comparisons | ✅ | °C enforced, UTC timestamps, Africa/Cairo days, lead stratification |
| FR-09 | Match forecasts to observations with time and distance tolerance | ✅ | `stations --match`, `apply_alignment_policy` (≤ 50 km, QC passed, same day) |
| FR-10 | Paired MAE, RMSE, bias, N, coverage, lead-specific comparison | ✅ | `/api/weather/accuracy`, Gold `model_accuracy`, Spark job |
| FR-11 | Preserve source versions; reproducible, no look-ahead leakage | ✅ | Immutable snapshot key, revisions, archived raw responses |
| FR-12 | Monthly tourism climate features | ⏳ | Travel module |
| FR-13 | Configurable comfort/rain/wind/stability scores | ⏳ | Travel module |
| FR-14 | Best-month recommendations with factors and limitations | ⏳ | Travel module |
| FR-15 | Export a dated city/month travel report (PDF) | ⏳ | Travel module (a PNG accuracy report exists: `weather_cli.py report`) |
| FR-16 | Schedule ingestion with Airflow and bounded retries | ✅ | `airflow/dags/depi_weather_dag.py` (2 retries, backoff); Azure jobs in production |
| FR-17 | Idempotent, replay-safe processing with run status | ✅ | Natural keys, leases, `weather_pipeline_runs` |
| FR-18 | Expose runtime, freshness, ingested/rejected counts, coverage, quality metrics | ✅ | Run tables, `/api/pipeline/*`, `/api/ready`, `validate` |
| FR-19 | Monitoring panels and alerts on failed/stale runs | 🟡 | Dashboard panel + Azure Monitor alert on job errors; Grafana not used |
| FR-20 | [Optional] Flight route waypoints | ⏳ | Optional, not started |
| FR-21 | [Optional] Aviation weather with disclaimer | ⏳ | Optional, not started |

## 3. Non-functional requirements

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| NFR-01 | Reproducible accuracy from immutable forecasts | ✅ | Snapshot key + versioned SQL/Parquet logic |
| NFR-02 | Replay adds no duplicate rows | ✅ | Second `collect-forecasts` run in deployment verification inserts 0 |
| NFR-03 | Missing station coverage never treated as observed | ✅ | Eligibility policy; "waiting for verified observations" in UI |
| NFR-04 | Demo batch within a time budget | ✅ | `--limit`, `WEATHER_MAX_LOCATIONS` |
| NFR-05 | Secrets out of Git | ✅ | `.env` ignored, `.env.example`, Container Apps secrets, OIDC CI/CD |
| NFR-06 | Run results visible quickly after completion | ✅ | Written to DB at run end; served live by API |
| NFR-07 | Source, data period, sample counts visible | ✅ | Model/distributor panel, N on every metric, station distance |
| NFR-08 | Portable setup | 🟡 | Docker image + documented setup; no Docker Compose file |
| NFR-09 | Aviation disclaimer | n/a | Aviation module not implemented |

## 4. Acceptance tests

| ID | Scenario | Status | How it is satisfied |
|---|---|---|---|
| AT-01 | Provider revises forecast for an existing valid day | ✅ | New run → new rows; earlier snapshot unchanged |
| AT-02 | Joining +24 h with +72 h forecasts | ✅ | All metrics grouped by `lead_days` |
| AT-03 | Missing / too-distant station | ✅ | `incompatible`, excluded from MAE, still counted |
| AT-04 | Invalid value / duplicate key | ✅ | Silver `is_valid=false`, anomaly issues; duplicates counted, not inserted |
| AT-05 | Unequal coverage between providers | 🟡 | N reported per model; common-intersection helper exists (`matched_model_samples`) but not yet applied in Gold |
| AT-06 | Repeat identical ingestion window | ✅ | `inserted: 0` on replay |
| AT-07 | Travel report with missing years | ⏳ | Travel module |
| AT-08 | Temporary API failure | ✅ | Provider retries + Airflow/job retry; run status and alert |
| AT-09 | Export monthly trip report | ⏳ | Travel module |
| AT-10 | Aviation disclaimer | n/a | Optional module not implemented |

## 5. Additions beyond the proposal

- Nationwide Egyptian geography (8,994 Overture polygons) with Arabic/English reverse geocoding.
- Medallion data lake (Bronze/Silver/Gold Parquet) on Azure Data Lake Storage Gen2.
- Azure Synapse serverless SQL views and a PySpark accuracy job.
- Statistical anomaly detection (robust z-score, run-to-run jumps).
- Lease-based concurrency control and crash recovery.
- One-command, re-runnable Azure deployment and GitHub Actions CI/CD.

## 6. Next steps

1. Travel-season module (FR-05, FR-12 – FR-15): Open-Meteo Historical (ERA5, labelled as reanalysis), monthly
   features, configurable scoring and a PDF report for Aswan.
2. Apply the common-sample intersection in Gold for strictly paired model ranking (AT-05).
3. Optional Prometheus exporter + Grafana dashboard over the run tables.
4. Docker Compose for a one-command local stack (MySQL + app + Airflow).
