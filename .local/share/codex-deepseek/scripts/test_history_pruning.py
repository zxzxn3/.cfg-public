import sqlite3
import tempfile
from pathlib import Path
import unittest

from prune_history import delete_rows, filter_jsonl, pack, sha, verify_tar


class HistoryPruningTests(unittest.TestCase):
    def test_delete_only_selected_and_dangling_edges(self):
        c = sqlite3.connect(':memory:')
        c.execute("ATTACH DATABASE ':memory:' AS history")
        c.execute('CREATE TABLE threads(id TEXT PRIMARY KEY, title TEXT)')
        c.execute('CREATE TABLE thread_spawn_edges(parent_thread_id TEXT, child_thread_id TEXT)')
        c.execute('CREATE TABLE history.thread_items(thread_id TEXT, text TEXT)')
        c.executemany('INSERT INTO threads VALUES (?,?)', [('old','old'),('live','new')])
        c.executemany('INSERT INTO history.thread_items VALUES (?,?)', [('old','old'),('live','live message')])
        c.execute("INSERT INTO thread_spawn_edges VALUES ('live','old')")
        delete_rows(c, ['old'])
        self.assertEqual(c.execute('SELECT * FROM threads').fetchall(), [('live','new')])
        self.assertEqual(c.execute('SELECT * FROM history.thread_items').fetchall(), [('live','live message')])
        self.assertEqual(c.execute('SELECT * FROM thread_spawn_edges').fetchall(), [])

    def test_deduplicated_archive_is_verified_and_detects_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); a=root/'a'; b=root/'b'; archive=root/'data.tar.gz'
            a.write_text('same private history'); b.write_text(a.read_text())
            manifest = pack(archive, [('one/a',a),('two/b',b)])
            self.assertEqual(verify_tar(archive,manifest),2)
            manifest['two/b']['sha256']='wrong'
            with self.assertRaises(ValueError): verify_tar(archive,manifest)

    def test_input_history_preserves_live_entries_and_rejects_changed_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'history.jsonl'
            p.write_text('{"session_id":"old","text":"hello"}\n{"session_id":"live","text":"working"}\n')
            self.assertEqual(filter_jsonl(p,{'old'},sha(p)),1)
            self.assertIn('working',p.read_text()); self.assertNotIn('hello',p.read_text())
            with self.assertRaises(RuntimeError): filter_jsonl(p,{'live'},'stale digest')


if __name__ == '__main__':
    unittest.main()
