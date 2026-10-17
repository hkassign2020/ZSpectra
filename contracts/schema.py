"""Frozen wire contracts shared by banks, mesh, and the LLM client."""

SENSOR_FEATURES = (
    "log_amount_bin",
    "log1p_gap_ms",
    "aborts_5s",
    "tx_count_60s",
    "distinct_dest_60s",
    "window_ncd",
    "compression_ratio",
    "window_entropy",
)

MESH_FEATURES = (
    "hop_depth",
    "dt_prev_hop_ms",
    "amount_carry_over",
    "banks_in_chain",
    "chain_span_ms",
)

BANK_TO_MESH_KEYS = frozenset(
    {"bank", "seq", "t_ms", "src_bid", "dst_bid", "amt_bin", "f", "sensor_us"}
)
MESH_TO_LLM_KEYS = frozenset({"chain", "p_cascade", "anom", "evidence"})
MESH_TO_BANK_KEYS = frozenset({"bid", "alert_id"})

CHANNEL_ALLOWLISTS = {
    "bank_to_mesh": BANK_TO_MESH_KEYS,
    "mesh_to_llm": MESH_TO_LLM_KEYS,
    "mesh_to_bank": MESH_TO_BANK_KEYS,
}

BID_PREFIX = "BID_"
BID_HEX_LENGTH = 12
