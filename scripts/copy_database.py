"""Copy the local MariaDB `depi` database into Azure Database for MySQL 8 (idempotent).

Why not mysqldump? The XAMPP MariaDB 10.4 dump/client and MySQL 8 differ in DDL (IF NOT EXISTS on
columns/indexes), spatial SRID handling and authentication plugins. This script instead:

1. creates the schema on the target with the dialect-aware migrations (``scripts/migrate.py``),
2. copies every table in foreign-key dependency order (topological sort), in keyset-paginated batches,
3. converts geometries through WKB with explicit long/lat axis order and SRID 4326,
4. verifies row counts per table and a known reverse-geocoding point.

Re-running is safe: rows are upserted by primary key.

Source = the local .env settings. Target = environment variables:
    TARGET_DB_HOST, TARGET_DB_USER, TARGET_DB_PASSWORD, [TARGET_DB_NAME=depi], [TARGET_DB_PORT=3306]
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from geo.config import Settings
from geo.db import connect
from scripts.migrate import apply_migrations

BATCH = 500
MAX_PACKET_PAYLOAD = 400_000
GEOMETRY_TYPES = {"geometry", "point", "linestring", "polygon", "multipoint", "multilinestring", "multipolygon",
                  "geometrycollection"}
BINARY_TYPES = {"binary", "varbinary", "tinyblob", "blob", "mediumblob", "longblob"}


def table_order(cur, schema: str) -> list[str]:
    cur.execute("SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_TYPE='BASE TABLE'", (schema,))
    tables = {row[0] for row in cur.fetchall()}
    cur.execute("SELECT TABLE_NAME,REFERENCED_TABLE_NAME FROM information_schema.KEY_COLUMN_USAGE "
                "WHERE TABLE_SCHEMA=%s AND REFERENCED_TABLE_NAME IS NOT NULL", (schema,))
    depends = {table: set() for table in tables}
    for table, referenced in cur.fetchall():
        if table != referenced and referenced in tables:
            depends[table].add(referenced)
    ordered, done = [], set()
    while depends:
        ready = sorted(t for t, deps in depends.items() if deps <= done)
        if not ready:  # cycle: copy the rest with FK checks disabled anyway
            ready = sorted(depends)
        for table in ready:
            ordered.append(table)
            done.add(table)
            depends.pop(table)
    return ordered


def columns(cur, schema: str, table: str) -> dict[str, str]:
    cur.execute("SELECT COLUMN_NAME,DATA_TYPE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s "
                "AND (EXTRA IS NULL OR EXTRA NOT LIKE '%%GENERATED%%') ORDER BY ORDINAL_POSITION", (schema, table))
    return {name: data_type.lower() for name, data_type in cur.fetchall()}


def packet_chunks(rows, limit_bytes: int = MAX_PACKET_PAYLOAD):
    """Split rows so each multi-row INSERT stays below the server max_allowed_packet (1 MB on XAMPP)."""
    chunk, size = [], 0
    for row in rows:
        row_size = sum(len(v) if isinstance(v, (bytes, bytearray, str)) else 16 for v in row) * 2 + 64
        if chunk and size + row_size > limit_bytes:
            yield chunk
            chunk, size = [], 0
        chunk.append(tuple(row))
        size += row_size
    if chunk:
        yield chunk


def copy_table(src_cur, dst, dst_cur, source_schema: str, target_schema: str, table: str, target_mariadb: bool = False) -> int:
    source_columns = columns(src_cur, source_schema, table)
    target_columns = columns(dst_cur, target_schema, table)
    shared = [c for c in source_columns if c in target_columns]
    def select_expression(column: str) -> str:
        if source_columns[column] in GEOMETRY_TYPES:
            return f"HEX(ST_AsBinary(`{column}`)) AS `{column}`"
        if source_columns[column] in BINARY_TYPES:  # hex keeps raw bytes intact across charsets
            return f"HEX(`{column}`) AS `{column}`"
        return f"`{column}`"

    geometry_value = "ST_GeomFromWKB(UNHEX(%s), 4326)" if target_mariadb else "ST_GeomFromWKB(UNHEX(%s), 4326, 'axis-order=long-lat')"

    def value_expression(column: str) -> str:
        if target_columns[column] in GEOMETRY_TYPES:
            return geometry_value
        return "UNHEX(%s)" if source_columns[column] in BINARY_TYPES else "%s"

    select = ", ".join(select_expression(c) for c in shared)
    values = ", ".join(value_expression(c) for c in shared)
    column_list = ", ".join(f"`{c}`" for c in shared)
    updates = ", ".join(f"`{c}`=VALUES(`{c}`)" for c in shared if c != "id") or "`id`=`id`"
    insert = f"INSERT INTO `{table}` ({column_list}) VALUES ({values}) ON DUPLICATE KEY UPDATE {updates}"
    copied, last_id = 0, None
    while True:
        if last_id is None:
            src_cur.execute(f"SELECT {select} FROM `{table}` ORDER BY id LIMIT {BATCH}")
        else:
            src_cur.execute(f"SELECT {select} FROM `{table}` WHERE id > %s ORDER BY id LIMIT {BATCH}", (last_id,))
        rows = src_cur.fetchall()
        if not rows:
            break
        for chunk in packet_chunks(rows):
            dst_cur.executemany(insert, chunk)
        dst.commit()
        copied += len(rows)
        last_id = rows[-1][shared.index("id")]
    return copied


def main() -> int:
    source = Settings()
    try:
        target = replace(source, db_host=os.environ["TARGET_DB_HOST"], db_user=os.environ["TARGET_DB_USER"],
                         db_password=os.environ["TARGET_DB_PASSWORD"], db_name=os.getenv("TARGET_DB_NAME", "depi"),
                         db_port=int(os.getenv("TARGET_DB_PORT", "3306")), db_ssl_ca="", db_connect_timeout=30)
    except KeyError as exc:
        print(f"Missing environment variable {exc}", file=sys.stderr)
        return 2
    apply_migrations(target)
    src, dst = connect(source), connect(target)
    src_cur, dst_cur = src.cursor(), dst.cursor()
    dst_cur.execute("SELECT VERSION()")
    target_mariadb = "mariadb" in dst_cur.fetchone()[0].lower()
    point = "ST_GeomFromText('POINT(31.2357 30.0444)', 4326" + (")" if target_mariadb else ", 'axis-order=long-lat')")
    dst_cur.execute("SET SESSION foreign_key_checks=0")
    dst_cur.execute("SET SESSION unique_checks=0")
    report = {}
    for table in table_order(src_cur, source.db_name):
        copied = copy_table(src_cur, dst, dst_cur, source.db_name, target.db_name, table, target_mariadb)
        src_cur.execute(f"SELECT COUNT(*) FROM `{table}`")
        expected = src_cur.fetchone()[0]
        dst_cur.execute(f"SELECT COUNT(*) FROM `{table}`")
        actual = dst_cur.fetchone()[0]
        report[table] = {"copied": copied, "source_rows": expected, "target_rows": actual, "ok": expected == actual}
        print(json.dumps({"table": table, **report[table]}), flush=True)
    dst_cur.execute("SET SESSION foreign_key_checks=1")
    dst_cur.execute(f"SELECT name FROM geo_unit WHERE admin_level=3 AND ST_Intersects(geometry, {point}) LIMIT 1")
    row = dst_cur.fetchone()
    reverse_ok = bool(row) and "cairo" in str(row[0]).lower()
    ok = all(item["ok"] for item in report.values()) and reverse_ok
    print(json.dumps({"status": "complete" if ok else "issues", "tables": len(report),
                      "reverse_lookup_cairo": row[0] if row else None}), flush=True)
    app_user, app_password = os.getenv("TARGET_APP_USER"), os.getenv("TARGET_APP_PASSWORD")
    if app_user and app_password:
        # Least-privilege runtime account (no DROP, no server-level rights). Values are validated, not interpolated blindly.
        if not re.fullmatch(r"[A-Za-z0-9_]{1,32}", app_user) or not re.fullmatch(r"[A-Za-z0-9_\-]{12,64}", app_password):
            raise ValueError("TARGET_APP_USER/TARGET_APP_PASSWORD contain unsupported characters")
        account = f"'{app_user}'@'%'"
        dst_cur.execute(f"CREATE USER IF NOT EXISTS {account} IDENTIFIED BY '{app_password}'")
        dst_cur.execute(f"ALTER USER {account} IDENTIFIED BY '{app_password}'")
        dst_cur.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, REFERENCES ON `{target.db_name}`.* TO {account}")
        print(json.dumps({"app_user": app_user, "status": "granted"}), flush=True)
    for handle in (src_cur, dst_cur, src, dst):
        handle.close()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
