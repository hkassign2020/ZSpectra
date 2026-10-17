"""Private demo-control API for synthetic heist and dataset replay requests."""

from __future__ import annotations

import hmac
import os
import threading
import time
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request
import requests

from tools.heist import _read_accounts, run_heist
from tools.replayer import load_jsonl, replay_events


def create_app(
    accounts_path: str | Path | None = None,
    events_path: str | Path | None = None,
    bank_urls: dict[str, str] | None = None,
    token: str | None = None,
    speed_factor: float = 1.0,
    benchmark_speed_factor: float = 10.0,
) -> Flask:
    accounts_path = Path(accounts_path or os.environ.get("HEIST_ACCOUNTS_FILE", "demo-data/heist-accounts.json"))
    events_path = Path(events_path or os.environ.get("EVENTS_FILE", "demo-data/events.jsonl"))
    bank_urls = bank_urls or {
        bank: os.environ.get(f"BANK_{bank}_URL", f"http://bank-{bank.lower()}:{port}")
        for bank, port in (("A", 8001), ("B", 8002), ("C", 8003))
    }
    token = token if token is not None else os.environ.get("REPLAY_TOKEN", "")
    lock = threading.Lock()
    app = Flask(__name__)

    def authorized() -> bool:
        supplied = request.headers.get("X-Replay-Token", "")
        return bool(token) and hmac.compare_digest(supplied, token)

    @app.get("/health")
    def health():
        return jsonify({"status": "ok"})

    @app.post("/heist")
    def heist():
        if not authorized():
            return jsonify({"error": "replay authorization required"}), 403
        if not lock.acquire(blocking=False):
            return jsonify({"error": "a replay is already running"}), 409
        try:
            responses = run_heist(
                _read_accounts(accounts_path),
                bank_urls,
                speed_factor=speed_factor,
            )
            return jsonify({"started": True, "accepted": all(row.get("accepted", False) for row in responses)})
        except (OSError, ValueError, requests.RequestException):
            return jsonify({"error": "heist delivery failed"}), 502
        finally:
            lock.release()

    @app.post("/replay")
    def replay():
        if not authorized():
            return jsonify({"error": "replay authorization required"}), 403
        if not lock.acquire(blocking=False):
            return jsonify({"error": "a replay is already running"}), 409
        try:
            responses = replay_events(load_jsonl(events_path), bank_urls, speed_factor=speed_factor)
            return jsonify({"replayed": len(responses)})
        except (OSError, ValueError, requests.RequestException):
            return jsonify({"error": "replay delivery failed"}), 502
        finally:
            lock.release()

    @app.post("/benchmark/heist")
    def benchmark_heist():
        if not authorized():
            return jsonify({"error": "replay authorization required"}), 403
        if not lock.acquire(blocking=False):
            return jsonify({"error": "a replay is already running"}), 409
        measurement: dict[str, Any] = {}

        def measured_post(url: str, *, json: dict[str, Any], timeout: float):
            started = time.perf_counter_ns() if json.get("event_id") == "HEIST-B-TO-C" else None
            response = requests.post(url, json=json, timeout=timeout)
            if started is not None:
                measurement["end_to_end_ms"] = (time.perf_counter_ns() - started) / 1_000_000.0
                try:
                    body = response.json()
                except ValueError:
                    body = {}
                alert = body.get("alert") if isinstance(body, dict) else None
                delivery = alert.get("hold_delivery", {}) if isinstance(alert, dict) else {}
                measurement["alert_received"] = isinstance(alert, dict) and bool(alert.get("alert_id"))
                measurement["hold_delivered"] = delivery.get("C") == "sent"
            return response

        try:
            run_heist(
                _read_accounts(accounts_path),
                bank_urls,
                speed_factor=benchmark_speed_factor,
                post=measured_post,
            )
            if "end_to_end_ms" not in measurement:
                return jsonify({"error": "hop-two response was not measured"}), 503
            return jsonify(measurement)
        except (OSError, ValueError, requests.RequestException):
            return jsonify({"error": "benchmark heist failed"}), 502
        finally:
            lock.release()

    @app.post("/reset")
    def reset():
        if not authorized():
            return jsonify({"error": "replay authorization required"}), 403
        return jsonify({"reset": True})

    return app


def main() -> None:
    app = create_app()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8081")), threaded=True)


if __name__ == "__main__":
    main()
