#!/usr/bin/env python3
"""案例库填写界面 — 本地网页，直接读写 xhs/素材库/案例库.csv。

用法：python3 scripts/case-entry/case_entry.py   （自动打开浏览器，Ctrl+C 退出）
"""
import csv
import json
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CSV_PATH = REPO / "xhs" / "素材库" / "案例库.csv"
FIELDS = ["案例ID", "场景", "对方原话", "我的原话", "结果", "可迁移的那一句", "已用于哪些笔记"]
PORT = 8787

PAGE = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><title>案例库 · 原话采集</title>
<style>
 body{margin:0;font-family:-apple-system,"PingFang SC",sans-serif;background:#f6f6f2;color:#222;display:flex;min-height:100vh}
 .main{flex:1;max-width:1080px;padding:40px 56px;margin:0 auto}
 .tabs{display:flex;gap:10px;margin:20px 0 8px}
 .tab{padding:8px 22px;border:1px solid #ccc;border-radius:20px;cursor:pointer;font-size:14px;background:#fff;user-select:none}
 .tab.active{background:#1c1c1c;color:#fff;border-color:#1c1c1c}
 .side{width:330px;background:#efeee7;padding:40px 32px;font-size:13.5px;line-height:1.8;color:#555}
 h1{font-size:20px} h3{margin:20px 0 6px;font-size:14px;color:#8a6d2f}
 label{display:block;margin:18px 0 6px;font-weight:600;font-size:14px}
 label small{font-weight:400;color:#999;margin-left:8px}
 input,textarea,select{width:100%;box-sizing:border-box;padding:10px;border:1px solid #ccc;border-radius:6px;font-size:15px;font-family:inherit;background:#fff}
 textarea{resize:vertical}
 button{margin-top:24px;padding:12px 28px;background:#1c1c1c;color:#fff;border:0;border-radius:6px;font-size:15px;cursor:pointer}
 button:hover{background:#c0392b}
 .ok{color:#2f7d4f;margin-left:14px} .quote-hint{border-left:3px solid #c0392b;padding-left:10px;margin:8px 0}
 table{border-collapse:collapse;width:100%;margin-top:28px;font-size:13px}
 td,th{border-bottom:1px solid #ddd;padding:6px 8px;text-align:left;vertical-align:top}
 .pending{color:#b06c00}
 .row{cursor:pointer} .row:hover td{background:#efeee7}
</style></head><body>
<div class="main">
 <h1>案例库</h1>
 <div class="tabs">
  <div class="tab active" id="t-entry" onclick="showTab('entry')">原话采集</div>
  <div class="tab" id="t-view" onclick="showTab('view')">案例查看</div>
  <div class="tab" id="t-drafts" onclick="showTab('drafts')">成稿预览</div>
 </div>
 <div id="pane-entry">
 <label>案例<select id="cid"></select></label>
 <label>场景</label><input id="f1">
 <label>对方原话 <small>只有这一句需要精确。短的、糙的、不完整的才是真的；记不全就加「大意是」「后面还说了一串记不清了」</small></label>
 <textarea id="f2" rows="3" placeholder="例：这个数据怎么来的，有对照组吗"></textarea>
 <label>我的原话 <small>写「当时大概说的是X；现在回头看，应该这么说：Y」</small></label>
 <textarea id="f3" rows="4"></textarea>
 <label>结果 <small>那场对话/答辩/谈判最后怎么样了</small></label>
 <textarea id="f4" rows="2"></textarea>
 <label>可迁移的那一句 <small>读者明天就能用的句式</small></label>
 <textarea id="f5" rows="2"></textarea>
 <button onclick="save()">保存到案例库.csv</button><span id="msg"></span>
 </div>
 <div id="pane-view" style="display:none">
 <div style="margin-top:16px;display:flex;gap:10px;align-items:center">
  <input id="q" placeholder="搜索案例：场景 / 原话 / 可迁移句 关键字…" style="flex:1" oninput="renderList()">
  <select id="flt" style="width:130px" onchange="renderList()">
   <option value="all">全部</option><option value="filled">已填</option><option value="pending">待补充</option>
  </select>
  <span id="cnt" style="font-size:13px;color:#888;white-space:nowrap"></span>
 </div>
 <table id="list"></table>
 </div>
 <div id="pane-drafts" style="display:none">
  <div style="display:flex;gap:24px;margin-top:16px">
   <div style="width:320px;flex-shrink:0">
    <div id="dcnt" style="font-size:13px;color:#888;margin-bottom:8px"></div>
    <div id="dlist"></div>
   </div>
   <div style="flex:1;min-width:0">
    <div id="dimgs" style="display:flex;gap:8px;overflow-x:auto;margin-bottom:14px"></div>
    <div id="daudit" style="font-size:13px;color:#8a6d2f;margin-bottom:10px"></div>
    <div id="dbody" style="background:#fff;border:1px solid #ddd;border-radius:8px;padding:24px 28px;font-size:15px;line-height:1.9;white-space:pre-wrap;word-break:break-word">← 从左侧选择一篇成稿</div>
   </div>
  </div>
 </div>
</div>
<div class="side" id="side">
 <h3>提取不出来？从情绪进：</h3>
 1. 哪一句话让你当场<b>胃里一沉</b>？<br>
 2. 哪一句你事后在车里、地铁上<b>又想了一遍</b>？<br>
 3. 哪一句你回家<b>跟人复述过</b>？（最可靠）<br>
 4. 哪一句你当时<b>特别想反驳但没敢</b>？
 <h3>外部检索源</h3>
 答辩 PPT 与评委反馈 · 绩效书面评语 · 周报「风险与阻塞」栏 · 抄送很多人的邮件 · <b>微信吐槽记录（金矿）</b> · 找当年在场的人聊
 <h3>校验标准</h3>
 <div class="quote-hint">结构工整、逻辑闭环的一定是现在编的。真实的刁难通常七八个字就完了。</div>
 <h3>边界</h3>
 可以：合并相似场景、模糊时间地点、多人合成一个角色。<br>不可以：造没发生过的场景；给认得出的人安没说过的话。
</div>
<script>
let rows=[];
async function load(){
 rows=await (await fetch('/data')).json();
 const sel=document.getElementById('cid');
 sel.innerHTML=rows.map((r,i)=>`<option value="${i}">${r["案例ID"]} · ${r["场景"]}</option>`).join('')+'<option value="new">＋ 新建案例</option>';
 sel.onchange=fill; fill();
 renderList();
}
function renderList(){
 const q=(document.getElementById('q').value||'').trim().toLowerCase();
 const flt=document.getElementById('flt').value;
 const hits=rows.map((r,i)=>({r,i})).filter(({r})=>{
  const filled=r["对方原话"]!=='待补充';
  if(flt==='filled'&&!filled) return false;
  if(flt==='pending'&&filled) return false;
  if(!q) return true;
  return ["案例ID","场景","对方原话","我的原话","结果","可迁移的那一句","已用于哪些笔记"].some(k=>(r[k]||'').toLowerCase().includes(q));
 });
 const filled=rows.filter(r=>r["对方原话"]!=='待补充').length;
 document.getElementById('cnt').textContent=`显示 ${hits.length}/${rows.length} · 已填 ${filled}`;
 document.getElementById('list').innerHTML='<tr><th>ID</th><th>场景</th><th>对方原话</th><th>可迁移的那一句</th></tr>'+
  hits.map(({r,i})=>`<tr class="row" onclick="view(${i})" title="点击查看/编辑完整内容"><td>${r["案例ID"]}</td><td>${r["场景"]}</td><td class="${r["对方原话"]==='待补充'?'pending':''}">${r["对方原话"].slice(0,55)}</td><td class="${r["可迁移的那一句"]==='待补充'?'pending':''}">${(r["可迁移的那一句"]||'').slice(0,35)}</td></tr>`).join('');
}
function showTab(name){
 for(const t of ['entry','view','drafts']){
  document.getElementById('pane-'+t).style.display=t===name?'':'none';
  document.getElementById('t-'+t).className='tab'+(t===name?' active':'');
 }
 document.getElementById('side').style.display=name==='entry'?'':'none';
 if(name==='view') renderList();
 if(name==='drafts') loadDrafts();
}
async function loadDrafts(){
 const ds=await (await fetch('/drafts')).json();
 document.getElementById('dcnt').textContent=`共 ${ds.length} 篇（含归档）`;
 document.getElementById('dlist').innerHTML=ds.map(d=>
  `<div class="ditem" onclick="showDraft('${d.name}',${d.archived})" style="padding:9px 12px;border:1px solid #ddd;border-radius:6px;margin-bottom:6px;cursor:pointer;background:#fff;font-size:13px">
    <b>${d.name.replace(/^成稿_/,'').replace(/\\.md$/,'')}</b>${d.archived?' <span style="color:#999">[归档]</span>':''}<br>
    <span style="color:${d.score===null?'#bbb':d.score>=85?'#2f7d4f':d.score>=70?'#b06c00':'#c0392b'}">${d.score===null?'未审核':d.score+' 分 · '+(d.grade||'')}</span>
   </div>`).join('');
}
let curDraft=null;
async function showDraft(name,arch){
 const d=await (await fetch('/draft?name='+encodeURIComponent(name)+'&arch='+(arch?1:0))).json();
 curDraft={name,arch};
 document.getElementById('daudit').textContent=d.audit?`审核：${d.audit}`:'尚无审核记录';
 document.getElementById('dimgs').innerHTML=(d.images||[]).map(u=>
  `<div style="text-align:center"><img src="${u}" style="height:240px;border-radius:6px;border:1px solid #ddd;display:block"><a href="${u}" download style="font-size:12px">下载</a></div>`).join('')
  ||'<span style="color:#999;font-size:13px">封面生成中，稍后重新点击本篇即可看到</span>';
 const cp=(id,txt)=>`<button style="margin:0;padding:5px 14px;font-size:12px" onclick="navigator.clipboard.writeText(document.getElementById('${id}').textContent).then(()=>this.textContent='已拷贝 ✓')">拷贝</button>`;
 document.getElementById('dbody').innerHTML=
  `<div style="display:flex;align-items:center;gap:12px;margin-bottom:4px"><h2 id="dtitle" style="margin:0;font-size:22px">${d.title||'（未解析出标题）'}</h2>${cp('dtitle')}</div>
   <div style="display:flex;gap:12px;align-items:flex-start;margin:14px 0 4px"><b style="flex-shrink:0">正文</b>${cp('dtext')}
    <button style="margin:0;padding:5px 14px;font-size:12px;background:#c0392b" onclick="prefill(this)">预填到小红书发布页</button></div>
   <div id="dtext" style="white-space:pre-wrap;background:#fafaf7;border:1px solid #eee;border-radius:6px;padding:14px">${(d.body||d.content).replace(/</g,'&lt;')}</div>
   <div style="display:flex;gap:12px;align-items:center;margin-top:12px"><b>标签</b><span id="dtags">${d.tags||''}</span>${d.tags?cp('dtags'):''}</div>
   <div id="dlog" style="font-size:12.5px;color:#666;white-space:pre-wrap;margin-top:10px"></div>
   <details style="margin-top:16px;border:none"><summary style="font-size:13px;color:#999;cursor:pointer">查看原始 md 全文</summary><pre style="white-space:pre-wrap;font-size:13px">${d.content.replace(/</g,'&lt;')}</pre></details>`;
}
let curTid=null;
async function prefill(btn){
 if(!curDraft) return;
 btn.textContent='预填中…';
 const r=await (await fetch('/prefill?name='+encodeURIComponent(curDraft.name)+'&arch='+(curDraft.arch?1:0))).json();
 curTid=r.tid||null;
 document.getElementById('dlog').textContent=r.log;
 btn.textContent=r.ok?'已预填 ✓':'预填失败，见下方日志';
 if(r.ok){
  const d=new Date(Date.now()+86400000);
  const t=`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')} 09:00`;
  btn.insertAdjacentHTML('afterend',
   ` <button style="margin:0 0 0 8px;padding:5px 14px;font-size:12px;background:#8a6d2f" onclick="doclick(this,'sched','${t}')">定时明早9点发布</button>`+
   ` <button style="margin:0 0 0 8px;padding:5px 14px;font-size:12px" onclick="doclick(this,'now','')">立即点击发布</button>`);
 }
}
async function doclick(btn,mode,t){
 if(!curTid) return;
 btn.textContent='执行中…';
 const r=await (await fetch('/doclick?tid='+curTid+'&mode='+mode+'&time='+encodeURIComponent(t))).json();
 document.getElementById('dlog').textContent=r.log;
 btn.textContent=r.ok?'完成 ✓ 去 Chrome 确认':'未完全成功，见日志';
}
function view(i){
 document.getElementById('cid').value=String(i);
 fill();
 showTab('entry');
 window.scrollTo({top:0,behavior:'smooth'});
}
function fill(){
 const v=document.getElementById('cid').value;
 const r=v==='new'?{"场景":"","对方原话":"","我的原话":"","结果":"","可迁移的那一句":""}:rows[v];
 const ids=['f1','f2','f3','f4','f5'], ks=["场景","对方原话","我的原话","结果","可迁移的那一句"];
 ids.forEach((id,i)=>document.getElementById(id).value=r[ks[i]]==='待补充'?'':r[ks[i]]);
}
async function save(){
 const v=document.getElementById('cid').value;
 const body={idx:v, 场景:f1.value, 对方原话:f2.value, 我的原话:f3.value, 结果:f4.value, 可迁移的那一句:f5.value};
 const msg=document.getElementById('msg');
 try{
  const res=await fetch('/save',{method:'POST',body:JSON.stringify(body)});
  msg.textContent=res.ok?'✅ 已写入':'❌ 保存失败（HTTP '+res.status+'）';
  msg.style.color=res.ok?'#2f7d4f':'#c0392b';
  if(res.ok) load();
 }catch(e){
  msg.textContent='❌ 服务未运行——在终端执行 python3 scripts/case-entry/case_entry.py 后，回到本页再点一次保存（已填内容不会丢）';
  msg.style.color='#c0392b';
 }
}
const h=location.hash.slice(1);
if(['view','drafts'].includes(h)) showTab(h);
load().catch(()=>{const m=document.getElementById('msg');m.textContent='❌ 服务未运行，请先启动';m.style.color='#c0392b';});
</script></body></html>"""


SUCAI = CSV_PATH.parent


def read_rows():
    with CSV_PATH.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def audit_map():
    p = SUCAI / "审核记录.csv"
    m = {}
    if p.exists():
        for r in csv.DictReader(p.open(encoding="utf-8")):
            m[(r.get("成稿文件") or "").strip()] = r
    return m


def list_drafts():
    am = audit_map()
    out = []
    for base, archived in [(SUCAI, False), (SUCAI / "归档稿", True)]:
        if not base.is_dir():
            continue
        for f in base.glob("成稿_*.md"):
            a = am.get(f.name)
            out.append({
                "name": f.name, "archived": archived,
                "score": int(a["总分"]) if a and (a.get("总分") or "").isdigit() else None,
                "grade": (a.get("评级") if a else None),
            })
    out.sort(key=lambda d: d["name"], reverse=True)
    return out


def parse_draft(text):
    """从成稿 md 中启发式提取 发布标题/正文/话题标签。解析失败各字段回退为空。"""
    import re
    title = ""
    m = re.search(r"^#{1,3}\s*发布标题[^\n]*\n(.*?)(?=\n#{1,3}\s|\Z)", text, re.M | re.S)
    if m:
        for line in m.group(1).splitlines():
            line = line.strip()
            if not line or line == "---":
                continue
            line = re.sub(r"——?触发器[^\n]*", "", line)          # 去触发器注释
            line = re.sub(r"（[^）]*触发器[^）]*）", "", line)
            line = line.strip(" *-①②③").lstrip("0123456789.．、 ")
            line = line.strip("*《》「」 ").strip()
            if line:
                title = line
                break
    if not title:
        m = re.search(r"^#\s*(?:成稿[：:]\s*)?(.+?)(?:\s*20\d{2}-\d{2}-\d{2})?\s*$", text, re.M)
        title = (m.group(1).strip() if m else "")
    body = ""
    m = re.search(r"^#{1,3}\s*\*{0,2}正文[^\n]*\n(.*?)(?=\n#{1,3}\s|\n\*\*(?:60秒|话题|合规|封面)|\Z)", text, re.M | re.S)
    if m:
        body = m.group(1).strip().strip("-").strip()
    tag_src = text
    m = re.search(r"^#{1,3}\s*\*{0,2}话题标签[^\n]*\n(.*?)(?=\n#{1,3}\s|\Z)", text, re.M | re.S)
    if m:
        tag_src = m.group(1)
    tags = [t for t in re.findall(r"#[\w一-鿿]+", tag_src)
            if not re.fullmatch(r"#[0-9A-Fa-f]{3,8}", t)]
    return {"title": title, "body": body, "tags": " ".join(dict.fromkeys(tags))[:300]}


def ensure_cover(name, title):
    """为成稿即时渲染封面卡（缓存到 成品图/<stem>/00_cover.png）。"""
    import subprocess as sp
    import tempfile
    stem = name.removeprefix("成稿_").removesuffix(".md")
    out_dir = SUCAI / "成品图" / stem
    cover = out_dir / "01_cover.png"
    if cover.exists() or not title:
        return
    cards = [{"type": "cover", "tag": "职场表达", "title": title, "body": ""}]
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(cards, f, ensure_ascii=False)
        tmp = f.name
    try:
        sp.run(["python3", str(SUCAI / "图文模板" / "make_cards.py"), tmp, str(out_dir)],
               capture_output=True, timeout=90)
    except Exception:
        pass


def do_publish_click(tid, mode, sched_time):
    """在已预填的发布页上执行最终动作：mode=now 直接点发布；mode=sched 先开定时并设时间再点。"""
    import urllib.request as rq
    import time
    log = []

    def ev(js):
        req = rq.Request(f"http://localhost:3456/eval?target={tid}", data=js.encode(), method="POST")
        return json.loads(rq.urlopen(req, timeout=30).read()).get("value")

    try:
        if mode == "sched":
            r = ev('(()=>{const leaf=[...document.querySelectorAll("*")].find(e=>e.children.length===0&&e.textContent.trim()==="定时发布");'
                   'let n=leaf,sw=null;for(let i=0;i<6&&n;i++){sw=[...(n.parentElement?.querySelectorAll("[class*=switch]")||[])]'
                   '.find(e=>/switch-switch/.test(e.className));if(sw)break;n=n.parentElement;}'
                   'if(!sw)return "sw✗";const on=/checked|active|open/.test(sw.className)||sw.querySelector("input")?.checked;'
                   'if(!on)sw.click();return "sw✓";})()')
            log.append(f"定时开关：{r}")
            time.sleep(1.5)
            if sched_time:
                r = ev('(()=>{const i=[...document.querySelectorAll("input")].find(x=>/\\d{4}-\\d{2}-\\d{2}/.test(x.value)||/时间|日期/.test(x.placeholder||""));'
                       'if(!i)return "time✗";'
                       'const s=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,"value").set;'
                       f's.call(i,{json.dumps(sched_time)});'
                       'i.dispatchEvent(new Event("input",{bubbles:true}));i.dispatchEvent(new Event("change",{bubbles:true}));'
                       'return "time✓ "+i.value;})()')
                log.append(f"定时时间：{r}")
                time.sleep(1)
        r = ev('(()=>{const b=[...document.querySelectorAll("button,[class*=btn],[class*=Btn]")]'
               '.find(e=>{const t=e.textContent.trim();return (t==="发布"||t==="定时发布")&&e.offsetWidth>40&&e.offsetWidth<300;});'
               'if(!b)return "btn✗";b.click();return "已点击「"+b.textContent.trim()+"」";})()')
        log.append(f"发布按钮：{r}")
        time.sleep(3)
        r = ev('document.body.innerText.slice(0,120)')
        log.append(f"页面状态：{(r or '')[:80]}")
        return {"ok": "✗" not in "".join(log), "log": "\n".join(log)}
    except Exception as e:
        return {"ok": False, "log": "\n".join(log + [f"失败：{e}"])}


def draft_detail(name, archived):
    if "/" in name or ".." in name:
        return None
    f = (SUCAI / "归档稿" / name) if archived else (SUCAI / name)
    if not f.exists():
        return None
    text = f.read_text(encoding="utf-8")
    a = audit_map().get(name)
    audit = f"{a['总分']} 分 · {a.get('评级','')} · 处置:{a.get('处置','')} · {a.get('备注','')[:80]}" if a else None
    parsed = parse_draft(text)
    ensure_cover(name, parsed["title"])
    imgs = []
    stem = name.removeprefix("成稿_").removesuffix(".md")
    img_dir = SUCAI / "成品图" / stem
    if img_dir.is_dir():
        imgs = [f"/img?f=成品图/{stem}/{p.name}" for p in sorted(img_dir.glob("*.png"))]
    return {"content": text, "audit": audit, "images": imgs, **parsed}


def prefill_xhs(name, archived):
    """通过 web-access CDP 代理，在用户 Chrome 中打开小红书创作平台并预填图/题/文。
    不点发布——最后一步留给人。返回操作日志。"""
    import urllib.request as rq
    import urllib.parse as up
    import time
    log = []

    def api(path, data=None):
        url = f"http://localhost:3456{path}"
        req = rq.Request(url, data=data.encode() if data else None, method="POST" if data else "GET")
        return json.loads(rq.urlopen(req, timeout=30).read())

    d = draft_detail(name, archived)
    if not d:
        return {"ok": False, "log": "成稿不存在"}
    stem = name.removeprefix("成稿_").removesuffix(".md")
    img_dir = SUCAI / "成品图" / stem
    files = [str(p) for p in sorted(img_dir.glob("*.png"))] if img_dir.is_dir() else []
    if not files:
        return {"ok": False, "log": "没有已渲染的卡片图，先等封面生成或本机渲染图文 JSON"}
    try:
        t = api("/new?url=" + up.quote("https://creator.xiaohongshu.com/publish/publish?source=official", safe=""))
        tid = t.get("targetId")
        log.append(f"已打开创作平台（tab {tid[:8]}…）")
        time.sleep(4)

        # 1. 页面默认停在「上传视频」，必须先切到「上传图文」tab
        r = api(f"/eval?target={tid}",
                '(()=>{const el=[...document.querySelectorAll("[class*=tab]")]'
                '.find(e=>e.textContent.trim()==="上传图文");'
                'if(el){el.click();return "ok"} return "notfound"})()')
        if r.get("value") != "ok":
            return {"ok": False, "log": "\n".join(log + ["找不到「上传图文」tab，页面结构可能已变"])}
        log.append("已切换到「上传图文」")
        time.sleep(2)

        # 2. 确认文件输入框收图片格式后上传
        r = api(f"/eval?target={tid}",
                'document.querySelector("input[type=file]")?.accept||""')
        if "png" not in (r.get("value") or ""):
            return {"ok": False, "log": "\n".join(log + [f"文件输入框格式异常：{r.get('value')}"])}
        r = api(f"/setFiles?target={tid}", json.dumps({"selector": "input[type=file]", "files": files}))
        log.append(f"图片上传：{len(files)} 张 → {r}")
        time.sleep(5)

        # 3. 标题走原生 setter + input 事件；正文是 ProseMirror，必须用 execCommand insertText
        import re as _re
        clean = _re.sub(r"\*\*(.+?)\*\*", r"\1", d["body"])       # 去 markdown 粗体
        clean = _re.sub(r"^#{1,6}\s+", "", clean, flags=_re.M)     # 去残留标题标记
        clean = _re.sub(r"^---+\s*$", "", clean, flags=_re.M).strip()
        full_body = clean  # 标签单独走话题联想，见第 3.5 步
        fill = (
            "(()=>{"
            "const t=[...document.querySelectorAll('input')].find(i=>(i.placeholder||'').includes('标题'));"
            "if(t){const s=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;"
            f"s.call(t,{json.dumps(d['title'][:20])});t.dispatchEvent(new Event('input',{{bubbles:true}}));}}"
            "const ed=document.querySelector('[contenteditable=true]');let r2='正文✗';"
            f"if(ed){{ed.focus();r2='正文'+(document.execCommand('insertText',false,{json.dumps(full_body)})?'✓':'✗');}}"
            "return (t?'标题✓':'标题✗')+' '+r2;})()"
        )
        r = api(f"/eval?target={tid}", fill)
        log.append(f"预填结果：{r.get('value')}")

        # 3.5 标签逐个走话题联想（点选后才是真话题），失败则保留纯文本
        tag_ok = 0
        for tag in d["tags"].split()[:10]:
            api(f"/eval?target={tid}",
                '(()=>{const ed=document.querySelector("[contenteditable=true]");ed.focus();'
                'const sel=window.getSelection();sel.selectAllChildren(ed);sel.collapseToEnd();'
                f'document.execCommand("insertText",false,{json.dumps(" #" + tag.lstrip("#"))});return 1;}})()')
            time.sleep(1.3)
            r = api(f"/eval?target={tid}",
                    '(()=>{const rows=[...document.querySelectorAll("*")].filter(e=>e.textContent.includes("浏览")&&e.childElementCount>=1&&e.offsetHeight>20&&e.offsetHeight<80);'
                    'if(rows.length){rows[0].click();return "picked"}'
                    'const ed=document.querySelector("[contenteditable=true]");ed.focus();document.execCommand("insertText",false," ");return "plain";})()')
            if r.get("value") == "picked":
                tag_ok += 1
            time.sleep(0.6)
        log.append(f"话题标签：{tag_ok}/{min(len(d['tags'].split()),10)} 个转为真话题，其余保留文本")

        # 4. 回读校验
        r = api(f"/eval?target={tid}",
                'JSON.stringify({t:[...document.querySelectorAll("input")].find(i=>(i.placeholder||"").includes("标题"))?.value,'
                'b:(document.querySelector("[contenteditable=true]")?.innerText||"").length})')
        log.append(f"回读校验：{r.get('value')}")
        log.append("✅ 预填完成。可在工作台点「立即发布」/「定时发布」，或到 Chrome 手动点发布。")
        return {"ok": True, "log": "\n".join(log), "tid": tid}
    except Exception as e:
        return {"ok": False, "log": "\n".join(log + [f"CDP 代理不可用或操作失败：{e}", "先运行 node ~/.claude/skills/web-access/scripts/check-deps.mjs 启动代理后重试"])}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body, ctype="text/html; charset=utf-8", code=200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def do_GET(self):
        from urllib.parse import urlparse, parse_qs
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/data":
            self._send(json.dumps(read_rows(), ensure_ascii=False), "application/json")
        elif u.path == "/drafts":
            self._send(json.dumps(list_drafts(), ensure_ascii=False), "application/json")
        elif u.path == "/draft":
            d = draft_detail(q.get("name", [""])[0], q.get("arch", ["0"])[0] == "1")
            if d is None:
                self._send('{"error":"not found"}', "application/json", 404)
            else:
                self._send(json.dumps(d, ensure_ascii=False), "application/json")
        elif u.path == "/doclick":
            r = do_publish_click(q.get("tid", [""])[0], q.get("mode", ["now"])[0], q.get("time", [""])[0])
            self._send(json.dumps(r, ensure_ascii=False), "application/json")
        elif u.path == "/prefill":
            r = prefill_xhs(q.get("name", [""])[0], q.get("arch", ["0"])[0] == "1")
            self._send(json.dumps(r, ensure_ascii=False), "application/json")
        elif u.path == "/img":
            rel = q.get("f", [""])[0]
            fp = (SUCAI / rel).resolve()
            if fp.is_file() and fp.suffix == ".png" and str(fp).startswith(str(SUCAI.resolve())):
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.end_headers()
                self.wfile.write(fp.read_bytes())
            else:
                self._send("not found", code=404)
        else:
            self._send(PAGE)

    def do_POST(self):
        d = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        rows = read_rows()
        if d["idx"] == "new":
            row = {k: "" for k in FIELDS}
            row["案例ID"] = f"C{len(rows)+1:03d}"
            rows.append(row)
        else:
            row = rows[int(d["idx"])]
        for k in ["场景", "对方原话", "我的原话", "结果", "可迁移的那一句"]:
            row[k] = d[k].strip() or "待补充"
        with CSV_PATH.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(rows)
        self._send('{"ok":true}', "application/json")


if __name__ == "__main__":
    print(f"案例库填写界面: http://localhost:{PORT}  （Ctrl+C 退出）")
    webbrowser.open(f"http://localhost:{PORT}")
    HTTPServer(("127.0.0.1", PORT), H).serve_forever()
