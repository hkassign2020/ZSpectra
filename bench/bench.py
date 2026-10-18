"""Evaluate detection, latency, feature ablations, and host zlib behavior."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import tempfile
import time
from typing import Any, Iterable
from unittest.mock import patch

import numpy as np
import requests
from sklearn.ensemble import HistGradientBoostingClassifier, IsolationForest

from audit import CanaryScanner
from gen.generator import GeneratorConfig
from mesh.service import create_app
from tools.heist import _read_accounts
from tools.platform_proof import collect as collect_platform
from train import _classifier_label, train_and_save
from bench.zlib_bench import run_zlib_bench


ABLATIONS = {
    "raw_only": tuple(range(5)) + tuple(range(8, 13)),
    "raw_plus_compression": tuple(range(13)),
    "compression_only": tuple(range(5, 8)) + tuple(range(8, 13)),
}
LOAD_SPEED_FACTORS = (1, 5, 10)


def _distribution(values: Iterable[float]) -> dict[str, float | int]:
    array = np.asarray(list(values), dtype=float)
    if array.ndim != 1 or len(array) == 0 or not np.isfinite(array).all():
        raise ValueError("a distribution requires finite observed values")
    p50, p95, p99 = np.percentile(array, [50, 95, 99])
    return {"p50": float(p50), "p95": float(p95), "p99": float(p99), "n": int(len(array))}


def _summary(values: Iterable[float]) -> dict[str, Any]:
    sample = [float(value) for value in values]
    if not sample or not np.isfinite(sample).all():
        raise ValueError("a summary requires finite observed values")
    return {
        "mean": float(statistics.mean(sample)),
        "std": float(statistics.pstdev(sample)),
        "per_seed": sample,
    }


def _render_readme_results(document: dict[str, Any]) -> str:
    """Create the short README snapshot directly from an evaluation result."""
    meta = document.get("meta", {})
    host = meta.get("host", {})
    seeds = ", ".join(str(seed) for seed in meta.get("seeds", [])) or "not recorded"
    lines = [f"- Evaluation host: `{host.get('arch', 'unknown')}`; seeds: {seeds}."]

    def add_summary(label: str, metrics: dict[str, Any] | None, scale: float = 1.0, unit: str = "") -> None:
        if not isinstance(metrics, dict) or not isinstance(metrics.get("mean"), (int, float)):
            return
        mean = float(metrics["mean"]) * scale
        std = float(metrics.get("std", 0.0)) * scale
        lines.append(f"- {label}: {mean:,.2f} ± {std:,.2f}{unit}.")

    detection = document.get("detection", {})
    add_summary(
        "Cascade recall (threshold calibrated to 1% validation FPR)",
        detection.get("recall_at_1pct_fpr"),
        100.0,
        "%",
    )
    add_summary("Observed held-out cascade FPR", detection.get("fpr_observed"), 100.0, "%")
    add_summary("Alert precision", detection.get("alert_precision"), 100.0, "%")
    add_summary("False-positive alert chains per seed", detection.get("false_positive_alert_chains"))
    add_summary("Alerted chains per seed", detection.get("alerted_chains"))
    add_summary("False-positive alert events per seed", detection.get("false_positive_alert_events"))
    add_summary("Cascades flagged at hop 2", detection.get("cascades_flagged_at_hop2_pct"), 1.0, "%")
    add_summary(
        "Slow-drip zero-day recall (Model 1 alone)",
        detection.get("zero_day_slow_drip_recall_at_1pct_fpr"),
        100.0,
        "%",
    )
    add_summary("Value still stoppable", detection.get("value_still_stoppable_pct"), 1.0, "%")
    add_summary("Time to flag from hop 1", detection.get("time_to_flag_ms"), 1.0, " ms")
    add_summary("Alert-to-next-hop gap", detection.get("alert_to_next_hop_gap_ms"), 1.0, " ms")

    load_test = document.get("load_test", {})
    for speed in ("1x", "5x", "10x"):
        distribution = load_test.get("decision_latency_ms", {}).get(speed)
        if isinstance(distribution, dict):
            lines.append(
                f"- Decision latency at {speed}: p50 {float(distribution['p50']):,.3f} ms, "
                f"p95 {float(distribution['p95']):,.3f} ms, p99 {float(distribution['p99']):,.3f} ms."
            )
        end_to_end = load_test.get("end_to_end_ms", {}).get(speed)
        if isinstance(end_to_end, dict):
            lines.append(
                f"- Hold-delivery latency at {speed}: p50 {float(end_to_end['p50']):,.3f} ms, "
                f"p95 {float(end_to_end['p95']):,.3f} ms, p99 {float(end_to_end['p99']):,.3f} ms."
            )

    for name, label in (
        ("raw_only", "Raw-only ablation recall"),
        ("raw_plus_compression", "Raw + compression ablation recall"),
        ("compression_only", "Compression-only ablation recall"),
    ):
        metric = document.get("ablation", {}).get(name, {}).get("recall_at_1pct_fpr")
        add_summary(label, metric, 100.0, "%")

    zlib = document.get("zlib_bench", {})
    if isinstance(zlib, dict):
        claim = zlib.get("hardware_acceleration_claimable") is True
        flag = "present" if zlib.get("dflt_flag") is True else "not observed"
        support = zlib.get("zlib_dfltcc_support")
        support_label = "yes" if support is True else "no" if support is False else "unknown"
        lines.append(
            f"- DFLTCC acceleration: {'verified by the configured comparison' if claim else 'not verified'} "
            f"(host flag: {flag}; zlib support: {support_label})."
        )
        lines.append(
            "- DFLTCC configured for the application runtime: "
            f"{'yes' if zlib.get('application_runtime_enabled') is True else 'no'} "
            f"(level mask: {zlib.get('application_runtime_level_mask') or 'not set'})."
        )
        for row in zlib.get("rows", []):
            if not isinstance(row, dict):
                continue
            size = row.get("buffer_bytes")
            on_us = row.get("hw_on_us")
            off_us = row.get("hw_off_us")
            baseline_us = row.get("baseline_us")
            if isinstance(on_us, (int, float)) and isinstance(off_us, (int, float)):
                lines.append(
                    f"- zlib level-six {size} byte toggle: on {on_us:,.2f} µs; off {off_us:,.2f} µs."
                )
            elif isinstance(baseline_us, (int, float)):
                lines.append(f"- zlib level-six {size} byte baseline: {baseline_us:,.2f} µs.")

    lines.append("All values above are copied from the accompanying `results.json`.")
    return "\n".join(lines)


def _update_readme_results(document: dict[str, Any], results_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    if results_path.resolve() != root / "results.json":
        return
    readme_path = root / "README.md"
    contents = readme_path.read_text(encoding="utf-8")
    start_marker = "<!-- EVAL-SUMMARY-START -->"
    end_marker = "<!-- EVAL-SUMMARY-END -->"
    start = contents.index(start_marker) + len(start_marker)
    end = contents.index(end_marker, start)
    updated = contents[:start] + "\n" + _render_readme_results(document) + "\n" + contents[end:]
    readme_path.write_text(updated, encoding="utf-8")


def _validation_chains(split_b: list[dict[str, Any]]) -> set[str]:
    first_seen: dict[str, int] = {}
    for row in split_b:
        chain = row["chain_id"]
        first_seen[chain] = min(first_seen.get(chain, row["ts_ms"]), row["ts_ms"])
    ordered = sorted(first_seen, key=first_seen.get)
    count = max(1, int(len(ordered) * 0.2))
    return set(ordered[-count:])


def _fit_ablation(
    splits: dict[str, list[dict[str, Any]]],
    validation_chains: set[str],
    indices: tuple[int, ...],
    seed: int,
) -> tuple[IsolationForest, HistGradientBoostingClassifier, np.ndarray, float]:
    normal_a = [row for row in splits["A"] if row["attack_type"] == "NORMAL" or row["attack_type"].startswith("HARD_NEG_")]
    classifier_rows = [row for row in splits["B"] if row["chain_id"] not in validation_chains]
    validation_rows = [row for row in splits["B"] if row["chain_id"] in validation_chains]
    if not normal_a or not classifier_rows or not validation_rows:
        raise ValueError("time split does not have enough rows for ablation training")

    def matrix(rows: list[dict[str, Any]]) -> np.ndarray:
        values = np.asarray([row["features"] for row in rows], dtype=float)
        return values[:, indices]

    x_a = matrix(normal_a)
    x_b = matrix(classifier_rows)
    x_valid = matrix(validation_rows)
    y_b = np.asarray([_classifier_label(row["attack_type"]) for row in classifier_rows], dtype=str)
    y_valid = np.asarray([_classifier_label(row["attack_type"]) for row in validation_rows], dtype=str)
    if not {"NORMAL", "CROSS_BANK_MULE_CASCADE", "ABORT_STORM"}.issubset(set(y_b)):
        raise ValueError("split B is missing a classifier class for ablation")

    anomaly_model = IsolationForest(
        n_estimators=100,
        contamination="auto",
        random_state=seed,
        n_jobs=1,
    ).fit(x_a)
    anomaly_reference = np.sort(-anomaly_model.decision_function(x_a))

    def anomaly_scores(features: np.ndarray) -> np.ndarray:
        scores = -anomaly_model.decision_function(features)
        return np.searchsorted(anomaly_reference, scores, side="right").astype(float) / len(anomaly_reference)

    anomaly_b = anomaly_scores(x_b)
    anomaly_valid = anomaly_scores(x_valid)
    classifier = HistGradientBoostingClassifier(
        max_iter=100,
        learning_rate=0.08,
        max_leaf_nodes=15,
        l2_regularization=1.0,
        random_state=seed,
    ).fit(np.column_stack((x_b, anomaly_b)), y_b)
    probabilities = classifier.predict_proba(np.column_stack((x_valid, anomaly_valid)))
    cascade_column = list(classifier.classes_).index("CROSS_BANK_MULE_CASCADE")
    normal_scores = probabilities[y_valid == "NORMAL", cascade_column]
    if not len(normal_scores):
        raise ValueError("ablation validation split has no normal events")
    try:
        threshold = float(np.quantile(normal_scores, 0.99, method="higher"))
    except TypeError:
        threshold = float(np.quantile(normal_scores, 0.99, interpolation="higher"))
    return anomaly_model, classifier, anomaly_reference, threshold


def _ablation_recall(
    fitted: tuple[IsolationForest, HistGradientBoostingClassifier, np.ndarray, float],
    rows: list[dict[str, Any]],
    indices: tuple[int, ...],
) -> float:
    anomaly_model, classifier, reference, threshold = fitted
    features = np.asarray([row["features"] for row in rows], dtype=float)[:, indices]
    scores = -anomaly_model.decision_function(features)
    anomaly = np.searchsorted(reference, scores, side="right").astype(float) / len(reference)
    probabilities = classifier.predict_proba(np.column_stack((features, anomaly)))
    cascade_column = list(classifier.classes_).index("CROSS_BANK_MULE_CASCADE")
    p_cascade = probabilities[:, cascade_column]
    positives = np.asarray([row["attack_type"] == "CROSS_BANK_MULE_CASCADE" for row in rows])
    if not positives.any():
        raise ValueError("test split contains no cascade rows for ablation")
    return float(np.mean(p_cascade[positives] >= threshold))


def _detection_metrics(model: Any, rows: list[dict[str, Any]]) -> dict[str, float]:
    scores = model.score([row["features"] for row in rows])
    cascade = np.asarray([row["attack_type"] == "CROSS_BANK_MULE_CASCADE" for row in rows])
    normal = np.asarray(
        [row["attack_type"] == "NORMAL" or row["attack_type"].startswith("HARD_NEG_") for row in rows]
    )
    slow = np.asarray([row["attack_type"] == "SLOW_DRIP_STRUCTURING" for row in rows])
    hop_two = np.asarray(
        [row["attack_type"] == "CROSS_BANK_MULE_CASCADE" and row["hop_index"] == 2 for row in rows]
    )
    cascade_scores = np.asarray([score["p_cascade"] for score in scores])
    anomaly_scores = np.asarray([score["anom"] for score in scores])
    cascade_predictions = cascade_scores >= model.thresholds["T_cascade"]
    anomaly_predictions = anomaly_scores >= model.thresholds["T_anom"]
    if not cascade.any() or not normal.any() or not slow.any() or not hop_two.any():
        raise ValueError("test split does not contain required positives and normal events")
    alerts = (
        cascade_predictions
        & np.asarray([row["linked"]["hop_depth"] >= 2 for row in rows])
    ) | anomaly_predictions
    predicted_chains: dict[str, dict[str, Any]] = {}
    for row, is_alert in zip(rows, alerts):
        if is_alert:
            predicted_chains.setdefault(row["linked"]["chain_id"], row)
    precision = float(
        sum(bool(row["is_attack"]) for row in predicted_chains.values())
        / max(1, len(predicted_chains))
    )
    cascade_outcomes = _cascade_outcome_metrics(rows, scores, model.thresholds)
    return {
        "recall_at_1pct_fpr": float(np.mean(cascade_predictions[cascade])),
        "fpr_observed": float(np.mean(cascade_predictions[normal])),
        "cascades_flagged_at_hop2_pct": 100.0 * float(np.mean(cascade_predictions[hop_two])),
        "zero_day_slow_drip_recall_at_1pct_fpr": float(np.mean(anomaly_predictions[slow])),
        "alert_precision": precision,
        "alerted_chains": float(len(predicted_chains)),
        "false_positive_alert_chains": float(
            sum(not bool(row["is_attack"]) for row in predicted_chains.values())
        ),
        "false_positive_alert_events": float(np.sum(alerts & normal)),
        "value_still_stoppable_pct": float(statistics.mean(cascade_outcomes["value_still_stoppable_pct"])),
        "time_to_flag_ms": float(statistics.mean(cascade_outcomes["time_to_flag_ms"])),
        "alert_to_next_hop_gap_ms": float(statistics.mean(cascade_outcomes["alert_to_next_hop_gap_ms"])),
    }


def _cascade_outcome_metrics(
    rows: list[dict[str, Any]],
    scores: list[dict[str, float]],
    thresholds: dict[str, float],
) -> dict[str, list[float]]:
    """Measure alert timing and remaining transfer value against held-out truth."""
    by_chain: dict[str, list[tuple[dict[str, Any], dict[str, float]]]] = {}
    for row, score in zip(rows, scores):
        if row["attack_type"] == "CROSS_BANK_MULE_CASCADE":
            by_chain.setdefault(row["chain_id"], []).append((row, score))

    outcomes: dict[str, list[float]] = {
        "value_still_stoppable_pct": [],
        "time_to_flag_ms": [],
        "alert_to_next_hop_gap_ms": [],
    }
    for chain in by_chain.values():
        chain.sort(key=lambda pair: (pair[0]["hop_index"], pair[0]["ts_ms"], pair[0]["event_id"]))
        hop_one = next((row for row, _score in chain if row["hop_index"] == 1), None)
        if hop_one is None:
            continue
        alert_rows = [
            row
            for row, score in chain
            if (
                row["linked"]["hop_depth"] >= 2
                and score["p_cascade"] >= thresholds["T_cascade"]
            )
            or score["anom"] >= thresholds["T_anom"]
        ]
        if not alert_rows:
            continue
        alert_row = min(alert_rows, key=lambda row: (row["ts_ms"], row["hop_index"], row["event_id"]))
        later_hops = [row for row, _score in chain if row["hop_index"] > alert_row["hop_index"]]
        first_amount = float(hop_one["ground_truth_amount"])
        if first_amount <= 0:
            continue
        remaining_value = sum(float(row["ground_truth_amount"]) for row in later_hops)
        outcomes["value_still_stoppable_pct"].append(100.0 * remaining_value / first_amount)
        outcomes["time_to_flag_ms"].append(float(alert_row["ts_ms"] - hop_one["ts_ms"]))
        if later_hops:
            next_hop = min(later_hops, key=lambda row: (row["ts_ms"], row["hop_index"], row["event_id"]))
            outcomes["alert_to_next_hop_gap_ms"].append(float(next_hop["ts_ms"] - alert_row["ts_ms"]))

    if not outcomes["value_still_stoppable_pct"] or not outcomes["time_to_flag_ms"]:
        raise ValueError("test split contains no flagged cascade chains for timing/value metrics")
    if not outcomes["alert_to_next_hop_gap_ms"]:
        raise ValueError("flagged test cascades have no later hop for gap measurement")
    return outcomes


def _decision_latencies(model: Any, rows: list[dict[str, Any]]) -> list[float]:
    ordered = sorted(rows, key=lambda row: (row["ts_ms"], row["event_id"]))
    app = create_app(models=model, bank_urls={}, scanner=CanaryScanner())
    client = app.test_client()
    latencies: list[float] = []
    observed_count = 0
    with patch.dict(os.environ, {"LLM_URL": "", "LLM_API_KEY": ""}):
        for row in ordered:
            response = client.post("/ingest", json=row["blind_message"])
            if response.status_code != 200:
                raise RuntimeError("in-process mesh rejected an evaluation event")
            measurements = client.get("/metrics").json["decision_latency_ms"]
            latencies.extend(float(value) for value in measurements[observed_count:])
            observed_count = len(measurements)
    if not latencies:
        raise RuntimeError("the evaluation split produced no alert decision latency samples")
    return latencies


def _host_metadata() -> dict[str, Any]:
    proof = collect_platform()
    host = proof.get("host", {}) if isinstance(proof, dict) else {}
    memory_gb = None
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                memory_gb = float(line.split()[1]) * 1024 / 1_000_000_000
                break
    except (OSError, ValueError, IndexError):
        pass
    try:
        git_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True, timeout=5
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        git_commit = None
    return {
        "git_commit": git_commit,
        "date": datetime.now(timezone.utc).isoformat(),
        "host": {
            "arch": host.get("architecture", platform.machine()) if isinstance(host, dict) else platform.machine(),
            "byte_order": host.get("byte_order", __import__("sys").byteorder) if isinstance(host, dict) else __import__("sys").byteorder,
            "model_name": host.get("model_name") if isinstance(host, dict) else None,
            "dflt_flag": host.get("dflt_flag") if isinstance(host, dict) else None,
            "cpus": os.cpu_count(),
            "mem_gb": memory_gb,
        },
    }


def _live_load_test(
    repeats: int,
    dashboard_url: str,
    replayer_url: str,
    token: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if not token:
        raise RuntimeError("REPLAY_TOKEN is required to measure live replay and hold delivery")
    if repeats < 1:
        raise ValueError("live replay repeat count must be positive")
    decision_latency_by_speed: dict[str, Any] = {}
    end_to_end_by_speed: dict[str, Any] = {}
    audit_before_control = {}
    negative_control_caught = False
    for speed_factor in LOAD_SPEED_FACTORS:
        decision_samples: list[float] = []
        end_to_end_samples: list[float] = []
        for _index in range(repeats):
            reset_response = requests.post(f"{dashboard_url}/api/reset", json={}, timeout=30)
            reset_response.raise_for_status()
            response = requests.post(
                f"{replayer_url}/benchmark/heist",
                headers={"X-Replay-Token": token},
                json={"speed_factor": speed_factor},
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()
            if not data.get("alert_received") or not data.get("hold_delivered"):
                raise RuntimeError("the live heist did not deliver its hop-two hold recommendation")
            end_to_end_samples.append(float(data["end_to_end_ms"]))
            metrics_response = requests.get(f"{dashboard_url}/api/metrics", timeout=15)
            metrics_response.raise_for_status()
            metric_values = metrics_response.json().get("decision_latency_ms", [])
            if not metric_values:
                raise RuntimeError("mesh did not record a decision latency for the live alert")
            decision_samples.extend(float(value) for value in metric_values)
        label = f"{speed_factor}x"
        decision_latency_by_speed[label] = _distribution(decision_samples)
        end_to_end_by_speed[label] = _distribution(end_to_end_samples)

    audit_response = requests.get(f"{dashboard_url}/api/audit", timeout=15)
    audit_response.raise_for_status()
    audit_before_control = audit_response.json()
    control_response = requests.post(f"{dashboard_url}/api/negative-control", json={}, timeout=15)
    control_response.raise_for_status()
    negative_control_caught = control_response.json().get("negative_control") == "caught"
    audit = {
        "messages_scanned": int(audit_before_control.get("messages_scanned", 0)),
        "bytes_scanned": int(audit_before_control.get("bytes_scanned", 0)),
        "canary_hits_real_run": int(audit_before_control.get("canary_hits", 0)),
        "validator_blocks": int(audit_before_control.get("validator_blocks", 0)),
        "negative_control_caught": negative_control_caught,
    }
    load_test = {
        "replay_speed_factors": list(LOAD_SPEED_FACTORS),
        "repeats_per_speed": repeats,
        "decision_latency_ms": decision_latency_by_speed,
        "end_to_end_ms": end_to_end_by_speed,
    }
    return end_to_end_by_speed["10x"], load_test, audit


def evaluate(
    *,
    seeds: tuple[int, ...] = (0, 1, 2),
    event_count: int = 20_000,
    output_path: str | Path = "results.json",
    live_repeats: int = 10,
) -> dict[str, Any]:
    if not seeds:
        raise ValueError("at least one evaluation seed is required")
    config = GeneratorConfig(event_count=event_count)
    per_seed: list[dict[str, float]] = []
    sensor_latencies: list[float] = []
    decision_latencies: list[float] = []
    ablation_values = {name: [] for name in ABLATIONS}

    key = os.environ.get("CONSORTIUM_KEY")
    if not key:
        raise RuntimeError("CONSORTIUM_KEY must be provided through the environment")
    with tempfile.TemporaryDirectory(prefix="zspectra-eval-") as temp_root:
        for seed in seeds:
            result = train_and_save(config=config, seed=seed, output_dir=Path(temp_root) / str(seed))
            splits = result["splits"]
            test_rows = sorted(splits["test"], key=lambda row: (row["ts_ms"], row["event_id"]))
            if not test_rows:
                raise ValueError("time split produced an empty test set")
            model = result["model"]
            per_seed.append(_detection_metrics(model, test_rows))
            sensor_latencies.extend(float(row["blind_message"]["sensor_us"]) for row in test_rows)
            decision_latencies.extend(_decision_latencies(model, test_rows))
            validation_chains = _validation_chains(splits["B"])
            for name, indices in ABLATIONS.items():
                fitted = _fit_ablation(splits, validation_chains, indices, seed)
                ablation_values[name].append(_ablation_recall(fitted, test_rows, indices))

    dashboard_url = os.environ.get("DASH_URL", "http://127.0.0.1:8080").rstrip("/")
    replayer_url = os.environ.get("REPLAYER_URL_HOST", "http://127.0.0.1:8081").rstrip("/")
    end_to_end, load_test, audit = _live_load_test(
        live_repeats,
        dashboard_url,
        replayer_url,
        os.environ.get("REPLAY_TOKEN", ""),
    )
    zlib_results = run_zlib_bench()
    zlib_results["application_runtime_enabled"] = os.environ.get("DFLTCC") == "1"
    zlib_results["application_runtime_level_mask"] = (
        os.environ.get("DFLTCC_LEVEL_MASK")
        if zlib_results["application_runtime_enabled"]
        else None
    )
    meta = _host_metadata()
    meta["seeds"] = list(seeds)
    detection = {
        "recall_at_1pct_fpr": _summary(item["recall_at_1pct_fpr"] for item in per_seed),
        "cascades_flagged_at_hop2_pct": _summary(item["cascades_flagged_at_hop2_pct"] for item in per_seed),
        "zero_day_slow_drip_recall_at_1pct_fpr": _summary(
            item["zero_day_slow_drip_recall_at_1pct_fpr"] for item in per_seed
        ),
        "fpr_observed": _summary(item["fpr_observed"] for item in per_seed),
        "alert_precision": _summary(item["alert_precision"] for item in per_seed),
        "alerted_chains": _summary(item["alerted_chains"] for item in per_seed),
        "false_positive_alert_chains": _summary(item["false_positive_alert_chains"] for item in per_seed),
        "false_positive_alert_events": _summary(item["false_positive_alert_events"] for item in per_seed),
        "value_still_stoppable_pct": _summary(item["value_still_stoppable_pct"] for item in per_seed),
        "time_to_flag_ms": _summary(item["time_to_flag_ms"] for item in per_seed),
        "alert_to_next_hop_gap_ms": _summary(item["alert_to_next_hop_gap_ms"] for item in per_seed),
    }
    result_document = {
        "meta": meta,
        "sensor_latency_us": _distribution(sensor_latencies),
        "decision_latency_ms": _distribution(decision_latencies),
        "end_to_end_ms": end_to_end,
        "load_test": load_test,
        "detection": detection,
        "ablation": {
            name: {"recall_at_1pct_fpr": _summary(values)}
            for name, values in ablation_values.items()
        },
        "zlib_bench": zlib_results,
        "audit": audit,
        "limitations": [
            "Synthetic data does not represent production banking traffic.",
            "Generated scenario labels and features come from an authored synthetic process; measured recall is benchmark-specific and does not establish production detection capability.",
            "Pseudonymization supports linkability; BIDs remain pseudonymous.",
            "Latency measurements use one host and are not a multi-region service-level result.",
            "A consortium key makes BIDs linkable by design.",
            "The digest-only canary scanner detects complete exact tokens; partial or altered canary text may not be detected.",
        ],
    }
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target.with_suffix(target.suffix + ".tmp")
    temp_path.write_text(json.dumps(result_document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp_path.replace(target)
    _update_readme_results(result_document, target)
    return result_document


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=int, default=20_000)
    parser.add_argument("--seeds", type=int, default=3)
    parser.add_argument("--live-repeats", type=int, default=10)
    parser.add_argument("--reduced", action="store_true", help="Use the reduced evaluation configuration")
    parser.add_argument("--output", default="results.json")
    args = parser.parse_args()
    event_count = 6_000 if args.reduced else args.events
    seeds = tuple(range(3 if args.reduced else args.seeds))
    result = evaluate(
        seeds=seeds,
        event_count=event_count,
        output_path=args.output,
        live_repeats=args.live_repeats,
    )
    print(json.dumps({"results_file": str(args.output), "seeds": result["meta"]["seeds"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
