"""Actual stdio MCP + Responses SDK transport against a local deterministic test server."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import asyncio
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import threading
import unittest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class MCPIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_stdio_discovery_and_read_only_investigation(self):
        with tempfile.TemporaryDirectory(prefix="trajectory-mcp-test-") as directory:
            base = Path(directory)
            repo = base / "repo"
            repo.mkdir()
            source = "def value(config):\n    return config['timeout']\n"
            (repo / "settings.py").write_text(source)
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            report = {"status": "completed", "summary": "Reads timeout from config.",
                      "findings": [{"claim": "value reads config['timeout'].", "evidence": [
                          {"path": "settings.py", "start_line": 1, "end_line": 2, "quote": "return config['timeout']"}]}],
                      "relations": [], "related_tests": [], "unresolved_questions": [],
                      "criteria_coverage": [{"criterion_index": 0, "status": "supported", "finding_indices": [0],
                                             "relation_indices": [], "test_indices": [], "explanation": "Source confirms it."}]}
            review = {"supported": True, "unsupported_claims": [], "criteria_supported": [0], "notes": []}
            outputs = [
                [{"type": "function_call", "id": "fc_1", "name": "read_source_ranges", "call_id": "read_1",
                  "arguments": json.dumps({"ranges": [{"path": "settings.py", "start_line": 1, "end_line": 2}]})}],
                [{"type": "message", "id": "msg_1", "role": "assistant", "status": "completed",
                  "content": [{"type": "output_text", "text": json.dumps(report), "annotations": []}]}],
                [{"type": "message", "id": "msg_2", "role": "assistant", "status": "completed",
                  "content": [{"type": "output_text", "text": json.dumps(review), "annotations": []}]}],
            ]
            requests = []

            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *args):
                    pass

                def do_POST(self):
                    body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                    requests.append(body)
                    output = outputs[len(requests) - 1]
                    payload = {"id": f"resp_{len(requests)}", "object": "response", "created_at": 0,
                               "status": "completed", "model": "test-model", "output": output,
                               "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15,
                                         "input_tokens_details": {"cached_tokens": 0},
                                         "output_tokens_details": {"reasoning_tokens": 1}}}
                    encoded = json.dumps(payload).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(encoded)))
                    self.end_headers()
                    self.wfile.write(encoded)

            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            checks = base / "checks.json"
            checks.write_text(json.dumps({"checks": [{"id": "timeout", "argv": ["{python}", "-c",
                "from settings import value; assert value({'timeout': 7}) == 14"]},
                {"id": "regression", "argv": ["{python}", "-m", "pytest", "-q", "{test_file}", "{junit_option}"]}]}), encoding="utf-8")
            params = StdioServerParameters(command=sys.executable,
                args=["-m", "trajectory_offload", "serve", "--repo", str(repo), "--model", "test-model",
                      "--checks", str(checks), "--log-dir", str(base / "logs"), "--max-seconds", "30"],
                env={**os.environ, "OPENAI_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1", "OPENAI_API_KEY": "test-only"})
            try:
                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        listing = await session.list_tools()
                        self.assertEqual([tool.name for tool in listing.tools],
                                         ["get_repository_state", "investigate_repository", "cluster_test_failures",
                                          "construct_regression_test", "augment_test_coverage", "verify_patch", "diagnose_failure"])
                        self.assertTrue(listing.tools[0].annotations.readOnlyHint)
                        self.assertTrue(listing.tools[2].annotations.readOnlyHint)
                        self.assertFalse(listing.tools[3].annotations.readOnlyHint)
                        state = (await session.call_tool("get_repository_state", {})).structuredContent
                        self.assertTrue(state["execution_enabled"])
                        self.assertIn("recon", state)
                        self.assertEqual(state["recon"]["stats"]["total_files"], 1)
                        result = await asyncio.wait_for(session.call_tool("investigate_repository", {
                            "objective": "Locate the timeout configuration consumer.", "semantic_review": True}), timeout=30)
                        self.assertFalse(result.isError)
                        structured = result.structuredContent
                        self.assertEqual(structured["status"], "completed")
                        self.assertEqual(structured["format_version"], 3)
                        self.assertEqual(structured["verification"]["semantic_review_status"], "passed")
                        ref = structured["findings"][0]["evidence"][0]["id"]
                        self.assertEqual(structured["evidence"][ref]["quote"], "return config['timeout']")
                        self.assertNotIn("mode", structured)
                        self.assertNotIn("log_path", structured)
                        self.assertEqual(structured["metrics"]["model_calls"], 3)
                        self.assertTrue(structured["verification"]["snapshot_unchanged"])
                        self.assertEqual((repo / "settings.py").read_text(), source)
                        self.assertEqual(len(requests), 3)
                        outputs.append([{"type": "message", "id": "msg_3", "role": "assistant", "status": "completed",
                                         "content": [{"type": "output_text", "text": json.dumps(report), "annotations": []}]}])
                        unreviewed = (await session.call_tool("investigate_repository", {
                            "objective": "Locate the timeout configuration consumer."})).structuredContent
                        self.assertEqual(unreviewed["status"], "completed")
                        self.assertEqual(unreviewed["verification"]["semantic_review_status"], "not_requested")
                        self.assertFalse(unreviewed["verification"]["semantic_support_checked"])
                        self.assertEqual(unreviewed["metrics"]["model_calls"], 1)
                        self.assertEqual(len(requests), 4)
                        patch_text = ("--- a/settings.py\n+++ b/settings.py\n@@ -1,2 +1,2 @@\n"
                                      " def value(config):\n-    return config['timeout']\n+    return config['timeout'] * 2\n")
                        verified = (await session.call_tool("verify_patch", {
                            "objective": "Verify the timeout doubling patch.", "snapshot_id": state["snapshot_id"],
                            "patch": patch_text, "check_ids": ["timeout"]})).structuredContent
                        self.assertEqual(verified["status"], "completed")
                        self.assertEqual(verified["verdict"], "passed")
                        self.assertEqual(verified["metrics"]["process_calls"], 2)
                        self.assertEqual(verified["metrics"]["model_calls"], 0)
                        self.assertNotIn("log_path", verified)
                        self.assertEqual((repo / "settings.py").read_text(), source)
                        grouped = (await session.call_tool("cluster_test_failures", {
                            "snapshot_id": state["snapshot_id"], "failures": [
                                {"test_id": "a", "kind": "failure", "message": "AssertionError: x"},
                                {"test_id": "b", "kind": "failure", "message": "AssertionError: x"}]})).structuredContent
                        self.assertEqual(grouped["groups"][0]["observation_ids"], ["F1", "F2"])
                        self.assertEqual(grouped["metrics"]["model_calls"], 0)
                        draft = {"test_source": "from settings import value\ndef test_timeout():\n"
                                 "    assert value({'timeout': 7}) == 14, 'TRAJECTORY_REGRESSION'\n",
                                 "scenarios": ["timeout should double"], "unresolved_questions": []}
                        outputs.append([{"type": "message", "id": "msg_4", "role": "assistant", "status": "completed",
                                         "content": [{"type": "output_text", "text": json.dumps(draft), "annotations": []}]}])
                        constructed = (await session.call_tool("construct_regression_test", {
                            "objective": "Add a timeout regression test.", "expected_behavior": "Timeout 7 returns 14.",
                            "snapshot_id": state["snapshot_id"], "source_patch": patch_text,
                            "test_file": "tests/test_regression.py", "check_id": "regression"})).structuredContent
                        self.assertEqual((constructed["status"], constructed["verdict"]), ("completed", "passed"))
                        self.assertEqual(constructed["metrics"]["process_calls"], 2)
                        self.assertFalse((repo / "tests/test_regression.py").exists())
                        outputs.append([{"type": "message", "id": "msg_5", "role": "assistant", "status": "completed",
                                         "content": [{"type": "output_text", "text": json.dumps(draft), "annotations": []}]}])
                        edited = (await session.call_tool("construct_regression_test", {
                            "objective": "Add a timeout regression test.", "expected_behavior": "Timeout 7 returns 14.",
                            "snapshot_id": state["snapshot_id"], "source_edits": [{"path": "settings.py",
                                "old": "return config['timeout']", "new": "return config['timeout'] * 2"}],
                            "test_file": "tests/test_regression.py", "check_id": "regression"})).structuredContent
                        self.assertEqual((edited["status"], edited["verdict"]), ("completed", "passed"))
                        self.assertFalse((repo / "tests/test_regression.py").exists())
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
