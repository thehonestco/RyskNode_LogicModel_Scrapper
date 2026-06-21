"""
part2/explainability/explainer.py
=================================
Buyer Risk Explainability — SHAP + LIME + PDP + Narrative
----------------------------------------------------------
Purpose
-------
RyskNode is a counterparty risk platform. The SELLER (MSME) submits a
BUYER's identifiers. This module explains WHY a particular Buyer received
their risk rating — giving the Seller actionable, auditable intelligence
on the Buyer's payment risk.

This module provides **permanent, production-grade explainability** for every
credit decision produced by the RA scoring pipeline. It wraps the existing
LightGBM and XGBoost PD models — zero architecture change is required.

This is NOT a one-off debugging tool. Every sanctioned or declined case
should have an explanation generated and stored alongside the score output.

Three levels of explanation
---------------------------
1. **Global (portfolio)**  — SHAP summary plot + feature importance bar chart.
   Run monthly / after model retraining. Tells risk committee which Buyer
   features are driving decisions across the entire portfolio.

2. **Local (per Buyer)** — SHAP waterfall plot + LIME local explanation.
   Generated at scoring time for each Buyer. Answers: "Why was Buyer XYZ
   flagged as high risk?" Stored in part2/reports/explanations/.

3. **Narrative** — Plain-English decision rationale auto-generated from SHAP
   values. Suitable for Seller-facing memo and risk committee reporting.
   Example output::

       BUYER RISK ASSESSMENT — BUYER_GST_001
       Decision: EXCEEDS ADVISED  (Band: CCC | Blended PD: 17.9%)
       Advised Seller Exposure Limit: ₹0

       Primary risk drivers (Buyer):
         • DSCR 0.90  — below minimum viable threshold       (-38 pts)
         • Leverage 0.80  — heavy debt load relative to assets (-31 pts)
         • Payment history 40/100  — poor repayment track record (-22 pts)

       Mitigating factors:
         • Sector risk 2/5  — moderate sector exposure          (+8 pts)

Regulatory alignment
--------------------
RBI's Draft Guidelines on Use of AI/ML in Financial Services (2024) require
financial institutions to maintain explainability for AI-driven credit
decisions. This module produces auditable, per-case explanation records.

Conceptual lineage
------------------
XAI patterns adapted from:
  Nallakaruppan et al. (2024). "An Explainable AI framework for credit
  evaluation and analysis." Applied Soft Computing.
  doi: 10.1016/j.asoc.2024.111307
  GitHub: https://github.com/Kaif0708/Credit-Risk-Explainability

Key difference: that work applies XAI to binary loan approval on retail
data. This module applies the same SHAP + LIME pattern to Buyer PD
regression models with calibrated probability outputs, for trade credit
counterparty risk assessment.
"""

from __future__ import annotations

import json
import logging
import warnings
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
logger = logging.getLogger(__name__)

# Optional heavy dependencies — graceful fallback if not installed
try:
    import shap

    _SHAP_AVAILABLE = True
except ImportError:
    _SHAP_AVAILABLE = False
    logger.warning("shap not installed. Run: pip install shap")

try:
    from lime import lime_tabular

    _LIME_AVAILABLE = True
except ImportError:
    _LIME_AVAILABLE = False
    logger.warning("lime not installed. Run: pip install lime")

try:
    import matplotlib

    matplotlib.use("Agg")  # non-interactive backend for server use
    import matplotlib.pyplot as plt

    _MPL_AVAILABLE = True
except ImportError:
    _MPL_AVAILABLE = False

EXPLAINATIONS_DIR = Path(__file__).resolve().parents[2] / "part2" / "reports" / "explanations"

