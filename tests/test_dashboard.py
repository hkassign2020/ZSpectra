import json

import pytest

pytest.importorskip("flask")
pytest.importorskip("requests")

from dash.app import create_app


class FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload

    def raise_for_status(self):
        pass


def test_results_endpoint_reads_only_the_configured_results_file(tmp_path):
    result_path = tmp_path / "results.json"
    result_path.write_text(json.dumps({"sensor_latency_us": {"p50": 12.5}}), encoding="utf-8")
    app = create_app(results_path=result_path, platform_collector=lambda: {"status": "test"})

    response = app.test_client().get("/api/results")

    assert response.status_code == 200
    assert response.json["results"]["sensor_latency_us"]["p50"] == 12.5


def test_heist_button_forwards_only_a_control_request(monkeypatch):
    captured = {}

    def post(url, *, headers, json, timeout):
        captured.update(url=url, headers=headers, payload=json)
        return FakeResponse({"started": True})

    monkeypatch.setattr("dash.app.requests.post", post)
    app = create_app(replayer_url="http://replayer:8081", replayer_token="test-token")

    response = app.test_client().post("/api/heist")

    assert response.status_code == 200
    assert captured == {
        "url": "http://replayer:8081/heist",
        "headers": {"X-Replay-Token": "test-token"},
        "payload": {},
    }
    assert "event" not in captured["payload"]


def test_metrics_endpoint_proxies_mesh_decision_measurements(monkeypatch):
    monkeypatch.setattr(
        "dash.app.requests.get",
        lambda url, timeout: FakeResponse({"decision_latency_ms": [1.25]}),
    )
    app = create_app(mesh_url="http://mesh:8000")

    response = app.test_client().get("/api/metrics")

    assert response.status_code == 200
    assert response.json == {"decision_latency_ms": [1.25]}
