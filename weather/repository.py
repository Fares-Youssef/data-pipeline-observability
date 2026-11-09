from __future__ import annotations

import hashlib
import json
import math
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from geo.config import Settings
from geo.db import cursor
from geo.repository import json_value
from weather.types import ForecastResponse, SamplingPoint


class WeatherRepository:
    def __init__(self, settings: Settings):
        self.settings = settings

    def sync_sampling_points(self, limit: int | None = None) -> dict[str, int]:
        """Map every Egyptian polygon to its stored interior representative point."""
        mapped = created = 0
        with cursor(self.settings) as (connection, cur):
            sql = (
                "SELECT u.id,u.centroid_lat,u.centroid_lon FROM geo_unit u "
                "LEFT JOIN weather_geo_unit_points m ON m.geo_unit_id=u.id AND m.role='primary' AND m.active=1 "
                "WHERE u.country_iso3='EGY' AND m.id IS NULL "
                "AND u.centroid_lat BETWEEN -90 AND 90 AND u.centroid_lon BETWEEN -180 AND 180 ORDER BY u.id"
            )
            if limit:
                sql += " LIMIT %s"
                cur.execute(sql, (limit,))
            else:
                cur.execute(sql)
            rows = cur.fetchall()
            for row in rows:
                lat = Decimal(str(row["centroid_lat"])).quantize(Decimal("0.0000001"))
                lon = Decimal(str(row["centroid_lon"])).quantize(Decimal("0.0000001"))
                cur.execute(
                    "INSERT INTO weather_sampling_points(latitude,longitude,point_method,metadata_json) "
                    "VALUES(%s,%s,'polygon_representative',JSON_OBJECT('source','geo_unit representative_point')) "
                    "ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(id)",
                    (lat, lon),
                )
                point_id = cur.lastrowid
                if cur.rowcount == 1:
                    created += 1
                cur.execute(
                    "INSERT IGNORE INTO weather_geo_unit_points"
                    "(geo_unit_id,sampling_point_id,role,mapping_method,representative) "
                    "VALUES(%s,%s,'primary','stored_polygon_representative_point',TRUE)",
                    (row["id"], point_id),
                )
                mapped += cur.rowcount
            connection.commit()
        return {"processed": len(rows), "points_created": created, "units_mapped": mapped}

    def sampling_status(self) -> dict[str, int]:
        with cursor(self.settings) as (_, cur):
            cur.execute("SELECT COUNT(*) total FROM geo_unit WHERE country_iso3='EGY'")
            total = cur.fetchone()["total"]
            cur.execute("SELECT COUNT(DISTINCT geo_unit_id) mapped FROM weather_geo_unit_points WHERE active=1")
            mapped = cur.fetchone()["mapped"]
            cur.execute("SELECT COUNT(*) points FROM weather_sampling_points WHERE active=1")
            points = cur.fetchone()["points"]
        return {"geographic_units": total, "mapped_units": mapped, "distinct_sampling_points": points, "unmapped_units": total - mapped}

    def active_points(self, limit: int | None = None, location_id: int | None = None) -> list[SamplingPoint]:
        # Priority order: governorates and districts first, then cities, then neighborhoods.
        # A capped run (WEATHER_MAX_LOCATIONS) therefore always covers the most important places.
        sql = (
            "SELECT p.id,p.latitude,p.longitude,p.timezone_name FROM weather_sampling_points p "
            "JOIN weather_geo_unit_points m ON m.sampling_point_id=p.id AND m.active=1 "
            "JOIN geo_unit u ON u.id=m.geo_unit_id "
            "WHERE p.active=1"
        )
        params: list[Any] = []
        if location_id is not None:
            sql += " AND m.geo_unit_id=%s"
            params.append(location_id)
        sql += " GROUP BY p.id,p.latitude,p.longitude,p.timezone_name ORDER BY MIN(u.admin_level),p.id"
        if limit:
            sql += " LIMIT %s"
            params.append(limit)
        with cursor(self.settings) as (_, cur):
            cur.execute(sql, params)
            rows = cur.fetchall()
        return [SamplingPoint(**row) for row in rows]

    def model_id(self, slug: str) -> int:
        with cursor(self.settings) as (_, cur):
            cur.execute("SELECT id FROM weather_models WHERE slug=%s AND active=1", (slug,))
            row = cur.fetchone()
        if not row:
            raise ValueError(f"Weather model is not initialized: {slug}")
        return row["id"]

    def recover_expired_pipelines(self, lock_key: str | None = None) -> int:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        conditions = "status='running' AND lease_expires_at_utc IS NOT NULL AND lease_expires_at_utc <= %s"
        params: list[Any] = [now]
        if lock_key:
            conditions += " AND lock_key=%s"
            params.append(lock_key)
        with cursor(self.settings) as (connection, cur):
            cur.execute(
                "UPDATE weather_pipeline_runs SET status='failed',finished_at_utc=%s,lock_key=NULL,worker_token=NULL,"
                "error_message=CONCAT(COALESCE(error_message,''),CASE WHEN error_message IS NULL THEN '' ELSE '\n' END,"
                "'execution lease expired; safe retry is allowed') WHERE " + conditions,
                [now, *params],
            )
            recovered = cur.rowcount
            connection.commit()
        return recovered

    def recover_pipeline(self, run_id: int, reason: str = "operator confirmed worker is not running") -> bool:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with cursor(self.settings) as (connection, cur):
            cur.execute(
                "UPDATE weather_pipeline_runs SET status='failed',finished_at_utc=%s,lock_key=NULL,worker_token=NULL,"
                "lease_expires_at_utc=NULL,error_message=%s WHERE id=%s AND status='running'",
                (now, reason, run_id),
            )
            changed = cur.rowcount == 1
            connection.commit()
        return changed

    def start_pipeline(self, job_type: str, logical_date: date, lock_key: str, metadata: dict | None = None,
                       lease_minutes: int = 15) -> int:
        self.recover_expired_pipelines(lock_key)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        lease_expires = now + timedelta(minutes=lease_minutes)
        worker_token = str(uuid.uuid4())
        with cursor(self.settings) as (connection, cur):
            try:
                cur.execute(
                    "INSERT INTO weather_pipeline_runs(job_type,logical_date,lock_key,worker_token,status,started_at_utc,"
                    "heartbeat_at_utc,lease_expires_at_utc,metadata_json) VALUES(%s,%s,%s,%s,'running',%s,%s,%s,%s)",
                    (job_type, logical_date, lock_key, worker_token, now, now, lease_expires, json.dumps(metadata or {})),
                )
                connection.commit()
                return cur.lastrowid
            except Exception as exc:
                connection.rollback()
                raise RuntimeError(
                    f"A live pipeline lease already holds {lock_key!r}. Wait for it to finish or use "
                    f"'weather_cli.py recover --expired' after the lease expires. Database message: {exc}"
                ) from exc

    def heartbeat_pipeline(self, run_id: int, counts: dict[str, int] | None = None, lease_minutes: int = 15) -> None:
        counts = counts or {}
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with cursor(self.settings) as (connection, cur):
            cur.execute(
                "UPDATE weather_pipeline_runs SET heartbeat_at_utc=%s,lease_expires_at_utc=%s,processed_count=%s,"
                "inserted_count=%s,duplicate_count=%s,failed_count=%s WHERE id=%s AND status='running'",
                (now, now + timedelta(minutes=lease_minutes), counts.get("processed", 0), counts.get("inserted", 0),
                 counts.get("duplicates", 0), counts.get("failed", 0), run_id),
            )
            if cur.rowcount != 1:
                connection.rollback()
                raise RuntimeError(f"Pipeline run {run_id} no longer owns an active lease")
            connection.commit()

    def start_batch(self, run_id: int, batch_key: str, checkpoint: dict | None = None) -> int:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with cursor(self.settings) as (connection, cur):
            cur.execute(
                "INSERT INTO weather_pipeline_batches(pipeline_run_id,batch_key,status,attempt_count,started_at_utc,checkpoint_json) "
                "VALUES(%s,%s,'running',1,%s,%s) ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(id),status='running',"
                "attempt_count=attempt_count+1,started_at_utc=VALUES(started_at_utc),finished_at_utc=NULL,error_message=NULL,"
                "checkpoint_json=VALUES(checkpoint_json)",
                (run_id, batch_key, now, json.dumps(checkpoint or {})),
            )
            batch_id = cur.lastrowid
            connection.commit()
        return batch_id

    def finish_batch(self, batch_id: int, status: str, counts: dict[str, int], error: str | None = None,
                     checkpoint: dict | None = None) -> None:
        with cursor(self.settings) as (connection, cur):
            cur.execute(
                "UPDATE weather_pipeline_batches SET status=%s,finished_at_utc=%s,processed_count=%s,inserted_count=%s,"
                "duplicate_count=%s,failed_count=%s,error_message=%s,checkpoint_json=%s WHERE id=%s",
                (status, datetime.now(timezone.utc).replace(tzinfo=None), counts.get("processed", 0), counts.get("inserted", 0),
                 counts.get("duplicates", 0), counts.get("failed", 0), error, json.dumps(checkpoint or {}), batch_id),
            )
            connection.commit()

    def finish_pipeline(self, run_id: int, status: str, counts: dict[str, int], error: str | None = None) -> None:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with cursor(self.settings) as (connection, cur):
            cur.execute(
                "UPDATE weather_pipeline_runs SET status=%s,finished_at_utc=%s,heartbeat_at_utc=%s,lease_expires_at_utc=NULL,"
                "lock_key=NULL,worker_token=NULL,processed_count=%s,inserted_count=%s,duplicate_count=%s,failed_count=%s,"
                "error_message=%s WHERE id=%s",
                (status, now, now, counts.get("processed", 0), counts.get("inserted", 0),
                 counts.get("duplicates", 0), counts.get("failed", 0), error, run_id),
            )
            connection.commit()

    def validation_report(self) -> dict[str, Any]:
        with cursor(self.settings) as (_, cur):
            cur.execute("SELECT COUNT(*) total FROM weather_pipeline_runs WHERE status='running'")
            running = cur.fetchone()["total"]
            cur.execute("SELECT COUNT(*) total FROM weather_pipeline_runs WHERE status='running' AND lease_expires_at_utc <= UTC_TIMESTAMP(6)")
            expired = cur.fetchone()["total"]
            cur.execute(
                "SELECT m.slug,COUNT(f.id) records,COUNT(DISTINCT f.sampling_point_id) points,"
                "COUNT(DISTINCT f.target_date) target_dates FROM weather_models m LEFT JOIN weather_daily_forecasts f "
                "ON f.model_id=m.id GROUP BY m.id,m.slug ORDER BY m.slug"
            )
            models = [self._json_row(row) for row in cur.fetchall()]
            cur.execute("SELECT COUNT(*) total FROM weather_daily_observations WHERE is_current=1")
            observations = cur.fetchone()["total"]
            cur.execute("SELECT COUNT(*) total FROM weather_station_point_matches WHERE is_primary=1")
            station_matches = cur.fetchone()["total"]
            cur.execute(
                "SELECT COUNT(*) total FROM weather_daily_observations o JOIN weather_station_point_matches sm "
                "ON sm.station_id=o.station_id WHERE o.is_current=1 AND o.temporal_alignment IN ('verified','compatible') "
                "AND sm.temporal_alignment IN ('verified','compatible')"
            )
            eligible = cur.fetchone()["total"]
            cur.execute("SELECT COUNT(*) total FROM weather_quality_issues WHERE resolved_at_utc IS NULL AND severity IN ('error','critical')")
            open_quality_errors = cur.fetchone()["total"]
        return {
            **self.sampling_status(), "running_pipelines": running, "expired_pipeline_leases": expired,
            "models": models, "current_observations": observations, "primary_station_matches": station_matches,
            "accuracy_eligible_observations": eligible, "open_quality_errors": open_quality_errors,
        }

    def analysis_summary(self) -> dict[str, Any]:
        with cursor(self.settings) as (_, cur):
            cur.execute(
                "SELECT COUNT(*) comparisons FROM weather_geo_unit_points gm "
                "JOIN weather_station_point_matches sm ON sm.sampling_point_id=gm.sampling_point_id AND sm.is_primary=1 "
                "JOIN weather_daily_observations o ON o.station_id=sm.station_id AND o.is_current=1 "
                "JOIN weather_daily_forecasts f ON f.sampling_point_id=gm.sampling_point_id AND f.target_date=o.observation_date "
                "WHERE o.temporal_alignment IN ('verified','compatible') AND sm.temporal_alignment IN ('verified','compatible') "
                "AND f.temperature_max_c IS NOT NULL AND f.temperature_min_c IS NOT NULL "
                "AND o.temperature_max_c IS NOT NULL AND o.temperature_min_c IS NOT NULL"
            )
            count = cur.fetchone()["comparisons"]
        return {"eligible_comparisons": count, "accuracy_ready": count > 0}

    def store_forecast_response(self, response: ForecastResponse, logical_date: date, source_run_key: str,
                                request_hash: bytes, run_kind: str = "live_latest", initialization_at: datetime | None = None,
                                pipeline_batch_id: int | None = None, archive_uri: str | None = None,
                                response_sha256: bytes | None = None) -> dict[str, int]:
        model_id = self.model_id(response.model_slug)
        inserted = duplicates = 0
        with cursor(self.settings) as (connection, cur):
            cur.execute(
                "INSERT INTO weather_forecast_runs(model_id,pipeline_batch_id,source_run_key,run_kind,initialization_at_utc,collected_at_utc,"
                "forecast_timezone,request_hash,response_archive_uri,response_sha256,metadata_json) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(id)",
                (model_id, pipeline_batch_id, source_run_key, run_kind, initialization_at, response.collected_at_utc.replace(tzinfo=None),
                 response.timezone_name, request_hash, archive_uri, response_sha256 or hashlib.sha256(response.raw_bytes).digest(),
                 json.dumps({"raw_archived": bool(archive_uri)})),
            )
            run_id = cur.lastrowid
            for day in response.days:
                cur.execute(
                    "INSERT IGNORE INTO weather_daily_forecasts(forecast_run_id,model_id,source_run_key,sampling_point_id,target_date,lead_days,"
                    "temperature_max_c,temperature_min_c,requested_latitude,requested_longitude,model_grid_latitude,"
                    "model_grid_longitude,forecast_day_timezone,quality_status) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (run_id, model_id, source_run_key, response.point.id, day.target_date, day.lead_days, day.temperature_max_c, day.temperature_min_c,
                     response.point.latitude, response.point.longitude, response.grid_latitude, response.grid_longitude,
                     response.timezone_name, "valid" if day.temperature_max_c is not None and day.temperature_min_c is not None else "unavailable"),
                )
                if cur.rowcount:
                    inserted += 1
                else:
                    duplicates += 1
            if inserted:
                cur.execute("UPDATE weather_sampling_points SET forecast_coverage=TRUE WHERE id=%s", (response.point.id,))
            connection.commit()
        return {"inserted": inserted, "duplicates": duplicates}

    def locations(self, query: str | None, parent_id: int | None, page: int, page_size: int) -> dict:
        conditions = ["u.country_iso3='EGY'"]
        params: list[Any] = []
        if parent_id is not None:
            conditions.append("u.parent_id=%s")
            params.append(parent_id)
        if query:
            conditions.append("(u.name LIKE %s OR u.name_local LIKE %s OR EXISTS(SELECT 1 FROM geo_name n WHERE n.unit_id=u.id AND n.name LIKE %s))")
            token = f"%{query}%"
            params.extend([token, token, token])
        where = " AND ".join(conditions)
        offset = (page - 1) * page_size
        with cursor(self.settings) as (_, cur):
            cur.execute(f"SELECT COUNT(*) total FROM geo_unit u WHERE {where}", params)
            total = cur.fetchone()["total"]
            cur.execute(
                f"SELECT u.id,u.parent_id,u.admin_level,u.admin_type,u.name,u.name_local,"
                "p.id sampling_point_id,p.latitude,p.longitude,p.forecast_coverage,p.observation_coverage "
                "FROM geo_unit u LEFT JOIN weather_geo_unit_points m ON m.geo_unit_id=u.id AND m.role='primary' AND m.active=1 "
                "LEFT JOIN weather_sampling_points p ON p.id=m.sampling_point_id "
                f"WHERE {where} ORDER BY u.admin_level,u.name LIMIT %s OFFSET %s",
                [*params, page_size, offset],
            )
            items = cur.fetchall()
        return {"items": [self._json_row(row) for row in items], "page": page, "page_size": page_size, "total": total}

    def location(self, unit_id: int) -> dict | None:
        with cursor(self.settings) as (_, cur):
            cur.execute(
                "SELECT u.id,u.parent_id,u.admin_level,u.admin_type,u.name,u.name_local,u.iso_code,"
                "p.id sampling_point_id,p.latitude,p.longitude,p.forecast_coverage,p.observation_coverage "
                "FROM geo_unit u LEFT JOIN weather_geo_unit_points m ON m.geo_unit_id=u.id AND m.role='primary' AND m.active=1 "
                "LEFT JOIN weather_sampling_points p ON p.id=m.sampling_point_id WHERE u.id=%s",
                (unit_id,),
            )
            row = cur.fetchone()
        return self._json_row(row) if row else None

    def models(self) -> list[dict]:
        with cursor(self.settings) as (_, cur):
            cur.execute(
                "SELECT m.id,m.slug,m.display_name,m.organization,m.provider_model_id,m.grid_resolution_km,"
                "m.maximum_horizon_days,s.distributor,s.license_name FROM weather_models m "
                "JOIN weather_sources s ON s.id=m.source_id WHERE m.active=1 ORDER BY m.id"
            )
            return [self._json_row(row) for row in cur.fetchall()]

    def upsert_stations(self, stations: list[dict]) -> dict[str, int]:
        inserted = updated = 0
        with cursor(self.settings) as (connection, cur):
            cur.execute("SELECT id FROM weather_sources WHERE slug='noaa_ghcn_daily'")
            source = cur.fetchone()
            if not source:
                raise RuntimeError("Run the Phase 2 migration before station discovery")
            for station in stations:
                cur.execute(
                    "INSERT INTO weather_stations(source_id,source_station_id,name,country_code,latitude,longitude,elevation_m,"
                    "has_tmax,has_tmin,first_observation_date,last_observation_date,reporting_frequency,reporting_day_convention) "
                    "VALUES(%s,%s,%s,'EG',%s,%s,%s,%s,%s,%s,%s,'daily','GHCN source date; exact local reporting window requires station metadata verification') "
                    "ON DUPLICATE KEY UPDATE name=VALUES(name),latitude=VALUES(latitude),longitude=VALUES(longitude),"
                    "elevation_m=VALUES(elevation_m),has_tmax=VALUES(has_tmax),has_tmin=VALUES(has_tmin),"
                    "first_observation_date=VALUES(first_observation_date),last_observation_date=VALUES(last_observation_date)",
                    (source["id"], station["source_station_id"], station["name"], station["latitude"], station["longitude"],
                     station["elevation_m"], station["has_tmax"], station["has_tmin"], date(station["first_year"], 1, 1), date(station["last_year"], 12, 31)),
                )
                if cur.rowcount == 1:
                    inserted += 1
                else:
                    updated += 1
            connection.commit()
        return {"inserted": inserted, "updated": updated}

    def stations(self, limit: int = 100, recent_year: int | None = None) -> list[dict]:
        sql = "SELECT * FROM weather_stations WHERE active=1"
        params: list[Any] = []
        if recent_year:
            sql += " AND last_observation_date >= %s"
            params.append(date(recent_year, 1, 1))
        sql += " ORDER BY last_observation_date DESC,source_station_id LIMIT %s"
        params.append(limit)
        with cursor(self.settings) as (_, cur):
            cur.execute(sql, params)
            return [self._json_row(row) for row in cur.fetchall()]

    def match_nearest_stations(self, maximum_distance_km: float = 250.0) -> dict[str, int]:
        """Record the nearest station as context, never as an in-place local measurement."""
        with cursor(self.settings) as (connection, cur):
            cur.execute("SELECT id,latitude,longitude,elevation_m FROM weather_stations WHERE active=1 AND has_tmax=1 AND has_tmin=1")
            stations = cur.fetchall()
            cur.execute("SELECT id,latitude,longitude,elevation_m FROM weather_sampling_points WHERE active=1")
            points = cur.fetchall()
            matched = too_far = 0
            for point in points:
                nearest = None
                for station in stations:
                    lat1, lat2 = math.radians(float(point["latitude"])), math.radians(float(station["latitude"]))
                    dlat = lat2 - lat1
                    dlon = math.radians(float(station["longitude"]) - float(point["longitude"]))
                    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
                    distance = 6371.0088 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
                    if nearest is None or distance < nearest[0]:
                        nearest = (distance, station)
                if nearest is None or nearest[0] > maximum_distance_km:
                    too_far += 1
                    continue
                distance, station = nearest
                elevation_difference = None
                if point["elevation_m"] is not None and station["elevation_m"] is not None:
                    elevation_difference = abs(Decimal(str(point["elevation_m"])) - Decimal(str(station["elevation_m"])))
                score = max(Decimal("0"), Decimal("1") - Decimal(str(distance)) / Decimal(str(maximum_distance_km)))
                cur.execute("UPDATE weather_station_point_matches SET is_primary=FALSE WHERE sampling_point_id=%s", (point["id"],))
                cur.execute(
                    "INSERT INTO weather_station_point_matches(station_id,sampling_point_id,distance_km,elevation_difference_m,"
                    "temporal_alignment,suitability_score,is_primary,metadata_json) VALUES(%s,%s,%s,%s,'uncertain',%s,TRUE,%s) "
                    "ON DUPLICATE KEY UPDATE distance_km=VALUES(distance_km),elevation_difference_m=VALUES(elevation_difference_m),"
                    "suitability_score=VALUES(suitability_score),is_primary=TRUE,updated_at=CURRENT_TIMESTAMP",
                    (station["id"], point["id"], round(distance, 3), elevation_difference, score,
                     json.dumps({"use": "nearest station context only", "accuracy_eligible": False})),
                )
                matched += 1
            connection.commit()
        return {"sampling_points": len(points), "candidate_stations": len(stations), "matched": matched, "beyond_maximum_distance": too_far}

    ALIGNMENT_POLICY = "policy-v1: GHCN-Daily date = Africa/Cairo calendar day; QC flag blank; station <= {km} km"

    def apply_alignment_policy(self, maximum_distance_km: float = 50.0) -> dict[str, int]:
        """Mark comparable station/point pairs and QC-passed observations as 'compatible'.

        Rows explicitly set to 'verified' by an operator are never changed.
        """
        policy = self.ALIGNMENT_POLICY.format(km=maximum_distance_km)
        with cursor(self.settings) as (connection, cur):
            cur.execute(
                "UPDATE weather_station_point_matches SET temporal_alignment="
                "IF(distance_km<=%s,'compatible','incompatible') "
                "WHERE is_primary=1 AND temporal_alignment<>'verified'",
                (maximum_distance_km,),
            )
            matches = cur.rowcount
            # GHCN quality flags are stored as 'M=..;Q=..;S=..|M=..;Q=..;S=..' (TMAX|TMIN); Q blank = passed QC.
            cur.execute(
                "UPDATE weather_daily_observations SET temporal_alignment="
                "IF(quality_flags IS NULL OR quality_flags NOT REGEXP 'Q=[^ ;|]','compatible','incompatible'),"
                "reporting_day_convention=%s WHERE is_current=1 AND temporal_alignment='uncertain'",
                (policy,),
            )
            observations = cur.rowcount
            connection.commit()
            cur.execute(
                "SELECT temporal_alignment,COUNT(*) total FROM weather_station_point_matches WHERE is_primary=1 GROUP BY temporal_alignment"
            )
            match_counts = {row["temporal_alignment"]: row["total"] for row in cur.fetchall()}
        return {"policy": policy, "matches_updated": matches, "observations_updated": observations,
                "primary_matches_by_alignment": match_counts}

    def store_observations(self, station_id: int, observations: list) -> dict[str, int]:
        inserted = duplicates = 0
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with cursor(self.settings) as (connection, cur):
            for item in observations:
                revision_text = f"{item.temperature_max_c}|{item.temperature_min_c}|{item.quality_flags}"
                revision_key = hashlib.sha256(revision_text.encode()).hexdigest()
                cur.execute(
                    "SELECT id,source_revision_key FROM weather_daily_observations WHERE station_id=%s AND observation_date=%s AND is_current=1",
                    (station_id, item.observation_date),
                )
                previous = cur.fetchone()
                if previous and previous["source_revision_key"] == revision_key:
                    duplicates += 1
                    continue
                if previous:
                    cur.execute("UPDATE weather_daily_observations SET is_current=FALSE WHERE id=%s", (previous["id"],))
                cur.execute(
                    "INSERT INTO weather_daily_observations(station_id,observation_date,temperature_max_c,temperature_min_c,"
                    "source_observation_id,source_revision_key,quality_flags,reporting_day_convention,temporal_alignment,collected_at_utc,is_current) "
                    "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,'uncertain',%s,TRUE)",
                    (station_id, item.observation_date, item.temperature_max_c, item.temperature_min_c,
                     f"{item.station_identifier}:{item.observation_date.isoformat()}", revision_key, item.quality_flags,
                     "GHCN source date; comparison ineligible until reporting window is verified", now),
                )
                observation_id = cur.lastrowid
                if previous:
                    cur.execute(
                        "INSERT INTO weather_observation_revisions(observation_id,superseded_observation_id,revision_reason) VALUES(%s,%s,'source value changed')",
                        (observation_id, previous["id"]),
                    )
                inserted += 1
            connection.commit()
        return {"inserted": inserted, "duplicates": duplicates}

    def forecast_history(self, location_id: int, target_date: date, model: str | None = None) -> list[dict]:
        params: list[Any] = [location_id, target_date]
        model_filter = ""
        if model:
            model_filter = " AND m.slug=%s"
            params.append(model)
        with cursor(self.settings) as (_, cur):
            cur.execute(
                "SELECT m.slug model_slug,m.display_name,f.target_date,f.lead_days,f.temperature_max_c,f.temperature_min_c,"
                "r.initialization_at_utc,r.issued_at_utc,r.collected_at_utc,r.run_kind,p.latitude,p.longitude "
                "FROM weather_geo_unit_points gm JOIN weather_sampling_points p ON p.id=gm.sampling_point_id "
                "JOIN weather_daily_forecasts f ON f.sampling_point_id=p.id "
                "JOIN weather_forecast_runs r ON r.id=f.forecast_run_id JOIN weather_models m ON m.id=r.model_id "
                "WHERE gm.geo_unit_id=%s AND gm.role='primary' AND f.target_date=%s" + model_filter +
                " ORDER BY m.slug,f.lead_days DESC,r.collected_at_utc DESC LIMIT 500",
                params,
            )
            return [self._json_row(row) for row in cur.fetchall()]

    def latest_forecasts(self, location_id: int, start_date: date | None = None, end_date: date | None = None,
                         model: str | None = None, limit: int = 200) -> list[dict]:
        conditions = ["gm.geo_unit_id=%s", "gm.role='primary'"]
        params: list[Any] = [location_id]
        if start_date:
            conditions.append("f.target_date >= %s")
            params.append(start_date)
        if end_date:
            conditions.append("f.target_date <= %s")
            params.append(end_date)
        if model:
            conditions.append("m.slug=%s")
            params.append(model)
        params.append(limit)
        with cursor(self.settings) as (_, cur):
            cur.execute(
                "SELECT m.slug model_slug,m.display_name,f.target_date,f.lead_days,f.temperature_max_c,f.temperature_min_c,"
                "r.initialization_at_utc,r.collected_at_utc,r.run_kind,p.latitude,p.longitude "
                "FROM weather_geo_unit_points gm JOIN weather_sampling_points p ON p.id=gm.sampling_point_id "
                "JOIN weather_daily_forecasts f ON f.sampling_point_id=p.id JOIN weather_forecast_runs r ON r.id=f.forecast_run_id "
                "JOIN weather_models m ON m.id=r.model_id WHERE " + " AND ".join(conditions) +
                " ORDER BY f.target_date,m.slug,r.collected_at_utc DESC LIMIT %s",
                params,
            )
            return [self._json_row(row) for row in cur.fetchall()]

    def actuals(self, location_id: int, start_date: date | None, end_date: date | None, limit: int = 200) -> list[dict]:
        conditions = ["gm.geo_unit_id=%s", "gm.role='primary'", "o.is_current=1", "sm.is_primary=1"]
        params: list[Any] = [location_id]
        if start_date:
            conditions.append("o.observation_date >= %s")
            params.append(start_date)
        if end_date:
            conditions.append("o.observation_date <= %s")
            params.append(end_date)
        params.append(limit)
        with cursor(self.settings) as (_, cur):
            cur.execute(
                "SELECT o.observation_date,o.temperature_max_c,o.temperature_min_c,o.quality_flags,o.temporal_alignment,"
                "s.source_station_id,s.name station_name,sm.distance_km,sm.elevation_difference_m "
                "FROM weather_geo_unit_points gm JOIN weather_station_point_matches sm ON sm.sampling_point_id=gm.sampling_point_id "
                "JOIN weather_stations s ON s.id=sm.station_id JOIN weather_daily_observations o ON o.station_id=s.id "
                "WHERE " + " AND ".join(conditions) + " ORDER BY o.observation_date DESC LIMIT %s",
                params,
            )
            return [self._json_row(row) for row in cur.fetchall()]

    def accuracy(self, location_id: int, start_date: date | None, end_date: date | None,
                 model: str | None, minimum_samples: int = 1) -> list[dict]:
        conditions = ["gm.geo_unit_id=%s", "gm.role='primary'", "o.is_current=1",
                      "o.temporal_alignment IN ('verified','compatible')", "sm.temporal_alignment IN ('verified','compatible')"]
        params: list[Any] = [location_id]
        if start_date:
            conditions.append("f.target_date >= %s")
            params.append(start_date)
        if end_date:
            conditions.append("f.target_date <= %s")
            params.append(end_date)
        if model:
            conditions.append("m.slug=%s")
            params.append(model)
        params.append(minimum_samples)
        with cursor(self.settings) as (_, cur):
            cur.execute(
                "SELECT m.slug model_slug,f.lead_days,COUNT(*) sample_count,"
                "AVG(ABS(f.temperature_max_c-o.temperature_max_c)) max_mae,"
                "SQRT(AVG(POW(f.temperature_max_c-o.temperature_max_c,2))) max_rmse,"
                "AVG(f.temperature_max_c-o.temperature_max_c) max_bias,"
                "AVG(ABS(f.temperature_min_c-o.temperature_min_c)) min_mae,"
                "SQRT(AVG(POW(f.temperature_min_c-o.temperature_min_c,2))) min_rmse,"
                "AVG(f.temperature_min_c-o.temperature_min_c) min_bias "
                "FROM weather_geo_unit_points gm JOIN weather_station_point_matches sm ON sm.sampling_point_id=gm.sampling_point_id "
                "JOIN weather_daily_observations o ON o.station_id=sm.station_id "
                "JOIN weather_daily_forecasts f ON f.sampling_point_id=gm.sampling_point_id AND f.target_date=o.observation_date "
                "JOIN weather_forecast_runs r ON r.id=f.forecast_run_id JOIN weather_models m ON m.id=r.model_id "
                "WHERE " + " AND ".join(conditions) +
                " AND f.temperature_max_c IS NOT NULL AND f.temperature_min_c IS NOT NULL "
                "AND o.temperature_max_c IS NOT NULL AND o.temperature_min_c IS NOT NULL "
                "GROUP BY m.slug,f.lead_days HAVING COUNT(*) >= %s ORDER BY m.slug,f.lead_days",
                params,
            )
            return [self._json_row(row) for row in cur.fetchall()]

    def pipeline_status(self) -> dict:
        with cursor(self.settings) as (_, cur):
            cur.execute("SELECT COUNT(*) total FROM weather_daily_forecasts")
            forecasts = cur.fetchone()["total"]
            cur.execute("SELECT COUNT(*) total FROM weather_daily_observations WHERE is_current=1")
            observations = cur.fetchone()["total"]
            cur.execute("SELECT status,COUNT(*) count FROM weather_pipeline_runs GROUP BY status")
            runs = {row["status"]: row["count"] for row in cur.fetchall()}
            cur.execute("SELECT job_type,status,started_at_utc,finished_at_utc,error_message FROM weather_pipeline_runs ORDER BY id DESC LIMIT 10")
            recent = [self._json_row(row) for row in cur.fetchall()]
        return {"forecast_records": forecasts, "observation_records": observations, "runs": runs, "recent_runs": recent, **self.sampling_status()}

    def pipeline_runs(self, page: int, page_size: int) -> dict:
        offset = (page - 1) * page_size
        with cursor(self.settings) as (_, cur):
            cur.execute("SELECT COUNT(*) total FROM weather_pipeline_runs")
            total = cur.fetchone()["total"]
            cur.execute("SELECT * FROM weather_pipeline_runs ORDER BY id DESC LIMIT %s OFFSET %s", (page_size, offset))
            items = [self._json_row(row) for row in cur.fetchall()]
        return {"items": items, "page": page, "page_size": page_size, "total": total}

    @staticmethod
    def _json_row(row: dict) -> dict:
        return {key: json_value(value) for key, value in row.items()}
