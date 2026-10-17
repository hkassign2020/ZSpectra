"""Bank-local Flask service. Raw event fields never leave this process."""

from __future__ import annotations

import json
import os
import hmac
import logging
from pathlib import Path
from typing import Any, Iterable

from flask import Flask, jsonify, request
import requests

from audit import Auditor, CanaryScanner
from audit.logging import ScannerLogHandler
from bank.sensor import BlindSensor, bid_for


def _scanner_from_environment() -> CanaryScanner:
    index_path = os.environ.get("CANARY_INDEX_FILE")
    if not index_path:
        return CanaryScanner()
    return CanaryScanner.from_index_file(index_path)


def _account_index(accounts: Iterable[dict[str, str]], key: bytes) -> dict[str, dict[str, str]]:
    index: dict[str, dict[str, str]] = {}
    for item in accounts:
        account = item["account"]
        index[bid_for(account, key)] = {
            "account": account,
            "customer_name": item["customer_name"],
        }
    return index


def _read_accounts(value: Iterable[dict[str, str]] | str | Path | None) -> list[dict[str, str]]:
    if value is None:
        path = os.environ.get("ACCOUNTS_FILE")
        if not path:
            return []
        value = path
    if isinstance(value, (str, Path)):
        loaded = json.loads(Path(value).read_text(encoding="utf-8"))
        if not isinstance(loaded, list):
            raise ValueError("account file must contain a list")
        return loaded
    return list(value)


def create_app(
    bank: str | None = None,
    key: bytes | None = None,
    mesh_url: str | None = None,
    accounts: Iterable[dict[str, str]] | str | Path | None = None,
    scanner: CanaryScanner | None = None,
    operator_token: str | None = None,
) -> Flask:
    """Create a bank service; explicit args make the boundary testable."""
    bank = bank or os.environ.get("BANK_CODE")
    if bank not in {"A", "B", "C"}:
        raise RuntimeError("BANK_CODE must be A, B, or C")
    if key is None:
        key_text = os.environ.get("CONSORTIUM_KEY")
        if not key_text:
            raise RuntimeError("CONSORTIUM_KEY must be provided through the environment")
        key = key_text.encode("utf-8")
    if not isinstance(key, bytes) or not key:
        raise RuntimeError("CONSORTIUM_KEY must be non-empty bytes")
    mesh_url = (mesh_url or os.environ.get("MESH_URL", "http://mesh:8000")).rstrip("/")
    scanner = scanner or _scanner_from_environment()
    validator = Auditor()
    sensor = BlindSensor(key, bank)
    local_index = _account_index(_read_accounts(accounts), key)
    hold_state: dict[str, dict[str, str]] = {}
    operator_token = operator_token or os.environ.get("OPERATOR_TOKEN")

    app = Flask(__name__)
    app.config.update(
        BANK_CODE=bank,
        HOLD_STATE=hold_state,
        LOCAL_INDEX=local_index,
        SCANNER=scanner,
        VALIDATOR=validator,
    )
    audit_handler = ScannerLogHandler(scanner, "audit.log")
    app.logger.addHandler(audit_handler)

    @app.post("/event")
    def event() -> Any:
        raw = request.get_json(silent=True)
        if type(raw) is not dict:
            return jsonify({"error": "invalid event"}), 400
        try:
            blind = sensor.process(raw)
            validator.check("bank_to_mesh", blind)
            scanner.scan("bank_to_mesh", json.dumps(blind, separators=(",", ":")))
            audit_handler.record(f"boundary audit allowed bank_to_mesh bank={bank} seq={blind['seq']}")
            response = requests.post(f"{mesh_url}/ingest", json=blind, timeout=5)
            response.raise_for_status()
        except (TypeError, ValueError, KeyError):
            return jsonify({"error": "event rejected"}), 400
        except requests.RequestException:
            app.logger.warning("mesh ingest unavailable for bank %s", bank)
            return jsonify({"error": "mesh unavailable"}), 502
        try:
            return jsonify(response.json()), response.status_code
        except ValueError:
            return jsonify({"accepted": response.ok}), response.status_code

    @app.post("/hold")
    def hold() -> Any:
        payload = request.get_json(silent=True)
        try:
            validator.check("mesh_to_bank", payload)
        except (TypeError, ValueError):
            return jsonify({"error": "invalid hold recommendation"}), 400
        scanner.scan("mesh_to_bank", json.dumps(payload, separators=(",", ":")))
        bid = payload["bid"]
        if bid not in local_index:
            return jsonify({"status": "ignored"})
        hold_state[bid] = {
            "status": "hold recommended, awaiting operator",
            "alert_id": payload["alert_id"],
        }
        return jsonify({"status": hold_state[bid]["status"]})

    @app.get("/reveal")
    def reveal() -> Any:
        if not operator_token:
            return jsonify({"error": "operator console is not configured"}), 503
        provided = request.headers.get("X-Operator-Token", "")
        if not hmac.compare_digest(provided, operator_token):
            return jsonify({"error": "operator authorization required"}), 403
        bid = request.args.get("bid", "")
        account = local_index.get(bid)
        if account is None:
            return jsonify({"error": "account not found"}), 404
        return jsonify(
            {
                "bid": bid,
                "customer_name": account["customer_name"],
                "hold": hold_state.get(bid, {}).get("status", "none"),
            }
        )

    @app.get("/audit")
    def audit_state() -> Any:
        return jsonify(
            {
                "messages_scanned": scanner.messages,
                "bytes_scanned": scanner.bytes_scanned,
                "canary_hits": scanner.hits,
                "validator_blocks": validator.blocked_count,
            }
        )

    @app.post("/reset")
    def reset() -> Any:
        sensor.reset()
        hold_state.clear()
        validator.reset()
        scanner.reset()
        audit_handler.reset()
        return jsonify({"reset": True})

    @app.get("/health")
    def health() -> Any:
        return jsonify({"status": "ok", "bank": bank})

    return app


def main() -> None:
    app = create_app()
    logging.getLogger("werkzeug").addHandler(
        ScannerLogHandler(app.config["SCANNER"], "audit.log")
    )
    bank = app.config["BANK_CODE"]
    port = int(os.environ.get("PORT", "8001"))
    if bank == "B":
        port = int(os.environ.get("PORT", "8002"))
    elif bank == "C":
        port = int(os.environ.get("PORT", "8003"))
    app.run(host=os.environ.get("BIND_HOST", "0.0.0.0"), port=port, threaded=True)


if __name__ == "__main__":
    main()
