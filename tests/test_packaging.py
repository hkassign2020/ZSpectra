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
    assert "DFLTCC" not in mesh_section
    assert "DFLTCC" in bank_section
    assert "TRAINING_METADATA_FILE=/data/training.json" in bank_section
    assert "models/training.json:/data/training.json:ro" in bank_section


def test_runtime_dependencies_are_limited_to_the_frozen_stack():
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    assert {line.lower() for line in requirements} == {"numpy", "scikit-learn", "flask", "requests"}
    development = (ROOT / "requirements-dev.txt").read_text(encoding="utf-8").splitlines()
    assert development == ["-r requirements.txt", "pytest"]


def test_only_the_mesh_image_installs_numpy_and_scikit_learn():
    for name in ("bank", "dash", "replayer"):
        dockerfile = (ROOT / "docker" / f"Dockerfile.{name}").read_text(encoding="utf-8")
        assert "requirements-core.txt" in dockerfile
        assert "requirements-ml.txt" not in dockerfile
    mesh = (ROOT / "docker" / "Dockerfile.mesh").read_text(encoding="utf-8")
    if "requirements-core.txt" in mesh:
        assert "requirements-ml.txt" in mesh
    else:
        # LinuxONE may use distro-provided wheels instead of pip installs.
        normalized = mesh.lstrip()
        assert normalized.startswith("FROM ubuntu:")
        assert "python3-numpy" in mesh
        assert "python3-sklearn" in mesh
        assert "python3-flask" in mesh
        assert "python3-requests" in mesh


def test_docker_context_excludes_local_data_and_mesh_receives_only_the_digest_index():
    ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert {"demo-data", "models", ".venv", "results.json"}.issubset(set(ignored))
    runner = (ROOT / "run.sh").read_text(encoding="utf-8")
    mesh_section = runner.split('docker run -d --name zs-mesh', 1)[1].split("for bank in A B C", 1)[0]
    replayer_section = runner.split('docker run -d --name zs-replayer', 1)[1].split("start_dashboard()", 1)[0]
    assert "canary-index.json:/data/canary-index.json:ro" in mesh_section
    assert "--add-host=host.docker.internal:host-gateway" in mesh_section
    assert "LLM_URL=http://host.docker.internal:8082/v1/chat/completions" in mesh_section
    assert "canaries.json" not in mesh_section
    assert "demo-data:/data" not in replayer_section
    assert "accounts/accounts-" not in replayer_section


def test_results_json_is_trackable_for_submission():
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "results.json" not in ignored


def test_linuxone_preflight_reports_binary_wheel_availability_separately():
    preflight = (ROOT / "tools" / "linuxone_preflight.py").read_text(encoding="utf-8")
    assert '"--wheel-check"' in preflight
    assert "--only-binary=:all:" in preflight
    assert "requirements-ml.txt" in preflight


def test_each_docker_image_checks_its_runtime_imports_during_build():
    expected_imports = {
        "bank": ("flask", "requests"),
        "mesh": ("numpy", "scipy", "sklearn", "flask", "requests"),
        "dash": ("flask", "requests"),
        "replayer": ("flask", "requests"),
    }
    for service, modules in expected_imports.items():
        dockerfile = (ROOT / "docker" / f"Dockerfile.{service}").read_text(encoding="utf-8")
        assert "python" in dockerfile and "-c" in dockerfile
        for module in modules:
            assert module in dockerfile


def test_dfltcc_is_opt_in_guarded_and_bank_runtime_checks_its_own_libz():
    runner = (ROOT / "run.sh").read_text(encoding="utf-8")
    bank = (ROOT / "docker" / "Dockerfile.bank").read_text(encoding="utf-8")
    service = (ROOT / "bank" / "service.py").read_text(encoding="utf-8")
    assert 'case "${ZSPECTRA_DFLTCC:-0}"' in runner
    assert "--require-dfltcc" in runner
    assert 'DFLTCC_LEVEL_MASK=${ZSPECTRA_DFLTCC_LEVEL_MASK:-0x007e}' in runner
    assert "tools/platform_proof.py" in bank
    assert "COPY runtime ./runtime" in bank
    assert "COPY runtime ./runtime" in (ROOT / "docker" / "Dockerfile.mesh").read_text(encoding="utf-8")
    assert "dfltcc_ready(collect())" in service
