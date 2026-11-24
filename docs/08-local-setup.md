# 08 — Local Setup (Windows)

Commands assume PowerShell opened in the project folder.

## 1. Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Python | 3.13 | `py --version` |
| MySQL or MariaDB | MySQL 8 / MariaDB 10.6+ | e.g. XAMPP; spatial functions required |
| Git | any | |
| Docker Desktop | optional | only to build the image locally |
| Java 17 + PySpark | optional | only for the local Spark job |
| Azure CLI | optional | only for deployment |

## 2. Database account

Start MySQL/MariaDB, then create a dedicated, least-privilege account:

```sql
CREATE DATABASE IF NOT EXISTS depi CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'depi_app'@'localhost' IDENTIFIED BY 'choose-a-strong-password';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, REFERENCES ON depi.* TO 'depi_app'@'localhost';
FLUSH PRIVILEGES;
```

(`DELETE` is needed only by `import_geography.py --reset`.)

## 3. Python environment

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` — at minimum `DB_USER` and `DB_PASSWORD`. `.env` is git-ignored.

## 4. Configuration reference

| Variable | Default | Purpose |
|---|---|---|
| `DB_HOST`, `DB_PORT`, `DB_NAME` | `127.0.0.1`, `3306`, `depi` | Database location |
| `DB_USER`, `DB_PASSWORD` | — | Database account (required) |
| `DB_SSL_CA`, `DB_SSL_VERIFY_CERT` | empty, `false` | TLS (set for Azure MySQL) |
| `FLASK_HOST`, `FLASK_PORT`, `FLASK_DEBUG` | `127.0.0.1`, `5000`, `false` | Local web server |
| `REVERSE_DIAGNOSTICS` | `false` | Adds lookup diagnostics to `/api/reverse` |
| `OPEN_METEO_API_KEY` | empty | Optional customer key (switches to the customer endpoint) |
| `WEATHER_BATCH_SIZE` | `25` | Points per API request |
| `WEATHER_REQUEST_INTERVAL` | `3` | Minimum seconds between requests |
| `WEATHER_RATE_LIMIT_BACKOFF` | `30` | Base wait after HTTP 429 |
| `WEATHER_REQUEST_TIMEOUT` | `30` | HTTP timeout (s) |
| `WEATHER_MAX_LOCATIONS` | `0` (no cap) | Cap on sampling points per run |
| `WEATHER_ALIGNMENT_DISTANCE_KM` | `50` | Max station distance for scoring |
| `LAKE_ROOT` | `lake` | Local data-lake folder |
| `AZURE_STORAGE_CONNECTION_STRING` | empty | When set, Bronze/Silver/Gold go to Azure Storage |
| `AZURE_STORAGE_CONTAINER` / `AZURE_LAKE_CONTAINER` | `weather-raw` / `lake` | Containers |
| `LOG_LEVEL` | `INFO` | Logging level |

## 5. Schema and geography

```powershell
python scripts\audit_db.py | Tee-Object audit-before.json   # optional: snapshot existing tables first
python weather_cli.py init-db                               # apply migrations 001–004 (idempotent)
python scripts\import_geography.py --reset                  # Egypt polygons from Overture Maps
```

- `--reset` clears only the four geography tables; later refreshes need no reset
  (`python scripts\import_geography.py` or `--refresh`). Re-running an unchanged release reports `inserted=0`.
- Downloads stream to `data\downloads`, retry transient errors and resume partial files.
- Optional: `python scripts\import_world.py` imports the complete global divisions theme (several GB).
- Legacy alternative source: `scripts\import_geoboundaries.py` (geoBoundaries gbOpen, coarser for Egypt).

Verify:

```powershell
python scripts\coverage_report.py | Tee-Object coverage.json
python scripts\verify_egypt_detail.py
```

## 6. Run the pipeline

```powershell
python weather_cli.py locations --sync          # polygons → sampling points
python weather_cli.py locations --status
python weather_cli.py stations --discover       # GHCN-Daily stations in Egypt
python weather_cli.py stations --match          # nearest station per point
python weather_cli.py collect-forecasts --limit 5 --dry-run
python weather_cli.py collect-forecasts --limit 5
python weather_cli.py collect-actuals --limit 5
python weather_cli.py backfill-actuals --lookback-days 45
python weather_cli.py stations --align          # eligibility policy
python weather_cli.py quality-check
python weather_cli.py export-lake
python weather_cli.py report
python weather_cli.py validate
python weather_cli.py status
```

Or the full scheduled flow on demand: `python weather_cli.py run-batch --force --limit 25`.

> Keep `--limit` small locally. The free Open-Meteo tier is non-commercial and rate-limited; a full nationwide run
> is ~9,000 points × 3 models.

## 7. Run the app

```powershell
python app.py
```

Open <http://127.0.0.1:5000>. Quick checks:

```powershell
Invoke-RestMethod http://127.0.0.1:5000/api/health
Invoke-RestMethod 'http://127.0.0.1:5000/api/reverse?lat=30.0444&lon=31.2357'
```

Manual spot checks: Cairo `(30.0444, 31.2357)`, Giza `(30.0131, 31.2089)`, Alexandria `(31.2001, 29.9187)`,
Aswan `(24.0889, 32.8998)`, Marsa Matrouh `(31.3543, 27.2373)`, and an ocean point `(0, -140)` which must return
`uncovered`.

## 8. Tests

```powershell
python -m pytest -q        # 25 tests, no database or network required
```

| File | Covers |
|---|---|
| `tests/test_app.py` | API validation, health, reverse lookup, bounded weather routes |
| `tests/test_weather.py` | Open-Meteo parser, GHCN parser, metrics, rate limiting, raw archive |
| `tests/test_lake_quality.py` | Silver rules, governorate lookup, Gold accuracy eligibility, anomaly detectors |
| `tests/test_migrate_mysql.py` | MariaDB → MySQL 8 DDL translation |
| `tests/test_overture_import.py`, `tests/test_service.py` | Geography import and hierarchy resolution |

## 9. Airflow (optional)

Mount `airflow/dags/` into `$AIRFLOW_HOME/dags`, set `DEPI_HOME` to the project path inside the Airflow workers,
make the project's requirements available there, and enable DAG `depi_weather_daily`.

## 10. Docker (optional)

```powershell
docker build -t depi-weather:local .
docker run --env-file .env -e DB_HOST=host.docker.internal -p 8000:8000 depi-weather:local
docker run --env-file .env -e DB_HOST=host.docker.internal depi-weather:local python weather_cli.py status
```

## 11. Data-safety rules (summary)

- Never update an old forecast with a newer run — every snapshot is kept.
- Observations are measured station values only; corrections create revisions.
- Values in °C, timestamps in UTC, calendar days in Africa/Cairo.
- `POINT(lon lat)` with SRID 4326 everywhere.
