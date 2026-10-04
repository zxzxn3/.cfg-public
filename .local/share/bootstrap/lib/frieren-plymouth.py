#!/usr/bin/env python3
"""Local installer for CachyOS + Limine. No third-party Python dependencies."""
from pathlib import Path
import datetime
import os
import re
import shutil
import subprocess
import sys

BASE = Path(__file__).resolve().parent.parent
ASSETS = BASE / 'assets/plymouth/frieren'
MARK_START = '# BEGIN bootstrap Frieren Plymouth'
MARK_END = '# END bootstrap Frieren Plymouth'
HOOK = '''# Managed by bootstrap/setup/60-frieren-plymouth.sh
# Insert before encryption/filesystems; preserve all other hooks and their order.
if [[ " ${HOOKS[*]} " != *" plymouth "* ]]; then
    _frieren_hooks=()
    _frieren_inserted=0
    for _frieren_hook in "${HOOKS[@]}"; do
        if [[ $_frieren_inserted == 0 && $_frieren_hook =~ ^(encrypt|sd-encrypt|filesystems)$ ]]; then
            _frieren_hooks+=(plymouth)
            _frieren_inserted=1
        fi
        _frieren_hooks+=("$_frieren_hook")
    done
    if [[ $_frieren_inserted == 0 ]]; then
        _frieren_hooks+=(plymouth)
    fi
    HOOKS=("${_frieren_hooks[@]}")
    unset _frieren_hooks _frieren_inserted _frieren_hook
fi
'''


def strip_block(text):
    return re.sub(r'(?m)^' + re.escape(MARK_START) + r'\n.*?^'
                  + re.escape(MARK_END) + r'\n?', '', text, flags=re.S)


def limine_config(original, other_configs, fallback):
    clean = strip_block(original)
    keys = set(re.findall(r'^\s*KERNEL_CMDLINE\[([^]\n]+)\]\s*\+?=',
                          '\n'.join([*other_configs, clean]), re.M))
    lines = [MARK_START]
    if 'default' not in keys:
        # A += with no existing default suppresses Limine's automatic fallback.
        # Seed it explicitly so root/cryptdevice/subvolume parameters survive.
        fallback = fallback.strip()
        if not fallback or '\n' in fallback or not re.search(r'\broot=\S+', fallback):
            raise ValueError('No configured default cmdline or usable root= fallback.')
        if re.search(r'\b(archisobasedir|archisolabel|rd.live.image|boot=live)\b', fallback):
            raise ValueError('Run from the installed system, not a live ISO.')
        lines.append('KERNEL_CMDLINE[default]=' + fallback)
        keys.add('default')
    for key in sorted(keys):
        lines.append(f'KERNEL_CMDLINE[{key}]+=quiet splash')
    lines.append(MARK_END)
    return clean.rstrip() + '\n\n' + '\n'.join(lines) + '\n'


def read(path):
    return path.read_text() if path.exists() else ''


def preflight():
    if not re.search(r'^ID=["\']?cachyos["\']?$', read(Path('/etc/os-release')), re.M):
        raise ValueError('This installer supports CachyOS with Limine and mkinitcpio.')
    for path in ['/usr/bin/limine-mkinitcpio', '/usr/bin/mkinitcpio', '/etc/mkinitcpio.conf']:
        if not Path(path).exists():
            raise ValueError(f'Required Limine/mkinitcpio component missing: {path}')
    if re.search(r'\b(archisobasedir|archisolabel|rd.live.image)\b', read(Path('/proc/cmdline'))):
        raise ValueError('Run from the installed system, not a live ISO.')
    configs = [Path('/etc/limine-entry-tool.conf'),
               *sorted(Path('/etc/limine-entry-tool.d').glob('*.conf'))]
    target = Path('/etc/default/limine')
    fallback = read(Path('/etc/kernel/cmdline')).strip() or read(Path('/proc/cmdline'))
    return limine_config(read(target), [read(p) for p in configs], fallback)


def install():
    if os.geteuid() != 0:
        raise ValueError('Use setup/60-frieren-plymouth.sh as your normal user.')
    config = preflight()
    subprocess.run(['sha256sum', '--quiet', '-c', 'SHA256SUMS'], cwd=ASSETS, check=True)
    backup = Path('/var/backups/plymouth') / ('frieren-bootstrap-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    backup.mkdir(parents=True, mode=0o700)
    paths = ['/etc/default/limine', '/etc/mkinitcpio.conf.d/99-frieren-plymouth.conf',
             '/etc/plymouth/plymouthd.conf', '/usr/share/plymouth/themes/frieren']
    for name in paths:
        path = Path(name)
        dest = backup / name.lstrip('/')
        if path.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            if path.is_dir():
                shutil.copytree(path, dest, symlinks=True)
            else:
                shutil.copy2(path, dest)
    (backup / 'README.txt').write_text('Previous files retain their absolute path structure here.\n'
        'After restoring them, run /usr/bin/limine-mkinitcpio to rebuild boot images.\n'
        'Paths absent before installation:\n' + '\n'.join(p for p in paths if not Path(p).exists()) + '\n')
    print(f'Backup: {backup}', flush=True)
    target = Path('/usr/share/plymouth/themes/frieren')
    target.mkdir(parents=True, exist_ok=True)
    for source in (ASSETS / 'theme').iterdir():
        shutil.copyfile(source, target / source.name)
        (target / source.name).chmod(0o644)
    for name, content in [('/etc/default/limine', config),
                          ('/etc/mkinitcpio.conf.d/99-frieren-plymouth.conf', HOOK)]:
        path = Path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        if read(path) != content:
            path.write_text(content)
            path.chmod(0o644)
    subprocess.run(['plymouth-set-default-theme', 'frieren'], check=True)
    # Use CachyOS's real Limine rebuild, not a possibly wrapped mkinitcpio -P.
    subprocess.run(['/usr/bin/limine-mkinitcpio'], check=True)
    selected = subprocess.check_output(['plymouth-set-default-theme'], text=True).strip()
    if selected != 'frieren':
        raise ValueError(f'Unexpected selected theme: {selected}')
    print('Frieren selected; Limine boot images rebuilt. No reboot requested.')


if __name__ == '__main__':
    try:
        if sys.argv[1:] == ['check']:
            preflight()
            print('CachyOS/Limine preflight passed.')
        elif sys.argv[1:] == ['install']:
            install()
        else:
            raise ValueError('Expected check or install.')
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        sys.exit(f'Frieren installation stopped: {exc}')
