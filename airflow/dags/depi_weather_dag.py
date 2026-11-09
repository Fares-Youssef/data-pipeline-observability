"""Apache Airflow DAG for the DEPI weather pipeline (Module 5: Data Pipeline Development with Airflow).

Production on Azure uses Container Apps Jobs; this DAG is the equivalent orchestration for an Airflow
deployment (local docker-compose, Astronomer, or Azure Data Factory Managed Airflow).
Each task calls the same idempotent CLI command, so retries never duplicate data.

    airflow/dags/ -> mount into $AIRFLOW_HOME/dags ; set DEPI_HOME to the project folder.
"""
import os
from datetime import datetime, timedelta

import pendulum
from airflow import DAG
from airflow.operators.bash import BashOperator

DEPI_HOME = os.environ.get("DEPI_HOME", "/opt/depi")
CLI = f"cd {DEPI_HOME} && python weather_cli.py"

default_args = {
    "owner": "depi",
    "retries": 2,
    "retry_delay": timedelta(minutes=10),
    "retry_exponential_backoff": True,
}

with DAG(
    dag_id="depi_weather_daily",
    description="Collect forecasts (GFS, IFS, AIFS) and observations, check quality, build the lake",
    schedule="0 9 * * *",                                  # 09:00 Africa/Cairo (DST handled by the timezone)
    start_date=pendulum.datetime(2026, 11, 1, tz="Africa/Cairo"),
    catchup=False,
    max_active_runs=1,
    default_args=default_args,
    tags=["depi", "weather"],
) as dag:
    validate = BashOperator(task_id="validate_db", bash_command=f"{CLI} validate")
    forecasts = {
        model: BashOperator(task_id=f"collect_{model}", bash_command=f"{CLI} collect-forecasts --model {model}",
                            execution_timeout=timedelta(hours=2))
        for model in ("gfs_global", "ecmwf_ifs", "ecmwf_aifs025_single")
    }
    actuals = BashOperator(task_id="collect_actuals", bash_command=f"{CLI} collect-actuals --lookback-days 14")
    quality = BashOperator(task_id="quality_check", bash_command=f"{CLI} quality-check")
    lake = BashOperator(task_id="export_lake", bash_command=f"{CLI} export-lake --days 30")
    report = BashOperator(task_id="report", bash_command=f"{CLI} report")

    # Models run one after another: the provider rate limit is shared, parallel calls would hit HTTP 429.
    gfs, ifs, aifs = forecasts.values()
    validate >> gfs >> ifs >> aifs >> quality
    validate >> actuals >> quality
    quality >> lake >> report
