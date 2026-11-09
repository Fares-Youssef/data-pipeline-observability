"""Verify detailed Egypt neighborhoods against stored Overture polygons."""

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCAL_DEPENDENCIES = ROOT / ".deps"
if LOCAL_DEPENDENCIES.is_dir():
    sys.path.insert(0, str(LOCAL_DEPENDENCIES))
sys.path.insert(0, str(ROOT))

from geo.config import Settings
from geo.db import cursor
from geo.repository import GeographyRepository
from geo.service import build_reverse_response


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    settings = Settings()
    repository = GeographyRepository(settings)
    targets = ["ارض اللواء", "المهندسين"]
    results = []
    with cursor(settings) as (_, cur):
        for target in targets:
            cur.execute(
                "SELECT id,name,name_local,admin_type,centroid_lat,centroid_lon "
                "FROM geo_unit WHERE country_iso3='EGY' AND (name_local=%s OR name=%s) "
                "ORDER BY admin_level DESC,id LIMIT 1",
                (target, target),
            )
            unit = cur.fetchone()
            if not unit:
                results.append({"target": target, "status": "missing"})
                continue
            rows, diagnostics = repository.reverse(unit["centroid_lat"], unit["centroid_lon"])
            response = build_reverse_response(unit["centroid_lat"], unit["centroid_lon"], rows, diagnostics)
            results.append({
                "target": target,
                "stored_unit": unit,
                "reverse_hierarchy": [
                    {"subtype": item["admin_type"], "name": item["name"], "name_local": item["name_local"]}
                    for item in response["hierarchy"]
                ],
                "resolved_neighborhood": response.get("neighborhood"),
                "status": response["match_status"],
            })
    print(json.dumps(results, ensure_ascii=False, indent=2, default=str))
    return 0 if all(item["status"] == "verified_polygon" for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

