# kb -- 起 wayvibes（打字时的机械键盘音效）
#
#   kb                           brown PBT 音效 + K580，音量 3，后台跑
#   kb -v 5                      换音量（0-10）
#   kb topre-purple-hybrid-pbt   换音效包，kb -h 列出可用名称
#   kb -f                        前台跑，Ctrl-C 停
#   kb -s                        停掉正在跑的
#
# 键盘不在线时让你现场重选一个（存进 wayvibes 的 input_device），
# 在非交互环境下则直接报错，不会卡在 wayvibes 的选设备提示上。
# 已经在跑的话会先杀掉再起，避免两个实例叠音。
# WAYVIBES_HOME 与安装脚本共用；WAYVIBES_DEVICE 可覆盖默认键盘名。

function kb --description 'wayvibes 键盘音效：brown PBT + K580'
    set -l bin /usr/bin/wayvibes
    set -l pack_root $HOME/wayvibes/soundpacks
    if set -q WAYVIBES_HOME; and test -n "$WAYVIBES_HOME"
        set pack_root "$WAYVIBES_HOME/soundpacks"
    end
    set -l pack cherrymx-brown-pbt
    set -l device 'Logi K580 Keyboard'
    if set -q WAYVIBES_DEVICE; and test -n "$WAYVIBES_DEVICE"
        set device "$WAYVIBES_DEVICE"
    end
    set -l volume 3
    set -l mode start

    while set -q argv[1]
        set -l arg $argv[1]
        switch $arg
            case -v
                if not set -q argv[2]
                    echo 'kb: -v 后面要跟一个 0-10 的音量' >&2
                    return 2
                end
                set volume $argv[2]
                set -e argv[1..2]
            case -f --foreground
                set mode foreground
                set -e argv[1]
            case -s -bg --stop
                set mode stop
                set -e argv[1]
            case -h --help
                echo '用法：kb [-v 音量] [-f] [-s] [音效包名]'
                echo "音效包（在 $pack_root）："
                command ls -1 $pack_root | string match -v mouse | string replace -r '^' '  '
                return 0
            case '-*'
                echo "kb: 不认识的参数 $arg" >&2
                return 2
            case '*'
                set pack $arg
                set -e argv[1]
        end
    end

    # 已经在跑就先停掉，免得两路音效叠在一起
    set -l running (pgrep -x wayvibes)
    if test -n "$running"
        kill $running 2>/dev/null
        for i in (seq 20)
            pgrep -x wayvibes >/dev/null 2>&1
            or break
            sleep 0.1
        end
        if pgrep -x wayvibes >/dev/null 2>&1
            echo 'kb: 旧进程没退出，强杀' >&2
            pkill -9 -x wayvibes
        end
        if test "$mode" = stop
            echo 'kb: 已停' >&2
            return 0
        end
        echo 'kb: 停掉上一个实例' >&2
    else if test "$mode" = stop
        echo 'kb: 没有在跑' >&2
        return 0
    end

    if not test -x $bin
        echo "kb: 找不到 $bin" >&2
        return 127
    end

    set -l pack_path $pack_root/$pack
    if not test -f $pack_path/config.json
        echo "kb: 音效包不存在：$pack_path" >&2
        echo '可用的：' >&2
        command ls -1 $pack_root | string match -v mouse | string replace -r '^' '  ' >&2
        return 1
    end

    # 认哪个键盘：默认点名 K580；不在线时改用 input_device 里存的那个
    set -l dev_args --device-name $device
    if not grep -qx -- "N: Name=\"$device\"" /proc/bus/input/devices
        echo "kb: 键盘「$device」不在线" >&2
        if not isatty
            echo '（不是交互终端，没法让你选）现在是这些键盘：' >&2
            grep -oP '^N: Name="\K[^"]+' /proc/bus/input/devices \
                | string match -r -i --entire 'kbd|keyboard' \
                | string replace -r '^' '  ' >&2
            return 1
        end
        echo '选一个用（1 开始）:' >&2
        $bin --device
        or begin
            echo 'kb: 没选成，放弃' >&2
            return 1
        end
        set -l chosen (string replace -r '^NAME:|^PATH:' '' \
            < $HOME/.config/wayvibes/input_device 2>/dev/null)
        test -n "$chosen"; and echo "kb: 记下了「$chosen」，下次自动用它" >&2
        # 让 wayvibes 自己读 input_device，不再点名
        set dev_args
    end

    if test "$mode" = foreground
        exec $bin $pack_path -v $volume $dev_args
    else
        $bin $pack_path -v $volume $dev_args --background
        echo "kb: $pack 已起，音量 $volume" >&2
    end
end
