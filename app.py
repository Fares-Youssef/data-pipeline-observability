import sys
from pathlib import Path


_LOCAL_DEPENDENCIES = Path(__file__).resolve().parent / ".deps"
if _LOCAL_DEPENDENCIES.is_dir():
    sys.path.insert(0, str(_LOCAL_DEPENDENCIES))

from flask import Flask, jsonify, request, send_from_directory

from geo.config import Settings
from geo.repository import GeographyRepository
from geo.service import build_reverse_response
from weather.repository import WeatherRepository


def _coordinate(raw: str | None, name: str, low: float, high: float) -> float:
    if raw is None or raw.strip() == "":
        raise ValueError(f"Missing required parameter: {name}")
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not low <= value <= high:
        raise ValueError(f"{name} must be between {low:g} and {high:g}")
    return value


def create_app(repository=None, settings: Settings | None = None, weather_repository=None) -> Flask:
    app = Flask(__name__, static_folder=None)
    settings = settings or Settings()
    repository = repository or GeographyRepository(settings)
    weather_repository = weather_repository or WeatherRepository(settings)

    def positive_int(name: str, default: int, maximum: int) -> int:
        raw = request.args.get(name)
        if raw is None:
            return default
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValueError(f"{name} must be an integer") from exc
        if not 1 <= value <= maximum:
            raise ValueError(f"{name} must be between 1 and {maximum}")
        return value

    def required_int(name: str, maximum: int) -> int:
        raw = request.args.get(name)
        if raw is None or raw == "":
            raise ValueError(f"{name} is required")
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValueError(f"{name} must be an integer") from exc
        if not 1 <= value <= maximum:
            raise ValueError(f"{name} must be between 1 and {maximum}")
        return value

    def iso_date(name: str):
        raw = request.args.get(name)
        if not raw:
            return None
        from datetime import date
        try:
            return date.fromisoformat(raw)
        except ValueError as exc:
            raise ValueError(f"{name} must use YYYY-MM-DD") from exc

    def weather_error(action):
        try:
            return action()
        except ValueError as exc:
            return jsonify({"status": "error", "error": str(exc)}), 400
        except Exception:
            app.logger.exception("Weather API request failed")
            return jsonify({"status": "error", "error": "Weather data is unavailable"}), 503

    @app.get("/")
    def index():
        return send_from_directory(app.root_path, "index.html")

    @app.get("/api/health")
    def health():
        missing = settings.missing_database_settings()
        if missing:
            return jsonify({"status": "unavailable", "error": "Missing database configuration", "required": missing}), 503
        try:
            return jsonify({"status": "ok", **repository.health()})
        except Exception:
            app.logger.exception("Database health check failed")
            return jsonify({"status": "unavailable", "error": "Database connection or geography schema is unavailable"}), 503

    @app.get("/api/reverse")
    def reverse():
        try:
            latitude = _coordinate(request.args.get("lat"), "lat", -90, 90)
            longitude = _coordinate(request.args.get("lon"), "lon", -180, 180)
        except ValueError as exc:
            return jsonify({"status": "error", "error": str(exc)}), 400
        try:
            rows, diagnostics = repository.reverse(latitude, longitude)
            details = diagnostics if settings.diagnostics else None
            return jsonify(build_reverse_response(latitude, longitude, rows, details))
        except Exception:
            app.logger.exception("Reverse lookup failed")
            return jsonify({"status": "error", "error": "Geographic lookup failed; database details were not exposed"}), 503

    @app.get("/api/geometry/<int:unit_id>")
    def geometry(unit_id: int):
        try:
            feature = repository.geometry(unit_id)
            if feature is None:
                return jsonify({"status": "error", "error": "Geographic unit not found"}), 404
            return jsonify({"status": "success", "feature": feature})
        except Exception:
            app.logger.exception("Geometry lookup failed")
            return jsonify({"status": "error", "error": "Boundary geometry is unavailable"}), 503

    @app.get("/api/locations")
    def locations():
        def action():
            page = positive_int("page", 1, 100000)
            page_size = positive_int("page_size", 25, 100)
            parent = request.args.get("parent_id")
            parent_id = int(parent) if parent else None
            return jsonify(weather_repository.locations(request.args.get("q"), parent_id, page, page_size))
        return weather_error(action)

    @app.get("/api/locations/<int:unit_id>")
    def location(unit_id: int):
        def action():
            item = weather_repository.location(unit_id)
            return jsonify(item) if item else (jsonify({"status": "error", "error": "Location not found"}), 404)
        return weather_error(action)

    def forecast_action(history: bool = False):
        location_id = required_int("location_id", 2_000_000_000)
        target = iso_date("target_date")
        model = request.args.get("model")
        if history:
            if target is None:
                raise ValueError("target_date is required")
            return jsonify({"items": weather_repository.forecast_history(location_id, target, model)})
        limit = positive_int("limit", 200, 500)
        return jsonify({"items": weather_repository.latest_forecasts(location_id, iso_date("start_date"), iso_date("end_date"), model, limit)})

    @app.get("/api/weather/latest")
    @app.get("/api/weather/forecasts")
    def forecasts():
        return weather_error(lambda: forecast_action(False))

    @app.get("/api/weather/forecast-history")
    @app.get("/api/weather/compare")
    def forecast_history():
        return weather_error(lambda: forecast_action(True))

    @app.get("/api/weather/actuals")
    def actuals():
        return weather_error(lambda: jsonify({"items": weather_repository.actuals(
            required_int("location_id", 2_000_000_000), iso_date("start_date"), iso_date("end_date"), positive_int("limit", 200, 500)
        )}))

    @app.get("/api/weather/accuracy")
    def accuracy():
        def action():
            target = iso_date("target_date")
            return jsonify({"items": weather_repository.accuracy(
                required_int("location_id", 2_000_000_000), target or iso_date("start_date"), target or iso_date("end_date"),
                request.args.get("model"), positive_int("minimum_samples", 1, 10000)
            )})
        return weather_error(action)

    @app.get("/api/weather/models")
    def models():
        return weather_error(lambda: jsonify({"items": weather_repository.models()}))

    @app.get("/api/weather/stations")
    def stations():
        return weather_error(lambda: jsonify({"items": weather_repository.stations(positive_int("limit", 100, 500))}))

    @app.get("/api/pipeline/status")
    def pipeline_status():
        return weather_error(lambda: jsonify(weather_repository.pipeline_status()))

    @app.get("/api/ready")
    def ready():
        def action():
            report = weather_repository.validation_report()
            healthy = report["unmapped_units"] == 0 and report["expired_pipeline_leases"] == 0 and report["open_quality_errors"] == 0
            return jsonify({"status": "ready" if healthy else "not_ready", **report}), 200 if healthy else 503
        return weather_error(action)

    @app.get("/api/pipeline/runs")
    def pipeline_runs():
        return weather_error(lambda: jsonify(weather_repository.pipeline_runs(positive_int("page", 1, 100000), positive_int("page_size", 25, 100))))

    return app


app = create_app()


if __name__ == "__main__":
    config = Settings()
    app.run(host=config.flask_host, port=config.flask_port, debug=config.flask_debug)

