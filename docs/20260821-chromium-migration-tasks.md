# Chromium 迁移任务台账

背景：小红书(XHS)和喜马拉雅(Ximalaya)两条自动化生产线要迁移到专用 Chromium，
不再共用日常 Chrome / 混用同一个 xhschrome 实例。确认的架构：

- **XHS Chromium**（1 个 profile）：承载 opencli Browser Bridge 扩展 + 原始 CDP 调试端口，
  合并原来"日常 Chrome 跑 opencli"和"xhschrome 跑 CDP"两个身份。
- **Ximalaya Chromium**（1 个全新 profile，全新端口）：纯 CDP，无需 opencli 扩展。
- cdp-proxy 需要两份实例：XHS 一份（沿用 9333→3456），Ximalaya 一份（新端口）。
- 迁移完成后卸载/断开日常 Chrome 上的 opencli Browser Bridge 扩展。

已确认：`cdp-proxy.mjs` 自身监听端口由 `CDP_PROXY_PORT`（默认 3456）控制，
目标 Chrome 调试端口由 `CHROME_DEBUG_PORT` 控制——两者独立，可分别配置。

**T1 变更记录（2026-08-21）**：Homebrew `chromium` cask 在本机过不了 Gatekeeper
（`spctl` 报 "code has no resources but signature indicates they must be present"，
`xattr -cr` 无法修复，macOS 直接弹"已损坏"），已卸载。改用 **Chrome for Testing**
（Google 官方为自动化场景发布的构建，`npx @puppeteer/browsers install chrome@stable`），
装在 `~/.chrome-for-testing/chrome/mac_arm-152.0.7977.54/chrome-mac-arm64/Google Chrome for Testing.app`。
虽然签名是 adhoc（非公证），但下载渠道不带 `com.apple.quarantine` 标记，
命令行直接启动（launchd 的方式）不会触发 Gatekeeper 拦截——已实测 `--version`、
`--remote-debugging-port` + `curl /json/version` 均正常。下文所有"Chromium"均指这个可执行文件。

规划的最终端口分配：
| 用途 | Chrome 调试端口 | cdp-proxy 监听端口 |
|---|---|---|
| XHS | 9333（不变） | 3456（不变） |
| Ximalaya | 9433（新） | 3457（新） |

## 任务列表

- [x] T1 安装 Chromium
      验收（改）：Chrome for Testing 可执行文件存在且通过实测（见上方变更记录）
      产出：`~/.chrome-for-testing/chrome/mac_arm-152.0.7977.54/chrome-mac-arm64/Google Chrome for Testing.app`
      依赖：无

- [x] T2 重建 XHS 专用浏览器 launchd 任务（Chrome → Chromium）
      验收：`com.eric.xhschrome` plist 已指向 Chrome for Testing 可执行文件，
            `--remote-debugging-port=9333`，`--user-data-dir=/Users/eric/.xhs-chromium-profile`（新 profile）；
            `launchctl` 重载后 `curl -s localhost:9333/json/version` 返回
            `"Browser": "Chrome/152.0.7977.54"`（实测通过）
      产出：`~/Library/LaunchAgents/com.eric.xhschrome.plist`
      依赖：T1

- [x] T3 在 XHS Chromium 里安装 opencli Browser Bridge 扩展
      验收（实测通过，2026-08-21）：binary 最终换成 chromium.org 官方持续构建
      （Chrome for Testing 有 mach-rendezvous 崩溃循环，详见 plist 内注释）。
      重载 `com.eric.xhschrome` 后 `curl -s localhost:9333/json/version` 返回
      `"Browser": "Chrome/154.0.8017.0"`；`opencli profile list` 新增
      `b38bbmnt — connected v1.0.22`（区别于日常 Chrome 的 `sb9scpbk`）；
      `OPENCLI_PROFILE=b38bbmnt opencli xiaohongshu whoami` 正确路由到该 profile，
      返回 `AUTH_REQUIRED`（全新 profile 尚未登录，符合预期，证明扩展已连上 daemon）。
      `opencli doctor` 因同时有两个 profile connected 且无 default 而报
      "Multiple Browser Bridge profiles are connected"——这是已知的多 profile
      歧义提示，不是扩展未连接（profile 列表里两个都明确标了 connected）。
      产出：`~/.chromium-org/Chromium.app`（永久安装）、
            `~/Library/LaunchAgents/com.eric.xhschrome.plist`（已更新）
      依赖：T2

