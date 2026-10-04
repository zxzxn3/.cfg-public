#!/usr/bin/env bash
set -euo pipefail

# 在另一台机器上直接运行（从 GitHub 取本脚本）：
#   gh api 'repos/zxzxn3/cfg/contents/deploy.sh' \
#     -H 'Accept: application/vnd.github.raw+json' | bash
# 如果要带参数
#   ... | bash -s -- -f
# --- 默认值 -----------------------------
DEFAULT_REMOTE='https://github.com/zxzxn3/cfg.git'
DEFAULT_GIT_DIR="$HOME/.cfg"
DEFAULT_WORK_TREE="$HOME"
# 锁不能放 /run/lock（那是 root 的）；XDG_RUNTIME_DIR 本身已按用户隔离
DEFAULT_LOCK="${XDG_RUNTIME_DIR:-/tmp}/cfg-deploy-$UID.lock"

# 有控制终端才能交互确认（脚本可能是管道来的，所以读 /dev/tty，不读 stdin）
TTY=0
if { : </dev/tty; } 2>/dev/null; then TTY=1; fi

usage() {
    cat <<HELP
Usage: bash deploy.sh [--remote URL] [--git-dir PATH] [--work-tree PATH] [-f|--force]

Deploy a repository's tracked files into a work-tree, using a bare Git directory
as the local copy.

  --remote URL      Repository to clone (default: $DEFAULT_REMOTE)
  --git-dir PATH    Bare Git directory (default: $DEFAULT_GIT_DIR)
  --work-tree PATH  Directory to deploy into (default: $DEFAULT_WORK_TREE)
  -f, --force       Ask nothing: delete an existing Git directory and overwrite
                    files already in the work-tree

--opt=VALUE also works.
HELP
}

# 交互确认；没有终端就当作"否"
confirm() {
    local answer
    [[ $TTY -eq 1 ]] || return 1
    read -r -p "$1 [y/N] " answer </dev/tty || return 1
    case $answer in y|Y|yes|Yes) return 0 ;; *) return 1 ;; esac
}

