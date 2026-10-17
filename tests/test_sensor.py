import math
import unittest
from pathlib import Path
import zlib

from bank.sensor import (
    ABORT_BIN_BOUNDARIES,
    BANK_TO_MESH_KEYS,
    GAP_BIN_BOUNDARIES_MS,
    _FIRST_GAP_MS,
    SENSOR_FEATURES,
    BlindSensor,
    bid_for,
)


class BlindSensorTests(unittest.TestCase):
    def setUp(self):
        self.sensor = BlindSensor(b"test-consortium-key", "A")

    def sample(self, **overrides):
        event = {
            "event_id": "evt-1",
            "ts_ms": 1_000,
            "bank": "A",
            "src_account": "A:000123",
            "dst_account": "A:000124",
            "amount": 125.0,
            "tx_type": "TRANSFER",
            "channel": "APP",
            "aborted_retries_last_5s": 0,
            "customer_name": "Sensitive Customer",
            "national_id": "ID-987-65",
            "memo": "sensitive customer memo",
        }
        event.update(overrides)
        return event

    def test_output_is_only_the_frozen_bank_to_mesh_contract(self):
        result = self.sensor.process(self.sample())
        self.assertEqual(set(result), set(BANK_TO_MESH_KEYS))
        self.assertEqual(len(result["f"]), len(SENSOR_FEATURES))
        self.assertTrue(all(isinstance(value, float) for value in result["f"]))
        self.assertTrue(all(math.isfinite(value) for value in result["f"]))
        self.assertEqual(result["bank"], "A")
        self.assertEqual(result["seq"], 1)
        self.assertEqual(result["t_ms"], 1_000)
        self.assertEqual(result["src_bid"], bid_for("A:000123", b"test-consortium-key"))
        self.assertEqual(result["dst_bid"], bid_for("A:000124", b"test-consortium-key"))
        self.assertGreaterEqual(result["sensor_us"], 0.0)
        self.assertNotIn("sensitive customer memo", repr(result))
        self.assertNotIn("evt-1", repr(result))
        self.assertNotIn("A:000123", repr(result))
        self.assertNotIn("Sensitive Customer", repr(result))
        self.assertNotIn("ID-987-65", repr(result))

    def test_bid_is_stable_and_canonicalizes_account_text(self):
        self.assertEqual(
            bid_for("  A:000123  ", b"shared-key"),
            bid_for("A:000123", b"shared-key"),
        )
        self.assertTrue(bid_for("A:000123", b"shared-key").startswith("BID_"))
        self.assertEqual(len(bid_for("A:000123", b"shared-key")), 16)
        self.assertNotEqual(
            bid_for("A:000123", b"shared-key"),
            bid_for("A:000123", b"other-key"),
        )

    def test_features_use_the_frozen_order_and_quarter_decade_amount_bin(self):
        result = self.sensor.process(self.sample())
        feature_map = dict(zip(SENSOR_FEATURES, result["f"]))
        self.assertEqual(tuple(SENSOR_FEATURES), (
            "log_amount_bin",
            "log1p_gap_ms",
            "aborts_5s",
            "tx_count_60s",
            "distinct_dest_60s",
            "window_ncd",
            "compression_ratio",
            "window_entropy",
        ))
        self.assertEqual(result["amt_bin"], 2.0)  # floor(4*log10(125))/4
        self.assertEqual(feature_map["log_amount_bin"], 2.0)
        self.assertEqual(feature_map["log1p_gap_ms"], math.log1p(_FIRST_GAP_MS))
        self.assertEqual(feature_map["aborts_5s"], 0.0)
        self.assertEqual(feature_map["tx_count_60s"], 1.0)
        self.assertEqual(feature_map["distinct_dest_60s"], 1.0)

    def test_window_caps_at_last_32_events_and_reference_is_stable(self):
        reference_file = Path(__file__).parents[1] / "bank" / "reference_window.txt"
        self.assertTrue(reference_file.is_file())
        self.assertTrue(reference_file.read_text(encoding="utf-8").strip())
        self.assertTrue(reference_file.read_bytes().endswith(b"\n"))

        sensor_a = BlindSensor(b"same-key", "A")
        sensor_b = BlindSensor(b"same-key", "A")
        last_a = last_b = None
        for index in range(40):
            event = self.sample(
                ts_ms=1_000 + index * 250,
                src_account="A:000123",
                dst_account=f"A:{index % 7:06d}",
                amount=25.0 + index,
                memo=f"private memo {index}",
            )
            last_a = sensor_a.process(event)
            last_b = sensor_b.process(event)
        self.assertEqual(last_a["f"], last_b["f"])
        self.assertEqual(set(last_a), set(BANK_TO_MESH_KEYS))
        self.assertEqual(len(sensor_a._windows["A:000123"]), 32)

    def test_window_is_source_scoped_and_has_only_coarse_blind_rows(self):
        interleaved = BlindSensor(b"same-key", "A")
        isolated = BlindSensor(b"same-key", "A")
        first = self.sample(ts_ms=1_000, src_account="A:000123")
        other = self.sample(
            ts_ms=1_250,
            src_account="A:009999",
            dst_account="A:000777",
            channel="API",
        )
        second = self.sample(
            ts_ms=1_500,
            src_account="A:000123",
            dst_account="A:000125",
            channel="WEB",
        )
        interleaved.process(first)
        interleaved.process(other)
        interleaved_result = interleaved.process(second)
        isolated.process(first)
        isolated_result = isolated.process(second)

        self.assertEqual(interleaved_result["f"], isolated_result["f"])
        rows = interleaved._windows["A:000123"]
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row.endswith("\n") for row in rows))
        self.assertTrue(all(len(row.rstrip("\n").split("|")) == 5 for row in rows))
        expected_prefix = interleaved_result["dst_bid"][:6]
        self.assertEqual(rows[-1], f"TRANSFER|2.00|2|0|{expected_prefix}\n")
        self.assertEqual(len(rows[0].rstrip("\n").split("|")[-1]), 6)
        self.assertNotIn("A:000123", "".join(rows))
        self.assertNotIn("WEB", "".join(rows))
        self.assertNotIn("API", "".join(rows))
        self.assertEqual(_FIRST_GAP_MS, 60_000)
        self.assertEqual(GAP_BIN_BOUNDARIES_MS, (0, 100, 500, 1_000, 5_000, 15_000, 60_000))
        self.assertEqual(ABORT_BIN_BOUNDARIES, (0, 1, 2, 4, 8, 16, 32))
        self.assertEqual(rows[0], f"TRANSFER|2.00|6|0|{bid_for('A:000124', b'same-key')[:6]}\n")
        window_bytes = "".join(rows).encode("utf-8")
        expected_ratio = len(zlib.compress(window_bytes, level=6)) / len(window_bytes)
        self.assertEqual(interleaved_result["f"][6], expected_ratio)

    def test_rolling_counters_are_account_scoped_and_time_bounded(self):
        self.sensor.process(self.sample(ts_ms=1_000, aborted_retries_last_5s=1))
        second = self.sensor.process(
            self.sample(ts_ms=2_000, dst_account="A:000125", aborted_retries_last_5s=1)
        )
        self.assertEqual(second["f"][2], 1.0)
        self.assertEqual(second["f"][3], 2.0)
        self.assertEqual(second["f"][4], 2.0)
        self.assertEqual(
            self.sensor._windows["A:000123"][-1],
            f"TRANSFER|2.00|3|1|{bid_for('A:000125', b'test-consortium-key')[:6]}\n",
        )

        later = self.sensor.process(
            self.sample(ts_ms=70_000, dst_account="A:000126", aborted_retries_last_5s=0)
        )
        self.assertEqual(later["f"][2], 0.0)
        self.assertEqual(later["f"][3], 1.0)
        self.assertEqual(later["f"][4], 1.0)


if __name__ == "__main__":
    unittest.main()
