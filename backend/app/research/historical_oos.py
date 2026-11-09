"""Independent historical outcome research: real matches, causal features, sealed season test.

Downloads public Football-Data.co.uk scores; does not ingest odds, overwrite
Phase 4/5 frozen artifacts, or claim this challenger is a certified champion.
"""
from __future__ import annotations

import argparse
import csv
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, date, datetime, time as clock_time
import hashlib
import io
import json
import math
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5
import time
from urllib.request import Request, urlopen

import numpy as np
from scipy.optimize import minimize_scalar
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from app.models.evaluation import ModelEvaluator
from app.models.baselines.attack_defense_poisson import AttackDefensePoissonModel
from app.models.dataset_dto import ModelingRowDTO


LEAGUE_MATCH_COUNTS = {"E0": 380, "SP1": 380, "I1": 380, "D1": 306}
SEASONS = tuple(f"{year % 100:02d}{(year + 1) % 100:02d}" for year in range(2019, 2026))
TRAIN_SEASONS = frozenset(SEASONS[:5])
TUNE_SEASON = "2425"
TEST_SEASON = "2526"
BASE_URL = "https://www.football-data.co.uk/mmz4281"
USER_AGENT = "FootballIntelligenceResearch/1.0 (scientific reproducibility)"
MAX_BYTES = 3_000_000
FEATURE_COUNT = 20


@dataclass(frozen=True)
class Fixture:
    league: str
    season: str
    match_date: date
    home: str
    away: str
    home_goals: int
    away_goals: int
    outcome: int  # 0 home / 1 draw / 2 away


def parse_finished_csv(raw: bytes, league: str, season: str) -> list[Fixture]:
    """Parse ONLY matches with official full-time score and result.

    Missing completed-season results are NOT silently imputed.
    """
    if raw[:500].lower().lstrip().startswith((b"<html", b"<!doctype")):
        raise ValueError("HTML_NOT_MATCH_CSV")
    if not raw or len(raw) > MAX_BYTES:
        raise ValueError("INVALID_SOURCE_SIZE")
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig", errors="replace")))
    required = {"Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR"}
    if not reader.fieldnames or required - set(reader.fieldnames):
        raise ValueError("MISSING_REQUIRED_CSV_FIELDS")

    fixtures: list[Fixture] = []
    duplicates: set[tuple[str, str]] = set()
    for index, row in enumerate(reader, start=2):
        if not row or not any(str(v or "").strip() for v in row.values() if isinstance(v, str)):
            continue
        full_time_result = str(row.get("FTR") or "").strip().upper()
        # Only ignore genuinely blank rows; a finished season with an
        # incomplete match is an explicit failure, not an invented score.
        if not full_time_result:
            raise ValueError(f"MISSING_FINAL_RESULT_AT_ROW_{index}")
        home = str(row.get("HomeTeam") or "").strip()
        away = str(row.get("AwayTeam") or "").strip()
        if not home or not away or home == away:
            raise ValueError(f"INVALID_FIXTURE_AT_ROW_{index}")
        try:
            from datetime import datetime
            raw_date = str(row.get("Date") or "").strip()
            dt = None
            for fmt in ("%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d-%m-%y"):
                try:
                    dt = datetime.strptime(raw_date, fmt).date()
                    break
                except ValueError:
                    pass
            if dt is None:
                raise ValueError("unrecognized source date")
            goals_home = int(str(row.get("FTHG") or "").strip())
            goals_away = int(str(row.get("FTAG") or "").strip())
        except (ValueError, TypeError) as exc:
            raise ValueError(f"INVALID_DATE_OR_SCORE_AT_ROW_{index}") from exc
        if not (0 <= goals_home <= 20 and 0 <= goals_away <= 20):
            raise ValueError(f"UNREASONABLE_SCORE_AT_ROW_{index}")
        label = 0 if goals_home > goals_away else 1 if goals_home == goals_away else 2
        if full_time_result != ("H", "D", "A")[label]:
            raise ValueError(f"RESULT_SCORE_MISMATCH_AT_ROW_{index}")
        identity = (home, away)
        if identity in duplicates:
            raise ValueError(f"DUPLICATE_LEAGUE_SEASON_PAIRING_AT_ROW_{index}")
        duplicates.add(identity)
        fixtures.append(Fixture(league, season, dt, home, away, goals_home, goals_away, label))

    expected = LEAGUE_MATCH_COUNTS[league]
    if len(fixtures) != expected:
        raise ValueError(
            f"COMPLETED_SEASON_COVERAGE_FAILED {league}/{season}: "
            f"expected={expected}, observed={len(fixtures)}"
        )
    return fixtures


