"""Launch and verify an isolated Chromium profile for one selected pipeline."""
from __future__ import annotations

import json
import os
import plistlib
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from .runtime import PipelineContext, active_context
from .cdp_bridge import PipelineCDP


class BrowserSetupError(RuntimeError):
    pass


def _executable(context: PipelineContext) -> str:
    configured = context.publishing["browser"]["executable"]
    candidates = []
    if configured != "auto":
        candidates.append(configured)
    # Some macOS installations keep a lightweight app stub in /Applications
    # while the real browser bundle lives on another volume. Resolve Chromium
    # or Chrome through the stub's real_app_path.txt before checking the
    # conventional executable locations below.
    app_bundles = [
        Path("/Applications/Chromium.app"),
        Path.home() / ".chromium-org" / "Chromium.app",
        Path("/Applications/Google Chrome.app"),
    ]
    for bundle in app_bundles:
        real_path_file = bundle / "Contents" / "Resources" / "real_app_path.txt"
        try:
            real_bundle = Path(real_path_file.read_text(encoding="utf-8").strip()).expanduser()
        except (OSError, UnicodeError):
            continue
        try:
            info = plistlib.loads((real_bundle / "Contents" / "Info.plist").read_bytes())
            executable_name = info.get("CFBundleExecutable")
        except (OSError, plistlib.InvalidFileException):
            executable_name = None
        if isinstance(executable_name, str) and executable_name:
            candidates.append(str(real_bundle / "Contents" / "MacOS" / executable_name))
        candidates.extend([
            str(real_bundle / "Contents" / "MacOS" / "Chromium"),
            str(real_bundle / "Contents" / "MacOS" / "Google Chrome"),
        ])
    candidates.extend([
        shutil.which("chromium") or "",
        shutil.which("Chromium") or "",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Users/eric/.chromium-org/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ])
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate).resolve())
    raise BrowserSetupError(
        "找不到 Chromium。请在 pipeline 配置的 publishing.browser.executable 中填写可执行文件路径。"
    )


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def _profile_lock_pid(profile: Path) -> int | None:
    """Read Chromium's SingletonLock PID without inspecting process arguments."""
    try:
        lock_target = os.readlink(profile / "SingletonLock")
    except OSError:
        return None
    match = re.search(r"-(\d+)$", lock_target)
    return int(match.group(1)) if match else None


def _port_listener_pids(port: int) -> set[int]:
    """Return local listener PIDs using lsof, which works when `ps` is restricted."""
    try:
        result = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-F", "p"],
            capture_output=True, text=True, timeout=3, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return set()
    pids: set[int] = set()
    for line in result.stdout.splitlines():
        if line.startswith("p") and line[1:].isdigit():
            pids.add(int(line[1:]))
    return pids


def _cdp_endpoint_is_chromium(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1) as response:
            version = json.loads(response.read())
    except Exception:
        return False
    browser = str(version.get("Browser", ""))
    return browser.startswith(("Chrome/", "Chromium/")) and bool(version.get("webSocketDebuggerUrl"))


