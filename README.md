# DEPI Weather Analytics & Forecast Accuracy Platform

A local Flask + MySQL/MariaDB reverse-geocoder backed only by imported polygon data. It never turns point labels into fictional boundaries and returns explicit coverage gaps.

## What is included

- Safe, additive geography tables with source/release metadata, hierarchy links, multilingual names, bounding boxes, SRID 4326 geometry, and a spatial index.
- A resumable, chunked geoBoundaries `gbOpen` importer for one country or all available countries.
- Database-native reverse lookup using a numeric bounding-box prefilter plus `ST_Intersects` (covers border points).
- An interactive Leaflet map with manual coordinate entry, RTL-safe local names, raw JSON, and clear verified/uncovered states.
- Health, schema audit, coverage reporting, and API tests.

Phase 2 extends the preserved Phase 1 geography with nationwide sampling, immutable model-specific forecast snapshots, measured station observations, accuracy-safe comparisons, a bilingual dashboard, and Azure-ready worker commands.

## Phase 2 quick start

```powershell
python weather_cli.py init-db
python weather_cli.py locations --sync
python weather_cli.py locations --status
python weather_cli.py stations --discover
python weather_cli.py stations --match
python weather_cli.py collect-forecasts --limit 5
python weather_cli.py collect-actuals --limit 5
python weather_cli.py backfill-actuals --lookback-days 45
python weather_cli.py validate
python weather_cli.py status
python app.py
```

`collect-forecasts` requests D0 through D+10 in `Africa/Cairo` and stores only the standard D+1 through D+10 comparison horizon. GFS, IFS, and AIFS are explicit model identifiers and never Best Match. A repeated identical logical run is idempotent. Use `--location-id`, `--model`, `--date`, `--batch-size`, `--limit`, and `--dry-run` for controlled testing.

`run-batch` is intended for an hourly UTC Azure trigger. It proceeds only during the 09:00 `Africa/Cairo` hour, so Egypt daylight-saving changes do not require a fixed-offset assumption. Use `--force` only for operator-initiated runs.

The dashboard and JSON API are at `http://127.0.0.1:5000`. See [AZURE_DEPLOYMENT.md](AZURE_DEPLOYMENT.md) for deployment, scheduling, TLS, backup, monitoring, and capacity guidance, and [PROVIDER_AND_COVERAGE.md](PROVIDER_AND_COVERAGE.md) for verified model identifiers and measured coverage.

For the complete Arabic production checklist, use [AZURE_CHECKLIST_AR.md](AZURE_CHECKLIST_AR.md). Run `powershell -ExecutionPolicy Bypass -File .\scripts\preflight.ps1` before every deployment and `python scripts\verify_deployment.py https://<app-fqdn>` after deployment.

## Windows setup

These commands assume PowerShell opened in the project folder.

1. Start the MySQL/MariaDB server behind phpMyAdmin (for example, start **MySQL** in the XAMPP control panel). phpMyAdmin itself is not used by this application.

2. Create a dedicated database account in MySQL/MariaDB. Grant it access only to `depi`. A database administrator can adapt the following example; choose a strong password rather than copying a placeholder:

   ```sql
   CREATE DATABASE IF NOT EXISTS depi CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
   CREATE USER IF NOT EXISTS 'depi_app'@'localhost' IDENTIFIED BY 'choose-a-strong-password';
   GRANT SELECT, INSERT, UPDATE, CREATE, ALTER, INDEX, REFERENCES ON depi.* TO 'depi_app'@'localhost';
   FLUSH PRIVILEGES;
   ```

