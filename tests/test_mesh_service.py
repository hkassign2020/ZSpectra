import json
import threading
import time

import pytest

pytest.importorskip("flask")
pytest.importorskip("requests")

from mesh.service import create_app


class DummyModels:
    thresholds = {"T_cascade": 0.5, "T_anom": 0.99}

    def score(self, vectors):
        return [{"anom": 0.1, "p_cascade": 0.9} for _ in vectors]

    def evidence(self, vector):
        return [["hop_depth", 99.0], ["log_amount_bin", 90.0], ["window_ncd", 85.0]]


def message(bank, seq, t_ms, src_bid, dst_bid):
    return {
        "bank": bank,
        "seq": seq,
        "t_ms": t_ms,
        "src_bid": src_bid,
        "dst_bid": dst_bid,
        "amt_bin": 1.0,
        "f": [0.1] * 8,
        "sensor_us": 10.0,
    }


def test_heist_alerts_at_hop_two_and_recommends_hold_for_received_account():
    sent_holds = []
    session_factories = []

    def post(url, json, timeout):
        sent_holds.append((url, json))

        class Response:
            status_code = 200
            ok = True

            @staticmethod
            def raise_for_status():
                return None

        return Response()

    class Session:
        def post(self, url, *, json, timeout):
            return post(url, json, timeout)

    def session_factory():
        session_factories.append(True)
        return Session()

    app = create_app(
        models=DummyModels(),
        bank_urls={bank: f"http://bank-{bank}" for bank in ("A", "B", "C")},
        http_session_factory=session_factory,
        llm_generator=lambda payload, scanner=None, **kwargs: (
            kwargs["prompt_json"], {"llm_mode": "template"}
        ),
    )
    client = app.test_client()
    first = message("A", 1, 1000, "BID_000000000001", "BID_000000000002")
    second = message("B", 1, 5000, "BID_000000000002", "BID_000000000003")

    assert client.post("/ingest", json=first).status_code == 200
    assert client.get("/state").json["alerts"] == []
    response = client.post("/ingest", json=second)

    state = client.get("/state").json
    assert response.status_code == 200
    assert len(state["alerts"]) == 1
    assert state["alerts"][0]["hop_depth"] == 2
    assert state["alerts"][0]["hold_rec"]["bid"] == "BID_000000000003"
    metrics = client.get("/metrics").json["decision_latency_ms"]
    assert len(metrics) == 1
    assert metrics[0] >= 0
    assert len(sent_holds) == 3
    assert len(session_factories) == 1
    assert all(payload["bid"] == "BID_000000000003" for _, payload in sent_holds)


def test_reset_clears_mesh_chain_and_alert_state():
    app = create_app(
        models=DummyModels(),
        bank_urls={},
        llm_generator=lambda payload, scanner=None, **kwargs: (
            kwargs["prompt_json"], {"llm_mode": "template"}
        ),
    )
    client = app.test_client()
    client.post(
        "/ingest",
        json=message("A", 1, 1000, "BID_000000000001", "BID_000000000002"),
    )

    assert client.post("/reset").status_code == 200
    assert client.get("/state").json["events"] == []
    assert client.get("/metrics").json["decision_latency_ms"] == []
    assert app.config["SCANNER"].messages == 0
    assert app.config["VALIDATOR"].blocked_count == 0


def test_negative_control_message_is_caught_by_the_shared_scanner():
    app = create_app(
        models=DummyModels(),
        bank_urls={},
        llm_generator=lambda payload, scanner=None, **kwargs: (
            kwargs["prompt_json"], {"llm_mode": "template"}
        ),
    )

    response = app.test_client().post("/audit/negative-control")

    assert response.json["negative_control"] == "caught"
    assert response.json["hits"] > 0
    assert app.config["SCANNER"].hits == 0
    assert app.config["SCANNER"].messages == 0
    assert app.config["SCANNER"].self_test_hits > 0


def test_hop_one_anomaly_is_promoted_when_the_chain_becomes_a_cascade():
    class AnomalyThenCascadeModels(DummyModels):
        def __init__(self):
            self.calls = 0

        def score(self, vectors):
            self.calls += 1
            if self.calls == 1:
                return [{"anom": 1.0, "p_cascade": 0.1}]
            return [{"anom": 0.1, "p_cascade": 0.9}]

    app = create_app(
        models=AnomalyThenCascadeModels(),
        bank_urls={},
        llm_generator=lambda payload, scanner=None, **kwargs: (
            kwargs["prompt_json"], {"llm_mode": "template"}
        ),
    )
    client = app.test_client()
    first = message("A", 1, 1000, "BID_000000000001", "BID_000000000002")
    second = message("B", 1, 5000, "BID_000000000002", "BID_000000000003")
    third = message("C", 1, 6000, "BID_000000000003", "BID_000000000004")

    first_response = client.post("/ingest", json=first)
    first_alert = first_response.json["alert"]
    assert first_alert["type"] == "UNKNOWN_ANOMALY"

    second_response = client.post("/ingest", json=second)
    active_alerts = client.get("/state").json["alerts"]

    assert second_response.status_code == 200
    assert len(active_alerts) == 1
    assert active_alerts[0]["type"] == "CROSS_BANK_MULE_CASCADE"
    assert active_alerts[0]["hop_depth"] == 2
    assert active_alerts[0]["alert_id"] == first_alert["alert_id"]
    assert active_alerts[0]["target_bid"] == "BID_000000000003"
    third_response = client.post("/ingest", json=third)
    assert third_response.json["alert"] is None
    assert len(client.get("/state").json["alerts"]) == 1


def test_alert_is_visible_before_slow_llm_brief_arrives():
    started = threading.Event()
    release = threading.Event()

    def slow_generator(payload, scanner=None, **kwargs):
        started.set()
        release.wait(timeout=2)
        return kwargs["prompt_json"], {
            "summary": "The blind chain was reviewed.",
            "suspected_pattern": "cross-bank chain",
            "risk_level": "unclassified",
            "recommended_action": "Review the evidence.",
            "limits": "Synthetic blind features only.",
            "llm_mode": "local",
        }

    app = create_app(models=DummyModels(), bank_urls={}, llm_generator=slow_generator)
    client = app.test_client()
    client.post(
        "/ingest",
        json=message("A", 1, 1000, "BID_000000000001", "BID_000000000002"),
    )

    response = client.post(
        "/ingest",
        json=message("B", 1, 5000, "BID_000000000002", "BID_000000000003"),
    )
    assert response.status_code == 200
    alert = response.json["alert"]
    assert alert["llm_status"] == "pending"
    assert json.loads(alert["llm_prompt"]) == alert["llm_payload"]
    assert started.wait(timeout=1)
    assert client.get("/state").json["alerts"][0]["llm_status"] == "pending"

    release.set()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        current = client.get("/state").json["alerts"][0]
        if current["llm_status"] == "complete":
            break
        time.sleep(0.01)
    assert current["llm_status"] == "complete"
    assert current["llm_brief"]["llm_mode"] == "local"