- [x] T4【暂停点·需用户扫码】XHS Chromium 里登录小红书主站 + 创作者中心
      验收（实测通过，2026-08-21）：`OPENCLI_PROFILE=b38bbmnt opencli xiaohongshu search 测试 --limit 1`
      返回真实搜索结果；`OPENCLI_PROFILE=b38bbmnt opencli xiaohongshu whoami` 返回
      `logged_in: true`，`username: Eric | 职场潜台词翻译官`，`followers: 1781`
      产出：无（登录态落在 `/Users/eric/.xhs-chromium-profile` 内）
      依赖：T3

- [x] T5 新建 Ximalaya 专用浏览器 launchd 任务
      验收（实测通过，2026-08-21）：新 plist `com.eric.ximalayachrome`，chromium.org
      Chromium + `--remote-debugging-port=9433` + `--user-data-dir=/Users/eric/.ximalaya-chromium-profile`；
      `launchctl bootstrap` 后 `curl -s localhost:9433/json/version` 返回
      `"Browser": "Chrome/154.0.8017.0"`；`launchctl list` 显示 PID 88576
      产出：`~/Library/LaunchAgents/com.eric.ximalayachrome.plist`
      依赖：T1

- [x] T6 新建 Ximalaya 专用 cdp-proxy 实例
      验收（实测通过，2026-08-21）：新 plist `com.eric.ximalayaproxy`，
      `CHROME_DEBUG_PORT=9433`，`CDP_PROXY_PORT=3457`；`curl -s localhost:3457/health`
      返回 `{"status":"ok","connected":true,"sessions":0,"managedTabs":0,"chromePort":9433}`；
      `curl -s localhost:3457/targets` 正确列出 9433 上的真实标签页；PID 88607
      （注：`/json/list` 不是这个代理的端点，用 `/health`、`/targets` 验证）
      产出：`~/Library/LaunchAgents/com.eric.ximalayaproxy.plist`
      依赖：T5

- [x] T7 让 Ximalaya 代码走新代理端口
      验收（实测通过，2026-08-21）：`config.py:191` `proxy = _s("CDP_PROXY", "http://localhost:3456")`，
      `_s()` 优先读 `os.environ`；`_load_env()` 用 `os.environ.setdefault()` 解析
      `config.env`，已存在的环境变量不会被文件值覆盖——所以在 launchd 层注入
      `CDP_PROXY` 既生效又不需要碰 `config.py` 里的硬编码默认值。
      已在 `com.eric.ximalaya.daily.plist` 加入
      `EnvironmentVariables → CDP_PROXY=http://localhost:3457`；
      `plutil -lint` 通过；`launchctl bootout` + `bootstrap` 重载成功（exit 0），
      `launchctl list` 显示任务存在、PID 为 `-`（无 RunAtLoad，重载未触发真实运行，安全）；
      手动 `python3 -c` 验证：设置该环境变量后 `Config.proxy` 解析结果确为
      `http://localhost:3457`；grep 确认 `cfg.proxy` 真被消费而非仅定义——
      `cli.py:931`（状态行）、`publish.py:114/176/223/698`（真实 HTTP 请求 + 未就绪报错）
      产出：`~/Library/LaunchAgents/com.eric.ximalaya.daily.plist`（新增 EnvironmentVariables）
      依赖：T6

