"""Import detailed Overture administrative and neighborhood polygons.

Default scope is Egypt. Use --all explicitly for the complete global dataset.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCAL_DEPENDENCIES = ROOT / ".deps"
if LOCAL_DEPENDENCIES.is_dir():
    sys.path.insert(0, str(LOCAL_DEPENDENCIES))
sys.path.insert(0, str(ROOT))

import pycountry
from shapely.geometry import GeometryCollection, MultiPolygon, shape
from shapely.validation import make_valid

from geo.config import Settings
from geo.db import DatabaseUnavailable, connect


EGYPT_BBOX = "24.5,21.7,37.2,31.8"
SUBTYPE_RANK = {
    "country": 0,
    "dependency": 1,
    "macroregion": 2,
    "region": 3,
    "macrocounty": 4,
    "county": 5,
    "localadmin": 6,
    "locality": 7,
    "borough": 8,
    "macrohood": 9,
    "neighborhood": 10,
    "microhood": 11,
}
MIN_MAX_ALLOWED_PACKET = 256 * 1024 * 1024


def alpha3(alpha2: str) -> str:
    country = pycountry.countries.get(alpha_2=alpha2.upper()) if alpha2 else None
    if country:
        return country.alpha_3
    # Overture/OSM commonly use XK for Kosovo, which is not ISO-assigned.
    return "XKX" if alpha2 == "XK" else (alpha2 or "UNK")[:3].upper()


def polygonal(geometry):
    geometry = make_valid(geometry)
    if geometry.geom_type in {"Polygon", "MultiPolygon"}:
        return geometry
    if isinstance(geometry, GeometryCollection):
        polygons = []
        for part in geometry.geoms:
            if part.geom_type == "Polygon":
                polygons.append(part)
            elif part.geom_type == "MultiPolygon":
                polygons.extend(part.geoms)
        return MultiPolygon(polygons) if polygons else None
    return None


def common_names(names: dict | None) -> dict[str, str]:
    result: dict[str, str] = {}
    common = (names or {}).get("common") or []
    items = common.items() if isinstance(common, dict) else common
    for item in items:
        if isinstance(item, (list, tuple)) and len(item) >= 2 and item[0] and item[1]:
            result[str(item[0])] = str(item[1])
    return result


def download_overture(feature_type: str, output: Path, *, all_world: bool, refresh: bool) -> None:
    state_file = Path(str(output) + ".state")
    if refresh:
        for path in (output, state_file):
            if path.exists():
                path.unlink()
    if output.exists() and output.stat().st_size > 0:
        print(f"Using cached {output}")
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable, "-m", "overturemaps", "download",
        "-f", "geojsonseq", "--type", feature_type, "-o", str(output),
    ]
    if not all_world:
        command.insert(4, f"--bbox={EGYPT_BBOX}")
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(LOCAL_DEPENDENCIES) + os.pathsep + environment.get("PYTHONPATH", "")
    print(">", " ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, env=environment, check=True)


def latest_release() -> str:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(LOCAL_DEPENDENCIES) + os.pathsep + environment.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, "-m", "overturemaps", "releases", "latest"],
        cwd=ROOT, env=environment, check=True, capture_output=True, text=True,
    )
    return result.stdout.strip().splitlines()[-1].strip()


def build_hierarchy_index(path: Path, sqlite_path: Path, country_filter: str | None) -> sqlite3.Connection:
    if sqlite_path.exists():
        sqlite_path.unlink()
    db = sqlite3.connect(sqlite_path)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute(
        "CREATE TABLE division (id TEXT PRIMARY KEY, country TEXT, subtype TEXT, "
        "parent_id TEXT, hierarchies_json TEXT NOT NULL)"
    )
    batch = []
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            feature = json.loads(line)
            properties = feature.get("properties") or {}
            country = properties.get("country")
            if country_filter and country != country_filter:
                continue
            batch.append((
                feature["id"], country, properties.get("subtype"),
                properties.get("parent_division_id"),
                json.dumps(properties.get("hierarchies") or [], ensure_ascii=False),
            ))
            if len(batch) >= 2000:
                db.executemany("INSERT OR REPLACE INTO division VALUES (?,?,?,?,?)", batch)
                db.commit()
                batch.clear()
    if batch:
        db.executemany("INSERT OR REPLACE INTO division VALUES (?,?,?,?,?)", batch)
        db.commit()
    count = db.execute("SELECT COUNT(*) FROM division").fetchone()[0]
    print(f"Indexed {count:,} Overture division hierarchies")
    return db


def reset_geography(settings: Settings) -> None:
    connection = connect(settings)
    cur = connection.cursor()
    try:
        cur.execute("DELETE FROM geo_name")
        cur.execute("UPDATE geo_unit SET parent_id=NULL")
        cur.execute("DELETE FROM geo_unit")
        cur.execute("DELETE FROM geo_import_job")
        cur.execute("DELETE FROM geo_source")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cur.close()
        connection.close()
    print("Cleared geography tables only; unrelated depi tables were preserved.")


def ensure_packet_capacity(settings: Settings) -> None:
    connection = connect(settings)
    cur = connection.cursor()
    try:
        cur.execute("SELECT @@GLOBAL.max_allowed_packet")
        if int(cur.fetchone()[0]) < MIN_MAX_ALLOWED_PACKET:
            cur.execute(f"SET GLOBAL max_allowed_packet={MIN_MAX_ALLOWED_PACKET}")
    finally:
        cur.close()
        connection.close()


def create_source(cur, release: str) -> int:
    metadata = {
        "theme": "divisions", "type": "division_area", "release": release,
        "documentation": "https://docs.overturemaps.org/guides/divisions/",
    }
    cur.execute(
        "INSERT INTO geo_source "
        "(slug,title,release_version,source_url,license_name,license_url,attribution,metadata_json) "
        "VALUES ('overture_divisions',%s,%s,%s,%s,%s,%s,%s) "
        "ON DUPLICATE KEY UPDATE title=VALUES(title),metadata_json=VALUES(metadata_json),id=LAST_INSERT_ID(id)",
        (
            "Overture Maps Divisions", release, "https://overturemaps.org/",
            "ODbL 1.0 (divisions includes OpenStreetMap)",
            "https://docs.overturemaps.org/attribution/",
            "Overture Maps Foundation; includes OpenStreetMap contributors",
            json.dumps(metadata),
        ),
    )
    return cur.lastrowid


def hierarchy_candidates(index: sqlite3.Connection, division_id: str) -> list[str]:
    row = index.execute(
        "SELECT parent_id,hierarchies_json FROM division WHERE id=?", (division_id,)
    ).fetchone()
    if not row:
        return []
    parent_id, raw = row
    candidates = []
    if parent_id:
        candidates.append(parent_id)
    for hierarchy in json.loads(raw):
        ids = [item.get("division_id") for item in hierarchy if item.get("division_id") != division_id]
        for identifier in reversed(ids):
            if identifier and identifier not in candidates:
                candidates.append(identifier)
    return candidates


def import_areas(
    settings: Settings, area_path: Path, hierarchy: sqlite3.Connection,
    release: str, country_filter: str | None, batch_size: int,
) -> tuple[int, int]:
    connection = connect(settings)
    cur = connection.cursor()
    inserted = updated = processed = 0
    try:
        cur.execute("SELECT VERSION()")
        is_mariadb = "mariadb" in cur.fetchone()[0].lower()
        geometry_expression = (
            "ST_GeomFromWKB(UNHEX(%s),4326)" if is_mariadb
            else "ST_GeomFromWKB(UNHEX(%s),4326,'axis-order=long-lat')"
        )
        source_id = create_source(cur, release)
        scope_iso3 = alpha3(country_filter) if country_filter else "ALL"
        cur.execute(
            "UPDATE geo_import_job SET status='failed',error_message='Superseded by resumed Overture import',finished_at=NOW() "
            "WHERE country_iso3=%s AND admin_level=255 AND status='running'", (scope_iso3,)
        )
        cur.execute(
            "INSERT INTO geo_import_job (source_id,country_iso3,admin_level,status,download_url) "
            "VALUES (%s,%s,255,'running',%s)",
            (source_id, scope_iso3, "Overture division_area GeoJSONSeq"),
        )
        job_id = cur.lastrowid
        connection.commit()
        with area_path.open("r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                feature = json.loads(line)
                properties = feature.get("properties") or {}
                country = properties.get("country")
                if country_filter and country != country_filter:
                    continue
                if properties.get("is_land") is not True:
                    continue
                subtype = properties.get("subtype")
                if subtype not in SUBTYPE_RANK:
                    continue
                geometry = polygonal(shape(feature["geometry"]))
                if geometry is None or geometry.is_empty:
                    continue
                names = properties.get("names") or {}
                translations = common_names(names)
                primary = str(names.get("primary") or feature["id"])
                display_name = translations.get("en") or primary
                local_name = translations.get("ar")
                division_id = str(properties.get("division_id") or feature["id"])
                bounds = geometry.bounds
                point = geometry.representative_point()
                wkb = geometry.wkb
                digest = hashlib.sha256(wkb).digest()
                cur.execute(
                    "SELECT id,geometry_sha256 FROM geo_unit WHERE source_id=%s AND source_feature_id=%s",
                    (source_id, feature["id"]),
                )
                existing = cur.fetchone()
                stored_properties = dict(properties)
                stored_properties["overture_area_id"] = feature["id"]
                cur.execute(
                    "INSERT INTO geo_unit "
                    "(source_id,source_feature_id,source_division_id,parent_id,parent_relation_method,country_iso3,"
                    "admin_level,admin_type,name,name_local,iso_code,centroid_lon,centroid_lat,min_lon,min_lat,max_lon,max_lat,"
                    "geometry,geometry_sha256,properties_json) "
                    f"VALUES (%s,%s,%s,NULL,'none',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,{geometry_expression},%s,%s) "
                    "ON DUPLICATE KEY UPDATE source_division_id=VALUES(source_division_id),parent_id=NULL,"
                    "parent_relation_method='none',country_iso3=VALUES(country_iso3),admin_level=VALUES(admin_level),"
                    "admin_type=VALUES(admin_type),name=VALUES(name),name_local=VALUES(name_local),iso_code=VALUES(iso_code),"
                    "centroid_lon=VALUES(centroid_lon),centroid_lat=VALUES(centroid_lat),min_lon=VALUES(min_lon),"
                    "min_lat=VALUES(min_lat),max_lon=VALUES(max_lon),max_lat=VALUES(max_lat),geometry=VALUES(geometry),"
                    "geometry_sha256=VALUES(geometry_sha256),properties_json=VALUES(properties_json),id=LAST_INSERT_ID(id)",
                    (
                        source_id, feature["id"], division_id, alpha3(country), SUBTYPE_RANK[subtype], subtype,
                        display_name, local_name, properties.get("region"), point.x, point.y, *bounds,
                        wkb.hex(), digest, json.dumps(stored_properties, ensure_ascii=False, default=str),
                    ),
                )
                unit_id = cur.lastrowid
                if existing:
                    updated += int(existing[1] != digest)
                else:
                    inserted += 1
                cur.execute("DELETE FROM geo_name WHERE unit_id=%s", (unit_id,))
                name_rows = {(display_name, "en" if translations.get("en") else "und", "primary")}
                if primary != display_name:
                    name_rows.add((primary, "und", "official"))
                for language, value in translations.items():
                    name_rows.add((value, language, "local" if language == "ar" else "alias"))
                cur.executemany(
                    "INSERT IGNORE INTO geo_name (unit_id,language_code,name,name_kind) VALUES (%s,%s,%s,%s)",
                    [(unit_id, language, value, kind) for value, language, kind in name_rows],
                )
                processed += 1
                if processed % batch_size == 0:
                    cur.execute(
                        "UPDATE geo_import_job SET checkpoint_feature=%s,inserted_count=%s,updated_count=%s WHERE id=%s",
                        (line_number, inserted, updated, job_id),
                    )
                    connection.commit()
                    print(f"Imported {processed:,} polygons")

        cur.execute(
            "SELECT id,source_division_id FROM geo_unit WHERE source_id=%s AND source_division_id IS NOT NULL",
            (source_id,),
        )
        unit_ids = {division_id: unit_id for unit_id, division_id in cur.fetchall()}
        parent_updates = []
        for division_id, unit_id in unit_ids.items():
            parent = next((unit_ids[candidate] for candidate in hierarchy_candidates(hierarchy, division_id) if candidate in unit_ids), None)
            if parent:
                parent_updates.append((parent, unit_id))
        for start in range(0, len(parent_updates), batch_size):
            cur.executemany(
                "UPDATE geo_unit SET parent_id=%s,parent_relation_method='source' WHERE id=%s",
                parent_updates[start:start + batch_size],
            )
            connection.commit()
        cur.execute(
            "UPDATE geo_import_job SET status='complete',checkpoint_feature=%s,inserted_count=%s,updated_count=%s,finished_at=NOW() WHERE id=%s",
            (line_number if 'line_number' in locals() else 0, inserted, updated, job_id),
        )
        connection.commit()
    except Exception as exc:
        try:
            connection.rollback()
            if 'job_id' in locals():
                cur.execute(
                    "UPDATE geo_import_job SET status='failed',error_message=%s,finished_at=NOW() WHERE id=%s",
                    (str(exc)[:4000], job_id),
                )
                connection.commit()
        except Exception:
            pass
        raise
    finally:
        cur.close()
        connection.close()
    print(f"Overture import complete: inserted={inserted:,}, changed={updated:,}, parent_links={len(parent_updates):,}")
    return inserted, updated


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import detailed Overture divisions. Default: all available Egypt detail."
    )
    parser.add_argument("--all", action="store_true", help="Import the full global divisions dataset (large)")
    parser.add_argument("--reset", action="store_true", help="Clear only geography tables before importing")
    parser.add_argument("--refresh", action="store_true", help="Redownload current Overture files")
    parser.add_argument("--batch-size", type=int, default=250)
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error("--batch-size must be at least 1")
    settings = Settings()
    if settings.missing_database_settings():
        print("Missing DB_USER; configure .env first.", file=sys.stderr)
        return 2
    scope = "world" if args.all else "egypt"
    country_filter = None if args.all else "EG"
    download_dir = ROOT / "data" / "downloads" / "overture"
    area_path = download_dir / f"{scope}_division_area.geojsonseq"
    division_path = download_dir / f"{scope}_division.geojsonseq"
    hierarchy_path = download_dir / f"{scope}_hierarchy.sqlite"
    try:
        download_overture("division_area", area_path, all_world=args.all, refresh=args.refresh)
        download_overture("division", division_path, all_world=args.all, refresh=args.refresh)
        release = latest_release()
        hierarchy = build_hierarchy_index(division_path, hierarchy_path, country_filter)
        ensure_packet_capacity(settings)
        if args.reset:
            reset_geography(settings)
        import_areas(settings, area_path, hierarchy, release, country_filter, args.batch_size)
        hierarchy.close()
    except DatabaseUnavailable as exc:
        print(exc, file=sys.stderr)
        return 3
    except subprocess.CalledProcessError as exc:
        print(f"Overture download command failed with exit code {exc.returncode}.", file=sys.stderr)
        return exc.returncode or 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

