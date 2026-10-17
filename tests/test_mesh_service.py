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


def test_heist_alerts_at_hop_two_and_recommends_hold_for_received_account(monkeypatch):
    sent_holds = []

    def post(url, json, timeout):
        sent_holds.append((url, json))

        class Response:
            status_code = 200
            ok = True

            @staticmethod
            def raise_for_status():
                return None

        return Response()

    monkeypatch.setattr("mesh.service.requests.post", post)
    app = create_app(
        models=DummyModels(),
        bank_urls={bank: f"http://bank-{bank}" for bank in ("A", "B", "C")},
        llm_generator=lambda payload, scanner=None: (
            "{}",
            {
                "summary": "Synthetic cascade detected.",
                "suspected_pattern": "cross-bank mule cascade",
                "risk_level": "high",
                "recommended_action": "review hold recommendation",
                "limits": "Synthetic demonstration.",
                "llm_mode": "template",
            },
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
    assert len(sent_holds) == 3
    assert all(payload["bid"] == "BID_000000000003" for _, payload in sent_holds)


def test_reset_clears_mesh_chain_and_alert_state():
    app = create_app(
        models=DummyModels(),
        bank_urls={},
        llm_generator=lambda payload, scanner=None: ("{}", {"llm_mode": "template"}),
    )
    client = app.test_client()
    client.post(
        "/ingest",
        json=message("A", 1, 1000, "BID_000000000001", "BID_000000000002"),
    )

    assert client.post("/reset").status_code == 200
    assert client.get("/state").json["events"] == []
    assert app.config["SCANNER"].messages == 0
    assert app.config["VALIDATOR"].blocked_count == 0


def test_negative_control_message_is_caught_by_the_shared_scanner():
    app = create_app(
        models=DummyModels(),
        bank_urls={},
        llm_generator=lambda payload, scanner=None: ("{}", {"llm_mode": "template"}),
    )

    response = app.test_client().post("/audit/negative-control")

    assert response.json["negative_control"] == "caught"
    assert response.json["hits"] > 0
