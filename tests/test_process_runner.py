from tools.process_runner import _services


def test_process_mode_keeps_the_consortium_key_only_in_bank_environments(monkeypatch):
    monkeypatch.setenv("CONSORTIUM_KEY", "test-consortium-key")
    monkeypatch.setenv("REPLAY_TOKEN", "test-replay-token")
    monkeypatch.setenv("LLM_API_KEY", "test-llm-key")

    services = {name: env for name, _module, _port, env in _services()}

    assert "CONSORTIUM_KEY" not in services["mesh"]
    assert services["bank-A"]["CONSORTIUM_KEY"] == "test-consortium-key"
    assert services["bank-B"]["CONSORTIUM_KEY"] == "test-consortium-key"
    assert services["bank-C"]["CONSORTIUM_KEY"] == "test-consortium-key"
    assert all("CONSORTIUM_KEY" not in services[name] for name in ("replayer", "dashboard"))
    assert services["mesh"]["LLM_API_KEY"] == "test-llm-key"
    assert all("LLM_API_KEY" not in services[name] for name in ("bank-A", "bank-B", "bank-C", "replayer", "dashboard"))
    assert "LLM_API_KEY" not in services["dashboard"]
    assert all(env["BIND_HOST"] == "127.0.0.1" for env in services.values())
