#!/usr/bin/env python3
"""Replace a literal placeholder inside a JSON document without breaking it.

Usage: render_json.py SRC DST PLACEHOLDER VALUE

VALUE is escaped as a JSON string, the original formatting of SRC is preserved,
and the result is re-parsed before it is written, so broken JSON is never
deployed. The placeholder must only appear inside JSON string values (true for
the EasyEffects preset files).
"""
import json
import sys


def main() -> int:
    if len(sys.argv) != 5:
        print("usage: render_json.py SRC DST PLACEHOLDER VALUE", file=sys.stderr)
        return 2
    src, dst, placeholder, value = sys.argv[1:5]
    try:
        with open(src, encoding="utf-8") as handle:
            text = handle.read()
        escaped = json.dumps(value)[1:-1]
        rendered = text.replace(placeholder, escaped)
        json.loads(rendered)
    except (OSError, ValueError) as exc:
        print(f"render_json: {src}: {exc}", file=sys.stderr)
        return 1
    with open(dst, "w", encoding="utf-8") as handle:
        handle.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
