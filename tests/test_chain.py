from mesh.chain import ChainLinker


def event(bank, seq, t_ms, src_bid, dst_bid, amt_bin):
    return {
        "bank": bank,
        "seq": seq,
        "t_ms": t_ms,
        "src_bid": src_bid,
        "dst_bid": dst_bid,
        "amt_bin": amt_bin,
        "f": [0.0] * 8,
        "sensor_us": 10.0,
    }


def test_links_cross_bank_hops_and_inherits_chain():
    linker = ChainLinker()
    first = linker.add(event("A", 1, 1000, "BID_000000000001", "BID_000000000002", 1.0))
    second = linker.add(event("B", 1, 5000, "BID_000000000002", "BID_000000000003", 0.75))

    assert first["hop_depth"] == 1
    assert second["hop_depth"] == 2
    assert second["chain_id"] == first["chain_id"]
    assert second["dt_prev_hop_ms"] == 4000
    assert second["chain"] == [
        ["A", "BID_000000000002", 0, 1.0],
        ["B", "BID_000000000003", 4000, 0.75],
    ]


def test_accepts_clock_skew_with_clipped_feature_gap():
    linker = ChainLinker()
    linker.add(event("A", 1, 5000, "BID_000000000001", "BID_000000000002", 1.0))
    second = linker.add(event("B", 1, 4000, "BID_000000000002", "BID_000000000003", 1.0))

    assert second["hop_depth"] == 2
    assert second["dt_prev_hop_ms"] == 0


def test_old_event_does_not_link():
    linker = ChainLinker()
    linker.add(event("A", 1, 1000, "BID_000000000001", "BID_000000000002", 1.0))
    second = linker.add(event("B", 1, 61001, "BID_000000000002", "BID_000000000003", 1.0))

    assert second["hop_depth"] == 1
    assert second["dt_prev_hop_ms"] == 0
