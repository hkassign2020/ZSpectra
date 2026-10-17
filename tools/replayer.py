"""Replay generated synthetic events through the owning bank services."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Iterable

import requests


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows = []
    with Path(path).open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if type(value) is not dict:
                raise ValueError(f"event line {line_number} must contain an object")
            rows.append(value)
    return rows


def replay_events(
    events: Iterable[dict[str, Any]],
    bank_urls: dict[str, str],
    *,
    speed_factor: float = 1.0,
    post: Callable[..., Any] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> list[dict[str, Any]]:
    """Replay in timestamp order and retain relative timing at the selected speed."""
    if speed_factor <= 0:
        raise ValueError("speed_factor must be positive")
    post = post or requests.post
    ordered = sorted(events, key=lambda item: (item["ts_ms"], item.get("event_id", "")))
    results = []
    previous_ts: int | None = None
    for event in ordered:
        bank = event.get("bank")
        if bank not in bank_urls:
            raise ValueError(f"missing URL for bank {bank}")
        timestamp = event.get("ts_ms")
        if type(timestamp) is not int:
            raise ValueError("event ts_ms must be an integer")
        if previous_ts is not None:
            sleep_fn(max(0, timestamp - previous_ts) / 1000.0 / speed_factor)
        previous_ts = timestamp
        response = post(
            f"{bank_urls[bank].rstrip('/')}/event",
            json=event,
            timeout=10,
        )
        response.raise_for_status()
        try:
            results.append(response.json())
        except (ValueError, AttributeError):
            results.append({"accepted": bool(getattr(response, "ok", False))})
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("events", nargs="?", default=os.environ.get("EVENTS_FILE", "demo-data/events.jsonl"))
    parser.add_argument("--speed-factor", type=float, default=float(os.environ.get("REPLAY_SPEED", "1")))
    args = parser.parse_args()
    urls = {
        bank: os.environ.get(f"BANK_{bank}_URL", f"http://bank-{bank.lower()}:{port}")
        for bank, port in (("A", 8001), ("B", 8002), ("C", 8003))
    }
    results = replay_events(load_jsonl(args.events), urls, speed_factor=args.speed_factor)
    print(json.dumps({"replayed": len(results)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
