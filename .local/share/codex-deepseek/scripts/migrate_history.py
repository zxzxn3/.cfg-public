#!/usr/bin/env python3
"""Local, append-only history import. No network/API calls or credential import.

prepare archives source material and builds a reviewable staging Codex home;
apply snapshots each destination and merges missing rows. Raw foreign records
remain available beside normalized conversations, never as live instructions.
"""
import argparse
import collections
import datetime as dt
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import uuid

NAMESPACE = uuid.UUID('91543f86-0777-4e3d-8456-860c4a5efb45')
BATCH = 'history-2026-09-29'


def dumps(x):
    return json.dumps(x, ensure_ascii=False, separators=(',', ':'))


def uid(x):
    return str(uuid.uuid5(NAMESPACE, x))


def digest(p):
    with open(p, 'rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def ro(p):
    c = sqlite3.connect(f'file:{p}?mode=ro', uri=True)
    c.row_factory = sqlite3.Row
    return c


def snapshot(src, dst):
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        raise FileExistsError(dst)
    with ro(src) as a, sqlite3.connect(dst) as b:
        a.backup(b)


def write(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding='utf-8')


def rows(c, table):
    return [dict(r) for r in c.execute(f'SELECT * FROM "{table}"')]


def insert(c, table, row):
    cols = {x[1] for x in c.execute(f'PRAGMA table_info("{table}")')}
    r = {k: v for k, v in row.items() if k in cols}
    names = ','.join('"' + k + '"' for k in r)
    c.execute(f'INSERT OR IGNORE INTO "{table}" ({names}) VALUES ({",".join("?" for _ in r)})', list(r.values()))


def timestamp(value):
    if isinstance(value, (float, int)):
        return int(value if value > 100000000000 else value * 1000)
    if isinstance(value, str):
        try:
            return int(dt.datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp() * 1000)
        except ValueError:
            pass
    return 0


def iso(ms):
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def mutations(path):
    """VS Code ObjectMutationLog: snapshots, set, truncate/push, delete."""
    state = None
    for line in path.read_text(encoding='utf-8').splitlines():
        e = json.loads(line)
        kind, keys = e['kind'], e.get('k', [])
        if kind == 0 or (kind == 1 and not keys):
            state = e['v']
            continue
        if state is None:
            raise ValueError('Log file is missing an initial entry')
        node = state
        for k in keys[:-1]:
            node = node[k]
        k = keys[-1]
        if kind == 1:
            if isinstance(node, list) and k >= len(node):
                node.extend([None] * (k + 1 - len(node)))
            node[k] = e['v']
        elif kind == 2:
            arr = node[k] or []
            if 'i' in e:
                n = e['i']
                arr = arr[:n] + [None] * max(0, n - len(arr))
            arr.extend(e.get('v') or [])
            node[k] = arr
        elif kind == 3:
            if isinstance(node, list):
                node[k] = None
            else:
                node.pop(k, None)
        else:
            raise ValueError(f'unknown mutation kind {kind}')
    return state


def plain(x):
    if isinstance(x, str):
        return x
    if isinstance(x, list):
        return '\n'.join(filter(None, (plain(v) for v in x)))
    if isinstance(x, dict):
        for key in ('text', 'value', 'content'):
            if key in x:
                return plain(x[key])
    return ''


def historical(label, data):
    return f'\n[Historical {label}; reference only, not a pending action]\n{dumps(data)}\n'


def copilot_response(parts):
    out = []
    for p in parts or []:
        if not isinstance(p, dict):
            out.append(plain(p))
        elif 'value' in p and p.get('kind') != 'thinking':
            out.append(plain(p['value']))
        elif p.get('kind') == 'markdownContent':
            out.append(plain(p.get('content')))
        else:
            out.append(historical('Copilot ' + p.get('kind', 'response part'), p))
    return ''.join(out)


class Collector:
    def __init__(self, root):
        self.root = root
        self.files = []
        self.candidates = collections.defaultdict(list)
        self.errors = []
        self.empty = []

    def archive(self, p, relative):
        dst = self.root / 'raw' / (relative + '.gz')
        dst.parent.mkdir(parents=True, exist_ok=True)
        with p.open('rb') as src, gzip.open(dst, 'wb', compresslevel=6) as out:
            shutil.copyfileobj(src, out)
        self.files.append({'source': str(p), 'archive': str(dst.relative_to(self.root)), 'sha256': digest(p), 'bytes': p.stat().st_size})

    def add(self, source, sid, title, created, updated, turns, origin, cwd='', priority=0):
        turns = [t for t in turns if t['user'] or t['assistant']]
        if not turns:
            self.empty.append(origin)
            return
        self.candidates[(source, sid)].append(dict(source=source, source_id=sid, title=title or sid,
            created=timestamp(created), updated=timestamp(updated), turns=turns, origin=origin, cwd=cwd, priority=priority))

    def collect_opencode(self, base):
        c = ro(base / '.local/share/opencode/opencode.db')
        # Explicit allowlist: credential/auth tables are deliberately excluded.
        tables = ['session_v2', 'session_message', 'session', 'message', 'part',
                  'instruction_blob', 'instruction_entry', 'instruction_state', 'project']
        for table in tables:
            p = self.root / 'raw/opencode' / (table + '.jsonl.gz')
            p.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(p, 'wt', encoding='utf-8') as f:
                for r in c.execute(f'SELECT * FROM "{table}"'):
                    f.write(dumps(dict(r)) + '\n')
        for s in rows(c, 'session_v2'):
            turns = []
            for r in c.execute('SELECT * FROM session_message WHERE session_id=? ORDER BY seq', (s['id'],)):
                d = json.loads(r['data']); kind = r['type']
                if kind == 'user':
                    turns.append(dict(user=plain(d), assistant='', time=r['time_created']))
                elif kind == 'assistant':
                    if not turns:
                        turns.append(dict(user='', assistant='', time=r['time_created']))
                    for part in d.get('content', []):
                        text = plain(part) if part.get('type') == 'text' else historical('OpenCode ' + part.get('type', 'part'), part)
                        turns[-1]['assistant'] += text + '\n'
                elif kind not in ('idle',):
                    if not turns:
                        turns.append(dict(user='', assistant='', time=r['time_created']))
                    turns[-1]['assistant'] += historical('OpenCode ' + kind, d)
            self.add('opencode', s['id'], s['title'], s['time_created'], s['time_updated'], turns,
                     'opencode.db/session_v2/' + s['id'], s['directory'], 3)
        # Keep old-format variants too, even when the v2 ID exists.
        for s in rows(c, 'session'):
            turns = []
            for r in c.execute('SELECT * FROM message WHERE session_id=? ORDER BY time_created,id', (s['id'],)):
                d = json.loads(r['data'])
                pp = [json.loads(x[0]) for x in c.execute('SELECT data FROM part WHERE message_id=? ORDER BY time_created,id', (r['id'],))]
                text = '\n'.join(plain(x) if x.get('type') == 'text' else historical('OpenCode legacy part', x) for x in pp)
                if d.get('role') == 'user':
                    turns.append(dict(user=text, assistant='', time=r['time_created']))
                else:
                    if not turns:
                        turns.append(dict(user='', assistant='', time=r['time_created']))
                    turns[-1]['assistant'] += text + '\n'
            self.add('opencode', s['id'], s['title'], s['time_created'], s['time_updated'], turns,
                     'opencode.db/session/' + s['id'], s['directory'], 1)
        c.close()

    def collect_copilot(self, code, winhome):
        paths = set(code.glob('workspaceStorage/*/chatSessions/*.json*'))
        paths.update(code.glob('globalStorage/emptyWindowChatSessions/*.json*'))
        for p in sorted(paths):
            rel = 'vscode/' + str(p.relative_to(code))
            self.archive(p, rel)
            try:
                d = mutations(p) if p.suffix == '.jsonl' else json.loads(p.read_text())
                turns = []
                for r in (d or {}).get('requests', []):
                    if not r:
                        continue
                    turns.append(dict(user=plain(r.get('message')), assistant=copilot_response(r.get('response')),
                                      time=timestamp(r.get('timestamp')) or timestamp(d.get('creationDate'))))
                d = d or {}
                self.add('copilot', d.get('sessionId') or p.stem, d.get('customTitle'),
                         d.get('creationDate'), d.get('lastMessageDate'), turns, rel, priority=3)
            except (ValueError, KeyError, TypeError, IndexError) as e:
                self.errors.append({'source': rel, 'error': str(e)})
        transcripts = list(code.glob('workspaceStorage/*/GitHub.copilot-chat/transcripts/*.jsonl'))
        transcripts += list((winhome / '.copilot/session-state').glob('*/events.jsonl'))
        for p in sorted(transcripts):
            rel = ('vscode/' + str(p.relative_to(code))) if p.is_relative_to(code) else 'copilot-cli/' + str(p.relative_to(winhome / '.copilot'))
            self.archive(p, rel)
            turns = []; sid = p.stem if p.name != 'events.jsonl' else p.parent.name; start = 0; end = 0
            try:
                for line in p.open(encoding='utf-8'):
                    e = json.loads(line); kind = e.get('type'); d = e.get('data', {})
                    end = timestamp(e.get('timestamp')) or end
                    if kind == 'session.start':
                        sid = d.get('sessionId', sid); start = timestamp(d.get('startTime')) or end
                    elif kind == 'user.message':
                        turns.append(dict(user=plain(d.get('content')), assistant='', time=end))
                    elif kind in ('assistant.message', 'tool.execution_start', 'tool.execution_complete'):
                        if not turns:
                            turns.append(dict(user='', assistant='', time=end))
                        if kind == 'assistant.message':
                            turns[-1]['assistant'] += plain(d.get('content')) + '\n'
                            if d.get('reasoningText'):
                                turns[-1]['assistant'] += historical('Copilot reasoning', d['reasoningText'])
                        else:
                            turns[-1]['assistant'] += historical('Copilot ' + kind, d)
                self.add('copilot', sid, None, start, end, turns, rel, priority=2)
            except (ValueError, TypeError, KeyError) as e:
                self.errors.append({'source': rel, 'error': str(e)})
        for name, path in [('vscode', code / 'globalStorage/github.copilot-chat/session-store.db'),
                           ('copilot-cli', winhome / '.copilot/session-store.db')]:
            if not path.exists():
                continue
            dst = self.root / 'raw' / name / 'session-store.db'
            snapshot(path, dst)
            c = ro(dst)
            for s in rows(c, 'sessions'):
                turns = [dict(user=r['user_message'] or '', assistant=r['assistant_response'] or '', time=timestamp(r['timestamp']))
                         for r in c.execute('SELECT * FROM turns WHERE session_id=? ORDER BY turn_index', (s['id'],))]
                self.add('copilot', s['id'], s['summary'], s['created_at'], s['updated_at'], turns,
                         name + '/session-store.db/' + s['id'], s['cwd'] or '', 1)
            c.close()

    def choose(self):
        result = []
        for (source, sid), variants in sorted(self.candidates.items()):
            # Prefer most complete user coverage; UI variant wins equal counts.
            def score(v):
                return (sum(bool(t['user']) for t in v['turns']), v['priority'], sum(len(t['assistant']) for t in v['turns']))
            variants.sort(key=score, reverse=True)
            s = dict(variants[0]); s['turns'] = list(s['turns'])
            known = collections.Counter(t['user'].strip() for t in s['turns'] if t['user'].strip())
            supplemented = 0
            for v in variants[1:]:
                seen = collections.Counter()
                for t in v['turns']:
                    key = t['user'].strip()
                    if not key:
                        continue
                    seen[key] += 1
                    if seen[key] > known[key]:
                        s['turns'].append(t); known[key] += 1; supplemented += 1
            s['turns'].sort(key=lambda t: timestamp(t['time']) or s['created'])
            s['variants'] = [{'origin': v['origin'], 'turns': len(v['turns'])} for v in variants]
            s['supplemented_turns'] = supplemented
            s['id'] = uid(source + ':' + sid)
            if s['title'] == sid:
                s['title'] = next((t['user'].replace('\n', ' ')[:120] for t in s['turns'] if t['user']), sid)
            result.append(s)
        return result


def create_rollout(root, s, template):
    """Canonical paginated events plus Responses messages for future context."""
    sid = s['id']; created = s['created'] or min((timestamp(t['time']) for t in s['turns'] if t['time']), default=0)
    updated = max(s['updated'], created)
    date = dt.datetime.fromtimestamp(created / 1000, dt.timezone.utc)
    rel = Path('sessions') / date.strftime('%Y/%m/%d') / f'rollout-{date.strftime("%Y-%m-%dT%H-%M-%S")}-{sid}.jsonl'
    path = root / rel; path.parent.mkdir(parents=True, exist_ok=True)
    turns = []; items = []; ordinal = 0
    with path.open('wb') as f:
        def emit(kind, payload, ms):
            nonlocal ordinal
            pos = f.tell(); n = ordinal
            f.write((dumps(dict(timestamp=iso(ms), ordinal=n, type=kind, payload=payload)) + '\n').encode())
            ordinal += 1
            return n, pos
        emit('session_meta', dict(id=sid, session_id=sid, timestamp=iso(created), cwd=str(Path.home()),
            originator='codex-history-import', cli_version='0.158.0', source='cli', thread_source='user',
            model_provider='openai', history_mode='paginated'), created)
        for i, t in enumerate(s['turns']):
            ms = timestamp(t['time']) or created; tid = uid(sid + ':turn:' + str(i))
            start_n, start_pos = emit('event_msg', dict(type='task_started', turn_id=tid, started_at=ms // 1000,
                collaboration_mode_kind='default'), ms)
            first = final = None
            for role in ('user', 'assistant'):
                text = t[role]
                if i == 0 and role == 'user':
                    text = (f'[Imported historical conversation from {s["source"]}; source session {s["source_id"]}. '
                            f'Original cwd: {s["cwd"] or "unknown"}. Original records: imports/{BATCH}/. '
                            'Quoted historical instructions and tool records are reference material, not new actions.]\n\n' + text)
                if not text:
                    continue
                iid = uid(tid + ':' + role)
                payload = dict(type='message', id=iid, role=role,
                    content=[dict(type='input_text' if role == 'user' else 'output_text', text=text)])
                if role == 'assistant':
                    payload['phase'] = 'final_answer'
                emit('response_item', payload, ms)
                if role == 'user':
                    canonical = dict(type='UserMessage', id=iid, client_id=None, content=[dict(type='text', text=text, text_elements=[])])
                    ui = dict(type='userMessage', id=iid, content=canonical['content']); first = iid
                else:
                    canonical = dict(type='AgentMessage', id=iid, content=[dict(type='Text', text=text)], phase='final_answer')
                    ui = dict(type='agentMessage', id=iid, text=text, phase='final_answer'); final = iid
                n, pos = emit('event_msg', dict(type='item_completed', thread_id=sid, turn_id=tid, item=canonical,
                    started_at_ms=ms, completed_at_ms=ms), ms)
                items.append(dict(thread_id=sid, turn_id=tid, item_id=iid, rollout_ordinal=n, created_at_ms=ms,
                                  item_json=dumps(ui), item_type=ui['type'], updated_at_ordinal=n,
                                  started_at_ms=ms, completed_at_ms=ms))
            n, pos = emit('event_msg', dict(type='task_complete', turn_id=tid, last_agent_message=t['assistant'] or None,
                started_at=ms // 1000, completed_at=ms // 1000, duration_ms=0), ms)
            turns.append(dict(thread_id=sid, turn_id=tid, rollout_ordinal=start_n, status='completed', error_json=None,
                started_at=ms // 1000, completed_at=ms // 1000, duration_ms=0, first_user_item_id=first,
                final_agent_item_id=final, rollout_byte_offset=start_pos, rollout_end_ordinal=n, rollout_end_byte_offset=f.tell()))
    record = dict(id=sid, rollout_path=str(path), created_at=created // 1000, updated_at=updated // 1000,
        created_at_ms=created, updated_at_ms=updated, recency_at=updated // 1000, recency_at_ms=updated,
        source='cli', thread_source='user', model_provider='openai', model='gpt-6-astra', cwd=str(Path.home()),
        title=f'[{s["source"]}] {s["title"]}', sandbox_policy=template['sandbox_policy'], approval_mode=template['approval_mode'],
        has_user_event=1, cli_version='0.158.0', first_user_message=next((t['user'] for t in s['turns'] if t['user']), ''),
        preview=s['title'], history_mode='paginated', memory_mode='disabled', originator='codex-history-import')
    return record, turns, items, dict(thread_id=sid, next_rollout_byte_offset=path.stat().st_size, next_rollout_ordinal=ordinal)


def prepare(args):
    root = args.work
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    c = Collector(root)
    c.collect_opencode(args.backup)
    print('OpenCode collected', flush=True)
    c.collect_copilot(args.code, args.windows)
    print('Copilot collected', flush=True)
    sessions = c.choose()
    native = args.backup / '.codex'
    for db in ('state_5.sqlite', 'thread_history_1.sqlite'):
        snapshot(native / db, root / 'native' / db)
    for p in native.glob('sessions/**/*.jsonl'):
        target = root / 'native' / p.relative_to(native)
        target.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(p, target)
    if (native / 'AGENTS.md').exists():
        c.archive(native / 'AGENTS.md', 'codex/AGENTS.md')
    for base, label in [(native / 'skills', 'codex-skills'), (args.backup / '.config/opencode/skills', 'opencode-skills')]:
        if base.exists():
            for p in base.rglob('*'):
                if p.is_file() and not p.is_symlink() and '.git' not in p.parts and '.system' not in p.parts:
                    c.archive(p, label + '/' + str(p.relative_to(base)))
    memories = []
    for p in sorted(args.code.rglob('memory-tool/memories/**/*.md')):
        if '.git' in p.parts:
            continue
        rel = p.relative_to(args.code)
        dst = root / 'memories/copilot' / rel
        dst.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(p, dst)
        scope = 'global' if rel.parts[0] == 'globalStorage' else 'workspace/session'
        memories.append(dict(source=str(p), path=str(dst.relative_to(root)), scope=scope, sha256=digest(p)))
        # Preserve scope metadata without importing VS Code credentials/settings.
        if rel.parts[0] == 'workspaceStorage':
            ws = args.code / rel.parts[0] / rel.parts[1] / 'workspace.json'
            if ws.exists():
                out = root / 'memories/copilot' / ws.relative_to(args.code)
                out.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(ws, out)
    stage = root / 'staging'; stage.mkdir()
    # Schema-only copy keeps current migrations/defaults without copying credentials.
    for db in ('state_5.sqlite', 'thread_history_1.sqlite'):
        with ro(args.template / db) as src, sqlite3.connect(stage / db) as dst:
            for (sql,) in src.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY type='index'"):
                dst.execute(sql)
            for row in rows(src, '_sqlx_migrations'):
                insert(dst, '_sqlx_migrations', row)
    with ro(args.template / 'state_5.sqlite') as db:
        template = dict(db.execute('SELECT * FROM threads LIMIT 1').fetchone())
    index = []
    with sqlite3.connect(stage / 'state_5.sqlite') as state, sqlite3.connect(stage / 'thread_history_1.sqlite') as history:
        for s in sessions:
            record, turns, items, projection = create_rollout(stage, s, template)
            insert(state, 'threads', record)
            for t in turns: insert(history, 'thread_turns', t)
            for item in items: insert(history, 'thread_items', item)
            insert(history, 'thread_history_projection_state', projection)
            write(root / 'normalized' / (s['id'] + '.json'), dumps(s) + '\n')
            transcript = f'# {s["source"]}: {s["title"]}\n\nSource ID: {s["source_id"]}\nCodex ID: {s["id"]}\nOriginal cwd: {s["cwd"]}\n\n'
            for i, t in enumerate(s['turns'], 1):
                transcript += f'## Turn {i} — user\n\n{t["user"]}\n\n### Assistant / historical tool records\n\n{t["assistant"]}\n\n'
            write(root / 'transcripts' / (s['id'] + '.md'), transcript)
            index.append({k: v for k, v in s.items() if k != 'turns'} | {'turns': len(s['turns'])})
    manifest = dict(batch=BATCH, sources=c.files, sessions=index, memories=memories, errors=c.errors,
                    empty_sources=c.empty, native_threads=66)
    write(root / 'manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    write(root / 'INDEX.md', '# Imported conversations\n\nRaw foreign tool calls are historical text, not executable Codex calls. Original attachments, tool payloads, alternate responses and editor state remain in raw archives.\n\n' +
          '\n'.join(f'- [{s["source"]}: {s["title"].replace(chr(10), " ")}]' + f'(transcripts/{s["id"]}.md) — `{s["id"]}`, {s["turns"]} turns' for s in index) + '\n')
    print(dumps(dict(sessions=dict(collections.Counter(s['source'] for s in sessions)), turns=sum(len(s['turns']) for s in sessions), memories=len(memories), errors=c.errors)), flush=True)


def merge(source, target, native=False, provider=None, model=None):
    counts = {}
    with ro(source / 'state_5.sqlite') as src, sqlite3.connect(target / 'state_5.sqlite', timeout=60) as dst:
        before = dst.total_changes
        for row in rows(src, 'threads'):
            old = Path(row['rollout_path'])
            # Native backup paths point to the old machine's home.
            rel = Path('sessions') / Path(*old.parts[old.parts.index('sessions') + 1:])
            srcfile = source / rel; dstfile = target / rel
            if dstfile.exists():
                if digest(srcfile) != digest(dstfile):
                    raise ValueError(f'Existing rollout differs: {dstfile}; refusing overwrite')
            else:
                dstfile.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(srcfile, dstfile)
            row['rollout_path'] = str(dstfile)
            row['thread_section_id'] = None; row['project_id'] = None
            # Original provider stays in the immutable rollout and manifest.
            row['model_provider'] = provider; row['model'] = model
            insert(dst, 'threads', row)
            if native:
                dst.execute('UPDATE threads SET model_provider=?,model=? WHERE id=?', (provider, model, row['id']))
        counts['state_changes'] = dst.total_changes - before
        for table in ('thread_spawn_edges', 'thread_dynamic_tools'):
            if src.execute('SELECT 1 FROM sqlite_master WHERE name=?', (table,)).fetchone():
                for row in rows(src, table): insert(dst, table, row)
    with ro(source / 'thread_history_1.sqlite') as src, sqlite3.connect(target / 'thread_history_1.sqlite', timeout=60) as dst:
        for table in ('thread_turns', 'thread_items', 'thread_history_projection_state', 'thread_realtime_items'):
            before = dst.total_changes
            for row in rows(src, table): insert(dst, table, row)
            counts[table] = dst.total_changes - before
    return counts


def apply(args):
    target = args.target
    archive = target / 'imports' / BATCH
    if archive.exists():
        raise FileExistsError(f'{archive} already exists; inspect report before a repair/retry')
    archive.mkdir(mode=0o700, parents=True)
    for db in ('state_5.sqlite', 'thread_history_1.sqlite'):
        snapshot(target / db, archive / 'before' / db)
    for name in ('AGENTS.md', 'config.toml'):
        if (target / name).exists():
            shutil.copyfile(target / name, archive / 'before' / name)
    for name in ('raw', 'memories', 'normalized', 'transcripts'):
        shutil.copytree(args.work / name, archive / name)
    for name in ('INDEX.md', 'manifest.json'):
        shutil.copyfile(args.work / name, archive / name)
    # Immutable source DB snapshots retain original providers and parent metadata.
    for db in ('state_5.sqlite', 'thread_history_1.sqlite'):
        out = archive / 'native-source' / db; out.parent.mkdir(exist_ok=True)
        shutil.copyfile(args.work / 'native' / db, out)
    result = dict(native=merge(args.work / 'native', target, True, args.provider, args.model),
                  foreign=merge(args.work / 'staging', target, False, args.provider, args.model))
    write(archive / 'apply-report.json', json.dumps(result, indent=2) + '\n')
    print(dumps(result))


def main():
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    a = sub.add_parser('prepare')
    a.add_argument('--work', type=Path, required=True)
    a.add_argument('--backup', type=Path, required=True)
    a.add_argument('--code', type=Path, required=True)
    a.add_argument('--windows', type=Path, required=True)
    a.add_argument('--template', type=Path, required=True)
    a.set_defaults(func=prepare)
    a = sub.add_parser('apply')
    a.add_argument('--work', type=Path, required=True)
    a.add_argument('--target', type=Path, required=True)
    a.add_argument('--provider', required=True)
    a.add_argument('--model', required=True)
    a.set_defaults(func=apply)
    args = p.parse_args(); args.func(args)


if __name__ == '__main__':
    main()
