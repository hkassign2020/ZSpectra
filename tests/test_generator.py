import unittest

from gen.generator import GeneratorConfig, generate


class GeneratorTests(unittest.TestCase):
    def setUp(self):
        self.config = GeneratorConfig(
            event_count=300,
            accounts_per_bank=24,
            duration_ms=24 * 60 * 60 * 1000,
            structuring_threshold=1_000.0,
        )

    def test_seed_makes_all_outputs_deterministic(self):
        first = generate(self.config, seed=19)
        second = generate(self.config, seed=19)
        self.assertEqual(first, second)

    def test_minimum_reduced_config_keeps_exact_event_count(self):
        config = GeneratorConfig(
            event_count=42,
            accounts_per_bank=2,
            duration_ms=24 * 60 * 60 * 1000,
            structuring_threshold=1_000.0,
        )
        raw_events, ground_truth, _, _ = generate(config, seed=3)
        self.assertEqual(len(raw_events), 42)
        self.assertEqual(len(ground_truth), 42)

    def test_raw_events_follow_private_raw_shape_and_have_no_labels(self):
        raw_events, ground_truth, accounts_by_bank, canary_list = generate(
            self.config, seed=2
        )

        self.assertEqual(len(raw_events), self.config.event_count)
        self.assertEqual(len(ground_truth), len(raw_events))
        self.assertEqual(set(accounts_by_bank), {"A", "B", "C"})
        for bank, accounts in accounts_by_bank.items():
            self.assertEqual(len(accounts), self.config.accounts_per_bank)
            self.assertTrue(all(set(row) == {"account", "customer_name"} for row in accounts))
            self.assertTrue(all(row["account"].startswith(f"{bank}:") for row in accounts))

        expected_fields = {
            "event_id",
            "ts_ms",
            "bank",
            "src_account",
            "dst_account",
            "amount",
            "tx_type",
            "channel",
            "aborted_retries_last_5s",
            "customer_name",
            "national_id",
            "memo",
        }
        forbidden_labels = {"label", "is_attack", "attack_type", "chain_id", "hop_index", "split"}
        self.assertEqual(set(raw_events[0]), expected_fields)
        for event in raw_events:
            self.assertEqual(set(event), expected_fields)
            self.assertFalse(forbidden_labels.intersection(event))
            self.assertIn(event["bank"], {"A", "B", "C"})
            self.assertGreaterEqual(event["amount"], 0)
            self.assertIn(event["tx_type"], {"TRANSFER", "PAYMENT", "CASHOUT"})
            self.assertIn(event["channel"], {"APP", "WEB", "API"})

        self.assertEqual(
            set(ground_truth[0]),
            {"event_id", "chain_id", "attack_type", "hop_index", "is_attack"},
        )
        self.assertEqual(
            [event["event_id"] for event in raw_events],
            [truth["event_id"] for truth in ground_truth],
        )
        self.assertEqual(len({event["event_id"] for event in raw_events}), len(raw_events))
        self.assertTrue(canary_list)

    def test_all_scenarios_exist_and_slow_drip_is_in_final_time_segment(self):
        raw_events, ground_truth, _, _ = generate(self.config, seed=8)
        by_id = {event["event_id"]: event for event in raw_events}
        by_type = {}
        for truth in ground_truth:
            by_type.setdefault(truth["attack_type"], []).append(truth)

        required_types = {
            "CROSS_BANK_MULE_CASCADE",
            "ABORT_STORM",
            "SLOW_DRIP_STRUCTURING",
            "HARD_NEG_PAYROLL_FANOUT",
            "HARD_NEG_FAMILY_RELAY",
            "HARD_NEG_MERCHANT_BURST",
            "HARD_NEG_QUICK_RELAY",
        }
        self.assertTrue(required_types.issubset(by_type))
        self.assertTrue(all(row["is_attack"] for row in by_type["CROSS_BANK_MULE_CASCADE"]))
        for negative_type in required_types - {
            "CROSS_BANK_MULE_CASCADE",
            "ABORT_STORM",
            "SLOW_DRIP_STRUCTURING",
        }:
            self.assertTrue(all(not row["is_attack"] for row in by_type[negative_type]))

        cascade_chains = {}
        for row in by_type["CROSS_BANK_MULE_CASCADE"]:
            cascade_chains.setdefault(row["chain_id"], []).append(row)
        for rows in cascade_chains.values():
            rows.sort(key=lambda row: row["hop_index"])
            self.assertEqual([row["hop_index"] for row in rows], [1, 2, 3])
            events = [by_id[row["event_id"]] for row in rows]
            self.assertEqual([event["bank"] for event in events], ["A", "B", "C"])
            self.assertEqual(events[0]["dst_account"], events[1]["src_account"])
            self.assertEqual(events[1]["dst_account"], events[2]["src_account"])
            self.assertTrue(events[2]["dst_account"].startswith("EXT:cashout_"))

        slow_drip = by_type["SLOW_DRIP_STRUCTURING"]
        cutoff = int(self.config.duration_ms * 0.8)
        self.assertTrue(all(by_id[row["event_id"]]["ts_ms"] >= cutoff for row in slow_drip))
        slow_chains = {}
        for row in slow_drip:
            slow_chains.setdefault(row["chain_id"], []).append(row)
        self.assertTrue(all(20 <= len(rows) <= 40 for rows in slow_chains.values()))
        for rows in slow_chains.values():
            timestamps = sorted(by_id[row["event_id"]]["ts_ms"] for row in rows)
            self.assertTrue(20 * 60 * 1000 <= timestamps[-1] - timestamps[0] <= 90 * 60 * 1000)
            dests = {by_id[row["event_id"]]["dst_account"] for row in rows}
            amounts = [by_id[row["event_id"]]["amount"] for row in rows]
            self.assertGreaterEqual(len(dests), 1)
            self.assertLessEqual(len(dests), 3)
            self.assertTrue(
                all(self.config.structuring_threshold * 0.97 <= amount < self.config.structuring_threshold
                    for amount in amounts)
            )

        family_chains = {}
        for row in by_type["HARD_NEG_FAMILY_RELAY"]:
            family_chains.setdefault(row["chain_id"], []).append(row)
        for rows in family_chains.values():
            rows.sort(key=lambda row: row["hop_index"])
            times = [by_id[row["event_id"]]["ts_ms"] for row in rows]
            self.assertTrue(
                all(45 * 60 * 1000 <= b - a <= 90 * 60 * 1000
                    for a, b in zip(times, times[1:]))
            )

    def test_abort_storms_are_observed_as_rollback_telemetry(self):
        raw_events, ground_truth, _, _ = generate(self.config, seed=5)
        events = {event["event_id"]: event for event in raw_events}
        abort_rows = [row for row in ground_truth if row["attack_type"] == "ABORT_STORM"]
        self.assertTrue(abort_rows)
        self.assertTrue(
            all(5 <= events[row["event_id"]]["aborted_retries_last_5s"] <= 30 for row in abort_rows)
        )
        self.assertFalse(any("hop_velocity" in event for event in raw_events))

    def test_canaries_are_fake_sentinels_present_in_raw_data(self):
        raw_events, _, accounts_by_bank, canary_list = generate(self.config, seed=11)
        raw_text = repr((raw_events, accounts_by_bank))
        self.assertTrue(canary_list)
        self.assertTrue(all(isinstance(canary, str) for canary in canary_list))
        self.assertTrue(all(canary in raw_text for canary in canary_list))
        self.assertGreater(len(canary_list), 6)

    def test_ground_truth_contains_deterministic_two_percent_false_positive_noise(self):
        _, truth, _, _ = generate(self.config, seed=23)
        noisy_normal = [
            row for row in truth
            if row["attack_type"] == "NORMAL" and row["is_attack"]
        ]

        self.assertEqual(len(noisy_normal), round(len(truth) * 0.02))


if __name__ == "__main__":
    unittest.main()
