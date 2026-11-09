import os
import sys
from dataclasses import dataclass
from pathlib import Path


_LOCAL_DEPENDENCIES = Path(__file__).resolve().parents[1] / ".deps"
if _LOCAL_DEPENDENCIES.is_dir():
    sys.path.insert(0, str(_LOCAL_DEPENDENCIES))

from dotenv import load_dotenv


load_dotenv()


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    db_host: str = os.getenv("DB_HOST", "127.0.0.1")
    db_port: int = int(os.getenv("DB_PORT", "3306"))
    db_user: str = os.getenv("DB_USER", "")
    db_password: str = os.getenv("DB_PASSWORD", "")
    db_name: str = os.getenv("DB_NAME", "depi")
    db_connect_timeout: int = int(os.getenv("DB_CONNECT_TIMEOUT", "5"))
    flask_host: str = os.getenv("FLASK_HOST", "127.0.0.1")
    flask_port: int = int(os.getenv("FLASK_PORT", "5000"))
    flask_debug: bool = _bool("FLASK_DEBUG")
    diagnostics: bool = _bool("REVERSE_DIAGNOSTICS")
    open_meteo_api_key: str = os.getenv("OPEN_METEO_API_KEY", "")
    weather_request_timeout: int = int(os.getenv("WEATHER_REQUEST_TIMEOUT", "30"))
    weather_batch_size: int = int(os.getenv("WEATHER_BATCH_SIZE", "25"))
    weather_request_interval: float = float(os.getenv("WEATHER_REQUEST_INTERVAL", "3"))
    weather_rate_limit_backoff: int = int(os.getenv("WEATHER_RATE_LIMIT_BACKOFF", "30"))
    weather_max_locations: int = int(os.getenv("WEATHER_MAX_LOCATIONS", "0"))
    weather_alignment_distance_km: float = float(os.getenv("WEATHER_ALIGNMENT_DISTANCE_KM", "50"))
    lake_root: str = os.getenv("LAKE_ROOT", "lake")
    azure_lake_container: str = os.getenv("AZURE_LAKE_CONTAINER", "lake")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    azure_storage_connection_string: str = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "")
    azure_storage_container: str = os.getenv("AZURE_STORAGE_CONTAINER", "weather-raw")
    db_ssl_ca: str = os.getenv("DB_SSL_CA", "")
    db_ssl_verify_cert: bool = _bool("DB_SSL_VERIFY_CERT")

    def missing_database_settings(self) -> list[str]:
        return [key for key, value in {"DB_USER": self.db_user}.items() if not value]

