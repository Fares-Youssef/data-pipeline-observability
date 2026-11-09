from geo.service import build_reverse_response


def unit(identifier, parent, level, name, country="EGY"):
    return {
        "id": identifier, "parent_id": parent, "country_iso3": country,
        "admin_level": level, "admin_type": f"ADM{level}", "name": name,
        "name_local": None, "names": [], "iso_code": None, "area_sq_km": None,
        "source_feature_id": f"source-{identifier}", "source_slug": "fixture",
        "source_title": "Fixture", "release_version": "1", "license_name": "test",
        "attribution": "test",
    }


def test_verified_parent_chain_wins_over_same_level_overlap():
    rows = [
        unit(30, 20, 2, "Child"),
        unit(21, 10, 1, "Overlapping but unrelated"),
        unit(20, 10, 1, "Verified parent"),
        unit(10, None, 0, "Egypt"),
    ]
    response = build_reverse_response(30, 31, rows)
    assert [item["name"] for item in response["hierarchy"]] == ["Egypt", "Verified parent", "Child"]


def test_international_overlap_does_not_mix_countries():
    rows = [unit(2, 1, 1, "Egypt region"), unit(9, None, 0, "Other", "OTHER"), unit(1, None, 0, "Egypt")]
    response = build_reverse_response(1, 1, rows)
    assert [item["name"] for item in response["hierarchy"]] == ["Egypt", "Egypt region"]
