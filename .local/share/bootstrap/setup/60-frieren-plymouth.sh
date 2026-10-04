#!/usr/bin/env bash
# Install the bundled Frieren animation on an installed CachyOS + Limine system.
set -Eeuo pipefail
# shellcheck source=../lib/common.sh
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)/lib/common.sh"
[[ $(id -u) -ne 0 ]] || die 'Run as your normal user, not root.'
command -v python3 >/dev/null || die 'python3 is required.'
# Preflight is read-only and runs before package/config changes, including dry runs.
python3 "$BOOTSTRAP_BASE/lib/frieren-plymouth.py" check
(cd "$BOOTSTRAP_BASE/assets/plymouth/frieren" && sha256sum --quiet -c SHA256SUMS)
run sudo pacman -S --needed plymouth
run sudo python3 "$BOOTSTRAP_BASE/lib/frieren-plymouth.py" install
if [[ $DRY_RUN == 1 ]]; then
    log 'Would back up configs, install Frieren, enable the Plymouth hook and quiet splash, then rebuild Limine boot images.'
else
    log 'Frieren installed. Reboot when ready.'
fi
