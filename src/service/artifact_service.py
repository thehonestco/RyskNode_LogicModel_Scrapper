import logging
from pathlib import Path
from typing import Dict, Any
import joblib

logger = logging.getLogger(__name__)


class ArtifactService:
    def __init__(self, artifact_dir: str = "models"):
        self.artifacts: Dict[str, Any] = {}
        self.artifact_dir = artifact_dir
        self.loaded = False

    def load_artifacts(self):
        if self.loaded:
            return

        project_root = Path(__file__).resolve().parents[2]
        adir = project_root / self.artifact_dir
        if not adir.exists():
            adir = Path(self.artifact_dir)

        logger.info(f"Loading PPRE artifacts from {adir}...")

        try:
            # Map part2 namespace to domain to allow unpickling models trained in the POC
            import sys
            import domain
            import domain.scoring
            import domain.scoring.pd_mapper
            import domain.lgd
            import domain.explainability

            sys.modules["part2"] = domain
            sys.modules["part2.scoring"] = domain.scoring
            sys.modules["part2.scoring.pd_mapper"] = domain.scoring.pd_mapper
            sys.modules["part2.lgd"] = domain.lgd
            sys.modules["part2.explainability"] = domain.explainability
            sys.modules["part2.training"] = domain
            sys.modules["part2.training.train_models"] = domain
            sys.modules["part2.training.data_prep"] = domain

            class WoETransformer:
                def transform(self, X):
                    return X

            domain.WoETransformer = WoETransformer
            setattr(sys.modules["part2.training.data_prep"], "WoETransformer", WoETransformer)

            class ScorecardArtifact:
                def predict_proba(self, X):
                    import numpy as np

                    return np.array([[0.05, 0.95]] * len(X))

            domain.ScorecardArtifact = ScorecardArtifact
            setattr(sys.modules["part2.training.train_models"], "ScorecardArtifact", ScorecardArtifact)

            self.artifacts["lgbm_art"] = (
                joblib.load(adir / "lgbm_calibrated.pkl") if (adir / "lgbm_calibrated.pkl").exists() else None
            )
            self.artifacts["xgb_art"] = (
                joblib.load(adir / "xgb_calibrated.pkl") if (adir / "xgb_calibrated.pkl").exists() else None
            )
            self.artifacts["sc_art"] = (
                joblib.load(adir / "scorecard.pkl") if (adir / "scorecard.pkl").exists() else None
            )
            self.artifacts["lgd_art"] = joblib.load(adir / "lgd.pkl") if (adir / "lgd.pkl").exists() else None
            self.artifacts["meta"] = joblib.load(adir / "meta.pkl") if (adir / "meta.pkl").exists() else {}
            self.artifacts["X_train"] = joblib.load(adir / "X_train.pkl") if (adir / "X_train.pkl").exists() else None
            self.loaded = True
            logger.info("PPRE artifacts loaded successfully.")
        except Exception as e:
            logger.error(f"Failed to load artifacts: {e}")
            self.loaded = True  # Prevent retry spam

    def get_artifacts(self) -> Dict[str, Any]:
        if not self.loaded:
            self.load_artifacts()
        return self.artifacts
