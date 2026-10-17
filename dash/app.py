"""Flask UI and narrow proxies for blind mesh state and demo controls."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable

from flask import Flask, jsonify, send_from_directory
import requests


def _platform_evidence() -> dict[str, Any]:
    evidence_path = os.environ.get("PLATFORM_EVIDENCE_FILE")
    if evidence_path:
        try:
            value = json.loads(Path(evidence_path).read_text(encoding="utf-8"))
            if isinstance(value, dict):
                return value
        except (OSError, json.JSONDecodeError):
            pass
    from tools.platform_proof import collect

    return collect()


def create_app(
    mesh_url: str | None = None,
    replayer_url: str | None = None,
    replayer_token: str | None = None,
    bank_urls: dict[str, str] | None = None,
    results_path: str | Path | None = None,
    platform_collector: Callable[[], dict[str, Any]] | None = None,
) -> Flask:
    mesh_url = (mesh_url or os.environ.get("MESH_URL", "http://mesh:8000")).rstrip("/")
    replayer_url = (replayer_url or os.environ.get("REPLAYER_URL", "http://replayer:8081")).rstrip("/")
    replayer_token = replayer_token if replayer_token is not None else os.environ.get("REPLAY_TOKEN", "")
    bank_urls = bank_urls or {
        bank: os.environ.get(f"BANK_{bank}_URL", f"http://bank-{bank.lower()}:{port}").rstrip("/")
        for bank, port in (("A", 8001), ("B", 8002), ("C", 8003))
    }
    results_path = Path(results_path or os.environ.get("RESULTS_FILE", "results.json"))
    platform_collector = platform_collector or _platform_evidence

    app = Flask(__name__, static_folder="static")
    app.config.update(
        MESH_URL=mesh_url,
        REPLAYER_URL=replayer_url,
        BANK_URLS=bank_urls,
        RESULTS_PATH=results_path,
    )

    @app.get("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    @app.get("/api/state")
    def state():
        try:
            response = requests.get(f"{mesh_url}/state", timeout=5)
            response.raise_for_status()
            return jsonify(response.json())
        except (requests.RequestException, ValueError):
            return jsonify({"error": "mesh state unavailable"}), 502

    @app.get("/api/platform")
    def platform():
        return jsonify(platform_collector())

    @app.get("/api/audit")
    def audit():
        counts = {
            "messages_scanned": 0,
            "bytes_scanned": 0,
            "canary_hits": 0,
            "validator_blocks": 0,
            "negative_control": None,
        }
        services = [("mesh", f"{mesh_url}/audit")]
        services.extend((f"bank-{bank}", f"{url}/audit") for bank, url in bank_urls.items())
        try:
            for _name, url in services:
                response = requests.get(url, timeout=5)
                response.raise_for_status()
                value = response.json()
                for key in ("messages_scanned", "bytes_scanned", "canary_hits", "validator_blocks"):
                    counts[key] += int(value.get(key, 0))
                if value.get("negative_control"):
                    counts["negative_control"] = value["negative_control"]
            return jsonify(counts)
        except (requests.RequestException, ValueError, TypeError):
            return jsonify({"error": "audit counters unavailable"}), 502

    @app.get("/api/results")
    def results():
        if not results_path.is_file():
            return jsonify({"available": False})
        try:
            document = json.loads(results_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return jsonify({"error": "results file is unreadable"}), 503
        if type(document) is not dict:
            return jsonify({"error": "results file must contain a JSON object"}), 503
        return jsonify({"available": True, "results": document})

    @app.post("/api/heist")
    def heist():
        if not replayer_token:
            return jsonify({"error": "demo runner is not configured"}), 503
        try:
            response = requests.post(
                f"{replayer_url}/heist",
                headers={"X-Replay-Token": replayer_token},
                json={},
                timeout=30,
            )
            response.raise_for_status()
            return jsonify(response.json()), response.status_code
        except requests.RequestException:
            return jsonify({"error": "demo runner unavailable"}), 502
        except ValueError:
            return jsonify({"error": "demo runner returned invalid JSON"}), 502

    @app.post("/api/negative-control")
    def negative_control():
        try:
            response = requests.post(f"{mesh_url}/audit/negative-control", json={}, timeout=5)
            response.raise_for_status()
            return jsonify(response.json()), response.status_code
        except requests.RequestException:
            return jsonify({"error": "mesh audit unavailable"}), 502
        except ValueError:
            return jsonify({"error": "mesh audit returned invalid JSON"}), 502

    @app.post("/api/reset")
    def reset():
        failures = []
        for name, url in [
            ("mesh", f"{mesh_url}/reset"),
            *((f"bank-{bank}", f"{base_url}/reset") for bank, base_url in bank_urls.items()),
            ("replayer", f"{replayer_url}/reset"),
        ]:
            try:
                headers = {"X-Replay-Token": replayer_token} if name == "replayer" else {}
                response = requests.post(url, headers=headers, json={}, timeout=5)
                response.raise_for_status()
            except requests.RequestException:
                failures.append(name)
        if failures:
            return jsonify({"reset": False, "unavailable": failures}), 502
        return jsonify({"reset": True})

    @app.get("/api/health")
    def health():
        services = {"mesh": f"{mesh_url}/health", "replayer": f"{replayer_url}/health"}
        services.update((f"bank-{bank}", f"{url}/health") for bank, url in bank_urls.items())
        statuses = {}
        for name, url in services.items():
            try:
                response = requests.get(url, timeout=3)
                response.raise_for_status()
                statuses[name] = "ok"
            except requests.RequestException:
                statuses[name] = "unavailable"
        healthy = all(value == "ok" for value in statuses.values())
        return jsonify({"status": "ok" if healthy else "degraded", "services": statuses}), 200 if healthy else 503

    return app


def main() -> None:
    port = int(os.environ.get("PORT", "8080"))
    create_app().run(host="0.0.0.0", port=port, threaded=True)


if __name__ == "__main__":
    main()
