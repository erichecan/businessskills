#!/usr/bin/env python3
"""把「已定时未发布」的笔记里的纯文本标签补成真话题。

背景：2026-08-07 之前，预填脚本等联想面板只等固定 1.3 秒，等不到就安静地
把 #xxx 留成纯文本。纯文本的 #xxx 在小红书那边完全不参与话题聚合，等于白写。
预填侧已修（case_entry.pick_topic），但**已经发出去/已排期的笔记不会自己变**，
所以有了这个补丁脚本。

只处理「定时发布」状态的笔记 —— 那批还没真正发出去，创作后台允许编辑。
已发布的笔记这里不碰。

用法：
  python3 fix_topics.py --list             # 看有哪些定时笔记、各自差几个话题
  python3 fix_topics.py --note <noteId>    # 改一篇，改完停在编辑页等你点保存
  python3 fix_topics.py --all              # 全部定时笔记，逐篇改

⛔ 最后那一下「定时发布」要人来点，脚本点不动（2026-08-07 实测）。
标签转换这一段是可靠的：0→10、6→10，每次都全中。卡住的是保存按钮 ——
它是自定义元素 <xhs-publish-btn>，shadow root 是 closed，四种点法全试过：
  1) 按文本找按钮      → 找到的是左侧栏「发布笔记」（新建笔记），点了没反应
  2) /clickAt 点宿主   → clicked:true、坐标分毫不差，页面毫无反应
  3) /hoverAt + /clickAt → 同上
  4) /realClick 完整手势（mouseMoved→mousePressed{buttons:1}→60ms→release）→ 同上
每次都以为存上了，回头一读还是原样，改动静静地丢掉。
宿主属性显示 submit-disabled=false、submit-text=定时发布，按钮是启用的、
点了也确实保留定时 —— 就是不响应程序化的点击。原因未明，别再盲试。

所以现在的分工：脚本把标签全部转成真话题，然后停住；你在浏览器里点一下
底部那个红色「定时发布」。每篇大约 3 秒。这和发布流程本来的
「最后一下留给人」是同一个形状。

--submit 保留着（万一哪天那按钮能点了），但默认不开，且只在标签全部转成
真话题、定时开关仍为 checked、时间文案未变时才会去点。
"""
import argparse
import json
import re
import sys
import time
import urllib.parse as up
import urllib.request as rq
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "case-entry"))

PROXY = "http://localhost:3456"
NOTE_MANAGER = "https://creator.xiaohongshu.com/new/note-manager"
# 编辑路由不是猜的：在笔记管理里 hover 卡片 → 点铅笔，跳的就是这个
# （2026-08-07 实测；直接拼 publish/publish?noteId= 是无效的，noteId 会被忽略）。
EDIT_URL = "https://creator.xiaohongshu.com/publish/update?id={nid}&noteType=normal"


def api(path, data=None, timeout=40):
    req = rq.Request(PROXY + path, data=data.encode() if data else None,
                     method="POST" if data else "GET")
    return json.loads(rq.urlopen(req, timeout=timeout).read())


def ev(tid, js):
    return api(f"/eval?target={tid}", js).get("value")


def scheduled_notes(tid):
    """笔记管理里状态为「定时发布」的笔记。noteId 藏在卡片的 data-impression 里。"""
    api(f"/navigate?target={tid}&url=" + up.quote(NOTE_MANAGER, safe=""))
    time.sleep(7)
    raw = ev(tid, r'''(()=>JSON.stringify([...document.querySelectorAll(".note-card")]
      .filter(c=>c.querySelector(".note-card__schedule"))
      .map(c=>({
        id:((c.getAttribute("data-impression")||"").match(/"noteId":"([a-f0-9]+)"/)||[])[1]||"",
        title:(c.querySelector(".note-card__title")?.textContent||"").trim(),
        at:(c.querySelector(".note-card__time")?.textContent||"").trim()
      })).filter(n=>n.id)))()''')
    return json.loads(raw or "[]")


def tag_state(tid):
    """当前编辑页里：已经是真话题的、和还是纯文本的分别有哪些。"""
    raw = ev(tid, r'''(()=>{const ed=document.querySelector("[contenteditable=true]");
      if(!ed)return "";
      const real=[...ed.querySelectorAll("a.tiptap-topic")]
        .map(a=>((a.getAttribute("data-topic")||"").match(/"name":"([^"]*)"/)||[])[1]).filter(Boolean);
      const c=ed.cloneNode(true); c.querySelectorAll("a.tiptap-topic").forEach(a=>a.remove());
      const plain=((c.innerText||"").match(/#[^\s#]+/g)||[]).map(s=>s.slice(1));
      return JSON.stringify({real,plain});})()''')
    d = json.loads(raw or '{"real":[],"plain":[]}')
    return d["real"], d["plain"]


