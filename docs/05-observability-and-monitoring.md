# 05 — Observability & Monitoring

Observability answers three questions at any time: **Did the pipeline run? Did it do what it should? Is the data
trustworthy?** The platform answers them from three sources: pipeline metadata in the database, application logs,
and cloud monitoring.

## 1. Pipeline metadata (database)

Every CLI job writes to `weather_pipeline_runs` and `weather_pipeline_batches`
(see [02 — Data model §5](02-data-model.md#5-operational--observability-tables)).

| Signal | Field(s) |
|---|---|
| Run outcome | `status`: `running`, `succeeded`, `partial`, `failed`, `cancelled` |
| Duration | `started_at_utc`, `finished_at_utc` |
| Liveness | `heartbeat_at_utc`, `lease_expires_at_utc` |
| Volume | `processed_count`, `inserted_count`, `duplicate_count`, `failed_count` |
| Failure reason | `error_message` (run and batch) |
| Retry history | `weather_pipeline_batches.attempt_count`, `checkpoint_json` |
| Data anomalies | `weather_quality_issues` (code, severity, message, details) |

Useful SQL:

```sql
-- Last 20 runs
SELECT id, job_type, logical_date, status, processed_count, inserted_count, duplicate_count, failed_count,
       TIMESTAMPDIFF(SECOND, started_at_utc, finished_at_utc) AS seconds, error_message
FROM weather_pipeline_runs ORDER BY id DESC LIMIT 20;

-- Failed batches and how often they were retried
SELECT r.job_type, b.batch_key, b.attempt_count, b.error_message
FROM weather_pipeline_batches b JOIN weather_pipeline_runs r ON r.id = b.pipeline_run_id
WHERE b.status = 'failed' ORDER BY b.id DESC;

-- Freshness: hours since the last successful forecast run
SELECT TIMESTAMPDIFF(HOUR, MAX(finished_at_utc), UTC_TIMESTAMP()) AS hours_since_success
FROM weather_pipeline_runs WHERE job_type = 'collect_forecasts' AND status = 'succeeded';

-- Open quality issues by type
SELECT issue_code, severity, COUNT(*) FROM weather_quality_issues
WHERE resolved_at_utc IS NULL GROUP BY issue_code, severity;
```

## 2. API health endpoints

| Endpoint | Purpose | Healthy | Unhealthy |
|---|---|---|---|
| `GET /api/health` | Liveness: DB reachable and geography schema present (used by Docker `HEALTHCHECK`) | 200 `status: ok` | 503 |
| `GET /api/ready` | Readiness: no unmapped units, no expired leases, no open error/critical issues | 200 `ready` | 503 `not_ready` |
| `GET /api/pipeline/status` | Totals (geographic units, sampling points, forecasts, observations) and runs by status | 200 | — |
| `GET /api/pipeline/runs?page=1&page_size=25` | Paginated run history | 200 | — |

`scripts/verify_deployment.py https://<app-fqdn>` calls these endpoints as a post-deployment smoke test (also run by
CI/CD).

## 3. Dashboard

The **Pipeline monitoring** panel in the dashboard (`index.html`) shows mapped locations, successful runs and failed
runs; the header cards show geographic units, sampling points, forecast snapshots and verified observations.

## 4. CLI status commands

```powershell
python weather_cli.py status      # records + runs by status
python weather_cli.py validate    # health report, exit code 1 if issues
python weather_cli.py analyze     # eligible comparisons available for accuracy
```

## 5. Logs

- Every command prints a **single JSON summary line** (`{"status": "complete", "processed": …, "inserted": …}`),
  which makes log queries simple.
- Errors are logged with full tracebacks (`logging.exception`) including context (model, offset, station).
- Log level via `LOG_LEVEL`. Passwords and connection strings are never logged; the deploy script never echoes full
  Azure CLI commands because they can contain secrets.

## 6. Azure Monitor / Log Analytics

Container Apps send stdout/stderr and system events to the `depi-logs` Log Analytics workspace.
[`monitoring/kql_queries.kql`](../monitoring/kql_queries.kql) contains ready-made queries:

```kusto
// Failed job executions in the last day
ContainerAppSystemLogs_CL
| where TimeGenerated > ago(1d)
| where JobName_s startswith "depi-" and Reason_s in ("BackoffLimitExceeded", "Failed", "DeadlineExceeded")
| project TimeGenerated, JobName_s, Reason_s, Log_s

// Pipeline errors written by weather_cli.py
ContainerAppConsoleLogs_CL
| where TimeGenerated > ago(1d)
| where ContainerJobName_s startswith "depi-" and Log_s has_any ("ERROR", "Traceback", "rate_limited", "HTTP 429")
```

## 7. Alerting

[`monitoring/create_alerts.ps1`](../monitoring/create_alerts.ps1) creates:

- an **action group** `depi-alerts` that sends e-mail, and
- a **scheduled-query alert** `depi-job-errors` (severity 2, evaluated hourly) that fires when any `depi-*` job logs
  `ERROR`, `Traceback` or `rate_limited`.

```powershell
powershell -ExecutionPolicy Bypass -File .\monitoring\create_alerts.ps1 -Email you@example.com
```

Recommended additional alerts (documented, configurable the same way): non-zero job exit, no successful forecast run
in 30 hours, observation lag, database storage and connection saturation.

## 8. Failure simulation & recovery (demo)

| Scenario | How to simulate | Expected evidence |
|---|---|---|
| Provider outage / 5xx | Disconnect network or point to a bad endpoint | Retries with backoff in logs; batch `failed`; run `partial`/`failed` with `error_message`; alert fires |
| Rate limit | Lower `WEATHER_REQUEST_INTERVAL` to 0 on a large run | `status: rate_limited`, exit code 75, `retry_after_seconds` reported |
| Crashed worker | Kill the process mid-run | Run stays `running` until the lease expires; `validate` reports `expired_pipeline_leases`; `recover --expired` marks it `failed` |
| Concurrent worker | Start the same command twice | Second start refused: "A live pipeline lease already holds …" |
| Replay | Re-run `collect-forecasts` for the same date | `inserted: 0`, `duplicates: N` — no duplicate rows |
| Bad data | Insert an implausible value / big jump | `quality-check` records a `warning` issue; Silver marks row `is_valid = false` |

## 9. Note on Prometheus / Grafana

The proposal planned Prometheus and Grafana. Because the platform is deployed on Azure Container Apps, the
implemented monitoring uses the **native Azure stack** (Log Analytics + Azure Monitor alerts) plus the
database-backed run metadata and API endpoints above. The same run tables could be exposed to Prometheus via an
exporter in a future iteration (see [10 — Requirements traceability](10-requirements-traceability.md)).