def download_completed_seasons(raw_dir: Path) -> tuple[list[Fixture], list[dict]]:
    """Use original source URLs, record every raw SHA-256 and reject partial coverage."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    output: list[Fixture] = []
    source_records = []
    for season in SEASONS:
        for league in LEAGUE_MATCH_COUNTS:
            url = f"{BASE_URL}/{season}/{league}.csv"
            path = raw_dir / f"{season}_{league}.csv"
            if path.exists():
                raw = path.read_bytes()
            else:
                last_error = None
                for attempt in range(3):
                    try:
                        request = Request(url, headers={"User-Agent": USER_AGENT})
                        with urlopen(request, timeout=30) as response:
                            content_type = response.headers.get("Content-Type", "")
                            raw = response.read(MAX_BYTES + 1)
                        if len(raw) > MAX_BYTES or "text/html" in content_type:
                            raise ValueError("PROVIDER_RETURNED_INVALID_CONTENT")
                        break
                    except Exception as exc:
                        last_error = exc
                        if attempt < 2:
                            time.sleep(1.0 + attempt)
                else:
                    raise RuntimeError(f"HISTORICAL_SOURCE_UNAVAILABLE {url}: {last_error}")
                # Parse BEFORE caching; never save invalid/html as a data source.
                parsed = parse_finished_csv(raw, league, season)
                path.write_bytes(raw)
                time.sleep(0.35)
            parsed = parse_finished_csv(raw, league, season)
            output.extend(parsed)
            source_records.append({
                "league": league,
                "season": season,
                "url": url,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "bytes": len(raw),
                "finished_matches": len(parsed),
            })
    return output, source_records


def _average(history: deque, field: int, default: float) -> float:
    return sum(item[field] for item in history) / len(history) if history else default


def build_pre_match_features(fixtures: list[Fixture]) -> tuple[np.ndarray, np.ndarray]:
    """Only matches from strictly EARLIER CALENDAR DATES inform any feature.

    The provider's Date column may lack precise kickoff time, so all games on a
    calendar date are scored before that date is added to history.
    """
    histories: dict[tuple[str, str], deque] = defaultdict(lambda: deque(maxlen=10))
    venues: dict[tuple[str, str, str], deque] = defaultdict(lambda: deque(maxlen=5))
    league_totals: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
    ordered = sorted(
        enumerate(fixtures),
        key=lambda item: (item[1].match_date, item[1].league, item[1].home, item[1].away),
    )
    matrix: list[list[float] | None] = [None] * len(fixtures)
    labels = np.array([match.outcome for match in fixtures], dtype=np.int64)
    cursor = 0
    while cursor < len(ordered):
        day = ordered[cursor][1].match_date
        next_cursor = cursor
        while next_cursor < len(ordered) and ordered[next_cursor][1].match_date == day:
            next_cursor += 1
        daily = ordered[cursor:next_cursor]

        for row_index, match in daily:
            home_hist = histories[(match.league, match.home)]
            away_hist = histories[(match.league, match.away)]
            home_venue = venues[(match.league, match.home, "home")]
            away_venue = venues[(match.league, match.away, "away")]
            count, home_sum, away_sum = league_totals[match.league]
            league_h = (home_sum + 30 * 1.35) / (count + 30)
            league_a = (away_sum + 30 * 1.10) / (count + 30)

            def rest_days(history: deque) -> float:
                return min(30.0, float((day - history[-1][3]).days)) if history else 14.0

            def recent(history: deque) -> list:
                return list(history)[-5:]

            hh, aa = recent(home_hist), recent(away_hist)
            h_venue, a_venue = recent(home_venue), recent(away_venue)
            features = [
                league_h, league_a,
                _average(hh, 2, 1.3), _average(aa, 2, 1.3),
                _average(hh, 0, league_h), _average(hh, 1, league_a),
                _average(aa, 0, league_a), _average(aa, 1, league_h),
                _average(h_venue, 0, league_h), _average(h_venue, 1, league_a),
                _average(a_venue, 0, league_a), _average(a_venue, 1, league_h),
                rest_days(home_hist), rest_days(away_hist),
                min(20.0, float(len(home_hist))), min(20.0, float(len(away_hist))),
                *[float(match.league == key) for key in LEAGUE_MATCH_COUNTS],
            ]
            if len(features) != FEATURE_COUNT:
                raise AssertionError("FEATURE_SCHEMA_DRIFT")
            matrix[row_index] = features

        for _, match in daily:
            league_totals[match.league][0] += 1
            league_totals[match.league][1] += match.home_goals
            league_totals[match.league][2] += match.away_goals
            home_pts = 3 if match.outcome == 0 else 1 if match.outcome == 1 else 0
            away_pts = 3 if match.outcome == 2 else 1 if match.outcome == 1 else 0
            h = (match.home_goals, match.away_goals, home_pts, day)
            a = (match.away_goals, match.home_goals, away_pts, day)
            histories[(match.league, match.home)].append(h)
            histories[(match.league, match.away)].append(a)
            venues[(match.league, match.home, "home")].append(h)
            venues[(match.league, match.away, "away")].append(a)
        cursor = next_cursor
    result = np.asarray(matrix, dtype=np.float64)
    if result.shape != (len(fixtures), FEATURE_COUNT) or not np.isfinite(result).all():
        raise ValueError("NONFINITE_OR_MISSING_CAUSAL_FEATURE")
    return result, labels


RESEARCH_FEATURE_NAMES = (
    "league_home_goals_mean", "league_away_goals_mean",
    "home_recent_points", "away_recent_points",
    "home_recent_scored", "home_recent_allowed",
    "away_recent_scored", "away_recent_allowed",
    "home_venue_scored", "home_venue_allowed",
    "away_venue_scored", "away_venue_allowed",
    "home_rest_days", "away_rest_days",
    "home_observed_matches", "away_observed_matches",
    "league_is_E0", "league_is_SP1", "league_is_I1", "league_is_D1",
)
STRUCTURAL_CANDIDATES = ("M1_ATTACK_DEFENSE", "M1_DECAY_365", "M3_DIXON_COLES")


def _research_modeling_rows(
    fixtures: list[Fixture], features: np.ndarray, source_manifest: list[dict],
) -> list[ModelingRowDTO]:
    """Use *research-only* team and league IDs; never impersonate canonical Phase 6."""
    release_hash = hashlib.sha256(
        json.dumps(source_manifest, sort_keys=True).encode("utf-8")
    ).hexdigest()
    result = []
    for match, vector in zip(fixtures, features):
        match_token = (
            f"{match.league}:{match.season}:{match.match_date.isoformat()}:"
            f"{match.home}:{match.away}"
        )
        vector_hash = hashlib.sha256(
            f"{match_token}:{vector.astype(float).tolist()}".encode("utf-8")
        ).hexdigest()
        result.append(ModelingRowDTO(
            match_id=uuid5(NAMESPACE_URL, f"historical-outcomes/match/{match_token}"),
            competition_id=uuid5(NAMESPACE_URL, f"historical-outcomes/league/{match.league}"),
            season_id=uuid5(NAMESPACE_URL, f"historical-outcomes/season/{match.league}/{match.season}"),
            # Original CSV has only reliable date precision. Noon is a DTO
            # placeholder, NOT a certified timestamp or live kickoff record.
            kickoff_utc=datetime.combine(match.match_date, clock_time(12, 0), tzinfo=UTC),
            temporal_precision="DATE_ONLY",
            home_team_id=uuid5(NAMESPACE_URL, f"historical-outcomes/team/{match.league}/{match.home}"),
            away_team_id=uuid5(NAMESPACE_URL, f"historical-outcomes/team/{match.league}/{match.away}"),
            home_team_name=match.home,
            away_team_name=match.away,
            dataset_release_key="football_data_research_2019_2026:v1",
            dataset_release_fingerprint=release_hash,
            feature_set_key="research_outcomes_20_v1",
            feature_set_fingerprint=vector_hash,
            ordered_feature_names=list(RESEARCH_FEATURE_NAMES),
            feature_values=vector.astype(float).tolist(),
            targets={
                "home_goals_v1": match.home_goals,
                "away_goals_v1": match.away_goals,
                "match_result_1x2_v1": match.outcome,
            },
        ))
    return result


def _structural_model(name: str) -> AttackDefensePoissonModel:
    if name == "M1_ATTACK_DEFENSE":
        result = AttackDefensePoissonModel(regularization_gamma=0.2)
    elif name == "M1_DECAY_365":
        result = AttackDefensePoissonModel(
            regularization_gamma=0.2, time_decay_half_life_days=365.0
        )
    elif name == "M3_DIXON_COLES":
        result = AttackDefensePoissonModel(
            regularization_gamma=0.2, optimize_rho=True
        )
    else:
        raise ValueError("UNREGISTERED_STRUCTURAL_CANDIDATE")
    result.feature_set_key = "research_outcomes_20_v1"
    return result


def _structural_probabilities(
    model: AttackDefensePoissonModel,
    rows: list[ModelingRowDTO],
    indices: list[int],
) -> np.ndarray:
    forecast = []
    for index in indices:
        # Target labels are excluded from the scored research DTO.
        row = rows[index].model_copy(update={"targets": {}})
        p = model.predict_raw(row)
        forecast.append([p.prob_home_win, p.prob_draw, p.prob_away_win])
    result = np.asarray(forecast, dtype=float)
    if (
        result.shape != (len(indices), 3)
        or not np.isfinite(result).all()
        or not np.allclose(result.sum(axis=1), 1, atol=1e-4)
    ):
        raise ValueError("INVALID_STRUCTURAL_PROBABILITY_OUTPUT")
    return result


def _model(name: str):
    if name.startswith("L2_"):
        c = float(name.split("_")[1])
        return make_pipeline(StandardScaler(), LogisticRegression(C=c, max_iter=2000))
    if name == "HGB_120":
        return HistGradientBoostingClassifier(
            max_iter=120, max_leaf_nodes=11, min_samples_leaf=45,
            l2_regularization=10.0, learning_rate=0.045, random_state=42,
        )
    raise ValueError("UNREGISTERED_RESEARCH_CANDIDATE")


def _probabilities(model, matrix: np.ndarray) -> np.ndarray:
    values = np.asarray(model.predict_proba(matrix), dtype=float)
    classes = model[-1].classes_ if hasattr(model, "steps") else model.classes_
    if not np.array_equal(classes, [0, 1, 2]):
        raise ValueError("THREE_CLASS_PROBABILITY_CONTRACT_BROKEN")
    if not np.isfinite(values).all() or not np.allclose(values.sum(axis=1), 1, atol=1e-6):
        raise ValueError("INVALID_MODEL_PROBABILITIES")
    return values


def _log_loss(labels: np.ndarray, probabilities: np.ndarray) -> float:
    probs = np.clip(probabilities, 1e-12, 1)
    return float(-np.mean(np.log(probs[np.arange(len(labels)), labels])))


def _temperature(probabilities: np.ndarray, temperature: float) -> np.ndarray:
    logits = np.log(np.clip(probabilities, 1e-12, 1.0)) / temperature
    logits -= logits.max(axis=1, keepdims=True)
    exp = np.exp(logits)
    return exp / exp.sum(axis=1, keepdims=True)


def _metric_summary(labels: np.ndarray, probabilities: np.ndarray) -> dict:
    return ModelEvaluator.compute_metric_summary(
        labels.astype(int).tolist(),
        probabilities.astype(float).tolist(),
    ).model_dump()


def evaluate_real_historical(
    fixtures: list[Fixture], source_manifest: list[dict],
) -> dict:
    """Pre-register chronological split & model policy; evaluate once on sealed 2526."""
    expected_total = len(SEASONS) * sum(LEAGUE_MATCH_COUNTS.values())
    if len(fixtures) != expected_total:
        raise ValueError("INCOMPLETE_EXPANDED_HISTORICAL_COVERAGE")
    if len(source_manifest) != len(SEASONS) * len(LEAGUE_MATCH_COUNTS):
        raise ValueError("RAW_SHA256_MANIFEST_INCOMPLETE")

    x, y = build_pre_match_features(fixtures)
    train_ids = [i for i, f in enumerate(fixtures) if f.season in TRAIN_SEASONS]
    validation = [i for i, f in enumerate(fixtures) if f.season == TUNE_SEASON]
    test_ids = [i for i, f in enumerate(fixtures) if f.season == TEST_SEASON]
    validation_dates = sorted({fixtures[i].match_date for i in validation})
    boundary = validation_dates[len(validation_dates) // 2]
    tune_ids = [i for i in validation if fixtures[i].match_date < boundary]
    calibration_ids = [i for i in validation if fixtures[i].match_date >= boundary]
    if any(len(group) < 300 for group in (tune_ids, calibration_ids, test_ids)):
        raise ValueError("INSUFFICIENT_VALIDATION_OR_HOLDOUT_SAMPLE")
    if max(fixtures[i].match_date for i in train_ids) >= min(fixtures[i].match_date for i in tune_ids):
        raise ValueError("TRAIN_TUNING_TIME_LEAKAGE")
    if max(fixtures[i].match_date for i in tune_ids) >= min(fixtures[i].match_date for i in calibration_ids):
        raise ValueError("TUNING_CALIBRATION_TIME_LEAKAGE")
    if max(fixtures[i].match_date for i in calibration_ids) >= min(fixtures[i].match_date for i in test_ids):
        raise ValueError("CALIBRATION_HOLDOUT_TIME_LEAKAGE")

    # All candidate choices and hyperparameters are fixed before opening test.
    candidates = [
        "L2_0.1", "L2_0.5", "L2_1.0", "L2_3.0", "HGB_120",
        *STRUCTURAL_CANDIDATES,
    ]
    scores = {}
    fitted = {}
    structural_rows = _research_modeling_rows(fixtures, x, source_manifest)
    for name in candidates:
        if name in STRUCTURAL_CANDIDATES:
            candidate = _structural_model(name)
            candidate.fit([structural_rows[i] for i in train_ids])
            # A structural fit that did not converge is not a candidate.
            diagnostics = (
                candidate.artifact.weights_payload.get("competitions", {})
            )
            if len(diagnostics) != len(LEAGUE_MATCH_COUNTS) or not all(
                c.get("diagnostics", {}).get("success") for c in diagnostics.values()
            ):
                raise ValueError(f"STRUCTURAL_CHALLENGER_NONCONVERGENCE: {name}")
            candidate_probs = _structural_probabilities(candidate, structural_rows, tune_ids)
        else:
            candidate = _model(name)
            candidate.fit(x[train_ids], y[train_ids])
            candidate_probs = _probabilities(candidate, x[tune_ids])
        scores[name] = _log_loss(y[tune_ids], candidate_probs)
        fitted[name] = candidate

    champion_name = min(scores, key=lambda key: (scores[key], key))
    selected = fitted[champion_name]

    def score_selected(indices: list[int]) -> np.ndarray:
        if champion_name in STRUCTURAL_CANDIDATES:
            return _structural_probabilities(selected, structural_rows, indices)
        return _probabilities(selected, x[indices])

    # Fit ONLY a scalar temperature on the later, disjoint calibration block.
    calibration_probs = score_selected(calibration_ids)
    optimization = minimize_scalar(
        lambda t: _log_loss(y[calibration_ids], _temperature(calibration_probs, t)),
        method="bounded", bounds=(0.5, 3.0), options={"xatol": 1e-5},
    )
    if not optimization.success:
        raise ValueError("CALIBRATION_OPTIMIZER_FAILED")
    temperature = float(optimization.x)
    calibrated_validation = _temperature(calibration_probs, temperature)

    # A confidence policy is chosen before opening 2025/26 outcomes.
    # This is a selection/abstention policy, not a betting return strategy.
    thresholds = [0.50, 0.55, 0.60, 0.65]
    viable = []
    for threshold in thresholds:
        chosen = calibrated_validation.max(axis=1) >= threshold
        if chosen.sum() < 100 or chosen.mean() < 0.1:
            continue
        conditional_accuracy = float(
            np.mean(np.argmax(calibrated_validation[chosen], axis=1) == y[calibration_ids][chosen])
        )
        overall_accuracy = float(
            np.mean(np.argmax(calibrated_validation, axis=1) == y[calibration_ids])
        )
        if conditional_accuracy > overall_accuracy:
            viable.append((conditional_accuracy, threshold))
    selected_threshold = sorted(viable, key=lambda item: (-item[0], item[1]))[0][1] if viable else None

    test_probs_raw = score_selected(test_ids)
    test_probs_cal = _temperature(test_probs_raw, temperature)
    class_counts = np.bincount(y[train_ids], minlength=3)
    class_prior = class_counts / class_counts.sum()
    baseline_probs = np.tile(class_prior, (len(test_ids), 1))

    by_league = {}
    for league in LEAGUE_MATCH_COUNTS:
        league_indices = [j for j, k in enumerate(test_ids) if fixtures[k].league == league]
        by_league[league] = _metric_summary(
            y[test_ids][league_indices], test_probs_cal[league_indices],
        )

    selected_count = 0
    selected_accuracy = None
    if selected_threshold is not None:
        mask = test_probs_cal.max(axis=1) >= selected_threshold
        selected_count = int(mask.sum())
        if selected_count:
            selected_accuracy = float(
                np.mean(np.argmax(test_probs_cal[mask], axis=1) == y[test_ids][mask])
            )

    return {
        "schema_version": "independent_research_1.1.0",
        "status": "OUT_OF_SAMPLE_RESEARCH_NOT_CERTIFIED_FOR_PUBLICATION",
        "research_note": (
            "Independent causal outcome-only challenger; does not use the 251-feature "
            "Phase 4/5 certified model, sportsbook odds, or historical holdout for training."
        ),
        "source": "Football-Data.co.uk public CSV, attribution retained",
        "source_manifest": source_manifest,
        "coverage": {
            "seasons": list(SEASONS),
            "competitions": list(LEAGUE_MATCH_COUNTS),
            "total_finished_fixtures": len(fixtures),
            "train": len(train_ids),
            "tuning": len(tune_ids),
            "calibration": len(calibration_ids),
            "sealed_test_2025_26": len(test_ids),
            "date_only_conservative_features": True,
        },
        "selection": {
            "training_seasons": sorted(TRAIN_SEASONS),
            "tuning_season": TUNE_SEASON,
            "calibration_season": TUNE_SEASON,
            "test_season": TEST_SEASON,
            "validation_split_date": boundary.isoformat(),
            "candidate_tuning_log_loss": scores,
            "selected_challenger": champion_name,
            "calibration_temperature": temperature,
        },
        "heldout_metrics": {
            "training_only_class_prior": _metric_summary(y[test_ids], baseline_probs),
            "challenger_raw": _metric_summary(y[test_ids], test_probs_raw),
            "challenger_temperature_calibrated": _metric_summary(y[test_ids], test_probs_cal),
            "per_league_calibrated": by_league,
        },
        "selective_1x2": {
            "confidence_policy": "max calibrated probability above fixed validation-chosen cutoff",
            "threshold_chosen_on_calibration_only": selected_threshold,
            "test_selected": selected_count,
            "test_coverage": selected_count / len(test_ids),
            "test_conditional_accuracy": selected_accuracy,
            "profit_or_betting_return_claimed": False,
        },
        "limitations": [
            "Historical source rows are fetched independently and not canonicalized to the approved Phase 6 251-feature ledger.",
            "Team names are source aliases; name changes may segment longitudinal team history.",
            "Source date precision is day-only; no same-day result contributes to any same-day feature.",
            "Temperature and abstention threshold use the same calibration block; final sealed test is untouched.",
            "Calibration evidence needs prospective shadow testing and independent recalibration validation.",
            "This report cannot determine whether the challenger beats the frozen certified C1 champion.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-dir", default="data/research_football_data_raw")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    fixtures, sources = download_completed_seasons(Path(args.raw_dir))
    report = evaluate_real_historical(fixtures, sources)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "fixtures": report["coverage"]["total_finished_fixtures"],
        "holdout": report["coverage"]["sealed_test_2025_26"],
        "selected": report["selection"]["selected_challenger"],
        "baseline_log_loss": report["heldout_metrics"]["training_only_class_prior"]["log_loss"],
        "baseline_brier": report["heldout_metrics"]["training_only_class_prior"]["brier_score"],
        "raw_model_log_loss": report["heldout_metrics"]["challenger_raw"]["log_loss"],
        "raw_model_brier": report["heldout_metrics"]["challenger_raw"]["brier_score"],
        "challenger_log_loss": report["heldout_metrics"]["challenger_temperature_calibrated"]["log_loss"],
        "challenger_brier": report["heldout_metrics"]["challenger_temperature_calibrated"]["brier_score"],
        "challenger_ece": report["heldout_metrics"]["challenger_temperature_calibrated"]["expected_calibration_error"],
        "challenger_accuracy": report["heldout_metrics"]["challenger_temperature_calibrated"]["accuracy"],
        "calibration_temperature": report["selection"]["calibration_temperature"],
        "per_league_log_loss": {
            code: metrics["log_loss"]
            for code, metrics in report["heldout_metrics"]["per_league_calibrated"].items()
        },
        "selective_1x2": report["selective_1x2"],
        "champion_compared": False,
        "report": str(output),
    }, indent=2))


if __name__ == "__main__":
    main()
