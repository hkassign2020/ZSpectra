"""Prepare synthetic bank inputs, digest-only audit data, and local models."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from gen.generator import GeneratorConfig, generate
from tools.prepare_canary_index import write_digest_index
from train import train_and_save


DEMO_ACCOUNTS = {
    "A": {"source": "A:DEMO_SOURCE"},
    "B": {"relay": "B:DEMO_RELAY"},
    "C": {"receiver": "C:DEMO_RECEIVER"},
}


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def demo_ready(
    key_text: str,
    output_dir: str | Path = "demo-data",
    model_dir: str | Path = "models",
) -> bool:
    """Check that artifacts exist and were prepared for the current key."""
    if not key_text:
        return False
    output = Path(output_dir)
    models = Path(model_dir)
    required = (
        output / ".consortium-key.sha256",
        output / "canary-index.json",
        output / "events.jsonl",
        output / "heist-accounts.json",
        output / "accounts" / "accounts-A.json",
        output / "accounts" / "accounts-B.json",
        output / "accounts" / "accounts-C.json",
        models / "fraud_models.pkl",
        models / "thresholds.json",
    )
    if not all(path.is_file() for path in required):
        return False
    expected = hashlib.sha256(key_text.encode("utf-8")).hexdigest()
    try:
        stored = required[0].read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError):
        return False
    return stored == expected


def prepare_demo(
    output_dir: str | Path = "demo-data",
    model_dir: str | Path = "models",
    *,
    event_count: int = 60_000,
    seed: int = 7,
) -> dict[str, Any]:
    if not os.environ.get("CONSORTIUM_KEY"):
        raise RuntimeError("CONSORTIUM_KEY must be provided through the environment")
    output = Path(output_dir)
    models = Path(model_dir)
    config = GeneratorConfig(event_count=event_count)

    train_and_save(config=config, seed=seed, output_dir=models)
    events, _ground_truth, accounts_by_bank, canaries = generate(config=config, seed=seed)
    canaries.extend(
        [
            DEMO_ACCOUNTS["A"]["source"],
            DEMO_ACCOUNTS["B"]["relay"],
            DEMO_ACCOUNTS["C"]["receiver"],
            "SYNTHETIC DEMO CUSTOMER",
            "SYNTHETIC-DEMO-ID",
            "synthetic settlement",
            "synthetic cashout",
        ]
    )

    account_dir = output / "accounts"
    account_dir.mkdir(parents=True, exist_ok=True)
    for bank in ("A", "B", "C"):
        accounts = list(accounts_by_bank[bank])
        if bank == "A":
            accounts.append({"account": DEMO_ACCOUNTS[bank]["source"], "customer_name": "Synthetic Heist Source"})
        elif bank == "B":
            accounts.append({"account": DEMO_ACCOUNTS[bank]["relay"], "customer_name": "Synthetic Heist Relay"})
        else:
            accounts.append({"account": DEMO_ACCOUNTS[bank]["receiver"], "customer_name": "Synthetic Heist Receiver"})
        _write_json(account_dir / f"accounts-{bank}.json", accounts)

    _write_json(output / "heist-accounts.json", DEMO_ACCOUNTS)
    with (output / "events.jsonl").open("w", encoding="utf-8", newline="\n") as stream:
        for event in events:
            stream.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")
    _write_json(output / "canaries.json", canaries)
    write_digest_index(canaries, output / "canary-index.json")
    fingerprint = hashlib.sha256(os.environ["CONSORTIUM_KEY"].encode("utf-8")).hexdigest()
    (output / ".consortium-key.sha256").write_text(fingerprint + "\n", encoding="ascii")
    return {
        "event_count": len(events),
        "banks": ["A", "B", "C"],
        "model_dir": str(models),
        "data_dir": str(output),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=int, default=60_000)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--output-dir", default="demo-data")
    parser.add_argument("--model-dir", default="models")
    parser.add_argument("--check", action="store_true", help="check whether the prepared artifacts match the current key")
    args = parser.parse_args()
    if args.check:
        key_text = os.environ.get("CONSORTIUM_KEY", "")
        return 0 if demo_ready(key_text, args.output_dir, args.model_dir) else 1
    summary = prepare_demo(args.output_dir, args.model_dir, event_count=args.events, seed=args.seed)
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
