import json
from types import SimpleNamespace

from bench import zlib_bench
from bench.zlib_bench import run_zlib_bench


def test_configured_toggle_runs_in_fresh_processes_and_records_comparable_times(monkeypatch):
    calls = []

    def fake_run(command, *, env, capture_output, text, check, timeout):
        calls.append(env.get("DFLTCC"))
        assert env.get("DFLTCC_LEVEL_MASK") == "0x007e"
        value = 8.0 if env.get("DFLTCC") == "0" else 4.0
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"mean_us": value, "runtime_version": "test-zlib"}),
            stderr="",
        )

    monkeypatch.setattr(zlib_bench.subprocess, "run", fake_run)
    monkeypatch.setattr(
        "bench.zlib_bench.collect",
        lambda: {
            "host": {
                "architecture": "s390x",
                "dflt_flag": True,
                "zlib_dfltcc_support": True,
            }
        },
    )

    result = run_zlib_bench(
        sizes=(32,), repeats=2, toggle_env="DFLTCC", toggle_on="1", toggle_off="0"
    )

    assert calls == ["0", "1"]
    assert result["runtime_version"] == "test-zlib"
    assert result["hw_toggle_env"] == "DFLTCC"
    assert result["benchmark_dfltcc_level_mask"] == "0x007e"
    assert result["toggle_configured"] is True
    assert result["hardware_acceleration_claimable"] is True
    assert result["rows"] == [
        {"buffer_bytes": 32, "hw_on_us": 4.0, "hw_off_us": 8.0, "baseline_us": None}
    ]


def test_unconfigured_toggle_reports_baseline_without_calling_it_hardware_off(monkeypatch):
    calls = []

    def fake_run(command, *, env, capture_output, text, check, timeout):
        calls.append(env.get("DFLTCC"))
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"mean_us": 7.5, "runtime_version": "test-zlib"}),
            stderr="",
        )

    monkeypatch.setattr(zlib_bench.subprocess, "run", fake_run)
    monkeypatch.setattr("bench.zlib_bench.collect", lambda: {"host": {"dflt_flag": True}})

    result = run_zlib_bench(sizes=(32,), repeats=1)

    assert len(calls) == 1
    assert result["hw_toggle_env"] is None
    assert result["hardware_acceleration_claimable"] is False
    assert result["rows"] == [
        {"buffer_bytes": 32, "hw_on_us": None, "hw_off_us": None, "baseline_us": 7.5}
    ]
