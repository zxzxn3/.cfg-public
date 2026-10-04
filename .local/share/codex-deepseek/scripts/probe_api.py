#!/usr/bin/env python3
"""Small opt-in live API checks. Never prints the credential or reasoning text.

Usage: python3 scripts/probe_api.py [deepseek-flash|deepseek-v4-pro]
Makes metered requests to the official DeepSeek endpoint.
"""
from collections import Counter
import json
from pathlib import Path
import sys
import urllib.error
import urllib.request

# Support direct execution from any working directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import launcher


def request(body: dict) -> tuple[list[dict], dict]:
    token = launcher.read_key()
    req = urllib.request.Request(
        "https://api.deepseek.com/responses", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + token})
    events = []
    try:
        with urllib.request.urlopen(req, timeout=60) as stream:
            for line in stream:
                if line.startswith(b"data:"):
                    events.append(json.loads(line[5:].strip()))
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace").replace(token, "[redacted]")
        raise RuntimeError(f"API HTTP {error.code}: {detail[:1500]}") from None
    terminal = [e for e in events if e.get("type") in
                ("response.completed", "response.incomplete", "response.failed")]
    if not terminal or terminal[-1]["type"] != "response.completed":
        raise RuntimeError("Response did not complete: " + json.dumps(terminal)[-1500:])
    response = terminal[-1]["response"]
    print(json.dumps({"model": body["model"], "events": dict(Counter(e["type"] for e in events)),
                      "usage": response.get("usage"), "output_types": [i["type"] for i in response["output"]]}), flush=True)
    return events, response


def main(model: str) -> None:
    base = {"model": model, "stream": True, "store": False,
            "reasoning": {"effort": "high"}, "max_output_tokens": 2048}
    tool = {"type": "function", "name": "double_number", "description": "Return twice the input integer.",
            "parameters": {"type": "object", "properties": {"number": {"type": "integer"}},
                           "required": ["number"], "additionalProperties": False}}
    history = [{"role": "user", "content": "Use double_number on 21, then report its result. You must call the tool."}]
    events, response = request({**base, "input": history, "tools": [tool], "tool_choice": "auto"})
    calls = [item for item in response["output"] if item["type"] == "function_call"]
    assert calls and calls[0]["name"] == "double_number", "Function call missing"
    reasoning = [item for item in response["output"] if item["type"] == "reasoning"]
    assert any(item.get("content") for item in reasoning), "Plain reasoning content missing"
    assert any(e["type"] == "response.reasoning_text.delta" for e in events), "Reasoning stream missing"
    history.extend(response["output"])
    for call in calls:
        value = json.loads(call["arguments"])["number"]
        history.append({"type": "function_call_output", "call_id": call["call_id"], "output": str(2 * value)})
    _, answer = request({**base, "input": history, "tools": [tool], "tool_choice": "auto"})
    assert "42" in json.dumps(answer["output"]), "Tool result missing from answer"
    print("PASS: reasoning stream and multi-turn function call with reasoning replay", flush=True)

    # Codex 0.158 emits namespace tools for collaboration and some MCP tools.
    namespaced = {"type": "namespace", "name": "arithmetic", "description": "Integer arithmetic.",
                  "tools": [tool]}
    try:
        _, response = request({**base, "input": [{"role": "user", "content":
            "Call arithmetic.double_number with number 21. Do not answer without calling the tool."}],
            "tools": [namespaced], "tool_choice": "auto"})
        calls = [item for item in response["output"] if item["type"] == "function_call"]
        assert calls and calls[0].get("namespace") == "arithmetic", "Namespace call not preserved"
        print("PASS: native namespace tool", flush=True)
    except (RuntimeError, AssertionError) as error:
        print(f"NAMESPACE INCOMPATIBILITY: {error}", flush=True)
        raise SystemExit(2)


if __name__ == "__main__":
    selected = sys.argv[1] if len(sys.argv) > 1 else "deepseek-flash"
    if selected not in launcher.MODELS:
        raise SystemExit("Unknown model")
    main(selected)
