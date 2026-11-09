from __future__ import annotations

import argparse
import json
import logging
import os
import shlex
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

_LOCAL_DEPENDENCIES = Path(__file__).resolve().parent / ".deps"
if _LOCAL_DEPENDENCIES.is_dir():
    sys.path.insert(0, str(_LOCAL_DEPENDENCIES))

from geo.config import Settings
from scripts.migrate import apply_migrations
from weather.archive import RawResponseArchive
from weather.observations import GhcnDailyProvider
from weather.providers import MODEL_CONFIGS, OpenMeteoProvider, RateLimitError
from weather.repository import WeatherRepository


def emit(value) -> None:
    print(json.dumps(value, ensure_ascii=False, default=str))


def cairo_date(raw: str | None) -> date:
    return date.fromisoformat(raw) if raw else datetime.now(ZoneInfo("Africa/Cairo")).date()


def collect_forecasts(args, settings: Settings, repository: WeatherRepository) -> int:
    run_at = datetime.fromisoformat(args.run.replace("Z", "+00:00")) if getattr(args, "run", None) else None
    if run_at and run_at.tzinfo is None:
        run_at = run_at.replace(tzinfo=ZoneInfo("UTC"))
    logical_date = run_at.astimezone(ZoneInfo("Africa/Cairo")).date() if run_at else cairo_date(args.date)
    models = [args.model] if args.model else list(MODEL_CONFIGS)
    point_limit = args.limit or settings.weather_max_locations or None
    points = repository.active_points(point_limit, args.location_id)
    if not points:
        emit({"status": "no_locations", "hint": "Run weather_cli.py locations --sync first"})
        return 2
    if args.dry_run:
        emit({"status": "dry_run", "logical_date": logical_date, "models": models, "sampling_points": len(points)})
        return 0
    provider = OpenMeteoProvider(
        settings.open_meteo_api_key or None, settings.weather_request_timeout, retries=2,
        minimum_interval_seconds=settings.weather_request_interval,
        rate_limit_backoff_seconds=settings.weather_rate_limit_backoff,
    )
    archive = RawResponseArchive(settings)
    grand = {"processed": 0, "inserted": 0, "duplicates": 0, "failed": 0}
    for model in models:
        lock_key = f"forecast:{logical_date}:{model}"
        totals = {"processed": 0, "inserted": 0, "duplicates": 0, "failed": 0}
        run_id = None
        error = None
        try:
            run_id = repository.start_pipeline("collect_forecasts", logical_date, lock_key, {"model": model})
            size = args.batch_size or settings.weather_batch_size
            for offset in range(0, len(points), size):
                batch = points[offset:offset + size]
                batch_key = f"{model}:{offset}:{batch[0].id}-{batch[-1].id}"
                batch_id = repository.start_batch(run_id, batch_key, {"offset": offset, "point_ids": [p.id for p in batch]})
                batch_counts = {"processed": 0, "inserted": 0, "duplicates": 0, "failed": 0}
                try:
                    responses = provider.fetch(model, batch, logical_date, run_at)
                    archive_result = archive.store(responses[0].raw_bytes, logical_date, model)
                    request_hash = provider.request_hash(model, [p.id for p in batch], logical_date)
                    source_run_key = (
                        f"run:{run_at.astimezone(ZoneInfo('UTC')).isoformat()}:{model}"
                        if run_at else f"live:{logical_date.isoformat()}:{model}"
                    )
                    for response in responses:
                        result = repository.store_forecast_response(
                            response, logical_date, source_run_key, request_hash,
                            "archived_run" if run_at else "live_latest", run_at,
                            pipeline_batch_id=batch_id, archive_uri=archive_result.uri,
                            response_sha256=archive_result.sha256,
                        )
                        batch_counts["processed"] += len(response.days)
                        batch_counts["inserted"] += result["inserted"]
                        batch_counts["duplicates"] += result["duplicates"]
                    repository.finish_batch(batch_id, "succeeded", batch_counts, checkpoint={"next_offset": offset + len(batch)})
                except Exception as exc:
                    logging.exception("Forecast batch failed", extra={"model": model, "offset": offset})
                    batch_counts["failed"] = len(batch)
                    error = str(exc)
                    repository.finish_batch(batch_id, "failed", batch_counts, error, {"retry_offset": offset})
                    if isinstance(exc, RateLimitError):
                        raise
                for key in totals:
                    totals[key] += batch_counts[key]
                repository.heartbeat_pipeline(run_id, totals)
            status = "succeeded" if totals["failed"] == 0 else ("partial" if totals["processed"] else "failed")
            repository.finish_pipeline(run_id, status, totals, error)
        except KeyboardInterrupt:
            if run_id is not None:
                repository.finish_pipeline(run_id, "cancelled", totals, "interrupted by operator")
            emit({"status": "cancelled", "model": model, **totals})
            return 130
        except Exception as exc:
            logging.exception("Forecast model collection failed", extra={"model": model})
            error = str(exc)
            if run_id is not None:
                repository.finish_pipeline(run_id, "failed", totals, error)
            totals["failed"] = max(totals["failed"], len(points))
            if isinstance(exc, RateLimitError):
                for key in grand:
                    grand[key] += totals[key]
                emit({"status": "rate_limited", "model": model, "retry_after_seconds": exc.retry_after_seconds, **grand})
                return 75
        for key in grand:
            grand[key] += totals[key]
    emit({"status": "complete" if grand["failed"] == 0 else "partial", **grand})
    return 0 if grand["failed"] == 0 else 1


