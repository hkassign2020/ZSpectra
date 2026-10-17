import hashlib

from tools.prepare_demo import demo_ready


def test_prepared_artifacts_are_bound_to_the_configured_key_without_saving_it(tmp_path):
    output = tmp_path / "demo-data"
    models = tmp_path / "models"
    output.mkdir()
    models.mkdir()
    for path in (
        output / "canary-index.json",
        output / "accounts" / "accounts-A.json",
        output / "accounts" / "accounts-B.json",
        output / "accounts" / "accounts-C.json",
        output / "heist-accounts.json",
        output / "events.jsonl",
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
    for path in (models / "fraud_models.pkl", models / "thresholds.json"):
        path.write_text("test", encoding="utf-8")
    (output / ".consortium-key.sha256").write_text(
        hashlib.sha256(b"test-key").hexdigest(), encoding="ascii"
    )

    assert demo_ready("test-key", output, models)
    assert not demo_ready("another-key", output, models)
    assert "test-key" not in (output / ".consortium-key.sha256").read_text(encoding="ascii")
