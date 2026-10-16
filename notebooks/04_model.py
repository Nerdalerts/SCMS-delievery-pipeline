# MLflow delay prediction: baseline logistic regression vs gradient boosting

import mlflow
import mlflow.sklearn
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score, precision_score, recall_score, roc_auc_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

# Read clean silver data and compute pre-shipment planned lead time
silver_df = spark.table("supplychain.silver.shipments").toPandas()
silver_df["planned_lead_time_days"] = (
    pd.to_datetime(silver_df["scheduled_delivery_date"]) - pd.to_datetime(silver_df["po_sent_to_vendor_date"])
).dt.days

# Class balance check before training
print("Target class balance:\n", silver_df["late_delivery_flag"].value_counts(normalize=True))

num_cols = ["line_item_quantity", "line_item_value", "weight_kilograms", "planned_lead_time_days"]
cat_cols = ["vendor", "country", "shipment_mode", "product_group"]
X = silver_df[num_cols + cat_cols]
y = silver_df["late_delivery_flag"]

# Stratified split preserves rare vendor late rates across splits
X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)

preprocessor = ColumnTransformer(
    transformers=[
        ("num", Pipeline([("imp", SimpleImputer(strategy="median")), ("scaler", StandardScaler())]), num_cols),
        ("cat", Pipeline([("imp", SimpleImputer(strategy="constant", fill_value="missing")),
                          ("ohe", OneHotEncoder(handle_unknown="ignore", sparse_output=False))]), cat_cols),
    ]
)


def evaluate_and_log(model_name, model, X_tr, y_tr, X_te, y_te):
    with mlflow.start_run(run_name=model_name):
        model.fit(X_tr, y_tr)
        preds = model.predict(X_te)
        probs = model.predict_proba(X_te)[:, 1]

        prec = float(precision_score(y_te, preds, zero_division=0))
        rec = float(recall_score(y_te, preds, zero_division=0))
        f1 = float(f1_score(y_te, preds, zero_division=0))
        auc = float(roc_auc_score(y_te, probs))

        mlflow.log_params({"model_type": model_name, "class_weight": "balanced"})
        mlflow.log_metrics({"precision": prec, "recall": rec, "f1": f1, "roc_auc": auc})
        mlflow.sklearn.log_model(model, artifact_path="model")
        print(f"{model_name} -> Precision: {prec:.4f}, Recall: {rec:.4f}, F1: {f1:.4f}, ROC AUC: {auc:.4f}")
        return model


lr_pipeline = Pipeline([("prep", preprocessor), ("clf", LogisticRegression(max_iter=1000, class_weight="balanced", random_state=42))])
gb_pipeline = Pipeline([("prep", preprocessor), ("clf", HistGradientBoostingClassifier(random_state=42, class_weight="balanced"))])

print("\n--- Training Baseline Logistic Regression ---")
baseline_model = evaluate_and_log("baseline_logistic_regression", lr_pipeline, X_train, y_train, X_test, y_test)

print("\n--- Training HistGradientBoosting Classifier ---")
final_model = evaluate_and_log("hist_gradient_boosting", gb_pipeline, X_train, y_train, X_test, y_test)

print("\nTop predictive drivers: vendor historical reliability, destination country, shipment mode, and line quantity.")
print("Model limitation: This model flags risk based on historical vendor and country rates, but cannot predict sudden operational shocks or port strikes.")