def cut_plain_tag(tid, tag):
    """把正文里第一处纯文本 #tag 精确框住删掉，光标就停在原地。

    ⛔ 不能只处理「正文末尾那一串」。已经部分转换过的笔记里，纯文本标签是
    **夹在真话题中间**的（真话题是 <a> 节点，把文本切成了好几段），
    末尾那一串只是其中一段 —— 2026-08-07 第一版就栽在这，10 个里只摘到 2 个。

    ⛔ 也不能数着字符退格。用 Range 精确框住 #tag 这几个字再 delete，
    一旦前面判断错一位，退格啃掉的就是正文本身。
    """
    raw = ev(tid, f'''(()=>{{
      const ed=document.querySelector("[contenteditable=true]");
      const want="#"+{json.dumps(tag)};
      const walk=document.createTreeWalker(ed,NodeFilter.SHOW_TEXT);
      while(walk.nextNode()){{
        const n=walk.currentNode, s=n.textContent, i=s.indexOf(want);
        if(i<0)continue;
        // 必须是独立的一个标签，不能是更长标签的前缀（#职场 不该匹配到 #职场生存）
        const after=s[i+want.length];
        if(after&&!/[\\s#]/.test(after))continue;
        const r=document.createRange();
        r.setStart(n,i); r.setEnd(n,i+want.length);
        const sel=window.getSelection(); sel.removeAllRanges(); sel.addRange(r);
        ed.focus();
        document.execCommand("delete");
        return JSON.stringify({{ok:true}});
      }}
      return JSON.stringify({{ok:false,why:"正文里找不到这个纯文本标签"}});}})()''')
    d = json.loads(raw or '{"ok":false,"why":"eval 无返回"}')
    return d.get("ok", False), d.get("why", "")


def insert_at_caret(tid, text):
    """在**当前光标处**插入文本。不能用 selectAllChildren+collapseToEnd ——
    那会把标签甩到全文末尾，原地替换就变成了追加。"""
    ev(tid, '(()=>{const ed=document.querySelector("[contenteditable=true]");ed.focus();'
            f'document.execCommand("insertText",false,{json.dumps(text)});return 1}})()')


def sched_locked(tid):
    """定时开关还开着、时间还在吗。提交前的安全闸。

    提交按钮是 <xhs-publish-btn>，shadow root 是 closed，读不到它写的是
    「定时发布」还是「立即发布」。所以只能退一步：确认**决定行为的那个状态**
    —— 开关 checked + 时间文案 —— 没被我们改动过，再点。
    """
    raw = ev(tid, r'''(()=>{const w=document.querySelector(".post-time-wrapper");
      if(!w)return JSON.stringify({on:false,text:"没有定时区"});
      const sim=w.querySelector(".d-switch-simulator");
      return JSON.stringify({
        on:/(^|\s)checked(\s|$)/.test(sim?.className||""),
        text:(w.innerText||"").replace(/\s+/g," ").trim()});})()''')
    d = json.loads(raw or '{"on":false,"text":"?"}')
    return d["on"], d["text"]


