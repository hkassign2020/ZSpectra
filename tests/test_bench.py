import pytest

pytest.importorskip("numpy")

from bench.bench import _distribution, _summary


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
