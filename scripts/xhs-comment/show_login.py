#!/usr/bin/env python3
"""让 cdp 那个 Chrome 自己举手 —— 登录态掉了的时候用。

## 为什么需要这个脚本

机器上有**两个长得一模一样的 Chrome**：日常那个（opencli 走扩展连它），和
cdp profile（`~/.xhs-chrome-profile`，用 `--remote-debugging-port=9333` 独立启动，
发评论、抓通知走它）。窗口外观没有任何区别，Eric 的原话是「chrome 的窗口都长一样的，
我怎么知道是哪个」—— 让人肉眼去分辨本身就是个设计缺陷。

所以由脚本来指认：按 PID 把那个 Chrome 提到最前 → 激活登录标签页 → 在页面顶部
贴一条红色横幅 → 顺便改标题。三重信号，认错不了。

## ⛔ 同一账号的网页会话是互斥的

2026-08-19 实测：在 cdp 里扫码登录后，日常 Chrome **当场被踢下线**（主站和
创作者中心一起掉）。反过来也一样。所以：

- 不要指望两个浏览器同时在线，任何「A 浏览器拿数据 + B 浏览器执行」的设计都会卡死
- 登录哪个，取决于接下来要跑什么：发评论/抓通知 → cdp；opencli 采集 → 日常 Chrome

用法：
    python3 scripts/xhs-comment/show_login.py           # 检查登录态，掉了就举手并等
    python3 scripts/xhs-comment/show_login.py --check   # 只检查，不开窗口
"""
import argparse
import json
import re
import subprocess
import sys
import time
import urllib.parse as up
import urllib.request as rq

PROXY = "http://localhost:3456"
DEVTOOLS = "http://127.0.0.1:9333"
PROFILE = "/Users/eric/.xhs-chrome-profile"
LOGIN_URL = "https://www.xiaohongshu.com/login"
PROBE_URL = "https://www.xiaohongshu.com/user/profile/64cc5138000000002b009107"


def cdp(path, data=None, timeout=60):
    req = rq.Request(PROXY + path, data=data.encode("utf-8") if data else None,
                     method="POST" if data else "GET")
    return json.loads(rq.urlopen(req, timeout=timeout).read())


def ev(tid, js):
    return cdp(f"/eval?target={tid}", js).get("value")


def logged_in():
    """真的打开一次主页来判断，不看 cookie。

    ⛔ 别用「首页能不能打开」当判据：实测登录态部分失效时 `/explore` 首页照常打开、
    需要登录的页面才跳 `/login`。判据要用真正要访问的那类页面。
    """
    tid = cdp("/new?url=" + up.quote(PROBE_URL, safe=":/?&=%"))["targetId"]
    try:
        time.sleep(10)
        url = ev(tid, "location.href") or ""
        return "/login" not in url
    finally:
        try:
            cdp("/close?target=" + tid)
        except Exception:                                   # noqa: BLE001
            pass


def chrome_pid():
    """找 cdp profile 那个 Chrome 的主进程 PID（不是 renderer/gpu 子进程）。"""
    out = subprocess.run(["ps", "-axo", "pid=,command="],
                         capture_output=True, text=True).stdout
    for line in out.splitlines():
        if f"--user-data-dir={PROFILE}" in line and "Google Chrome.app/Contents/MacOS" in line \
                and "--type=" not in line:
            m = re.match(r"\s*(\d+)", line)
            if m:
                return int(m.group(1))
    return None


BANNER_JS = ('(function(){var o=document.getElementById("__xhs_banner__");if(o)o.remove();'
             'var d=document.createElement("div");d.id="__xhs_banner__";'
             'd.style.cssText="position:fixed;top:0;left:0;right:0;z-index:2147483647;'
             'background:#e60023;color:#fff;font-size:24px;font-weight:900;padding:16px;'
             'text-align:center;font-family:-apple-system,sans-serif;";'
             'd.textContent="就是这个 Chrome —— 请在本窗口扫码登录小红书";'
             'document.body.appendChild(d);'
             'document.title="就是这个窗口 在这里扫码";return "ok";})()')


def raise_hand():
    """开登录页 → 把窗口提到最前 → 激活标签页 → 贴横幅。返回 targetId。"""
    tid = cdp("/new?url=" + up.quote(LOGIN_URL, safe=":/?&=%"))["targetId"]
    time.sleep(6)
    pid = chrome_pid()
    if pid:
        subprocess.run(["osascript", "-e",
                        f"tell application \"System Events\" to set frontmost of "
                        f"(first process whose unix id is {pid}) to true"],
                       capture_output=True)
        print(f"  · 已把 Chrome（PID {pid}）提到最前")
    else:
        print("  ⚠️ 没找到 cdp Chrome 的主进程，只能靠横幅认")
    # ⛔ /new 开的是**后台** tab，不激活的话页面不渲染，二维码根本不出现。
    try:
        rq.urlopen(f"{DEVTOOLS}/json/activate/{tid}", timeout=10).read()
        print("  · 已把登录标签页切到前台")
    except Exception as e:                                  # noqa: BLE001
        print(f"  ⚠️ 标签页激活失败（{e}），可能要手动点一下那个标签")
    ev(tid, BANNER_JS)
    print("  · 页面顶部已贴红色横幅")
    return tid


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="只检查登录态，不开窗口")
    ap.add_argument("--wait", type=int, default=20, help="举手后最多等几分钟")
    a = ap.parse_args()

    if logged_in():
        print("✅ cdp profile 已登录，不用扫码")
        return 0
    print("⛔ cdp profile 的登录态掉了")
    if a.check:
        return 1

    tid = raise_hand()
    print(f"\n👉 最前面那个顶部有红条的 Chrome 就是，请扫码（最多等 {a.wait} 分钟）")
    deadline = time.time() + a.wait * 60
    while time.time() < deadline:
        time.sleep(15)
        try:
            url = cdp(f"/info?target={tid}").get("url", "")
        except Exception:                                   # noqa: BLE001
            print("登录标签页被关了 —— 重新跑一次确认登录态")
            return 1
        if "/login" not in url:
            print(f"✅ 登录成功（页面已跳到 {url[:50]}）")
            return 0
    print("⏰ 等超时了，还在登录页")
    return 1


if __name__ == "__main__":
    sys.exit(main())
