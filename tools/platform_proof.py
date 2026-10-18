"""Capture platform evidence on the LinuxONE host used for the demo."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from pathlib import Path
import argparse


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


def _zlib_dfltcc_support() -> bool | None:
    """Inspect the loaded user-space zlib for IBM's documented marker."""
    if platform.system() != "Linux":
        return None
    try:
        import zlib

        zlib.compress(b"zspectra-dfltcc-probe", level=1)
        maps = Path("/proc/self/maps").read_text(encoding="utf-8", errors="replace")
    except (OSError, ImportError):
        return None
    libraries = {
        line.split()[-1]
        for line in maps.splitlines()
        if "/" in line and "libz.so" in line.split()[-1]
    }
    if not libraries:
        return None
    for library in libraries:
        try:
            if b"DFLTCC" in Path(library).read_bytes():
                return True
        except OSError:
            continue
    return False


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
            "zlib_dfltcc_support": _zlib_dfltcc_support(),
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


def dfltcc_ready(evidence: dict[str, object]) -> bool:
    """Require s390x, the CPU feature, and DFLTCC-capable loaded libz."""
    host = evidence.get("host", {})
    if not isinstance(host, dict):
        return False
    return (
        host.get("architecture") == "s390x"
        and host.get("dflt_flag") is True
        and host.get("zlib_dfltcc_support") is True
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--require-dfltcc",
        action="store_true",
        help="fail unless the host has s390x, the dflt CPU flag, and DFLTCC-capable libz",
    )
    args = parser.parse_args()
    evidence = collect()
    print(json.dumps(evidence, indent=2, sort_keys=True))
    if args.require_dfltcc:
        return 0 if dfltcc_ready(evidence) else 2
    return 0 if platform.system() == "Linux" else 2


if __name__ == "__main__":
    raise SystemExit(main())
