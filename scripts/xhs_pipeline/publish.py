"""Prepare and schedule a reviewed Postory XHS graphic note package."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .cdp_bridge import PipelineCDP
from .approvals import require_approval
from .runtime import PipelineContext


REPO = Path(__file__).resolve().parents[2]
CREATOR_IMAGE_URL = "https://creator.xiaohongshu.com/publish/publish?from=menu&target=image"


def _wait_target(cdp: PipelineCDP, target_id: str, timeout: int = 35) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        target = next((item for item in cdp.targets()
                       if item.get("targetId") == target_id), None)
        if target and target.get("type") == "page":
            return target
        time.sleep(0.5)
    raise RuntimeError("小红书图文编辑页没有在规定时间内打开")


def _page_text(cdp: PipelineCDP, target_id: str) -> str:
    result = cdp.evaluate(target_id, "document.body?.innerText || ''")
    return str(result.get("value") or "")


def _wait_account_label(cdp: PipelineCDP, target_id: str, label: str, timeout: int = 35) -> str:
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        last = _page_text(cdp, target_id)
        if label in last:
            return last
        if any(marker in last for marker in ("扫码登录", "登录后", "登录已过期")):
            raise RuntimeError("专属创作页提示需要登录；停止上传和发布")
        time.sleep(0.75)
    raise RuntimeError(f"创作页未能核验账号「{label}」；页面文本：{last[:180]}")


def _ensure_image_tab(cdp: PipelineCDP, target_id: str) -> None:
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        result = cdp.evaluate(target_id, """(()=>{
          const input=[...document.querySelectorAll('input[type=file]')]
            .find(e=>/png|jpg|jpeg/i.test(e.accept||''));
          if(input) return 'ready';
          const tabs=[...document.querySelectorAll('.header-tabs .creator-tab:not([style])')];
          const tab=tabs.find(e=>e.textContent.trim()==='上传图文');
          if(tab && !tab.classList.contains('active')) tab.click();
          return tab?'tab':'loading';
        })()""")
        state = result.get("value")
        if state == "ready":
            return
        time.sleep(0.7)
    raise RuntimeError("创作中心图文上传入口未就绪")


def _editor_state(cdp: PipelineCDP, target_id: str) -> dict:
    result = cdp.evaluate(target_id, """(()=>{
      const text=document.body?.innerText||'';
      const title=[...document.querySelectorAll('input')]
        .find(e=>(e.placeholder||'').includes('标题'));
      const editor=document.querySelector('[contenteditable=true]');
      const count=[...text.matchAll(/(?:^|\\n)(\\d+)\\/18(?:\\n|$)/g)]
        .map(m=>Number(m[1])).sort((a,b)=>b-a)[0]||0;
      return {titleReady:!!title,editorReady:!!editor,bodyLength:(editor?.innerText||'').length,
        imageCount:count,text:text.slice(0,900)};
    })()""")
    return result.get("value") or {}


def _fill_editor(cdp: PipelineCDP, target_id: str, title: str, caption: str) -> dict:
    expression = """(()=>{
      const input=[...document.querySelectorAll('input')]
        .find(e=>(e.placeholder||'').includes('标题'));
      const editor=document.querySelector('[contenteditable=true]');
      if(!input || !editor) return {ok:false,reason:'title/editor field missing'};
      const setter=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;
      setter.call(input,TITLE_VALUE);
      input.dispatchEvent(new Event('input',{bubbles:true}));
      input.dispatchEvent(new Event('change',{bubbles:true}));
      editor.focus();
      const selection=window.getSelection();
      selection.selectAllChildren(editor);
      document.execCommand('delete');
      selection.collapseToEnd();
      document.execCommand('insertText',false,CAPTION_VALUE);
      return {ok:true,title:input.value,bodyLength:(editor.innerText||'').length,
        bodyPrefix:(editor.innerText||'').slice(0,80)};
    })()"""
    expression = expression.replace("TITLE_VALUE", json.dumps(title, ensure_ascii=False))
    expression = expression.replace("CAPTION_VALUE", json.dumps(caption, ensure_ascii=False))
    return cdp.evaluate(target_id, expression).get("value") or {}


def _dismiss_editor_popovers(cdp: PipelineCDP, target_id: str) -> None:
    def action(ws, session_id):
        ws.command("Input.dispatchKeyEvent", {
            "type": "rawKeyDown", "key": "Escape", "code": "Escape",
            "windowsVirtualKeyCode": 27,
        }, session_id=session_id)
        ws.command("Input.dispatchKeyEvent", {
            "type": "keyUp", "key": "Escape", "code": "Escape",
            "windowsVirtualKeyCode": 27,
        }, session_id=session_id)
        return True
    cdp.with_target(target_id, action)
    cdp.evaluate(target_id, "document.activeElement?.blur(); true")
    time.sleep(0.5)


def _verify_scheduled_note(cdp: PipelineCDP, target_id: str, title: str, scheduled_at: str) -> str:
    result = cdp.evaluate(target_id, """(()=>{
      const item=[...document.querySelectorAll('*')]
        .find(e=>e.children.length===0 && e.textContent.trim()==='笔记管理');
      if(!item) return 'nav-missing';
      item.click(); return 'opened';
    })()""")
    if result.get("value") not in {"opened", "already"}:
        raise RuntimeError("已提交定时发布，但找不到创作中心的「笔记管理」核验入口")
    deadline = time.monotonic() + 25
    last_text = ""
    while time.monotonic() < deadline:
        targets = cdp.targets()
        manager = next((item for item in targets
                        if item.get("targetId") == target_id
                        and "/new/note-manager" in item.get("url", "")), None)
        if manager:
            last_text = _page_text(cdp, target_id)
            if title in last_text and "定时发布" in last_text and scheduled_at in last_text:
                return "创作中心笔记管理已核验标题、定时状态和北京时间"
        time.sleep(0.8)
    raise RuntimeError(
        f"提交成功页已跳转，但后台暂未核验到标题/定时状态：{title} / {scheduled_at}；"
        f"页面：{last_text[:500]}"
    )


def _schedule_helpers():
    schedule_dir = str(REPO / "scripts" / "xhs-publish")
    if schedule_dir not in sys.path:
        sys.path.insert(0, schedule_dir)
    import auto_publish  # type: ignore[import-not-found]
    return auto_publish


def publish_package(
    context: PipelineContext,
    package: Path,
    *,
    account_label: str,
    submit_scheduled: bool,
    schedule_at: str = "auto",
) -> dict:
    """Upload cards, fill copy, select the legacy rotation slot, then schedule.

    ``submit_scheduled`` is an explicit per-run authorization for the final
    public scheduling action. Account identity is verified against live page
    text using the caller-confirmed label; the shared registry is not mutated.
    """
    package = package.expanduser().resolve()
    if not package.is_dir():
        raise RuntimeError(f"笔记包目录不存在：{package}")
    note_path = package / "note.json"
    caption_path = package / "caption.txt"
    if not note_path.is_file() or not caption_path.is_file():
        raise RuntimeError("图文包必须包含 note.json 和 caption.txt")
    note = json.loads(note_path.read_text(encoding="utf-8"))
    approval = require_approval(package, note)
    title = str(note.get("selected_title") or "").strip()
    caption = caption_path.read_text(encoding="utf-8").strip()
    if not title or len(title) > 20:
        raise RuntimeError(f"小红书标题需为 1–20 字，当前 {len(title)} 字")
    if not caption:
        raise RuntimeError("caption.txt 正文为空")
    card_names = note.get("card_files") or []
    cards = [package / name for name in card_names]
    cards = [path.resolve() for path in cards]
    if not cards or any(not path.is_file() for path in cards):
        missing = [str(path) for path in cards if not path.is_file()]
        raise RuntimeError("图卡文件缺失：" + (", ".join(missing) if missing else "card_files 为空"))
    for path in cards:
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            raise RuntimeError(f"平台不支持的图卡格式：{path.name}")
        if path.stat().st_size > 32 * 1024 * 1024:
            raise RuntimeError(f"图卡超过小红书 32MB 单张限制：{path.name}")

    if not account_label.strip():
        raise RuntimeError("需提供已核验的 Postory 账号显示名")
    if context.account_id != "postory-xhs":
        raise RuntimeError(f"账号标识不匹配：{context.account_id}")
    if context.publishing.get("final_submit_requires_approval") is not True:
        raise RuntimeError("配置必须保留最终发布审批闸门")
    if not submit_scheduled:
        raise RuntimeError("本次只允许预填；正式定时发布需显式传入 --submit-scheduled")

    auto = _schedule_helpers()
    ok, why = auto.ensure_cdp_proxy_healthy()
    if not ok:
        raise RuntimeError(why)
    if schedule_at == "auto":
        scheduled_at, slot_hour = auto.next_slot()
    else:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", schedule_at):
            raise RuntimeError("--at 格式应为 YYYY-MM-DD HH:MM（按原流程使用北京时间）")
        scheduled_at, slot_hour = schedule_at, int(schedule_at[11:13])
    target_time = datetime.strptime(scheduled_at, "%Y-%m-%d %H:%M").replace(
        tzinfo=ZoneInfo("Asia/Shanghai"))
    if target_time <= datetime.now(ZoneInfo("Asia/Shanghai")):
        raise RuntimeError(f"定时时间已过：{scheduled_at} Asia/Shanghai；拒绝误发成即时笔记")

    cdp = PipelineCDP(context.cdp_port)
    target_id = None
    # Resume the newest in-progress image editor if a previous run already
    # uploaded this package. This prevents duplicate card uploads on retries.
    for existing in reversed(cdp.targets()):
        if (existing.get("type") == "page"
                and existing.get("url", "").startswith("https://creator.xiaohongshu.com/publish/publish")
                and "target=image" in existing.get("url", "")):
            candidate_text = _page_text(cdp, existing["targetId"])
            candidate_state = _editor_state(cdp, existing["targetId"])
            if account_label in candidate_text and candidate_state.get("editorReady"):
                target_id = existing["targetId"]
                break
    if not target_id:
        target = cdp.browser_command("Target.createTarget", {
            "url": CREATOR_IMAGE_URL, "background": False,
        })
        target_id = target.get("targetId")
    if not target_id:
        raise RuntimeError("未能创建小红书图文编辑页")
    try:
        _wait_target(cdp, target_id)
        cdp.browser_command("Target.activateTarget", {"targetId": target_id})
        _wait_account_label(cdp, target_id, account_label)
        state = _editor_state(cdp, target_id)
        if state.get("editorReady") and state.get("titleReady"):
            if state.get("imageCount") != len(cards):
                raise RuntimeError(
                    f"已有编辑页图片数为 {state.get('imageCount')}，本包为 {len(cards)}；停止以免重复或混发"
                )
            print(f"复用已上传的图卡：{state['imageCount']} 张")
        else:
            _ensure_image_tab(cdp, target_id)
            cdp.set_files(target_id, 'input[type=file][accept*="png"]', [str(path) for path in cards])
            deadline = time.monotonic() + 75
            while time.monotonic() < deadline:
                state = _editor_state(cdp, target_id)
                if (state.get("editorReady") and state.get("titleReady")
                        and state.get("imageCount") == len(cards)):
                    break
                time.sleep(1)
            else:
                raise RuntimeError(
                    f"编辑器未能确认 {len(cards)} 张图及标题/正文输入框；当前状态：{state}"
                )

        filled = _fill_editor(cdp, target_id, title, caption)
        if not filled.get("ok") or filled.get("title") != title:
            raise RuntimeError(f"标题/正文预填校验失败：{filled}")
        if int(filled.get("bodyLength", 0)) < min(80, len(caption)):
            raise RuntimeError(f"正文预填长度异常：{filled}")
        _dismiss_editor_popovers(cdp, target_id)

        switch = auto.open_sched_switch(target_id)
        if not str(switch).startswith("已打开"):
            raise RuntimeError(f"未能确认定时发布开关已打开：{switch}")
        node = shutil.which("node") or next(
            (path for path in ("/opt/homebrew/bin/node", "/usr/local/bin/node")
             if Path(path).is_file()), None)
        if not node:
            raise RuntimeError("找不到 Node.js，无法执行原有定时选择器")
        scheduler = REPO / "scripts" / "xhs-publish" / "set_schedule.mjs"
        child_env = os.environ.copy()
        child_env["CHROME_DEBUG_PORT"] = str(context.cdp_port)
        cmd = [node, str(scheduler), "--at", scheduled_at, "--target", target_id]
        # 原定时脚本会先回读时间和检查仍在未来，再以平台自己的
        # publish 事件提交“定时发布”，并等待编辑器跳转/清空标题。
        cmd.append("--publish")
        result = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=240, env=child_env)
        output = (result.stdout or "") + (result.stderr or "")
        if result.returncode != 0:
            raise RuntimeError("定时发布未确认成功：\n" + output[-2500:])
        if "页面已跳走，判定发布成功" not in output:
            raise RuntimeError("定时脚本未返回发布成功信号：\n" + output[-2500:])
        platform_verification = _verify_scheduled_note(cdp, target_id, title, scheduled_at)

        state = {
            "status": "scheduled",
            "account_id": context.account_id,
            "account_label": account_label,
            "title": title,
            "scheduled_at": scheduled_at,
            "timezone": "Asia/Shanghai",
            "slot_hour": slot_hour,
            "cards": [path.name for path in cards],
            "submitted_at": datetime.now(ZoneInfo("UTC")).isoformat(),
            "platform_verification": platform_verification,
            "confirmation": output[-1200:],
            "approved_by": approval["approved_by"],
            "approved_at": approval["approved_at"],
        }
        (package / "publish-result.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return state
    finally:
        cdp.browser_command("Target.activateTarget", {"targetId": target_id})
