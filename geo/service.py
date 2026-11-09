from __future__ import annotations

from typing import Any


EGYPT_LABELS = {0: "country", 1: "governorate", 2: "district"}
GENERIC_LABELS = {0: "country", 1: "admin_level_1", 2: "admin_level_2", 3: "admin_level_3", 4: "admin_level_4", 5: "admin_level_5"}
OVERTURE_LABELS = {
    "country": "country",
    "dependency": "country",
    "macroregion": "governorate",
    "region": "governorate",
    "macrocounty": "district",
    "county": "district",
    "localadmin": "district",
    "locality": "city",
    "borough": "area",
    "macrohood": "area",
    "neighborhood": "neighborhood",
    "microhood": "neighborhood",
}


def _public_unit(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "parent_id": row["parent_id"],
        "admin_level": row["admin_level"],
        "admin_type": row["admin_type"],
        "name": row["name"],
        "name_local": row["name_local"],
        "names": row.get("names", []),
        "iso_code": row["iso_code"],
        "source_feature_id": row["source_feature_id"],
        "source": {
            "slug": row["source_slug"],
            "title": row["source_title"],
            "release": row["release_version"],
            "license": row["license_name"],
            "attribution": row["attribution"],
        },
        "match": "verified_polygon_intersection",
    }


def build_reverse_response(latitude: float, longitude: float, rows: list[dict], diagnostics: dict | None = None) -> dict:
    response: dict[str, Any] = {
        "latitude": latitude,
        "longitude": longitude,
        "status": "success",
        "match_status": "uncovered",
        "hierarchy": [],
        "matched_admin_levels": [],
        "country": None,
        "governorate": None,
        "district": None,
        "city": None,
        "area": None,
        "neighborhood": None,
        "coverage_notes": [],
    }
    if not rows:
        response["coverage_notes"].append(
            "No imported polygon covers this coordinate. It may be water or outside the current import scope."
        )
        if diagnostics is not None:
            response["diagnostics"] = diagnostics
        return response

    deepest = rows[0]
    country = deepest["country_iso3"]
    labels = EGYPT_LABELS if country == "EGY" else GENERIC_LABELS
    candidates = [row for row in rows if row["country_iso3"] == country]
    by_id = {row["id"]: row for row in candidates}
    selected: dict[int, dict] = {deepest["admin_level"]: deepest}
    current = deepest
    # Stored, verified parent links take precedence over independent overlaps.
    while current.get("parent_id") in by_id:
        current = by_id[current["parent_id"]]
        selected[current["admin_level"]] = current
    # A source may omit a parent link; retain an honest polygon match for that
    # level while keeping it visibly independent in the returned unit metadata.
    for row in candidates:
        selected.setdefault(row["admin_level"], row)
    ordered = [selected[level] for level in sorted(selected)]
    public = [_public_unit(row) for row in ordered]
    response["hierarchy"] = public
    response["matched_admin_levels"] = [unit["admin_level"] for unit in public]
    response["match_status"] = "verified_polygon"
    for unit in public:
        label = OVERTURE_LABELS.get(unit["admin_type"]) or labels.get(unit["admin_level"])
        if label in response:
            response[label] = unit

    deepest_type = deepest.get("admin_type")
    if deepest_type in OVERTURE_LABELS:
        if deepest_type != "microhood":
            response["coverage_notes"].append(
                f"The deepest imported Overture boundary here is {deepest_type}; finer polygons may not be published for this point."
            )
    else:
        max_level = max(selected)
        if max_level < 5:
            response["coverage_notes"].append(
                f"The deepest imported boundary here is ADM{max_level}; finer city/area/neighborhood polygons are unavailable."
            )
    response["coverage_notes"].append(
        "A polygon match confirms containment only; administrative labels follow the source dataset's classification."
    )
    if diagnostics is not None:
        response["diagnostics"] = diagnostics
    return response

