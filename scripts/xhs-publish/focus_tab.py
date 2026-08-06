#!/usr/bin/env python3
"""把某个 CDP tab 切到 Chrome 前台。

为什么需要它：web-access 的 cdp-proxy 用 `Target.createTarget({background: true})` 建 tab，
预填完的小红书发布页永远停在后台——而这条流程的最后一步（选时段、点定时发布）必须由人点。
2026-08-05 实测：预填成功、日志写着「已打开创作平台」，Eric 那边根本没看见页面，
过一会儿 tab 还消失了。人找不到那个 tab，半自动发布就等于没发布。

为什么不走常规办法：
  · AppleScript 控制 Chrome 需要「自动化」权限，Claude Code 的 shell 没有，
    `count of windows` 静默返回 0，不报错，最容易被误判成「Chrome 没开」。
  · Chrome 150 的 CDP HTTP 发现端点（/json/list、/json/version）一律 404，
    curl 拿不到 targetId，也拿不到浏览器级 WebSocket 地址。
  · 共享代理 cdp-proxy.mjs 没有 activate 端点，而它是全局 skill，
    为一个项目去改它、还得重启，影响面太大。

所以走第四条路：Chrome 把浏览器级 WebSocket 路径写在 user-data-dir 的
DevToolsActivePort 文件里（第 1 行端口、第 2 行路径），直接连它发 Target.activateTarget。
不依赖任何权限、不碰共享代理。

用法：
  python3 focus_tab.py --url-contains creator.xiaohongshu.com
  python3 focus_tab.py --target A0192D3BA52A
"""
import argparse
import base64
import json
import os
import socket
import struct
import sys
from pathlib import Path

PORT_FILE = (Path.home() / "Library/Application Support/Google/Chrome/DevToolsActivePort")


def browser_ws():
    if not PORT_FILE.exists():
        raise SystemExit(f"⛔ 找不到 {PORT_FILE}（Chrome 没开，或用的不是默认 profile）")
    lines = PORT_FILE.read_text().strip().splitlines()
    if len(lines) < 2:
        raise SystemExit(f"⛔ {PORT_FILE} 格式异常：{lines}")
    return int(lines[0].strip()), lines[1].strip()


class MiniWS:
    """够用就好的 WebSocket 客户端（RFC 6455 的一个子集）。

    不用 websockets 库：homebrew python 受 PEP 668 保护装不了包，而这个脚本
    将来要被 launchd 调，不该背一个需要 --break-system-packages 才能装上的依赖。
    这里只需要「发一条 JSON、读到对应 id 的回复」，用不上分片、压缩、ping/pong 之外的东西。
    """

    def __init__(self, port, path, timeout=15):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(
            f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n\r\n".encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise SystemExit("⛔ CDP 握手失败：连接被关闭")
            buf += chunk
        if b"101" not in buf.split(b"\r\n")[0]:
            raise SystemExit(f"⛔ CDP 握手失败：{buf.split(chr(13).encode())[0][:80]}")
        self.rest = buf.split(b"\r\n\r\n", 1)[1]

    def _recv(self, n):
        while len(self.rest) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise SystemExit("⛔ CDP 连接中断")
            self.rest += chunk
        out, self.rest = self.rest[:n], self.rest[n:]
        return out

    def send(self, text):
        data = text.encode()
        n = len(data)
        head = b"\x81"
        if n < 126:
            head += struct.pack("!B", 0x80 | n)
        elif n < 65536:
            head += struct.pack("!BH", 0x80 | 126, n)
        else:
            head += struct.pack("!BQ", 0x80 | 127, n)
        mask = os.urandom(4)
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv(self):
        b0, b1 = self._recv(2)
        n = b1 & 0x7F
        if n == 126:
            n = struct.unpack("!H", self._recv(2))[0]
        elif n == 127:
            n = struct.unpack("!Q", self._recv(8))[0]
        payload = self._recv(n)
        if b1 & 0x80:                      # 服务端本不该 mask，容错处理
            m = payload[:4]
            payload = bytes(c ^ m[i % 4] for i, c in enumerate(payload[4:]))
        return payload.decode("utf-8", "replace")

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", help="targetId（32 位十六进制）")
    ap.add_argument("--url-contains", help="按 URL 子串找 tab")
    ap.add_argument("--list", action="store_true", help="只列出所有 page 类型的 tab")
    args = ap.parse_args()

    port, path = browser_ws()
    ws = MiniWS(port, path)
    try:
        def send(method, params=None, _id=[0]):
            _id[0] += 1
            ws.send(json.dumps({"id": _id[0], "method": method, "params": params or {}}))
            while True:
                msg = json.loads(ws.recv())
                if msg.get("id") == _id[0]:
                    return msg

        pages = [t for t in send("Target.getTargets")["result"]["targetInfos"]
                 if t.get("type") == "page"]
        if args.list:
            for t in pages:
                print(f"{t['targetId'][:12]} | {(t.get('title') or '')[:34]:<34} | {t.get('url','')[:70]}")
            return 0

        tid = args.target
        if not tid:
            if not args.url_contains:
                raise SystemExit("⛔ 需要 --target 或 --url-contains")
            hit = [t for t in pages if args.url_contains in (t.get("url") or "")]
            if not hit:
                raise SystemExit(f"⛔ 没有 URL 含「{args.url_contains}」的 tab")
            # 多个就挑最后建的：预填流程里新开的那个总是最新的
            tid = hit[-1]["targetId"]
        send("Target.activateTarget", {"targetId": tid})
        t = next((x for x in pages if x["targetId"] == tid), {})
        print(f"✅ 已切到前台：{(t.get('title') or tid)[:50]}")
        print(f"   {t.get('url','')[:90]}")
    finally:
        ws.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
