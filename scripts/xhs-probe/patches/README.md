# MediaCrawler 本机修复（2026-10-05）

已应用到 `~/.mediacrawler`，原文件备份为 `.py.before-20261005`。
升级外部工具后检查补丁是否仍需要，使用 `git apply --check <补丁绝对路径>` 再应用。

- 借用现有 CDP 浏览器时不关闭其 context 或用户页面。
- `XHS_AUTOMATED_JOB=1` 的探词/采集任务登录检查失败时退出，保留人工登录页面。
- 安全验证与登录检查失败分开报告；失败不等同于 cookie 丢失。
- 手动 MediaCrawler 仍可扫码；推荐 `python3 scripts/xhs-comment/show_login.py`，默认等 20 分钟，退出后登录页仍保留。

现场验证：原 Chromium 有 web_session；移除 opencli 搜索登录探针后，MediaCrawler 登录检查返回 True，面试搜索取得数据。尚未验证人工扫码完成全过程。
