from geo.service import build_reverse_response
from scripts.import_geography import alpha3, common_names


def overture_unit(identifier, parent, level, subtype, name, local=None):
    return {
        "id": identifier, "parent_id": parent, "country_iso3": "EGY",
        "admin_level": level, "admin_type": subtype, "name": name,
        "name_local": local, "names": [], "iso_code": "EG-GZ", "area_sq_km": None,
        "source_feature_id": f"overture-{identifier}", "source_slug": "overture_divisions",
        "source_title": "Overture Maps Divisions", "release_version": "test",
        "license_name": "ODbL", "attribution": "Overture / OSM",
    }


def test_overture_names_and_country_codes():
    assert alpha3("EG") == "EGY"
    assert common_names({"common": [["en", "Ard Al Lwaa'"], ["ar", "ارض اللواء"]]}) == {
        "en": "Ard Al Lwaa'", "ar": "ارض اللواء"
    }


def test_overture_neighborhood_maps_to_response_slot():
    rows = [
        overture_unit(4, 3, 10, "neighborhood", "Ard Al Lwaa'", "ارض اللواء"),
        overture_unit(3, 2, 5, "county", "Kardasa"),
        overture_unit(2, 1, 3, "region", "Aj Jiza", "الجيزة"),
        overture_unit(1, None, 0, "country", "Egypt", "مصر"),
    ]
    response = build_reverse_response(30.056, 31.183, rows)
    assert response["country"]["name"] == "Egypt"
    assert response["governorate"]["name"] == "Aj Jiza"
    assert response["district"]["name"] == "Kardasa"
    assert response["neighborhood"]["name_local"] == "ارض اللواء"

