# 07 — API & Dashboard

The Flask application ([`app.py`](../app.py)) serves the dashboard and a read-only JSON API. Locally it runs with
`python app.py` on `http://127.0.0.1:5000`; in production with Gunicorn on port 8000 inside the container.

The HTTP app never runs collection jobs or a scheduler — all writes happen in the CLI jobs.

## 1. Conventions

- All responses are JSON. Validation errors → **400** `{"status": "error", "error": "…"}`.
  Database/unexpected errors → **503** with a generic message (internal details are never exposed).
- Dates use `YYYY-MM-DD`. Coordinates are decimal degrees (`lat` −90…90, `lon` −180…180).
- Paging parameters are bounded (`page_size` ≤ 100, `limit` ≤ 500).

## 2. Endpoints

### Health & operations

| Method & path | Description |
|---|---|
| `GET /api/health` | DB connectivity, server version, geographic record count and coverage by level, import jobs |
| `GET /api/ready` | Readiness gate (200 `ready` / 503 `not_ready`) — see [05](05-observability-and-monitoring.md) |
| `GET /api/pipeline/status` | Forecast/observation totals, runs by status, 10 most recent runs, sampling coverage |
| `GET /api/pipeline/runs?page=&page_size=` | Paginated pipeline run history |

### Geography

| Method & path | Description |
|---|---|
| `GET /api/reverse?lat=&lon=` | Reverse geocoding against stored polygons: hierarchy (country → governorate → district → city → neighbourhood), names (Arabic/English), source attribution. Uncovered points return `match_status: "uncovered"` — names are never invented. |
| `GET /api/geometry/<unit_id>` | GeoJSON feature of one polygon |
| `GET /api/locations?q=&parent_id=&page=&page_size=` | Search places by Arabic or English name |
| `GET /api/locations/<unit_id>` | One place with its sampling point |

### Weather

| Method & path | Required | Optional | Description |
|---|---|---|---|
| `GET /api/weather/models` | — | — | Active models with organization, distributor, resolution, horizon |
| `GET /api/weather/forecasts` (alias `/latest`) | `location_id` | `start_date`, `end_date`, `model`, `limit` | Latest stored forecasts |
| `GET /api/weather/forecast-history` (alias `/compare`) | `location_id`, `target_date` | `model` | Every snapshot for one target day, D−10 … D−1, all models |
| `GET /api/weather/actuals` | `location_id` | `start_date`, `end_date`, `limit` | Eligible station observations with station name, distance, alignment |
| `GET /api/weather/accuracy` | `location_id` | `target_date` or `start_date`/`end_date`, `model`, `minimum_samples` | MAE / RMSE / bias / N per model and lead (Tmax and Tmin) |
| `GET /api/weather/stations` | — | `limit` | GHCN stations |

## 3. Examples

```powershell
Invoke-RestMethod http://127.0.0.1:5000/api/health
Invoke-RestMethod 'http://127.0.0.1:5000/api/reverse?lat=30.0444&lon=31.2357'
Invoke-RestMethod 'http://127.0.0.1:5000/api/locations?q=Aswan&page_size=5'
Invoke-RestMethod 'http://127.0.0.1:5000/api/weather/forecast-history?location_id=123&target_date=2026-11-20'
Invoke-RestMethod 'http://127.0.0.1:5000/api/weather/accuracy?location_id=123&start_date=2026-11-01&end_date=2026-11-20'
```

Example accuracy item:

```json
{
  "model_slug": "ecmwf_ifs",
  "lead_days": 3,
  "sample_count": 18,
  "max_mae": 1.12, "max_rmse": 1.41, "max_bias": -0.35,
  "min_mae": 1.48, "min_rmse": 1.90, "min_bias": 0.62
}
```

(Illustrative values — real numbers depend on collected data.)

## 4. Dashboard

`GET /` serves [`index.html`](../index.html), a single-page dashboard (Chart.js, no build step) with an
**English / العربية** toggle (RTL supported).

| Area | Shows |
|---|---|
| Header cards | Geographic units, sampling points, forecast snapshots, verified observations |
| Location search | Type-ahead over Arabic and English place names |
| Max / Min temperature evolution | For the chosen target date, how each model's forecast changed from D−10 to D−1, with the verified actual as a dashed line when available |
| Accuracy detail | Per model and lead: forecast, actual, error; MAE / RMSE / bias / N footnote |
| Model sources | Each model with originating organization and distributor (explicit models, never Best Match) |
| Pipeline monitoring | Mapped locations, successful and failed runs |
| Actual observation coverage | Nearest station name, distance, temporal-alignment status |

When no eligible observation exists the dashboard explicitly shows "Waiting for verified actual observations"
rather than plotting a model value as the truth.
