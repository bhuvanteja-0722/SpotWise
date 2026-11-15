"""Distributed SpotWise cleanup and aggregation job for Spark 3.5."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone

from pyspark.sql import SparkSession, functions as F

OPTIONAL_COLUMNS = {
    "area": "Citywide", "category": None, "metric": "value", "unit": "count",
    "name": None, "detail": None, "observed_at": None, "latitude": None, "longitude": None,
}
CITY_ALIASES = {
    "bengaluru": "Bengaluru", "bangalore": "Bengaluru",
    "mumbai": "Mumbai", "bombay": "Mumbai",
    "hyderabad": "Hyderabad", "secunderabad": "Hyderabad",
    "pune": "Pune", "chennai": "Chennai", "madras": "Chennai",
    "delhi ncr": "Delhi NCR", "delhi": "Delhi NCR", "new delhi": "Delhi NCR",
    "gurugram": "Delhi NCR", "gurgaon": "Delhi NCR", "noida": "Delhi NCR",
    "kolkata": "Kolkata", "calcutta": "Kolkata", "ahmedabad": "Ahmedabad",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="HDFS path containing standardized CSV or Parquet files")
    parser.add_argument("--format", choices=("csv", "parquet"), required=True)
    parser.add_argument("--category", required=True, help="Signal category if the file has no category column")
    parser.add_argument("--metric", default="value", help="Metric label if the file has no metric column")
    parser.add_argument("--output", required=True, help="HDFS output directory for cleaned Parquet observations")
    parser.add_argument("--keyspace", default="spotwise")
    parser.add_argument("--table", default="area_signal_summary")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    spark = SparkSession.builder.appName("SpotWise-Distributed-ETL").getOrCreate()
    spark.sparkContext.setLogLevel("WARN")
    try:
        reader = spark.read.option("header", True).option("mode", "PERMISSIVE")
        source = reader.csv(args.input) if args.format == "csv" else spark.read.parquet(args.input)
        columns = {name.casefold(): name for name in source.columns}
        if "city" not in columns or "value" not in columns:
            raise ValueError("Input must include required columns: city and value")

        selected = [F.col(columns["city"]).cast("string").alias("city")]
        for name, default in OPTIONAL_COLUMNS.items():
            existing = columns.get(name)
            if existing:
                selected.append(F.col(existing).cast("string").alias(name))
            elif name == "category":
                selected.append(F.lit(args.category).alias(name))
            elif name == "metric":
                selected.append(F.lit(args.metric).alias(name))
            else:
                selected.append(F.lit(default).cast("string").alias(name))
        selected.append(F.col(columns["value"]).cast("string").alias("value"))
        input_rows = source.count()
        cleaned = source.select(*selected)
        city_key = F.regexp_replace(F.lower(F.trim("city")), r"\s+", " ")
        city_map = F.create_map(*[
            item
            for alias, canonical in CITY_ALIASES.items()
            for item in (F.lit(alias), F.lit(canonical))
        ])
        cleaned = (
            cleaned
            .withColumn("_city_key", city_key)
            .withColumn("city", F.coalesce(city_map[F.col("_city_key")], F.initcap(F.col("_city_key"))))
            .withColumn("area", F.when(F.col("area").isNull() | (F.trim("area") == ""), "Citywide").otherwise(F.regexp_replace(F.trim("area"), r"\s+", " ")))
            .withColumn("category", F.lower(F.trim("category")))
            .withColumn("metric", F.lower(F.trim("metric")))
            .withColumn("unit", F.when(F.col("unit").isNull() | (F.trim("unit") == ""), "count").otherwise(F.lower(F.trim("unit"))))
            .withColumn("value", F.expr("try_cast(value AS DOUBLE)"))
            .withColumn("latitude", F.expr("try_cast(latitude AS DOUBLE)"))
            .withColumn("longitude", F.expr("try_cast(longitude AS DOUBLE)"))
            .withColumn("observed_at", F.expr("try_to_timestamp(observed_at)"))
            .withColumn(
                "coordinates_valid",
                F.col("latitude").between(-90, 90) & F.col("longitude").between(-180, 180),
            )
            .withColumn("latitude", F.when(F.col("coordinates_valid"), F.col("latitude")))
            .withColumn("longitude", F.when(F.col("coordinates_valid"), F.col("longitude")))
            .drop("coordinates_valid", "_city_key")
        )

        valid = cleaned.filter(
            F.col("city").isNotNull() & (F.col("city") != "")
            & F.col("category").isNotNull() & (F.col("category") != "")
            & F.col("metric").isNotNull() & (F.col("metric") != "")
            & F.col("value").isNotNull() & ~F.isnan("value")
            & (F.abs(F.col("value")) != F.lit(float("inf")))
        )
        valid_rows = valid.count()
        cleaned = valid.dropDuplicates()
        row_count = cleaned.count()
        if row_count == 0:
            raise ValueError("No valid rows remain after city/value validation and exact de-duplication")

        cleaned.write.mode("overwrite").partitionBy("city", "category").parquet(args.output)
        summary = cleaned.groupBy("city", "category", "metric", "area", "unit").agg(
            F.count(F.lit(1)).cast("long").alias("record_count"),
            F.avg("value").cast("double").alias("average_value"),
            F.expr("percentile_approx(value, 0.5, 10000)").cast("double").alias("median_value"),
            F.sum("value").cast("double").alias("total_value"),
            F.min("value").cast("double").alias("minimum_value"),
            F.max("value").cast("double").alias("maximum_value"),
        ).withColumn("refreshed_at", F.current_timestamp())
        (summary.write.format("org.apache.spark.sql.cassandra")
         .options(keyspace=args.keyspace, table=args.table)
         .mode("append")
         .save())
        print(
            f"SpotWise Spark ETL completed at {datetime.now(timezone.utc).isoformat()}: "
            f"input={input_rows:,}, invalid={input_rows - valid_rows:,}, "
            f"exact_duplicates={valid_rows - row_count:,}, output={row_count:,} rows"
        )
        print(f"Curated Parquet: {args.output}; Cassandra: {args.keyspace}.{args.table}")
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
