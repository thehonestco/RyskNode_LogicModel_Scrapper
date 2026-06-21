"""
part2/lgd/openlgd_model.py
===========================
openLGD Model — Loss Given Default estimation.

Uses the loss_recovery table from Part 1 historical store.
openLGD provides beta regression and cure-rate modeling purpose-built
for credit LGD estimation.

Input table (loss_recovery)
---------------------------
  loss_id          : unique row
  decision_id      : link to underwriting decision
  ead              : exposure at default (INR)
  recovery_amount  : amount recovered (INR)
  writeoff_amount  : amount written off (INR)
  recovery_end_date: recovery completion date
  lgd_target       : realized LGD = 1 - (recovery_amount / ead)

Features used for LGD prediction
---------------------------------
  Numeric features from the joined feature_snapshot:
    net_revenue_latest, tangible_net_worth, current_ratio,
    debt_to_equity, business_vintage_years, ead,
    governance_score (from pd_mapper)

Outputs
-------
  lgd_pred  : predicted LGD (0–1) per exposure
  el_pct    : EL% = PD% × LGD%
  el_amount : EL in INR = EL% × EAD
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional

import joblib
import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_absolute_error, r2_score

logger = logging.getLogger(__name__)

LGD_FEATURES = [
    "net_revenue_latest",
    "tangible_net_worth",
    "current_ratio",
    "debt_to_equity",
    "business_vintage_years",
    "ead",
    "governance_score",
]


def _beta_clip(arr: np.ndarray, eps: float = 1e-6) -> np.ndarray:
    """Clip LGD values to (0, 1) open interval for beta regression."""
    return np.clip(arr, eps, 1 - eps)


def train_lgd_model(
    df: pd.DataFrame,
    target_col: str = "lgd_target",
    feature_cols: Optional[List[str]] = None,
    test_size: float = 0.2,
    artifact_path: Optional[str] = None,
) -> Dict:
    """
    Train openLGD model on the loss_recovery table.

    Falls back to a GradientBoostingRegressor if openLGD is not
    installed, to keep the pipeline runnable during development.

    Parameters
    ----------
    df            : loss_recovery table joined with feature_snapshot
    target_col    : column containing realized LGD (0–1)
    feature_cols  : features to use (defaults to LGD_FEATURES)
    test_size     : hold-out fraction
    artifact_path : path to save fitted model artifact

    Returns
    -------
    dict with model, mae, r2, feature_cols
    """
    feat = feature_cols or LGD_FEATURES
    feat = [f for f in feat if f in df.columns]

    X = df[feat].fillna(-999).values
    y = _beta_clip(df[target_col].clip(0, 1).values)

    X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=test_size, random_state=42)

    try:
        import openLGD

        model = openLGD.BetaRegression()
        logger.info("Using openLGD BetaRegression")
    except ImportError:
        from sklearn.ensemble import GradientBoostingRegressor

        model = GradientBoostingRegressor(
            n_estimators=200,
            learning_rate=0.05,
            max_depth=3,
            random_state=42,
        )
        logger.warning(
            "openLGD not installed — falling back to GradientBoostingRegressor. "
            "Install openLGD>=0.5 for production use."
        )

    model.fit(X_tr, y_tr)
    y_pred = np.clip(model.predict(X_te), 0, 1)

    mae = mean_absolute_error(y_te, y_pred)
    r2 = r2_score(y_te, y_pred)
    logger.info("LGD model | MAE: %.4f | R2: %.4f", mae, r2)

    result = {
        "model": model,
        "feature_cols": feat,
        "mae": round(mae, 6),
        "r2": round(r2, 6),
    }

    if artifact_path:
        Path(artifact_path).parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(result, artifact_path)
        logger.info("LGD artifact saved to %s", artifact_path)

    return result


def predict_lgd(
    model_artifact: Dict,
    df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Predict LGD for a DataFrame of exposures.

    Returns DataFrame with columns:
      lgd_pred, el_pct, el_amount (if ead column present)
    """
    feat = model_artifact["feature_cols"]
    model = model_artifact["model"]

    feat = [f for f in feat if f in df.columns]
    X = df[feat].fillna(-999).values
    out = df.copy()
    out["lgd_pred"] = np.clip(model.predict(X), 0, 1)

    if "pd_blended" in df.columns and "ead" in df.columns:
        out["el_pct"] = out["pd_blended"] * out["lgd_pred"]
        out["el_amount"] = out["el_pct"] * out["ead"]

    return out
