"""Cross-bank hop linking over pseudonymous account identifiers."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy


class ChainLinker:
    """Link events whose source BID matches a recent prior destination BID."""

    def __init__(self, min_delta_ms: int = -2000, max_delta_ms: int = 60000) -> None:
        self.min_delta_ms = min_delta_ms
        self.max_delta_ms = max_delta_ms
        self._by_dst: dict[str, list[dict]] = defaultdict(list)
        self._next_chain = 1
        self._arrival_count = 0

    def reset(self) -> None:
        self._by_dst.clear()
        self._next_chain = 1
        self._arrival_count = 0

    def add(self, message: dict) -> dict:
        """Return an enriched copy; wire input is never modified."""
        src_bid = message["src_bid"]
        t_ms = message["t_ms"]
        candidates = self._by_dst.get(src_bid, ())
        parents = [
            prior
            for prior in candidates
            if self.min_delta_ms <= t_ms - prior["t_ms"] <= self.max_delta_ms
        ]
        if candidates:
            if parents:
                self._by_dst[src_bid] = parents
            else:
                self._by_dst.pop(src_bid, None)
        parent = max(parents, key=lambda item: (item["t_ms"], item["arrival"])) if parents else None

        if parent is None:
            chain_id = f"CH_{self._next_chain:08d}"
            self._next_chain += 1
            hop_depth = 1
            dt_ms = 0
            chain = [[message["bank"], message["dst_bid"], 0, message["amt_bin"]]]
            chain_start_ms = t_ms
        else:
            chain_id = parent["chain_id"]
            hop_depth = parent["hop_depth"] + 1
            dt_ms = max(0, t_ms - parent["t_ms"])
            chain = deepcopy(parent["chain"])
            chain.append([message["bank"], message["dst_bid"], dt_ms, message["amt_bin"]])
            chain_start_ms = parent["chain_start_ms"]

        enriched = dict(message)
        enriched.update(
            {
                "chain_id": chain_id,
                "hop_depth": hop_depth,
                "dt_prev_hop_ms": dt_ms,
                "chain": chain,
                "chain_start_ms": chain_start_ms,
                "chain_span_ms": sum(row[2] for row in chain),
            }
        )
        record = dict(enriched)
        record["arrival"] = self._arrival_count
        self._arrival_count += 1
        self._by_dst[message["dst_bid"]].append(record)
        return enriched
