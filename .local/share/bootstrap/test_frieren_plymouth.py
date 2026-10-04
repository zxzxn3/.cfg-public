"""Offline checks for boot configuration changes; never writes /etc."""
import importlib.util
from pathlib import Path
import subprocess
import unittest

spec = importlib.util.spec_from_file_location('frieren', Path(__file__).parent / 'lib/frieren-plymouth.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class ConfigTests(unittest.TestCase):
    def test_existing_parameters_and_repeat(self):
        original = 'ESP_PATH=/boot\nKERNEL_CMDLINE[default]+="root=UUID=abc rw rootflags=subvol=/@"\nKERNEL_CMDLINE[linux-custom]=root=/dev/mapper/root rd.luks.name=abc=root\n'
        result = m.limine_config(original, [], '')
        self.assertTrue(result.startswith(original))
        self.assertIn('KERNEL_CMDLINE[linux-custom]+=quiet splash', result)
        self.assertEqual(result, m.limine_config(result, [], ''))

    def test_missing_default_preserves_fallback(self):
        fallback = 'root=UUID=new-machine rw rootflags=subvol=/@ rd.luks.name=abc=root'
        result = m.limine_config('', [], fallback)
        self.assertIn('KERNEL_CMDLINE[default]=' + fallback, result)
        self.assertEqual(result, m.limine_config(result, [], fallback))

    def test_dropin_default_not_replaced(self):
        result = m.limine_config('', ['KERNEL_CMDLINE[default]=root=UUID=dropin'], '')
        self.assertNotIn('KERNEL_CMDLINE[default]=', result)
        self.assertIn('KERNEL_CMDLINE[default]+=quiet splash', result)

    def test_invalid_fallback_refused(self):
        for fallback in ['', 'quiet splash', 'root=/dev/loop0 archisobasedir=arch', 'root=a\nBAD=value']:
            with self.assertRaises(ValueError):
                m.limine_config('', [], fallback)

    def test_hooks_and_repeat(self):
        for hooks, expected in [
            ('base systemd autodetect kms keyboard sd-vconsole sd-encrypt filesystems',
             'base systemd autodetect kms keyboard sd-vconsole plymouth sd-encrypt filesystems'),
            ('base udev autodetect kms keyboard keymap encrypt filesystems',
             'base udev autodetect kms keyboard keymap plymouth encrypt filesystems'),
            ('base systemd kms plymouth filesystems sd-btrfs-overlayfs',
             'base systemd kms plymouth filesystems sd-btrfs-overlayfs'),
            ('base systemd kms filesystems sd-btrfs-overlayfs',
             'base systemd kms plymouth filesystems sd-btrfs-overlayfs'),
        ]:
            script = 'set -eu\nHOOKS=(' + hooks + ')\n' + m.HOOK + m.HOOK + '\nprintf "%s" "${HOOKS[*]}"'
            actual = subprocess.check_output(['bash', '-c', script], text=True)
            self.assertEqual(actual, expected)


if __name__ == '__main__':
    unittest.main()
