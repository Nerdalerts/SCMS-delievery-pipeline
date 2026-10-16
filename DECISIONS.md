# Design decisions

A running log of engineering and design decisions for the supply chain lakehouse pipeline. Each entry captures the problem, what was chosen, and what was rejected.

## Decision 1: Databricks source files instead of .ipynb

Problem: Standard Jupyter notebooks (`.ipynb`) store execution counts, cell outputs, and metadata that cause messy git diffs and risk leaking workspace paths or sensitive figures.
Choice: Stored notebooks as plain `.py` files using the `# Databricks notebook source` header.
Rejected: Using `.ipynb` format or using export scripts, because plain `.py` files diff cleanly in git and guarantee zero output leakage.

## Decision 2: Static repository hygiene check

Problem: Accidental commits of personal email addresses, Databricks personal access tokens (`dapi...`), or workspace URLs violate privacy rules and pose security risks.
Choice: Created a lightweight Python script (`scripts/check_repo.py`) that scans the repo for secret patterns and files larger than 5 MB before every commit.
Rejected: Heavy pre-commit frameworks or third-party secret scanners that require additional dependencies and complex local setup.

## Decision 3: Keeping raw CSV out of version control

Problem: Storing dataset files directly in git bloats repository history and conflicts with data privacy best practices.
Choice: Placed data in a local `data/` directory ignored by git with a `.gitkeep`, providing download instructions and open license attribution in the README.
Rejected: Tracking sample data chunks in git or committing zipped archives.

## Decision 4: Preserving rows with non-numeric freight and weight

Problem: Roughly 38% of weight values and 40% of freight costs in the raw data contain text annotations such as "Freight Included in Commodity Cost" or "See ASN-xxxx".
Choice: Converted unparseable text to SQL nulls while keeping the shipment records intact for delivery delay analysis.
Rejected: Dropping all rows with non-numeric freight or imputing mean costs, because missing freight cost reflects commercial contracting terms (like EXW or DDP) rather than bad data.

## Decision 5: Dead-letter isolation for date chronology failures

Problem: Five shipment records have delivered dates that occurred before the purchase order was sent to the vendor.
Choice: Routed those records to `supplychain.silver.rejected_rows` with a clear failure reason, allowing the clean silver table to pass strict chronological integrity.
Rejected: Silently dropping invalid records or overwriting dates, because auditing rejected records is necessary for supplier contract enforcement.

## Decision 6: Stratified split over time-based split for ML

Problem: Shipment dates span a decade with uneven delivery frequency across vendors, and some vendor-destination combinations only appear in specific years.
Choice: Used a stratified train/test split on the target label (`late_delivery_flag`) to preserve the 11.5% late delivery class balance across both sets.
Rejected: A strict chronological split, which would leave the test set with zero training examples for vendors that only operated in earlier years.

## Decision 7: Strict feature leakage boundary

Problem: Predicting delivery delay could easily be contaminated by post-shipment operational fields like actual delivery date, recorded date, or actual delay days.
Choice: Restricted features strictly to pre-dispatch attributes (vendor, country, shipment mode, product group, quantity, line value, weight, and planned lead time).
Rejected: Using intermediate tracking timestamps or customs clearance dates that are only available after a shipment is already en route.

## Decision 8: Native Databricks Workflows orchestration

Problem: The pipeline requires running four notebook tasks sequentially with dependable error propagation.
Choice: Orchestrated tasks using Databricks Workflows, which runs natively against Unity Catalog Delta tables without extra cloud services.
Rejected: Adding external orchestration tools like Azure Data Factory or AWS Glue, which would add networking complexity and costs for a self-contained lakehouse.

