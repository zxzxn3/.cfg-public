import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from migrate_history import Collector, insert, mutations


class HistoryMigrationTests(unittest.TestCase):
    def test_vscode_patch_truncation_delete_and_snapshot(self):
        entries = [
            {'kind': 0, 'v': {'requests': [1, 2, 3], 'old': True}},
            {'kind': 2, 'k': ['requests'], 'i': 1, 'v': [4, 5]},
            {'kind': 1, 'k': ['requests', 1], 'v': 6},
            {'kind': 3, 'k': ['old']},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'chat.jsonl'
            p.write_text('\n'.join(map(json.dumps, entries)))
            self.assertEqual(mutations(p), {'requests': [1, 6, 5]})
            with p.open('a') as f:
                f.write('\n' + json.dumps({'kind': 0, 'v': {'requests': []}}))
            self.assertEqual(mutations(p), {'requests': []})

    def test_duplicate_variants_preserve_repeated_user_turns(self):
        c = Collector(Path('/unused'))
        def turns(texts):
            return [dict(user=t, assistant='answer', time=1000 + i) for i, t in enumerate(texts)]
        c.add('copilot', 'one', 'title', 1000, 2000, turns(['a', 'a']), 'ui', priority=3)
        c.add('copilot', 'one', 'title', 1000, 2000, turns(['a', 'b']), 'db', priority=1)
        chosen = c.choose()
        self.assertEqual(len(chosen), 1)
        self.assertEqual(sorted(t['user'] for t in chosen[0]['turns']), ['a', 'a', 'b'])

    def test_insert_is_idempotent_and_preserves_existing_rows(self):
        c = sqlite3.connect(':memory:')
        c.execute('CREATE TABLE records(id TEXT PRIMARY KEY,value TEXT,new_column TEXT DEFAULT "default")')
        insert(c, 'records', {'id': 'a', 'value': 'live', 'old_column': 'ignore'})
        insert(c, 'records', {'id': 'a', 'value': 'backup'})
        self.assertEqual(c.execute('SELECT * FROM records').fetchall(), [('a', 'live', 'default')])


if __name__ == '__main__':
    unittest.main()
