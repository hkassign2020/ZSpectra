"""Blind-chain mesh service and audited outbound routing."""

from __future__ import annotations

import json
import logging
import os
import pickle
import secrets
import threading
import time
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Callable

from flask import Flask, Response, jsonify, request, stream_with_context
import requests

from audit import Auditor, CanaryScanner
from audit.logging import ScannerLogHandler
from mesh.chain import ChainLinker
from mesh.features import mesh_feature_vector
from mesh.llm import generate_brief, serialize_payload


def _scanner_from_environment() -> CanaryScanner:
    index_path = os.environ.get("CANARY_INDEX_FILE")
    if not index_path:
        return CanaryScanner()
    return CanaryScanner.from_index_file(index_path)


def _bank_urls_from_environment() -> dict[str, str]:
    values = {}
    for bank in ("A", "B", "C"):
        value = os.environ.get(f"BANK_{bank}_URL")
        if value:
            values[bank] = value.rstrip("/")
    return values


def load_models(model_path: str | Path, thresholds_path: str | Path) -> Any:
    """Load a model trained on the host architecture; no model crosses the wire."""
    with Path(model_path).open("rb") as stream:
        model = pickle.load(stream)
    model.thresholds = model.load_thresholds(thresholds_path)
    return model


def create_app(
    models: Any,
    bank_urls: dict[str, str] | None = None,
    scanner: CanaryScanner | None = None,
    llm_generator: Callable[..., tuple[str, dict[str, Any]]] | None = None,
) -> Flask:
    if models is None:
        raise RuntimeError("trained models are required")
    thresholds = getattr(models, "thresholds", None)
    if not isinstance(thresholds, dict) or not {"T_cascade", "T_anom"}.issubset(thresholds):
        raise RuntimeError("model thresholds are required")

    bank_urls = bank_urls or _bank_urls_from_environment()
    scanner = scanner or _scanner_from_environment()
    llm_generator = llm_generator or generate_brief
    auditor = Auditor()
    linker = ChainLinker()
    lock = threading.RLock()
    stream_queues: list[Queue[str]] = []
    state: dict[str, Any] = {
        "events": [],
        "alerts": [],
        "alert_revisions": {},
        "alerted_chains": set(),
        "last_prompt": None,
        "negative_control": None,
        "decision_latencies_ms": [],
    }

    app = Flask(__name__)
    app.config.update(STATE=state, SCANNER=scanner, VALIDATOR=auditor)
    audit_handler = ScannerLogHandler(scanner, "audit.log")
    app.logger.addHandler(audit_handler)

    def publish(payload: dict[str, Any]) -> None:
        data = json.dumps(payload, separators=(",", ":"))
        for stream_queue in tuple(stream_queues):
            stream_queue.put(data)

    def audited_post(channel: str, url: str, payload: dict[str, Any]) -> requests.Response:
        auditor.check(channel, payload)
        scanner.scan(channel, json.dumps(payload, separators=(",", ":")))
        return requests.post(url, json=payload, timeout=5)

    def route_hold(target_bid: str, alert_id: str) -> dict[str, str]:
        payload = {"bid": target_bid, "alert_id": alert_id}
        delivered: dict[str, str] = {}
        for bank, base_url in bank_urls.items():
            try:
                response = audited_post("mesh_to_bank", f"{base_url}/hold", payload)
                response.raise_for_status()
                delivered[bank] = "sent"
            except requests.RequestException:
                delivered[bank] = "unavailable"
                app.logger.warning("hold recommendation delivery failed for bank %s", bank)
        return delivered

    def handle_alert(
        linked: dict[str, Any],
        score: dict[str, float],
        vector: list[float],
        kind: str,
        alert_id: str | None = None,
    ) -> dict[str, Any]:
        alert_id = alert_id or "AL_" + secrets.token_hex(4)
        evidence = models.evidence(vector)
        llm_payload = {
            "chain": linked["chain"],
            "p_cascade": float(score["p_cascade"]),
            "anom": float(score["anom"]),
            "evidence": evidence,
        }
        # Validate and freeze the dashboard prompt before making the alert visible.
        # The async sender rechecks this exact serialization through the auditor and scanner.
        prompt_json = serialize_payload(llm_payload)
        state["last_prompt"] = prompt_json
        hold_rec = {"bid": linked["dst_bid"], "alert_id": alert_id}
        return {
            "alert_id": alert_id,
            "type": kind,
            "chain_id": linked["chain_id"],
            "hop_depth": linked["hop_depth"],
            "target_bid": linked["dst_bid"],
            "p_cascade": float(score["p_cascade"]),
            "anom": float(score["anom"]),
            "evidence": evidence,
            "llm_payload": llm_payload,
            "llm_prompt": prompt_json,
            "llm_brief": None,
            "llm_status": "pending",
            "hold_rec": hold_rec,
            "hold_delivery": {bank: "pending" for bank in bank_urls},
        }

    def start_llm_job(alert_id: str, revision: int, payload: dict[str, Any], prompt_json: str) -> None:
        def generate() -> None:
            try:
                returned_prompt, brief = llm_generator(
                    payload,
                    scanner=scanner,
                    auditor=auditor,
                    timeout=60,
                    prompt_json=prompt_json,
                )
                if returned_prompt != prompt_json:
                    raise ValueError("LLM generator changed the dashboard prompt")
                if not isinstance(brief, dict) or brief.get("llm_mode") not in {"local", "api", "template"}:
                    raise ValueError("LLM generator returned an invalid llm_mode")
                audit_handler.record(
                    f"boundary audit allowed mesh_to_llm chain depth={len(payload['chain'])}"
                )
            except Exception:
                app.logger.exception("LLM brief generation failed; using a safe template")
                brief = {
                    "summary": "A model brief was unavailable; review the blind evidence.",
                    "suspected_pattern": "unclassified transfer pattern",
                    "risk_level": "unclassified",
                    "recommended_action": "Review the alert; the owning bank makes any hold decision.",
                    "limits": "The model response was unavailable. BID values are pseudonymous and linkable.",
                    "llm_mode": "template",
                }
                scanner.scan("llm_response", json.dumps(brief, sort_keys=True, separators=(",", ":")))

            with lock:
                if state["alert_revisions"].get(alert_id) != revision:
                    return
                current = next((item for item in state["alerts"] if item["alert_id"] == alert_id), None)
                if current is not None:
                    current["llm_brief"] = brief
                    current["llm_status"] = "complete"

        threading.Thread(target=generate, name=f"llm-{alert_id}", daemon=True).start()

    @app.post("/ingest")
    def ingest() -> Any:
        received_ns = time.perf_counter_ns()
        raw_body = request.get_data(cache=True, as_text=True)
        message = request.get_json(silent=True)
        scan_text = (
            json.dumps(message, separators=(",", ":"), ensure_ascii=False)
            if message is not None
            else raw_body
        )
        scanner.scan("bank_to_mesh", scan_text)
        try:
            auditor.check("bank_to_mesh", message)
        except (TypeError, ValueError):
            app.logger.warning("boundary validator blocked bank_to_mesh message")
            return jsonify({"error": "invalid blind message"}), 400
        audit_handler.record(
            f"boundary audit allowed bank_to_mesh bank={message['bank']} seq={message['seq']}"
        )

        llm_job = None
        hold_job = None
        with lock:
            linked = linker.add(message)
            vector = message["f"] + mesh_feature_vector(linked)
            try:
                score = models.score([vector])[0]
            except (TypeError, ValueError, RuntimeError):
                app.logger.exception("model scoring failed")
                return jsonify({"error": "scoring unavailable"}), 503

            alert = None
            chain_id = linked["chain_id"]
            prior_alert = next(
                (item for item in state["alerts"] if item["chain_id"] == chain_id),
                None,
            )
            cascade_triggered = (
                linked["hop_depth"] >= 2
                and score["p_cascade"] >= thresholds["T_cascade"]
            )
            if cascade_triggered and (
                prior_alert is None or prior_alert["type"] != "CROSS_BANK_MULE_CASCADE"
            ):
                # A hop-one anomaly is provisional: promote it in place if a
                # later event completes a cascade. Keep one active alert per chain.
                alert = handle_alert(
                    linked,
                    score,
                    vector,
                    "CROSS_BANK_MULE_CASCADE",
                    alert_id=prior_alert["alert_id"] if prior_alert else None,
                )
                revision = state["alert_revisions"].get(alert["alert_id"], 0) + 1
                state["alert_revisions"][alert["alert_id"]] = revision
                llm_job = (
                    alert["alert_id"],
                    revision,
                    json.loads(alert["llm_prompt"]),
                    alert["llm_prompt"],
                )
                hold_job = (alert["alert_id"], revision, alert["target_bid"])
                if prior_alert:
                    state["alerts"][state["alerts"].index(prior_alert)] = alert
                else:
                    state["alerts"].append(alert)
                state["alerted_chains"].add(chain_id)
            elif prior_alert is None and score["anom"] >= thresholds["T_anom"]:
                alert = handle_alert(linked, score, vector, "UNKNOWN_ANOMALY")
                state["alerted_chains"].add(chain_id)
                state["alerts"].append(alert)

            if alert is not None:
                state["decision_latencies_ms"].append(
                    (time.perf_counter_ns() - received_ns) / 1_000_000.0
                )

            record = {
                "message": dict(message),
                "chain_id": linked["chain_id"],
                "hop_depth": linked["hop_depth"],
                "chain": linked["chain"],
                "mesh_features": mesh_feature_vector(linked),
                "p_cascade": float(score["p_cascade"]),
                "anom": float(score["anom"]),
                "evidence": models.evidence(vector),
                "alert": alert,
            }
            state["events"].append(record)
            if len(state["events"]) > 500:
                del state["events"][:-500]
            publish(record)

        # Let /state serve the new alert before any model or bank network call completes.
        if llm_job is not None:
            start_llm_job(*llm_job)
        if hold_job is not None:
            alert_id, revision, target_bid = hold_job
            delivery = route_hold(target_bid, alert_id)
            with lock:
                if state["alert_revisions"].get(alert_id) == revision:
                    current = next((item for item in state["alerts"] if item["alert_id"] == alert_id), None)
                    if current is not None:
                        current["hold_delivery"] = delivery

        return jsonify({"accepted": True, "alert": alert}), 200

    @app.get("/state")
    def get_state() -> Any:
        with lock:
            return jsonify(
                {
                    "events": list(state["events"]),
                    "alerts": list(state["alerts"]),
                    "last_prompt": state["last_prompt"],
                    "negative_control": state["negative_control"],
                    "audit": {
                        "messages_scanned": scanner.messages,
                        "bytes_scanned": scanner.bytes_scanned,
                        "canary_hits": scanner.hits,
                        "self_test_messages": scanner.self_test_messages,
                        "self_test_bytes_scanned": scanner.self_test_bytes_scanned,
                        "self_test_hits": scanner.self_test_hits,
                        "validator_blocks": auditor.blocked_count,
                    },
                }
            )

    @app.get("/metrics")
    def metrics() -> Any:
        """Expose local benchmark measurements, separate from wire messages."""
        with lock:
            return jsonify({"decision_latency_ms": list(state["decision_latencies_ms"])})

    @app.get("/stream")
    def stream() -> Response:
        stream_queue: Queue[str] = Queue()
        stream_queues.append(stream_queue)

        @stream_with_context
        def generate():
            try:
                while True:
                    try:
                        data = stream_queue.get(timeout=15)
                        yield f"data: {data}\n\n"
                    except Empty:
                        yield ": keep-alive\n\n"
            finally:
                if stream_queue in stream_queues:
                    stream_queues.remove(stream_queue)

        return Response(generate(), mimetype="text/event-stream")

    @app.post("/reset")
    def reset() -> Any:
        with lock:
            linker.reset()
            state["events"].clear()
            state["alerts"].clear()
            state["alert_revisions"].clear()
            state["alerted_chains"].clear()
            state["last_prompt"] = None
            state["negative_control"] = None
            state["decision_latencies_ms"].clear()
            scanner.reset()
            auditor.reset()
            audit_handler.reset()
        return jsonify({"reset": True})

    @app.post("/audit/negative-control")
    def negative_control() -> Any:
        from audit.scanner import NEGATIVE_CONTROL_TOKEN

        control_message = json.dumps(
            {"negative_control_canary": NEGATIVE_CONTROL_TOKEN},
            separators=(",", ":"),
        )
        # Self-test traffic is deliberately excluded from production audit
        # counters so a caught test canary cannot look like a real leak.
        hits = scanner.scan("negative_control", control_message, count=False)
        caught = hits > 0
        state["negative_control"] = "caught" if caught else "missed"
        return jsonify({"negative_control": state["negative_control"], "hits": hits})

    @app.get("/audit")
    def audit_state() -> Any:
        return jsonify(
            {
                "messages_scanned": scanner.messages,
                "bytes_scanned": scanner.bytes_scanned,
                "canary_hits": scanner.hits,
                "self_test_messages": scanner.self_test_messages,
                "self_test_bytes_scanned": scanner.self_test_bytes_scanned,
                "self_test_hits": scanner.self_test_hits,
                "validator_blocks": auditor.blocked_count,
                "negative_control": state["negative_control"],
            }
        )

    @app.get("/health")
    def health() -> Any:
        return jsonify({"status": "ok"})

    return app


def main() -> None:
    model_path = os.environ.get("MODEL_FILE", "models/fraud_models.pkl")
    threshold_path = os.environ.get("THRESHOLDS_FILE", "models/thresholds.json")
    models = load_models(model_path, threshold_path)
    app = create_app(models)
    logging.getLogger("werkzeug").addHandler(
        ScannerLogHandler(app.config["SCANNER"], "audit.log")
    )
    port = int(os.environ.get("PORT", "8000"))
    app.run(host=os.environ.get("BIND_HOST", "0.0.0.0"), port=port, threaded=True)


if __name__ == "__main__":
    main()