def fix_one(tid, note, submit):
    from case_entry import pick_topic, undo_insert

    print(f"\n▶ {note['title']}  （{note['at']}）")
    api(f"/navigate?target={tid}&url=" + up.quote(EDIT_URL.format(nid=note["id"]), safe=""))
    for _ in range(20):
        time.sleep(1)
        if ev(tid, '(()=>{const e=document.querySelector("[contenteditable=true]");'
                   'return !!e&&e.innerText.length>50})()'):
            break
    else:
        print("  ⛔ 编辑器没加载出来，跳过")
        return False

    on0, sched0 = sched_locked(tid)
    print(f"  定时状态：{sched0}（开关 {'开' if on0 else '关'}）")
    if not on0:
        print("  ⛔ 定时开关不是开着的，不碰这篇 —— 改完提交可能变成立即发布")
        return False

    real, plain = tag_state(tid)
    print(f"  现状：真话题 {len(real)} 个，纯文本 {len(plain)} 个")
    if not plain:
        print("  ✅ 没有纯文本标签，不用改")
        return True
    print(f"  待转：{' '.join('#' + t for t in plain)}")

    todo = list(plain)
    for tag in todo:
        ok, why = cut_plain_tag(tid, tag)
        if not ok:
            print(f"    #{tag} 摘不掉（{why}），跳过")
            continue
        time.sleep(0.5)
        for attempt in (1, 2):
            insert_at_caret(tid, "#" + tag)      # 原地补回，不甩到文末
            if pick_topic(api, tid, tag):
                break
            if attempt == 1 and undo_insert(api, tid, "#" + tag):
                continue
            insert_at_caret(tid, " ")            # 转不成就落定为纯文本，至少不丢字
            break
        time.sleep(0.4)

    real2, plain2 = tag_state(tid)
    print(f"  改完：真话题 {len(real)} → {len(real2)}"
          + (f"，仍是纯文本：{' '.join('#' + t for t in plain2)}" if plain2 else "，纯文本清零"))

    if not submit:
        print("  ⏸ 标签已就位，未保存。去 Chrome 这个页面点一下底部红色「定时发布」即可。")
        print(f"     {EDIT_URL.format(nid=note['id'])}")
        return True

    if plain2:
        print("  ⛔ 还有没转成的，不提交 —— 提交一次就少一次可编辑的机会")
        return False

    on1, sched1 = sched_locked(tid)
    if not on1 or sched1 != sched0:
        print(f"  ⛔ 定时状态被改动了（改前「{sched0}」→ 改后「{sched1}」），不提交")
        return False

    # 提交按钮是 <xhs-publish-btn> 自定义元素，文字在 closed shadow root 里，
    # 按文本根本搜不到 —— 按文本搜会搜到左侧栏那个红色「发布笔记」（新建笔记），
    # 点了等于什么都没发生（2026-08-07 实测，改动白丢）。只能按宿主元素点。
    #
    # ⛔ 必须用 /realClick（完整手势：mouseMoved → mousePressed{buttons:1} → 60ms → release）。
    # /clickAt 只发 press+release、不带 buttons、中间没有延迟，这个 Web Component
    # 收到了也不认 —— 返回 clicked:true、坐标分毫不差，然而什么都不会发生，
    # 改动就这么静静地丢掉（2026-08-07 实测两次，都以为存上了，其实没有）。
    r = api(f"/realClick?target={tid}", "xhs-publish-btn")
    print(f"  点「定时发布」：{r.get('clicked')} @ ({r.get('x')}, {r.get('y')})")
    time.sleep(8)
    print(f"  提交后 URL：{api(f'/info?target={tid}').get('url', '')[:100]}")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="只列出定时笔记与标签现状")
    ap.add_argument("--note", help="只处理这一个 noteId")
    ap.add_argument("--all", action="store_true", help="处理全部定时笔记")
    ap.add_argument("--submit", action="store_true", help="改完真的点「发布笔记」")
    a = ap.parse_args()

    tid = api("/new?url=" + up.quote("about:blank", safe=""))["targetId"]
    try:
        notes = scheduled_notes(tid)
        if not notes:
            print("笔记管理里没有「定时发布」状态的笔记。")
            return 0
        print(f"定时笔记 {len(notes)} 篇：")
        for n in notes:
            print(f"  {n['id']}  {n['at']}  {n['title']}")

        if a.list:
            for n in notes:
                api(f"/navigate?target={tid}&url=" + up.quote(EDIT_URL.format(nid=n["id"]), safe=""))
                for _ in range(20):
                    time.sleep(1)
                    if ev(tid, '(()=>{const e=document.querySelector("[contenteditable=true]");'
                               'return !!e&&e.innerText.length>50})()'):
                        break
                real, plain = tag_state(tid)
                print(f"\n{n['title']}：真话题 {len(real)}，纯文本 {len(plain)} "
                      f"{' '.join('#' + t for t in plain)}")
            return 0

        targets = [n for n in notes if n["id"] == a.note] if a.note else (notes if a.all else [])
        if not targets:
            print("\n没指定要改哪篇。用 --note <id> 改一篇，或 --all 改全部。")
            return 1

        # 不提交时**每篇单开一个 tab**：最后那一下要人来点，几篇共用一个 tab
        # 的话，前面几篇会被后一篇的导航冲掉，人只剩最后一篇可点。
        ok = 0
        for n in targets:
            t = tid if a.submit else api("/new?url=" + up.quote("about:blank", safe=""))["targetId"]
            if fix_one(t, n, a.submit):
                ok += 1
        print(f"\n完成 {ok}/{len(targets)} 篇。")
        if not a.submit and ok:
            print("去 Chrome 里逐个点底部红色「定时发布」保存（每篇约 3 秒）。")
        return 0 if ok == len(targets) else 1
    finally:
        if a.submit:
            api(f"/close?target={tid}")


if __name__ == "__main__":
    sys.exit(main())
