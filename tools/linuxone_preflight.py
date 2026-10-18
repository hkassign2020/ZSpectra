"""Check host Python packages and Docker readiness before a LinuxONE run."""

from __future__ import annotations

import argparse
import importlib
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile


MODULES = {
    "numpy": "numpy",
    "scikit-learn": "sklearn",
    "scipy": "scipy",
    "Flask": "flask",
    "requests": "requests",
}
ROOT = Path(__file__).resolve().parents[1]


def _command(command: list[str]) -> dict[str, object]:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"available": False, "detail": str(exc)}
    return {
        "available": result.returncode == 0,
        "returncode": result.returncode,
        "detail": (result.stdout + result.stderr).strip(),
    }


def _installed_packages() -> dict[str, object]:
    packages = {}
    for distribution, module_name in MODULES.items():
        try:
            importlib.import_module(module_name)
            try:
                package_version = version(distribution)
            except PackageNotFoundError:
                package_version = "unknown"
            packages[distribution] = {"available": True, "version": package_version}
        except Exception as exc:  # report broken native imports as unavailable
            packages[distribution] = {"available": False, "detail": f"{type(exc).__name__}: {exc}"}
    return packages


def _pip_install_check() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="zspectra-pip-check-") as install_dir:
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "--disable-pip-version-check",
                    "--no-warn-script-location",
                    "--target",
                    install_dir,
                    "-r",
                    str(ROOT / "requirements.txt"),
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=900,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"available": False, "detail": f"{type(exc).__name__}: {exc}"}
        if result.returncode != 0:
            return {
                "available": False,
                "returncode": result.returncode,
                "detail": (result.stdout + result.stderr)[-4000:],
            }
        env = os.environ.copy()
        env["PYTHONPATH"] = install_dir + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        imports = subprocess.run(
            [sys.executable, "-c", "import numpy, scipy, sklearn, flask, requests; print(numpy.__version__, scipy.__version__, sklearn.__version__)"],
            capture_output=True,
            text=True,
            check=False,
            env=env,
            timeout=30,
        )
        return {
            "available": imports.returncode == 0,
            "returncode": imports.returncode,
            "detail": imports.stdout.strip() if imports.returncode == 0 else imports.stderr[-4000:],
        }


def _pip_wheel_check() -> dict[str, object]:
    """Check wheel availability without allowing pip to compile from source."""
    with tempfile.TemporaryDirectory(prefix="zspectra-wheel-check-") as wheel_dir:
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "download",
                    "--disable-pip-version-check",
                    "--only-binary=:all:",
                    "--dest",
                    wheel_dir,
                    "-r",
                    str(ROOT / "requirements-ml.txt"),
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=180,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"available": False, "detail": f"{type(exc).__name__}: {exc}"}
        return {
            "available": result.returncode == 0,
            "returncode": result.returncode,
            "detail": (result.stdout + result.stderr)[-4_000:],
            "scope": "numpy, scikit-learn and their dependencies; binary wheels only",
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--install-check",
        action="store_true",
        help="try installing requirements.txt into a temporary directory and import them",
    )
    parser.add_argument(
        "--wheel-check",
        action="store_true",
        help="check whether NumPy/scikit-learn dependency wheels exist for this host without compiling",
    )
    args = parser.parse_args()

    docker_cli = shutil.which("docker")
    report = {
        "host": {"system": platform.system(), "architecture": platform.machine(), "byte_order": sys.byteorder},
        "python": sys.version.split()[0],
        "installed_packages": _installed_packages(),
        "docker_cli": _command([docker_cli, "--version"]) if docker_cli else {"available": False, "detail": "docker command not found"},
        "docker_daemon": _command([docker_cli, "info", "--format", "{{.ServerVersion}}"])
        if docker_cli
        else {"available": False, "detail": "docker command not found"},
    }
    if args.install_check:
        report["clean_install_check"] = _pip_install_check()
    if args.wheel_check:
        report["binary_wheel_check"] = _pip_wheel_check()
    print(json.dumps(report, indent=2, sort_keys=True))

    missing = [name for name, status in report["installed_packages"].items() if not status["available"]]
    if missing:
        return 1
    if args.install_check and not report["clean_install_check"]["available"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
