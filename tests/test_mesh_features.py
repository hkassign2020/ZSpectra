from mesh.features import mesh_feature_values, mesh_feature_vector


def test_mesh_features_use_linked_chain_only():
    event = {
        "hop_depth": 2,
        "dt_prev_hop_ms": 4000,
        "chain_span_ms": 4000,
        "chain": [
            ["A", "BID_000000000002", 0, 1.0],
            ["B", "BID_000000000003", 4000, 0.75],
        ],
    }

    values = mesh_feature_values(event)
    assert values == {
        "hop_depth": 2.0,
        "dt_prev_hop_ms": 4000.0,
        "amount_carry_over": -0.25,
        "banks_in_chain": 2.0,
        "chain_span_ms": 4000.0,
    }
    assert mesh_feature_vector(event) == [2.0, 4000.0, -0.25, 2.0, 4000.0]


def test_first_hop_mesh_features_are_zero_where_no_prior_hop_exists():
    event = {
        "hop_depth": 1,
        "dt_prev_hop_ms": 0,
        "chain_span_ms": 0,
        "chain": [["A", "BID_000000000002", 0, 1.0]],
    }

    values = mesh_feature_values(event)
    assert values["dt_prev_hop_ms"] == 0.0
    assert values["amount_carry_over"] == 0.0
    assert values["banks_in_chain"] == 1.0
