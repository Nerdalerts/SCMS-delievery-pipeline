"""Local runner for Supply Chain Lakehouse Pipeline and ML Delay Prediction.

Executes Bronze ingestion, Silver standardization and data quality gates,
Gold aggregations exported to dashboard/exports/, and trains ML models.
"""

import os
import sys
import pandas as pd
import numpy as np
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score, precision_score, recall_score, roc_auc_score, classification_report, confusion_matrix
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_PATH = os.path.join(ROOT_DIR, "data", "SCMS_Delivery_History_Dataset.csv")
EXPORTS_DIR = os.path.join(ROOT_DIR, "dashboard", "exports")
os.makedirs(EXPORTS_DIR, exist_ok=True)


def clean_column_name(name: str) -> str:
    cleaned = name.strip().replace("#", "").replace("/", "").replace("(", "").replace(")", "")
    return "_".join(cleaned.lower().split())


def run_pipeline():
    print("=" * 70)
    print("STEP 1: BRONZE INGESTION")
    print("=" * 70)
    if not os.path.exists(DATA_PATH):
        raise FileNotFoundError(f"Dataset not found at {DATA_PATH}")

    raw_df = pd.read_csv(DATA_PATH, dtype=str)
    print(f"Loaded raw dataset: {len(raw_df):,} records, {len(raw_df.columns)} columns.")

    # PII privacy check
    pii_terms = ["email", "phone", "first_name", "last_name", "address", "ssn", "password"]
    raw_columns = list(raw_df.columns)
    assert not any(p in c.lower() for p in pii_terms for c in raw_columns), "PII detected in column names!"
    print("Privacy check passed: No PII detected in column schema.")

    print("\n" + "=" * 70)
    print("STEP 2: SILVER STANDARDIZATION & QUALITY GATE")
    print("=" * 70)

    # Standardize column names
    col_mapping = {c: clean_column_name(c) for c in raw_df.columns}
    df = raw_df.rename(columns=col_mapping)

    # Cast numeric columns
    numeric_cols = ["id", "line_item_quantity", "line_item_value", "weight_kilograms", "freight_cost_usd"]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Parse dates
    date_cols = {
        "scheduled_delivery_date": "%d-%b-%y",
        "delivered_to_client_date": "%d-%b-%y",
        "po_sent_to_vendor_date": None  # Mixed/flexible format
    }
    for col, fmt in date_cols.items():
        if col in df.columns:
            if fmt:
                df[col] = pd.to_datetime(df[col], format=fmt, errors="coerce")
            else:
                df[col] = pd.to_datetime(df[col], errors="coerce")

    # Standardize categoricals
    df["vendor"] = df["vendor"].astype(str).str.strip().str.upper()
    df["country"] = df["country"].astype(str).str.strip()
    df.loc[df["country"].str.contains(r"te d'Ivoire", case=False, na=False), "country"] = "Cote d'Ivoire"
    df["shipment_mode"] = df["shipment_mode"].fillna("Unknown").astype(str).str.strip()
    df.loc[df["shipment_mode"] == "nan", "shipment_mode"] = "Unknown"

    # Deduplicate
    initial_count = len(df)
    df = df.drop_duplicates()
    dedup_removed = initial_count - len(df)
    print(f"Check 2 - Deduplication check: {dedup_removed} duplicate rows removed.")

    # Calculate late delivery metrics
    df["late_delivery_flag"] = (df["delivered_to_client_date"] > df["scheduled_delivery_date"]).astype(int)
    df["delay_days"] = (df["delivered_to_client_date"] - df["scheduled_delivery_date"]).dt.days

    total_records = len(df)
    null_weight_count = df["weight_kilograms"].isna().sum()
    null_freight_count = df["freight_cost_usd"].isna().sum()
    print(f"Check 5 - Missing spend reporting:")
    print(f"  Null weight: {null_weight_count:,} ({null_weight_count / total_records * 100:.2f}%)")
    print(f"  Null freight: {null_freight_count:,} ({null_freight_count / total_records * 100:.2f}%)")

    # Quality conditions
    cond_id_null = df["id"].isna()
    cond_date_invalid = df["po_sent_to_vendor_date"].notna() & (df["delivered_to_client_date"] < df["po_sent_to_vendor_date"])
    cond_neg_qty_val = (df["line_item_quantity"] < 0) | (df["line_item_value"] < 0)

    cond_rejected = cond_id_null | cond_date_invalid | cond_neg_qty_val

    rejected_df = df[cond_rejected].copy()
    rejected_df["reason"] = np.where(
        cond_id_null[cond_rejected], "Null primary key ID",
        np.where(cond_date_invalid[cond_rejected], "Delivered date before PO sent date", "Negative quantity or line value")
    )

    clean_df = df[~cond_rejected].copy()

    print(f"Check 1 - Null ID failures: {cond_id_null.sum()}")
    print(f"Check 3 - Delivered before PO sent failures: {cond_date_invalid.sum()}")
    print(f"Check 4 - Negative quantity or value failures: {cond_neg_qty_val.sum()}")
    print(f"Silver quality gate: {len(clean_df):,} verified clean rows, {len(rejected_df):,} rejected rows.")

    print("\n" + "=" * 70)
    print("STEP 3: GOLD AGGREGATIONS & EXPORTS")
    print("=" * 70)

    # 1. Late rates by mode
    mode_gold = clean_df.groupby("shipment_mode").agg(
        shipment_count=("id", "count"),
        late_shipment_count=("late_delivery_flag", "sum"),
        late_delivery_rate=("late_delivery_flag", "mean"),
        avg_delay_days=("delay_days", "mean")
    ).reset_index()
    mode_gold["late_delivery_rate"] = mode_gold["late_delivery_rate"].round(4)
    mode_gold["avg_delay_days"] = mode_gold["avg_delay_days"].round(2)
    mode_gold = mode_gold.sort_values(by="shipment_count", ascending=False)
    mode_gold.to_csv(os.path.join(EXPORTS_DIR, "late_rates_by_mode.csv"), index=False)
    print("Generated: late_rates_by_mode.csv")

    # 2. Late rates by country
    country_gold = clean_df.groupby("country").agg(
        shipment_count=("id", "count"),
        late_shipment_count=("late_delivery_flag", "sum"),
        late_delivery_rate=("late_delivery_flag", "mean"),
        avg_delay_days=("delay_days", "mean")
    ).reset_index()
    country_gold["late_delivery_rate"] = country_gold["late_delivery_rate"].round(4)
    country_gold["avg_delay_days"] = country_gold["avg_delay_days"].round(2)
    country_gold = country_gold.sort_values(by="shipment_count", ascending=False)
    country_gold.to_csv(os.path.join(EXPORTS_DIR, "late_rates_by_country.csv"), index=False)
    print("Generated: late_rates_by_country.csv")

    # 3. Late rates by vendor
    vendor_gold = clean_df.groupby("vendor").agg(
        shipment_count=("id", "count"),
        late_shipment_count=("late_delivery_flag", "sum"),
        late_delivery_rate=("late_delivery_flag", "mean"),
        avg_delay_days=("delay_days", "mean")
    ).reset_index()
    vendor_gold["late_delivery_rate"] = vendor_gold["late_delivery_rate"].round(4)
    vendor_gold["avg_delay_days"] = vendor_gold["avg_delay_days"].round(2)
    vendor_gold = vendor_gold.sort_values(by="shipment_count", ascending=False)
    vendor_gold.to_csv(os.path.join(EXPORTS_DIR, "late_rates_by_vendor.csv"), index=False)
    print("Generated: late_rates_by_vendor.csv")

    # 4. Freight value summary
    freight_valid = clean_df[clean_df["freight_cost_usd"].notna() & clean_df["line_item_value"].notna()].copy()
    freight_gold = freight_valid.groupby(["product_group", "shipment_mode"]).agg(
        shipment_count=("id", "count"),
        total_line_value=("line_item_value", "sum"),
        total_freight_cost=("freight_cost_usd", "sum")
    ).reset_index()
    freight_gold["total_line_value"] = freight_gold["total_line_value"].round(2)
    freight_gold["total_freight_cost"] = freight_gold["total_freight_cost"].round(2)
    freight_gold["freight_share_of_value"] = (
        freight_gold["total_freight_cost"] / freight_gold["total_line_value"]
    ).round(4)
    freight_gold = freight_gold.sort_values(by="shipment_count", ascending=False)
    freight_gold.to_csv(os.path.join(EXPORTS_DIR, "freight_value_summary.csv"), index=False)
    print("Generated: freight_value_summary.csv")

    # 5. Yearly delivery trend
    clean_df["delivery_year"] = clean_df["delivered_to_client_date"].dt.year
    yearly_df = clean_df[clean_df["delivery_year"].notna() & (clean_df["delivery_year"] >= 2006) & (clean_df["delivery_year"] <= 2015)].copy()
    yearly_df["delivery_year"] = yearly_df["delivery_year"].astype(int)
    yearly_gold = yearly_df.groupby("delivery_year").agg(
        shipment_count=("id", "count"),
        late_count=("late_delivery_flag", "sum"),
        late_rate=("late_delivery_flag", "mean"),
        avg_delay_days=("delay_days", "mean")
    ).reset_index().rename(columns={"delivery_year": "year"})
    yearly_gold["late_rate"] = yearly_gold["late_rate"].round(4)
    yearly_gold["avg_delay_days"] = yearly_gold["avg_delay_days"].round(2)
    yearly_gold = yearly_gold.sort_values(by="year")
    yearly_gold.to_csv(os.path.join(EXPORTS_DIR, "yearly_delivery_trend.csv"), index=False)
    print("Generated: yearly_delivery_trend.csv")

    # 6. Vendor & mode combinations
    vm_gold = clean_df.groupby(["vendor", "shipment_mode"]).agg(
        shipment_count=("id", "count"),
        late_count=("late_delivery_flag", "sum"),
        late_rate=("late_delivery_flag", "mean"),
        avg_delay_days=("delay_days", "mean")
    ).reset_index()
    vm_gold["late_rate"] = vm_gold["late_rate"].round(4)
    vm_gold["avg_delay_days"] = vm_gold["avg_delay_days"].round(2)
    vm_gold = vm_gold.sort_values(by=["vendor", "shipment_mode"])
    vm_gold.to_csv(os.path.join(EXPORTS_DIR, "vendor_mode_combinations.csv"), index=False)
    print("Generated: vendor_mode_combinations.csv")

    print("\n" + "=" * 70)
    print("STEP 4: MACHINE LEARNING DELAY PREDICTION")
    print("=" * 70)

    clean_df["planned_lead_time_days"] = (
        clean_df["scheduled_delivery_date"] - clean_df["po_sent_to_vendor_date"]
    ).dt.days

    print("Target class balance:\n", clean_df["late_delivery_flag"].value_counts(normalize=True).round(4))

    num_cols = ["line_item_quantity", "line_item_value", "weight_kilograms", "planned_lead_time_days"]
    cat_cols = ["vendor", "country", "shipment_mode", "product_group"]
    X = clean_df[num_cols + cat_cols]
    y = clean_df["late_delivery_flag"]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )
    print(f"Split dataset: {len(X_train):,} training samples, {len(X_test):,} test samples.")

    preprocessor = ColumnTransformer(
        transformers=[
            ("num", Pipeline([
                ("imp", SimpleImputer(strategy="median")),
                ("scaler", StandardScaler())
            ]), num_cols),
            ("cat", Pipeline([
                ("imp", SimpleImputer(strategy="constant", fill_value="missing")),
                ("ohe", OneHotEncoder(handle_unknown="ignore", sparse_output=False))
            ]), cat_cols),
        ]
    )

    def evaluate_model(name, model):
        print(f"\n--- Training {name} ---")
        model.fit(X_train, y_train)
        preds = model.predict(X_test)
        probs = model.predict_proba(X_test)[:, 1]

        prec = float(precision_score(y_test, preds, zero_division=0))
        rec = float(recall_score(y_test, preds, zero_division=0))
        f1 = float(f1_score(y_test, preds, zero_division=0))
        auc = float(roc_auc_score(y_test, probs))

        print(f"{name} Results:")
        print(f"  Precision: {prec:.4f}")
        print(f"  Recall:    {rec:.4f}")
        print(f"  F1 Score:  {f1:.4f}")
        print(f"  ROC AUC:   {auc:.4f}")
        print("\nConfusion Matrix:")
        print(confusion_matrix(y_test, preds))
        return {"name": name, "precision": prec, "recall": rec, "f1": f1, "roc_auc": auc}

    lr_pipeline = Pipeline([
        ("prep", preprocessor),
        ("clf", LogisticRegression(max_iter=1000, class_weight="balanced", random_state=42))
    ])

    gb_pipeline = Pipeline([
        ("prep", preprocessor),
        ("clf", HistGradientBoostingClassifier(random_state=42, class_weight="balanced"))
    ])

    lr_res = evaluate_model("Baseline (Logistic Regression)", lr_pipeline)
    gb_res = evaluate_model("Final (HistGradientBoosting)", gb_pipeline)

    print("\n" + "=" * 70)
    print("PIPELINE EXECUTION COMPLETE")
    print("=" * 70)
    return {"lr": lr_res, "gb": gb_res}


if __name__ == "__main__":
    run_pipeline()
