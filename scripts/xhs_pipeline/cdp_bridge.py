"""Small per-pipeline loopback bridge for the existing XHS automation scripts.

It implements only the endpoints used by this repository. Each bridge listens
on the proxy port from the selected pipeline config and talks only to that
pipeline's Chromium CDP port.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import struct
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .runtime import active_context


class CDPError(RuntimeError):
    pass


class CDPWebSocket:
    def __init__(self, url: str, timeout: float = 20):
        parsed = urlsplit(url)
        if parsed.scheme != "ws" or not parsed.hostname or not parsed.port:
            raise CDPError(f"Unsupported CDP WebSocket URL: {url!r}")
        self.sock = socket.create_connection((parsed.hostname, parsed.port), timeout=timeout)
        self.sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        self.sock.sendall(
            f"GET {path} HTTP/1.1\r\nHost: {parsed.hostname}:{parsed.port}\r\n"
            f"Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n".encode("ascii")
        )
        response = self._read_until(b"\r\n\r\n")
        if b" 101 " not in response.split(b"\r\n", 1)[0]:
            raise CDPError(f"CDP WebSocket handshake failed: {response[:180]!r}")
        self.pending = response.split(b"\r\n\r\n", 1)[1]
        self.next_id = 0

    def _read_until(self, marker: bytes) -> bytes:
        buf = getattr(self, "pending", b"")
        while marker not in buf:
            part = self.sock.recv(4096)
            if not part:
                raise CDPError("CDP WebSocket closed during handshake")
            buf += part
        head, rest = buf.split(marker, 1)
        self.pending = rest
        return head + marker

    def _read_exactly(self, size: int) -> bytes:
        data = getattr(self, "pending", b"")
        while len(data) < size:
            part = self.sock.recv(max(4096, size - len(data)))
            if not part:
                raise CDPError("CDP WebSocket closed")
            data += part
        out, self.pending = data[:size], data[size:]
        return out

    def _send(self, payload: bytes) -> None:
        size = len(payload)
        header = bytearray([0x81])
        if size < 126:
            header.append(0x80 | size)
        elif size < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", size))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", size))
        mask = os.urandom(4)
        self.sock.sendall(bytes(header) + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def _recv_text(self) -> str:
        while True:
            b0, b1 = self._read_exactly(2)
            opcode = b0 & 0x0F
            length = b1 & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._read_exactly(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._read_exactly(8))[0]
            payload = self._read_exactly(length)
            if b1 & 0x80:
                mask, payload = payload[:4], payload[4:]
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 0x8:
                raise CDPError("CDP WebSocket sent a close frame")
            if opcode == 0x1:
                return payload.decode("utf-8", "replace")

    def command(self, method: str, params: dict | None = None, session_id: str | None = None) -> dict:
        self.next_id += 1
        request = {"id": self.next_id, "method": method, "params": params or {}}
        if session_id:
            request["sessionId"] = session_id
        self._send(json.dumps(request).encode("utf-8"))
        while True:
            message = json.loads(self._recv_text())
            if message.get("id") != self.next_id:
                continue
            if "error" in message:
                raise CDPError(message["error"].get("message", "CDP command failed"))
            return message.get("result", {})

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


class PipelineCDP:
    def __init__(self, cdp_port: int):
        self.cdp_port = cdp_port

    def _browser_socket_url(self) -> str:
        url = f"http://127.0.0.1:{self.cdp_port}/json/version"
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                payload = json.loads(response.read())
        except Exception as exc:
            raise CDPError(f"Chromium CDP port {self.cdp_port} is unavailable: {exc}") from exc
        ws_url = payload.get("webSocketDebuggerUrl")
        if not ws_url:
            raise CDPError("Chromium did not provide webSocketDebuggerUrl")
        return ws_url

    def browser_command(self, method: str, params: dict | None = None) -> dict:
        ws = CDPWebSocket(self._browser_socket_url())
        try:
            return ws.command(method, params)
        finally:
            ws.close()

    def with_target(self, target_id: str, action):
        ws = CDPWebSocket(self._browser_socket_url())
        session_id = None
        try:
            attached = ws.command("Target.attachToTarget", {"targetId": target_id, "flatten": True})
            session_id = attached["sessionId"]
            return action(ws, session_id)
        finally:
            if session_id:
                try:
                    ws.command("Target.detachFromTarget", {"sessionId": session_id})
                except Exception:
                    pass
            ws.close()

    def targets(self) -> list[dict]:
        return self.browser_command("Target.getTargets").get("targetInfos", [])

    def evaluate(self, target_id: str, expression: str) -> dict:
        def action(ws, session_id):
            return ws.command("Runtime.evaluate", {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
                "userGesture": True,
            }, session_id=session_id)
        result = self.with_target(target_id, action)
        if result.get("exceptionDetails"):
            detail = result["exceptionDetails"].get("text", "JavaScript evaluation failed")
            return {"error": detail}
        return {"value": result.get("result", {}).get("value")}

    def click(self, target_id: str, selector: str) -> dict:
        box = self.evaluate(target_id, f"""(()=>{{
          const e=document.querySelector({json.dumps(selector)});
          if(!e) return {{error:'selector not found'}};
          const r=e.getBoundingClientRect();
          return {{x:r.left+r.width/2,y:r.top+r.height/2}};
        }})()""")
        point = box.get("value")
        if not isinstance(point, dict) or "x" not in point:
            return {"ok": False, "error": (point or {}).get("error", "element is not visible")}
        def action(ws, session_id):
            ws.command("Input.dispatchMouseEvent", {
                "type": "mouseMoved", "x": point["x"], "y": point["y"],
            }, session_id=session_id)
            ws.command("Input.dispatchMouseEvent", {
                "type": "mousePressed", "x": point["x"], "y": point["y"],
                "button": "left", "buttons": 1, "clickCount": 1,
            }, session_id=session_id)
            return ws.command("Input.dispatchMouseEvent", {
                "type": "mouseReleased", "x": point["x"], "y": point["y"],
                "button": "left", "buttons": 0, "clickCount": 1,
            }, session_id=session_id)
        self.with_target(target_id, action)
        return {"ok": True}

    def set_files(self, target_id: str, selector: str, files: list[str]) -> dict:
        """Set local files on a file input in the selected page target."""
        def action(ws, session_id):
            ws.command("DOM.enable", {}, session_id=session_id)
            root = ws.command("DOM.getDocument", {"depth": 0}, session_id=session_id)["root"]["nodeId"]
            found = ws.command("DOM.querySelector", {
                "nodeId": root, "selector": selector,
            }, session_id=session_id)
            node_id = found.get("nodeId")
            if not node_id:
                raise CDPError(f"File input not found: {selector}")
            ws.command("DOM.setFileInputFiles", {
                "nodeId": node_id, "files": files,
            }, session_id=session_id)
            return {"ok": True, "files": len(files)}
        return self.with_target(target_id, action)


def serve(pipeline_id: str) -> None:
    context = active_context(required=True)
    if context.pipeline_id != pipeline_id:
        raise SystemExit("XHS_PIPELINE_ID does not match --pipeline")
    cdp = PipelineCDP(context.cdp_port)

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, payload: dict, status: int = 200):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> str:
            length = int(self.headers.get("Content-Length", "0"))
            return self.rfile.read(length).decode("utf-8") if length else ""

        def _route(self, method: str):
            allowed_hosts = {f"127.0.0.1:{context.proxy_port}", f"localhost:{context.proxy_port}"}
            if self.headers.get("Host", "").lower() not in allowed_hosts:
                return self._reply({"error": "loopback host required"}, 403)
            origin = self.headers.get("Origin")
            allowed_origins = {
                f"http://127.0.0.1:{context.proxy_port}",
                f"http://localhost:{context.proxy_port}",
            }
            if origin and origin not in allowed_origins:
                return self._reply({"error": "cross-origin request rejected"}, 403)
            parsed = urlsplit(self.path)
            query = parse_qs(parsed.query)
            target = (query.get("target") or [""])[0]
            try:
                if method == "GET" and parsed.path == "/health":
                    return self._reply({"ok": True, "chromePort": context.cdp_port,
                                        "pipelineId": context.pipeline_id})
                if method == "GET" and parsed.path in {"/targets", "/json/list"}:
                    return self._reply({"targets": cdp.targets()})
                if method == "POST" and parsed.path == "/new":
                    foreground = self.headers.get("X-XHS-Foreground", "").lower() == "true"
                    result = cdp.browser_command("Target.createTarget", {
                        "url": self._body(), "background": not foreground,
                    })
                    return self._reply(result)
                if method == "POST" and parsed.path == "/eval" and target:
                    return self._reply(cdp.evaluate(target, self._body()))
                if method == "POST" and parsed.path == "/navigate" and target:
                    url = self._body()
                    return self._reply(cdp.with_target(
                        target, lambda ws, sid: ws.command("Page.navigate", {"url": url}, session_id=sid)))
                if method == "POST" and parsed.path == "/click" and target:
                    return self._reply(cdp.click(target, self._body()))
                if method == "GET" and parsed.path == "/close" and target:
                    return self._reply(cdp.browser_command("Target.closeTarget", {"targetId": target}))
                return self._reply({"error": "unsupported bridge endpoint"}, 404)
            except Exception as exc:
                return self._reply({"error": str(exc)}, 502)

        def do_GET(self):
            self._route("GET")

        def do_POST(self):
            self._route("POST")

        def log_message(self, fmt, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", context.proxy_port), Handler)
    print(f"CDP bridge {context.pipeline_id}: 127.0.0.1:{context.proxy_port} -> {context.cdp_port}", flush=True)
    server.serve_forever()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pipeline", required=True)
    args = parser.parse_args()
    serve(args.pipeline)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