def _existing_browser_matches(profile: Path, port: int) -> bool:
    """Confirm the configured profile owns the CDP listener.

    Chromium's per-profile SingletonLock contains its PID. Matching that PID to
    the process listening on the configured CDP port avoids relying only on
    `ps`, which is unavailable in some managed macOS environments. When
    Chromium's DevToolsActivePort marker is present, it is the direct profile
    ownership signal and must match the configured port.
    """
    marker = profile / "DevToolsActivePort"
    try:
        marker_port = int(marker.read_text(encoding="utf-8").splitlines()[0])
    except (OSError, ValueError, IndexError):
        marker_port = None
    if marker_port is not None:
        return marker_port == port and _cdp_endpoint_is_chromium(port)

    lock_pid = _profile_lock_pid(profile)
    listener_pids = _port_listener_pids(port)
    if lock_pid is not None and lock_pid in listener_pids:
        return _cdp_endpoint_is_chromium(port)

    # Keep a fallback for systems where lsof is unavailable but process-list
    # inspection is permitted. Never accept an occupied port without a match.
    try:
        result = subprocess.run(
            ["ps", "-axo", "command="], capture_output=True, text=True, timeout=3
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    token = f"--user-data-dir={profile}"
    return any(token in line and f"--remote-debugging-port={port}" in line
               for line in result.stdout.splitlines()) and _cdp_endpoint_is_chromium(port)


def _start_bridge(context: PipelineContext, log_dir: Path) -> None:
    url = f"http://127.0.0.1:{context.proxy_port}/health"
    try:
        with urllib.request.urlopen(url, timeout=1) as response:
            health = json.loads(response.read())
        if health.get("pipelineId") == context.pipeline_id and health.get("chromePort") == context.cdp_port:
            return
        raise BrowserSetupError(f"代理端口 {context.proxy_port} 已被其他流水线占用")
    except BrowserSetupError:
        raise
    except Exception:
        if _port_open(context.proxy_port):
            raise BrowserSetupError(f"代理端口 {context.proxy_port} 已被非本流水线进程占用")

    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "cdp-bridge.log"
    log = log_path.open("a", encoding="utf-8")
    env = os.environ.copy()
    env["XHS_PIPELINE_ID"] = context.pipeline_id
    scripts_dir = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (scripts_dir, env.get("PYTHONPATH", "")) if part
    )
    command = [sys.executable, "-m", "xhs_pipeline.cdp_bridge", "--pipeline", context.pipeline_id]
    subprocess.Popen(command, cwd=str(Path(__file__).resolve().parents[2]), env=env,
                     stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                     start_new_session=True)
    for _ in range(40):
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                health = json.loads(response.read())
            if health.get("pipelineId") == context.pipeline_id:
                return
        except Exception:
            time.sleep(0.25)
    raise BrowserSetupError(f"CDP bridge 未能在端口 {context.proxy_port} 启动；查看 {log_path}")


def start(*, open_creator: bool = True) -> PipelineContext:
    """Start the selected Chromium profile and its private bridge, then open login."""
    context = active_context(required=True)
    profile = context.browser_profile
    profile.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        profile.chmod(0o700)
    except OSError:
        pass
    log_dir = context.data_root / "logs"

    if _port_open(context.cdp_port):
        if not _existing_browser_matches(profile, context.cdp_port):
            raise BrowserSetupError(
                f"CDP 端口 {context.cdp_port} 已被占用，且无法确认它属于 {context.pipeline_id} 的 profile；停止启动。"
            )
    else:
        executable = _executable(context)
        command = [
            executable,
            f"--remote-debugging-port={context.cdp_port}",
            f"--user-data-dir={profile}",
            f"--remote-allow-origins=http://127.0.0.1:{context.cdp_port}",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        log_dir.mkdir(parents=True, exist_ok=True)
        log = (log_dir / "chromium.log").open("a", encoding="utf-8")
        subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                         start_new_session=True)
        cdp_url = f"http://127.0.0.1:{context.cdp_port}/json/version"
        for _ in range(80):
            try:
                with urllib.request.urlopen(cdp_url, timeout=1) as response:
                    if json.loads(response.read()).get("webSocketDebuggerUrl"):
                        break
            except Exception:
                time.sleep(0.25)
        else:
            raise BrowserSetupError(f"Chromium 未能在 CDP 端口 {context.cdp_port} 就绪")

    _start_bridge(context, log_dir)
    if open_creator:
        _open_creator(context)
    return context


def _open_creator(context: PipelineContext) -> None:
    """Reuse and foreground the creator page in this pipeline's Chromium."""
    cdp = PipelineCDP(context.cdp_port)
    creator_url = "https://creator.xiaohongshu.com/new/home"
    targets = [target for target in cdp.targets()
               if target.get("type") == "page"
               and str(target.get("url", "")).startswith("https://creator.xiaohongshu.com/")]
    if targets:
        target_id = targets[0].get("targetId")
    else:
        target_id = cdp.browser_command("Target.createTarget", {
            "url": creator_url,
            "background": False,
        }).get("targetId")
    if not target_id:
        raise BrowserSetupError("小红书创作中心页面创建失败")
    cdp.browser_command("Target.activateTarget", {"targetId": target_id})


if __name__ == "__main__":
    try:
        ctx = start()
    except Exception as exc:
        raise SystemExit(f"⛔ {exc}") from exc
    print(f"✅ {ctx.pipeline_id} Chromium 已就绪；请在打开的窗口扫码登录账号 {ctx.account_id}。")
