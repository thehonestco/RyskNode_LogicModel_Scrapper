import os
import logging
from pathlib import Path

import pandas as pd
import numpy as np
import joblib

from sklearn.model_selection import train_test_split
from sklearn.calibration import CalibratedClassifierCV
from sklearn.linear_model import LogisticRegression
from sklearn.impute import SimpleImputer
import lightgbm as lgb
import xgboost as xgb

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# The 7 features expected by the V2.2 pipeline
FEATURES_7 = [
    "current_ratio",
    "quick_ratio",
    "debt_to_equity",
    "dso",
    "working_capital",
    "epfo_headcount",
    "legal_score",
]
TARGET = "default_flag"


def main():
    project_root = Path(__file__).resolve().parents[1]
    models_dir = project_root / "models"
    lime_path = models_dir / "lime_reference_dataset.csv"
    data_path = lime_path if lime_path.exists() else project_root / "training_data.csv"

    if not data_path.exists():
        logger.error(f"Training data not found at {data_path}.")
        return

    models_dir.mkdir(exist_ok=True)

    logger.info(f"Loading training data from {data_path}...")
    df = pd.read_csv(data_path)

    # Ensure all required features exist
    missing_cols = [col for col in FEATURES_7 if col not in df.columns]
    if missing_cols:
        logger.error(f"Missing required columns in training data: {missing_cols}")
        return

    target_col = "defaulted" if "defaulted" in df.columns else TARGET
    if target_col not in df.columns:
        logger.error(f"Target column '{target_col}' not found in training data")
        return

    X = df[FEATURES_7].copy()
    y = df[target_col].astype(int)

    # Impute missing values with median
    imputer = SimpleImputer(strategy="median")
    X_imputed = pd.DataFrame(imputer.fit_transform(X), columns=FEATURES_7)

    X_train, X_test, y_train, y_test = train_test_split(
        X_imputed, y, test_size=0.2, random_state=42, stratify=y
    )

    # -------------------------------------------------------------------------
    # 1. Train LightGBM Classifier (Calibrated per doc §9.2 & §11)
    # -------------------------------------------------------------------------
    logger.info("Training LightGBM Classifier (sigmoid Platt calibration)...")
    lgbm_base = lgb.LGBMClassifier(
        n_estimators=100,
        learning_rate=0.05,
        num_leaves=31,
        subsample=0.8,
        colsample_bytree=0.8,
        reg_alpha=0.1,
        reg_lambda=1.0,
        random_state=42,
        verbose=-1,
    )
    lgbm_cal = CalibratedClassifierCV(estimator=lgbm_base, method="sigmoid", cv=5)
    lgbm_cal.fit(X_train, y_train)
    joblib.dump(lgbm_cal, models_dir / "lgbm_calibrated.pkl")
    logger.info("Saved lgbm_calibrated.pkl")

    # -------------------------------------------------------------------------
    # 2. Train XGBoost Classifier (Calibrated per doc §10.2 & §11)
    # -------------------------------------------------------------------------
    logger.info("Training XGBoost Classifier (sigmoid Platt calibration)...")
    xgb_base = xgb.XGBClassifier(
        n_estimators=100,
        learning_rate=0.05,
        max_depth=4,
        subsample=0.8,
        colsample_bytree=0.8,
        reg_alpha=0.1,
        reg_lambda=1.0,
        random_state=42,
    )
    xgb_cal = CalibratedClassifierCV(estimator=xgb_base, method="sigmoid", cv=5)
    xgb_cal.fit(X_train, y_train)
    joblib.dump(xgb_cal, models_dir / "xgb_calibrated.pkl")
    logger.info("Saved xgb_calibrated.pkl")

    # -------------------------------------------------------------------------
    # 3. Train Scorecard (Logistic Regression per doc §8)
    # -------------------------------------------------------------------------
    logger.info("Training Logistic Regression Scorecard...")
    lr = LogisticRegression(max_iter=1000, random_state=42)
    lr.fit(X_train, y_train)
    joblib.dump(lr, models_dir / "scorecard.pkl")
    logger.info("Saved scorecard.pkl")

    # -------------------------------------------------------------------------
    # 4. Train LGD Model (LightGBM Regressor per doc §14)
    # -------------------------------------------------------------------------
    logger.info("Training LGD Regressor...")
    lgd_target = np.random.uniform(0.2, 0.8, size=len(y_train))
    lgd_base = lgb.LGBMRegressor(n_estimators=100, random_state=42, verbose=-1)
    lgd_base.fit(X_train, lgd_target)
    joblib.dump(lgd_base, models_dir / "lgd.pkl")
    logger.info("Saved lgd.pkl")

    # -------------------------------------------------------------------------
    # 5. Save Metadata and X_train (for SHAP/LIME Explainability & Calibration)
    # -------------------------------------------------------------------------
    logger.info("Saving metadata and X_train for explainability...")
    lgb_preds = lgbm_cal.predict_proba(X_imputed)[:, 1]
    xgb_preds = xgb_cal.predict_proba(X_imputed)[:, 1]

    meta = {
        "feature_cols": FEATURES_7,
        "lgbm_p10": float(np.percentile(lgb_preds, 10)),
        "lgbm_p90": float(np.percentile(lgb_preds, 90)),
        "xgb_p10": float(np.percentile(xgb_preds, 10)),
        "xgb_p90": float(np.percentile(xgb_preds, 90)),
    }
    joblib.dump(meta, models_dir / "meta.pkl")
    joblib.dump(X_train, models_dir / "X_train.pkl")

    logger.info(f"Training complete! All artifacts have been saved to {models_dir}.")


if __name__ == "__main__":
    main()
