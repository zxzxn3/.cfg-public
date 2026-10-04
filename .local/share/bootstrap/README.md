# Bootstrap

Personal package selection and setup for CachyOS. Run as your normal user;
setup scripts request sudo where needed. cfg tracks this directory as part of
its `$HOME` work tree, at `~/.local/share/bootstrap/`.

## Deploy dotfiles

The repository entry point is `~/deploy.sh`, outside this directory:

```sh
bash ~/deploy.sh --help
bash ~/deploy.sh
```

It clones the remote cfg repository and checks out its files into `$HOME`.
Push changes you want to deploy first. It can replace the existing bare
repository; `--force` also permits overwriting local files without confirmation.
There is no deploy dry-run option. See the script header for initial download.

## Packages

Run from this directory with Python 3.11+ and pacman available:

```sh
./installer.py                   # package TUI; ? shows the keys
./installer.py --list            # package status and sources
./installer.py --list --variants # include alternative -bin / -git packages
python3 test_installer.py        # offline checks
```

Select rows with Space, install with `i`, remove with `r`, and rescan with `R`.
`/` filters, `f` shows missing packages, `v` shows alternatives, and `U` toggles
an upgrade before installation. Commands and sudo prompts run in the terminal.
AUR installation requires an available helper; use the TUI help for details.

| File | Purpose |
| --- | --- |
| `packages/packages.toml` | The installer's package selection, grouped by function |
| `packages/base.txt` | Human reference for the base CachyOS installation |
| `packages/base-installer.txt` | Human reference for packages from installer options |
| `packages/candidates.txt` | Interesting software to learn or use later |
| `packages/unwanted.txt` | Software disliked or not needed, to avoid reconsidering it |

The four reference lists do not trigger installation or removal. Their entries
are not a live inventory; query pacman for current installation and sources.

## Setup

Run `setup/*.sh` individually. The package TUI does not run them.
All support `DRY_RUN=1`; the bundled JuhRadial installer has its own guard.

```sh
DRY_RUN=1 ./setup/20-rime-ice.sh
./setup/20-rime-ice.sh
```

| Script | Purpose |
| --- | --- |
| `00-repositories.sh` | Add CachyOS/chaotic-aur repositories and run `pacman -Syu`; use before package installation when needed |
| `10-juhradial-mx.sh` | Bundled upstream JuhRadial MX installer with a local dry-run guard |
| `20-rime-ice.sh` | Install rime-ice through plum and request Rime deployment |
| `30-easyeffects-presets.sh` | Install EasyEffects presets without activating one |
| `50-wayvibes-soundpacks.sh` | Install soundpacks under `${WAYVIBES_HOME:-$HOME/wayvibes}/soundpacks` and add the user to `input` |
| `60-frieren-plymouth.sh` | Install the bundled Plymouth theme on CachyOS with Limine and mkinitcpio |

`kb` uses the same `WAYVIBES_HOME` override; `WAYVIBES_DEVICE` selects a keyboard
instead of its default K580. Export overrides for both the shell and setup scripts.

## Plymouth

Keep `assets/` and `lib/` with the setup scripts. On a Limine + mkinitcpio system:

```sh
DRY_RUN=1 ./setup/60-frieren-plymouth.sh
./setup/60-frieren-plymouth.sh
python3 -m unittest test_frieren_plymouth.py
```

The script checks prerequisites and asset hashes, installs Plymouth, backs up
modified files under `/var/backups/plymouth/frieren-bootstrap-*`, and rebuilds
boot images. Reboot after success. If rebuilding fails, use the printed backup
path and its `README.txt` for recovery.

Asset details live in [the theme README](assets/plymouth/frieren/README.md).
`assets/plymouth/frieren/validate_runtime.py` checks the theme against the local
Plymouth interpreter; its native library ABI must match.
