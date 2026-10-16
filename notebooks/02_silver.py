# Silver transformation and data quality gate

from pyspark.sql.functions import col, datediff, expr, lit, to_date, trim, try_to_date, upper, when

spark.sql("CREATE SCHEMA IF NOT EXISTS supplychain.silver")

# PII column assertion per section 2.2
pii_terms = ["email", "phone", "first_name", "last_name", "address", "ssn", "password"]
raw_columns = spark.table("supplychain.bronze.shipments_raw").columns
assert not any(p in c.lower() for p in pii_terms for c in raw_columns), "PII detected in column names"


def clean_column_name(name):
    # Strip punctuation and replace spaces to create clean snake_case names
    cleaned = name.strip().replace("#", "").replace("/", "").replace("(", "").replace(")", "")
    return "_".join(cleaned.lower().split())


def load_and_standardize():
    raw_df = spark.table("supplychain.bronze.shipments_raw").drop("ingested_at", "source_file")
    df = raw_df
    for c in raw_df.columns:
        df = df.withColumnRenamed(c, clean_column_name(c))

    # Casting to double converts non-numeric text like "See ASN-xxxx" to null cleanly
    standardized_df = (
        df.withColumn("id", col("id").cast("long"))
        .withColumn("line_item_quantity", expr("try_cast(line_item_quantity as double)"))
        .withColumn("line_item_value", expr("try_cast(line_item_value as double)"))
        .withColumn("weight_kilograms", expr("try_cast(weight_kilograms as double)"))
        .withColumn("freight_cost_usd", expr("try_cast(freight_cost_usd as double)"))
        .withColumn("scheduled_delivery_date", try_to_date(col("scheduled_delivery_date"), "d-MMM-yy"))
        .withColumn("delivered_to_client_date", try_to_date(col("delivered_to_client_date"), "d-MMM-yy"))
        .withColumn("po_sent_to_vendor_date", try_to_date(col("po_sent_to_vendor_date"), "M/d/yy"))
        .withColumn("vendor", trim(upper(col("vendor"))))
        .withColumn("country", when(col("country").like("%te d'Ivoire%"), lit("Cote d'Ivoire")).otherwise(trim(col("country"))))
        .withColumn("shipment_mode", when(col("shipment_mode").isNull(), lit("Unknown")).otherwise(trim(col("shipment_mode"))))
        .withColumn("late_delivery_flag", when(col("delivered_to_client_date") > col("scheduled_delivery_date"), 1).otherwise(0))
        .withColumn("delay_days", datediff(col("delivered_to_client_date"), col("scheduled_delivery_date")))
        .dropDuplicates()
    )
    return standardized_df


def apply_quality_checks(df):
    total_records = df.count()
    null_weight_count = df.filter(col("weight_kilograms").isNull()).count()
    null_freight_count = df.filter(col("freight_cost_usd").isNull()).count()

    # Freight nulls represent contract terms rather than invalid rows, so report instead of dropping
    print(f"Check 5 - Null weight share: {null_weight_count / total_records * 100:.2f}%")
    print(f"Check 5 - Null freight share: {null_freight_count / total_records * 100:.2f}%")

    cond_id_null = col("id").isNull()
    cond_date_invalid = col("po_sent_to_vendor_date").isNotNull() & (col("delivered_to_client_date") < col("po_sent_to_vendor_date"))
    cond_neg_qty_val = (col("line_item_quantity") < 0) | (col("line_item_value") < 0)

    rejected_df = df.filter(cond_id_null | cond_date_invalid | cond_neg_qty_val).withColumn(
        "reason",
        when(cond_id_null, lit("Null primary key ID"))
        .when(cond_date_invalid, lit("Delivered date before PO sent date"))
        .otherwise(lit("Negative quantity or line value"))
    )

    clean_df = df.filter(~(cond_id_null | cond_date_invalid | cond_neg_qty_val))

    print(f"Check 1 - Null ID failures: {df.filter(cond_id_null).count()}")
    print(f"Check 3 - Delivered before PO sent failures: {df.filter(cond_date_invalid).count()}")
    print(f"Check 4 - Negative quantity or value failures: {df.filter(cond_neg_qty_val).count()}")
    return clean_df, rejected_df


silver_df, rejected_df = apply_quality_checks(load_and_standardize())
silver_df.write.format("delta").mode("overwrite").saveAsTable("supplychain.silver.shipments")
rejected_df.write.format("delta").mode("overwrite").saveAsTable("supplychain.silver.rejected_rows")

spark.sql("COMMENT ON TABLE supplychain.silver.shipments IS 'Cleaned shipment lines with typed dates, standardized text, and late flags'")
spark.sql("COMMENT ON TABLE supplychain.silver.rejected_rows IS 'Shipment records failing business date chronology or value checks'")
print(f"Silver complete: {silver_df.count()} clean rows, {rejected_df.count()} rejected rows")