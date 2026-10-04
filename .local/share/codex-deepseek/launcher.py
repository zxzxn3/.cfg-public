#!/usr/bin/env python3
"""An isolated Codex CLI home for DeepSeek; never changes ~/.codex."""
from __future__ import annotations

import getpass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
DATA = HERE / ".codex"
KEY_ATTRIBUTES = ["application", "codex-deepseek", "provider", "deepseek"]
MODELS = ("deepseek-flash", "deepseek-v4-pro")


def catalog() -> dict:
    # Vendored official model metadata; no external project dependency.
    result = json.loads((HERE / "models.json").read_text())
    if {m["slug"] for m in result["models"]} != set(MODELS):
        raise ValueError("Upstream model catalog changed; review before installing")
    return result


def configuration() -> str:
    quote = json.dumps
    return f'''# Managed by codex-deepseek/launcher.py install.
# This file belongs only to the independent DeepSeek Codex home.
approval_policy = "never"
sandbox_mode = "danger-full-access"
model = "deepseek-flash"
model_provider = "deepseek"
model_reasoning_effort = "high"
model_reasoning_summary = "none"
show_raw_agent_reasoning = true
web_search = "disabled"
model_auto_compact_token_limit = 262144
model_catalog_json = {quote(str(HERE / "models.json"))}

[model_providers.deepseek]
name = "DeepSeek"
base_url = "https://api.deepseek.com"
wire_api = "responses"
requires_openai_auth = false
supports_websockets = false

# Codex reads the token through a pipe, without putting it in config or env.
[model_providers.deepseek.auth]
command = {quote(shutil.which("secret-tool") or "/usr/bin/secret-tool")}
args = {quote(["lookup", *KEY_ATTRIBUTES])}
refresh_interval_ms = 0
'''


def write_changed(path: Path, content: str, mode: int = 0o600) -> None:
    if path.exists() and path.read_text() == content:
        return
    if path.exists():
        n = 1
        while path.with_name(path.name + f".bak.{n}").exists():
            n += 1
        shutil.copy2(path, path.with_name(path.name + f".bak.{n}"))
    temporary = path.with_name(path.name + f".tmp.{os.getpid()}")
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        with os.fdopen(fd, "w") as stream:
            stream.write(content)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def install() -> None:
    version = subprocess.check_output(["codex", "--version"], text=True)
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", version)
    if not match or tuple(map(int, match.groups())) < (0, 158, 0):
        raise ValueError("Codex CLI >= 0.158.0 is required for this configuration")
    catalog()  # Validate the single project-owned model catalog.
    DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
    write_changed(DATA / "config.toml", configuration())
    bindir = Path.home() / ".local/bin"
    bindir.mkdir(parents=True, exist_ok=True)
    link = bindir / "codex-deepseek"
    if link.is_symlink() and link.resolve() == Path(__file__).resolve():
        pass
    elif link.exists() or link.is_symlink():
        raise ValueError(f"Refusing to replace an unrelated launcher: {link}")
    else:
        link.symlink_to(Path(__file__).resolve())
    print(f"Installed {link}\nIndependent Codex home: {DATA}")


def save_key() -> None:
    key = getpass.getpass("DeepSeek API key (hidden): ").strip()
    if not key or any(c.isspace() for c in key):
        raise ValueError("Empty key or whitespace in key")
    subprocess.run(["secret-tool", "store", "--label=Codex DeepSeek API key",
                    *KEY_ATTRIBUTES], input=key, text=True, capture_output=True,
                   check=True, timeout=60)
    if read_key() != key:
        raise ValueError("Keyring verification failed")
    print("Saved credential to the desktop Secret Service keyring")


def read_key() -> str:
    result = subprocess.run(["secret-tool", "lookup", *KEY_ATTRIBUTES],
                            capture_output=True, text=True, timeout=60)
    key = result.stdout.strip()
    if result.returncode or not key or any(c.isspace() for c in key):
        raise ValueError("Unlock your keyring or run codex-deepseek setup-key")
    return key


def check_key() -> None:
    read_key()


def main(args: list[str]) -> int:
    if args == ["install"]:
        install()
        return 0
    if args == ["setup-key"]:
        save_key()
        return 0
    if not (DATA / "config.toml").is_file():
        raise ValueError("Run python3 launcher.py install in the codex-deepseek project first")
    if args not in (["--help"], ["--version"]):
        check_key()
    env = os.environ.copy()
    # Intended CODEX_HOME override: isolate auth, daemon, history and model catalog.
    env["CODEX_HOME"] = str(DATA)
    for name in ("CODEX_THREAD_ID", "CODEX_SESSION_ID"):
        env.pop(name, None)
    os.execvpe("codex", ["codex", *args], env)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"codex-deepseek: {error}", file=sys.stderr)
        raise SystemExit(1)
