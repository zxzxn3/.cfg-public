#!/usr/bin/env python3
"""Opt-in live engine test: high/max reasoning, image input, local compaction.

Runs its own stdio app-server in the isolated DeepSeek home. Does not connect
to or restart the user's existing Codex daemon/session.
"""
import json
from pathlib import Path
import queue
import struct
import subprocess
import sys
import tempfile
import threading
import time
import zlib

# Support direct execution from any working directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import launcher


def red_png(path: Path) -> None:
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    pixels = (b"\0" + b"\xff\0\0" * 32) * 32
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 32, 32, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b""))


def main(model: str) -> None:
    with tempfile.TemporaryDirectory(prefix="deepseek-session-probe-", dir=Path.home() / ".cache") as tmp:
        with tempfile.TemporaryFile(mode="w+") as errors:
            process = subprocess.Popen([str(Path.home() / ".local/bin/codex-deepseek"),
                "app-server", "--stdio", "--strict-config"], cwd=tmp, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=errors, text=True, bufsize=1)
            inbox = queue.Queue()

            def reader():
                for line in process.stdout:
                    inbox.put(json.loads(line))
                inbox.put({"error": "app-server exited"})

            threading.Thread(target=reader, daemon=True).start()
            counter = 0

            def send(method, params=None):
                nonlocal counter
                counter += 1
                process.stdin.write(json.dumps({"id": counter, "method": method, "params": params or {}}) + "\n")
                process.stdin.flush()
                return counter

            def wait_for(predicate, limit=120):
                deadline = time.monotonic() + limit
                messages = []
                while time.monotonic() < deadline:
                    message = inbox.get(timeout=max(0.1, deadline - time.monotonic()))
                    if "error" in message:
                        raise RuntimeError(str(message["error"]))
                    if message.get("method") == "error":
                        raise RuntimeError(str(message.get("params")))
                    messages.append(message)
                    if predicate(message):
                        return messages
                raise TimeoutError("Timed out waiting for app-server")

            def rpc(method, params=None):
                number = send(method, params)
                return wait_for(lambda m: m.get("id") == number)[-1]["result"]

            def turn(thread, inputs, effort):
                send("turn/start", {"threadId": thread, "input": inputs, "effort": effort})
                events = wait_for(lambda m: m.get("method") == "turn/completed")
                status = events[-1]["params"]["turn"]["status"]
                if status != "completed":
                    raise RuntimeError(f"Turn status: {status}")
                answers = [m["params"]["item"].get("text", "") for m in events
                           if m.get("method") == "item/completed" and m["params"]["item"].get("type") == "agentMessage"]
                return " ".join(answers)

            try:
                rpc("initialize", {"clientInfo": {"name": "deepseek-session-probe", "version": "1"},
                                   "capabilities": {"experimentalApi": True}})
                process.stdin.write('{"method":"initialized"}\n')
                process.stdin.flush()
                info = rpc("thread/start", {"model": model, "ephemeral": True, "cwd": tmp})
                thread = info["thread"]["id"]
                inputs = [{"type": "text", "text": "Remember the marker LANTERN-582 for later. Reply with the marker only. Do not use tools.", "text_elements": []}]
                if model == "deepseek-flash":
                    image = Path(tmp) / "test.png"
                    red_png(image)
                    inputs[0]["text"] = "Remember marker LANTERN-582 for later. Name the solid color of the attached image in English, then the marker. Do not use tools."
                    inputs.append({"type": "localImage", "path": str(image)})
                answer = turn(thread, inputs, "high")
                assert "LANTERN-582" in answer, "Initial response lost the marker"
                if model == "deepseek-flash":
                    assert "red" in answer.lower(), "Image was not recognized"
                print(f"PASS: {model} high reasoning" + (" and image input" if model == "deepseek-flash" else ""), flush=True)
                send("thread/compact/start", {"threadId": thread})
                events = wait_for(lambda m: m.get("method") == "turn/completed")
                assert events[-1]["params"]["turn"]["status"] == "completed", "Compaction failed"
                answer = turn(thread, [{"type": "text", "text": "What exact marker did I ask you to remember? Reply with only the marker; do not use tools.", "text_elements": []}], "max")
                assert "LANTERN-582" in answer, "Compaction lost the marker"
                print(f"PASS: {model} local compaction, post-compaction history, max reasoning", flush=True)
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == "__main__":
    model = sys.argv[1] if len(sys.argv) > 1 else "deepseek-flash"
    if model not in launcher.MODELS:
        raise SystemExit("Unknown model")
    main(model)