# Feature display names — maps internal Buyer feature column names to human-readable labels
FEATURE_LABELS: Dict[str, str] = {
    "dscr": "Debt Service Coverage Ratio",
    "leverage": "Leverage (Debt/Assets)",
    "collateral_cover": "Collateral Cover Ratio",
    "sector_risk": "Sector Risk Score",
    "payment_history_score": "Payment History Score",
    "net_revenue": "Net Revenue (Buyer)",
    "current_ratio": "Current Ratio",
    "interest_coverage": "Interest Coverage Ratio",
    "years_in_operation": "Years in Operation",
    "promoter_stake": "Promoter Stake (%)",
}


# ─────────────────────────────────────────────────────────────────────────────
class CreditExplainer:
    """
    Permanent XAI wrapper for the RyskNode Buyer PD models.

    Scores the BUYER — the counterparty whose payment risk the SELLER
    needs to assess before extending trade credit.

    Parameters
    ----------
    lgbm_model : fitted LightGBM model
        The trained LightGBM Buyer PD model from lgbm_trainer.py.
    xgb_model : fitted XGBoost model
        The trained XGBoost Buyer PD model from xgb_trainer.py.
    feature_names : list[str]
        Ordered list of Buyer feature column names used during model training.
    X_train : np.ndarray or pd.DataFrame
        Training data — required to fit SHAP explainers and LIME background.
    primary_model : str
        Which model to use as the primary explainer. 'lgbm' or 'xgb'.
        Default: 'lgbm' (Tree SHAP is fastest on LightGBM).

    Example
    -------
    ::

        from domain.explainability import CreditExplainer

        explainer = CreditExplainer(
            lgbm_model   = lgbm_model,
            xgb_model    = xgb_model,
            feature_names= FEATURE_COLS,
            X_train      = X_train,
        )

        # Explain a Buyer at scoring time — called by Seller's risk check
        report = explainer.explain_buyer(
            buyer_id     = "BUYER_GST_001",
            x_instance   = x_row,          # 1-D numpy array of Buyer features
            blended_pd   = 0.179,
            band         = "CCC",
            decision     = "declined",
        )
        print(report["narrative"])   # → Seller-facing memo

        # Global portfolio explainability (run monthly)
        explainer.explain_portfolio(X_test, save_plots=True)
    """

    def __init__(
        self,
        lgbm_model: Any,
        xgb_model: Any,
        feature_names: List[str],
        X_train: Any,
        primary_model: str = "lgbm",
    ):
        self.lgbm_model = lgbm_model
        self.xgb_model = xgb_model
        self.feature_names = feature_names
        self.X_train = np.array(X_train)
        self.primary_model = primary_model

        self._shap_explainer: Optional[Any] = None
        self._lime_explainer: Optional[Any] = None
        self._shap_fitted = False

    # ── Lazy init ────────────────────────────────────────────────────────────

    def _init_shap(self) -> None:
        """Initialise Tree SHAP explainer (lazy — only when first needed)."""
        if not _SHAP_AVAILABLE:
            raise ImportError("Install shap: pip install shap")
        if self._shap_fitted:
            return
        model = self.lgbm_model if self.primary_model == "lgbm" else self.xgb_model
        self._shap_explainer = shap.TreeExplainer(
            model,
            data=shap.sample(self.X_train, min(100, len(self.X_train))),
            feature_perturbation="interventional",
        )
        self._shap_fitted = True
        logger.info("CreditExplainer: SHAP TreeExplainer initialised.")

    def _init_lime(self) -> None:
        """Initialise LIME tabular explainer (lazy)."""
        if not _LIME_AVAILABLE:
            raise ImportError("Install lime: pip install lime")
        if self._lime_explainer is not None:
            return
        self._lime_explainer = lime_tabular.LimeTabularExplainer(
            self.X_train,
            feature_names=self.feature_names,
            class_names=["Non-Default", "Default"],
            mode="classification",
            discretize_continuous=True,
            random_state=42,
        )
        logger.info("CreditExplainer: LIME LimeTabularExplainer initialised.")

    # ─────────────────────────────────────────────────────────────────────────
    # 1. Local (per-Buyer) explanation
    # ─────────────────────────────────────────────────────────────────────────

    def explain_buyer(
        self,
        buyer_id: str,
        x_instance: np.ndarray,
        blended_pd: float,
        band: str,
        decision: str,
        advised_limit: float = 0.0,
        save: bool = True,
    ) -> Dict:
        """
        Generate a full local explanation for a single Buyer.

        This explanation is returned to the Seller as the rationale
        behind the credit decision on their counterparty.

        Parameters
        ----------
        buyer_id     : Buyer entity identifier (GSTIN / PAN / CIN or internal ID)
        x_instance   : 1-D numpy array of Buyer feature values in feature_names order
        blended_pd   : Buyer's blended PD from openriskscore_blender.py
        band         : Buyer's master scale band (e.g. "CCC")
        decision     : "approved" | "declined" | "within_limit" | "exceeds_advised"
        advised_limit: advised Seller exposure limit from limit_advisor.py (0 if declined)
        save         : if True, saves JSON + plots to part2/reports/explanations/

        Returns
        -------
        dict with keys: buyer_id, shap_values, shap_ranked, lime_explanation,
                        narrative, plot_paths
        """
        x_instance = np.array(x_instance).flatten()
        report: Dict = {
            "buyer_id": buyer_id,
            "generated_at": datetime.now().isoformat(),
            "blended_pd": blended_pd,
            "band": band,
            "decision": decision,
            "advised_limit": advised_limit,
            "shap_values": {},
            "shap_ranked": [],
            "lime_explanation": {},
            "narrative": "",
            "plot_paths": [],
        }

        # ── SHAP local explanation ──────────────────────────────────────
        if _SHAP_AVAILABLE:
            try:
                self._init_shap()
                sv = self._shap_explainer(x_instance.reshape(1, -1))
                shap_vals = sv.values[0] if hasattr(sv, "values") else sv[0]

                # Map to feature names
                shap_dict = {self.feature_names[i]: float(shap_vals[i]) for i in range(len(self.feature_names))}
                # Rank by absolute impact
                ranked = sorted(shap_dict.items(), key=lambda x: abs(x[1]), reverse=True)
                report["shap_values"] = shap_dict
                report["shap_ranked"] = [
                    {
                        "feature": feat,
                        "label": FEATURE_LABELS.get(feat, feat),
                        "feature_value": float(x_instance[self.feature_names.index(feat)]),
                        "shap_value": round(sv, 5),
                        "direction": "risk_increasing" if sv > 0 else "risk_reducing",
                    }
                    for feat, sv in ranked
                ]

                # Waterfall plot
                if _MPL_AVAILABLE and save:
                    plot_path = self._save_shap_waterfall(buyer_id, sv, x_instance)
                    report["plot_paths"].append(str(plot_path))

            except Exception as e:
                logger.warning("SHAP local explanation failed for Buyer %s: %s", buyer_id, e)

        # ── LIME local explanation ──────────────────────────────────────
        if _LIME_AVAILABLE:
            try:
                self._init_lime()
                model = self.lgbm_model if self.primary_model == "lgbm" else self.xgb_model

                def _predict_fn(X):
                    """Wrap model to return [P(non-default), P(default)] columns."""
                    pds = np.array(model.predict(X)).flatten()
                    return np.column_stack([1 - pds, pds])

                lime_exp = self._lime_explainer.explain_instance(
                    x_instance,
                    _predict_fn,
                    num_features=min(8, len(self.feature_names)),
                    top_labels=1,
                )
                lime_list = lime_exp.as_list()
                report["lime_explanation"] = {
                    "features": [{"condition": cond, "weight": round(weight, 5)} for cond, weight in lime_list]
                }

                # LIME plot
                if _MPL_AVAILABLE and save:
                    plot_path = self._save_lime_plot(buyer_id, lime_exp)
                    report["plot_paths"].append(str(plot_path))

            except Exception as e:
                logger.warning("LIME explanation failed for Buyer %s: %s", buyer_id, e)

        # ── Narrative ───────────────────────────────────────────────
        report["narrative"] = self._build_narrative(
            buyer_id, blended_pd, band, decision, advised_limit, report["shap_ranked"]
        )

        if save:
            self._save_explanation(buyer_id, report)

        return report

    # Backward-compat alias
    def explain_borrower(self, entity_id: str = "UNKNOWN", **kwargs) -> Dict:
        """Deprecated alias — use explain_buyer() instead."""
        return self.explain_buyer(buyer_id=entity_id, **kwargs)

    # ─────────────────────────────────────────────────────────────────────────
    # 2. Global (portfolio) explanation
    # ─────────────────────────────────────────────────────────────────────────

    def explain_portfolio(
        self,
        X: np.ndarray,
        entity_ids: Optional[List[str]] = None,
        save_plots: bool = True,
        top_n: int = 10,
    ) -> Dict:
        """
        Global portfolio-level Buyer explainability.

        Computes SHAP values for every Buyer in X, producing:
          - Mean absolute SHAP per feature (global importance ranking)
          - Summary beeswarm plot (feature vs impact distribution)
          - Bar chart of top-N Buyer features by mean |SHAP|

        Run this monthly after model retraining or after outcome validation.

        Parameters
        ----------
        X           : Buyer feature matrix (n_buyers × n_features)
        entity_ids  : optional list of Buyer IDs for labelling
        save_plots  : save PNG plots to part2/reports/explanations/
        top_n       : number of top features to show in bar chart

        Returns
        -------
        dict with keys: feature_importance (ranked), plot_paths
        """
        if not _SHAP_AVAILABLE:
            raise ImportError("Install shap: pip install shap")

        self._init_shap()
        X = np.array(X)
        sv = self._shap_explainer(X)
        shap_vals = sv.values if hasattr(sv, "values") else sv

        mean_abs = np.mean(np.abs(shap_vals), axis=0)
        ranked = sorted(zip(self.feature_names, mean_abs.tolist()), key=lambda x: x[1], reverse=True)

        result: Dict = {
            "generated_at": datetime.now().isoformat(),
            "n_buyers": len(X),
            "feature_importance": [
                {
                    "rank": i + 1,
                    "feature": feat,
                    "label": FEATURE_LABELS.get(feat, feat),
                    "mean_abs_shap": round(v, 6),
                }
                for i, (feat, v) in enumerate(ranked)
            ],
            "plot_paths": [],
        }

        if save_plots and _MPL_AVAILABLE:
            bar_path = self._save_global_importance(
                [r["label"] for r in result["feature_importance"][:top_n]],
                [r["mean_abs_shap"] for r in result["feature_importance"][:top_n]],
            )
            result["plot_paths"].append(str(bar_path))

            beeswarm_path = self._save_shap_summary(sv, X)
            result["plot_paths"].append(str(beeswarm_path))

        # Save JSON
        EXPLANATIONS_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = EXPLANATIONS_DIR / f"portfolio_explanation_{ts}.json"
        with open(out, "w") as fh:
            json.dump(result, fh, indent=2, default=str)
        logger.info("Portfolio Buyer explanation saved → %s", out)

        return result

    # ─────────────────────────────────────────────────────────────────────────
    # 3. Partial Dependency Plots (PDP)
    # ─────────────────────────────────────────────────────────────────────────

    def plot_pdp(
        self,
        X: np.ndarray,
        feature_name: str,
        n_points: int = 50,
        save: bool = True,
    ) -> Optional[Path]:
        """
        Partial Dependency Plot for a single Buyer feature.

        Shows how the model's predicted Buyer PD changes as feature_name is
        varied across its range, holding all other Buyer features at their
        observed values. Useful for risk committee presentations:
        "As Buyer DSCR falls below 1.0, payment default probability rises non-linearly."

        Parameters
        ----------
        X            : Buyer feature matrix (n_buyers × n_features)
        feature_name : Buyer column name to vary (must be in feature_names)
        n_points     : number of grid points to evaluate
        save         : save PNG to part2/reports/explanations/

        Returns
        -------
        Path to saved PNG or None
        """
        if not _MPL_AVAILABLE:
            logger.warning("matplotlib not available — PDP skipped.")
            return None

        if feature_name not in self.feature_names:
            raise ValueError(f"'{feature_name}' not in feature_names. Available: {self.feature_names}")

        model = self.lgbm_model if self.primary_model == "lgbm" else self.xgb_model
        X = np.array(X)
        feat_idx = self.feature_names.index(feature_name)
        feat_vals = np.linspace(X[:, feat_idx].min(), X[:, feat_idx].max(), n_points)
        pdp_preds = []

        for v in feat_vals:
            X_mod = X.copy()
            X_mod[:, feat_idx] = v
            preds = np.array(model.predict(X_mod)).flatten()
            pdp_preds.append(float(np.mean(preds)))

        fig, ax = plt.subplots(figsize=(8, 4))
        ax.plot(feat_vals, pdp_preds, color="#01696f", linewidth=2)
        ax.fill_between(feat_vals, pdp_preds, alpha=0.08, color="#01696f")
        label = FEATURE_LABELS.get(feature_name, feature_name)
        ax.set_xlabel(label, fontsize=11)
        ax.set_ylabel("Avg Buyer PD (Predicted)", fontsize=11)
        ax.set_title(f"Partial Dependency Plot — Buyer {label}", fontsize=12)
        ax.axhline(0, color="grey", linestyle="--", linewidth=0.8)
        ax.grid(True, alpha=0.3)
        plt.tight_layout()

        path = None
        if save:
            EXPLANATIONS_DIR.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = EXPLANATIONS_DIR / f"pdp_{feature_name}_{ts}.png"
            fig.savefig(path, dpi=150, bbox_inches="tight")
            logger.info("PDP saved → %s", path)
        plt.close(fig)
        return path

    # ─────────────────────────────────────────────────────────────────────────
    # Narrative builder
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _build_narrative(
        buyer_id: str,
        blended_pd: float,
        band: str,
        decision: str,
        advised_limit: float,
        shap_ranked: List[Dict],
    ) -> str:
        """
        Build a plain-English Buyer risk assessment narrative from SHAP-ranked features.

        This narrative is suitable for:
          - Seller-facing credit decision memo
          - Risk committee reporting
          - Regulatory transparency / audit trail
          - Model explainability record
        """
        lines = []
        lines.append(f"BUYER RISK ASSESSMENT — {buyer_id}")
        lines.append("-" * 50)
        dec_upper = decision.upper().replace("_", " ")
        lines.append(f"Decision: {dec_upper}  (Band: {band} | Blended PD: {blended_pd * 100:.1f}%)")
        if advised_limit > 0:
            lines.append(f"Advised Seller Exposure Limit: \u20b9{advised_limit:,.0f}")
        else:
            lines.append("Advised Seller Exposure Limit: ₹0 (Buyer declined)")
        lines.append("")

        risk_factors = [f for f in shap_ranked if f["direction"] == "risk_increasing"]
        mitigants = [f for f in shap_ranked if f["direction"] == "risk_reducing"]

        if risk_factors:
            lines.append("Primary risk drivers (Buyer):")
            for f in risk_factors[:4]:
                label = f["label"]
                val = f["feature_value"]
                sv = f["shap_value"]
                lines.append(f"  \u2022 {label}: {val:.3g}  (impact: +{sv:+.4f} on default probability)")

        if mitigants:
            lines.append("")
            lines.append("Mitigating factors (Buyer):")
            for f in mitigants[:3]:
                label = f["label"]
                val = f["feature_value"]
                sv = f["shap_value"]
                lines.append(f"  \u2022 {label}: {val:.3g}  (impact: {sv:+.4f} on default probability)")

        if not shap_ranked:
            lines.append("(SHAP not available — install shap for detailed breakdown)")

        return "\n".join(lines)

    # ─────────────────────────────────────────────────────────────────────────
    # Plot savers (internal)
    # ─────────────────────────────────────────────────────────────────────────

    def _save_shap_waterfall(self, buyer_id: str, shap_values: Any, x_instance: np.ndarray) -> Path:
        EXPLANATIONS_DIR.mkdir(parents=True, exist_ok=True)
        path = EXPLANATIONS_DIR / f"shap_waterfall_{buyer_id}.png"
        try:
            fig, ax = plt.subplots(figsize=(10, 5))
            shap.plots.waterfall(shap_values[0], max_display=10, show=False)
            plt.title(f"SHAP Waterfall — Buyer {buyer_id}", pad=12)
            plt.tight_layout()
            plt.savefig(path, dpi=150, bbox_inches="tight")
            plt.close()
        except Exception as e:
            logger.warning("Waterfall plot failed for Buyer %s: %s", buyer_id, e)
            plt.close("all")
        return path

    def _save_lime_plot(self, buyer_id: str, lime_exp: Any) -> Path:
        EXPLANATIONS_DIR.mkdir(parents=True, exist_ok=True)
        path = EXPLANATIONS_DIR / f"lime_{buyer_id}.png"
        try:
            fig = lime_exp.as_pyplot_figure()
            fig.suptitle(f"LIME Explanation — Buyer {buyer_id}", fontsize=12)
            plt.tight_layout()
            fig.savefig(path, dpi=150, bbox_inches="tight")
            plt.close(fig)
        except Exception as e:
            logger.warning("LIME plot failed for Buyer %s: %s", buyer_id, e)
            plt.close("all")
        return path

    def _save_global_importance(self, labels: List[str], values: List[float]) -> Path:
        EXPLANATIONS_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = EXPLANATIONS_DIR / f"global_importance_{ts}.png"
        fig, ax = plt.subplots(figsize=(9, 5))
        ax.barh(labels[::-1], values[::-1], color="#01696f", alpha=0.85)
        ax.set_xlabel("Mean |SHAP Value|", fontsize=11)
        ax.set_title("Global Buyer Feature Importance (SHAP)", fontsize=12)
        ax.grid(axis="x", alpha=0.3)
        plt.tight_layout()
        fig.savefig(path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        return path

    def _save_shap_summary(self, shap_values: Any, X: np.ndarray) -> Path:
        EXPLANATIONS_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = EXPLANATIONS_DIR / f"shap_summary_{ts}.png"
        try:
            fig, ax = plt.subplots(figsize=(10, 6))
            shap.summary_plot(
                shap_values.values if hasattr(shap_values, "values") else shap_values,
                X,
                feature_names=self.feature_names,
                show=False,
                plot_type="dot",
            )
            plt.title("SHAP Summary — Buyer Portfolio", pad=12)
            plt.tight_layout()
            plt.savefig(path, dpi=150, bbox_inches="tight")
            plt.close()
        except Exception as e:
            logger.warning("SHAP summary plot failed: %s", e)
            plt.close("all")
        return path

    # ─────────────────────────────────────────────────────────────────────────
    # Persist explanation JSON
    # ─────────────────────────────────────────────────────────────────────────

    def _save_explanation(self, buyer_id: str, report: Dict) -> None:
        EXPLANATIONS_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = EXPLANATIONS_DIR / f"explanation_{buyer_id}_{ts}.json"
        with open(path, "w") as fh:
            json.dump(report, fh, indent=2, default=str)
        logger.info("Buyer explanation saved → %s", path)
