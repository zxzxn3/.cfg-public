#!/usr/bin/env bash
# Install/update rime-ice (雾凇拼音) for fcitx5-rime.
#
# Upstream recommends the rime/plum recipe. plum runs against a temporary
# directory; the produced files are then deployed into the real Rime user
# directory, which is never rewritten in place. Personal data
# (custom_phrase.txt, *.custom.yaml, user dictionaries, userdb, sync/, trash/)
# is preserved. cfg manages rime_ice.custom.yaml and my_phrase.txt separately.
set -Eeuo pipefail
# shellcheck source=../lib/common.sh
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)/lib/common.sh"

[[ $(id -u) -ne 0 ]] || die 'Run as your normal user, not root.'

# fcitx5-rime user directory (confirmed in fcitx/fcitx5-rime src/rimeengine.cpp).
RIME_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/fcitx5/rime"
PLUM_URL="https://github.com/rime/plum.git"
PLUM_DIR="$CACHE_DIR/plum"
PACKAGE="iDvel/rime-ice"
REQUIRED_FILES=(default.yaml rime_ice.schema.yaml rime_ice.dict.yaml)
REQUIRED_DIRS=(cn_dicts)

# Rime needs a deployment (schema/dictionary rebuild), not just a reload of
# Fcitx configuration. SetConfig routes this URI to RimeEngine::deploy().
redeploy_rime() {
	if [[ $DRY_RUN != 1 ]]; then
		if ! command -v fcitx5-remote >/dev/null 2>&1 || ! fcitx5-remote --check; then
			log 'Fcitx5 is not running in this session; Rime will load the files when started.'
			return
		fi
		command -v dbus-send >/dev/null 2>&1 || die 'Files installed, but dbus-send is missing. Use the Rime menu to Deploy.'
	fi
	log 'Requesting Rime deployment in the running Fcitx5 session.'
	run dbus-send --session --type=method_call --print-reply --reply-timeout=30000 \
		--dest=org.fcitx.Fcitx5 /controller org.fcitx.Fcitx.Controller1.SetConfig \
		string:fcitx://config/addon/rime/deploy variant:string: || \
		die 'Files installed, but Rime deployment failed. Use the Rime menu to Deploy.'
}

log "Fcitx5 Rime user directory: $RIME_DIR"
if [[ $DRY_RUN == 1 ]]; then
	log "[dry-run] Would stage '$PACKAGE' with plum in a temporary directory, then deploy managed files into $RIME_DIR"
	log "[dry-run] Existing custom_phrase.txt, *.custom.yaml, user dictionaries and userdb are preserved; cfg manages rime_ice.custom.yaml and my_phrase.txt separately."
	redeploy_rime
	exit 0
fi

clone_or_update "$PLUM_URL" "$PLUM_DIR"
[[ -f "$PLUM_DIR/rime-install" ]] || die "plum is incomplete: $PLUM_DIR/rime-install is missing"

stage=$(mktemp -d "${TMPDIR:-/tmp}/rime-ice.XXXXXX")
cleanup() { rm -rf -- "$stage"; }
trap cleanup EXIT

log "Staging rime-ice with the upstream plum recipe (temporary directory)."
env rime_dir="$stage" bash "$PLUM_DIR/rime-install" "$PACKAGE"

# A recipe that silently produced too little must never count as success.
for required in "${REQUIRED_FILES[@]}"; do
	[[ -f "$stage/$required" ]] || die "Upstream recipe did not produce '$required'; refusing to deploy a partial rime-ice. Check the upstream recipe, or install the release full.zip archive manually instead."
done
for required in "${REQUIRED_DIRS[@]}"; do
	[[ -d "$stage/$required" ]] || die "Upstream recipe did not produce the dictionary directory '$required/'; refusing to deploy a partial rime-ice."
done

mkdir -p -- "$RIME_DIR"

deployed=0 kept=0
while IFS= read -r -d '' src; do
	rel=${src#"$stage"/}
	base=${rel##*/}
	case $rel in
	userdb/* | */userdb/* | sync/* | sync/*/* | trash/* | installation.yaml) continue ;;
	esac
	case $base in
	*.custom.yaml) continue ;;
	custom_phrase.txt)
		if [[ -e "$RIME_DIR/$rel" ]]; then
			log "Keeping existing personal file: $RIME_DIR/$rel"
			kept=$((kept + 1))
			continue
		fi
		;;
	esac
	deploy_file "$src" "$RIME_DIR/$rel" 644
	deployed=$((deployed + 1))
done < <(find "$stage" -type f -print0)

log "rime-ice deployed ($deployed files; $kept personal files kept)."
log "User dictionaries, userdb, sync and trash were not touched."
redeploy_rime
