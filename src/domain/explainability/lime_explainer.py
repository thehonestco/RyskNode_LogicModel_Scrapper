"""
part2/explainability/lime_explainer.py
=======================================
LIME Explainer — spot-check local explanations for any single entity.

Uses lime.lime_tabular.LimeTabularExplainer.
Used for governance checks and for explaining scorecard (LR) predictions
where SHAP TreeExplainer does not apply.

Use cases
---------
  1. Explain why a borderline entity received a specific PD band.
  2. Governance spot-check: compare LIME and SHAP reason codes.
  3. Explain LR scorecard model (Model A) predictions.
"""
from __future__ import annotations

import logging
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def build_lime_explainer(
    X_train: pd.DataFrame,
    feature_names: Optional[List[str]] = None,
    class_names: Optional[List[str]] = None,
    mode: str = "classification",
) -> "lime.lime_tabular.LimeTabularExplainer":
    """
    Build a LIME tabular explainer fitted on training data distribution.

    Parameters
    ----------
    X_train       : training feature DataFrame (used for kernel density)
    feature_names : column names (auto-detected from X_train if None)
    class_names   : class labels (default: ['No Default', 'Default'])
    mode          : 'classification' or 'regression'

    Returns
    -------
    LimeTabularExplainer instance
    """
    from lime.lime_tabular import LimeTabularExplainer

    fn = feature_names or X_train.columns.tolist()
    cn = class_names  or ["No Default", "Default"]

    explainer = LimeTabularExplainer(
        training_data  = X_train.values,
        feature_names  = fn,
        class_names    = cn,
        mode           = mode,
        discretize_continuous = True,
        random_state   = 42,
    )
    logger.info("LIME explainer built on %d training samples", len(X_train))
    return explainer


def local_lime_explanation(
    explainer,
    predict_fn: Callable,
    X_row: pd.DataFrame,
    entity_id: str = "UNKNOWN",
    num_features: int = 10,
    num_samples:  int = 1000,
) -> Dict:
    """
    Compute LIME local explanation for a single entity.

    Parameters
    ----------
    explainer    : fitted LimeTabularExplainer
    predict_fn   : model.predict_proba function
    X_row        : single-row DataFrame for the entity
    entity_id    : entity identifier for logging
    num_features : number of top features to explain
    num_samples  : LIME perturbation samples

    Returns
    -------
    dict with feature_weights, intercept, local_pred, entity_id
    """
    exp = explainer.explain_instance(
        data_row     = X_row.values[0],
        predict_fn   = predict_fn,
        num_features = num_features,
        num_samples  = num_samples,
        labels       = (1,),
    )

    weights  = exp.as_list(label=1)
    intercept = exp.intercept[1]
    local_pred = exp.local_pred[1] if hasattr(exp, "local_pred") else None

    logger.info(
        "LIME explanation for %s | local_pred: %s | top driver: %s",
        entity_id,
        f"{local_pred:.4f}" if local_pred is not None else "n/a",
        weights[0][0] if weights else "n/a",
    )

    return {
        "entity_id":       entity_id,
        "feature_weights": [(f, round(float(w), 6)) for f, w in weights],
        "intercept":       round(float(intercept), 6),
        "local_pred":      round(float(local_pred), 6) if local_pred is not None else None,
    }
