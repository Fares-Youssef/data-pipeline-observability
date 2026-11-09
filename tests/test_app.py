from app import create_app
from geo.config import Settings


class FakeRepository:
    def health(self):
        return {"database": "connected", "geographic_records": 2, "coverage": [], "import_jobs": []}

    def reverse(self, latitude, longitude):
        if latitude == 30.0444 and longitude == 31.2357:
            base = {
                "parent_id": None, "admin_type": "administrative", "name_local": None,
                "names": [], "iso_code": None, "source_feature_id": "real-source-id",
                "source_slug": "fixture", "source_title": "Test polygon fixture",
                "release_version": "test", "license_name": "test", "attribution": "test",
                "country_iso3": "EGY", "area_sq_km": None,
            }
            return [
                {**base, "id": 2, "parent_id": 1, "admin_level": 1, "name": "Cairo"},
                {**base, "id": 1, "admin_level": 0, "name": "Egypt"},
            ], {"candidate_count": 2}
        return [], {"candidate_count": 0}

    def geometry(self, unit_id):
        if unit_id != 2:
            return None
        return {
            "type": "Feature", "id": 2,
            "properties": {"id": 2, "name": "Cairo", "name_local": None, "admin_type": "region", "country_iso3": "EGY"},
            "geometry": {"type": "Polygon", "coordinates": [[[31,30],[32,30],[32,31],[31,30]]]},
        }


class FakeWeatherRepository:
    def locations(self, query, parent_id, page, page_size):
        return {"items": [{"id": 2, "name": "Cairo"}], "page": page, "page_size": page_size, "total": 1}

    def location(self, unit_id):
        return {"id": unit_id, "name": "Cairo"} if unit_id == 2 else None

    def models(self):
        return [{"slug": "gfs_global"}]

    def forecast_history(self, location_id, target_date, model):
        return []

    def latest_forecasts(self, *args):
        return []

    def actuals(self, *args):
        return []

    def accuracy(self, *args):
        return []

    def stations(self, limit):
        return []

    def pipeline_status(self):
        return {"geographic_units": 2, "distinct_sampling_points": 1, "mapped_units": 2, "unmapped_units": 0, "forecast_records": 0, "observation_records": 0, "runs": {}}

    def pipeline_runs(self, page, page_size):
        return {"items": [], "page": page, "page_size": page_size, "total": 0}

    def validation_report(self):
        return {"unmapped_units": 0, "expired_pipeline_leases": 0, "open_quality_errors": 0}


def client():
    settings = Settings(db_user="test", db_password="not-used")
    app = create_app(FakeRepository(), settings, FakeWeatherRepository())
    app.config.update(TESTING=True)
    return app.test_client()


def test_health():
    response = client().get("/api/health")
    assert response.status_code == 200
    assert response.json["geographic_records"] == 2


def test_reverse_coordinate_order_and_hierarchy():
    response = client().get("/api/reverse?lat=30.0444&lon=31.2357")
    assert response.status_code == 200
    assert response.json["latitude"] == 30.0444
    assert response.json["longitude"] == 31.2357
    assert response.json["country"]["name"] == "Egypt"
    assert response.json["governorate"]["name"] == "Cairo"
    assert response.json["neighborhood"] is None


def test_uncovered_point_is_not_invented():
    response = client().get("/api/reverse?lat=0&lon=-140")
    assert response.status_code == 200
    assert response.json["match_status"] == "uncovered"
    assert response.json["country"] is None


def test_invalid_coordinates():
    for query in ("lat=91&lon=0", "lat=0&lon=181", "lat=nope&lon=2", "lon=2"):
        response = client().get("/api/reverse?" + query)
        assert response.status_code == 400
        assert response.json["status"] == "error"


def test_frontend_served():
    response = client().get("/")
    assert response.status_code == 200
    assert b"DEPI Weather Intelligence" in response.data
    assert b"chart.js" in response.data.lower()
    assert b"/api/weather/forecast-history" in response.data
    assert b"Waiting for verified actual observations" in response.data


def test_geometry_endpoint():
    response = client().get("/api/geometry/2")
    assert response.status_code == 200
    assert response.json["feature"]["geometry"]["type"] == "Polygon"
    assert client().get("/api/geometry/999").status_code == 404


def test_weather_routes_validate_and_remain_bounded():
    assert client().get("/api/locations?page_size=101").status_code == 400
    assert client().get("/api/weather/forecast-history").status_code == 400
    assert client().get("/api/weather/models").json["items"][0]["slug"] == "gfs_global"
    assert client().get("/api/ready").status_code == 200

