import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from geo.config import Settings
from geo.db import DatabaseUnavailable, connect


def statements(text: str):
    for part in text.split(";"):
        statement = part.strip()
        if statement:
            yield statement


_ADD_COLUMN = re.compile(r"^ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+(\w+)\s", re.I | re.S)
_CREATE_INDEX = re.compile(r"^CREATE\s+(UNIQUE\s+)?INDEX\s+IF\s+NOT\s+EXISTS\s+(\w+)\s+ON\s+(\w+)", re.I | re.S)


def mysql_statement(statement: str, exists) -> str | None:
    """Translate MariaDB-only idempotent DDL to MySQL 8.

    MySQL 8 has no ``ADD COLUMN IF NOT EXISTS`` / ``CREATE INDEX IF NOT EXISTS``; the existence check is done
    through ``exists(kind, table, name)`` instead. Returns None when the statement must be skipped.
    The geometry column also gets ``SRID 4326`` so MySQL can use the spatial index for ST_Intersects.
    """
    match = _ADD_COLUMN.match(statement)
    if match:
        table, column = match.groups()
        return None if exists("column", table, column) else re.sub(r"\s+IF\s+NOT\s+EXISTS", "", statement, count=1, flags=re.I)
    match = _CREATE_INDEX.match(statement)
    if match:
        _, index, table = match.groups()
        return None if exists("index", table, index) else re.sub(r"\s+IF\s+NOT\s+EXISTS", "", statement, count=1, flags=re.I)
    return re.sub(r"(\bgeometry\s+GEOMETRY\s+NOT\s+NULL)(?!\s+SRID)", r"\1 SRID 4326", statement, flags=re.I)


def apply_migrations(settings: Settings) -> None:
    migration_dir = Path(__file__).resolve().parents[1] / "migrations"
    connection = connect(settings)
    cur = connection.cursor()

    def exists(kind: str, table: str, name: str) -> bool:
        source = "COLUMNS" if kind == "column" else "STATISTICS"
        field = "COLUMN_NAME" if kind == "column" else "INDEX_NAME"
        cur.execute(f"SELECT COUNT(*) FROM information_schema.{source} "
                    f"WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND {field}=%s", (table, name))
        return cur.fetchone()[0] > 0

    try:
        cur.execute("SELECT VERSION()")
        is_mariadb = "mariadb" in cur.fetchone()[0].lower()
        for migration in sorted(migration_dir.glob("*.sql")):
            for statement in statements(migration.read_text(encoding="utf-8")):
                if not is_mariadb:
                    statement = mysql_statement(statement, exists)
                    if statement is None:
                        continue
                cur.execute(statement)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cur.close()
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Create the safe Phase 1 geography schema.")
    parser.add_argument("--create-database", action="store_true", help="Create DB_NAME if the account is allowed")
    args = parser.parse_args()
    settings = Settings()
    if settings.missing_database_settings():
        print("Missing DB_USER. Copy .env.example to .env and configure the database account.", file=sys.stderr)
        return 2
    if not re.fullmatch(r"[A-Za-z0-9_]+", settings.db_name):
        print("DB_NAME may contain only letters, digits, and underscores.", file=sys.stderr)
        return 2
    if args.create_database:
        connection = connect(settings, include_database=False)
        cur = connection.cursor()
        cur.execute(
            f"CREATE DATABASE IF NOT EXISTS `{settings.db_name}` "
            "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
        )
        connection.commit()
        cur.close()
        connection.close()
    apply_migrations(settings)
    print(f"Geography and weather schema is ready in {settings.db_name}.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DatabaseUnavailable as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(3)

