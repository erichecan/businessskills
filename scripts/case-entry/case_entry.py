#!/usr/bin/env python3
"""案例库填写界面 — 本地网页，直接读写 xhs/素材库/案例库.csv。

用法：python3 scripts/case-entry/case_entry.py   （自动打开浏览器，Ctrl+C 退出）
"""
import csv
import json
import re
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CSV_PATH = REPO / "xhs" / "素材库" / "案例库.csv"
# 来源/来源链接/状态 三列由 harvest_cases.py 引入：案例库同时收 Eric 自己的经历（来源=自有）
# 和采集来的真实原话（来源=采集）。⛔ 这里少写一列，界面保存时 DictWriter 会把整列抹掉。
FIELDS = ["案例ID", "场景", "对方原话", "我的原话", "结果", "可迁移的那一句", "已用于哪些笔记",
          "来源", "来源链接", "状态"]
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
let allDrafts=[];
const TV={'合格':['#2f7d4f','✅'],'疑似不合格':['#b06c00','⚠️'],'作废':['#c0392b','⛔']};
async function loadDrafts(){
 allDrafts=await (await fetch('/drafts')).json();
 renderDrafts();
}
const PSTATE=['已发布','已定时','已预填','预填失败','未处理'];
function renderDrafts(){
 const f=document.querySelector('input[name=tf]:checked')?.value||'all';
 let ds=allDrafts;
 if(f==='badtitle') ds=ds.filter(d=>d.tverdict!=='合格');
 else if(f!=='all') ds=ds.filter(d=>(d.pub||{}).state===f);
 const bad=allDrafts.filter(d=>d.tverdict!=='合格').length;
 const cnt={}; for(const s of PSTATE) cnt[s]=allDrafts.filter(d=>(d.pub||{}).state===s).length;
 const rb=(v,t,n)=>`<label style="display:inline;font-weight:400;margin-right:10px"><input type="radio" name="tf" value="${v}" ${f===v?'checked':''} onchange="renderDrafts()"> ${t}${n===undefined?'':' '+n}</label>`;
 document.getElementById('dcnt').innerHTML=
  `共 <b>${allDrafts.length}</b> 篇（含归档）· 显示 <b>${ds.length}</b>
   <div style="margin:8px 0 4px;font-size:12px">${rb('all','全部')}${rb('badtitle','标题有问题',bad)}</div>
   <div style="margin:2px 0 10px;font-size:12px">
    ${rb('已发布','🟢 已发布',cnt['已发布'])}${rb('已定时','🔵 已定时',cnt['已定时'])}<br>
    ${rb('已预填','🟡 已预填未点',cnt['已预填'])}${rb('未处理','⚪️ 未发布',cnt['未处理'])}
    ${cnt['预填失败']?rb('预填失败','🔴 预填失败',cnt['预填失败']):''}</div>`;
 document.getElementById('dlist').innerHTML=ds.map(d=>{
  const [c,ic]=TV[d.tverdict]||['#999','·'];const p=d.pub||{};
  return `<div class="ditem" onclick="showDraft('${d.name}',${d.archived})" style="padding:9px 12px;border:1px solid #ddd;border-left:3px solid ${c};border-radius:6px;margin-bottom:6px;cursor:pointer;background:#fff;font-size:13px">
    <div style="display:flex;justify-content:space-between;gap:6px">
     <b>${d.name.replace(/^成稿_/,'').replace(/\\.md$/,'')}</b>
     <span style="color:${p.color||'#bbb'};white-space:nowrap;font-size:11.5px">${p.label||''}</span></div>
    ${d.archived?'<span style="color:#999">[归档]</span> ':''}<span style="font-size:11px;padding:1px 6px;border-radius:8px;background:${d.lane==='推荐流'?'#e8e0f5':'#e0eef5'};color:#555">${d.lane}</span>
    <span style="color:${d.score===null?'#bbb':!d.independent?'#999':d.score>=85?'#2f7d4f':d.score>=70?'#b06c00':'#c0392b'}">${
      d.score===null?'未审核':d.score+' 分 · '+(d.grade||'')+(d.independent?'':' ⚠️ 仅自评')}</span>
    <span style="color:#aaa">· 图 ${d.images}</span>${d.disposition?` <span style="color:#888">· ${d.disposition}</span>`:''}<br>
    <span style="color:${c};font-size:12px">${ic} 标题${d.tverdict}（${d.tlen}字）</span>
    ${p.detail?`<br><span style="color:#888;font-size:11.5px">${p.detail}</span>`:''}
   </div>`}).join('');
}
let curDraft=null;
async function showDraft(name,arch){
 const d=await (await fetch('/draft?name='+encodeURIComponent(name)+'&arch='+(arch?1:0))).json();
 curDraft={name,arch};
 const p=d.pub||{};
 document.getElementById('daudit').innerHTML=
   `<div style="border-left:3px solid ${p.color||'#bbb'};padding:6px 12px;margin-bottom:8px;background:#fafaf7">
     <b style="color:${p.color||'#888'}">${p.label||''}</b>
     ${p.detail?`<span style="color:#666"> · ${p.detail}</span>`:''}</div>`
   +(d.audit?`审核：${d.audit}`:'尚无审核记录')
   +(d.predict?`<br><span style="color:#666">📊 ${d.predict}</span>`:'');
 document.getElementById('dimgs').innerHTML=(d.images||[]).map(u=>
  `<div style="text-align:center"><img src="${u}" style="height:240px;border-radius:6px;border:1px solid #ddd;display:block"><a href="${u}" download style="font-size:12px">下载</a></div>`).join('')
  ||'<span style="color:#999;font-size:13px">封面生成中，稍后重新点击本篇即可看到</span>';
 const cp=(id,txt)=>`<button style="margin:0;padding:5px 14px;font-size:12px" onclick="navigator.clipboard.writeText(document.getElementById('${id}').textContent).then(()=>this.textContent='已拷贝 ✓')">拷贝</button>`;
 const t=d.title_check||{};const [tc,tic]=TV[t.verdict]||['#999','·'];
 const rows=(l,ic)=>(l||[]).map(x=>`<div>${ic} ${x.replace(/</g,'&lt;')}</div>`).join('');
 document.getElementById('dbody').innerHTML=
  `<div style="display:flex;align-items:center;gap:12px;margin-bottom:4px"><h2 id="dtitle" style="margin:0;font-size:22px">${d.title||'（未解析出标题）'}</h2>${cp('dtitle')}</div>
   <div style="border-left:3px solid ${tc};background:#fafaf7;padding:8px 12px;font-size:13px;margin:6px 0 14px">
     <b style="color:${tc}">${tic} 标题体检：${t.verdict||'?'}</b>（${t.length||0} 字，上限 20）
     <div style="color:#555;margin-top:4px">${rows(t.hits,'✅')}${rows(t.fatal,'⛔')}${rows(t.misses,'⚠️')}</div>
     <div style="color:#888;margin-top:6px;font-size:12px">规则：skills/eric-xhs-title「⭐ 最高优先级」— 关键词 + 推翻一个预设；疑问句 −35、写读者困境 −32</div>
   </div>
   <div style="display:flex;gap:10px;align-items:center;margin:14px 0 4px;flex-wrap:wrap"><b style="flex-shrink:0">成稿全文</b>
    <button style="margin:0;padding:5px 14px;font-size:12px;background:#2f7d4f" onclick="saveDraft(this)">保存修改</button>
    <button style="margin:0;padding:5px 14px;font-size:12px;background:#566270" onclick="rerender(this)">只重生成本篇图片</button>
    <button style="margin:0;padding:5px 14px;font-size:12px;background:#c0392b" onclick="prefill(this)">预填到小红书发布页</button>
    ${cp('dtags')}</div>
   <textarea id="dedit" spellcheck="false" style="width:100%;box-sizing:border-box;height:460px;font:13px/1.7 ui-monospace,Menlo,monospace;padding:14px;border:1px solid #ddd;border-radius:6px;background:#fafaf7">${d.content.replace(/</g,'&lt;')}</textarea>
   <div style="display:flex;gap:12px;align-items:center;margin-top:12px"><b>标签</b><span id="dtags">${d.tags||''}</span></div>
   <div id="dlog" style="font-size:12.5px;color:#666;white-space:pre-wrap;margin-top:10px"></div>`;
}
async function saveDraft(btn){
 if(!curDraft) return;
 btn.textContent='保存中…';
 const r=await (await fetch('/savedraft',{method:'POST',body:JSON.stringify(
   {name:curDraft.name,arch:curDraft.arch,text:document.getElementById('dedit').value})})).json();
 btn.textContent=r.ok?'已保存 ✓':'保存失败';
 document.getElementById('dlog').textContent=r.log||'';
 if(r.ok){await loadDrafts();showDraft(curDraft.name,curDraft.arch);}
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
async function rerender(btn){
 if(!curDraft) return;
 btn.textContent='重渲染中…';
 const r=await (await fetch('/rerender?name='+encodeURIComponent(curDraft.name)+'&arch='+(curDraft.arch?1:0))).json();
 btn.textContent=r.ok?'已重新生成 ✓':'失败，见下方日志';
 document.getElementById('dlog').textContent=r.log||'';
 if(r.ok){await loadDrafts();showDraft(curDraft.name,curDraft.arch);}
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
    # utf-8-sig 而非 utf-8：带 BOM 时首列名会变成 "\ufeff案例ID"，页面上整列取不到值（全 undefined）
    with CSV_PATH.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def audit_map():
    """只认「独立审核」行。

    ⛔ 原来不分审核方，谁写的行都当审核分显示 —— 8:30 任务自评 86 分绿的那篇，
    界面上看着跟独立审核 86 分一模一样，而它根本没过审。D7 定的是「自评无处置权」，
    界面上让自评冒充审核分，等于把那条决策在人眼这一侧又漏掉一次。
    自评行单独返回，标成「自评」显示。
    """
    p = SUCAI / "审核记录.csv"
    indep, own = {}, {}
    if p.exists():
        for r in csv.DictReader(p.open(encoding="utf-8-sig")):
            name = (r.get("成稿文件") or "").strip()
            if (r.get("审核方") or "").strip() == "独立审核":
                indep[name] = r
            else:
                own[name] = r
    return indep, own


def lane_of_draft(text):
    m = re.search(r"口径[:：]\s*\**\s*(搜索流|推荐流)", text)
    return m.group(1) if m else "搜索流"


def prediction_map():
    p = SUCAI / "预测记录.csv"
    m = {}
    if p.exists():
        for r in csv.DictReader(p.open(encoding="utf-8-sig")):
            m[(r.get("成稿文件") or "").strip()] = r
    return m


def list_drafts():
    indep, own = audit_map()
    pub, plog = _pub_sources()
    out = []
    for base, archived in [(SUCAI, False), (SUCAI / "归档稿", True)]:
        if not base.is_dir():
            continue
        for f in base.glob("成稿_*.md"):
            a, o = indep.get(f.name), own.get(f.name)
            src = a or o
            try:
                text = f.read_text(encoding="utf-8")
            except OSError:
                text = ""
            # 标题整篇解析，不能只读前 800 字：「## 发布标题」在引言区之后，
            # 截断会让老稿全部退化成用 H1 当标题，体检结果就全错了。
            title = parse_draft(text).get("title", "") if text else ""
            tc = title_verdict(title)
            out.append({
                "name": f.name, "archived": archived,
                "score": int(src["总分"]) if src and (src.get("总分") or "").isdigit() else None,
                "grade": (src.get("评级") if src else None),
                "independent": a is not None,          # False = 只有自评，不算过审
                "lane": lane_of_draft(text[:800]),
                "disposition": (src.get("处置") if src else None),
                "title": title,
                "tverdict": tc.get("verdict"), "twhy": tc.get("why"), "tlen": tc.get("length"),
                "pub": publish_status(f.name, title, pub, plog),
                "images": len(list((SUCAI / "成品图" /
                                    f.name.removeprefix("成稿_").removesuffix(".md")).glob("*.png")))
                if (SUCAI / "成品图" / f.name.removeprefix("成稿_").removesuffix(".md")).is_dir() else 0,
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
            if not line or line == "---" or line.startswith(">"):
                continue                                          # > 开头是自评注释，不是标题
            # ⛔ 清洗顺序是有讲究的，改动前先想清楚：原来「去领起词」排在「去序号」前面，
            # 而实际行长这样 `**① 主推**「HR说…」`——序号在领起词前面，
            # 于是领起词规则永远匹配不上，「主推「」就跟着标题被填进了小红书。
            # 现在按 由外到内 的顺序剥：注释 → 装饰 → 序号 → 领起词 → 括号块 → 引号。
            line = re.split(r"\s*[—–]{2,}", line)[0]              # ①「—— 触发器：…」整段砍掉
            line = re.sub(r"[→>]\s*(触发器|公式|口径)[^\n]*", "", line)
            line = re.sub(r"【[^】]*】", "", line)                  # ②【首选：认知冲突 × …】整块
            line = line.replace("*", "")                          # ③ 粗体星号
            line = line.strip(" -–—①②③④⑤⑥⑦⑧⑨").lstrip("0123456789.．、 ")  # ④ 序号
            line = re.sub(r"^\s*(主推|首选|备选|备用|推荐)\s*[：:]?\s*", "", line)      # ⑤ 领起词
            # ⑥ 括号注释。原来只删含「字」的，于是「…该不该追问（搜索原句，关键词最左）」
            # 这种不含字数的注释整段留在标题里 —— 而这个标题会被 auto_publish 填进发布页。
            ANNO = r"字|搜索原句|关键词|触发器|公式|张力|首选|备选|备用|推荐|口径|命中"
            line = re.sub(rf"[（(][^）)]*(?:{ANNO})[^）)]*[）)]", "", line)
            line = line.strip("*《》「」『』\"' ").strip()          # ⑦ 包裹引号
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
        body = re.sub(r"^（正文总字数[^）]*）\s*$", "", body, flags=re.M).strip()
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
            # 定时开关 = .post-time-wrapper .d-switch.d-clickable
            # 实测（2026-08-02）：
            #   · el.click() 无效——原生 click 事件缺 clientX/clientY/view，Vue 不认
            #   · pointerdown→…→click 五连发会触发两次 toggle，净效果为零（曾误报 sw✓）
            #   · 正解：只发一个带坐标的合成 click
            # 滚动与取坐标必须分两次 eval，同一次里 rect 还是滚动前的值。
            ev('(()=>{const s=document.querySelector(".post-time-wrapper .d-switch.d-clickable");'
               'if(s)s.scrollIntoView({block:"center",behavior:"instant"});return 1})()')
            time.sleep(2)
            r = ev('(()=>{const w=document.querySelector(".post-time-wrapper");'
                   'if(!w)return "sw✗ 无定时区（图片可能未上传完）";'
                   'const on=()=>/(^|\\s)checked(\\s|$)/.test(w.querySelector(".d-switch-simulator")?.className||"");'
                   'if(on())return "sw✓ 已开";'
                   'const s=w.querySelector(".d-switch.d-clickable"),r=s.getBoundingClientRect();'
                   's.dispatchEvent(new MouseEvent("click",{bubbles:true,cancelable:true,'
                   'clientX:r.left+r.width/2,clientY:r.top+r.height/2,view:window}));'
                   'return on()?"sw✓":"sw✗ 点击未生效";})()')
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
        # 发布按钮是 div.btn-inner / span.btn-text，文本为「发布笔记」，不是 <button>
        r = ev('(()=>{const b=[...document.querySelectorAll("[class*=btn-inner],[class*=btn-text],button,[class*=btn]")]'
               '.find(e=>{const t=(e.innerText||"").trim();'
               'return /^(发布笔记|定时发布笔记|发布|定时发布)$/.test(t)&&e.offsetWidth>40&&e.offsetWidth<320;});'
               'if(!b)return "btn✗ 找不到发布按钮";'
               'b.scrollIntoView({block:"center",behavior:"instant"});'
               'const r=b.getBoundingClientRect(),x=r.left+r.width/2,y=r.top+r.height/2;'
               '["pointerdown","mousedown","pointerup","mouseup","click"].forEach(t=>'
               'b.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,clientX:x,clientY:y,view:window})));'
               'return "已点击「"+(b.innerText||"").trim()+"」";})()')
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
    indep, own = audit_map()
    a, o = indep.get(name), own.get(name)
    src = a or o
    audit = None
    if src:
        who = "独立审核" if a else "⚠️ 仅自评（未过审，不算数）"
        audit = (f"{src['总分']} 分 · {src.get('评级','')} · {who} · "
                 f"处置:{src.get('处置','')} · {src.get('备注','')[:80]}")
    pred = prediction_map().get(name)
    predict = None
    if pred:
        # 押的数要看得见，不然 7 天后复盘时没人记得当初预测了什么
        predict = (f"{pred.get('口径','')} 预测 7 天：观看 {pred.get('观看_低')}-{pred.get('观看_高')} · "
                   f"赞 {pred.get('点赞_低')}-{pred.get('点赞_高')} · 藏 {pred.get('收藏_低')}-{pred.get('收藏_高')} · "
                   f"评 {pred.get('评论_低')}-{pred.get('评论_高')} · 转 {pred.get('转发_低')}-{pred.get('转发_高')} · "
                   f"CES {pred.get('CES_低')}-{pred.get('CES_高')}")
    parsed = parse_draft(text)
    ensure_cover(name, parsed["title"])
    imgs = []
    stem = name.removeprefix("成稿_").removesuffix(".md")
    img_dir = SUCAI / "成品图" / stem
    if img_dir.is_dir():
        imgs = [f"/img?f=成品图/{stem}/{p.name}" for p in sorted(img_dir.glob("*.png"))]
    return {"content": text, "audit": audit, "predict": predict,
            "lane": lane_of_draft(text), "images": imgs,
            "title_check": title_verdict(parsed.get("title", "")),
            "pub": publish_status(name, parsed.get("title", "")), **parsed}


def _pub_sources():
    """（后台已发笔记 by 标题, 本地发布日志最新一行 by 成稿文件）。

    两个来源不是一回事，必须都读：
      · 发布数据.csv 是每天从创作后台抓回来的**真实已发列表**，是硬事实；
      · 发布日志.csv 只记录走过 auto_publish 这条脚本路径的动作，
        人工在页面上直接发的它不知道。
    实测差异：谈薪预算就这么多 / 结构化面试不能说 两篇，本地日志停在
    「— dry-run 未点发布」，后台却早就有了 —— 只信日志会把已发的当成没发，
    再发一次就是重复占位。所以后台优先。
    """
    pub = {}
    p = SUCAI / "发布数据.csv"
    if p.exists():
        for r in csv.DictReader(p.open(encoding="utf-8-sig")):
            t = (r.get("标题") or "").strip()
            if t:
                pub[t] = r          # 同一篇会按抓取日重复出现，留最后一条（最新数据）
    log = {}
    p = SUCAI / "发布日志.csv"
    if p.exists():
        for r in csv.DictReader(p.open(encoding="utf-8-sig")):
            n = (r.get("成稿文件") or "").strip()
            if n:
                log[n] = r
    return pub, log


def publish_status(name, title, pub=None, log=None):
    """这篇稿走到发布流程的哪一步了。返回 {state, label, detail, color}。"""
    if pub is None or log is None:
        pub, log = _pub_sources()
    row = log.get(name)
    # 标题可能在发布之后又被改过，所以先用当前标题匹配后台，
    # 匹配不上再用发布日志当时记下的标题兜一次。
    hit = pub.get((title or "").strip())
    if hit is None and row:
        hit = pub.get((row.get("标题") or "").strip())
    if hit:
        n = lambda k: (hit.get(k) or "0").strip() or "0"
        return {"state": "已发布", "label": "🟢 已发布", "color": "#2f7d4f",
                "detail": f"{hit.get('发布时间','')} · 观看 {n('观看')} 赞 {n('点赞')} "
                          f"藏 {n('收藏')} 评 {n('评论')}"}
    if not row:
        return {"state": "未处理", "label": "⚪️ 未发布", "color": "#bbb", "detail": ""}
    done = (row.get("发布") or "").strip()
    if done.startswith("✅"):
        # 已点定时但后台还没抓到：要么没到点，要么抓取任务当天没跑
        return {"state": "已定时", "label": "🔵 已定时待生效", "color": "#2563eb",
                "detail": f"{done}（后台数据里还没出现，等 daily_data 抓取）"}
    if (row.get("预填") or "").strip() == "✅":
        return {"state": "已预填", "label": "🟡 已预填·最后一步没点", "color": "#b06c00",
                "detail": f"{row.get('日期','')} 预填 · 建议时段 {row.get('定时','')} · {done}"}
    return {"state": "预填失败", "label": "🔴 预填失败", "color": "#c0392b",
            "detail": (row.get("备注") or done)[:80]}


def rerender_cards(name):
    """按本篇自己的 cards.json 重新渲染全部 7 张卡，只动这一篇。

    ⛔ 这里原来是个会毁稿的坑：旧实现只 rmtree 了 成品图/<stem>/ 然后重新读一遍详情，
    而重新读详情时 ensure_cover 只会补一张 01_cover.png —— 点一次「重新生成图片」，
    7 张正片被删光、只剩一张封面。更糟的是发布闸门只检查「目录里有没有 png」，
    剩那一张照样算通过，于是会发出一篇只有封面的笔记。

    现在直接调 make_cards.py 按 图文_<stem>_cards.json 全量重渲。
    渲染成功才替换原目录：渲到一半失败的话，原来的 7 张还在，不至于既没新的也没旧的。
    """
    import shutil
    import subprocess as sp
    import tempfile
    stem = name.removeprefix("成稿_").removesuffix(".md")
    cards = SUCAI / f"图文_{stem}_cards.json"
    if not cards.exists():
        return {"ok": False, "log": f"找不到 {cards.name}，无法重渲（这篇可能从未生成过卡片）"}
    out = SUCAI / "成品图" / stem
    with tempfile.TemporaryDirectory() as tmp:
        r = sp.run(["python3", str(SUCAI / "图文模板" / "make_cards.py"), str(cards), tmp + "/"],
                   capture_output=True, text=True, timeout=300)
        made = sorted(Path(tmp).glob("*.png"))
        if r.returncode != 0 or not made:
            return {"ok": False, "log": (r.stderr or r.stdout or "渲染无输出")[-400:]}
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir(parents=True, exist_ok=True)
        for p in made:
            shutil.copy2(p, out / p.name)
    return {"ok": True, "log": f"已重新渲染 {len(made)} 张 → 成品图/{stem}/"}


def save_draft(name, archived, text):
    """把编辑后的成稿写回磁盘。改完顺手报一下标题体检结果，省得改完还要自己去看。"""
    if "/" in name or ".." in name or not name.endswith(".md"):
        return {"ok": False, "log": "非法文件名"}
    f = (SUCAI / "归档稿" / name) if archived else (SUCAI / name)
    if not f.exists():
        return {"ok": False, "log": "成稿不存在"}
    if not text.strip():
        return {"ok": False, "log": "内容为空，拒绝写入"}
    # 存一份改前的：手改正文很容易把 draft_check 的机械项（字数/句长）碰坏，
    # 想退回去时得有东西可退。同名只留最近一份，不做版本堆积。
    (SUCAI / "归档稿" / "_编辑备份").mkdir(parents=True, exist_ok=True)
    (SUCAI / "归档稿" / "_编辑备份" / name).write_text(
        f.read_text(encoding="utf-8"), encoding="utf-8")
    f.write_text(text, encoding="utf-8")
    title = parse_draft(text).get("title", "")
    return {"ok": True, "log": f"已保存（改前版本备份在 归档稿/_编辑备份/{name}）",
            "title": title, "title_check": title_verdict(title)}


def title_verdict(title):
    """标题体检。判定规则不在这里实现——统一走 scripts/xhs-health/title_check.py，
    否则页面和脚本会各有一套规则，迟早对不上。"""
    import sys as _s
    _s.path.insert(0, str(REPO / "scripts" / "xhs-health"))
    try:
        from title_check import check
        return check(title)
    except Exception as e:
        return {"verdict": "?", "why": f"体检不可用：{e}", "length": len(title or "")}


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

        # 1. 页面默认停在「上传视频」，必须先切到「上传图文」tab。
        # 轮询而非固定 sleep：创作平台首屏渲染慢，固定等 4 秒时 tab 常常还没挂上 DOM。
        switched = False
        for _ in range(20):
            r = api(f"/eval?target={tid}",
                    '(()=>{const el=[...document.querySelectorAll("[class*=tab]")]'
                    '.find(e=>e.textContent.trim()==="上传图文"&&(e.offsetWidth||e.offsetHeight));'
                    'if(el){el.click();return "ok"} return "notfound"})()')
            if r.get("value") == "ok":
                switched = True
                break
            time.sleep(1)
        if not switched:
            return {"ok": False, "log": "\n".join(log + ["等待 20 秒仍找不到「上传图文」tab，页面结构可能已变"])}
        log.append("已切换到「上传图文」")
        time.sleep(2)

        # 2. 确认文件输入框收图片格式后上传
        # 切 tab 后图片 input 才会挂载，同样轮询等它出现
        accept = ""
        for _ in range(15):
            r = api(f"/eval?target={tid}",
                    '(()=>{const i=[...document.querySelectorAll("input[type=file]")]'
                    '.find(x=>/png|jpg|jpeg/i.test(x.accept||""));return i?i.accept:""})()')
            accept = r.get("value") or ""
            if "png" in accept.lower():
                break
            time.sleep(1)
        if "png" not in accept.lower():
            return {"ok": False, "log": "\n".join(log + [f"等待图片上传框超时，当前 accept：{accept}"])}
        r = api(f"/setFiles?target={tid}",
                json.dumps({"selector": 'input[type=file][accept*="png"]', "files": files}))
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
        elif u.path == "/rerender":
            name = q.get("name", [""])[0]
            if "/" in name or ".." in name:
                self._send('{"ok":false,"log":"非法文件名"}', "application/json", 400)
                return
            r = rerender_cards(name)
            if r["ok"]:
                d = draft_detail(name, q.get("arch", ["0"])[0] == "1")
                r["images"] = (d or {}).get("images", [])
            self._send(json.dumps(r, ensure_ascii=False), "application/json")
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
        if self.path.startswith("/savedraft"):
            r = save_draft(d.get("name", ""), bool(d.get("arch")), d.get("text", ""))
            self._send(json.dumps(r, ensure_ascii=False), "application/json")
            return
        rows = read_rows()
        if d["idx"] == "new":
            row = {k: "" for k in FIELDS}
            # 只数 C 开头的：表里混有 H 开头的采集条目，用 len(rows) 会一路跳号
            cnums = [int(m.group(1)) for r in rows
                     if (m := re.match(r"C(\d+)", r.get("案例ID", "")))]
            row["案例ID"] = f"C{max(cnums, default=0)+1:03d}"
            row["来源"], row["状态"] = "自有", "已确认"  # 手工新建的一律是 Eric 自己的
            rows.append(row)
        else:
            row = rows[int(d["idx"])]
            if row.get("状态") == "待确认":
                row["状态"] = "已确认"  # 人工编辑过即视为确认
        for k in ["场景", "对方原话", "我的原话", "结果", "可迁移的那一句"]:
            row[k] = d[k].strip() or "待补充"
        with CSV_PATH.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        self._send('{"ok":true}', "application/json")


if __name__ == "__main__":
    print(f"案例库填写界面: http://localhost:{PORT}  （Ctrl+C 退出）")
    webbrowser.open(f"http://localhost:{PORT}")
    HTTPServer(("127.0.0.1", PORT), H).serve_forever()
