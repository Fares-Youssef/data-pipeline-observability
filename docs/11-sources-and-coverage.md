# 11 — Data Sources, Licensing & Coverage

## 1. Forecast models

Verified on 31 October 2026 with live Cairo requests:

| Model | Explicit Open-Meteo identifier | Endpoint | Resolution | Origin | Returned horizon |
|---|---|---|---|---|---|
| NOAA GFS Global | `gfs_global` | `/v1/gfs` | ~13 km | NOAA/NCEP | D0–D+10 requested |
| ECMWF IFS HRES | `ecmwf_ifs` | `/v1/ecmwf` | ~9 km | ECMWF | D0–D+10 requested |
| ECMWF AIFS 0.25° Single | `ecmwf_aifs025_single` | `/v1/ecmwf` | ~28 km | ECMWF | D0–D+10 requested |

- D0 is requested but only **D+1 … D+10** are stored (the standard comparison horizon).
- Variables: `temperature_2m_max`, `temperature_2m_min`, Celsius, `timezone=Africa/Cairo`, `cell_selection=land`.
- **Open-Meteo is recorded as the distributor, never as the originating meteorological organization.**
- The free API is non-commercial, documents daily call limits, has no uptime guarantee and supports multi-coordinate
  requests. A customer key (`OPEN_METEO_API_KEY`) switches to the customer endpoint with identical behaviour.
- Archived runs can be collected through the Single Runs API with `collect-forecasts --run <ISO-UTC>`.

Because all three models are accessed through one aggregator, results are presented as a **model comparison**, not as
independent commercial-provider measurements.

## 2. Observations

| Source | What | Notes |
|---|---|---|
| NOAA NCEI **GHCN-Daily** | Daily station TMAX/TMIN for Egypt | Discovered from `ghcnd-inventory.txt` / `ghcnd-stations.txt`; values in tenths of °C; QC flags preserved; publication lag of days to weeks |

Observation coverage is intentionally independent of forecast coverage. Missing actuals remain unavailable — they are
never replaced with model or reanalysis values. A station may be many kilometres from a neighbourhood; its distance
is shown and only stations within 50 km are used for scoring.

## 3. Geography

| Item | Value |
|---|---|
| Source | Overture Maps **Divisions** theme, release `2026-10-21.0` |
| Egypt polygons imported | **8,994** |
| Verified source parent links | 8,993 |
| Repeated-import result | `inserted=0`, `changed=0` |

| Overture subtype | Polygons |
|---|---:|
| country | 1 |
| region (governorate) | 28 |
| county (district / markaz) | 365 |
| locality (city) | 493 |
| macrohood | 32 |
| neighborhood | 7,984 |
| microhood | 91 |
| **Total** | **8,994** |

Spot checks against stored polygons and source hierarchy (no coordinate-to-name mapping in code):

| Target | Stored hierarchy |
|---|---|
| أرض اللواء | Egypt → Aj Jiza → Kardasa → Ard Al Lwaa' / ارض اللواء |
| المهندسين | Egypt → Aj Jiza → Al-Aguza → Al Mohandessin / المهندسين |

`locations --sync` deduplicates identical representative points and maps every eligible unit to one sampling point.

Known limitations:
- Coverage depends on what OpenStreetMap/Overture publishes; a missing polygon stays missing.
- Point-only Places records are never converted into boundaries.
- Some names exist in only one language.
- Administrative and cultural neighbourhood polygons may overlap; lookup follows the verified hierarchy and prefers
  the most specific polygon.

A legacy importer for **geoBoundaries gbOpen** (`scripts/import_geoboundaries.py`) is kept as an alternative source.

## 4. Licensing & attribution

| Source | Licence / terms |
|---|---|
| Open-Meteo | [Open-Meteo terms](https://open-meteo.com/en/terms) (free tier non-commercial) |
| NOAA GFS | Public NOAA data |
| ECMWF IFS / AIFS open data | CC-BY 4.0 |
| NOAA GHCN-Daily | Public NOAA/NCEI data — review source terms |
| Overture Maps Divisions | ODbL (OpenStreetMap-derived) — attribution required |
| geoBoundaries | Per-layer licences (stored in `geo_source.metadata_json`) |

Each import stores the licence, source URL, release and metadata in `geo_source` / `weather_sources`. Review the
upstream licence before redistributing data.

## 5. References

1. Open-Meteo Forecast API — <https://open-meteo.com/en/docs>
2. Open-Meteo Single Runs API — <https://open-meteo.com/en/docs/single-runs-api>
3. NOAA NCEI GHCN-Daily — <https://www.ncei.noaa.gov/products/land-based-station/global-historical-climatology-network-daily>
4. Overture Maps documentation — <https://docs.overturemaps.org/>
5. Apache Airflow DAGs — <https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/dags.html>
6. Azure Container Apps jobs — <https://learn.microsoft.com/azure/container-apps/jobs>
7. Azure Synapse serverless SQL — <https://learn.microsoft.com/azure/synapse-analytics/sql/on-demand-workspace-overview>
