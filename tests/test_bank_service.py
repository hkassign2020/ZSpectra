import pytest

pytest.importorskip("flask")
pytest.importorskip("requests")

from bank.service import create_app
from bank.sensor import bid_for


def event():
    return {
        "event_id": "evt-1",
        "ts_ms": 1000,
        "bank": "A",
        "src_account": "A:000001",
        "dst_account": "B:000002",
        "amount": 125.0,
        "tx_type": "TRANSFER",
        "channel": "APP",
        "aborted_retries_last_5s": 0,
        "customer_name": "Fake Name",
        "national_id": "FAKE-ID",
        "memo": "synthetic only",
    }


def test_event_path_sends_only_validated_blind_payload(monkeypatch):
    sent = {}

    def post(url, json, timeout):
        sent.update({"url": url, "json": json, "timeout": timeout})

        class Response:
            status_code = 200

            @staticmethod
            def json():
                return {"accepted": True}

            @staticmethod
            def raise_for_status():
                return None

        return Response()

    monkeypatch.setattr("bank.service.requests.post", post)
    app = create_app(bank="A", key=b"test-key", mesh_url="http://mesh:8000", accounts=[])
    response = app.test_client().post("/event", json=event())

    assert response.status_code == 200
    assert set(sent["json"]) == {
        "bank", "seq", "t_ms", "src_bid", "dst_bid", "amt_bin", "f", "sensor_us"
    }
    assert "Fake Name" not in repr(sent["json"])
    assert "FAKE-ID" not in repr(sent["json"])


def test_hold_only_changes_local_account_state():
    app = create_app(
        bank="B",
        key=b"test-key",
        mesh_url="http://mesh:8000",
        accounts=[{"account": "B:000002", "customer_name": "Fake Name"}],
    )
    client = app.test_client()
    local_bid = bid_for("B:000002", b"test-key")
    other_bid = bid_for("A:000001", b"test-key")

    local = client.post("/hold", json={"bid": local_bid, "alert_id": "AL_01234567"})
    remote = client.post("/hold", json={"bid": other_bid, "alert_id": "AL_01234567"})

    assert local.json["status"] == "hold recommended, awaiting operator"
    assert remote.json["status"] == "ignored"


def test_reset_clears_bank_hold_and_audit_state():
    app = create_app(
        bank="C",
        key=b"test-key",
        mesh_url="http://mesh:8000",
        accounts=[{"account": "C:000002", "customer_name": "Fake Name"}],
    )
    client = app.test_client()
    bid = bid_for("C:000002", b"test-key")
    client.post("/hold", json={"bid": bid, "alert_id": "AL_01234567"})
    assert app.config["HOLD_STATE"]
    assert app.config["SCANNER"].messages > 0

    response = client.post("/reset")

    assert response.json == {"reset": True}
    assert app.config["HOLD_STATE"] == {}
    assert app.config["SCANNER"].messages == 0
