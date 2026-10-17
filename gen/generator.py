"""Deterministic synthetic bank-event generator.

Raw events intentionally contain plausible synthetic PII, but never scenario,
attack, chain, or evaluation labels.  Those labels are returned separately in
``ground_truth`` so the blind sensor can be exercised without label leakage.
"""

from __future__ import annotations

from dataclasses import dataclass
import random
from typing import Any


BANKS = ("A", "B", "C")
DAY_MS = 24 * 60 * 60 * 1000
SLOW_DRIP = "SLOW_DRIP_STRUCTURING"


@dataclass(frozen=True)
class GeneratorConfig:
    """Dataset scale and time span. Defaults produce the project demo dataset."""

    event_count: int = 60_000
    accounts_per_bank: int = 2_000
    duration_ms: int = DAY_MS
    structuring_threshold: float = 10_000.0


_FIRST_NAMES = (
    "Avery", "Briar", "Cedar", "Dakota", "Ember", "Finley", "Harper", "Indigo",
    "Jules", "Kai", "Linden", "Marlowe", "Nova", "Oakley", "Parker", "Quinn",
)
_LAST_NAMES = (
    "Alder", "Barton", "Cove", "Dale", "Ellis", "Frost", "Gray", "Hart",
    "Ives", "Juniper", "Kestrel", "Lake", "Moss", "North", "Orchid", "Pine",
)

# Event rates are fractions of the final data volume. Counts are rounded down
# to complete, indivisible scenario groups, with at least one group of each.
_SCENARIOS = (
    ("CROSS_BANK_MULE_CASCADE", 0.04, 3, True),
    ("ABORT_STORM", 0.02, 4, True),
    ("HARD_NEG_PAYROLL_FANOUT", 0.01, 3, False),
    ("HARD_NEG_FAMILY_RELAY", 0.01, 3, False),
    ("HARD_NEG_MERCHANT_BURST", 0.01, 4, False),
    ("HARD_NEG_QUICK_RELAY", 0.01, 2, False),
)


def _validate_config(config: GeneratorConfig) -> None:
    if not isinstance(config, GeneratorConfig):
        raise TypeError("config must be a GeneratorConfig")
    if config.event_count < 42:
        raise ValueError("event_count must be at least 42 to include all scenarios and canaries")
    if config.accounts_per_bank < 1:
        raise ValueError("accounts_per_bank must be positive")
    if config.duration_ms < 25:
        raise ValueError("duration_ms must be at least 25 milliseconds")
    if config.structuring_threshold < 1_000.0:
        raise ValueError("structuring_threshold must be at least 1000 for synthetic near-threshold events")


