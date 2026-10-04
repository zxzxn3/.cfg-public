# Auxiliary scripts

Run commands below from the project root. The runtime entry point remains
`launcher.py`; these tools are not part of ordinary startup. Installation,
credentials and backup procedures live in the [root README](../README.md).

## Offline checks

```sh
python3 -m unittest discover -s scripts -p 'test_*.py'
```

| Script | Coverage |
| --- | --- |
| `test_launcher.py` | Keyring storage through stdin, lookup verification and invalid-secret handling |
| `test_integration.py` | Installed Codex against a local SSE server: shell and freeform apply_patch, full reasoning replay, effort and summary settings |
| `test_history_migration.py` | Mutation-log replay, duplicate variants with repeated prompts, non-overwriting inserts |
| `test_history_pruning.py` | Explicit deletion scope, deduplicated archive integrity, input-history preservation |

The integration test requires the installed Codex binary and uses a disposable
home. These tests do not use the saved credential or paid model inference.

## Opt-in live probes

These commands use the saved key and make metered DeepSeek API requests:

```sh
python3 scripts/probe_api.py deepseek-flash
python3 scripts/probe_api.py deepseek-v4-pro
python3 scripts/probe_session.py deepseek-flash
python3 scripts/probe_session.py deepseek-v4-pro
```

`probe_api.py` checks streaming reasoning, function/result round trips and
namespace calls. `probe_session.py` checks high/max reasoning, Flash image input,
compaction and recall using a separate stdio app-server. It does not restart the
existing daemon. Probes print structural results and usage, not credentials or
reasoning text. They do not establish benchmark parity or full-context coverage.

## History maintenance

`migrate_history.py` imports native Codex, OpenCode and Copilot history.
`prune_history.py` applies an explicit deletion manifest after archive checks.
Both are tied to the batch and database assumptions in their source; review
those before reuse. They are not startup or routine restore commands.

```sh
python3 scripts/migrate_history.py prepare --help
python3 scripts/migrate_history.py apply --help
python3 scripts/prune_history.py --help
```

Use a separate staging home and backups. Do not run against archived imports
through compatibility links or replace a live home with an old database.
Private recovery records are indexed in `~/Archives/codex/INDEX.md`.
