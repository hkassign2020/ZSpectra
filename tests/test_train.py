from train import split_rows_by_chain


def test_time_splits_keep_ground_truth_chains_together():
    rows = []
    for chain_index in range(20):
        for hop in range(2):
            rows.append(
                {
                    "chain_id": f"chain-{chain_index}",
                    "ts_ms": chain_index * 100 + hop,
                    "attack_type": "NORMAL",
                }
            )

    splits = split_rows_by_chain(rows)
    owner = {}
    for split_name, split_rows in splits.items():
        for row in split_rows:
            owner.setdefault(row["chain_id"], set()).add(split_name)
    assert all(len(split_names) == 1 for split_names in owner.values())
    assert {row["chain_id"] for row in splits["A"]}.isdisjoint(
        {row["chain_id"] for row in splits["B"]}
    )


def test_slow_drip_remains_in_test_split():
    rows = []
    for chain_index in range(20):
        attack_type = "SLOW_DRIP_STRUCTURING" if chain_index >= 16 else "NORMAL"
        for hop in range(2):
            rows.append(
                {
                    "chain_id": f"chain-{chain_index}",
                    "ts_ms": chain_index * 100 + hop,
                    "attack_type": attack_type,
                }
            )

    splits = split_rows_by_chain(rows)
    assert all(
        row["attack_type"] != "SLOW_DRIP_STRUCTURING"
        for split_name in ("A", "B")
        for row in splits[split_name]
    )