def discover_stations(args, settings: Settings, repository: WeatherRepository) -> int:
    provider = GhcnDailyProvider(settings.weather_request_timeout)
    stations = provider.discover_egypt_stations()
    result = repository.upsert_stations(stations)
    emit({"status": "complete", "discovered": len(stations), **result})
    return 0


def collect_actuals(args, settings: Settings, repository: WeatherRepository) -> int:
    end = cairo_date(args.date) - timedelta(days=1)
    start = end - timedelta(days=args.lookback_days - 1)
    stations = repository.stations(args.limit or 100, recent_year=end.year - 2)
    if args.dry_run:
        emit({"status": "dry_run", "stations": len(stations), "start": start, "end": end})
        return 0
    provider = GhcnDailyProvider(settings.weather_request_timeout)
    totals = {"inserted": 0, "duplicates": 0, "failed": 0}
    run_id = repository.start_pipeline(args.command, end, f"{args.command}:{end}", {"start": str(start), "end": str(end)})
    error = None
    try:
        for station in stations:
            batch_id = repository.start_batch(run_id, f"station:{station['source_station_id']}", {"station_id": station["id"]})
            batch_counts = {"processed": 0, "inserted": 0, "duplicates": 0, "failed": 0}
            try:
                observations = provider.station_observations(station["source_station_id"], start, end)
                result = repository.store_observations(station["id"], observations)
                batch_counts.update(result)
                batch_counts["processed"] = result["inserted"] + result["duplicates"]
                repository.finish_batch(batch_id, "succeeded", batch_counts)
            except Exception as exc:
                logging.exception("GHCN station collection failed", extra={"station": station["source_station_id"]})
                batch_counts["failed"] = 1
                error = str(exc)
                repository.finish_batch(batch_id, "failed", batch_counts, error)
            totals["inserted"] += batch_counts["inserted"]
            totals["duplicates"] += batch_counts["duplicates"]
            totals["failed"] += batch_counts["failed"]
            repository.heartbeat_pipeline(run_id, {"processed": totals["inserted"] + totals["duplicates"], **totals})
    except KeyboardInterrupt:
        counts = {"processed": totals["inserted"] + totals["duplicates"], **totals}
        repository.finish_pipeline(run_id, "cancelled", counts, "interrupted by operator")
        emit({"status": "cancelled", **totals})
        return 130
    counts = {"processed": totals["inserted"] + totals["duplicates"], **totals}
    alignment = repository.apply_alignment_policy(settings.weather_alignment_distance_km)
    logging.info("Alignment policy applied: %s", alignment)
    repository.finish_pipeline(run_id, "succeeded" if totals["failed"] == 0 else ("partial" if counts["processed"] else "failed"), counts, error)
    emit({"status": "complete" if totals["failed"] == 0 else "partial", "start": start, "end": end, **totals})
    return 0 if totals["failed"] == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DEPI Phase 2 weather pipeline")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db")
    locations = sub.add_parser("locations")
    locations.add_argument("--sync", action="store_true")
    locations.add_argument("--status", action="store_true")
    locations.add_argument("--limit", type=int)
    stations = sub.add_parser("stations")
    stations.add_argument("--discover", action="store_true")
    stations.add_argument("--match", action="store_true")
    stations.add_argument("--align", action="store_true", help="Apply the comparison eligibility policy")
    stations.add_argument("--maximum-distance-km", type=float, default=250.0)
    stations.add_argument("--limit", type=int, default=100)
    forecast = sub.add_parser("collect-forecasts")
    forecast.add_argument("--date")
    forecast.add_argument("--run", help="Archived model initialization in ISO-8601 UTC for the Single Runs API")
    forecast.add_argument("--model", choices=sorted(MODEL_CONFIGS))
    forecast.add_argument("--location-id", type=int)
    forecast.add_argument("--batch-size", type=int)
    forecast.add_argument("--limit", type=int)
    forecast.add_argument("--dry-run", action="store_true")
    forecast.add_argument("--retry-failed", action="store_true")
    actuals = sub.add_parser("collect-actuals")
    actuals.add_argument("--date")
    actuals.add_argument("--lookback-days", type=int, default=14)
    actuals.add_argument("--limit", type=int)
    actuals.add_argument("--dry-run", action="store_true")
    backfill = sub.add_parser("backfill-actuals")
    backfill.add_argument("--date")
    backfill.add_argument("--lookback-days", type=int, default=45)
    backfill.add_argument("--limit", type=int)
    backfill.add_argument("--dry-run", action="store_true")
    lake = sub.add_parser("export-lake", help="Write Silver/Gold Parquet layers (local or Azure Data Lake)")
    lake.add_argument("--days", type=int, default=30)
    quality = sub.add_parser("quality-check", help="Statistical anomaly detection on recent forecasts")
    quality.add_argument("--days", type=int, default=3)
    sub.add_parser("report", help="Render Matplotlib/Seaborn charts from the local Gold layer")
    sub.add_parser("validate")
    sub.add_parser("analyze")
    sub.add_parser("status")
    recover = sub.add_parser("recover")
    recover.add_argument("--expired", action="store_true", help="Release only expired execution leases")
    recover.add_argument("--run-id", type=int, help="Release one confirmed orphaned run")
    batch = sub.add_parser("run-batch")
    batch.add_argument("--date")
    batch.add_argument("--limit", type=int)
    batch.add_argument("--batch-size", type=int)
    batch.add_argument("--force", action="store_true", help="Run outside the 09:00 Africa/Cairo gate")
    return parser


