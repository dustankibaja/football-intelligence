"""Frozen Phase 4 model forward inference for verified live pre-match snapshots.

This module intentionally NEVER fits models, imputes with live statistics, or
uses heuristic coefficients. Its only source of parameters is the committed
C1/M1 training artifacts and their training-only preprocessing.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from app.features.manifest import get_mvp_a_feature_set_v1_manifest
from app.models.baselines.attack_defense_poisson import AttackDefensePoissonModel
from app.models.baselines.multinomial_logistic import MultinomialLogistic1X2Model
from app.models.dataset_dto import ModelingRowDTO
from app.models.engine import GoalDistributionEngine
from app.prediction.domain import ShadowPredictionInput


EXPECTED_C1_FINGERPRINT = "6328ef1d0b175964"
EXPECTED_M1_FINGERPRINT = "e116413051f52d5f"
EXPECTED_DATASET_FINGERPRINT = (
    "9b9ceb90da803eaa498c26a070d4ac69483fa4d06c391444b2c326f3fa03021c"
)


@dataclass(frozen=True)
class FrozenInferenceOutput:
    c1_probabilities: tuple[float, float, float]
    m1_probabilities: tuple[float, float, float]
    lambda_home: float
    lambda_away: float
    btts_yes: float
    over_2_5: float
    score_matrix: list[list[float]]
    top_scorelines: list[dict]
    score_matrix_fingerprint: str
    c1_artifact_fingerprint: str
    m1_artifact_fingerprint: str
    c1_file_sha256: str
    m1_file_sha256: str
    home_cold_start: bool
    away_cold_start: bool


class FrozenArtifactInference:
    """Load and verify actual frozen C1/M1 weights, then delegate to model code."""

    def __init__(self, artifact_dir: Path | None = None) -> None:
        self.artifact_dir = artifact_dir or Path(__file__).resolve().parents[2]
        self.c1_path = self.artifact_dir / "c1_artifact.json"
        self.m1_path = self.artifact_dir / "m1_artifact.json"
        self.c1 = MultinomialLogistic1X2Model()
        self.m1 = AttackDefensePoissonModel()
        self.c1.load_artifact(str(self.c1_path))
        self.m1.load_artifact(str(self.m1_path))
        self.c1_file_sha256 = self._file_sha256(self.c1_path)
        self.m1_file_sha256 = self._file_sha256(self.m1_path)
        self.verify()
        self.distribution_engine = GoalDistributionEngine()

    @staticmethod
    def _file_sha256(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def verify(self) -> None:
        for name, model, expected_fp, expected_key in (
            ("C1", self.c1, EXPECTED_C1_FINGERPRINT, "multinomial_logistic_1x2_v1"),
            ("M1", self.m1, EXPECTED_M1_FINGERPRINT, "attack_defense_poisson_v1"),
        ):
            artifact = model.artifact
            if (
                artifact is None
                or artifact.model_key != expected_key
                or artifact.version != "1.0.0"
                or artifact.artifact_fingerprint != expected_fp
                or not artifact.verify_integrity()
                or artifact.training_window.modeling_dataset_fingerprint != EXPECTED_DATASET_FINGERPRINT
            ):
                raise ValueError(f"{name}_FROZEN_ARTIFACT_INVALID")
        if (
            self._file_sha256(self.c1_path) != self.c1_file_sha256
            or self._file_sha256(self.m1_path) != self.m1_file_sha256
        ):
            raise ValueError("FROZEN_ARTIFACT_FILE_CHANGED_AFTER_LOADING")

    def predict(
        self, inp: ShadowPredictionInput, competition_id: UUID, season_id: UUID
    ) -> FrozenInferenceOutput:
        self.verify()
        manifest = get_mvp_a_feature_set_v1_manifest()
        if (
            len(inp.feature_values) != 251
            or inp.feature_count != 251
            or inp.feature_keys != manifest.included_feature_keys
        ):
            raise ValueError("FROZEN_FEATURE_SCHEMA_MISMATCH")
        if not inp.feature_snapshot_id or not inp.feature_snapshot_fingerprint:
            raise ValueError("FROZEN_FEATURE_PROVENANCE_MISSING")
        if not competition_id or not season_id:
            raise ValueError("CANONICAL_COMPETITION_OR_SEASON_MISSING")
        if str(competition_id) not in self.m1.artifact.weights_payload["competitions"]:
            raise ValueError("M1_COMPETITION_NOT_IN_FROZEN_ARTIFACT")
        if any(v is not None and (not isinstance(v, (float, int)) or not math.isfinite(v)) for v in inp.feature_values):
            raise ValueError("NONFINITE_OR_INVALID_LIVE_FEATURE")

        row = ModelingRowDTO(
            match_id=inp.fixture_id,
            competition_id=competition_id,
            season_id=season_id,
            kickoff_utc=inp.kickoff_utc,
            home_team_id=inp.home_team_id,
            away_team_id=inp.away_team_id,
            ordered_feature_names=inp.feature_keys,
            feature_values=inp.feature_values,
            feature_set_key=manifest.feature_set_key,
            feature_set_fingerprint=inp.feature_snapshot_fingerprint,
            dataset_release_key="mvp_a_historical:v1",
            dataset_release_fingerprint=EXPECTED_DATASET_FINGERPRINT,
            targets={},
        )
        c1 = self.c1.predict_raw(row)
        m1 = self.m1.predict_raw(row)
        evidence = m1.raw_outputs.get("evidence_quality", {})
        if "home_cold_start" not in evidence or "away_cold_start" not in evidence:
            raise ValueError("M1_TRAINING_HISTORY_EVIDENCE_MISSING")
        if m1.predicted_home_goals is None or m1.predicted_away_goals is None:
            raise ValueError("M1_LAMBDAS_MISSING")
        lh, la = float(m1.predicted_home_goals), float(m1.predicted_away_goals)
        comp = self.m1.artifact.weights_payload["competitions"][str(competition_id)]
        rho = float(comp.get("dixon_coles_rho", 0.0))
        matrix = self.distribution_engine.compute_score_matrix(lh, la, rho=rho)
        scorelines = self.distribution_engine.top_exact_scores(matrix, n=5)
        # The exact matrix hash is derived from the same full distribution as M1.
        import json
        matrix_fp = hashlib.sha256(json.dumps(matrix.matrix, sort_keys=True).encode("utf-8")).hexdigest()
        return FrozenInferenceOutput(
            c1_probabilities=(c1.prob_home_win, c1.prob_draw, c1.prob_away_win),
            m1_probabilities=(m1.prob_home_win, m1.prob_draw, m1.prob_away_win),
            lambda_home=lh,
            lambda_away=la,
            btts_yes=float(m1.prob_btts_yes),
            over_2_5=float(m1.prob_over_25),
            score_matrix=matrix.matrix,
            top_scorelines=scorelines,
            score_matrix_fingerprint=matrix_fp,
            c1_artifact_fingerprint=self.c1.artifact.artifact_fingerprint,
            m1_artifact_fingerprint=self.m1.artifact.artifact_fingerprint,
            c1_file_sha256=self.c1_file_sha256,
            m1_file_sha256=self.m1_file_sha256,
            home_cold_start=bool(evidence["home_cold_start"]),
            away_cold_start=bool(evidence["away_cold_start"]),
        )
