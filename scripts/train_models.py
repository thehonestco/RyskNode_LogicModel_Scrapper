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
    'current_ratio', 
    'quick_ratio', 
    'debt_to_equity', 
    'dso', 
    'working_capital', 
    'epfo_headcount', 
    'legal_score'
]
TARGET = 'default_flag'

def main():
    project_root = Path(__file__).resolve().parents[1]
    data_path = project_root / "training_data.csv"
    models_dir = project_root / "models"
    
    if not data_path.exists():
        logger.error(f"Training data not found at {data_path}. Please place training_data.csv in the project root.")
        return
        
    models_dir.mkdir(exist_ok=True)
    
    logger.info("Loading training data...")
    df = pd.read_csv(data_path)
    
    # Ensure all required features exist
    missing_cols = [col for col in FEATURES_7 if col not in df.columns]
    if missing_cols:
        logger.error(f"Missing required columns in training_data.csv: {missing_cols}")
        return
        
    if TARGET not in df.columns:
        logger.error(f"Target column '{TARGET}' not found in training_data.csv")
        return
        
    X = df[FEATURES_7]
    y = df[TARGET]
    
    # Simple imputation for any missing values
    imputer = SimpleImputer(strategy='median')
    X_imputed = imputer.fit_transform(X)
    
    X_train, X_test, y_train, y_test = train_test_split(X_imputed, y, test_size=0.2, random_state=42, stratify=y)
    
    # -------------------------------------------------------------------------
    # 1. Train LightGBM Classifier (Calibrated)
    # -------------------------------------------------------------------------
    logger.info("Training LightGBM Classifier...")
    lgbm_base = lgb.LGBMClassifier(n_estimators=100, random_state=42, class_weight='balanced')
    lgbm_cal = CalibratedClassifierCV(estimator=lgbm_base, method='isotonic', cv=3)
    lgbm_cal.fit(X_train, y_train)
    joblib.dump(lgbm_cal, models_dir / "lgbm_calibrated.pkl")
    logger.info("Saved lgbm_calibrated.pkl")

    # -------------------------------------------------------------------------
    # 2. Train XGBoost Classifier (Calibrated)
    # -------------------------------------------------------------------------
    logger.info("Training XGBoost Classifier...")
    pos_weight = len(y[y==0])/len(y[y==1]) if len(y[y==1]) > 0 else 1
    xgb_base = xgb.XGBClassifier(n_estimators=100, random_state=42, scale_pos_weight=pos_weight)
    xgb_cal = CalibratedClassifierCV(estimator=xgb_base, method='isotonic', cv=3)
    xgb_cal.fit(X_train, y_train)
    joblib.dump(xgb_cal, models_dir / "xgb_calibrated.pkl")
    logger.info("Saved xgb_calibrated.pkl")

    # -------------------------------------------------------------------------
    # 3. Train Scorecard (Logistic Regression)
    # -------------------------------------------------------------------------
    logger.info("Training Logistic Regression Scorecard...")
    lr = LogisticRegression(max_iter=1000, class_weight='balanced', random_state=42)
    lr.fit(X_train, y_train)
    
    joblib.dump(lr, models_dir / "scorecard.pkl")
    logger.info("Saved scorecard.pkl")

    # -------------------------------------------------------------------------
    # 4. Train LGD Model (LightGBM Regressor)
    # -------------------------------------------------------------------------
    logger.info("Training LGD Regressor...")
    # Since our training data only has 'default_flag', we'll simulate an LGD target 
    # to demonstrate the pipeline. A real dataset would have 'lgd_target'.
    lgd_target = np.random.uniform(0.2, 0.8, size=len(y_train)) 
    lgd_base = lgb.LGBMRegressor(n_estimators=100, random_state=42)
    lgd_base.fit(X_train, lgd_target)
    joblib.dump(lgd_base, models_dir / "lgd.pkl")
    logger.info("Saved lgd.pkl")
    
    # -------------------------------------------------------------------------
    # 5. Save Metadata and X_train (for SHAP/Explainability)
    # -------------------------------------------------------------------------
    logger.info("Saving metadata and X_train for explainability...")
    meta = {"feature_cols": FEATURES_7}
    joblib.dump(meta, models_dir / "meta.pkl")
    joblib.dump(X_train, models_dir / "X_train.pkl")
    
    logger.info(f"Training complete! All artifacts have been saved to {models_dir}.")
    logger.info("Restart the FastAPI application for the new models to take effect.")

if __name__ == "__main__":
    main()
