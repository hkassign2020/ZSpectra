from contracts.schema import (
    BANK_TO_MESH_KEYS,
    MESH_FEATURES,
    MESH_TO_BANK_KEYS,
    MESH_TO_LLM_KEYS,
    SENSOR_FEATURES,
)


def test_frozen_sensor_feature_order():
    assert SENSOR_FEATURES == (
        "log_amount_bin",
        "log1p_gap_ms",
        "aborts_5s",
        "tx_count_60s",
        "distinct_dest_60s",
        "window_ncd",
        "compression_ratio",
        "window_entropy",
    )


def test_mesh_feature_order():
    assert MESH_FEATURES == (
        "hop_depth",
        "dt_prev_hop_ms",
        "amount_carry_over",
        "banks_in_chain",
        "chain_span_ms",
    )


def test_wire_contract_key_sets_are_frozen():
    assert BANK_TO_MESH_KEYS == frozenset(
        {"bank", "seq", "t_ms", "src_bid", "dst_bid", "amt_bin", "f", "sensor_us"}
    )
    assert MESH_TO_LLM_KEYS == frozenset(
        {"chain", "p_cascade", "anom", "evidence"}
    )
    assert MESH_TO_BANK_KEYS == frozenset({"bid", "alert_id"})
