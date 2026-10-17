"""Capture platform evidence on the LinuxONE host used for the demo."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from pathlib import Path


def _read_text(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _run(command: list[str]) -> dict[str, object]:
    try:
        result = subprocess.run(
            command, check=False, capture_output=True, text=True, timeout=15
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"command": command, "returncode": None, "output": str(exc)}
    return {
        "command": command,
        "returncode": result.returncode,
        "output": (result.stdout + result.stderr).strip(),
    }


def collect() -> dict[str, object]:
    """Return raw platform evidence without claiming unsupported features."""
    if platform.system() != "Linux":
        return {
            "status": "unavailable",
            "reason": "Run this command on the LinuxONE Linux host.",
            "observed_host": {
                "system": platform.system(),
                "architecture": platform.machine(),
                "byte_order": sys.byteorder,
            },
        }

    cpuinfo = _read_text("/proc/cpuinfo")
    sysinfo = _read_text("/proc/sysinfo")
    feature_lines = [
        line.strip()
        for line in cpuinfo.splitlines()
        if line.lower().startswith("features")
    ]
    return {
        "status": "captured",
        "host": {
            "architecture": platform.machine(),
            "byte_order": sys.byteorder,
            "model_name": platform.processor() or platform.uname().machine,
            "dflt_flag": any("dflt" in line.lower() for line in feature_lines),
            "cpu_features": feature_lines,
            "sysinfo": [
                line.strip()
                for line in sysinfo.splitlines()
                if any(key in line for key in ("Manufacturer", "Type", "LPAR", "VM00"))
            ],
        },
        "commands": [
            _run(["lscpu"]),
            _run(["grep", "-m1", "features", "/proc/cpuinfo"]),
            _run(["grep", "-E", "Manufacturer|Type|LPAR|VM00", "/proc/sysinfo"]),
            _run(["free", "-h"]),
            _run(["docker", "ps"]),
        ],
    }


def main() -> int:
    print(json.dumps(collect(), indent=2, sort_keys=True))
    return 0 if platform.system() == "Linux" else 2


if __name__ == "__main__":
    raise SystemExit(main())
