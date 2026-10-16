# Databricks notebook source
# Bronze ingestion: load raw shipment CSV as-is into Delta Lake

from pyspark.sql.functions import current_timestamp, input_file_name

# Default storage location in Unity Catalog volume or DBFS
dbutils.widgets.text("raw_data_path", "/Volumes/supply_chain/bronze/raw_data/SCMS_Delivery_History_Dataset.csv")
raw_data_path = dbutils.widgets.get("raw_data_path")


def ensure_bronze_schema():
    # Unity Catalog requires catalog, schema, and volume to exist
    spark.sql("CREATE CATALOG IF NOT EXISTS supply_chain")
    spark.sql("CREATE SCHEMA IF NOT EXISTS supply_chain.bronze")
    spark.sql("CREATE VOLUME IF NOT EXISTS supply_chain.bronze.raw_data")


def ingest_raw_csv(source_path):
    # InferSchema is disabled so raw text anomalies remain intact for silver
    raw_df = (
        spark.read.format("csv")
        .option("header", "true")
        .option("inferSchema", "false")
        .load(source_path)
    )

    # Ingestion metadata provides traceability back to source files
    bronze_df = (
        raw_df.withColumn("ingested_at", current_timestamp())
        .withColumn("source_file", input_file_name())
    )

    (
        bronze_df.write.format("delta")
        .mode("overwrite")
        .saveAsTable("supply_chain.bronze.shipments_raw")
    )
    return bronze_df.count()


ensure_bronze_schema()
raw_record_count = ingest_raw_csv(raw_data_path)
print(f"Bronze ingestion complete: {raw_record_count} records written to supply_chain.bronze.shipments_raw")
