import pytest

pytest.importorskip("numpy")

from bench.bench import (
    _cascade_outcome_metrics,
    _distribution,
    _live_load_test,
    _render_readme_results,
    _summary,
)


def test_distribution_reports_observed_percentiles_and_sample_count():
    result = _distribution([1.0, 2.0, 3.0, 4.0])
    assert result["p50"] == pytest.approx(2.5)
    assert result["p95"] == pytest.approx(3.85)
    assert result["p99"] == pytest.approx(3.97)
    assert result["n"] == 4


def test_summary_aggregates_actual_seed_values():
    result = _summary([0.25, 0.75])
    assert result["mean"] == 0.5
    assert result["std"] == 0.25
    assert result["per_seed"] == [0.25, 0.75]


def test_cascade_outcomes_use_ground_truth_value_and_alert_timestamps():
    rows = [
        {"chain_id": "c1", "attack_type": "CROSS_BANK_MULE_CASCADE", "hop_index": 1,
         "ts_ms": 100, "event_id": "e1", "ground_truth_amount": 100.0, "linked": {"hop_depth": 1}},
        {"chain_id": "c1", "attack_type": "CROSS_BANK_MULE_CASCADE", "hop_index": 2,
         "ts_ms": 150, "event_id": "e2", "ground_truth_amount": 90.0, "linked": {"hop_depth": 2}},
        {"chain_id": "c1", "attack_type": "CROSS_BANK_MULE_CASCADE", "hop_index": 3,
         "ts_ms": 220, "event_id": "e3", "ground_truth_amount": 80.0, "linked": {"hop_depth": 3}},
    ]
    scores = [
        {"p_cascade": 0.1, "anom": 0.1},
        {"p_cascade": 0.9, "anom": 0.1},
        {"p_cascade": 0.1, "anom": 0.1},
    ]

    result = _cascade_outcome_metrics(rows, scores, {"T_cascade": 0.5, "T_anom": 0.95})

    assert result["value_still_stoppable_pct"] == [80.0]
    assert result["time_to_flag_ms"] == [50.0]
    assert result["alert_to_next_hop_gap_ms"] == [70.0]


def test_live_load_test_replays_each_speed_and_summarizes_observed_alert_metrics(monkeypatch):
    speeds = []

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self.payload

    def fake_post(url, *, headers=None, json=None, timeout):
        if url.endswith("/api/reset"):
            return Response({"reset": True})
        if url.endswith("/benchmark/heist"):
            speeds.append(json["speed_factor"])
            return Response({"alert_received": True, "hold_delivered": True, "end_to_end_ms": 2.0})
        if url.endswith("/api/negative-control"):
            return Response({"negative_control": "caught"})
        raise AssertionError(url)

    def fake_get(url, *, timeout):
        if url.endswith("/api/metrics"):
            return Response({"decision_latency_ms": [1.0, 3.0]})
        if url.endswith("/api/audit"):
            return Response({"messages_scanned": 3, "bytes_scanned": 60, "canary_hits": 0,
                             "validator_blocks": 0})
        raise AssertionError(url)

    monkeypatch.setattr("bench.bench.requests.post", fake_post)
    monkeypatch.setattr("bench.bench.requests.get", fake_get)

    end_to_end, load_test, audit = _live_load_test(2, "http://dash", "http://replay", "token")

    assert speeds == [1, 1, 5, 5, 10, 10]
    assert end_to_end["n"] == 2
    assert load_test["decision_latency_ms"]["1x"]["n"] == 4
    assert load_test["decision_latency_ms"]["10x"]["p50"] == 2.0
    assert load_test["end_to_end_ms"]["5x"]["n"] == 2
    assert audit["negative_control_caught"] is True


def test_readme_results_snapshot_comes_from_all_requested_result_sections():
    document = {
        "meta": {"host": {"arch": "x86_64"}, "seeds": [0, 1]},
        "detection": {
            "recall_at_1pct_fpr": {"mean": 0.8, "std": 0.1},
            "zero_day_slow_drip_recall_at_1pct_fpr": {"mean": 0.5, "std": 0.2},
            "value_still_stoppable_pct": {"mean": 75.0, "std": 5.0},
            "time_to_flag_ms": {"mean": 10.0, "std": 2.0},
            "alert_to_next_hop_gap_ms": {"mean": 20.0, "std": 3.0},
        },
        "load_test": {
            "decision_latency_ms": {
                speed: {"p50": 1.0, "p95": 2.0, "p99": 3.0}
                for speed in ("1x", "5x", "10x")
            }
        },
        "ablation": {
            name: {"recall_at_1pct_fpr": {"mean": 0.6, "std": 0.1}}
            for name in ("raw_only", "raw_plus_compression", "compression_only")
        },
    }

    summary = _render_readme_results(document)

    assert "Cascade recall at 1% FPR: 80.00 ± 10.00%" in summary
    assert "Slow-drip zero-day recall (Model 1 alone)" in summary
    assert "Value still stoppable" in summary
    assert all(f"at {speed}:" in summary for speed in ("1x", "5x", "10x"))
    assert "Compression-only ablation recall" in summary
    assert "results.json" in summary


def test_readme_snapshot_does_not_claim_dfltcc_from_the_cpu_flag_alone():
    summary = _render_readme_results(
        {
            "meta": {"host": {"arch": "s390x"}, "seeds": [0]},
            "zlib_bench": {
                "dflt_flag": True,
                "zlib_dfltcc_support": False,
                "hw_toggle_env": None,
                "hardware_acceleration_claimable": False,
                "rows": [
                    {"buffer_bytes": 1024, "hw_on_us": None, "hw_off_us": None, "baseline_us": 4.0}
                ],
            },
        }
    )

    assert "DFLTCC acceleration: not verified" in summary
    assert "host flag: present" in summary
