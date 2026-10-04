"""Offline contract test using the installed Codex binary, never the real API."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import sys
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest

# Support direct execution from any working directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import launcher


class IntegrationTest(unittest.TestCase):
    def test_native_responses_tools_and_reasoning_round_trip(self):
        requests = []
        with tempfile.TemporaryDirectory(prefix="codex-deepseek-offline-", dir=Path.home() / ".cache") as tmp:
            root = Path(tmp)
            home = root / "codex-home"
            work = root / "work"
            home.mkdir()
            work.mkdir()
            (home / "models.json").write_text(json.dumps(launcher.catalog()))
            (home / "config.toml").write_text(
                launcher.configuration().replace(str(launcher.DATA), str(home))
                .replace(str(launcher.HERE / "models.json"), str(home / "models.json")))

            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *args):
                    pass

                def do_POST(self):
                    body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                    requests.append(body)
                    step = len(requests)
                    if step == 1:
                        output = [
                            {"id": "rs_1", "type": "reasoning", "summary": [],
                             "content": [{"type": "reasoning_text", "text": "Check the shell before editing."}]},
                            {"id": "fc_1", "type": "function_call", "call_id": "call_1",
                             "name": "exec_command", "arguments": json.dumps({
                                 "cmd": "printf DEEPSEEK_SHELL_OK", "workdir": str(work)})},
                        ]
                    elif step == 2:
                        output = [
                            {"id": "rs_2", "type": "reasoning", "summary": [],
                             "content": [{"type": "reasoning_text", "text": "Now create the requested file."}]},
                            {"id": "ct_1", "type": "custom_tool_call", "call_id": "call_2",
                             "name": "apply_patch", "input": "*** Begin Patch\n*** Add File: probe.txt\n+DEEPSEEK_PATCH_OK\n*** End Patch\n"},
                        ]
                    else:
                        output = [{"id": "msg_1", "type": "message", "role": "assistant",
                                   "content": [{"type": "output_text", "text": "DEEPSEEK_DONE"}]}]
                    events = [{"type": "response.created", "response": {"id": f"resp_{step}"}}]
                    for i, item in enumerate(output):
                        events.append({"type": "response.output_item.added", "output_index": i, "item": item})
                        if item["type"] == "reasoning":
                            events.append({"type": "response.reasoning_text.delta", "item_id": item["id"],
                                           "output_index": i, "content_index": 0, "delta": item["content"][0]["text"]})
                        events.append({"type": "response.output_item.done", "output_index": i, "item": item})
                    events.append({"type": "response.completed", "response": {
                        "id": f"resp_{step}", "status": "completed", "output": output,
                        "usage": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150,
                                  "input_tokens_details": {"cached_tokens": 0},
                                  "output_tokens_details": {"reasoning_tokens": 20}}}})
                    payload = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)

            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                args = ["codex", "exec", "--strict-config", "--skip-git-repo-check", "--ephemeral", "--json",
                        "-c", f'model_providers.deepseek.base_url="http://127.0.0.1:{server.server_port}"',
                        "-c", f'model_providers.deepseek.auth.command={json.dumps(shutil.which("printf"))}',
                        "-c", 'model_providers.deepseek.auth.args=["offline-test-token"]',
                        "-c", 'model_providers.deepseek.request_max_retries=0',
                        "-c", 'model_providers.deepseek.stream_max_retries=0',
                        "Use the shell and apply_patch to create probe.txt. Work only in the current directory."]
                env = {**os.environ, "CODEX_HOME": str(home)}
                for name in ("CODEX_THREAD_ID", "CODEX_SESSION_ID"):
                    env.pop(name, None)
                result = subprocess.run(args, cwd=work, env=env, capture_output=True, text=True, input="", timeout=40)
            finally:
                server.shutdown()
                server.server_close()
            self.assertEqual(result.returncode, 0, result.stderr[-1500:] + result.stdout[-3000:] + json.dumps(requests[0].get("tools", []))[:8000])
            self.assertEqual(len(requests), 3, result.stdout[-3000:])
            self.assertEqual((work / "probe.txt").read_text(), "DEEPSEEK_PATCH_OK\n")
            self.assertIn("DEEPSEEK_SHELL_OK", json.dumps(requests[1]["input"]))
            self.assertIn("DEEPSEEK_DONE", result.stdout)
            reasoning = [i for i in requests[2]["input"] if i.get("type") == "reasoning"]
            self.assertEqual([i["content"][0]["text"] for i in reasoning],
                             ["Check the shell before editing.", "Now create the requested file."])
            for body in requests:
                self.assertEqual(body["model"], "deepseek-flash")
                self.assertEqual(body["reasoning"]["effort"], "high")
                self.assertNotIn("summary", body["reasoning"])
                self.assertFalse(body["store"])
                self.assertNotIn("previous_response_id", body)
            print("Native CLI: streaming completion, shell, freeform apply_patch, full reasoning replay passed.")


if __name__ == "__main__":
    unittest.main()
