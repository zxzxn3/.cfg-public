# Codex with DeepSeek

Launch Codex with DeepSeek in an independent `CODEX_HOME`. Requirements:
Linux, Codex CLI 0.158.0+, Python 3.11+, `secret-tool`, an unlocked desktop
Secret Service keyring, and a DeepSeek API key. Compatibility tests target
Codex 0.158.0; use the probes after changing the client or provider.

## Files

```text
~/.local/share/codex-deepseek/          launcher, models, scripts and documentation
~/.local/share/codex-deepseek/.codex/ private CODEX_HOME (default)
~/Archives/codex/                    private recovery records
~/.local/bin/codex-deepseek          symlink to launcher.py
```

cfg tracks the project source and ignores its private `.codex/` directory.
`launcher.py` resolves both code and `.codex/` relative to itself.
Private archives remain outside the project at `~/Archives/codex/`.

## Install

From `~/.local/share/codex-deepseek`:

```sh
chmod u+x launcher.py
python3 launcher.py install
~/.local/bin/codex-deepseek setup-key
```

`install` writes `config.toml` in the data home and creates the command link. It backs up
changed configuration as `.bak.N`, but does not merge custom settings. Generated
settings live in `launcher.py`; they include `approval_policy = "never"` and
`sandbox_mode = "danger-full-access"`. The model catalog is `models.json`.

`setup-key` stores the key through `secret-tool` under
`application=codex-deepseek provider=deepseek`. Authentication retrieves it
through a pipe, without a plaintext key in the project or child environment.
The keyring must be unlocked in the desktop session.

## Use

Put `~/.local/bin` on PATH, or use the full launcher path:

```sh
codex                                      # ordinary Codex home
codex-deepseek                             # DeepSeek Flash
codex-deepseek -m deepseek-v4-pro
codex-deepseek -c 'model_reasoning_effort="max"'
codex-deepseek resume --all
```

Only standalone `install` and `setup-key` are launcher commands; other arguments
pass to Codex. The child process uses the project's `.codex/`. Launch separate
sessions for ChatGPT and DeepSeek.

The provider uses native Responses streaming. Configuration and supported
models are defined in `launcher.py` and `models.json`; probes and tests cover
compatibility. See [scripts/README.md](scripts/README.md) for commands, including
opt-in paid probes.

## Backup and restore

Stop the processes using the data home, including its background server,
before copying the directory. A live backup needs a SQLite-aware snapshot.
Restore to a fresh destination at the same absolute path when possible.
A full project backup includes the hidden `.codex/` directory. Back up
`~/Archives/codex/` separately. Git does not preserve runtime data, archives
or the keyring.

Back up symlink targets separately: this installation shares `AGENTS.md`,
`memories/`, and individual user skills with `~/.codex/`. Inspect the actual
links when backing up; `install` does not create or manage them. Keep the
private archive when retaining the historical `imports` links. Recovery records
are indexed in `~/Archives/codex/INDEX.md` when a private backup is available.

If only the command link is missing, recreate it without running `install`:

```sh
mkdir -p ~/.local/bin
ln -s ~/.local/share/codex-deepseek/launcher.py ~/.local/bin/codex-deepseek
```

An existing correct link needs no change. Restore the keyring separately or
run `codex-deepseek setup-key` again. Ordinary `~/.codex` data and external
project working trees need their own backups.

Moving the project also changes `CODEX_HOME`. Review the command link,
model-catalog path, session database paths and external references before
resuming. `install` regenerates configuration without merging custom settings
or migrating history. Do not blanket-replace text in historical JSONL files.

## References

- [DeepSeek Codex integration](https://api-docs.deepseek.com/zh-cn/quick_start/agent_integrations/codex/)
- [DeepSeek Responses compatibility](https://api-docs.deepseek.com/guides/responses_api)