- [x] T8【暂停点·需用户扫码】Ximalaya Chromium 里登录喜马拉雅创作者后台
      验收（实测通过，2026-08-21）：通过 `/new` + `/navigate`（走 3457 代理）在
      Ximalaya 专用 Chromium 里打开 `https://studio.ximalaya.com/upload`，
      未登录时被 302 到 `passport.ximalaya.com`；用户 APP 扫码后 `/info` 显示
      页面已跳回 `https://studio.ximalaya.com/upload`（标题"创作中心-喜马拉雅"）；
      再用项目自己的只读函数 `CDP_PROXY=http://localhost:3457 python3 -c
      "...pub.check_login()..."` 验证，返回 `ok=True`，
      `note="登录态有效（上传表单已渲染）"`——不是仅"页面能打开"，
      而是 DOM 里真的渲染出了上传表单（`check_login()` 内部判据）
      产出：无（登录态落在 `/Users/eric/.ximalaya-chromium-profile` 内）
      依赖：T7

- [x] T9 卸载/断开日常 Chrome 上的 opencli Browser Bridge 扩展
      验收（实测通过，2026-08-21）：踩过两次坑——先后试过纯删扩展目录、
      删目录+编辑 `Secure Preferences` 里的 `extensions.settings`/`protection.macs`
      注册项，两次都在 Chrome 重启后被自动重装。根因：日常 Chrome 登录了
      3 个 Google 账号且开着 Chrome 同步，扩展安装状态被同步到账号层，
      本地文件改动会被下一次同步拉回覆盖——按"同一问题连续 2 次没修好"
      的规则停下来问用户，用户选择自己在 `chrome://extensions` 里手动点
      "移除"（这个动作走真实卸载流程，能正确把状态回写同步服务器）。
      移除后验证：`opencli profile list` 只剩 XHS Chromium 的
      `b38bbmnt — connected`，日常 Chrome 的 `sb9scpbk` 从列表里完全消失
      （不是 disconnected，是不存在）；`Default/Extensions/ildkmabpimmkaediidaifkhjpohdnifk`
      目录确认不存在。
      产出：无
      依赖：T4（确认新 XHS Chromium 工作正常之后才卸载旧的）

- [ ] T10 全量回归验证
      验收：10 个 XHS launchd 任务里，涉及浏览器的（xhscollect/xhsdata/xhsfirstcomment/xhsprobe/xhspublish/xhswrite）
            全部改为指向新架构且手动跑一次成功；`com.eric.ximalaya.daily` 的 `daily --no-make` 成功跑通且走新代理
      产出：验证记录（写回本文件）
      依赖：T2-T9

- [ ] T11 更新文档
      验收：`ximalaya/CLAUDE.md` 的"直连日常 Chrome"表述更新为实际架构；
            `xhs-scraping-via-opencli` 记忆更新为 Chromium 架构
      产出：`ximalaya/CLAUDE.md`、memory 文件
      依赖：T10

## 待确认的次要检查（顺手做，不阻塞主线）

- [ ] `review_prediction.py`、`harvest_cases.py` 的浏览器依赖机制
- [ ] `xhs-auto` / `case-entry`（除 harvest_cases.py） / `wake-claude` 目录是否有额外浏览器依赖

## 巡检发现（2026-08-23）

- [x] **发现**：`运行日志.csv` 08-23 当天记了 5 轮词收集（run1-run5），但
      `launchd-com.eric.xhscollect.log` 当天只真实跑了 2 次（09:00/15:00，
      对应格式简洁的 run3/run5）。其余 run1/run2/run4 备注里写着
      "小红书网页版本轮实测（Claude in Chrome 打开 search_result…）"——
      是某次交互式会话手动用 Claude in Chrome 探测登录态，不是 opencli，
      也不在 `daily_collect.py` 代码路径里（grep 确认脚本里没这段逻辑）。
      Claude in Chrome 附着的不是 XHS 专用 Chromium，登录态因此一直误判为
      "未登录"，全程只是读操作（词收集），未涉及发布/评论等写操作，无账号风险。
      **修复**：`CLAUDE.md`「联网抓取规则」已补一条——明确禁用 Claude in Chrome，
      且不区分自动化脚本还是交互式会话手动探测，一律先走 opencli；opencli
      没有对应命令时才允许手动操作浏览器，且必须是 XHS 专用 Chromium（9333）。
      产出：`CLAUDE.md` 联网抓取规则段落
      依赖：无

