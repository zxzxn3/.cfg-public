#!/usr/bin/env bash
# Shared helpers for the bootstrap setup scripts.
# Sourced by setup/*.sh; not meant to be executed on its own.

BOOTSTRAP_BASE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
CACHE_DIR="${CACHE_DIR:-${BOOTSTRAP_CACHE:-${XDG_CACHE_HOME:-$HOME/.cache}/linux-cfg-bootstrap}}"
DRY_RUN="${DRY_RUN:-0}"

log() { printf '[bootstrap] %s\n' "$*"; }
warn() { printf '[bootstrap] warning: %s\n' "$*" >&2; }
die() {
	printf '[bootstrap] error: %s\n' "$*" >&2
	exit 1
}

# run CMD...  Prints the command in --dry-run instead of executing it.
run() {
	if [[ $DRY_RUN == 1 ]]; then
		printf '[dry-run]'
		printf ' %q' "$@"
		printf '\n'
		return 0
	fi
	"$@"
}

# backup_target PATH -> copies PATH to PATH.bak.N (N = first free number).
# A symlink is backed up as the link itself (cp -a), never followed.
backup_target() {
	local target=$1 index=1
	while [[ -e "$target.bak.$index" || -L "$target.bak.$index" ]]; do
		index=$((index + 1))
	done
	cp -a -- "$target" "$target.bak.$index"
	log "Backed up $target -> $target.bak.$index"
}

# deploy_file SRC DST [MODE]  Idempotent; backs up a different existing DST.
deploy_file() {
	local src=$1 dst=$2 mode=${3:-644}
	[[ -f $src ]] || die "Missing source file: $src"
	if [[ -d $dst && ! -L $dst ]]; then
		die "Refusing to replace a directory with a file: $dst"
	fi
	if [[ -f $dst && ! -L $dst ]] && cmp -s -- "$src" "$dst"; then
		log "Unchanged: $dst"
		return 0
	fi
	if [[ $DRY_RUN == 1 ]]; then
		if [[ -e $dst || -L $dst ]]; then
			log "[dry-run] Would back up and replace $dst"
		else
			log "[dry-run] Would install $src -> $dst"
		fi
		return 0
	fi
	mkdir -p -- "$(dirname -- "$dst")"
	if [[ -e $dst || -L $dst ]]; then
		backup_target "$dst"
	fi
	# -T treats DST as a plain file; remove a symlink so the link itself is
	# replaced instead of writing through it.
	[[ -L $dst ]] && rm -f -- "$dst"
	install -m "$mode" -T -- "$src" "$dst"
	log "Installed: $dst"
}

# clone_or_update URL DIR
# Stops on any failure: a stale or non-git cache must never be treated as a
# valid upstream source.
clone_or_update() {
	local url=$1 dir=$2
	if [[ $DRY_RUN == 1 ]]; then
		log "[dry-run] Would clone or update $url into $dir"
		return 0
	fi
	if [[ -e $dir && ! -d "$dir/.git" ]]; then
		die "Cache path exists but is not a git checkout: $dir. Remove it and rerun."
	fi
	if [[ -d "$dir/.git" ]]; then
		if ! git -C "$dir" pull --ff-only --quiet; then
			die "Failed to update $dir from $url. Fix the network or cache state and rerun."
		fi
	else
		mkdir -p -- "$(dirname -- "$dir")"
		git clone --depth 1 -- "$url" "$dir" || die "Failed to clone $url"
	fi
}
