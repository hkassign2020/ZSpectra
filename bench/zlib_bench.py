"""Measure level-six zlib compression and an explicitly configured platform toggle."""

from __future__ import annotations

import os
import platform
import statistics
import time
import zlib
from typing import Any, Iterable

from tools.platform_proof import collect


BUFFER_SIZES = (1_024, 16_384, 262_144, 4_194_304, 67_108_864)


def _buffer(size: int) -> bytes:
    pattern = bytes(range(251))
    return (pattern * ((size + len(pattern) - 1) // len(pattern)))[:size]


def _measure(data: bytes, repeats: int) -> float:
    timings = []
    for _ in range(repeats):
        started = time.perf_counter_ns()
        compressed = zlib.compress(data, level=6)
        timings.append((time.perf_counter_ns() - started) / 1_000.0)
        del compressed
    return float(statistics.mean(timings))


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
    dflt_flag = host.get("dflt_flag") if isinstance(host, dict) else None
    env_name = toggle_env or os.environ.get("ZLIB_HW_TOGGLE_ENV")
    on_value = toggle_on if toggle_on is not None else os.environ.get("ZLIB_HW_TOGGLE_ON")
    off_value = toggle_off if toggle_off is not None else os.environ.get("ZLIB_HW_TOGGLE_OFF")
    toggle_configured = bool(env_name and on_value is not None and off_value is not None)
    prior_value = os.environ.get(env_name) if env_name else None
    rows = []
    try:
        for size in normalized_sizes:
            data = _buffer(size)
            if toggle_configured:
                os.environ[env_name] = str(off_value)
                off_us = _measure(data, repeats)
                os.environ[env_name] = str(on_value)
                on_us = _measure(data, repeats)
            else:
                off_us = _measure(data, repeats)
                on_us = None
            rows.append(
                {
                    "buffer_bytes": size,
                    "hw_on_us": on_us,
                    "hw_off_us": off_us,
                }
            )
            del data
    finally:
        if env_name:
            if prior_value is None:
                os.environ.pop(env_name, None)
            else:
                os.environ[env_name] = prior_value

    return {
        "runtime_version": zlib.ZLIB_RUNTIME_VERSION,
        "platform_architecture": platform.machine(),
        "dflt_flag": dflt_flag,
        "hw_toggle_env": env_name if toggle_configured else None,
        "rows": rows,
    }
