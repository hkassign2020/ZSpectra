import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import requests

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
        with patch.dict(os.environ, {"LLM_URL": "", "LLM_API_KEY": ""}):
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
        captured = {}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                size = int(self.headers.get("Content-Length", "0"))
                captured["path"] = self.path
                captured["authorization"] = self.headers.get("Authorization")
                captured["body"] = json.loads(self.rfile.read(size))
                content = json.dumps(
                    {
                        "choices": [
                            {
                                "message": {
                                    "content": json.dumps(
                                        {
                                            "summary": "A cross-bank chain was scored.",
                                            "suspected_pattern": "cross-bank chain",
                                            "risk_level": "elevated",
                                            "recommended_action": "Review the alert.",
                                            "limits": "Synthetic blind features only.",
                                        }
                                    )
                                }
                            }
                        ]
                    }
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)

            def log_message(self, _format, *_args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        try:
            with patch.dict(
                os.environ,
                {
                    "LLM_URL": f"http://127.0.0.1:{server.server_port}/v1/chat/completions",
                    "LLM_API_KEY": "secret-test-key",
                    "LLM_MODEL": "granite-lab2",
                },
            ):
                auditor = RecordingAuditor()
                scanner = RecordingScanner()
                with patch("requests.post", wraps=requests.post) as post:
                    prompt_json, result = generate_brief(
                        self.payload, scanner=scanner, auditor=auditor
                    )

            expected_json = json.dumps(self.payload, separators=(",", ":"))
            self.assertEqual(captured["path"], "/v1/chat/completions")
            self.assertEqual(captured["authorization"], "Bearer secret-test-key")
            self.assertEqual(captured["body"]["model"], "granite-lab2")
            self.assertEqual(
                captured["body"]["messages"],
                [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": expected_json},
                ],
            )
            self.assertEqual(captured["body"]["max_tokens"], 150)
            self.assertEqual(captured["body"]["temperature"], 0.2)
            self.assertEqual(captured["body"]["response_format"], {"type": "json_object"})
            self.assertEqual(post.call_args.kwargs["timeout"], 60)
            self.assertEqual(prompt_json, expected_json)
            self.assertEqual(auditor.calls, [("mesh_to_llm", self.payload)])
            self.assertEqual(result["llm_mode"], "local")
            self.assertNotIn("secret-test-key", json.dumps(result))
            self.assertEqual([channel for channel, _ in scanner.calls], ["llm_prompt", "llm_response"])
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=2)

    def test_local_llm_request_does_not_require_an_api_key(self):
        captured = {}

        with patch.dict(
            os.environ,
            {"LLM_URL": "http://llama:8080/v1/chat/completions", "LLM_API_KEY": ""},
        ):
            def post(url, **kwargs):
                captured["url"] = url
                captured.update(kwargs)
                raise RuntimeError("test endpoint is not running")

            with patch.dict(sys.modules, {"requests": SimpleNamespace(post=post)}):
                _, result = generate_brief(self.payload, auditor=RecordingAuditor())

        self.assertEqual(captured["url"], "http://llama:8080/v1/chat/completions")
        self.assertNotIn("Authorization", captured["headers"])
        self.assertEqual(result["llm_mode"], "template")

    def test_api_mode_without_a_url_uses_the_template(self):
        with patch.dict(os.environ, {"LLM_URL": "", "LLM_API_KEY": "secret-test-key"}):
            _, result = generate_brief(self.payload, auditor=RecordingAuditor())

        self.assertEqual(result["llm_mode"], "template")

    def test_api_errors_use_template_fallback(self):
        with patch.dict(
            os.environ,
            {"LLM_URL": "https://llm.example.test/v1/chat/completions", "LLM_API_KEY": "secret-test-key"},
        ):
            def post(url, **kwargs):
                raise RuntimeError("request failed")

            with patch.dict(sys.modules, {"requests": SimpleNamespace(post=post)}):
                _, result = generate_brief(self.payload, auditor=RecordingAuditor())

        self.assertEqual(result["llm_mode"], "template")
        self.assertNotIn("secret-test-key", json.dumps(result))

    def test_slow_local_server_uses_template_after_the_request_timeout(self):
        class SlowHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                time.sleep(0.15)
                content = b'{}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                try:
                    self.wfile.write(content)
                except OSError:
                    pass

            def log_message(self, _format, *_args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), SlowHandler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        try:
            with patch.dict(
                os.environ,
                {
                    "LLM_URL": f"http://127.0.0.1:{server.server_port}/v1/chat/completions",
                    "LLM_API_KEY": "",
                    "LLM_MODEL": "slow-test-model",
                },
            ):
                _, result = generate_brief(
                    self.payload, auditor=RecordingAuditor(), timeout=0.02
                )
            self.assertEqual(result["llm_mode"], "template")
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