3. Create the Python environment and install dependencies:

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install -r requirements.txt
   Copy-Item .env.example .env
   ```

   This workspace may also contain a `.deps` directory created during automated verification. When present, the supplied entry points automatically use it for ordinary commands such as `python app.py`. A normal virtual environment remains the recommended reproducible setup.

4. Edit `.env`. Set `DB_USER` and `DB_PASSWORD`; adjust host/port if the server is not on `127.0.0.1:3306`. `.env` is ignored by Git.

5. Audit existing tables **before** migration:

   ```powershell
   python scripts\audit_db.py | Tee-Object audit-before.json
   ```

6. Apply the idempotent additive migration:

   ```powershell
   python scripts\migrate.py
   ```

   If `depi` does not exist and the configured account has server-level create permission, use `python scripts\migrate.py --create-database` once.

## Import data

The recommended importer now uses Overture Maps divisions for detailed administrative, locality, and neighborhood polygons. With no scope argument it imports Egypt at every published detail level:

```powershell
python scripts\migrate.py
python scripts\import_geography.py --reset
```

`--reset` clears only the four geography tables and preserves every unrelated table in `depi`. Later refreshes are idempotent and do not need reset:

```powershell
python scripts\import_geography.py
python scripts\import_geography.py --refresh
```

Egypt coverage is filtered to country code `EG` from Overture's current release and can include country, region, county, localadmin, locality, borough, macrohood, neighborhood, and microhood polygons where the source publishes them. Missing subtypes remain explicit; the importer never converts point-only Places records into fake boundaries.

Repeat the same command to verify idempotency: `inserted=0` is expected for an unchanged release. Geometry rows are keyed by source release plus official Overture feature ID; changed source geometry updates that same record.

Global import is deliberately explicit because it can take substantial disk space and time:

```powershell
python scripts\import_world.py
```

That explicit command imports Overture's complete global divisions theme and writes local counts to `coverage.json`. It is substantially larger than Egypt-only import. Run it again after an interruption; database upserts do not create duplicates. Use `python scripts\import_world.py --skip-migration` only when desired.

The underlying configurable form remains available:

```powershell
python scripts\import_geography.py --all --batch-size 250
```

Downloads are streamed to `data\downloads`, retry transient HTTP errors, and resume a partial file with HTTP Range where supported. Database progress is committed every batch and recorded in `geo_import_job`. Re-running is the recovery mechanism after interruption. Use `--levels 0,1,2,3,4,5` only when those deeper boundaries are actually needed; availability differs by country.

## Run and verify

```powershell
python -m pytest -q
python scripts\coverage_report.py | Tee-Object coverage.json
python app.py
```

Open <http://127.0.0.1:5000>. Useful checks:

```powershell
Invoke-RestMethod http://127.0.0.1:5000/api/health
Invoke-RestMethod 'http://127.0.0.1:5000/api/reverse?lat=30.0444&lon=31.2357'
```

For temporary lookup diagnostics, set `REVERSE_DIAGNOSTICS=true` in `.env` and restart Flask. Passwords are never returned by the API or deliberately logged.

After the Egypt import, manually/test-check Cairo `(30.0444,31.2357)`, Giza `(30.0131,31.2089)`, Alexandria `(31.2001,29.9187)`, Aswan `(24.0889,32.8998)`, and Marsa Matrouh `(31.3543,27.2373)`. Also test an uncovered ocean point such as `(0,-140)`, border points, and invalid ranges. Names must match stored `geo_unit` rows; the application contains no coordinate-to-name lookup table.

## Schema and lookup notes

- `geo_source`: source, version, licensing, attribution, and original layer metadata.
- `geo_unit`: source identity, hierarchy, names, ISO-like codes, centroid/representative point, bbox, and Polygon/MultiPolygon geometry.
- `geo_name`: language/script-aware official, local, and alias names when a source supplies them.
- `geo_import_job`: import status and checkpoints.

Longitude is X and latitude is Y everywhere: `POINT(lon lat)`. Data is stored with SRID 4326. `ST_Intersects` intentionally includes border points; if overlapping polygons match at one level, the stable result order prefers the smallest known area and then record ID. The API exposes all selected matches and the source attribution.

The geoBoundaries importer uses a polygon representative point to establish an ADM*n* → ADM*n-1* relationship and stores the method as `spatial_containment`. It never assigns an exact neighborhood from a centroid. Source-native parent identifiers should take priority in a future Overture importer.

## Data licensing and attribution

The importer stores each layer's returned license, original source URL, release/build date, and complete metadata. geoBoundaries requires attribution and its component datasets can carry different licenses. Review stored `geo_source.metadata_json` and the upstream license before redistribution. OpenStreetMap map tiles have their own attribution and usage policy; they are for interactive display, not bulk downloading.

## Phase 2 data-safety rules

Keep weather facts separate from geography and separate observations from forecasts:

- `weather_sources` and `weather_models` preserve provider, distributor, organization, licensing, and exact model identity.
- `weather_daily_observations` and `weather_observation_revisions` preserve measured station values and corrections.
- `weather_forecast_runs` stores live or archived run metadata without pretending collection time is initialization time.
- `weather_daily_forecasts` stores point/date/lead facts once per immutable run.

Never update an old forecast with a newer run. Store every snapshot so `(model, run, valid date, point)` can later be compared with observations. Values are normalized to Celsius, timestamps are UTC, and calendar-day calculations use Africa/Cairo. A response SHA-256 is retained locally; configure Blob Storage before nationwide production to archive compressed raw payloads.

