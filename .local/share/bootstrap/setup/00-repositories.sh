#!/usr/bin/env bash
# Add the CachyOS and chaotic-aur repositories: keys, mirrorlists, and the
# /etc/pacman.conf sections that point at them.
#
# Run manually before installer.py if the required package repositories are
# missing. installer.py does not run setup scripts; AUR packages use an AUR helper.
#
# The keyring and mirrorlist packages only drop files into /etc/pacman.d/, so
# the sections have to be added to /etc/pacman.conf by hand - that is the part
# that is easy to forget and the reason this script exists. They are inserted
# above the first [core], because pacman takes a package from the first
# repository that has it: the CachyOS rebuilds must win over the official
# packages, which is also how a fresh CachyOS install is ordered.
set -Eeuo pipefail
# shellcheck source=../lib/common.sh
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)/lib/common.sh"

[[ $(id -u) -ne 0 ]] || die 'Run as your normal user, not root.'
command -v sudo >/dev/null 2>&1 || die 'sudo is required.'

PACMAN_CONF="${PACMAN_CONF:-/etc/pacman.conf}"
CHAOTIC_KEY=3056513887B78AEB
CACHYOS_KEY=F3B607488DB35A47

# repo:mirrorlist, in the order the sections must appear in pacman.conf.
# x86-64-v3 is what a generic CachyOS install uses; on an AVX-512 machine the
# three -v3 entries are replaced by -v4 ones (cachyos-v4-mirrorlist), exactly
# as the ISO decides it.
REPOS=(
	chaotic-aur:chaotic-mirrorlist
	cachyos-v3:cachyos-v3-mirrorlist
	cachyos-extra-v3:cachyos-v3-mirrorlist
	cachyos-core-v3:cachyos-v3-mirrorlist
	cachyos:cachyos-mirrorlist
)

# Refresh only after all repository sections are active. Upgrade together with
# the refresh so subsequent installs do not create a partial upgrade.
refresh_repositories() {
	log 'Refreshing repository databases and upgrading the system.'
	run sudo pacman --config "$PACMAN_CONF" -Syu
}

log 'Adding the CachyOS and chaotic-aur repositories.'
run sudo pacman-key --recv-key "$CHAOTIC_KEY" --keyserver keyserver.ubuntu.com
run sudo pacman-key --lsign-key "$CHAOTIC_KEY"
run sudo pacman -U 'https://cdn-mirror.chaotic.cx/chaotic-aur/chaotic-keyring.pkg.tar.zst'
run sudo pacman -U 'https://cdn-mirror.chaotic.cx/chaotic-aur/chaotic-mirrorlist.pkg.tar.zst'
run sudo pacman-key --recv-keys "$CACHYOS_KEY" --keyserver keyserver.ubuntu.com
run sudo pacman-key --lsign-key "$CACHYOS_KEY"
run sudo pacman -U \
	'https://mirror.cachyos.org/repo/x86_64/cachyos/cachyos-keyring-20240331-1-any.pkg.tar.zst' \
	'https://mirror.cachyos.org/repo/x86_64/cachyos/cachyos-mirrorlist-27-1-any.pkg.tar.zst' \
	'https://mirror.cachyos.org/repo/x86_64/cachyos/cachyos-v3-mirrorlist-27-1-any.pkg.tar.zst' \
	'https://mirror.cachyos.org/repo/x86_64/cachyos/cachyos-v4-mirrorlist-27-1-any.pkg.tar.zst' \
	'https://mirror.cachyos.org/repo/x86_64/cachyos/pacman-7.1.0.r9.g54d9411-4-x86_64.pkg.tar.zst'

# --- /etc/pacman.conf -----------------------------------------------------

[[ -r $PACMAN_CONF ]] || die "Cannot read $PACMAN_CONF"

# section_present NAME -> true when [NAME] is an active section of the file.
# A commented '#[NAME]' does not count; trailing whitespace does.
section_present() {
	grep -qE -e "^\[$1\]([[:space:]]|$)" -- "$PACMAN_CONF"
}

missing=()
for entry in "${REPOS[@]}"; do
	repo=${entry%%:*}
	mirrorlist=${entry#*:}
	if section_present "$repo"; then
		log "Already in $PACMAN_CONF: [$repo]"
		continue
	fi
	if [[ $DRY_RUN != 1 && ! -r /etc/pacman.d/$mirrorlist ]]; then
		die "Missing /etc/pacman.d/$mirrorlist; refusing to activate [$repo]."
	fi
	missing+=("$entry")
done

if (( ${#missing[@]} == 0 )); then
	log "$PACMAN_CONF already has every section."
	refresh_repositories
	exit 0
fi

block=''
for entry in "${missing[@]}"; do
	repo=${entry%%:*}
	mirrorlist=${entry#*:}
	printf -v block '%s\n# Added by linux-cfg bootstrap: setup/00-repositories.sh\n[%s]\nInclude = /etc/pacman.d/%s\n' \
		"$block" "$repo" "$mirrorlist"
done

if [[ $DRY_RUN == 1 ]]; then
	log "[dry-run] Would insert this into $PACMAN_CONF, above the first [core]:"
	printf '%s' "$block"
	refresh_repositories
	exit 0
fi

tmp=$(mktemp)
trap 'rm -f -- "$tmp"' EXIT
# Insert before the first [core] so the CachyOS rebuilds keep priority; on a
# file without [core] the block goes to the end instead of being dropped.
awk -v block="$block" '
	BEGIN { inserted = 0 }
	!inserted && /^\[core\][[:space:]]*$/ {
		printf "%s\n", block
		inserted = 1
	}
	{ print }
	END {
		if (!inserted)
			printf "%s\n", block
	}
' "$PACMAN_CONF" > "$tmp"
run sudo install -m 0644 -- "$tmp" "$PACMAN_CONF"

names=("${missing[@]%%:*}")
log "Sections added: ${names[*]}"
refresh_repositories
