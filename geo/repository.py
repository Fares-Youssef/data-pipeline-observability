from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from .config import Settings
from .db import cursor


def json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


class GeographyRepository:
    def __init__(self, settings: Settings):
        self.settings = settings

    def health(self) -> dict[str, Any]:
        with cursor(self.settings) as (_, cur):
            cur.execute("SELECT VERSION() AS version")
            version = cur.fetchone()["version"]
            cur.execute("SELECT COUNT(*) AS total FROM geo_unit")
            total = cur.fetchone()["total"]
            cur.execute(
                "SELECT country_iso3, admin_level, admin_type, COUNT(*) AS records "
                "FROM geo_unit GROUP BY country_iso3, admin_level, admin_type "
                "ORDER BY country_iso3, admin_level, admin_type"
            )
            coverage = cur.fetchall()
            cur.execute(
                "SELECT status, COUNT(*) AS jobs FROM geo_import_job GROUP BY status ORDER BY status"
            )
            jobs = cur.fetchall()
        return {
            "database": "connected",
            "server_version": version,
            "geographic_records": total,
            "coverage": coverage,
            "import_jobs": jobs,
        }

    def reverse(self, latitude: float, longitude: float) -> tuple[list[dict], dict]:
        point_wkt = f"POINT({longitude:.12f} {latitude:.12f})"
        with cursor(self.settings) as (connection, cur):
            cur.execute("SELECT VERSION() AS version")
            version = cur.fetchone()["version"]
            # MySQL 8 follows the EPSG latitude/longitude axis definition unless
            # explicitly told that our WKT is conventional GIS longitude/latitude.
            point_expression = (
                "ST_GeomFromText(%s, 4326)"
                if "mariadb" in version.lower()
                else "ST_GeomFromText(%s, 4326, 'axis-order=long-lat')"
            )
            sql = f"""
            SELECT u.id, u.parent_id, u.country_iso3, u.admin_level,
                   u.admin_type, u.source_feature_id, u.name, u.name_local,
                   u.iso_code, u.area_sq_km, s.slug AS source_slug,
                   s.title AS source_title, s.release_version, s.license_name,
                   s.attribution
            FROM geo_unit AS u
            JOIN geo_source AS s ON s.id = u.source_id
            WHERE u.min_lon <= %s AND u.max_lon >= %s
              AND u.min_lat <= %s AND u.max_lat >= %s
              AND ST_Intersects(u.geometry, {point_expression})
            ORDER BY u.admin_level DESC,
                     ((u.max_lon-u.min_lon)*(u.max_lat-u.min_lat)) ASC,
                     u.id ASC
            """
            cur.execute(sql, (longitude, longitude, latitude, latitude, point_wkt))
            rows = cur.fetchall()
            if not rows:
                return [], {"candidate_count": 0, "strategy": "bbox + ST_Intersects"}
            ids = [row["id"] for row in rows]
            placeholders = ",".join(["%s"] * len(ids))
            cur.execute(
                f"SELECT unit_id, language_code, script_code, name, name_kind "
                f"FROM geo_name WHERE unit_id IN ({placeholders}) ORDER BY id",
                ids,
            )
            names = cur.fetchall()
        by_unit: dict[int, list[dict]] = {}
        for name in names:
            by_unit.setdefault(name.pop("unit_id"), []).append(name)
        for row in rows:
            row["names"] = by_unit.get(row["id"], [])
            for key, value in list(row.items()):
                row[key] = json_value(value)
        return rows, {
            "candidate_count": len(rows),
            "strategy": "bbox + ST_Intersects (SRID 4326)",
            "server_family": "MariaDB" if "mariadb" in version.lower() else "MySQL",
        }

    def geometry(self, unit_id: int) -> dict[str, Any] | None:
        with cursor(self.settings) as (_, cur):
            cur.execute(
                "SELECT id,name,name_local,admin_type,country_iso3,"
                "ST_AsGeoJSON(geometry,6) AS geometry_json "
                "FROM geo_unit WHERE id=%s",
                (unit_id,),
            )
            row = cur.fetchone()
        if not row:
            return None
        geometry = row.pop("geometry_json")
        if isinstance(geometry, bytes):
            geometry = geometry.decode("utf-8")
        return {
            "type": "Feature",
            "id": row["id"],
            "properties": row,
            "geometry": json.loads(geometry),
        }

