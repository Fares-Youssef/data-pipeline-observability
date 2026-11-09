import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from geo.config import Settings
from geo.db import DatabaseUnavailable, cursor


def main() -> int:
    settings = Settings()
    if settings.missing_database_settings():
        print("Missing DB_USER; configure .env first.", file=sys.stderr)
        return 2
    report = {"database": settings.db_name, "tables": [], "geography": []}
    with cursor(settings) as (_, cur):
        cur.execute(
            "SELECT TABLE_NAME, TABLE_ROWS FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA=%s ORDER BY TABLE_NAME",
            (settings.db_name,),
        )
        report["tables"] = cur.fetchall()
        cur.execute(
            "SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, COLUMN_KEY "
            "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s "
            "AND (DATA_TYPE IN ('geometry','polygon','multipolygon','point') "
            "OR COLUMN_NAME REGEXP 'country|city|district|admin|geo|lat|lon') "
            "ORDER BY TABLE_NAME, ORDINAL_POSITION",
            (settings.db_name,),
        )
        report["geographic_columns"] = cur.fetchall()
        cur.execute(
            "SELECT COUNT(*) AS present FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA=%s AND TABLE_NAME='geo_unit'",
            (settings.db_name,),
        )
        if cur.fetchone()["present"]:
            cur.execute(
                "SELECT country_iso3, admin_level, COUNT(*) AS records "
                "FROM geo_unit GROUP BY country_iso3, admin_level ORDER BY country_iso3, admin_level"
            )
            report["geography"] = cur.fetchall()
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DatabaseUnavailable as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(3)

