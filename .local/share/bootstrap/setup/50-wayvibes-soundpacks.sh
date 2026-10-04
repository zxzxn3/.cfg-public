#!/usr/bin/env bash
# Install the Wayvibes soundpack collection (mechanical keyboard sounds).
#
# The AUR package (wayvibes-git) ships the binary and nothing else, so the
# sounds come from the upstream repository and land in the directory kb.fish
# reads: ~/wayvibes/soundpacks/.
#
# Upstream keeps two Mechvibes generations side by side in the same directory:
#
#   V1 (classic)     `defines` maps each key to its own wav file.
#   V2 (MechvibesDX) `definitions` slices one audio file, and keys also sound
#                    on release. This is the generation this machine runs.
#
# The format is read from each pack's own config.json rather than from a list
# of names, so an upstream rename cannot silently install the wrong thing: V1
# packs are reported and skipped, and a run that finds no V2 pack at all fails
# instead of reporting success. Mouse packs (V2, one directory deeper) come
# along; they want the mouse event node rather than the keyboard, see wayvibes
# --help.
set -Eeuo pipefail
# shellcheck source=../lib/common.sh
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)/lib/common.sh"

[[ $(id -u) -ne 0 ]] || die 'Run as your normal user, not root.'

REPO_URL="https://github.com/sahaj-b/wayvibes.git"
CACHE_REPO="$CACHE_DIR/wayvibes"
UPSTREAM_DIR="$CACHE_REPO/soundpacks"
TARGET_DIR="${WAYVIBES_HOME:-$HOME/wayvibes}/soundpacks"

log "Wayvibes soundpack directory: $TARGET_DIR"
if [[ $DRY_RUN == 1 ]]; then
	log "[dry-run] Would clone or update $REPO_URL and copy every MechvibesDX (V2) soundpack into $TARGET_DIR"
	log "[dry-run] V1 packs would be skipped; no pack would be selected or started."
	exit 0
fi

# wayvibes reads /dev/input/event*, which is root:input mode 0660, so the user
# has to be in the input group or it cannot see a single keypress.
if id -nG | tr ' ' '\n' | grep -qx input; then
	log "User is already in the 'input' group."
else
	log "Adding $USER to the 'input' group; wayvibes cannot read key events without it."
	run sudo usermod -a -G input "$USER"
	warn 'Log out and back in for the new group to take effect.'
fi

clone_or_update "$REPO_URL" "$CACHE_REPO"
[[ -d $UPSTREAM_DIR ]] || die "Upstream soundpacks are missing: $UPSTREAM_DIR. Has upstream moved them?"

installed=0
skipped=0

# One config.json per pack; mouse packs sit one level deeper, under mouse/<name>/.
while IFS= read -r -d '' config; do
	pack_dir=${config%/*}
	relative=${pack_dir#"$UPSTREAM_DIR"/}
	[[ $relative == "$pack_dir" ]] && die "Cannot make a pack path relative to $UPSTREAM_DIR: $pack_dir"

	if ! grep -q '"definitions"' "$config"; then
		log "Skipping V1 pack: $relative"
		skipped=$((skipped + 1))
		continue
	fi

	while IFS= read -r -d '' file; do
		deploy_file "$file" "$TARGET_DIR/$relative/${file#"$pack_dir"/}" 644
	done < <(find "$pack_dir" -type f -print0)
	installed=$((installed + 1))
done < <(find "$UPSTREAM_DIR" -name config.json -type f -print0)

(( installed > 0 )) || die "No MechvibesDX (V2) soundpack found under $UPSTREAM_DIR; refusing to report success. Check the upstream layout."

if (( skipped > 0 )); then
	log "Skipped $skipped V1 pack(s); this machine runs the V2 set (see the header)."
fi

log "Installed $installed soundpack(s) into $TARGET_DIR."
log 'Nothing was selected or started; pick one with kb (kb -h lists them).'