main() {
    local gitdir=$DEFAULT_GIT_DIR work_tree=$DEFAULT_WORK_TREE remote_url=$DEFAULT_REMOTE
    local force='' branch status_output checkout_failed baseline

    # 解析参数
    while (( $# )); do
        case "$1" in
            --remote) [[ $# -ge 2 && -n $2 ]] || { usage >&2; return 2; }; remote_url=$2; shift 2 ;;
            --remote=*) remote_url=${1#--remote=}; [[ -n $remote_url ]] || { usage >&2; return 2; }; shift ;;
            --git-dir) [[ $# -ge 2 && -n $2 ]] || { usage >&2; return 2; }; gitdir=$2; shift 2 ;;
            --git-dir=*) gitdir=${1#--git-dir=}; [[ -n $gitdir ]] || { usage >&2; return 2; }; shift ;;
            --work-tree) [[ $# -ge 2 && -n $2 ]] || { usage >&2; return 2; }; work_tree=$2; shift 2 ;;
            --work-tree=*) work_tree=${1#--work-tree=}; [[ -n $work_tree ]] || { usage >&2; return 2; }; shift ;;
            -f|--force) force=-f; shift ;;
            --help|-h) usage; return ;;
            *) usage >&2; return 2 ;;
        esac
    done

    # work-tree 是 $HOME，所以必须按普通用户跑：sudo 下 $HOME 会变成 /root
    # （--help/-h 在解析时就返回了，不需要过这一关）
    (( EUID != 0 )) || { printf 'Run as your normal user, not root: sudo would deploy into /root.\n' >&2; return 1; }

    # 加锁，并注册退出 / 信号处理
    exec 9>"$DEFAULT_LOCK"
    flock -n 9 || { printf 'Another deploy is running.\n' >&2; return 1; }
    umask 022
    # 暂存目录建在 gitdir 的父目录下（同一个文件系统），所以新库落地是原子 rename，不是跨文件系统复制
    mkdir -p -- "$(dirname -- "$gitdir")"
    work=$(mktemp -d -- "$(dirname -- "$gitdir")/.cfg-stage.XXXXXX")
    trap 'rm -rf -- "$work"' EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM

    # 已有仓库：-f 或交互确认才继续，否则中止
    if [[ -e $gitdir ]] && [[ -z $force ]] \
       && ! confirm "$gitdir already exists. Delete it and deploy from scratch?"; then
        printf '%s already exists; rerun with -f/--force to redeploy.\n' "$gitdir" >&2
        return 1
    fi

    # clone 到 gitdir 旁边的暂存目录（同一文件系统）-> 旧库挪进暂存区、新库原子就位 -> 铺文件。
    # 暂存区由 EXIT trap 收走：换位前失败旧库原地不动，换位后失败旧库随暂存区一起清掉。
    git clone --bare "$remote_url" "$work/repo"
    branch=$(git --git-dir="$work/repo" symbolic-ref --short HEAD)
    git --git-dir="$work/repo" rev-parse --verify 'HEAD^{commit}' >/dev/null
    # 裸克隆不写 remote.origin.fetch，也不建 refs/remotes/*，于是 branch 没有 upstream。
    # 用本地已有对象补出当前分支的跟踪 ref（不必再联网 fetch），让 branch 有 upstream。
    git --git-dir="$work/repo" config remote.origin.fetch '+refs/heads/*:refs/remotes/origin/*'
    git --git-dir="$work/repo" update-ref "refs/remotes/origin/$branch" "refs/heads/$branch"
    git --git-dir="$work/repo" branch --set-upstream-to="origin/$branch" "$branch" >/dev/null
    # 加载旧版本状态，让后续 checkout 能识别并删除新版已移除的文件。
    # 取不到就当作没有旧版本：这一次不删旧文件，部署照常进行。
    if [[ -e $gitdir ]] && git --git-dir="$work/repo" fetch --no-tags "$gitdir" HEAD; then
        baseline=FETCH_HEAD
        git --git-dir="$work/repo" read-tree FETCH_HEAD
        git --git-dir="$work/repo" update-ref --no-deref HEAD FETCH_HEAD
    fi
    # 换位之前，先把新旧之间的差异打出来（A 新增 / M 修改 / D 删除），部署照旧往下走
    baseline=${baseline:-$(git --git-dir="$work/repo" hash-object -t tree /dev/null)}
    git --no-pager --git-dir="$work/repo" diff --name-status "$baseline" "$branch"
    if [[ -e $gitdir || -L $gitdir ]]; then mv -T -- "$gitdir" "$work/old"; fi
    mv -T -- "$work/repo" "$gitdir"
    # 铺文件。被拒、或成功了却把本地改动带过去，都算"没到位"：没给 -f 且能交互时问一次。
    mkdir -p -- "$work_tree"
    while :; do
        checkout_failed=0
        git --git-dir="$gitdir" --work-tree="$work_tree" checkout --no-overwrite-ignore $force "$branch" || checkout_failed=1
        status_output=$(git --git-dir="$gitdir" --work-tree="$work_tree" status --porcelain --untracked-files=no)
        if [[ $checkout_failed == 0 && -z $status_output ]]; then break; fi
        # `! confirm` 读作"用户没同意"：带了 -f、或没同意 → 报错停下；答 y 时落到下面重试
        if [[ -n $force ]] || ! confirm "Overwrite the files already in $work_tree?"; then
            printf 'Checkout failed or %s differs from the target version:\n%s\n' "$work_tree" "$status_output" >&2; return 1
        fi
        force=-f
    done
    printf 'Deployed %s from %s. Running programs were not restarted; log out or restart them yourself.\n' "$work_tree" "$remote_url"
}

main "$@"
