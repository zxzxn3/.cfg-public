#!/usr/bin/env python3
"""Apply an explicitly reviewed retirement manifest to matching Codex homes.

No model requests. Verify a deduplicated cold backup before deleting anything.
Never select conversations by age/length automatically. Live/new IDs are excluded
by requiring every decision to belong to the previously imported batch.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tarfile

BATCH = 'history-2026-09-29'


def sha(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def read_db(path):
    c = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    c.row_factory = sqlite3.Row
    return c


def snapshot(src, dst):
    dst.parent.mkdir(parents=True, exist_ok=True)
    a = read_db(src); b = sqlite3.connect(dst)
    try:
        a.backup(b)
    finally:
        a.close(); b.close()


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def tables_with_threads(c, schema):
    out = []
    for (name,) in c.execute(f"SELECT name FROM {schema}.sqlite_master WHERE type='table'"):
        cols = [r[1] for r in c.execute(f'PRAGMA {schema}.table_info("{name}")')]
        refs = [k for k in cols if k in ('thread_id', 'parent_thread_id', 'child_thread_id')]
        if refs:
            out.append((name, refs))
    return out


def delete_rows(c, ids):
    """Delete only explicit IDs and their referencing metadata; caller commits."""
    marks = ','.join('?' for _ in ids)
    counts = {}
    for schema in ('history', 'main'):
        for table, refs in tables_with_threads(c, schema):
            before = c.total_changes
            where = ' OR '.join(f'"{col}" IN ({marks})' for col in refs)
            c.execute(f'DELETE FROM {schema}."{table}" WHERE {where}', list(ids) * len(refs))
            counts[schema + '.' + table] = c.total_changes - before
    c.execute(f'DELETE FROM main.threads WHERE id IN ({marks})', list(ids))
    return counts


def active_ids():
    """Inspect only ID fields and open rollout names, never print environment."""
    found = set()
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if proc.stat().st_uid != os.getuid():
                continue
            for entry in (proc / 'environ').read_bytes().split(b'\0'):
                key, _, value = entry.partition(b'=')
                if key in (b'CODEX_THREAD_ID', b'CODEX_SESSION_ID'):
                    found.add(value.decode(errors='replace'))
            for fd in (proc / 'fd').iterdir():
                name = os.readlink(fd)
                if '/sessions/' in name and name.endswith('.jsonl'):
                    found.add(Path(name).stem[-36:])
        except (OSError, PermissionError):
            continue
    return found


def verify_tar(path, expected):
    verified = {}
    with tarfile.open(path, 'r|gz') as archive:
        for member in archive:
            if member.islnk():
                value = verified[member.linkname]
            else:
                f = archive.extractfile(member)
                if f is None:
                    raise ValueError(f'Unexpected archive member: {member.name}')
                value = hashlib.file_digest(f, 'sha256').hexdigest()
            if value != expected[member.name]['sha256']:
                raise ValueError(f'Archive hash mismatch: {member.name}')
            verified[member.name] = value
    if set(verified) != set(expected):
        raise ValueError('Archive member coverage mismatch')
    return len(verified)


def pack(path, files):
    by_hash = {}; manifest = {}
    with tarfile.open(path, 'w:gz', compresslevel=6) as archive:
        for name, source in files:
            digest = sha(source)
            manifest[name] = {'sha256': digest, 'bytes': source.stat().st_size}
            info = archive.gettarinfo(str(source), arcname=name)
            info.mode = 0o600
            if digest in by_hash:
                info.type = tarfile.LNKTYPE; info.linkname = by_hash[digest]; info.size = 0
                archive.addfile(info)
            else:
                with source.open('rb') as f:
                    archive.addfile(info, f)
                by_hash[digest] = name
    return manifest


def row_fingerprint(c, table, ids):
    marks = ','.join('?' for _ in ids)
    key = 'id' if table == 'threads' else 'thread_id'
    data = [tuple(r) for r in c.execute(f'SELECT * FROM "{table}" WHERE "{key}" IN ({marks})', list(ids))]
    return sorted(data, key=repr)


def check_unchanged(home, before, ids):
    for db, tables in [('state_5.sqlite', ['threads']),
                       ('thread_history_1.sqlite', ['thread_turns', 'thread_items', 'thread_history_projection_state'])]:
        a = read_db(home / db); b = read_db(before / db)
        try:
            for table in tables:
                if row_fingerprint(a, table, ids) != row_fingerprint(b, table, ids):
                    raise RuntimeError(f'Retiring thread changed during review: {home}/{table}')
        finally:
            a.close(); b.close()


def filter_jsonl(path, ids, expected_hash):
    if not path.exists():
        return 0
    # Do not overwrite a live input-history append. Caller can retry after review.
    if sha(path) != expected_hash:
        raise RuntimeError(f'Live index changed; refusing replacement: {path}')
    keep = []; removed = 0
    for line in path.read_text().splitlines(keepends=True):
        row = json.loads(line)
        if any(row.get(k) in ids for k in ('id', 'thread_id', 'session_id')):
            removed += 1
        else:
            keep.append(line)
    if removed:
        temp = path.with_name(path.name + '.prune-tmp')
        temp.write_text(''.join(keep))
        if sha(path) != expected_hash:
            temp.unlink()
            raise RuntimeError(f'Live index changed before replace: {path}')
        temp.replace(path)
    return removed


def main():
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--plan', type=Path, required=True)
    p.add_argument('--content', type=Path, required=True)
    p.add_argument('--home', type=Path, action='append', required=True)
    p.add_argument('--archive-root', type=Path, required=True)
    args = p.parse_args()
    plan = json.loads(args.plan.read_text())
    selected = {d['id'] for d in plan['decisions'] if d['action'] == 'retire'}
    kept = {d['id'] for d in plan['decisions'] if d['action'] == 'keep'}
    if not selected or selected & kept or selected & active_ids():
        raise ValueError('Invalid or active selection')
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    vault = args.archive_root / ('memory-prune-' + stamp)
    vault.mkdir(mode=0o700, parents=True, exist_ok=False)
    work = vault / '.work'; work.mkdir()
    files = [('decisions.json', args.plan)]
    homes = []; imported_files = {}; selected_files = {}; original_counts = {}
    for home in args.home:
        name = 'openai' if home == Path.home() / '.codex' else 'deepseek'
        if name in original_counts:
            raise ValueError('Duplicate home label')
        root = home / 'imports' / BATCH
        manifest = json.loads((root / 'manifest.json').read_text())
        foreign = {s['id'] for s in manifest['sessions']}
        src = read_db(root / 'native-source/state_5.sqlite')
        imported = foreign | {r[0] for r in src.execute('SELECT id FROM threads')}; src.close()
        if selected | kept != imported:
            raise ValueError('Manifest does not exactly cover the imported set')
        before = work / name
        for db in ('state_5.sqlite', 'thread_history_1.sqlite'):
            snapshot(home / db, before / db)
        for file in ('AGENTS.md', 'history.jsonl', 'session_index.jsonl'):
            if (home / file).exists():
                shutil.copyfile(home / file, before / file)
        for file in sorted(before.iterdir()):
            files.append((f'homes/{name}/before/{file.name}', file))
        for file in sorted(root.rglob('*')):
            if file.is_file():
                entry = f'homes/{name}/imports/{BATCH}/{file.relative_to(root)}'
                files.append((entry, file)); imported_files[str(file)] = entry
        c = read_db(before / 'state_5.sqlite')
        present = {r[0] for r in c.execute('SELECT id FROM threads')}
        if not imported <= present:
            raise ValueError('An imported conversation is already missing')
        original_counts[name] = len(present)
        rollouts = {r['id']: Path(r['rollout_path']) for r in c.execute('SELECT id,rollout_path FROM threads') if r['id'] in selected}
        c.close()
        for sid, file in rollouts.items():
            if not file.is_relative_to(home / 'sessions'):
                raise ValueError('Selected rollout is outside the session directory')
            entry = f'homes/{name}/{file.relative_to(home)}'
            files.append((entry, file)); selected_files[str(file)] = entry
        homes.append(dict(home=home, name=name, before=before, root=root, foreign=foreign,
                          rollouts=rollouts, protected=present-selected, config_sha256=sha(home/'config.toml')))
    archive = vault / 'history.tar.gz'
    print('Creating deduplicated cold archive', flush=True)
    archived = pack(archive, files)
    print('Verifying every cold archive member', flush=True)
    verify_tar(archive, archived)
    write(vault / 'members.json', json.dumps(archived, indent=2) + '\n')
    write(vault / 'decisions.json', json.dumps(plan, ensure_ascii=False, indent=2) + '\n')
    write(vault / 'SHA256SUMS', sha(archive) + '  history.tar.gz\n')
    print(f'Archive verified: {len(archived)} paths; {archive.stat().st_size} bytes', flush=True)
    # Recheck both homes before deleting anything from either one.
    if selected & active_ids():
        raise RuntimeError('A selected conversation is now active')
    for h in homes:
        check_unchanged(h['home'], h['before'], selected)
        for file in h['rollouts'].values():
            if sha(file) != archived[selected_files[str(file)]]['sha256']:
                raise RuntimeError('Selected rollout changed while making archive')
        for file in ('history.jsonl', 'session_index.jsonl'):
            if (h['before']/file).exists() and sha(h['home']/file) != sha(h['before']/file):
                raise RuntimeError('A live index changed while making archive')
    results = {}
    for h in homes:
        home, name, root = h['home'], h['name'], h['root']
        # Attach both stores so the changes share a transaction boundary.
        c = sqlite3.connect(home / 'state_5.sqlite', timeout=60)
        c.execute('PRAGMA foreign_keys=ON')
        c.execute('ATTACH DATABASE ? AS history', (str(home / 'thread_history_1.sqlite'),))
        try:
            c.execute('BEGIN IMMEDIATE')
            result = delete_rows(c, sorted(selected))
            c.commit()
        except BaseException:
            c.rollback(); raise
        finally:
            c.close()
        result['removed_rollouts'] = 0
        for file in h['rollouts'].values():
            file.unlink(); result['removed_rollouts'] += 1
        for file in ('history.jsonl', 'session_index.jsonl'):
            if (h['before']/file).exists():
                result[file] = filter_jsonl(home/file, selected, sha(h['before']/file))
        # Keep full readable transcripts only for retained foreign conversations.
        for file in sorted(root.rglob('*')):
            if not file.is_file():
                continue
            if file.parent.name == 'transcripts' and file.stem in kept:
                continue
            if sha(file) != archived[imported_files[str(file)]]['sha256']:
                raise RuntimeError('Source export changed; refusing removal')
            file.unlink()
        for directory in sorted((p for p in root.rglob('*') if p.is_dir()), key=lambda p:len(p.parts), reverse=True):
            if not any(directory.iterdir()):
                directory.rmdir()
        live = read_db(home/'state_5.sqlite')
        current = {r[0] for r in live.execute('SELECT id FROM threads')}
        assert not selected & current and h['protected'] <= current
        assert live.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        history = read_db(home/'thread_history_1.sqlite')
        assert history.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        marks = ','.join('?' for _ in selected)
        for table in ('thread_turns','thread_items','thread_history_projection_state'):
            assert history.execute(f'SELECT count(*) FROM {table} WHERE thread_id IN ({marks})',list(selected)).fetchone()[0] == 0
        index = ['# 保留的历史会话', '', f'保留 {len(kept)} 个导入会话；已退役 {len(selected)} 个。新会话不在本次整理范围内。', '',
                 '使用 `codex resume --all` 或 `codex-deepseek resume --all`，也可指定下面的 ID。', '',
                 '短记事与主题记忆见当前 CODEX_HOME/memories/INDEX.md；原始迁移资料仅在冷存档，不默认检索。', '']
        for r in live.execute('SELECT id,title,rollout_path FROM threads ORDER BY created_at'):
            if r['id'] not in kept:
                continue
            title = r['title'].replace('\n',' ') or '商业/MCP 调研子会话（保留实质成果）'
            path = f'transcripts/{r["id"]}.md' if r['id'] in h['foreign'] else r['rollout_path']
            index.append(f'- [{title[:140]}]({path}) — `{r["id"]}`')
        write(root/'INDEX.md', '\n'.join(index)+'\n')
        write(root/'README.md', f'原始迁移档案已去重冷存档：`{archive}`。\n日常仅保留 INDEX.md 和仍有价值的会话正文；详见 `{home}/memories/CLEANUP.md`。\n')
        for file in args.content.glob('*.md'):
            if file.name == 'AGENTS.template.md':
                write(home/'AGENTS.md', file.read_text().replace('@HOME@', str(home)))
            else:
                write(home/'memories'/file.name, file.read_text())
        write(home/'memories/maintenance/decisions.json', json.dumps(plan, ensure_ascii=False, indent=2)+'\n')
        result.update(before_threads=original_counts[name], after_threads=len(current),
                      retained_imported=len(kept), protected_live=len(h['protected']-kept),
                      config_unchanged=sha(home/'config.toml')==h['config_sha256'])
        assert result['config_unchanged']
        results[name] = result
        live.close(); history.close()
        write(home/'memories/maintenance/result.json', json.dumps(result,indent=2)+'\n')
        write(home/'memories/CLEANUP.md', f'''# 记忆精简记录

整理时间：{stamp}。范围：上一轮导入的 180 个会话，不含当前/新增会话。

- 两边各删除 {len(selected)} 个已审阅的旧会话及其日志、分页数据、旧输入索引；保留 {len(kept)} 个。
- 92 个退役会话合为 52 条短记事；测试/问候合并，不逐次记忆。
- 28 份旧记忆改为 SYSTEM / RESEARCH / PROJECTS 三份主题笔记，日常偏好收进短 AGENTS.md；HISTORY.md 仅供按需检索。
- 去掉两个运行目录内的原始导出、旧记忆副本、冗余 normalized 数据和上次迁移备份，避免再次被误当现状。
- 新/当前会话、认证与 config.toml 未变；剩余历史数据库完整性检查通过。

判定与逐 ID 摘要：maintenance/decisions.json。变动统计：maintenance/result.json。
短会话中的研究证明、固件诊断和未完成交接仍保留；较长会话这轮未做激进删减。

## 冷存档

仅一份去重归档：`{archive}`，校验与路径清单在同目录。
其中保留两边整理前的 SQLite 一致性快照、AGENTS/输入索引、被删除日志和完整旧导出；重复文件使用归档内 hardlink。
需要溯源时在独立目录选择性解压。不要把整库快照覆盖当前仍产生新消息的 Codex，也不要批量重新导入退役内容。
此归档不进入 Git，不默认加载。原来的 /mnt/f 备份和 Windows 数据源未动。

此次修改还包括项目 .gitignore 与 prune_history.py；没有 commit/push。
''')
        print(name, json.dumps(result), flush=True)
    write(vault/'result.json', json.dumps(results,indent=2)+'\n')
    # The verified archive now owns these snapshots; no second warm backup remains.
    shutil.rmtree(work)
    print('Done. Cold archive:', archive, flush=True)


if __name__ == '__main__':
    main()
