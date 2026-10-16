# Gold business aggregations built with Spark SQL

spark.sql("CREATE SCHEMA IF NOT EXISTS supplychain.gold")

# Gold 1: Late delivery rate and average delay by vendor
spark.sql("""
CREATE OR REPLACE TABLE supplychain.gold.late_rates_by_vendor AS
SELECT
    vendor,
    COUNT(id) AS shipment_count,
    SUM(late_delivery_flag) AS late_shipment_count,
    ROUND(AVG(late_delivery_flag), 4) AS late_delivery_rate,
    ROUND(AVG(delay_days), 2) AS avg_delay_days
FROM supplychain.silver.shipments
GROUP BY vendor
ORDER BY shipment_count DESC
""")
spark.sql("COMMENT ON TABLE supplychain.gold.late_rates_by_vendor IS 'Vendor level shipment volume, late delivery rates, and average delay days'")

# Gold 2: Late delivery rate and average delay by shipment mode
spark.sql("""
CREATE OR REPLACE TABLE supplychain.gold.late_rates_by_mode AS
SELECT
    shipment_mode,
    COUNT(id) AS shipment_count,
    SUM(late_delivery_flag) AS late_shipment_count,
    ROUND(AVG(late_delivery_flag), 4) AS late_delivery_rate,
    ROUND(AVG(delay_days), 2) AS avg_delay_days
FROM supplychain.silver.shipments
GROUP BY shipment_mode
ORDER BY shipment_count DESC
""")
spark.sql("COMMENT ON TABLE supplychain.gold.late_rates_by_mode IS 'Shipment mode delivery reliability and delay metrics'")

# Gold 3: Late delivery rate and average delay by destination country
spark.sql("""
CREATE OR REPLACE TABLE supplychain.gold.late_rates_by_country AS
SELECT
    country,
    COUNT(id) AS shipment_count,
    SUM(late_delivery_flag) AS late_shipment_count,
    ROUND(AVG(late_delivery_flag), 4) AS late_delivery_rate,
    ROUND(AVG(delay_days), 2) AS avg_delay_days
FROM supplychain.silver.shipments
GROUP BY country
ORDER BY shipment_count DESC
""")
spark.sql("COMMENT ON TABLE supplychain.gold.late_rates_by_country IS 'Destination country shipment volume and late delivery proportions'")

# Gold 4: Freight cost as a share of line value by product group and mode
spark.sql("""
CREATE OR REPLACE TABLE supplychain.gold.freight_value_summary AS
SELECT
    product_group,
    shipment_mode,
    COUNT(id) AS shipment_count,
    ROUND(SUM(line_item_value), 2) AS total_line_value,
    ROUND(SUM(freight_cost_usd), 2) AS total_freight_cost,
    ROUND(SUM(freight_cost_usd) / NULLIF(SUM(line_item_value), 0), 4) AS freight_share_of_value
FROM supplychain.silver.shipments
WHERE freight_cost_usd IS NOT NULL AND line_item_value IS NOT NULL
GROUP BY product_group, shipment_mode
ORDER BY shipment_count DESC
""")
spark.sql("COMMENT ON TABLE supplychain.gold.freight_value_summary IS 'Freight spend relative to commodity value by commodity type and mode'")

# Lineage navigation note for interview defense
print("Gold layer complete. 4 tables created.")
print("To inspect lineage in Databricks: Open Catalog Explorer -> supplychain -> gold -> select table -> click Lineage tab.")