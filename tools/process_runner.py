"""Run the demo services as localhost-bound processes when Docker is unavailable."""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
RUN_DIR = ROOT / ".run"
LOG_DIR = RUN_DIR / "logs"
PID_FILE = RUN_DIR / "processes.json"


def _base_env() -> dict[str, str]:
    env = os.environ.copy()
    for name in ("CONSORTIUM_KEY", "LLM_API_KEY", "REPLAY_TOKEN", "OPERATOR_TOKEN"):
        env.pop(name, None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    env["BIND_HOST"] = "127.0.0.1"
    return env


def _services() -> list[tuple[str, str, int, dict[str, str]]]:
    required_key = os.environ.get("CONSORTIUM_KEY", "")
    replay_token = os.environ.get("REPLAY_TOKEN", "")
    if not required_key:
        raise RuntimeError("CONSORTIUM_KEY must be set before process-mode startup")
    if not replay_token:
        raise RuntimeError("REPLAY_TOKEN must be set before process-mode startup")

    bank_urls = {
        "A": "http://127.0.0.1:8001",
        "B": "http://127.0.0.1:8002",
        "C": "http://127.0.0.1:8003",
    }
    common = _base_env()
    services: list[tuple[str, str, int, dict[str, str]]] = []

    mesh_env = common.copy()
    mesh_env.update(
        {
            "MODEL_FILE": str(ROOT / "models" / "fraud_models.pkl"),
            "THRESHOLDS_FILE": str(ROOT / "models" / "thresholds.json"),
            "CANARY_INDEX_FILE": str(ROOT / "demo-data" / "canary-index.json"),
            "BANK_A_URL": bank_urls["A"],
            "BANK_B_URL": bank_urls["B"],
            "BANK_C_URL": bank_urls["C"],
            "PORT": "8000",
        }
    )
    for name in ("LLM_API_URL", "LLM_API_KEY", "LLM_API_MODEL", "LLM_API_MODE"):
        if name in os.environ:
            mesh_env[name] = os.environ[name]
    services.append(("mesh", "mesh.service", 8000, mesh_env))

    for bank, port in (("A", 8001), ("B", 8002), ("C", 8003)):
        bank_env = common.copy()
        bank_env.update(
            {
                "BANK_CODE": bank,
                "CONSORTIUM_KEY": required_key,
                "MESH_URL": "http://127.0.0.1:8000",
                "PORT": str(port),
                "ACCOUNTS_FILE": str(ROOT / "demo-data" / "accounts" / f"accounts-{bank}.json"),
                "CANARY_INDEX_FILE": str(ROOT / "demo-data" / "canary-index.json"),
            }
        )
        if os.environ.get("OPERATOR_TOKEN"):
            bank_env["OPERATOR_TOKEN"] = os.environ["OPERATOR_TOKEN"]
        services.append((f"bank-{bank}", "bank.service", port, bank_env))

    replayer_env = common.copy()
    replayer_env.update(
        {
            "REPLAY_TOKEN": replay_token,
            "REPLAY_SPEED": os.environ.get("REPLAY_SPEED", "1"),
            "HEIST_ACCOUNTS_FILE": str(ROOT / "demo-data" / "heist-accounts.json"),
            "EVENTS_FILE": str(ROOT / "demo-data" / "events.jsonl"),
            "BANK_A_URL": bank_urls["A"],
            "BANK_B_URL": bank_urls["B"],
            "BANK_C_URL": bank_urls["C"],
            "PORT": "8081",
        }
    )
    services.append(("replayer", "tools.replay_service", 8081, replayer_env))

    dash_env = common.copy()
    dash_env.update(
        {
            "MESH_URL": "http://127.0.0.1:8000",
            "REPLAYER_URL": "http://127.0.0.1:8081",
            "REPLAY_TOKEN": replay_token,
            "RESULTS_FILE": str(ROOT / "results.json"),
            "PLATFORM_EVIDENCE_FILE": str(ROOT / "demo-data" / "platform-evidence.json"),
            "BANK_A_URL": bank_urls["A"],
            "BANK_B_URL": bank_urls["B"],
            "BANK_C_URL": bank_urls["C"],
            "PORT": "8080",
        }
    )
    services.append(("dashboard", "dash.app", 8080, dash_env))
    return services


def _read_processes() -> dict[str, dict[str, Any]]:
    try:
        value = json.loads(PID_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_processes(processes: dict[str, dict[str, Any]]) -> None:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    temporary = PID_FILE.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(processes, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(PID_FILE)


def _matches_module(pid: int, module: str) -> bool:
    command_line = Path(f"/proc/{pid}/cmdline")
    try:
        value = command_line.read_bytes().replace(b"\0", b" ").decode("utf-8", errors="replace")
    except OSError:
        return False
    return f"-m {module}" in value


def stop() -> int:
    processes = _read_processes()
    to_stop = []
    for name, value in processes.items():
        try:
            pid = int(value["pid"])
            module = str(value["module"])
        except (KeyError, TypeError, ValueError):
            continue
        if _matches_module(pid, module):
            to_stop.append((name, pid))

    for _name, pid in reversed(to_stop):
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and any(
        _matches_module(pid, str(processes.get(name, {}).get("module", "")))
        for name, pid in to_stop
    ):
        time.sleep(0.1)
    for name, pid in reversed(to_stop):
        module = str(processes[name]["module"])
        if _matches_module(pid, module):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    if PID_FILE.exists():
        PID_FILE.unlink()
    print(json.dumps({"stopped": [name for name, _pid in to_stop]}))
    return 0


def _wait_ready(pid: int, module: str, port: int) -> bool:
    url = f"http://127.0.0.1:{port}" + ("/api/health" if module == "dash.app" else "/health")
    for _ in range(60):
        if not _matches_module(pid, module):
            return False
        try:
            with urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return True
        except (OSError, URLError):
            pass
        time.sleep(0.5)
    return False


def start() -> int:
    stop()
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    services = _services()
    processes: dict[str, dict[str, Any]] = {}
    _write_processes(processes)

    try:
        for name, module, port, env in services:
            log_path = LOG_DIR / f"{name}.log"
            with log_path.open("ab") as log_stream:
                process = subprocess.Popen(
                    [sys.executable, "-m", module],
                    cwd=ROOT,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=log_stream,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    close_fds=True,
                )
            processes[name] = {"pid": process.pid, "module": module}
            _write_processes(processes)
            if not _wait_ready(process.pid, module, port):
                tail = log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
                raise RuntimeError(f"{name} failed to become ready on port {port}. Log tail:\n{tail}")
        print(json.dumps({"started": list(processes), "mode": "process", "dashboard": "http://127.0.0.1:8080"}))
        return 0
    except Exception:
        stop()
        raise


def status() -> int:
    processes = _read_processes()
    result = {}
    for name, value in processes.items():
        try:
            pid = int(value["pid"])
            module = str(value["module"])
            result[name] = "running" if _matches_module(pid, module) else "stopped"
        except (KeyError, TypeError, ValueError):
            result[name] = "unknown"
    print(json.dumps({"mode": "process", "services": result}, indent=2, sort_keys=True))
    return 0 if result and all(item == "running" for item in result.values()) else 1


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in {"start", "stop", "status"}:
        print("Usage: python -m tools.process_runner start|stop|status", file=sys.stderr)
        return 2
    action = sys.argv[1]
    if action == "start":
        return start()
    if action == "stop":
        return stop()
    return status()


if __name__ == "__main__":
    raise SystemExit(main())
