from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCAL_DEPENDENCIES = ROOT / ".deps"
if LOCAL_DEPENDENCIES.is_dir():
    sys.path.insert(0, str(LOCAL_DEPENDENCIES))
sys.path.insert(0, str(ROOT))

import ijson
import requests
from requests.adapters import HTTPAdapter
from shapely.geometry import GeometryCollection, MultiPolygon, shape
from shapely.validation import make_valid
from urllib3.util.retry import Retry

from geo.config import Settings
from geo.db import DatabaseUnavailable, connect


API = "https://www.geoboundaries.org/api/current/gbOpen/{iso3}/{level}/"
DOWNLOADS = ROOT / "data" / "downloads"
MIN_MAX_ALLOWED_PACKET = 256 * 1024 * 1024


def session() -> requests.Session:
    result = requests.Session()
    retries = Retry(total=5, connect=5, read=5, backoff_factor=1, status_forcelist=(429, 500, 502, 503, 504))
    result.mount("https://", HTTPAdapter(max_retries=retries))
    result.headers["User-Agent"] = "DEPI-Weather-Analytics-Geography/1.0 (educational project)"
    return result


def ensure_packet_capacity(settings: Settings) -> int:
    """Raise XAMPP's tiny packet limit for full-resolution global polygons."""
    connection = connect(settings)
    cur = connection.cursor()
    try:
        cur.execute("SELECT @@GLOBAL.max_allowed_packet")
        current = int(cur.fetchone()[0])
        if current < MIN_MAX_ALLOWED_PACKET:
            try:
                cur.execute(f"SET GLOBAL max_allowed_packet={MIN_MAX_ALLOWED_PACKET}")
            except Exception as exc:
                raise RuntimeError(
                    f"MariaDB max_allowed_packet is only {current // (1024 * 1024)} MB. "
                    "The import needs at least 256 MB for full-resolution polygons. "
                    "Set max_allowed_packet=256M under [mysqld] in the XAMPP my.ini, "
                    "restart MySQL, then rerun the same command."
                ) from exc
        return max(current, MIN_MAX_ALLOWED_PACKET)
    finally:
        cur.close()
        connection.close()


def mark_job_failed(settings: Settings, job_id: int, message: str) -> None:
    """Record failure even when MariaDB dropped the original connection."""
    try:
        connection = connect(settings)
        cur = connection.cursor()
        cur.execute(
            "UPDATE geo_import_job SET status='failed',error_message=%s,finished_at=NOW() WHERE id=%s",
            (message[:4000], job_id),
        )
        connection.commit()
        cur.close()
        connection.close()
    except Exception:
        pass


