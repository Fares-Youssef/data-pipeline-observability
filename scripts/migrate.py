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


def apply_migrations(settings: Settings) -> None:
    migration_dir = Path(__file__).resolve().parents[1] / "migrations"
    connection = connect(settings)
    cur = connection.cursor()
    try:
        for migration in sorted(migration_dir.glob("*.sql")):
            for statement in statements(migration.read_text(encoding="utf-8")):
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

