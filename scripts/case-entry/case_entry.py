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
async function showDraft(name,arch){
 const d=await (await fetch('/draft?name='+encodeURIComponent(name)+'&arch='+(arch?1:0))).json();
 document.getElementById('dbody').textContent=d.content;
 document.getElementById('daudit').textContent=d.audit?`独立/自评审核：${d.audit}`:'尚无审核记录';
 document.getElementById('dimgs').innerHTML=(d.images||[]).map(u=>`<img src="${u}" style="height:220px;border-radius:6px;border:1px solid #ddd">`).join('')||'';
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


def draft_detail(name, archived):
    if "/" in name or ".." in name:
        return None
    f = (SUCAI / "归档稿" / name) if archived else (SUCAI / name)
    if not f.exists():
        return None
    a = audit_map().get(name)
    audit = f"{a['总分']} 分 · {a.get('评级','')} · 处置:{a.get('处置','')} · {a.get('备注','')[:80]}" if a else None
    imgs = []
    stem = name.removeprefix("成稿_").removesuffix(".md")
    img_dir = SUCAI / "成品图" / stem
    if img_dir.is_dir():
        imgs = [f"/img?f=成品图/{stem}/{p.name}" for p in sorted(img_dir.glob("*.png"))]
    return {"content": f.read_text(encoding="utf-8"), "audit": audit, "images": imgs}


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