def download(client: requests.Session, url: str, target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    offset = partial.stat().st_size if partial.exists() else 0
    headers = {"Range": f"bytes={offset}-"} if offset else {}
    with client.get(url, headers=headers, stream=True, timeout=(15, 120)) as response:
        response.raise_for_status()
        mode = "ab" if offset and response.status_code == 206 else "wb"
        with partial.open(mode) as stream:
            for chunk in response.iter_content(1024 * 1024):
                if chunk:
                    stream.write(chunk)
    partial.replace(target)
    return target


def polygonal(geometry):
    geometry = make_valid(geometry)
    if geometry.geom_type in {"Polygon", "MultiPolygon"}:
        return geometry
    if isinstance(geometry, GeometryCollection):
        polygons = [part for part in geometry.geoms if part.geom_type == "Polygon"]
        multipolygons = [part for part in geometry.geoms if part.geom_type == "MultiPolygon"]
        merged = polygons + [poly for multi in multipolygons for poly in multi.geoms]
        return MultiPolygon(merged) if merged else None
    return None


def source_id(cur, metadata: dict) -> int:
    release = metadata.get("buildDate") or "current"
    cur.execute(
        "INSERT INTO geo_source "
        "(slug,title,release_version,source_url,license_name,license_url,attribution,metadata_json) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON DUPLICATE KEY UPDATE title=VALUES(title), source_url=VALUES(source_url), "
        "license_name=VALUES(license_name), license_url=VALUES(license_url), "
        "attribution=VALUES(attribution), metadata_json=VALUES(metadata_json), id=LAST_INSERT_ID(id)",
        (
            "geoboundaries_gbopen",
            "geoBoundaries gbOpen",
            release,
            "https://www.geoboundaries.org/",
            metadata.get("boundaryLicense"),
            metadata.get("licenseSource"),
            "geoBoundaries and the original boundary source; see stored layer metadata",
            json.dumps(metadata, ensure_ascii=False),
        ),
    )
    return cur.lastrowid


def parent_id(cur, iso3: str, level: int, representative, point_expression: str) -> int | None:
    if level == 0:
        return None
    point_wkt = f"POINT({representative.x:.12f} {representative.y:.12f})"
    cur.execute(
        "SELECT id FROM geo_unit WHERE country_iso3=%s AND admin_level=%s "
        "AND min_lon<=%s AND max_lon>=%s AND min_lat<=%s AND max_lat>=%s "
        f"AND ST_Intersects(geometry, {point_expression}) "
        "ORDER BY ((max_lon-min_lon)*(max_lat-min_lat)) ASC, id ASC LIMIT 1",
        (iso3, level - 1, representative.x, representative.x, representative.y, representative.y, point_wkt),
    )
    row = cur.fetchone()
    return row[0] if row else None


def import_layer(settings: Settings, iso3: str, level: int, batch_size: int) -> tuple[int, int]:
    client = session()
    endpoint = API.format(iso3=iso3, level=f"ADM{level}")
    response = client.get(endpoint, timeout=(15, 60))
    if response.status_code == 404:
        print(f"{iso3} ADM{level}: unavailable")
        return 0, 0
    response.raise_for_status()
    metadata = response.json()
    if isinstance(metadata, list):
        metadata = metadata[0]
    url = metadata["gjDownloadURL"]
    path = download(client, url, DOWNLOADS / iso3 / f"ADM{level}.geojson")
    connection = connect(settings)
    cur = connection.cursor()
    inserted = updated = 0
    try:
        cur.execute("SELECT VERSION()")
        version = cur.fetchone()[0]
        is_mariadb = "mariadb" in version.lower()
        point_expression = "ST_GeomFromText(%s,4326)" if is_mariadb else "ST_GeomFromText(%s,4326,'axis-order=long-lat')"
        # mysql-connector can bind Python bytes as VARCHAR against MariaDB,
        # which ST_GeomFromWKB rejects (error 4079). Passing hexadecimal text
        # through UNHEX gives both MariaDB and MySQL an unambiguous binary WKB.
        geometry_expression = (
            "ST_GeomFromWKB(UNHEX(%s),4326)"
            if is_mariadb
            else "ST_GeomFromWKB(UNHEX(%s),4326,'axis-order=long-lat')"
        )
        sid = source_id(cur, metadata)
        cur.execute(
            "UPDATE geo_import_job SET status='failed', "
            "error_message='Superseded by a resumed idempotent import', finished_at=NOW() "
            "WHERE country_iso3=%s AND admin_level=%s AND status='running'",
            (iso3, level),
        )
        cur.execute(
            "INSERT INTO geo_import_job (source_id,country_iso3,admin_level,status,download_url) "
            "VALUES (%s,%s,%s,'running',%s)",
            (sid, iso3, level, url),
        )
        job_id = cur.lastrowid
        connection.commit()
        with path.open("rb") as stream:
            for index, feature in enumerate(ijson.items(stream, "features.item"), 1):
                properties = feature.get("properties") or {}
                geometry = polygonal(shape(feature["geometry"]))
                if geometry is None or geometry.is_empty:
                    continue
                feature_id = str(properties.get("shapeID") or feature.get("id") or f"{metadata['boundaryID']}:{index}")
                name = str(properties.get("shapeName") or properties.get("name") or feature_id)
                iso_code = properties.get("shapeISO") or properties.get("shapeGroup")
                bounds = geometry.bounds
                representative = geometry.representative_point()
                parent = parent_id(cur, iso3, level, representative, point_expression)
                geometry_wkb = geometry.wkb
                digest = hashlib.sha256(geometry_wkb).digest()
                cur.execute("SELECT id, geometry_sha256 FROM geo_unit WHERE source_id=%s AND source_feature_id=%s", (sid, feature_id))
                existing = cur.fetchone()
                cur.execute(
                    "INSERT INTO geo_unit "
                    "(source_id,source_feature_id,parent_id,parent_relation_method,country_iso3,admin_level,admin_type,name,"
                    "iso_code,centroid_lon,centroid_lat,min_lon,min_lat,max_lon,max_lat,geometry,geometry_sha256,properties_json) "
                    f"VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,{geometry_expression},%s,%s) "
                    "ON DUPLICATE KEY UPDATE parent_id=VALUES(parent_id), parent_relation_method=VALUES(parent_relation_method), "
                    "admin_type=VALUES(admin_type), name=VALUES(name), iso_code=VALUES(iso_code), "
                    "centroid_lon=VALUES(centroid_lon), centroid_lat=VALUES(centroid_lat), min_lon=VALUES(min_lon), "
                    "min_lat=VALUES(min_lat), max_lon=VALUES(max_lon), max_lat=VALUES(max_lat), "
                    "geometry=VALUES(geometry), geometry_sha256=VALUES(geometry_sha256), properties_json=VALUES(properties_json), "
                    "id=LAST_INSERT_ID(id)",
                    (
                        sid, feature_id, parent, "spatial_containment" if parent else "none", iso3, level,
                        metadata.get("boundaryCanonical") or f"ADM{level}", name, iso_code,
                        representative.x, representative.y, *bounds, geometry_wkb.hex(), digest,
                        json.dumps(properties, ensure_ascii=False, default=str),
                    ),
                )
                unit_id = cur.lastrowid
                if existing:
                    updated += int(existing[1] != digest)
                else:
                    inserted += 1
                cur.execute(
                    "INSERT IGNORE INTO geo_name (unit_id,language_code,name,name_kind) VALUES (%s,'und',%s,'primary')",
                    (unit_id, name),
                )
                if index % batch_size == 0:
                    cur.execute(
                        "UPDATE geo_import_job SET checkpoint_feature=%s,inserted_count=%s,updated_count=%s WHERE id=%s",
                        (index, inserted, updated, job_id),
                    )
                    connection.commit()
                    print(f"{iso3} ADM{level}: processed {index}")
        cur.execute(
            "UPDATE geo_import_job SET status='complete',checkpoint_feature=%s,inserted_count=%s,updated_count=%s,finished_at=NOW() WHERE id=%s",
            (index if 'index' in locals() else 0, inserted, updated, job_id),
        )
        connection.commit()
    except Exception as exc:
        try:
            connection.rollback()
        except Exception:
            pass
        if 'job_id' in locals():
            mark_job_failed(settings, job_id, str(exc))
        raise
    finally:
        cur.close()
        connection.close()
    print(f"{iso3} ADM{level}: inserted={inserted}, changed={updated}")
    return inserted, updated


def main() -> int:
    parser = argparse.ArgumentParser(description="Import real gbOpen administrative polygons into MySQL/MariaDB.")
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--country", help="ISO-3166 alpha-3 code, e.g. EGY")
    scope.add_argument("--global", dest="global_import", action="store_true", help="Import all countries reported by the API")
    parser.add_argument("--levels", default="0,1,2", help="Comma-separated ADM levels (default: 0,1,2)")
    parser.add_argument("--batch-size", type=int, default=100)
    args = parser.parse_args()
    settings = Settings()
    if settings.missing_database_settings():
        print("Missing DB_USER; configure .env first.", file=sys.stderr)
        return 2
    try:
        preflight = connect(settings)
        preflight.close()
    except DatabaseUnavailable as exc:
        print(exc, file=sys.stderr)
        return 3
    try:
        packet_limit = ensure_packet_capacity(settings)
        print(f"Database packet capacity: {packet_limit // (1024 * 1024)} MB")
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 4
    levels = sorted({int(value) for value in args.levels.split(",")})
    if any(level < 0 or level > 5 for level in levels):
        parser.error("levels must be between 0 and 5")
    if args.global_import:
        client = session()
        metadata = client.get(API.format(iso3="ALL", level="ALL"), timeout=(15, 120)).json()
        countries = sorted({item["boundaryISO"] for item in metadata})
    else:
        countries = [args.country.upper()]
    for iso3 in countries:
        for level in levels:
            try:
                import_layer(settings, iso3, level, args.batch_size)
            except requests.HTTPError as exc:
                print(f"{iso3} ADM{level}: HTTP error {exc}", file=sys.stderr)
            time.sleep(0.2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

