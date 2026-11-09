"""One-command global Overture geography setup at every published subtype."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCAL_DEPENDENCIES = ROOT / ".deps"
if LOCAL_DEPENDENCIES.is_dir():
    sys.path.insert(0, str(LOCAL_DEPENDENCIES))


def run(command: list[str], *, capture: bool = False) -> subprocess.CompletedProcess:
    print("\n>", " ".join(command), flush=True)
    return subprocess.run(
        command,
        cwd=ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare the schema and import worldwide Overture division polygons."
    )
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument(
        "--skip-migration",
        action="store_true",
        help="Skip the safe idempotent migration when it has already been applied",
    )
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error("--batch-size must be at least 1")

    python = sys.executable
    try:
        if not args.skip_migration:
            run([python, "scripts/migrate.py"])
        run([python, "scripts/import_geography.py", "--all", "--batch-size", str(args.batch_size)])
        report = run([python, "scripts/coverage_report.py"], capture=True)
    except subprocess.CalledProcessError as exc:
        print(
            f"\nWorld import stopped because a step exited with code {exc.returncode}. "
            "Fix the reported problem and run this same command again; imports are idempotent.",
            file=sys.stderr,
        )
        return exc.returncode or 1

    report_path = ROOT / "coverage.json"
    report_path.write_text(report.stdout, encoding="utf-8")
    print(report.stdout)
    print(f"Coverage report written to {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

