"""Build synthetic blind features and train the separated model pipeline."""

from __future__ import annotations

import json
import os
import pickle
from pathlib import Path
from typing import Any, Iterable

from bank.sensor import BlindSensor
from gen.generator import GeneratorConfig, generate
from mesh.chain import ChainLinker
from mesh.features import mesh_feature_vector


def split_rows_by_chain(rows: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Assign whole ground-truth chains to chronological 40/30/30 splits."""
    by_chain: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_chain.setdefault(row["chain_id"], []).append(row)
    ordered_chains = sorted(
        by_chain,
        key=lambda chain_id: min(row["ts_ms"] for row in by_chain[chain_id]),
    )
    count = len(ordered_chains)
    split_a_end = max(1, int(count * 0.4))
    split_b_end = max(split_a_end + 1, int(count * 0.7))
    split_b_end = min(split_b_end, count)
    selected = {
        "A": ordered_chains[:split_a_end],
        "B": ordered_chains[split_a_end:split_b_end],
        "test": ordered_chains[split_b_end:],
    }
    return {
        name: [row for chain_id in chain_ids for row in by_chain[chain_id]]
        for name, chain_ids in selected.items()
    }


def prepare_rows(
    raw_events: list[dict[str, Any]],
    ground_truth: list[dict[str, Any]],
    key: bytes,
) -> list[dict[str, Any]]:
    """Transform raw events locally, then join truth only in this harness."""
    truth_by_event = {row["event_id"]: row for row in ground_truth}
    sensors: dict[str, BlindSensor] = {}
    linker = ChainLinker()
    rows: list[dict[str, Any]] = []
    for raw in sorted(raw_events, key=lambda item: (item["ts_ms"], item["event_id"])):
        bank = raw["bank"]
        sensor = sensors.setdefault(bank, BlindSensor(key, bank))
        blind_message = sensor.process(raw)
        linked = linker.add(blind_message)
        truth = truth_by_event[raw["event_id"]]
        rows.append(
            {
                "event_id": raw["event_id"],
                "chain_id": truth["chain_id"],
                "ts_ms": raw["ts_ms"],
                "attack_type": truth["attack_type"],
                "is_attack": truth["is_attack"],
                "hop_index": truth["hop_index"],
                # Evaluation-only ground truth. This never enters the feature
                # vector or the bank-to-mesh message.
                "ground_truth_amount": float(truth["amount"]),
                "features": blind_message["f"] + mesh_feature_vector(linked),
                "blind_message": blind_message,
                "linked": linked,
            }
        )
    return rows


def _classifier_label(attack_type: str) -> str:
    if attack_type in {"CROSS_BANK_MULE_CASCADE", "ABORT_STORM"}:
        return attack_type
    return "NORMAL"


def train_and_save(
    config: GeneratorConfig | None = None,
    seed: int = 7,
    output_dir: str | Path = "models",
) -> dict[str, Any]:
    key_text = os.environ.get("CONSORTIUM_KEY")
    if not key_text:
        raise RuntimeError("CONSORTIUM_KEY must be provided through the environment")
    raw_events, ground_truth, _, _ = generate(config=config, seed=seed)
    rows = prepare_rows(raw_events, ground_truth, key_text.encode("utf-8"))
    splits = split_rows_by_chain(rows)

    split_b_chains = sorted(
        {row["chain_id"] for row in splits["B"]},
        key=lambda chain_id: min(row["ts_ms"] for row in splits["B"] if row["chain_id"] == chain_id),
    )
    validation_count = max(1, int(len(split_b_chains) * 0.2))
    validation_chains = set(split_b_chains[-validation_count:])
    classifier_rows = [row for row in splits["B"] if row["chain_id"] not in validation_chains]
    validation_rows = [row for row in splits["B"] if row["chain_id"] in validation_chains]
    if not classifier_rows or not validation_rows:
        raise ValueError("split B must have training and validation chains")

    normal_a = [
        row
        for row in splits["A"]
        if row["attack_type"] == "NORMAL" or row["attack_type"].startswith("HARD_NEG_")
    ]
    if not normal_a:
        raise ValueError("split A has no normal or hard-negative rows for anomaly training")

    from mesh.models import FraudModels

    model = FraudModels(random_state=seed).fit(
        [row["features"] for row in normal_a],
        [row["features"] for row in classifier_rows],
        [_classifier_label(row["attack_type"]) for row in classifier_rows],
        [row["features"] for row in validation_rows],
        [_classifier_label(row["attack_type"]) for row in validation_rows],
    )
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    model_path = target / "fraud_models.pkl"
    with model_path.open("wb") as stream:
        pickle.dump(model, stream, protocol=pickle.HIGHEST_PROTOCOL)
    model.save_thresholds(target / "thresholds.json")
    metadata = {
        "seed": seed,
        "split_rows": {name: len(value) for name, value in splits.items()},
        "classifier_training_rows": len(classifier_rows),
        "validation_rows": len(validation_rows),
        "model_path": str(model_path),
    }
    (target / "training.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return {"model": model, "rows": rows, "splits": splits, "metadata": metadata}


if __name__ == "__main__":
    train_and_save()
