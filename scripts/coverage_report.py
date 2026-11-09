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
    with cursor(settings) as (_, cur):
        cur.execute(
            "SELECT u.country_iso3, u.admin_level, u.admin_type, COUNT(*) AS polygon_count, "
            "COUNT(DISTINCT u.source_id) AS source_releases, MIN(s.release_version) AS oldest_release, "
            "MAX(s.release_version) AS newest_release "
            "FROM geo_unit u JOIN geo_source s ON s.id=u.source_id "
            "GROUP BY u.country_iso3,u.admin_level,u.admin_type ORDER BY u.country_iso3,u.admin_level,u.admin_type"
        )
        coverage = cur.fetchall()
        cur.execute(
            "SELECT country_iso3,admin_level,status,checkpoint_feature,inserted_count,updated_count,started_at,finished_at "
            "FROM geo_import_job ORDER BY id DESC"
        )
        jobs = cur.fetchall()
    report = {"coverage": coverage, "import_jobs": jobs, "limitations": [
        "A listed level means verified polygons were imported; source depth still varies by location.",
        "Overture subtypes are ranked from country through microhood and are not interchangeable with fixed ADM numbers.",
        "No point-only record is treated as a boundary.",
    ]}
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DatabaseUnavailable as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(3)

