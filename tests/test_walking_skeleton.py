from types import SimpleNamespace

import pytest

pytest.importorskip("numpy")
pytest.importorskip("sklearn")
pytest.importorskip("flask")
pytest.importorskip("requests")

from bank.service import create_app as create_bank_app
from bank.sensor import bid_for
from bench.bench import (
    ABLATIONS,
    _ablation_recall,
    _decision_latencies,
    _detection_metrics,
    _fit_ablation,
    _validation_chains,
)
from gen.generator import GeneratorConfig
from mesh.service import create_app as create_mesh_app
from tools.heist import build_heist_events
from train import train_and_save


class ClientResponse:
    def __init__(self, response):
        self._response = response
        self.status_code = response.status_code
        self.ok = response.status_code < 400

    def json(self):
        return self._response.get_json()

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError("test client returned an error")


def test_two_hop_walking_skeleton_emits_hold_to_local_bank(monkeypatch, tmp_path):
    key = b"walking-skeleton-test-key"
    monkeypatch.setenv("CONSORTIUM_KEY", key.decode("ascii"))
    monkeypatch.setenv("LLM_API_URL", "")
    monkeypatch.setenv("LLM_API_KEY", "")
    trained = train_and_save(
        config=GeneratorConfig(event_count=6_000),
        seed=17,
        output_dir=tmp_path / "models",
    )
    test_rows = sorted(trained["splits"]["test"], key=lambda row: (row["ts_ms"], row["event_id"]))
    metrics = _detection_metrics(trained["model"], test_rows)
    assert 0.0 <= metrics["recall_at_1pct_fpr"] <= 1.0
    assert len(_decision_latencies(trained["model"], test_rows[:30])) == 30
    validation_chains = _validation_chains(trained["splits"]["B"])
    for name, indices in ABLATIONS.items():
        fitted = _fit_ablation(trained["splits"], validation_chains, indices, 17)
        assert 0.0 <= _ablation_recall(fitted, test_rows, indices) <= 1.0
    local_accounts = {
        "A": [{"account": "A:DEMO_SOURCE", "customer_name": "Synthetic Source"}],
        "B": [{"account": "B:DEMO_RELAY", "customer_name": "Synthetic Relay"}],
        "C": [{"account": "C:DEMO_RECEIVER", "customer_name": "Synthetic Receiver"}],
    }
    bank_apps = {
        bank: create_bank_app(bank, key, "http://mesh:8000", local_accounts[bank])
        for bank in ("A", "B", "C")
    }
    bank_clients = {bank: app.test_client() for bank, app in bank_apps.items()}
    mesh_client_holder = {}

    def bank_to_mesh(url, *, json, timeout):
        return ClientResponse(mesh_client_holder["client"].post("/ingest", json=json))

    # Keep each service's requests adapter separate while preserving its exception type.
    import bank.service as bank_service
    import mesh.service as mesh_service
    import requests

    monkeypatch.setattr(bank_service, "requests", SimpleNamespace(post=bank_to_mesh, RequestException=requests.RequestException))

    def mesh_to_bank(url, *, json, timeout):
        bank = url.split("bank-")[1].split(":", 1)[0].upper()
        return ClientResponse(bank_clients[bank].post("/hold", json=json))

    monkeypatch.setattr(
        mesh_service,
        "requests",
        SimpleNamespace(post=mesh_to_bank, RequestException=requests.RequestException),
    )
    mesh_app = create_mesh_app(
        trained["model"],
        bank_urls={bank: f"http://bank-{bank}:800{index + 1}" for index, bank in enumerate(("A", "B", "C"))},
    )
    mesh_client_holder["client"] = mesh_app.test_client()

    responses = []
    for event in build_heist_events(
        {"A": {"source": "A:DEMO_SOURCE"}, "B": {"relay": "B:DEMO_RELAY"}, "C": {"receiver": "C:DEMO_RECEIVER"}}
    ):
        responses.append(bank_clients[event["bank"]].post("/event", json=event))

    alert = responses[1].get_json()["alert"]
    receiver_bid = bid_for("C:DEMO_RECEIVER", key)
    assert responses[0].status_code == responses[1].status_code == responses[2].status_code == 200
    assert alert["hop_depth"] == 2
    assert alert["hold_rec"]["bid"] == receiver_bid
    assert bank_apps["C"].config["HOLD_STATE"][receiver_bid]["status"] == "hold recommended, awaiting operator"
