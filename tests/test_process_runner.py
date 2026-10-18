from tools.process_runner import _services


def test_process_mode_keeps_the_consortium_key_only_in_bank_environments(monkeypatch):
    monkeypatch.setenv("CONSORTIUM_KEY", "test-consortium-key")
    monkeypatch.setenv("REPLAY_TOKEN", "test-replay-token")
    monkeypatch.setenv("LLM_URL", "http://llm-host:8082/v1/chat/completions")
    monkeypatch.setenv("LLM_MODEL", "granite-local")
    monkeypatch.setenv("LLM_API_KEY", "test-llm-key")

    services = {name: env for name, _module, _port, env in _services()}

    assert "CONSORTIUM_KEY" not in services["mesh"]
    assert services["bank-A"]["CONSORTIUM_KEY"] == "test-consortium-key"
    assert services["bank-B"]["CONSORTIUM_KEY"] == "test-consortium-key"
    assert services["bank-C"]["CONSORTIUM_KEY"] == "test-consortium-key"
    assert services["bank-A"]["TRAINING_METADATA_FILE"].endswith("training.json")
    assert all("CONSORTIUM_KEY" not in services[name] for name in ("replayer", "dashboard"))
    assert services["mesh"]["LLM_API_KEY"] == "test-llm-key"
    assert services["mesh"]["LLM_URL"] == "http://llm-host:8082/v1/chat/completions"
    assert services["mesh"]["LLM_MODEL"] == "granite-local"
    assert all(
        all(name not in services[service] for name in ("LLM_API_KEY", "LLM_URL", "LLM_MODEL"))
        for service in ("bank-A", "bank-B", "bank-C", "replayer", "dashboard")
    )
    assert "LLM_API_KEY" not in services["dashboard"]
    assert all(env["BIND_HOST"] == "127.0.0.1" for env in services.values())
