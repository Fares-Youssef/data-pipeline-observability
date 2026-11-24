-- Example analytical queries (serverless SQL bills per data scanned; partition filters reduce cost).
USE depi_lake;
GO
-- 1) Daily volume per model, using partition elimination on issue_date.
SELECT issue_date, model_slug, COUNT(*) AS forecasts
FROM dbo.fact_forecast
WHERE issue_date >= DATEADD(day, -14, CAST(GETDATE() AS date))
GROUP BY issue_date, model_slug
ORDER BY issue_date, model_slug;

-- 2) Window function: how the forecast for one target day evolved run by run.
SELECT model_slug, sampling_point_id, target_date, lead_days, temperature_max_c,
       temperature_max_c - LAG(temperature_max_c) OVER (PARTITION BY model_slug, sampling_point_id, target_date
                                                        ORDER BY lead_days DESC) AS change_vs_previous_run
FROM dbo.fact_forecast
WHERE governorate = 'Cairo';

-- 3) Best model per governorate and lead (Gold layer).
SELECT governorate, lead_days, model_slug, samples, mae_max_c
FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY governorate, lead_days ORDER BY mae_max_c) AS rn
      FROM dbo.model_accuracy) ranked
WHERE rn = 1
ORDER BY governorate, lead_days;

-- 4) Where do the models disagree most? (low-confidence areas)
SELECT TOP 20 l.governorate, AVG(d.spread_max_c) AS avg_spread_c, COUNT(*) AS samples
FROM dbo.model_disagreement d
JOIN (SELECT DISTINCT sampling_point_id, governorate FROM dbo.dim_location WHERE sampling_point_id IS NOT NULL) l
  ON l.sampling_point_id = d.sampling_point_id
GROUP BY l.governorate
ORDER BY avg_spread_c DESC;
