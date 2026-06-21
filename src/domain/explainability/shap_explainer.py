"""
part2/explainability/shap_explainer.py
=======================================
SHAP Explainer — global and local explanations for tree PD models.

Uses shap.TreeExplainer for LightGBM and XGBoost models.
Produces:
  - Global feature importance (mean |SHAP|)
  - Local waterfall explanation per entity
  - SHAP values DataFrame for audit trail
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def build_shap_explainer(model) -> "shap.TreeExplainer":
    """
    Build a SHAP TreeExplainer for a fitted LightGBM or XGBoost model.

    Parameters
    ----------
    model : fitted LGBMClassifier or XGBClassifier

    Returns
    -------
    shap.TreeExplainer instance
    """
    import shap

    explainer = shap.TreeExplainer(model)
    logger.info("SHAP TreeExplainer built for %s", type(model).__name__)
    return explainer


def global_feature_importance(
    explainer,
    X: pd.DataFrame,
    top_n: int = 20,
) -> pd.DataFrame:
    """
    Compute global feature importance as mean absolute SHAP value.

    Returns
    -------
    DataFrame with columns: feature, mean_abs_shap  (sorted descending)
    """
    import shap

    shap_values = explainer.shap_values(X)
    if isinstance(shap_values, list):
        shap_values = shap_values[1]  # binary: class 1

    importance = (
        pd.DataFrame(
            {
                "feature": X.columns.tolist(),
                "mean_abs_shap": np.abs(shap_values).mean(axis=0),
            }
        )
        .sort_values("mean_abs_shap", ascending=False)
        .head(top_n)
        .reset_index(drop=True)
    )

    logger.info("Top SHAP feature: %s", importance.iloc[0]["feature"])
    return importance


def local_explanation(
    explainer,
    X_row: pd.DataFrame,
    entity_id: str = "UNKNOWN",
) -> Dict:
    """
    Compute local SHAP explanation for a single entity.

    Parameters
    ----------
    explainer  : fitted shap.TreeExplainer
    X_row      : single-row DataFrame for the entity
    entity_id  : entity identifier for logging

    Returns
    -------
    dict with shap_values, base_value, feature_names,
    top_drivers (top 5 positive and negative contributors)
    """
    import shap

    shap_values = explainer.shap_values(X_row)
    if isinstance(shap_values, list):
        shap_values = shap_values[1]

    sv = shap_values[0]
    base = explainer.expected_value
    if isinstance(base, (list, np.ndarray)):
        base = base[1]

    features = X_row.columns.tolist()
    pairs = sorted(zip(features, sv), key=lambda x: abs(x[1]), reverse=True)

    top_positive = [(f, round(float(v), 6)) for f, v in pairs if v > 0][:5]
    top_negative = [(f, round(float(v), 6)) for f, v in pairs if v < 0][:5]

    logger.info(
        "SHAP local explanation for %s | base_value: %.4f | top driver: %s",
        entity_id,
        float(base),
        pairs[0][0] if pairs else "n/a",
    )

    return {
        "entity_id": entity_id,
        "base_value": round(float(base), 6),
        "shap_values": {f: round(float(v), 6) for f, v in zip(features, sv)},
        "top_positive": top_positive,
        "top_negative": top_negative,
    }


def shap_values_dataframe(
    explainer,
    X: pd.DataFrame,
) -> pd.DataFrame:
    """
    Return full SHAP values as a DataFrame (same shape as X).
    Used for audit trail storage in reason_codes_log.
    """
    import shap

    shap_values = explainer.shap_values(X)
    if isinstance(shap_values, list):
        shap_values = shap_values[1]
    return pd.DataFrame(shap_values, columns=X.columns, index=X.index)
