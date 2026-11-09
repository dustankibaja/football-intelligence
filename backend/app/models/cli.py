"""
Developer CLI Interface for Phase 4 Modeling Dataset & Model Operations
Provides command-line tools to inspect modeling datasets, list registered models, train baseline models,
inspect trained artifacts, and execute raw pre-match predictions.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from uuid import UUID

# Ensure backend root is in sys.path when invoked directly
backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from app.db.session import SessionLocal
from app.models.base import TrainedModelArtifact
from app.models.builder import ModelingDatasetBuilder
from app.models.registry import global_model_registry


def dataset_show_command(args: argparse.Namespace) -> None:
    db = SessionLocal()
    try:
        builder = ModelingDatasetBuilder(dataset_key=args.dataset, version=args.version)
        manifest, rows = builder.build_modeling_dataset(db, limit=args.limit)

        print("==========================================================")
        print(f" MODELING DATASET SPECIFICATION: {manifest.modeling_dataset_key}")
        print("==========================================================")
        print(f"Version            : {manifest.version}")
        print(f"Historical Release : {manifest.dataset_release_key}")
        print(f"Release Checksum   : {manifest.dataset_release_fingerprint}")
        print(f"Feature Set        : {manifest.feature_set_key}")
        print(f"Feature Set Fingerprint: {manifest.feature_set_fingerprint}")
        print(f"Dataset Fingerprint: {manifest.fingerprint}")
        print(f"Eligible Row Count : {len(rows)}")
        print("-" * 58)
        print("Target Labels      :")
        for tk in manifest.target_keys:
            print(f"  - {tk}")
        print("Row Eligibility    : FINISHED matches with valid non-null scorelines")
        print("Chronology Rule    : kickoff_utc ASC, tie-broken by match_id ASC")
        print("==========================================================")
    finally:
        db.close()


def list_command(args: argparse.Namespace) -> None:
    models = global_model_registry.list_models()
    print("========================================================================================================================")
    print("                                            PHASE 4 MODEL REGISTRY")
    print("========================================================================================================================")
    print(f"{'Model Key':<28} | {'Family':<22} | {'Version':<8} | {'Lifecycle':<13} | {'Capabilities':<35}")
    print("-" * 120)
    for m in models:
        caps_str = ", ".join(m.get("capabilities", []))
        print(
            f"{m['model_key']:<28} | {m['model_family']:<22} | {m['version']:<8} | "
            f"{m['lifecycle_status']:<13} | {caps_str:<35}"
        )
    print("========================================================================================================================")


def train_command(args: argparse.Namespace) -> None:
    db = SessionLocal()
    try:
        model_cls = global_model_registry.get_model_class(args.model)
        model = model_cls()

        builder = ModelingDatasetBuilder(dataset_key=args.dataset, version=args.version)
        manifest, rows = builder.build_modeling_dataset(db, limit=args.limit)

        print("==========================================================")
        print(f" FITTING PHASE 4 MODEL: {args.model}")
        print("==========================================================")
        print(f"Training Dataset : {args.dataset}:{args.version} ({len(rows)} rows)")

        artifact = model.fit(rows)

        out_path = args.output or f"artifacts/models/{args.model}_artifact.json"
        model.save_artifact(out_path)

        print("\nTRAINING SUCCESSFUL:")
        print(f"Artifact Fingerprint : {artifact.artifact_fingerprint}")
        print(f"Preprocessor Finger  : {artifact.preprocessor_artifact.fingerprint}")
        print(f"Lifecycle Status     : {artifact.lifecycle_status}")
        print(f"Evaluation Status    : {artifact.evaluation_status}")
        print(f"Saved Artifact Path  : {out_path}")
        print("==========================================================")
    finally:
        db.close()


def predict_command(args: argparse.Namespace) -> None:
    db = SessionLocal()
    try:
        match_id = UUID(args.match_id)
        artifact_path = args.artifact

        if not os.path.exists(artifact_path):
            print(f"Error: Artifact file '{artifact_path}' not found.")
            sys.exit(1)

        with open(artifact_path, "r", encoding="utf-8") as f:
            art_data = json.load(f)

        m_key = art_data["model_key"]
        model_cls = global_model_registry.get_model_class(m_key)
        model = model_cls()
        model.load_artifact(artifact_path)

        # Build dataset single row
        builder = ModelingDatasetBuilder(dataset_key=args.dataset, version=args.version)
        manifest, rows = builder.build_modeling_dataset(db)

        target_row = next((r for r in rows if r.match_id == match_id), None)
        if not target_row:
            print(f"Error: Target match {match_id} not found inside dataset {args.dataset}:{args.version}.")
            sys.exit(1)

        # Route through ProbabilityEngine
        from app.models.router import ProbabilityEngine
        engine = ProbabilityEngine()
        prediction = engine.predict(model, target_row)

        print("==========================================================")
        print(f" RAW PRE-MATCH PREDICTION: {model.model_key}")
        print("==========================================================")
        print(f"Match ID             : {prediction.match_id}")
        print(f"Model Artifact FP    : {prediction.model_artifact_fingerprint}")
        print(f"Probability Status   : {prediction.probability_status}")
        print(f"Lifecycle Status     : {prediction.lifecycle_status}")
        print("-" * 58)
        if prediction.lambda_home is not None and prediction.lambda_away is not None:
            print(f"Expected Goals (H)   : {prediction.lambda_home:.4f}")
            print(f"Expected Goals (A)   : {prediction.lambda_away:.4f}")
            print("-" * 58)
        print(f"Prob Home Win (0)    : {prediction.one_x_two.home_win:.4f}")
        print(f"Prob Draw     (1)    : {prediction.one_x_two.draw:.4f}")
        print(f"Prob Away Win (2)    : {prediction.one_x_two.away_win:.4f}")
        if prediction.btts is not None:
            print(f"Prob BTTS Yes        : {prediction.btts.yes:.4f}")
            print(f"Prob BTTS No         : {prediction.btts.no:.4f}")
        if prediction.over_under_2_5 is not None:
            print(f"Prob Over 2.5        : {prediction.over_under_2_5.over:.4f}")
            print(f"Prob Under 2.5       : {prediction.over_under_2_5.under:.4f}")
        if prediction.most_likely_score is not None:
            mls = prediction.most_likely_score
            print(f"Most Likely Score    : {mls[0]}-{mls[1]}")
        if prediction.top_exact_scores:
            print("-" * 58)
            print("Top 5 Exact Scores   :")
            for s in prediction.top_exact_scores:
                print(f"  Rank {s['rank']}: {s['score_str']} -> {s['probability']:.4f}")
        print("==========================================================")
    finally:
        db.close()


def artifact_show_command(args: argparse.Namespace) -> None:
    if not os.path.exists(args.filepath):
        print(f"Error: Artifact file '{args.filepath}' not found.")
        sys.exit(1)

    with open(args.filepath, "r", encoding="utf-8") as f:
        art_data = json.load(f)

    art = TrainedModelArtifact.model_validate(art_data)

    print("==========================================================")
    print(f" TRAINED MODEL ARTIFACT INSPECTOR: {art.model_key}")
    print("==========================================================")
    print(f"Model Family       : {art.model_family}")
    print(f"Version            : {art.version}")
    print(f"Target Key         : {art.target_key}")
    print(f"Feature Set Key    : {art.feature_set_key}")
    print(f"Lifecycle Status   : {art.lifecycle_status}")
    print(f"Evaluation Status  : {art.evaluation_status}")
    print(f"Calibration Status : {art.calibration_status}")
    print(f"Artifact Fingerprint: {art.artifact_fingerprint}")
    print(f"Preprocessor FP    : {art.preprocessor_artifact.fingerprint}")
    print(f"Training Row Count : {art.training_window.row_count}")
    print("==========================================================")


def artifact_verify_command(args: argparse.Namespace) -> None:
    if not os.path.exists(args.filepath):
        print(f"Error: Artifact file '{args.filepath}' not found.")
        sys.exit(1)

    with open(args.filepath, "r", encoding="utf-8") as f:
        art_data = json.load(f)

    art = TrainedModelArtifact.model_validate(art_data)
    is_valid = art.verify_integrity()

    print("==========================================================")
    print(f" MODEL ARTIFACT INTEGRITY VERIFICATION: {art.model_key}")
    print("==========================================================")
    print(f"Stored Fingerprint   : {art.artifact_fingerprint}")
    print(f"Computed Fingerprint : {art.compute_fingerprint()}")
    print(f"Integrity Check      : {'VERIFIED / VALID' if is_valid else 'TAMPERED / INVALID'}")
    print("==========================================================")
    if not is_valid:
        sys.exit(1)


def evaluate_command(args: argparse.Namespace) -> None:
    from app.db.models import Season
    from app.models.evaluation import ModelEvaluator
    from app.models.splitting import TemporalSplitter
    from sqlalchemy import select

    db = SessionLocal()
    try:
        model_cls = global_model_registry.get_model_class(args.model)
        model = model_cls()

        builder = ModelingDatasetBuilder(dataset_key=args.dataset, version=args.version)
        manifest, rows = builder.build_modeling_dataset(db, limit=args.limit)

        seasons = db.scalars(select(Season)).all()
        s_map = {s.id: s.season_year for s in seasons}

        print("==========================================================")
        print(f" SCIENTIFIC MODEL EVALUATION: {args.model}")
        print("==========================================================")
        print(f"Dataset : {args.dataset}:{args.version} ({len(rows)} rows)")
        print(f"Strategy: {args.split_strategy.upper()}")

        if args.split_strategy == "rolling_origin":
            from datetime import timedelta
            from app.models.rolling_origin import (
                evaluate_rolling_origin,
                make_rolling_origin_folds,
            )

            kickoff_groups = len({row.kickoff_utc for row in rows})
            train_groups = max(20, int(kickoff_groups * 0.50))
            val_groups = max(5, int(kickoff_groups * 0.10))
            folds = make_rolling_origin_folds(
                rows,
                n_folds=args.folds,
                initial_train_groups=train_groups,
                validation_groups=val_groups,
                embargo=timedelta(hours=args.embargo_hours),
            )
            oos = evaluate_rolling_origin(folds, model_cls)
            metrics = oos.pooled_test
            print("\n--- ROLLING ORIGIN / OUT-OF-SAMPLE / SHADOW RESEARCH ---")
            print(f"Fold count          : {len(oos.per_fold)}")
            print(f"Unique OOS fixtures : {metrics.sample_size}")
            print(f"Pooled Log Loss     : {metrics.log_loss:.4f}")
            print(f"Pooled Brier Score  : {metrics.brier_score:.4f}")
            print(f"Pooled RPS          : {metrics.ranked_probability_score:.4f}")
            print(f"Pooled ECE          : {metrics.expected_calibration_error:.4f}")
            for idx, fold_metric in enumerate(oos.per_fold, start=1):
                print(
                    f"Fold {idx}: train={oos.train_counts[idx-1]}, "
                    f"val={oos.validation_counts[idx-1]}, "
                    f"test={oos.test_counts[idx-1]}, "
                    f"LL={fold_metric.log_loss:.4f}, "
                    f"Brier={fold_metric.brier_score:.4f}"
                )
            print("NON-PRODUCTION: do not publish or replace the champion.")
            if args.output:
                output = {
                    "evaluation_mode": oos.mode,
                    "model_key": oos.model_key,
                    "dataset_release": f"{args.dataset}:{args.version}",
                    "fold_count": len(oos.per_fold),
                    "embargo_hours": args.embargo_hours,
                    "pooled_test": metrics.model_dump(),
                    "folds": [
                        {
                            "fold": i + 1,
                            "train_count": oos.train_counts[i],
                            "validation_count": oos.validation_counts[i],
                            "test_count": oos.test_counts[i],
                            "metrics": metric.model_dump(),
                        }
                        for i, metric in enumerate(oos.per_fold)
                    ],
                    "tested_match_ids": list(oos.tested_match_ids),
                }
                os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
                with open(args.output, "w", encoding="utf-8") as out:
                    json.dump(output, out, indent=2, sort_keys=True)
                print(f"Saved independent research report to: {args.output}")
            return

        if args.split_strategy == "season_holdout":
            split_manifest, (train_rows, val_rows, test_rows) = TemporalSplitter.split_season_holdout(
                rows, season_id_to_year=s_map
            )
            model.fit(train_rows)
            report = ModelEvaluator.evaluate_model(model, test_rows, split_manifest=split_manifest)
        else:
            split_manifest, folds = TemporalSplitter.split_expanding_window(rows, n_splits=3)
            # Evaluate on the final fold for summary
            train_rows, val_rows, test_rows = folds[-1]
            model.fit(train_rows)
            report = ModelEvaluator.evaluate_model(model, test_rows, split_manifest=split_manifest)

        om = report.overall_metrics
        print("\n---------------- EVALUATION SCORECARD ----------------")
        print(f"Test Sample Size      : {om.sample_size}")
        print(f"Multiclass Log Loss   : {om.log_loss:.4f}")
        print(f"Multiclass Brier Score: {om.brier_score:.4f}")
        print(f"Ranked Prob Score(RPS): {om.ranked_probability_score:.4f}")
        print(f"  - Brier (Home Win)  : {om.brier_home:.4f}")
        print(f"  - Brier (Draw)      : {om.brier_draw:.4f}")
        print(f"  - Brier (Away Win)  : {om.brier_away:.4f}")
        print(f"Classification Acc    : {om.accuracy * 100:.2f}%")
        print(f"Balanced Accuracy     : {om.balanced_accuracy * 100:.2f}%")
        print(f"Exp. Calibration Error: {om.expected_calibration_error:.4f}")
        print(f"Mean Prediction Entropy: {om.mean_prediction_entropy:.4f} nats")
        print("-" * 54)
        print("Actual Distribution   :", om.actual_distribution)
        print("Predicted Distribution:", om.predicted_distribution)
        if report.warnings:
            print("-" * 54)
            print("Scientific Warnings   :")
            for w in report.warnings:
                print(f"  [!] {w}")

        if args.output:
            os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(report.model_dump_json(indent=2))
            print(f"\nSaved Full Evaluation Report to: {args.output}")
        print("==========================================================")
    finally:
        db.close()


def split_command(args: argparse.Namespace) -> None:
    from app.db.models import Season
    from app.models.splitting import TemporalSplitter
    from sqlalchemy import select

    db = SessionLocal()
    try:
        builder = ModelingDatasetBuilder(dataset_key=args.dataset, version=args.version)
        manifest, rows = builder.build_modeling_dataset(db, limit=args.limit)

        seasons = db.scalars(select(Season)).all()
        s_map = {s.id: s.season_year for s in seasons}

        if args.strategy == "season_holdout":
            split_manifest, (train_rows, val_rows, test_rows) = TemporalSplitter.split_season_holdout(
                rows, season_id_to_year=s_map
            )
        else:
            split_manifest, folds = TemporalSplitter.split_expanding_window(rows, n_splits=args.folds)

        print("==========================================================")
        print(f" TEMPORAL SPLIT MANIFEST: {split_manifest.strategy.value}")
        print("==========================================================")
        print(f"Manifest ID         : {split_manifest.manifest_id}")
        print(f"Split Fingerprint   : {split_manifest.config_fingerprint}")
        print(f"Total Rows Evaluated: {split_manifest.total_rows_evaluated}")
        print(f"Number of Folds     : {len(split_manifest.folds)}")
        print("-" * 58)
        for f in split_manifest.folds:
            print(f"Fold {f.fold_id}:")
            print(f"  Train: {f.train_count} rows ({f.train_start} to {f.train_end})")
            print(f"  Val  : {f.val_count} rows ({f.val_start} to {f.val_end})")
            print(f"  Test : {f.test_count} rows ({f.test_start} to {f.test_end})")
        print("==========================================================")
    finally:
        db.close()


def inspect_parameters_command(args: argparse.Namespace) -> None:
    if not os.path.exists(args.artifact):
        print(f"Error: Artifact file '{args.artifact}' not found.")
        sys.exit(1)

    with open(args.artifact, "r", encoding="utf-8") as f:
        art_data = json.load(f)

    art = TrainedModelArtifact.model_validate(art_data)
    print("==========================================================")
    print(f" LEARNED PARAMETER INSPECTION: {art.model_key}")
    print("==========================================================")
    print(f"Model Family       : {art.model_family}")
    print(f"Artifact Fingerprint: {art.artifact_fingerprint}")
    print(f"Training Rows      : {art.training_window.row_count}")

    weights = art.weights_payload
    comps = weights.get("competitions", {})
    if not comps:
        print("No competition-specific parameter payload found in artifact.")
        return

    for c_id, c_data in comps.items():
        print("-" * 58)
        print(f"COMPETITION: {c_id}")
        print(f"  Baseline (mu)     : {c_data.get('goal_baseline_mu', 'N/A')}")
        print(f"  Home Adv (h)      : {c_data.get('home_advantage_h', 'N/A')}")

        diag = c_data.get("diagnostics", {})
        print(f"  Convergence       : {diag.get('success', 'N/A')} (iters: {diag.get('iterations', 'N/A')}, obj: {diag.get('final_objective', 'N/A')})")
        print(f"  Identifiability   : alpha_res={diag.get('identifiability_residual_alpha', 0.0):.2e}, delta_res={diag.get('identifiability_residual_delta', 0.0):.2e}")

        att = c_data.get("team_attack_strengths", {})
        defense = c_data.get("team_defense_strengths", {})
        names = c_data.get("team_names", {})
        counts = c_data.get("team_match_counts", {})

        sorted_att = sorted(att.items(), key=lambda x: x[1], reverse=True)
        sorted_def = sorted(defense.items(), key=lambda x: x[1], reverse=True)

        print("\n  Top 3 Attacks (alpha > 0 scores more):")
        for tid, val in sorted_att[:3]:
            tname = names.get(tid, tid)
            print(f"    - {tname:<25}: {val:+.4f} (matches: {counts.get(tid, 0)})")

        print("\n  Weakest 3 Attacks (alpha < 0 scores less):")
        for tid, val in sorted_att[-3:]:
            tname = names.get(tid, tid)
            print(f"    - {tname:<25}: {val:+.4f} (matches: {counts.get(tid, 0)})")

        print("\n  Top 3 Defences (delta > 0 concedes fewer):")
        for tid, val in sorted_def[:3]:
            tname = names.get(tid, tid)
            print(f"    - {tname:<25}: {val:+.4f} (matches: {counts.get(tid, 0)})")

        print("\n  Weakest 3 Defences (delta < 0 concedes more):")
        for tid, val in sorted_def[-3:]:
            tname = names.get(tid, tid)
            print(f"    - {tname:<25}: {val:+.4f} (matches: {counts.get(tid, 0)})")
    print("==========================================================")


def main() -> None:
    parser = argparse.ArgumentParser(description="Football Intelligence System - Phase 4 Modeling CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # dataset show
    ds_parser = subparsers.add_parser("dataset", help="Inspect modeling dataset")
    ds_sub = ds_parser.add_subparsers(dest="subcommand", required=True)
    ds_show = ds_sub.add_parser("show", help="Show modeling dataset specification")
    ds_show.add_argument("--dataset", default="mvp_a_historical", help="Dataset key")
    ds_show.add_argument("--version", default="v1", help="Dataset version")
    ds_show.add_argument("--limit", type=int, help="Optional row limit")

    # list
    subparsers.add_parser("list", help="List registered Phase 4 prediction models")

    # train
    tr_parser = subparsers.add_parser("train", help="Fit a baseline prediction model")
    tr_parser.add_argument("--model", required=True, help="Model key (e.g. attack_defense_poisson_v1)")
    tr_parser.add_argument("--dataset", default="mvp_a_historical", help="Dataset key")
    tr_parser.add_argument("--version", default="v1", help="Dataset version")
    tr_parser.add_argument("--limit", type=int, help="Optional training row limit")
    tr_parser.add_argument("--output", help="Optional artifact output path")

    # evaluate
    ev_parser = subparsers.add_parser("evaluate", help="Evaluate a model using chronological split")
    ev_parser.add_argument("--model", required=True, help="Model key (e.g. class_prior_1x2_v1)")
    ev_parser.add_argument("--dataset", default="mvp_a_historical", help="Dataset key")
    ev_parser.add_argument("--version", default="v1", help="Dataset version")
    ev_parser.add_argument("--split-strategy", default="season_holdout", choices=["season_holdout", "expanding_window", "rolling_origin"], help="Temporal split strategy")
    ev_parser.add_argument("--folds", type=int, default=3, help="Rolling-origin out-of-sample folds")
    ev_parser.add_argument("--embargo-hours", type=float, default=3.0, help="Temporal embargo between folds, in hours")
    ev_parser.add_argument("--limit", type=int, help="Optional row limit")
    ev_parser.add_argument("--output", help="Optional output JSON report path")

    # split
    sp_parser = subparsers.add_parser("split", help="Generate and inspect temporal split manifest")
    sp_parser.add_argument("--strategy", default="season_holdout", choices=["season_holdout", "expanding_window"], help="Temporal split strategy")
    sp_parser.add_argument("--dataset", default="mvp_a_historical", help="Dataset key")
    sp_parser.add_argument("--version", default="v1", help="Dataset version")
    sp_parser.add_argument("--folds", type=int, default=3, help="Number of folds for expanding window")
    sp_parser.add_argument("--limit", type=int, help="Optional row limit")

    # predict
    pr_parser = subparsers.add_parser("predict", help="Generate raw pre-match prediction for a target match")
    pr_parser.add_argument("--artifact", required=True, help="Path to trained model artifact JSON")
    pr_parser.add_argument("--match-id", required=True, help="Target Match UUID")
    pr_parser.add_argument("--dataset", default="mvp_a_historical", help="Dataset key")
    pr_parser.add_argument("--version", default="v1", help="Dataset version")

    # artifact show / verify
    art_parser = subparsers.add_parser("artifact", help="Inspect trained model artifact metadata")
    art_sub = art_parser.add_subparsers(dest="subcommand", required=True)
    art_show = art_sub.add_parser("show", help="Show artifact metadata")
    art_show.add_argument("filepath", help="Path to trained model artifact JSON")
    art_ver = art_sub.add_parser("verify", help="Verify artifact integrity and checksum")
    art_ver.add_argument("filepath", help="Path to trained model artifact JSON")

    # inspect-parameters
    param_parser = subparsers.add_parser("inspect-parameters", help="Inspect learned team & competition parameters")
    param_parser.add_argument("--artifact", required=True, help="Path to trained model artifact JSON")

    args = parser.parse_args()

    if args.command == "dataset" and args.subcommand == "show":
        dataset_show_command(args)
    elif args.command == "list":
        list_command(args)
    elif args.command == "train":
        train_command(args)
    elif args.command == "evaluate":
        evaluate_command(args)
    elif args.command == "split":
        split_command(args)
    elif args.command == "predict":
        predict_command(args)
    elif args.command == "artifact" and args.subcommand == "show":
        artifact_show_command(args)
    elif args.command == "artifact" and args.subcommand == "verify":
        artifact_verify_command(args)
    elif args.command == "inspect-parameters":
        inspect_parameters_command(args)


if __name__ == "__main__":
    main()

