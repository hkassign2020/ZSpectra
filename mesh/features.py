"""Mesh-side features derived from the linked blind chain."""

from __future__ import annotations

from contracts.schema import MESH_FEATURES


def mesh_feature_values(event: dict) -> dict[str, float]:
    chain = event["chain"]
    amount_delta = float(chain[-1][3] - chain[-2][3]) if len(chain) > 1 else 0.0
    values = {
        "hop_depth": float(event["hop_depth"]),
        "dt_prev_hop_ms": float(event["dt_prev_hop_ms"]),
        "amount_carry_over": amount_delta,
        "banks_in_chain": float(len({hop[0] for hop in chain})),
        "chain_span_ms": float(event["chain_span_ms"]),
    }
    return values


def mesh_feature_vector(event: dict) -> list[float]:
    values = mesh_feature_values(event)
    return [values[name] for name in MESH_FEATURES]