def _scenario_event_counts(total: int) -> dict[str, int]:
    counts: dict[str, int] = {}
    for name, fraction, group_size, _ in _SCENARIOS:
        requested = int(total * fraction)
        groups = max(1, requested // group_size)
        counts[name] = groups * group_size
    return counts


def _slow_drip_lengths(total: int, available: int, rng: random.Random) -> list[int]:
    """Plan 20–40 row slow-drip chains while preserving three canary rows."""
    groups = max(1, round(total * 0.02 / 30))
    groups = min(groups, available // 20)
    lengths: list[int] = []
    remaining = available
    for index in range(groups):
        groups_left = groups - index - 1
        maximum = min(40, remaining - 20 * groups_left)
        length = rng.randint(20, maximum)
        lengths.append(length)
        remaining -= length
    return lengths


def _account(bank: str, index: int) -> str:
    return f"{bank}:{index:06d}"


def _ext(kind: str, index: int) -> str:
    return f"EXT:{kind}_{index:02d}"


def _group_anchor(rng: random.Random, duration_ms: int, span_ms: int) -> int:
    """Choose a short group wholly in split A or B, away from the split edge."""
    boundary = int(duration_ms * 0.8)
    # Pick B one time in four, giving the classifier test period known attacks
    # while keeping the held-out slow-drip type exclusive to the final segment.
    if rng.randrange(4) == 0 and duration_ms - span_ms >= boundary:
        low, high = boundary, duration_ms - span_ms
    else:
        low, high = 0, max(0, boundary - span_ms)
    if high <= low:
        return low
    return rng.randint(low, high)


def generate(
    config: GeneratorConfig | None = None,
    seed: int = 7,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, list[str]], list[str]]:
    """Return ``(raw_events, ground_truth, accounts_by_bank, canary_list)``.

    The generator uses only Python's standard library.  Given the same config
    and seed it returns byte-for-byte equivalent Python data structures.
    """
    if config is None:
        config = GeneratorConfig()
    _validate_config(config)
    rng = random.Random(seed)

    accounts_by_bank = {
        bank: [
            {
                "account": _account(bank, i),
                "customer_name": (
                    f"CANARY_NAME_{bank}_ZSPECTRA_01"
                    if i == 0
                    else f"{_FIRST_NAMES[(i + BANKS.index(bank) * 3) % len(_FIRST_NAMES)]} "
                    f"{_LAST_NAMES[(i * 5 + BANKS.index(bank)) % len(_LAST_NAMES)]}"
                ),
            }
            for i in range(config.accounts_per_bank)
        ]
        for bank in BANKS
    }
    account_records = {
        row["account"]: row
        for bank_records in accounts_by_bank.values()
        for row in bank_records
    }
    events: list[dict[str, Any]] = []
    truth_rows: list[dict[str, Any]] = []
    event_number = 0
    chain_number = 0

    def new_chain() -> str:
        nonlocal chain_number
        chain_number += 1
        return f"CHAIN-{chain_number:06d}"

    def emit(
        *,
        chain_id: str,
        attack_type: str,
        is_attack: bool,
        hop_index: int,
        ts_ms: int,
        bank: str,
        src_account: str,
        dst_account: str,
        amount: float,
        tx_type: str,
        channel: str,
        aborted_retries_last_5s: int = 0,
        memo: str | None = None,
        canary_fields: bool = False,
    ) -> None:
        nonlocal event_number
        event_number += 1
        event_id = f"EVT-{event_number:08d}"
        account_index = int(src_account.split(":", 1)[1])
        customer_name = account_records[src_account]["customer_name"]
        national_id = (
            f"CANARY_NID_{bank}_ZSPECTRA_01"
            if account_index == 0
            else f"SIM-{bank}-{account_index:06d}"
        )
        event_memo = memo or f"sim_{tx_type.lower()}_{rng.randrange(1_000_000):06d}"
        if canary_fields:
            event_memo = f"CANARY_MEMO_{bank}_ZSPECTRA_01"
        events.append(
            {
                "event_id": event_id,
                "ts_ms": int(ts_ms),
                "bank": bank,
                "src_account": src_account,
                "dst_account": dst_account,
                "amount": round(float(amount), 2),
                "tx_type": tx_type,
                "channel": channel,
                "aborted_retries_last_5s": int(aborted_retries_last_5s),
                "customer_name": customer_name,
                "national_id": national_id,
                "memo": event_memo,
            }
        )
        # Keep the evaluation truth outside the raw event object and preserve
        # the exact small ground-truth contract used by the evaluation stage.
        truth_rows.append(
            {
                "event_id": event_id,
                "chain_id": chain_id,
                "attack_type": attack_type,
                "hop_index": int(hop_index),
                "is_attack": bool(is_attack),
            }
        )

    def random_account(bank: str) -> str:
        return accounts_by_bank[bank][rng.randrange(config.accounts_per_bank)]["account"]

    def other_bank(bank: str) -> str:
        options = [candidate for candidate in BANKS if candidate != bank]
        return options[rng.randrange(len(options))]

    def normal_event(index: int) -> None:
        # Reserve three ordinary rows to make each bank's raw account canary
        # visible to a downstream leak scanner, plus a PII/memo sentinel.
        if index == 0:
            bank, src, dst = "A", _account("A", 0), _account("B", 0)
            force_canary = True
        elif index == 1:
            bank, src, dst = "B", _account("B", 0), _account("C", 0)
            force_canary = True
        elif index == 2:
            bank, src, dst = "C", _account("C", 0), _account("A", 1 % config.accounts_per_bank)
            force_canary = True
        else:
            bank = BANKS[rng.randrange(len(BANKS))]
            src = random_account(bank)
            tx_pick = rng.randrange(100)
            if tx_pick < 9:
                dst = _ext("cashout", rng.randrange(30))
                tx_type = "CASHOUT"
            elif tx_pick < 24:
                dst = _ext("merchant", rng.randrange(30))
                tx_type = "PAYMENT"
            else:
                dst = random_account(other_bank(bank))
                tx_type = "TRANSFER"
            chain_id = new_chain()
            emit(
                chain_id=chain_id,
                attack_type="NORMAL",
                is_attack=False,
                hop_index=0,
                ts_ms=rng.randint(0, config.duration_ms),
                bank=bank,
                src_account=src,
                dst_account=dst,
                amount=rng.uniform(3.0, 5_000.0),
                tx_type=tx_type,
                channel=("APP", "WEB", "API")[rng.randrange(3)],
            )
            return
        chain_id = new_chain()
        emit(
            chain_id=chain_id,
            attack_type="NORMAL",
            is_attack=False,
            hop_index=0,
            ts_ms=rng.randint(0, config.duration_ms),
            bank=bank,
            src_account=src,
            dst_account=dst,
            amount=rng.uniform(3.0, 5_000.0),
            tx_type="TRANSFER",
            channel=("APP", "WEB", "API")[rng.randrange(3)],
            canary_fields=force_canary,
        )

    scenario_counts = _scenario_event_counts(config.event_count)
    static_scenario_total = sum(scenario_counts.values())
    slow_budget = config.event_count - static_scenario_total - 3
    slow_lengths = _slow_drip_lengths(config.event_count, slow_budget, rng)

    # Three-hop cross-bank mule path: A->B, B->C, then C->external cash-out.
    # The second transfer occurs quickly and carries almost all funds onward,
    # so the cascade is visible at hop 2 before the terminal cash-out.
    cascade_count = scenario_counts["CROSS_BANK_MULE_CASCADE"]
    for _ in range(cascade_count // 3):
        chain_id = new_chain()
        a_idx = rng.randrange(config.accounts_per_bank)
        b_idx = rng.randrange(config.accounts_per_bank)
        c_idx = rng.randrange(config.accounts_per_bank)
        amount = rng.uniform(300.0, 4_000.0)
        bank_a, bank_b, bank_c = "A", "B", "C"
        cascade_step_cap = max(1, min(30_000, config.duration_ms // 2))
        anchor = _group_anchor(rng, config.duration_ms, 2 * cascade_step_cap)
        hop2_ts = anchor + rng.randint(1, cascade_step_cap)
        hop3_ts = hop2_ts + rng.randint(1, cascade_step_cap)
        second_amount = amount * rng.uniform(0.91, 0.99)
        emit(
            chain_id=chain_id,
            attack_type="CROSS_BANK_MULE_CASCADE",
            is_attack=True,
            hop_index=1,
            ts_ms=anchor,
            bank=bank_a,
            src_account=_account(bank_a, a_idx),
            dst_account=_account(bank_b, b_idx),
            amount=amount,
            tx_type="TRANSFER",
            channel="API",
        )
        emit(
            chain_id=chain_id,
            attack_type="CROSS_BANK_MULE_CASCADE",
            is_attack=True,
            hop_index=2,
            ts_ms=hop2_ts,
            bank=bank_b,
            src_account=_account(bank_b, b_idx),
            dst_account=_account(bank_c, c_idx),
            amount=second_amount,
            tx_type="TRANSFER",
            channel="API",
        )
        emit(
            chain_id=chain_id,
            attack_type="CROSS_BANK_MULE_CASCADE",
            is_attack=True,
            hop_index=3,
            ts_ms=hop3_ts,
            bank=bank_c,
            src_account=_account(bank_c, c_idx),
            dst_account=_ext("cashout", rng.randrange(30)),
            amount=second_amount * rng.uniform(0.91, 0.99),
            tx_type="CASHOUT",
            channel="API",
        )

    # Repeated rollbacks on a single source account create explicit simulated
    # rollback telemetry without inventing hop-velocity as a feature.
    abort_count = scenario_counts["ABORT_STORM"]
    abort_step = max(1, min(1_000, config.duration_ms // 200_000))
    for _ in range(abort_count // 4):
        chain_id = new_chain()
        bank = BANKS[rng.randrange(len(BANKS))]
        src = random_account(bank)
        dst = random_account(other_bank(bank))
        span = abort_step * 3
        anchor = _group_anchor(rng, config.duration_ms, span)
        for hop in range(4):
            emit(
                chain_id=chain_id,
                attack_type="ABORT_STORM",
                is_attack=True,
                hop_index=hop + 1,
                ts_ms=anchor + hop * abort_step,
                bank=bank,
                src_account=src,
                dst_account=dst,
                amount=rng.uniform(20.0, 700.0),
                tx_type="TRANSFER",
                channel="APP",
                aborted_retries_last_5s=rng.randint(5, 30),
            )

    # Structuring chains contain 20–40 individually just-under-threshold
    # transfers from one source to a small set of destinations. The event rate
    # spreads them across the final test window; they are not mixed into the
    # earlier training period.
    boundary = int(config.duration_ms * 0.8)
    for slow_length in slow_lengths:
        minimum_span = min(20 * 60 * 1000, max(1, config.duration_ms - 1))
        maximum_span = min(90 * 60 * 1000, max(minimum_span, config.duration_ms - 1))
        intervals = slow_length - 1
        minimum_step = max(1, (minimum_span + intervals - 1) // intervals)
        maximum_step = max(minimum_step, maximum_span // intervals)
        slow_step = rng.randint(minimum_step, maximum_step)
        slow_span = slow_step * intervals
        slow_high = max(boundary, config.duration_ms - slow_span)
        chain_id = new_chain()
        anchor = rng.randint(boundary, slow_high) if slow_high > boundary else boundary
        source_bank = BANKS[rng.randrange(len(BANKS))]
        source_account = random_account(source_bank)
        destination_pool = [
            record["account"]
            for bank in BANKS
            if bank != source_bank
            for record in accounts_by_bank[bank]
        ]
        destination_count = rng.randint(1, min(3, len(destination_pool)))
        destinations = rng.sample(destination_pool, destination_count)
        for hop in range(slow_length):
            amount = round(
                config.structuring_threshold
                - rng.uniform(
                    config.structuring_threshold * 0.01,
                    config.structuring_threshold * 0.03,
                ),
                2,
            )
            emit(
                chain_id=chain_id,
                attack_type=SLOW_DRIP,
                is_attack=True,
                hop_index=hop + 1,
                ts_ms=anchor + hop * slow_step,
                bank=source_bank,
                src_account=source_account,
                dst_account=destinations[hop % destination_count],
                amount=amount,
                tx_type="TRANSFER",
                channel="WEB",
            )

    # Hard negatives exercise common high-activity patterns while remaining
    # explicitly non-attack in ground_truth.
    payroll_count = scenario_counts["HARD_NEG_PAYROLL_FANOUT"]
    for _ in range(payroll_count // 3):
        chain_id = new_chain()
        bank = BANKS[rng.randrange(3)]
        src = random_account(bank)
        dest_bank = other_bank(bank)
        span = 2 * max(1, min(5_000, config.duration_ms // 100_000))
        anchor = _group_anchor(rng, config.duration_ms, span)
        for hop in range(3):
            emit(
                chain_id=chain_id,
                attack_type="HARD_NEG_PAYROLL_FANOUT",
                is_attack=False,
                hop_index=hop + 1,
                ts_ms=anchor + hop * max(1, span // 2),
                bank=bank,
                src_account=src,
                dst_account=random_account(dest_bank),
                amount=rng.uniform(700.0, 2_500.0),
                tx_type="TRANSFER",
                channel="API",
                memo="payroll_batch",
            )

    family_count = scenario_counts["HARD_NEG_FAMILY_RELAY"]
    for _ in range(family_count // 3):
        chain_id = new_chain()
        banks = list(BANKS)
        rng.shuffle(banks)
        path_indices = [rng.randrange(config.accounts_per_bank) for _ in range(3)]
        step = max(1, min(90 * 60 * 1000, config.duration_ms // 24))
        anchor = _group_anchor(rng, config.duration_ms, 2 * step)
        amount = rng.uniform(40.0, 250.0)
        for hop in range(3):
            emit(
                chain_id=chain_id,
                attack_type="HARD_NEG_FAMILY_RELAY",
                is_attack=False,
                hop_index=hop + 1,
                ts_ms=anchor + hop * step,
                bank=banks[hop],
                src_account=_account(banks[hop], path_indices[hop]),
                dst_account=_account(banks[(hop + 1) % 3], path_indices[(hop + 1) % 3]),
                amount=amount,
                tx_type="TRANSFER",
                channel="APP",
                memo="family_support",
            )

    merchant_count = scenario_counts["HARD_NEG_MERCHANT_BURST"]
    merchant_step = max(1, min(2_000, config.duration_ms // 500_000))
    for _ in range(merchant_count // 4):
        chain_id = new_chain()
        bank = BANKS[rng.randrange(3)]
        merchant = _ext("merchant", rng.randrange(30))
        span = 3 * merchant_step
        anchor = _group_anchor(rng, config.duration_ms, span)
        for hop in range(4):
            emit(
                chain_id=chain_id,
                attack_type="HARD_NEG_MERCHANT_BURST",
                is_attack=False,
                hop_index=hop + 1,
                ts_ms=anchor + hop * merchant_step,
                bank=bank,
                src_account=random_account(bank),
                dst_account=merchant,
                amount=rng.uniform(8.0, 220.0),
                tx_type="PAYMENT",
                channel="APP",
                memo="merchant_batch",
            )

    quick_count = scenario_counts["HARD_NEG_QUICK_RELAY"]
    for _ in range(quick_count // 2):
        chain_id = new_chain()
        bank_a = BANKS[rng.randrange(3)]
        bank_b = other_bank(bank_a)
        middle = random_account(bank_b)
        bank_c = next(bank for bank in BANKS if bank not in {bank_a, bank_b})
        quick_step_cap = max(1, min(5_000, config.duration_ms // 2))
        anchor = _group_anchor(rng, config.duration_ms, quick_step_cap)
        amount = rng.uniform(15.0, 90.0)
        emit(
            chain_id=chain_id,
            attack_type="HARD_NEG_QUICK_RELAY",
            is_attack=False,
            hop_index=1,
            ts_ms=anchor,
            bank=bank_a,
            src_account=random_account(bank_a),
            dst_account=middle,
            amount=amount,
            tx_type="TRANSFER",
            channel="APP",
            memo="shared_expense",
        )
        emit(
            chain_id=chain_id,
            attack_type="HARD_NEG_QUICK_RELAY",
            is_attack=False,
            hop_index=2,
            ts_ms=anchor + rng.randint(1, quick_step_cap),
            bank=bank_b,
            src_account=middle,
            dst_account=_account(bank_c, rng.randrange(config.accounts_per_bank)),
            amount=amount * rng.uniform(0.65, 0.9),
            tx_type="PAYMENT",
            channel="APP",
            memo="shared_expense",
        )

    # Complete the exact configured volume with ordinary transfers. At least
    # three rows remain for the planted, bank-specific canary values.
    normal_count = config.event_count - sum(scenario_counts.values()) - sum(slow_lengths)
    for index in range(normal_count):
        normal_event(index)

    # Keep the external event stream chronological, while retaining truth rows
    # aligned by event_id. Split/evaluation code can group using truth.chain_id.
    events.sort(key=lambda event: (event["ts_ms"], event["event_id"]))
    truth_by_id = {row["event_id"]: row for row in truth_rows}
    ground_truth = [truth_by_id[event["event_id"]] for event in events]

    # Keep scenario types stable for grouped splitting, while adding a small,
    # deterministic false-positive noise sample to the binary truth label.
    normal_candidates = [
        index
        for index, row in enumerate(ground_truth)
        if row["attack_type"] == "NORMAL" and not row["is_attack"]
    ]
    noise_count = min(len(normal_candidates), round(len(ground_truth) * 0.02))
    for index in rng.sample(normal_candidates, noise_count):
        ground_truth[index]["is_attack"] = True

    canary_values = set()
    for bank_records in accounts_by_bank.values():
        for record in bank_records:
            canary_values.add(record["account"])
            canary_values.add(record["customer_name"])
    for event in events:
        for field in ("customer_name", "national_id", "memo", "src_account", "dst_account"):
            value = event[field]
            if type(value) is str and value:
                canary_values.add(value)
    canary_list = sorted(canary_values)
    return events, ground_truth, accounts_by_bank, canary_list
