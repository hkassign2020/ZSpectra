from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_only_bank_container_receives_the_consortium_key():
    runner = (ROOT / "run.sh").read_text(encoding="utf-8")
    mesh_section = runner.split('docker run -d --name zs-mesh', 1)[1].split("for bank in A B C", 1)[0]
    bank_section = runner.split("for bank in A B C", 1)[1].split('docker run -d --name zs-replayer', 1)[0]
    replayer_section = runner.split('docker run -d --name zs-replayer', 1)[1].split("start_dashboard()", 1)[0]
    dashboard_section = runner.split("start_dashboard()", 1)[1].split("capture_platform()", 1)[0]

    assert "CONSORTIUM_KEY" not in mesh_section
    assert "CONSORTIUM_KEY" in bank_section
    assert "CONSORTIUM_KEY" not in replayer_section
    assert "CONSORTIUM_KEY" not in dashboard_section


def test_runtime_dependencies_are_limited_to_the_frozen_stack():
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    assert {line.lower() for line in requirements} == {"numpy", "scikit-learn", "flask", "requests"}
    development = (ROOT / "requirements-dev.txt").read_text(encoding="utf-8").splitlines()
    assert development == ["-r requirements.txt", "pytest"]


def test_docker_context_excludes_local_data_and_mesh_receives_only_the_digest_index():
    ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert {"demo-data", "models", ".venv", "results.json"}.issubset(set(ignored))
    runner = (ROOT / "run.sh").read_text(encoding="utf-8")
    mesh_section = runner.split('docker run -d --name zs-mesh', 1)[1].split("for bank in A B C", 1)[0]
    replayer_section = runner.split('docker run -d --name zs-replayer', 1)[1].split("start_dashboard()", 1)[0]
    assert "canary-index.json:/data/canary-index.json:ro" in mesh_section
    assert "canaries.json" not in mesh_section
    assert "demo-data:/data" not in replayer_section
    assert "accounts/accounts-" not in replayer_section
