# cfg -- run git in the bare ~/.cfg dotfiles repo (work tree $HOME)
#        without retyping --git-dir/--work-tree every time.
#
# The repo is owned by this user, so no sudo is needed.
#
#   cfg status --short
#   cfg log --oneline -10
#   cfg add .gitignore
#   cfg show HEAD:.gitconfig

function cfg --description 'git in the ~/.cfg bare repo (work tree $HOME)'
    set -l git_dir $HOME/.cfg
    set -l work_tree $HOME

    set -l git (command -v git)
    if test -z "$git"
        echo 'cfg: git not found' >&2
        return 127
    end

    if not $git --git-dir=$git_dir rev-parse --git-dir >/dev/null 2>&1
        echo "cfg: cannot read $git_dir" >&2
        return 1
    end

    $git --git-dir=$git_dir --work-tree=$work_tree $argv
end