def main() -> int:
    # Container jobs can pass the command through DEPI_COMMAND (e.g. "collect-forecasts --limit 5"),
    # which avoids quoting dash-prefixed arguments in `az containerapp job update --args`.
    argv = sys.argv[1:] or shlex.split(os.getenv("DEPI_COMMAND", ""))
    args = build_parser().parse_args(argv)
    settings = Settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    repository = WeatherRepository(settings)
    if args.command == "init-db":
        apply_migrations(settings)
        emit({"status": "complete", "database": settings.db_name})
        return 0
    if args.command == "locations":
        if args.sync:
            emit(repository.sync_sampling_points(args.limit))
        else:
            emit(repository.sampling_status())
        return 0
    if args.command == "stations":
        if args.discover:
            return discover_stations(args, settings, repository)
        if args.match:
            emit(repository.match_nearest_stations(args.maximum_distance_km))
            return 0
        if args.align:
            emit(repository.apply_alignment_policy(settings.weather_alignment_distance_km))
            return 0
        emit(repository.stations(args.limit))
        return 0
    if args.command == "collect-forecasts":
        return collect_forecasts(args, settings, repository)
    if args.command in {"collect-actuals", "backfill-actuals"}:
        return collect_actuals(args, settings, repository)
    if args.command == "export-lake":
        from weather.lake import export_lake
        emit(export_lake(settings, args.days))
        return 0
    if args.command == "quality-check":
        from weather.quality import run_quality_checks
        emit(run_quality_checks(settings, args.days))
        return 0
    if args.command == "report":
        from weather.report import build_report
        emit(build_report(settings))
        return 0
    if args.command == "status":
        emit(repository.pipeline_status())
        return 0
    if args.command == "recover":
        if args.run_id:
            changed = repository.recover_pipeline(args.run_id)
            emit({"status": "recovered" if changed else "unchanged", "run_id": args.run_id})
            return 0 if changed else 1
        recovered = repository.recover_expired_pipelines() if args.expired else 0
        emit({"status": "complete", "expired_runs_recovered": recovered})
        return 0
    if args.command == "validate":
        report = repository.validation_report()
        healthy = (report["unmapped_units"] == 0 and report["running_pipelines"] == 0
                   and report["expired_pipeline_leases"] == 0 and report["open_quality_errors"] == 0)
        emit({"status": "valid" if healthy else "issues", **report})
        return 0 if healthy else 1
    if args.command == "analyze":
        summary = repository.analysis_summary()
        emit({"status": "ready" if summary["accuracy_ready"] else "waiting_for_eligible_observations", **summary,
              "note": "Only temporally and geographically compatible station observations are included."})
        return 0
    if args.command == "run-batch":
        now = datetime.now(ZoneInfo("Africa/Cairo"))
        if not args.force and now.hour != 9:
            emit({"status": "skipped", "reason": "outside 09:00 Africa/Cairo execution window", "cairo_time": now.isoformat()})
            return 0
        forecast_args = argparse.Namespace(date=args.date, run=None, model=None, location_id=None, batch_size=args.batch_size,
                                           limit=args.limit, dry_run=False, retry_failed=True)
        forecast_code = collect_forecasts(forecast_args, settings, repository)
        actual_args = argparse.Namespace(command="collect_actuals", date=args.date, lookback_days=14, limit=args.limit, dry_run=False)
        actual_code = collect_actuals(actual_args, settings, repository)
        post_code = 0
        for label, step in (("quality-check", "weather.quality:run_quality_checks"), ("export-lake", "weather.lake:export_lake")):
            module_name, function_name = step.split(":")
            try:
                module = __import__(module_name, fromlist=[function_name])
                emit({"step": label, **getattr(module, function_name)(settings, 14)})
            except Exception:
                logging.exception("Post-processing step failed", extra={"step": label})
                post_code = 1
        return max(forecast_code, actual_code, post_code)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
