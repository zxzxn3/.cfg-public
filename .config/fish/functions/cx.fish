function cx --description 'zellij：一个 Codex 面板接续最近会话，另两个选择会话'
    if zellij list-sessions --short 2>/dev/null | string match -q -- cx
        # 已经在了（-f 顺便跳过复活时的 "Press ENTER to run"）
        zellij attach -f cx
    else
        # zellij 0.45 的 `--session X --layout Y` 是「往 X 追加 tab」，
        # 会话不存在时会直接报错，所以先建出来再把默认那个 tab 换成 codex
        zellij attach cx -c -b
        zellij --session cx action override-layout codex
        zellij attach cx
    end
end
