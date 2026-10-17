import pytest

pytest.importorskip("flask")
pytest.importorskip("requests")

from tools.replay_service import create_app


def test_heist_control_requires_token_and_never_returns_raw_event(monkeypatch, tmp_path):
    accounts_path = tmp_path / "accounts.json"
    accounts_path.write_text('{"A":{"source":"A:S"},"B":{"relay":"B:R"},"C":{"receiver":"C:C"}}', encoding="utf-8")
    monkeypatch.setattr("tools.replay_service.run_heist", lambda *args, **kwargs: [{"accepted": True}] * 3)
    app = create_app(
        accounts_path=accounts_path,
        bank_urls={bank: f"http://bank-{bank}" for bank in ("A", "B", "C")},
        token="control-token",
    )
    client = app.test_client()

    rejected = client.post("/heist", json={})
    accepted = client.post("/heist", json={}, headers={"X-Replay-Token": "control-token"})

    assert rejected.status_code == 403
    assert accepted.json == {"started": True, "accepted": True}
    assert "A:S" not in accepted.get_data(as_text=True)


def test_benchmark_route_reports_measured_hop_two_delivery(monkeypatch, tmp_path):
    accounts_path = tmp_path / "accounts.json"
    accounts_path.write_text('{"A":{"source":"A:S"},"B":{"relay":"B:R"},"C":{"receiver":"C:C"}}', encoding="utf-8")

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "alert": {
                    "alert_id": "AL_01234567",
                    "hold_delivery": {"C": "sent"},
                }
            }

    def fake_run_heist(accounts, bank_urls, *, speed_factor, post):
        post("http://bank-b/event", json={"event_id": "HEIST-B-TO-C"}, timeout=10)
        return [{"accepted": True}]

    monkeypatch.setattr("tools.replay_service.run_heist", fake_run_heist)
    monkeypatch.setattr("tools.replay_service.requests.post", lambda *args, **kwargs: Response())
    client = create_app(
        accounts_path=accounts_path,
        token="control-token",
    ).test_client()

    response = client.post("/benchmark/heist", headers={"X-Replay-Token": "control-token"})

    assert response.status_code == 200
    assert response.json["alert_received"] is True
    assert response.json["hold_delivered"] is True
    assert isinstance(response.json["end_to_end_ms"], float)
