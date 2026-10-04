"""Keyring tests without accessing real credentials."""
from pathlib import Path
import contextlib
import io
import subprocess
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import launcher

class CredentialTest(unittest.TestCase):
    def test_store_and_verify(self):
        results = [subprocess.CompletedProcess([], 0, '', ''),
                   subprocess.CompletedProcess([], 0, 'test-only\n', '')]
        with patch.object(launcher.getpass, 'getpass', return_value='test-only'), patch.object(launcher.subprocess, 'run', side_effect=results) as run, contextlib.redirect_stdout(io.StringIO()):
            launcher.save_key()
        self.assertEqual(run.call_args_list[0].kwargs['input'], 'test-only')
        self.assertNotIn('test-only', run.call_args_list[0].args[0])
        self.assertEqual(run.call_args_list[1].args[0], ['secret-tool', 'lookup', *launcher.KEY_ATTRIBUTES])

    def test_missing_or_invalid_secret(self):
        for code, value in [(1, 'sensitive-output'), (0, ''), (0, 'bad key')]:
            with patch.object(launcher.subprocess, 'run', return_value=subprocess.CompletedProcess([], code, value, '')):
                with self.assertRaisesRegex(ValueError, 'Unlock your keyring'):
                    launcher.check_key()

if __name__ == '__main__':
    unittest.main()
