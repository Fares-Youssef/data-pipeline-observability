ALTER TABLE weather_pipeline_runs
    ADD COLUMN IF NOT EXISTS heartbeat_at_utc DATETIME(6) NULL AFTER started_at_utc;

ALTER TABLE weather_pipeline_runs
    ADD COLUMN IF NOT EXISTS lease_expires_at_utc DATETIME(6) NULL AFTER heartbeat_at_utc;

ALTER TABLE weather_pipeline_runs
    ADD COLUMN IF NOT EXISTS worker_token CHAR(36) NULL AFTER lock_key;

CREATE INDEX IF NOT EXISTS ix_weather_pipeline_lease
    ON weather_pipeline_runs (status, lease_expires_at_utc);

ALTER TABLE weather_daily_forecasts
    ADD COLUMN IF NOT EXISTS model_id BIGINT UNSIGNED NULL AFTER forecast_run_id;

ALTER TABLE weather_daily_forecasts
    ADD COLUMN IF NOT EXISTS source_run_key VARCHAR(191) NULL AFTER model_id;

UPDATE weather_daily_forecasts f
JOIN weather_forecast_runs r ON r.id=f.forecast_run_id
SET f.model_id=r.model_id, f.source_run_key=r.source_run_key
WHERE f.model_id IS NULL OR f.source_run_key IS NULL;

ALTER TABLE weather_daily_forecasts
    MODIFY COLUMN model_id BIGINT UNSIGNED NOT NULL,
    MODIFY COLUMN source_run_key VARCHAR(191) NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_weather_daily_model_snapshot
    ON weather_daily_forecasts (model_id, source_run_key, sampling_point_id, target_date);

CREATE INDEX IF NOT EXISTS ix_weather_daily_model_target
    ON weather_daily_forecasts (model_id, target_date, lead_days);
