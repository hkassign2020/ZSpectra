"""Measure level-six zlib compression with isolated hardware-toggle runs."""

from __future__ import annotations

import json
import math
import os
import platform
import subprocess
import sys
from typing import Any, Iterable

from tools.platform_proof import collect


BUFFER_SIZES = (1_024, 16_384, 262_144, 4_194_304, 67_108_864)
_CHILD_BENCHMARK = r"""
import json, statistics, sys, time, zlib
size, repeats, level = (int(value) for value in sys.argv[1:4])
pattern = bytes(range(251))
data = (pattern * ((size + len(pattern) - 1) // len(pattern)))[:size]
timings = []
for _ in range(repeats):
    started = time.perf_counter_ns()
    compressed = zlib.compress(data, level=level)
    timings.append((time.perf_counter_ns() - started) / 1_000.0)
    del compressed
print(json.dumps({"mean_us": statistics.mean(timings), "runtime_version": zlib.ZLIB_RUNTIME_VERSION}))
"""


def _measure_subprocess(
    size: int,
    repeats: int,
    *,
    toggle_env: str | None = None,
    toggle_value: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run a fresh interpreter so zlib reads toggle variables before import."""
    child_env = os.environ.copy()
    if toggle_env is not None and toggle_value is not None:
        child_env[toggle_env] = toggle_value
    if extra_env:
        child_env.update(extra_env)
    completed = subprocess.run(
        [sys.executable, "-c", _CHILD_BENCHMARK, str(size), str(repeats), "6"],
        capture_output=True,
        text=True,
        check=False,
        env=child_env,
        timeout=300,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "zlib benchmark subprocess failed: "
            + (completed.stderr or completed.stdout)[-2_000:]
        )
    try:
        result = json.loads(completed.stdout)
        mean_us = float(result["mean_us"])
        runtime_version = result["runtime_version"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("zlib benchmark subprocess returned invalid output") from exc
    if not math.isfinite(mean_us) or mean_us < 0:
        raise RuntimeError("zlib benchmark returned an invalid timing")
    return {"mean_us": mean_us, "runtime_version": str(runtime_version)}


def run_zlib_bench(
    *,
    sizes: Iterable[int] = BUFFER_SIZES,
    repeats: int = 3,
    toggle_env: str | None = None,
    toggle_on: str | None = None,
    toggle_off: str | None = None,
) -> dict[str, Any]:
    if type(repeats) is not int or repeats < 1:
        raise ValueError("repeats must be a positive integer")
    normalized_sizes = tuple(sizes)
    if any(type(size) is not int or size < 1 for size in normalized_sizes):
        raise ValueError("buffer sizes must be positive integers")

    evidence = collect()
    host = evidence.get("host", {}) if isinstance(evidence, dict) else {}
    host = host if isinstance(host, dict) else {}
    architecture = host.get("architecture") or platform.machine()
    dflt_flag = host.get("dflt_flag")
    zlib_support = host.get("zlib_dfltcc_support")

    env_name = toggle_env or os.environ.get("ZLIB_HW_TOGGLE_ENV")
    on_value = toggle_on if toggle_on is not None else os.environ.get("ZLIB_HW_TOGGLE_ON")
    off_value = toggle_off if toggle_off is not None else os.environ.get("ZLIB_HW_TOGGLE_OFF")
    if env_name is None and architecture == "s390x" and zlib_support is True:
        # DFLTCC is selected only after the target's loaded zlib reports support.
        env_name = "DFLTCC"
    if env_name == "DFLTCC":
        on_value = "1" if on_value is None else on_value
        off_value = "0" if off_value is None else off_value
    toggle_configured = bool(env_name and on_value is not None and off_value is not None)
    toggle_extra_env: dict[str, str] = {}
    if toggle_configured and env_name == "DFLTCC":
        toggle_extra_env["DFLTCC_LEVEL_MASK"] = os.environ.get(
            "DFLTCC_LEVEL_MASK", "0x007e"
        )

    rows = []
    runtime_version = None
    for size in normalized_sizes:
        if toggle_configured:
            off_result = _measure_subprocess(
                size,
                repeats,
                toggle_env=env_name,
                toggle_value=str(off_value),
                extra_env=toggle_extra_env,
            )
            on_result = _measure_subprocess(
                size,
                repeats,
                toggle_env=env_name,
                toggle_value=str(on_value),
                extra_env=toggle_extra_env,
            )
            runtime_version = runtime_version or off_result["runtime_version"]
            rows.append(
                {
                    "buffer_bytes": size,
                    "hw_on_us": on_result["mean_us"],
                    "hw_off_us": off_result["mean_us"],
                    "baseline_us": None,
                }
            )
        else:
            baseline = _measure_subprocess(size, repeats)
            runtime_version = runtime_version or baseline["runtime_version"]
            rows.append(
                {
                    "buffer_bytes": size,
                    "hw_on_us": None,
                    "hw_off_us": None,
                    "baseline_us": baseline["mean_us"],
                }
            )

    measured_gain = any(
        isinstance(row["hw_on_us"], (int, float))
        and isinstance(row["hw_off_us"], (int, float))
        and row["hw_on_us"] < row["hw_off_us"]
        for row in rows
    )
    claimable = bool(
        toggle_configured
        and env_name == "DFLTCC"
        and architecture == "s390x"
        and dflt_flag is True
        and zlib_support is True
        and measured_gain
    )
    return {
        "runtime_version": runtime_version,
        "platform_architecture": architecture,
        "dflt_flag": dflt_flag,
        "zlib_dfltcc_support": zlib_support,
        "hw_toggle_env": env_name if toggle_configured else None,
        "hw_toggle_on": str(on_value) if toggle_configured else None,
        "hw_toggle_off": str(off_value) if toggle_configured else None,
        "benchmark_dfltcc_level_mask": toggle_extra_env.get("DFLTCC_LEVEL_MASK"),
        "toggle_configured": toggle_configured,
        "toggle_isolation": "fresh_process_per_mode",
        "hardware_acceleration_claimable": claimable,
        "rows": rows,
    }
