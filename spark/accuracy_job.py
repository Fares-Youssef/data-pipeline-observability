"""PySpark job: recompute Gold accuracy from the Silver layer at scale (Modules 6 & 7.4).

Synapse Spark pool (notebook or Spark job definition):
    LAKE = "abfss://lake@<storage-account>.dfs.core.windows.net"
Local (needs Java 17 + `pip install pyspark`):
    spark-submit spark/accuracy_job.py --lake lake

Outputs:  gold/spark_model_accuracy  and  gold/spark_governorate_ranking  (Parquet, overwrite).
"""
import argparse

from pyspark.sql import SparkSession, Window
from pyspark.sql import functions as F


def main(lake: str) -> None:
    spark = SparkSession.builder.appName("depi-weather-accuracy").getOrCreate()
    forecasts = spark.read.parquet(f"{lake}/silver/fact_forecast").where(F.col("is_valid"))
    observations = spark.read.parquet(f"{lake}/silver/fact_observation") \
        .where(F.col("temporal_alignment").isin("verified", "compatible"))
    errors_path = f"{lake}/gold/forecast_error"
    try:
        errors = spark.read.parquet(errors_path)
    except Exception:  # first days: no eligible observations yet
        print("No Gold forecast_error partitions yet; nothing to aggregate.")
        print("Silver forecasts:", forecasts.count(), "| eligible observations:", observations.count())
        return

    accuracy = (errors.groupBy("model_slug", "lead_days", "governorate")
                .agg(F.count("*").alias("samples"),
                     F.avg("abs_error_max_c").alias("mae_max_c"),
                     F.sqrt(F.avg(F.pow("error_max_c", 2))).alias("rmse_max_c"),
                     F.avg("error_max_c").alias("bias_max_c"),
                     F.avg("abs_error_min_c").alias("mae_min_c"),
                     F.sqrt(F.avg(F.pow("error_min_c", 2))).alias("rmse_min_c"),
                     F.avg("error_min_c").alias("bias_min_c")))
    accuracy.write.mode("overwrite").partitionBy("lead_days").parquet(f"{lake}/gold/spark_model_accuracy")

    rank_window = Window.partitionBy("governorate", "lead_days").orderBy(F.col("mae_max_c").asc())
    ranking = accuracy.withColumn("rank", F.rank().over(rank_window)).where(F.col("rank") == 1)
    ranking.write.mode("overwrite").parquet(f"{lake}/gold/spark_governorate_ranking")
    ranking.orderBy("governorate", "lead_days").show(50, truncate=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--lake", default="lake")
    main(parser.parse_args().lake)
