# Azure deployment runbook

The web container and scheduled jobs use the same image but different commands. The HTTP app never starts a scheduler or performs collection inside a request.

## Services

- Azure Container Apps: `gunicorn app:app` for the dashboard/API.
- Container Apps Jobs: `python weather_cli.py ...` for forecasts, observations, reconciliation, and validation.
- Azure Database for MySQL Flexible Server: the `depi` schema with TLS required.
- Blob Storage: compressed raw-response archives when `AZURE_STORAGE_CONNECTION_STRING` is configured.
- Log Analytics / Azure Monitor: stdout JSON/application logs and job exit status.
- Container Apps secrets or Key Vault references: database password, storage connection, optional Open-Meteo customer API key.

## Safe database migration

1. Stop geography imports and take a consistent local backup:
   `mysqldump --single-transaction --routines --triggers --default-character-set=utf8mb4 depi > depi.sql`
2. Record exact checks: `SELECT COUNT(*) FROM geo_unit`, counts by `admin_level`, orphan parents, Arabic-name samples, and geometry validity samples.
3. Create Azure MySQL with `utf8mb4`, require TLS, create a least-privilege application account, and import the dump.
4. Set `DB_HOST`, `DB_USER`, `DB_PASSWORD`, `DB_NAME=depi`, `DB_SSL_CA`, and `DB_SSL_VERIFY_CERT=true`.
5. Run `python weather_cli.py init-db`. Migration 003 is additive and does not delete the four geography tables.
6. Run `python weather_cli.py locations --status`, API health checks, count comparisons, and selected geometry queries before enabling scheduled jobs.

Do not run the import until the destination server exists and a backup has been verified. Azure automated backups should use the required retention period; perform periodic restore drills to a separate server.

## Build and deploy outline

```powershell
$ResourceGroup = 'rg-depi-weather'
$AcrName = 'depiweather56232c7d'
$ImageTag = 'v1.0.0'
az acr build --registry $AcrName --image "depi-weather:$ImageTag" .
# Set the environment, image registry, database host, user, and secret variables before creating the app.
az containerapp create --name depi-weather-api --resource-group $ResourceGroup --environment $ContainerEnvironment --image "$AcrName.azurecr.io/depi-weather:$ImageTag" --target-port 8000 --ingress external --secrets "db-password=$DatabasePassword" --env-vars "DB_HOST=$DatabaseHost" DB_NAME=depi "DB_USER=$DatabaseUser" DB_PASSWORD=secretref:db-password DB_SSL_CA=/etc/ssl/certs/ca-certificates.crt DB_SSL_VERIFY_CERT=true
```

Use managed identity for ACR/Key Vault where available. Never place credentials in the image, command history, templates committed to source control, or dashboard responses.

## Scheduled jobs and Cairo DST

Container Apps cron is UTC. Egypt changes offset seasonally, so a single fixed UTC expression cannot guarantee 09:00 Africa/Cairo. Run a lightweight job hourly and let the command acquire the database logical-date lock only when local Cairo time is in the configured window, or update the UTC cron at DST boundaries through infrastructure automation. The production recommendation is the hourly trigger plus timezone-aware gate.

Example job command and nominal hourly trigger:

```powershell
az containerapp job create --name depi-forecast --resource-group <rg> --environment <env> --trigger-type Schedule --cron-expression "0 * * * *" --image <registry>/depi-weather:<tag> --cpu 1 --memory 2Gi --replica-timeout 7200 --replica-retry-limit 1 --parallelism 1 --replica-completion-count 1 --command python --args weather_cli.py run-batch
az containerapp job create --name depi-observations --resource-group <rg> --environment <env> --trigger-type Schedule --cron-expression "30 12 * * *" --image <registry>/depi-weather:<tag> --command python --args weather_cli.py collect-actuals --lookback-days 14
az containerapp job create --name depi-backfill --resource-group <rg> --environment <env> --trigger-type Schedule --cron-expression "0 14 * * *" --image <registry>/depi-weather:<tag> --command python --args weather_cli.py backfill-actuals --lookback-days 45
```

The hourly forecast command contains the Cairo-time gate and exits quietly outside the 09:00 Africa/Cairo hour.

Do not enable nationwide collection on an unverified free API allowance. With 8,992 points and three models, the workload represents 26,976 coordinate-model combinations per day before provider-grid reuse. Configure a suitable customer API key or self-hosted provider capacity first.

## Monitoring and recovery

Alert on non-zero job exits, `failed`/`partial` pipeline runs, provider 429/5xx rates, no successful forecast run in 30 hours, observation lag, database storage, and connection saturation. Failed batches remain isolated; unique keys make retries idempotent. Raw archives should use lifecycle policies (hot to cool/archive), while normalized historical forecast rows are retained.

## Capacity and cost guidance

The naïve upper bound is 98.5 million forecast rows/year (8,994 × 3 × 10 × 365). Actual volume is `distinct_sampling_points × models × 10 × daily runs` and must be measured after `locations --sync`. API use is similarly based on coordinates, variables and horizon—not only HTTP request count. Start with a small Azure MySQL tier and one-job replica, measure storage/index growth and execution time, then resize. Confirm current Azure regional pricing and Open-Meteo terms before nationwide activation; pricing changes over time.
