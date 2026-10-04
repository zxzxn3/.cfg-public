#!/usr/bin/env bash
# Install the JackHack96 EasyEffects preset collection.
#
# Presets are copied file by file into the native EasyEffects config directory.
# Nothing is selected, enabled or autostarted. We intentionally do not probe for
# an old Flatpak data directory: mixing targets would deploy presets where the
# package installed by this installer does not read them.
#
# JSON files that reference the collection directory through
# <PRESETS_DIRECTORY> are rendered with lib/render_json.py (python3 stdlib) so
# the result is always valid JSON, even for paths with spaces, quotes or '&'.
set -Eeuo pipefail
# shellcheck source=../lib/common.sh
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)/lib/common.sh"

[[ $(id -u) -ne 0 ]] || die 'Run as your normal user, not root.'

REPO_URL="https://github.com/JackHack96/EasyEffects-Presets.git"
CACHE_REPO="$CACHE_DIR/EasyEffects-Presets"
PLACEHOLDER='<PRESETS_DIRECTORY>'
RENDER_JSON="$BOOTSTRAP_BASE/lib/render_json.py"
PRESETS_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/easyeffects"
OUT_DIR="$PRESETS_DIR/output"
IRS_DIR="$PRESETS_DIR/irs"

log "EasyEffects config directory: $PRESETS_DIR"
if [[ $DRY_RUN == 1 ]]; then
	log "[dry-run] Would clone or update $REPO_URL and copy its presets into $OUT_DIR and impulse responses into $IRS_DIR"
	log "[dry-run] No preset would be selected or enabled."
	exit 0
fi

command -v python3 >/dev/null 2>&1 || die 'python3 is required to render EasyEffects presets (see README.md).'

clone_or_update "$REPO_URL" "$CACHE_REPO"
[[ -d $CACHE_REPO ]] || die "EasyEffects presets cache is missing: $CACHE_REPO"

shopt -s nullglob
json_files=("$CACHE_REPO"/*.json)
irs_files=("$CACHE_REPO"/irs/*.irs)
if (( ${#json_files[@]} == 0 )); then
	die "No preset JSON files found in $CACHE_REPO; upstream layout may have changed."
fi

mkdir -p -- "$OUT_DIR" "$IRS_DIR"

for json in "${json_files[@]}"; do
	name=${json##*/}
	rendered=$(mktemp)
	python3 "$RENDER_JSON" "$json" "$rendered" "$PLACEHOLDER" "$PRESETS_DIR" || {
		rm -f -- "$rendered"
		die "Failed to render preset: $name"
	}
	deploy_file "$rendered" "$OUT_DIR/$name" 644
	rm -f -- "$rendered"
done

for irs in "${irs_files[@]}"; do
	deploy_file "$irs" "$IRS_DIR/${irs##*/}" 644
done

log "EasyEffects presets copied. Nothing was activated; choose a preset in EasyEffects yourself."
