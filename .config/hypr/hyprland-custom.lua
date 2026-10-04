-- ===============================================================
-- 本机自定义配置（与 hyprland.lua 同级）
--
-- 由 hyprland.lua 末尾的 require("hyprland-custom") 载入，在发行版自带的
-- config/*.lua 之后再执行，因此这里可以覆盖它们的设置。
--
-- 注意：require 会给每个文件独立作用域，config/binds.lua 里的
--       `local launchPrefix` 之类**在这里看不到**，需要自己定义。
--       config/variables.lua 里的 BROWSER / TERMINAL 等是全局变量，可直接用。
-- ===============================================================


-- ---------------------------------------------------------------
-- 开机自启
-- 发行版自带的 config/autostart.lua 只启动 noctalia，这里追加 hypridle。
-- （hypridle 不通过 systemd user unit 启动，改配置后需 pkill hypridle 再启动。）
-- ---------------------------------------------------------------
hl.on("hyprland.start", function()
  hl.exec_cmd("hypridle")
end)


-- ---------------------------------------------------------------
-- 锁屏统一走 hyprlock
--
-- 背景：发行版 config/binds.lua 里 SUPER+L 绑的是 `noctalia msg session lock`，
--       也就是 noctalia 自带的锁屏（不是 hyprlock）。同时 hypridle 的
--       `loginctl lock-session` 也会被 noctalia 的 logind 集成接走。
--       结果就是空闲锁屏时两个锁屏器抢 ext_session_lock，谁先抢到就是谁，
--       经常显示成 noctalia 的锁屏而不是本机定制的 hyprlock。
--       配套设置：~/.local/state/noctalia/settings.toml 里 `[lockscreen] enabled = false`。
-- ---------------------------------------------------------------
hl.unbind("SUPER + L")
hl.bind("SUPER + L", hl.dsp.exec_cmd("hyprlock"), {
  description = "Lock screen (hyprlock)",
})


-- ---------------------------------------------------------------
-- 自定义快捷键
-- ---------------------------------------------------------------
local launchPrefix = "uwsm app -- " -- 与 config/binds.lua 保持一致

-- SUPER+W: 窗口已开则聚焦最近用过的那个，未开则启动浏览器
local function focus_or_launch(class, cmd)
  local target
  for _, w in ipairs(hl.get_windows()) do
    if w.class:lower() == class:lower()
      and (not target or w.focus_history_id < target.focus_history_id) then
      target = w
    end
  end
  if target then
    hl.dispatch(hl.dsp.focus({ window = target }))
  else
    hl.exec_cmd(cmd)
  end
end

hl.unbind("SUPER + W")
hl.bind("SUPER + W", function()
  focus_or_launch("firefox", launchPrefix .. BROWSER)
end, {
  description = "Focus or launch Firefox",
})

-- SUPER+C: 微信唤出 / 收回
--
--   没开        → 启动
--   已开且收起  → 唤出并聚焦
--   已开且显示中 → 收回
--
-- Hyprland 没有原生「最小化」，所以收回用的是 special workspace：
-- 把微信挪进独立的 special:wechat，收起=让这块工作区保持隐藏/收起，唤出=显示。
-- 窗口只是被挪到看不见的地方，进程一直在跑，托盘图标、消息收发都不受影响
-- （真正的「关闭窗口」会走 SIGTERM 退出程序，那是另一回事）。
-- 用独立的 wechat 而不是默认的 special，是为了不跟 SUPER+S / SUPER+SHIFT+S
-- 的 scratchpad 互相干扰。
--
-- 注意：发行版 config/binds.lua 把 SUPER+C 绑给了计算器（CALCULATOR），
--       这里先解绑再覆盖，计算器仍可用 XF86Calculator 键唤出。
local wechatCmd     = launchPrefix .. "/usr/lib/wechat-universal/start.sh"
local wechatSpecial = "special:wechat"
local wechatClass   = "wechat"

-- 关于 special workspace 的背景虚化
--
-- scratchpad 显示时，decoration:blur:special 会把背后的内容整片糊掉。
-- 这个开关是全局的、没有 per-workspace 版本，所以做法是：
-- 只在「显示 wechat 这一刻」把它临时关掉，收起后立刻恢复原值；
-- 显示别的 scratchpad 之前也先恢复，保证它们照旧有背景虚化。
--
-- 关键点：Hyprland 在**显示那一刻**读这个开关并缓存，之后再改不会重绘
-- （reload 会生效，运行中 hl.config 改则不生效）。所以必须在 toggle 之前设好。
local blurSpecialBase = hl.get_config("decoration:blur:special")
if type(blurSpecialBase) ~= "boolean" then
  blurSpecialBase = true
end

local function set_special_blur(on)
  hl.config({ decoration = { blur = { special = on } } })
end

-- 唤出 / 收起唯一入口。show 必须赶在 toggle 之前关虚化，hide 之后立刻恢复。
local function wechat_show()
  set_special_blur(false)
  hl.dispatch(hl.dsp.workspace.toggle_special(wechatClass))
end

local function wechat_hide()
  hl.dispatch(hl.dsp.workspace.toggle_special(wechatClass))
  set_special_blur(blurSpecialBase)
end

-- 记录 wechat scratchpad 当前是不是显示着。Hyprland 的 Lua API 查不出
-- 「某块 special 是否正在显示」（get_active_special_workspace 返回 nil），
-- 所以自己记，并靠 workspace.special_active 事件校正。
local wechatShown = false
-- 刚按下启动键、还在等窗口映射出来。由下面的 window.open 收尾。
local wechatPending = false

hl.on("workspace.special_active", function(ws, _mon)
  -- ws 在「收起」事件里是失效对象，读字段会报错，用 pcall 兜住
  local name
  local ok = pcall(function()
    name = ws.name
  end)

  if ok and name == wechatSpecial then
    wechatShown = true
  else
    -- 收起的是 wechat，或者显示的是别的 scratchpad → 恢复全局默认
    wechatShown = false
    set_special_blur(blurSpecialBase)
  end
end)

-- 冷启动收尾：只 exec 是不够的。把窗口挪进 special 并不会把 special 显示
-- 出来，所以第一次按键只会得到一个落在当前工作区的普通窗口，得按第二下才
-- 进 scratchpad。挂在 window.open 上等它映射，再挪进去并唤出。
hl.on("window.open", function(w)
  if not wechatPending then
    return
  end
  local ok, class = pcall(function()
    return w.class
  end)
  if not (ok and class and class:lower() == wechatClass) then
    return
  end
  wechatPending = false
  hl.dispatch(hl.dsp.window.move({ window = w, workspace = wechatSpecial, follow = false }))
  wechat_show()
end)

local function wechat_window()
  local target
  for _, w in ipairs(hl.get_windows()) do
    if w.class:lower() == "wechat"
      and (not target or w.focus_history_id < target.focus_history_id) then
      target = w
    end
  end
  return target
end

hl.unbind("SUPER + C")
hl.bind("SUPER + C", function()
  local win = wechat_window()

  if not win then
    wechatPending = true
    hl.exec_cmd(wechatCmd)
    return
  end

  if win.workspace and win.workspace.name == wechatSpecial then
    if wechatShown then wechat_hide() else wechat_show() end
  else
    -- 还开在普通工作区（窗口规则生效前就存在的实例）：这一下算「收回」。
    -- follow=false 让移动过程不抢焦点，也不顺带把 special 显示出来。
    set_special_blur(blurSpecialBase)
    hl.dispatch(hl.dsp.window.move({ window = win, workspace = wechatSpecial, follow = false }))
  end
end, {
  description = "Toggle WeChat",
})

-- 发行版的 scratchpad（默认 special，SUPER+S / SUPER+SHIFT+S）行为照旧，
-- 只是显示之前先把背景虚化恢复回去，免得被 wechat 的临时设置带偏。
-- 这两条 rebind 与原配置 dispatcher 完全一致，只是多了恢复虚化这一步。
hl.unbind("SUPER + S")
hl.bind("SUPER + S", function()
  set_special_blur(blurSpecialBase)
  hl.dispatch(hl.dsp.workspace.toggle_special("special"))
end, {
  description = "Toggle scratchpad",
})

hl.unbind("SUPER + SHIFT + S")
hl.bind("SUPER + SHIFT + S", function()
  set_special_blur(blurSpecialBase)
  hl.dispatch(hl.dsp.window.move({ workspace = "special" }))
end, {
  description = "Move window to scratchpad",
})


-- ---------------------------------------------------------------
-- 外部程序的窗口规则
-- ---------------------------------------------------------------

-- JuhRadial MX（罗技 MX 鼠标的径向菜单）一次会开两个 XWayland 窗口，两者的
-- class 和 title 都是 "JuhRadial MX"：
--   1. 圆环本体（几百像素的方窗）
--   2. 「点击外围关闭」用的全屏 scrim（config.json 里 radial.click_outside_closes，
--      默认开）。它铺满整块桌面，只画 0.4% 不透明度的黑来占位。
-- scrim 一旦被 Hyprland 模糊，整块屏幕就跟着糊 —— 这就是菜单弹出后满屏发虚的
-- 原因。两个窗口同 class，一条规则可以同时管住。
hl.window_rule({
  name = "juhradial-overlay",
  match = { class = "^(JuhRadial MX)$" },
  float = true,      -- 覆盖层，不参与平铺
  pin = true,        -- 切工作区时不该把它带走
  no_blur = true,    -- 关键项：别让全屏 scrim 把整个桌面糊掉
  no_shadow = true,  -- 覆盖层不需要阴影
  no_anim = true,    -- 弹出/消失不做动画
  border_size = 0,   -- 无边框
})

-- Kitty 复用进程：已有窗口时避免重复初始化，保留每个独立窗口。
TERMINAL = "kitty --single-instance"
hl.unbind("SUPER + Return")
hl.bind("SUPER + Return", hl.dsp.exec_cmd(launchPrefix .. TERMINAL), {
  description = "Open Kitty (shared process)",
})
hl.unbind("CONTROL + SHIFT + Escape")
hl.bind("CONTROL + SHIFT + Escape", hl.dsp.exec_cmd(launchPrefix .. TERMINAL .. " -e btop"))
