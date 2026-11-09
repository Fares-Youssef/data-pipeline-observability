from __future__ import annotations

import argparse
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


def get_json(base_url: str, path: str) -> dict:
    with urlopen(base_url.rstrip("/") + path, timeout=20) as response:
        if response.status != 200:
            raise RuntimeError(f"{path} returned HTTP {response.status}")
        return json.load(response)


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify a deployed DEPI Weather API")
    parser.add_argument("base_url", help="For example: https://depi-weather.example.com")
    args = parser.parse_args()
    checks = {}
    try:
        checks["health"] = get_json(args.base_url, "/api/health")
        checks["ready"] = get_json(args.base_url, "/api/ready")
        checks["models"] = get_json(args.base_url, "/api/weather/models")
        checks["pipeline"] = get_json(args.base_url, "/api/pipeline/status")
    except (HTTPError, URLError, RuntimeError, ValueError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc), "checks": checks}, default=str))
        return 1
    failures = []
    if checks["health"].get("geographic_records") != 8994:
        failures.append("geographic record count is not 8,994")
    if checks["ready"].get("unmapped_units") != 0:
        failures.append("some geographic units are unmapped")
    if len(checks["models"].get("items", [])) != 3:
        failures.append("three explicit weather models are not active")
    print(json.dumps({"status": "passed" if not failures else "failed", "failures": failures, "checks": checks}, default=str))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
