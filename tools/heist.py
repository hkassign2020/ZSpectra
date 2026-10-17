"""Drive the deterministic synthetic cascade through the bank HTTP APIs."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any, Callable

import requests


def build_heist_events(accounts: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    """Create a three-bank synthetic cascade with a hop-two hold target."""
    try:
        source_a = accounts["A"]["source"]
        relay_b = accounts["B"]["relay"]
        receiver_c = accounts["C"]["receiver"]
    except (KeyError, TypeError) as exc:
        raise ValueError("heist accounts must define A.source, B.relay, and C.receiver") from exc
    if not all(type(value) is str and value for value in (source_a, relay_b, receiver_c)):
        raise ValueError("heist account identifiers must be non-empty strings")

    common = {
        "channel": "API",
        "aborted_retries_last_5s": 0,
        "customer_name": "SYNTHETIC DEMO CUSTOMER",
        "national_id": "SYNTHETIC-DEMO-ID",
    }
    return [
        {
            **common,
            "event_id": "HEIST-A-TO-B",
            "ts_ms": 0,
            "bank": "A",
            "src_account": source_a,
            "dst_account": relay_b,
            "amount": 3200.0,
            "tx_type": "TRANSFER",
            "memo": "synthetic settlement",
        },
        {
            **common,
            "event_id": "HEIST-B-TO-C",
            "ts_ms": 4000,
            "bank": "B",
            "src_account": relay_b,
            "dst_account": receiver_c,
            "amount": 3040.0,
            "tx_type": "PAYMENT",
            "memo": "synthetic settlement",
        },
        {
            **common,
            "event_id": "HEIST-C-CASHOUT",
            "ts_ms": 9000,
            "bank": "C",
            "src_account": receiver_c,
            "dst_account": "EXT:DEMO-CASHOUT",
            "amount": 2890.0,
            "tx_type": "CASHOUT",
            "memo": "synthetic cashout",
        },
    ]


def run_heist(
    accounts: dict[str, dict[str, str]],
    bank_urls: dict[str, str],
    *,
    speed_factor: float = 1.0,
    post: Callable[..., Any] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> list[dict[str, Any]]:
    """Send raw synthetic events to their owning bank, preserving event timing."""
    if speed_factor <= 0:
        raise ValueError("speed_factor must be positive")
    post = post or requests.post
    results = []
    previous_ts: int | None = None
    for event in build_heist_events(accounts):
        if event["bank"] not in bank_urls:
            raise ValueError(f"missing URL for bank {event['bank']}")
        if previous_ts is not None:
            sleep_fn((event["ts_ms"] - previous_ts) / 1000.0 / speed_factor)
        previous_ts = event["ts_ms"]
        response = post(
            f"{bank_urls[event['bank']].rstrip('/')}/event",
            json=event,
            timeout=10,
        )
        response.raise_for_status()
        try:
            result = response.json()
        except (ValueError, AttributeError):
            result = {"accepted": bool(getattr(response, "ok", False))}
        results.append(result)
    return results


def _read_accounts(path: str | Path) -> dict[str, dict[str, str]]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if type(value) is not dict:
        raise ValueError("heist accounts file must contain a JSON object")
    return value


def _bank_urls_from_environment() -> dict[str, str]:
    urls = {
        bank: os.environ.get(f"BANK_{bank}_URL", f"http://bank-{bank.lower()}:{port}")
        for bank, port in (("A", 8001), ("B", 8002), ("C", 8003))
    }
    return urls


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--accounts", default=os.environ.get("HEIST_ACCOUNTS_FILE", "demo-data/heist-accounts.json"))
    parser.add_argument("--speed-factor", type=float, default=float(os.environ.get("REPLAY_SPEED", "1")))
    args = parser.parse_args()
    results = run_heist(_read_accounts(args.accounts), _bank_urls_from_environment(), speed_factor=args.speed_factor)
    print(json.dumps({"sent": len(results), "responses": results}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