## 巡检发现（2026-08-26）

- [x] **发现**：`com.eric.cdpproxy`（本迁移里给 XHS 专用代理，T6 的姊妹任务，
      虽然没单独立编号但同一批建的）自 2026-08-25 起持续退出码 1，`发布任务`
      当晚直接失败（"CDP 代理不可用...HTTP Error 400: Bad Request"）。
      根因两层，都是插件侧的架构漂移，不是配置手误：
      1. `web-access` skill 已从 `~/.claude/skills/web-access/` 迁移成插件
         （`~/.claude/plugins/cache/web-access/web-access/2.5.2/`），plist 里
         `ProgramArguments` 还指着旧路径，`MODULE_NOT_FOUND`。
      2. 更关键：现在装的这版 `cdp-proxy.mjs` **已经不读 `CHROME_DEBUG_PORT`**
         了——本文件第 6 行"已确认"的那句话（08-21 当天用 T6 实测过）在某次插件
         更新后失效，改成了读共享 `config.env` 的 `WEB_ACCESS_BROWSER` 偏好（当前
         值 `chrome`，指日常 Chrome/9222），且新的自动发现逻辑只认标准安装路径
         下的浏览器，压根找不到咱们 `--user-data-dir=/Users/eric/.xhs-chromium-profile`
         这种自定义路径的实例。**如果只改 plist 路径就重启，代理会连上日常 Chrome
         而不是 XHS 专用 Chromium**——正是联网抓取规则那条铁律要防的事，且这次是
         真实发布写操作，不是被动读登录态。
      3. 另外发现一个独立但同源的问题：`web-access` v2.5.3 起 `/new`、`/navigate`
         两个端点改成必须用 POST body 传 URL，GET `?url=` 写法被硬拒 400（插件自带
         `references/migration-2.5.3.md` 有迁移说明和原因）。这个问题**跟代理连哪个
         浏览器无关**，是另一层 API 契约破坏，influences 全部走这两个端点的脚本。
      **修复**：
      - 在插件的 `cdp-proxy.mjs` 里加回 `CHROME_DEBUG_PORT` 直连分支（设了就跳过
        发现逻辑，直接连固定端口 + 自己取 `/json/version` 的 `webSocketDebuggerUrl`），
        不碰 `config.env`（不影响日常 web-access 用途）；这是插件 cache 目录里的文件，
        **插件下次自动更新可能会覆盖掉这个补丁**，如果 `cdpproxy` 又开始退出码 1，
        先检查这个分支是否还在。
      - 修正 `com.eric.cdpproxy.plist` 的 `ProgramArguments` 路径。
      - 把仓库内所有 `/new?url=`、`/navigate?...&url=` 旧写法改成 POST body 传 URL，
        涉及 `scripts/case-entry/case_entry.py`、`scripts/xhs-probe/probe.py`、
        `scripts/xhs-comment/{show_login,draft_comments,outreach}.py`、
        `scripts/xhs-publish/{fetch_stats,fix_topics}.py`，共 7 个文件、约 15 处调用。
      验收：`curl localhost:3456/health` 返回 `"chromePort":9333`；手动重跑
      `auto_publish.py --only 成稿_2026-08-19_述职一句话写出价值.md --full-auto`
      全自动完成，定时发布 2026-08-27 18:00，`发布日志.csv` 已回填。
      产出：插件补丁（不在仓库内，需留意插件更新风险）、`com.eric.cdpproxy.plist`、
      仓库内 7 个脚本文件
      依赖：无
