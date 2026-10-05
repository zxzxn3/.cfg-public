# 配置管理

`cfg` 是由 `git` 进行版本管理的个人配置库，其工作假设是机器的软件环境（发行版 `cachyos`、桌面 `hyprland`、软件包）一致，同时对不同的软件环境和硬件环境保有稳健性。`cfg` 通常以 `bare repo` 的形式存放在 `$HOME/.cfg`，工作树为 `$HOME`；在 `fish` 中通过名为 `cfg` 的函数快捷访问，相当于 `git --git-dir=$HOME/.cfg --work-tree=$HOME`。由于工作树范围较大，`cfg` 通常采用白名单管理。

利用 `cfg` 管理配置文件时有如下决策和偏好：

- 源指的是被程序识别的配置，默认配置是一种特殊的源，随程序提供，可能编译进程序；程序识别非默认配置的方式分成单源和多源；在没有可用源或源不满足要求时，程序按自身规则回退到默认配置或无法工作；在默认配置也无法满足时，程序无法工作；源的修改有多种方式，最简单普适的是用户直接修改，这和通过程序专属读写 UI 修改需要区分开来（读写 UI 指除了会修改源以外，还会读取来反馈变更）；有些程序支持零侵入（如 `drop-in`）或低侵入（`include/require/source`）的方式来添加源；当存在多个有效源时，程序有不同的合成行为来得到有效配置，包括分级、择取、合并、覆盖；出于让配置改动可见、可控、边界清晰的考虑，有[决策树](#附表a决策树)决定如何管理配置文件：

  **程序没有提供该源的满足运行的默认值：**

  - `.config/hypr/{hyprlock.conf|hypridle.conf}`
  - `.config/fish/functions/*.fish`
  - `.config/zellij/layouts/*.kdl`
  - `.config/newsboat/urls`

  **由读写 UI 管理：**

  - `.config/mimeapps.list` 中的文件关联由 KDE 文件关联设置（`kcmshell6 kcm_filetypes` / `keditfiletype`）读写，协议处理关联另行维护
  - `.config/fcitx5/conf/classicui.conf` 由 `fcitx5-configtool` 管理
  - `.config/hypr/hyprland-gui.lua` 由 `hyprmod` 管理
  - `.config/juhradial/config.json` 由 `juhradial-settings` 管理
  - `.config/syncthingtray.ini` 由 `syncthingtray-qt6` 管理
  - `.local/state/noctalia/settings.toml` 由 `noctalia` 管理

  **有零侵覆盖机制：**

  - `.config/uwsm/env` 可由 `.config/uwsm/env.d/*` 进行零侵可控覆盖
  - `.local/share/fcitx5/rime/rime_ice.schema.yaml` 可由 `rime_ice.custom.yaml` 进行零侵可控覆盖
  - `.config/fastfetch/config.jsonc` 按查找顺序择取配置文件，不自动合并各级文件，未指定项沿用内置默认值
  - `.config/kitty/kitty.conf` 在系统级配置之上按项覆盖，未指定项沿用已有值

  **有低侵入机制：**

  - `.config/hypr/hyprland.lua` 使用 `require("hyprland-custom")`

- 配套资源的放置尊重惯例，但这种惯例最好是来自于开发者的。大部分情况都采用就近放置。

文件到位后，按软件要求重新加载、部署或重启，使配置生效。在大部分情况下，`cfg` 希望无需额外初始化，但有少数情况下，例如超出 `cfg` 的管辖范围，又或是出于控制仓库体积、复用上游提供的安装脚本等考虑，在首次部署时需要通过可选脚本来初始化 `cfg` 的工作环境（添加包仓库、安装包、调整防火墙、添加预设包、设置 `plymouth` 动画等等）。

利用 `cfg` 管理用户脚本项目时有如下偏好：

- 用户自行维护、无需编译的脚本项目，通常与配套资源集中放置在 `.local/share/<项目名>/`，各自维护 Git 忽略规则；命令入口可通过软链接放在 `.local/bin/`。这是出于项目灵活性的考虑。

## 附表A：决策树

  ```mermaid
  flowchart TD
    A{"程序是否提供了该源的默认值？"}
    A -- 否 --> B["直接管理"]
    A -- 是 --> C{"是否提供全部或部分设置的<br/>专属读写 UI？"}

    C -- 是 --> D["将 UI 管理的源纳入管理"]
    C -- 否 --> E{"能否零侵入地添加源<br/>完成所需改动？"}

    E -- 是 --> F["添加源并管理添加的源"]
    E -- 否 --> G{"能否低侵入地添加源<br/>完成所需改动？"}

    G -- 是 --> H["添加源并管理添加的源<br/>同时管理必要的入口改动"]
    G -- 否 --> I["直接管理"]
  ```

## 附表B：配置机制

`.config/mimeapps.list`：用户级 XDG 共享关联配置，默认应用与添加、移除关联各有解析规则，不是整文件替换。没有此文件时仍有满足程序工作要求的回退关联；KDE 文件关联设置（`kcmshell6 kcm_filetypes`，或 `keditfiletype text/plain` 编辑单个类型）可读写其中的文件关联，但未覆盖本次所需的全部协议处理设置。按决策树“全部或部分设置”的 UI 判定，走 A → C → D，将这份共享源纳入管理；未被该 UI 覆盖的协议处理关联另行维护。该文件同时具备无需修改系统源即可添加的用户级覆盖机制，但分类优先采用 UI 分支，无需额外生成脚本。[查找顺序](https://specifications.freedesktop.org/mime-apps/latest/file.html)、[默认应用与回退规则](https://specifications.freedesktop.org/mime-apps/latest/default.html)、[保存源码](https://github.com/KDE/kde-cli-tools/blob/master/keditfiletype/mimetypedata.cpp)

`.config/fastfetch/config.jsonc`：存在多级查找路径，默认读取先找到的可用配置，不自动合并各级文件；用户配置缺少的选项沿用内置默认值，而非系统配置中的值；有配置生成 UI（`fastfetch --gen-config`），不能据此视为持续读写现有配置的设置 UI。[配置说明](https://github.com/fastfetch-cli/fastfetch/wiki/Configuration)、[加载源码](https://github.com/fastfetch-cli/fastfetch/blob/2.69.0/src/fastfetch.c)

`.config/kitty/kitty.conf`：存在多级配置，先加载 `/etc/xdg/kitty/kitty.conf`（若存在），再加载按路径择取的用户配置；后加载的普通选项覆盖先前值，未指定项保留，映射等累积项有各自的清除规则；支持 `include`；有打开配置编辑器的入口（文件不存在时生成注释模板），也有主题选择 TUI，可写入主题文件及其 `include`，并非完整设置 UI。[加载规则](https://sw.kovidgoyal.net/kitty/invocation/#cmdoption-kitty-config)、[配置入口](https://sw.kovidgoyal.net/kitty/conf/)、[主题 TUI](https://sw.kovidgoyal.net/kitty/kittens/themes/)

`.config/fish/functions/*.fish`：存在多级函数查找路径，按 `$fish_function_path` 顺序为每个函数加载首个同名文件；不同名函数可以共存，同名函数不合并；有 `funced` 交互编辑或调用外部编辑器、`funcsave` 保存的入口，不能简单写成“无 UI”。[自动加载](https://fishshell.com/docs/current/tutorial.html#autoloading-functions)、[交互编辑](https://fishshell.com/docs/current/cmds/funced.html)、[保存](https://fishshell.com/docs/current/cmds/funcsave.html)

`.config/hypr/hyprlock.conf`：存在用户级、系统级查找路径，默认择取一份主配置，不自动合并各级文件；未指定项使用内置默认值，可用 `source` 显式加载其他文件；无内置配置读写 UI。[加载源码](https://github.com/hyprwm/hyprlock/blob/v0.9.6/src/config/ConfigManager.cpp)

`.config/hypr/hypridle.conf`：存在用户级、系统级查找路径，默认择取一份主配置，不自动合并各级文件；未指定项使用内置默认值，可用 `source` 显式加载其他文件；无内置配置读写 UI。[加载源码](https://github.com/hyprwm/hypridle/blob/v0.1.8/src/config/ConfigManager.cpp)

`.config/newsboat/urls`：没有系统级订阅列表与用户级列表的自动合并；存在 XDG 配置目录时使用它，否则使用 `~/.newsboat/`，也可用 `-u` 指定文件；缺少订阅不会继承一份系统列表；TUI 的 `edit-urls` 调用文本编辑器，返回后重载，也可通过 OPML 导入写入。[文件位置与编辑入口](https://newsboat.org/releases/2.44/docs/newsboat.html)

`.config/zellij/layouts/codex.kdl`：按名称或路径选用独立布局，不与上级同名布局自动合并；`codex.kdl` 需通过 `--layout codex` 等方式选用，文件存在不代表自动启用；有布局管理 TUI，可选择布局、将当前会话或标签页保存为布局，也可用 `dump-layout` 导出，不代表运行中每次调整都会回写原文件。[布局加载](https://zellij.dev/documentation/layouts.html)、[布局管理 UI](https://zellij.dev/tutorials/layouts/)

`.config/fcitx5/conf/classicui.conf`：按 Fcitx 的配置查找路径读取首个可打开的同名文件，不逐项合并多份同名文件；缺少的选项沿用插件默认值；有 `fcitx5-configtool` 读写 UI，通过 Fcitx 配置接口保存用户设置。[Classic UI 源码](https://github.com/fcitx/fcitx5/blob/master/src/ui/classic/classicui.cpp)、[INI 加载](https://github.com/fcitx/fcitx5/blob/master/src/lib/fcitx-config/iniparser.cpp)、[路径查找](https://github.com/fcitx/fcitx5/blob/master/src/lib/fcitx-utils/standardpaths.cpp)

`.config/hypr/hyprland-gui.lua`：不是自动发现的独立配置层，由主配置的 `require("hyprland-gui")` 显式加载；与其他 Lua 配置按调用顺序生效，普通选项可被后续赋值覆盖，规则等不一定是替换；有 HyprMod 读写 UI，主要管理这份文件，不保证反映后续自定义文件覆盖后的全部状态。[HyprMod](https://github.com/BlueManCZ/hyprmod)、[本仓库加载顺序](.config/hypr/hyprland.lua)

`.config/juhradial/config.json`：主配置读取用户目录中的一份 JSON，缺少文件或字段时使用程序内置默认值；设置端将用户值递归合入默认配置，不是系统级与用户级文件逐份叠加；有 `juhradial-settings` 读写 UI，保存后通知 daemon 重载。[配置说明](https://github.com/JuhLabs/juhradial-mx/blob/master/docs/configuration.md)、[设置端源码](https://github.com/JuhLabs/juhradial-mx/blob/master/settings-qt/bridge/backend.py)

`.config/syncthingtray.ini`：普通安装使用 QSettings 用户级 INI，未设置的键可按 QSettings 规则回退到系统级设置，再使用程序给出的默认值，并非整文件替换；工作目录或可执行文件旁的同名 INI 可切换为便携配置；有 Syncthing Tray 读写 UI。这里是托盘程序的配置，不是 Syncthing 本体配置。[程序说明](https://github.com/Martchus/syncthingtray#location-of-the-configuration-file)、[配置文件选择](https://github.com/Martchus/qtutilities/blob/master/resources/resources.cpp)、[QSettings 回退规则](https://doc.qt.io/qt-6/qsettings.html#fallback-mechanism)

`.local/state/noctalia/settings.toml`：存在多层合并，内置默认值之上先合并 `.config/noctalia/` 中按文件名排序的 TOML，再叠加这份 GUI 覆盖文件；GUI 层对同一设置的值优先；有 Noctalia 读写 UI，GUI、IPC 等持久化操作写入此文件，缺少它时沿用前面各层。[配置机制](https://docs.noctalia.dev/noctalia/configuration/)

`.config/uwsm/env`、`.config/uwsm/env.d/*`：存在多级加载，按系统到用户的优先级依次执行 shell 文件；同一目录中先加载 `env`，再加载 `env.d/*`，之后还有桌面专用的 `env-<desktop>` 及其片段；后续赋值可覆盖同名变量，也可显式追加或取消变量，用户文件存在不阻止系统文件执行；无内置配置读写 UI。[环境加载顺序](https://github.com/Vladimir-csp/uwsm#readme)

`.local/share/fcitx5/rime/rime_ice.schema.yaml`、`rime_ice.custom.yaml`：方案源文件和定制补丁共同参与部署；同名用户方案可遮蔽共享目录中的方案，`*.custom.yaml` 则通过 `patch` 修改对应方案的指定项，生成供运行时使用的配置，不是用整个 custom 文件替换方案；补丁需重新部署才生效，Fcitx 的设置或部署入口不等于任意方案补丁的读写 UI，此文件仍以文本维护。[Rime 定制机制](https://github.com/rime/home/wiki/CustomizationGuide)

`.config/hypr/hyprland.lua`：当前仓库通过 `require` 依次加载发行版的 `config.*`、`hyprland-gui`、`hyprland-custom`；属于显式组合，普通设置按执行顺序覆盖，规则或绑定按各自接口处理，不能概括成所有配置全量覆盖；没有完整读写任意 Lua 代码的设置 UI，HyprMod 日常写入独立 GUI 文件，但首次接入或迁移可以修改主入口。[本仓库主配置](.config/hypr/hyprland.lua)、[HyprMod 接入源码](https://github.com/BlueManCZ/hyprmod/blob/main/hyprmod/core/setup.py)
