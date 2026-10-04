#!/usr/bin/env python3
"""Train and benchmark a LightGBM fraud classifier on the Kaggle credit-card data."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import lightgbm as lgb
import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split


RANDOM_STATE = 42
TEST_SIZE = 0.2
VALIDATION_SIZE = 0.2
THROUGHPUT_BATCH_SIZE = 1_000
SINGLE_ROW_REPEATS = 1_000
THROUGHPUT_REPEATS = 10


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Train and benchmark a LightGBM credit-card fraud classifier."
    )
    parser.add_argument(
        "--data",
        type=Path,
        help="Path to creditcard.csv (auto-detected in common locations if omitted).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parent / "benchmark_result.json",
        help="JSON output path (default: benchmark_result.json beside this script).",
    )
    return parser.parse_args()


def find_dataset(explicit_path: Path | None) -> Path:
    if explicit_path is not None:
        dataset_path = explicit_path.expanduser().resolve()
        if not dataset_path.is_file():
            raise FileNotFoundError(f"Dataset file does not exist: {dataset_path}")
        return dataset_path

    search_directories = dict.fromkeys(
        (
            Path.cwd(),
            Path(__file__).resolve().parent,
            Path.home() / "ml-benchmark",
        )
    )
    candidates = [
        directory / "creditcard.csv"
        for directory in search_directories
        if (directory / "creditcard.csv").is_file()
    ]
    if len(candidates) == 1:
        return candidates[0].resolve()
    if len(candidates) > 1:
        paths = ", ".join(str(path) for path in candidates)
        raise ValueError(
            f"Found multiple creditcard.csv files ({paths}); specify one with --data."
        )

    locations = ", ".join(str(path) for path in search_directories)
    raise FileNotFoundError(
        "Could not find creditcard.csv. Download the Kaggle dataset or pass "
        f"--data /path/to/creditcard.csv. Searched: {locations}"
    )


def load_and_split_data(
    dataset_path: Path,
) -> tuple[
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.Series,
    pd.Series,
    pd.Series,
    float,
]:
    start_time = time.perf_counter()
    data = pd.read_csv(dataset_path)
    if "Class" not in data.columns:
        raise ValueError(
            f"Expected a 'Class' target column in {dataset_path}; "
            f"found columns: {', '.join(map(str, data.columns))}"
        )
    if data["Class"].nunique() != 2:
        raise ValueError("Expected the 'Class' column to contain both target classes.")

    features = data.drop(columns="Class")
    labels = data["Class"]
    train_validation_features, test_features, train_validation_labels, test_labels = (
        train_test_split(
            features,
            labels,
            test_size=TEST_SIZE,
            random_state=RANDOM_STATE,
            stratify=labels,
        )
    )
    train_features, validation_features, train_labels, validation_labels = (
        train_test_split(
            train_validation_features,
            train_validation_labels,
            test_size=VALIDATION_SIZE,
            random_state=RANDOM_STATE,
            stratify=train_validation_labels,
        )
    )
    load_data_seconds = time.perf_counter() - start_time

    return (
        train_features,
        validation_features,
        test_features,
        train_labels,
        validation_labels,
        test_labels,
        load_data_seconds,
    )


def run_benchmark(dataset_path: Path) -> dict[str, Any]:
    (
        train_features,
        validation_features,
        test_features,
        train_labels,
        validation_labels,
        test_labels,
        load_data_seconds,
    ) = load_and_split_data(dataset_path)

    if len(test_features) < THROUGHPUT_BATCH_SIZE:
        raise ValueError(
            f"The test split has {len(test_features)} rows; at least "
            f"{THROUGHPUT_BATCH_SIZE} are required to measure batch throughput."
        )

    model = lgb.LGBMClassifier(
        objective="binary",
        class_weight="balanced",
        n_estimators=500,
        learning_rate=0.05,
        num_leaves=31,
        random_state=RANDOM_STATE,
        n_jobs=-1,
        verbosity=-1,
    )
    training_start = time.perf_counter()
    model.fit(
        train_features,
        train_labels,
        eval_set=[(validation_features, validation_labels)],
        eval_metric="auc",
        callbacks=[lgb.early_stopping(stopping_rounds=30, verbose=False)],
    )
    training_seconds = time.perf_counter() - training_start

    probabilities = model.predict_proba(test_features)[:, 1]
    predictions = (probabilities >= 0.5).astype(int)
    metrics = {
        "auc_roc": float(roc_auc_score(test_labels, probabilities)),
        "accuracy": float(accuracy_score(test_labels, predictions)),
        "f1_score": float(f1_score(test_labels, predictions, zero_division=0)),
        "precision": float(
            precision_score(test_labels, predictions, zero_division=0)
        ),
        "recall": float(recall_score(test_labels, predictions, zero_division=0)),
    }

    single_row = test_features.iloc[[0]]
    model.predict_proba(single_row)
    single_row_start = time.perf_counter()
    for _ in range(SINGLE_ROW_REPEATS):
        model.predict_proba(single_row)
    inference_latency_seconds = (
        time.perf_counter() - single_row_start
    ) / SINGLE_ROW_REPEATS

    throughput_batch = test_features.iloc[:THROUGHPUT_BATCH_SIZE]
    model.predict_proba(throughput_batch)
    throughput_start = time.perf_counter()
    for _ in range(THROUGHPUT_REPEATS):
        model.predict_proba(throughput_batch)
    inference_1000_rows_seconds = (
        time.perf_counter() - throughput_start
    ) / THROUGHPUT_REPEATS
    inference_throughput_rows_per_second = (
        THROUGHPUT_BATCH_SIZE / inference_1000_rows_seconds
    )

    return {
        "dataset": str(dataset_path),
        "dataset_rows": len(train_features)
        + len(validation_features)
        + len(test_features),
        "feature_count": train_features.shape[1],
        "split_rows": {
            "train": len(train_features),
            "validation": len(validation_features),
            "test": len(test_features),
        },
        "load_data_seconds": load_data_seconds,
        "training_seconds": training_seconds,
        "best_iteration": int(model.best_iteration_),
        "metrics": metrics,
        "inference_latency_1_row_seconds": inference_latency_seconds,
        "inference_1000_rows_seconds": inference_1000_rows_seconds,
        "inference_throughput_rows_per_second": inference_throughput_rows_per_second,
    }


def main() -> None:
    args = parse_args()
    dataset_path = find_dataset(args.data)
    results = run_benchmark(dataset_path)

    output_path = args.output.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as result_file:
        json.dump(results, result_file, indent=2, allow_nan=False)
        result_file.write("\n")

    print(f"Benchmark results saved to: {output_path}")
    print(f"Load data: {results['load_data_seconds']:.4f} seconds")
    print(f"Training: {results['training_seconds']:.4f} seconds")
    print(f"Best iteration: {results['best_iteration']}")
    for metric_name, metric_value in results["metrics"].items():
        print(f"{metric_name}: {metric_value:.6f}")
    print(
        "Inference latency (1 row): "
        f"{results['inference_latency_1_row_seconds'] * 1_000:.4f} ms"
    )
    print(
        "Inference throughput (1,000 rows): "
        f"{results['inference_throughput_rows_per_second']:.2f} rows/second"
    )


if __name__ == "__main__":
    main()