import json
import os
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from mesh.llm import SYSTEM_PROMPT, generate_brief


class RecordingAuditor:
    def __init__(self):
        self.calls = []

    def check(self, channel, payload):
        self.calls.append((channel, payload))
        return True


class RecordingScanner:
    def __init__(self):
        self.calls = []

    def scan(self, channel, text):
        self.calls.append((channel, text))
        return []


class LLMBriefTests(unittest.TestCase):
    def setUp(self):
        self.payload = {
            "chain": [
                {"bank": "bank-a", "bid": "BID_abc", "dt_ms": 0, "amt_bin": 2},
                {"bank": "bank-b", "bid": "BID_def", "dt_ms": 850, "amt_bin": 2},
            ],
            "p_cascade": 0.83,
            "anom": 0.72,
            "evidence": [
                {"feature": "hop_depth", "percentile": 98},
                {"feature": "amount_carry_over", "percentile": 94},
            ],
        }

    def test_template_fallback_is_deterministic_and_audited_first(self):
        with patch.dict(os.environ, {"LLM_API_URL": "", "LLM_API_KEY": ""}):
            auditor = RecordingAuditor()
            scanner = RecordingScanner()

            prompt_json, result = generate_brief(self.payload, scanner=scanner, auditor=auditor)

            self.assertEqual(auditor.calls, [("mesh_to_llm", self.payload)])
            self.assertEqual(result, generate_brief(self.payload, auditor=auditor)[1])
            self.assertEqual(prompt_json, json.dumps(self.payload, separators=(",", ":")))
            self.assertEqual(result["llm_mode"], "template")
            self.assertEqual(
                set(result),
                {"summary", "suspected_pattern", "risk_level", "recommended_action", "limits", "llm_mode"},
            )
            self.assertIn("0.83", result["summary"])
            self.assertIn("owning bank", result["recommended_action"])
            self.assertEqual(len(scanner.calls), 2)
            self.assertEqual(scanner.calls[0], ("llm_prompt", prompt_json))
            self.assertEqual(scanner.calls[1][0], "llm_response")

    def test_rejects_extra_top_level_fields_after_audit(self):
        auditor = RecordingAuditor()
        unsafe_payload = dict(self.payload, account_name="Alice Example")

        with self.assertRaisesRegex(ValueError, "contract"):
            generate_brief(unsafe_payload, auditor=auditor)

        self.assertEqual(auditor.calls, [("mesh_to_llm", unsafe_payload)])

    def test_api_receives_only_blind_payload_json_and_never_returns_key(self):
        with patch.dict(
            os.environ,
            {"LLM_API_URL": "http://127.0.0.1:9000/generate", "LLM_API_KEY": "secret-test-key"},
        ):
            auditor = RecordingAuditor()
            scanner = RecordingScanner()
            captured = {}

            class Response:
                text = '{"summary":"A cross-bank chain was scored.","suspected_pattern":"cross-bank chain","risk_level":"elevated","recommended_action":"Review the alert.","limits":"Synthetic blind features only."}'

                def raise_for_status(self):
                    pass

                def json(self):
                    return json.loads(self.text)

            def post(url, **kwargs):
                captured["url"] = url
                captured.update(kwargs)
                return Response()

            with patch.dict(sys.modules, {"requests": SimpleNamespace(post=post)}):
                prompt_json, result = generate_brief(self.payload, scanner=scanner, auditor=auditor)

            expected_json = json.dumps(self.payload, separators=(",", ":"))
            self.assertEqual(prompt_json, expected_json)
            self.assertEqual(captured["json"]["input"], expected_json)
            self.assertEqual(captured["json"]["system"], SYSTEM_PROMPT)
            self.assertEqual(captured["headers"]["Authorization"], "Bearer secret-test-key")
            self.assertEqual(auditor.calls, [("mesh_to_llm", self.payload)])
            self.assertEqual(result["llm_mode"], "local")
            self.assertNotIn("secret-test-key", json.dumps(result))
            self.assertEqual([channel for channel, _ in scanner.calls], ["llm_prompt", "llm_response"])

    def test_api_mode_requires_both_url_and_key(self):
        with patch.dict(
            os.environ,
            {"LLM_API_URL": "https://llm.example.test/generate", "LLM_API_KEY": ""},
        ):
            _, result = generate_brief(self.payload, auditor=RecordingAuditor())

        self.assertEqual(result["llm_mode"], "template")

    def test_api_errors_use_template_fallback(self):
        with patch.dict(
            os.environ,
            {"LLM_API_URL": "https://llm.example.test/generate", "LLM_API_KEY": "secret-test-key"},
        ):
            def post(url, **kwargs):
                raise RuntimeError("request failed")

            with patch.dict(sys.modules, {"requests": SimpleNamespace(post=post)}):
                _, result = generate_brief(self.payload, auditor=RecordingAuditor())

        self.assertEqual(result["llm_mode"], "template")
        self.assertNotIn("secret-test-key", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
