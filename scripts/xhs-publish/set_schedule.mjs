#!/usr/bin/env node
// 在小红书发布页把「定时发布」的时间选好 —— 此前认为无法程序化的那一步。
//
// 2026-08-06 的实测推翻了 auto_publish.py 里那条注释（「d-datepicker 只认 isTrusted 手势，
// CDP Input.dispatchMouseEvent 试过，元素能点中但面板不展开」）：
// 面板其实**点得开**，最朴素的 mousePressed + mouseReleased 就够。
// 此前判定失败，多半是因为面板被挂在 <body> 下的 .d-popover 里，而不是 .post-time-wrapper 内部——
// 在 wrapper 里找面板，永远找不到，于是得出「点不开」的结论。
//
// 面板结构（实测）：
//   .post-time-date-picker-popover-class
//     .d-datepicker-header-main            "2026年8月"，左右各两个箭头（年/月）
//     .d-datepicker-cell-main[.disabled]   日期格，过去的日子带 disabled
//     .d-timepicker-timebar × 2            第一条 24 个「时」，第二条 60 个「分」
//                                          单元 .d-timepicker-time.d-clickable
//
// 用法：
//   node set_schedule.mjs --at "2026-08-07 09:00"          # 只选时间，不点发布
//   node set_schedule.mjs --at "2026-08-07 09:00" --publish # 选完并点「定时发布」
//   node set_schedule.mjs --at ... --target <tid>           # 指定 tab
//
// ⛔ 不带 --publish 时绝不点发布按钮。这个脚本可能在调试中被反复运行，
//    默认就发出去的话，一次手滑就是一篇没准备好的笔记进了公开时间线。

import fs from 'node:fs';
import path from 'node:path';

const argv = process.argv.slice(2);
const arg = (k, d = '') => { const i = argv.indexOf(k); return i >= 0 && argv[i + 1] ? argv[i + 1] : d; };
const AT = arg('--at');
const DO_PUBLISH = argv.includes('--publish');
const WANT_TID = arg('--target');

if (!AT || !/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/.test(AT)) {
  console.error('用法：node set_schedule.mjs --at "YYYY-MM-DD HH:MM" [--publish] [--target <tid>]');
  process.exit(2);
}
const [Y, MO, D] = AT.slice(0, 10).split('-').map(Number);
const [H, MI] = AT.slice(11).split(':').map(Number);

const sleep = ms => new Promise(r => setTimeout(r, ms));

// ── 连接 ────────────────────────────────────────────────────────────────────
// 优先走自动化专用实例（com.eric.xhschrome，端口 9333）。它是命令行带
// --remote-debugging-port 起的，HTTP 端点正常应答、且**不需要**那个会过期的
// chrome://inspect 开关。日常 Chrome 的 9222 只作兜底：它的 HTTP 端点全 404，
// 只能读 DevToolsActivePort 拿带 UUID 的 WS 路径，而且授权随时会失效。
async function browserWsUrl() {
  const port = Number(process.env.CHROME_DEBUG_PORT || 9333);
  try {
    const r = await fetch(`http://127.0.0.1:${port}/json/version`,
                          { signal: AbortSignal.timeout(2500) });
    const j = await r.json();
    if (j.webSocketDebuggerUrl) return j.webSocketDebuggerUrl;
  } catch { /* 专用实例没起，往下兜底 */ }

  const files = [
    'Library/Application Support/Google/Chrome/DevToolsActivePort',
    'Library/Application Support/Google/Chrome Canary/DevToolsActivePort',
    'Library/Application Support/Chromium/DevToolsActivePort',
  ].map(p => path.join(process.env.HOME, p));
  for (const f of files) {
    if (!fs.existsSync(f)) continue;
    const [p2, wsPath] = fs.readFileSync(f, 'utf8').trim().split('\n');
    if (p2 && wsPath) return `ws://127.0.0.1:${p2}${wsPath}`;
  }
  throw new Error(
    `连不上 Chrome。专用实例（端口 ${port}）没应答，日常 Chrome 也没有可用的调试入口。\n` +
    `→ 检查 launchctl list com.eric.xhschrome`);
}

function rawConnect(url) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(url);
    const pending = new Map();
    let id = 0;
    ws.addEventListener('open', () => resolve({
      send(method, params = {}, sessionId) {
        const mid = ++id;
        return new Promise((res, rej) => {
          pending.set(mid, { res, rej });
          ws.send(JSON.stringify(sessionId ? { id: mid, method, params, sessionId } : { id: mid, method, params }));
          setTimeout(() => pending.has(mid) && (pending.delete(mid), rej(new Error(`${method} 超时`))), 20000);
        });
      },
      close: () => ws.close(),
    }));
    ws.addEventListener('error', e => reject(new Error(`WebSocket 连接失败：${e.message || e.type}`)));
    ws.addEventListener('message', ev => {
      const m = JSON.parse(ev.data);
      if (m.id && pending.has(m.id)) {
        const { res, rej } = pending.get(m.id);
        pending.delete(m.id);
        m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result);
      }
    });
  });
}

async function attach() {
  const raw = await rawConnect(await browserWsUrl());
  const { targetInfos } = await raw.send('Target.getTargets');
  const pages = targetInfos.filter(t => t.type === 'page');
  const t = WANT_TID ? pages.find(p => p.targetId === WANT_TID)
                     : pages.find(p => /creator\.xiaohongshu\.com\/publish/.test(p.url || ''));
  if (!t) { raw.close(); throw new Error('没找到小红书发布页 tab'); }
  const { sessionId } = await raw.send('Target.attachToTarget', { targetId: t.targetId, flatten: true });
  return { send: (m, p) => raw.send(m, p, sessionId), close: () => raw.close() };
}

// ── 点击原语 ────────────────────────────────────────────────────────────────

const evaluate = async (cdp, js) => {
  const r = await cdp.send('Runtime.evaluate', { expression: js, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || '页面求值异常');
  return r.result?.value;
};

/** 传一段返回元素的 JS 表达式，滚进可视区后按视口坐标点它。
 *
 * ⛔ 2026-08-16 重写。老版本把 scrollIntoView 和 getBoundingClientRect 放在
 * **同一次求值**里（注释说「分两次拿到的是滚动前的 rect」）—— 那个理由只在
 * 不等待的前提下成立，代价是滚动尚未稳定就把坐标读走了。
 * 实测后果：timebar 里 24 个「时」，需要滚动才能露出的目标会点偏 ——
 * 目标 20:00 点成了 13:00（差 7 格），11:00 也失败，而 12:00（无需滚动）成功。
 * 一晚三篇里两篇栽在这。
 *
 * 现在分三步：滚动 → 等它稳 → 重取坐标，并且**点之前用 elementFromPoint
 * 校验该坐标处真的是目标元素**（被遮挡/坐标失效都会在这暴露），不中就重试。
 * 校验比「小心地一次算对」可靠：点偏了会立刻发现，而不是等回读时才看出来。 */
async function clickBy(cdp, exprReturningEl, label) {
  let last = null;
  for (let attempt = 1; attempt <= 3; attempt++) {
    await evaluate(cdp, `(() => {
      const el = ${exprReturningEl};
      if (el) el.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'instant' });
      return !!el;
    })()`);
    await sleep(260);                       // 让滚动与重排落定，再取坐标

    const box = await evaluate(cdp, `(() => {
      const el = ${exprReturningEl};
      if (!el) return null;
      const r = el.getBoundingClientRect();
      if (r.width < 1 || r.height < 1) return { hidden: true };
      const x = r.x + r.width / 2, y = r.y + r.height / 2;
      const hit = document.elementFromPoint(x, y);
      return {
        x, y,
        onTarget: !!hit && (hit === el || el.contains(hit) || hit.contains(el)),
        want: (el.textContent || '').trim().slice(0, 12),
        got: hit ? (hit.textContent || '').trim().slice(0, 12) : '(无)',
      };
    })()`);
    if (!box) throw new Error(`${label}：没找到元素`);
    if (box.hidden) throw new Error(`${label}：元素存在但不可见`);
    last = box;

    if (box.onTarget) {
      await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: box.x, y: box.y, button: 'left', clickCount: 1 });
      await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: box.x, y: box.y, button: 'left', clickCount: 1 });
      await sleep(450);
      return;
    }
    console.log(`  ⟳ ${label}：坐标处是「${box.got}」不是「${box.want}」，重定位（第 ${attempt} 次）`);
    await sleep(220);
  }
  throw new Error(`${label}：三次重定位后坐标处仍是「${last?.got}」而非「${last?.want}」，不点，避免点错格子`);
}

const POP = `document.querySelector('.post-time-date-picker-popover-class')`;
const readInput = cdp => evaluate(cdp,
  `(document.querySelector('.d-datepicker-input-filter')||{}).value
   || (document.querySelector('.post-time-wrapper')||{}).innerText || ''`);

// ── 主流程 ──────────────────────────────────────────────────────────────────

async function main() {
  const cdp = await attach();
  await cdp.send('Runtime.enable');
  await cdp.send('Page.enable');
  // ⛔ 2026-09-04 加。这台自动化专用 Chromium（com.eric.xhschrome）跟随的是
  // **宿主机真实系统时区** America/Toronto（EDT），不是 Asia/Shanghai —— 而创作
  // 平台「定时发布」面板内部算「现在」直接用 `new Date()`，只是把结果标成
  // 「北京时间」显示，并不真的做时区换算。两者相差 12 小时，实测后果：
  // 面板默认预填值（「当前+1.5h」）读出来是 EDT 时间但被当成北京时间显示
  // （比如 EDT 08:32 显示成"2026-09-04 09:32 北京时间"）；更隐蔽的是，选完
  // 时/分之后，面板会用它内部这个跑偏的「现在」去做校验/纠偏，把已经选好
  // 的日期/分钟悄悄拉回去（2026-09-04 实测：选好 "2026-09-05 09:00"，选完
  // 时分后回读却变成了 "2026-09-04 09:35"——分钟被拉回接近 EDT 当前时刻）。
  // 用 Emulation.setTimezoneOverride 把这个 tab 的时区钉死成 Asia/Shanghai，
  // 消掉这个偏差源头。必须在 cdp（本文件自己的 session）关闭前一直有效——
  // 实测这个 override 挂在**发起它的那个 CDP session** 上，session 一断就还原，
  // 所以不能用完即关的一次性连接去设置，得跟 set_schedule 主流程共用同一个 cdp。
  await cdp.send('Emulation.setTimezoneOverride', { timezoneId: 'Asia/Shanghai' });
  // 原来这里还开了 DOM 域，专为 DOM.getDocument({pierce:true}) 穿透 shadow root
  // 找发布按钮。发布改走事件派发后就不需要了 —— 日历那几步的坐标是
  // getBoundingClientRect 拿的，走 Runtime 域即可。
  // ⛔ 必须先把 tab 切到前台。cdp-proxy 用 background:true 建 tab，
  // 后台标签页收不到有效的合成鼠标输入 —— 2026-08-06 22:12 实测：
  // 同一段代码手工对前台 tab 跑一次就成，对刚预填出来的后台 tab 跑就报「面板没能打开」。
  // 这也解释了当初「CDP 点不开日历」的结论是怎么来的。
  await cdp.send('Page.bringToFront').catch(() => {});
  await sleep(600);

  const before = await readInput(cdp);
  console.log(`当前时间框：${String(before).replace(/\n/g, ' ').slice(0, 60)}`);
  console.log(`目标时间　：${AT}\n`);

  // 1. 打开面板。
  // ⛔ 每一步之前都要重新确认面板还在 —— 组件在选完日期后会**自己收起**
  //    （2026-08-06 实测：选完 7 号，下一步取 .d-timepicker-timebar 直接 null 崩掉）。
  //    重试而非一击：预填刚结束时组件可能还没挂好监听。
  async function ensurePanel(what) {
    for (let i = 0; i < 4; i++) {
      if (await evaluate(cdp, `!!${POP}`)) return;
      if (i) console.log(`   面板没开（${what}），第 ${i + 1} 次尝试…`);
      await clickBy(cdp, `document.querySelector('.d-datepicker-input-filter')`, '打开日历');
      await sleep(500 + i * 700);
    }
    throw new Error(`日历面板没能打开（${what}，已重试 4 次）`);
  }
  await ensurePanel('初次');
  console.log('✅ 日历面板已打开');

  // 2. 翻到目标月份。用「显示的年月 vs 目标年月」的差值决定点哪个箭头、点几次，
  //    不靠猜箭头顺序 —— 先试点一次看年月往哪边动，再决定方向。
  //
  // ⛔ 2026-08-31 修：老正则 \d{4}\D+\d{1,2} 太松，日历被连续点击点飞进「年份
  //    区间」视图（header 文本变成"2037-2048"这种十年跨度）时，它照样能解析出
  //    一个（非法的）年月值——"2048"里取前两位当月份=20。循环于是一直判断
  //    「不相等」却又不报错，硬跑满 24 次守卫后仍往下走，到选日期那步才因为
  //    面板根本没有日期格子而报「没找到元素」，看起来像是完全不相关的 bug。
  //    现在只认「YYYY年M月」这一种月视图格式；一旦跑出这个格式（不管是刚打开
  //    时就不对，还是翻页途中被点飞），立刻停止翻月并报错，不再继续瞎点，也不
  //    再放任它跑到猜不到出错原因的下一步。
  const headText = () => evaluate(cdp, `(${POP}.querySelector('.d-datepicker-header-main')||{}).textContent||''`);
  const parseHead = s => {
    const m = String(s).replace(/\s+/g, '').match(/^(\d{4})年(\d{1,2})月$/);
    if (!m) return null;
    const month = Number(m[2]);
    return (month >= 1 && month <= 12) ? Number(m[1]) * 12 + month : null;
  };
  const want = Y * 12 + MO;
  for (let guard = 0; guard < 24; guard++) {
    const raw = await headText();
    const cur = parseHead(raw);
    if (cur === null) throw new Error(`日历不在月视图（面板文本「${raw}」）—— 可能被连续点击点飞进了年份/年代选择视图，停止翻月`);
    if (cur === want) break;
    if (guard === 23) throw new Error(`翻月 24 次仍没翻到目标月份，当前停在「${raw}」`);
    // 四个 .--space-p-extra-small 依次是：上一年 / 上一月 / 下一月 / 下一年。
    //
    // ⛔ 2026-08-31 查明真正根因：这四个箭头不是 header 的前 4 个 children ——
    // 标题文字（.d-datepicker-header-main）夹在正中间，实测十年视图下 header 是
    // [上一屏箭头, 标题, 下一屏箭头] 3 个 children，标题在 index 1；月视图同理应是
    // [上一年, 上一月, 标题, 下一月, 下一年] 5 个 children，标题在 index 2。
    // 老代码 `.children[idx]` 把「箭头在 4 元素数组里的序号」直接当 children 下标用，
    // idx=2（翻下一月）因此点到的其实是标题文字——点它会让面板"缩视图级别"跳成
    // 年份/十年份选择视图，这正是本文件另一处 bug（面板跑出月视图）的成因。
    // idx=1（翻上一月）因为在标题之前，位置没错位，所以往回翻从来没出过这个问题。
    // 现在改成按 class 精确取 4 个箭头，不再用「所有 children」的位置去猜。
    const idx = cur < want ? 2 : 1;
    await clickBy(cdp,
      `[...${POP}.querySelector('.d-datepicker-header').querySelectorAll('.--space-p-extra-small')][${idx}]`,
      '翻月');
  }
  console.log(`✅ 月份已到 ${await headText()}`);

  // 3. 选日期。⛔ 必须排掉 .disabled —— 过去的日子和上/下月溢出的格子都是 disabled，
  //    不排的话「7 号」可能点到上个月那个 7 号，或者点了个点不动的格子还以为成功了。
  //
  // ⛔ 2026-08-13 修：目标日**已经是当前选中日**时，再点一次是「取消选中」，
  //    不是「选中」。组件随即把整个定时区收掉、连 .post-time-wrapper 里的
  //    定时开关都重置回 unchecked，datepicker 从 DOM 里整个消失 ——
  //    下一步 ensurePanel 去找 .d-datepicker-input-filter 就报「没找到元素」，
  //    看起来像选择器失效，实际是被自己点没的。
  //    只在跨午夜时现形：默认时间是「当前+1.5h」，23:25 跑时它已经是次日，
  //    而轮换池给的目标也是次日 → 同一天，一点就废。22:00 跑时默认还在当天，撞不上。
  //    判断用时间框的文本而不是格子的 selected class：class 名会随组件版本变，
  //    时间框的值是这一步真正要改的东西，且后面第 5 步还会回读兜底。
  const dateStr = `${Y}-${String(MO).padStart(2, '0')}-${String(D).padStart(2, '0')}`;
  await ensurePanel('选日期前');
  if (String(await readInput(cdp)).includes(dateStr)) {
    console.log(`✅ ${D} 号已是当前选中日，跳过点击（再点会取消选中）`);
  } else {
    await clickBy(cdp,
      `[...${POP}.querySelectorAll('.d-datepicker-cell-main:not(.disabled)')]
         .find(e => e.textContent.trim() === '${D}')`,
      `选日期 ${D} 号`);
    console.log(`✅ 已选 ${D} 号`);
  }

  // 4. 选时、分。两条 timebar 分别是 24 个时和 60 个分，单元要先滚进条内可视区
  //
  // ⛔ 2026-09-04 查明「选 XX 时/分：没找到元素」的真正根因：**当前已选中的那一格
  // 文本会被拼上单位**——不是纯数字 "09"，而是 "09时"（分钟同理 "32分"）；其余未选中
  // 的格子才是纯两位数字。老代码用严格相等 `textContent.trim() === '09'` 去匹配，
  // 一旦目标时/分恰好等于面板打开时默认预选的那个值（默认是"当前时间+1.5h"，会随时间
  // 漂移，撞上纯属运气），textContent 实际是 "09时" ≠ "09"，`.find()` 找不到任何元素、
  // 直接报错——不是虚拟滚动、不是没渲染，元素其实一直都在 DOM 里，只是文本多了个字。
  // （用 `curl localhost:3456/eval` 直接读两条 timebar 的 textContent 列表实测确认，
  // 见 2026-09-04 debug session。）
  // 修法：匹配前去掉末尾的 时/分 后缀，两种文本形态都能命中。
  await ensurePanel('选时分前');          // 选完日期面板常会自己收起
  const hh = String(H).padStart(2, '0'), mm = String(MI).padStart(2, '0');
  await clickBy(cdp,
    `[...${POP}.querySelectorAll('.d-timepicker-timebar')[0].querySelectorAll('.d-timepicker-time')]
       .find(e => e.textContent.trim().replace(/[时分]$/, '') === '${hh}')`, `选 ${hh} 时`);
  await clickBy(cdp,
    `[...${POP}.querySelectorAll('.d-timepicker-timebar')[1].querySelectorAll('.d-timepicker-time')]
       .find(e => e.textContent.trim().replace(/[时分]$/, '') === '${mm}')`, `选 ${mm} 分`);
  console.log(`✅ 已选 ${hh}:${mm}`);

  // 5. 回读校验 —— 点了不等于选上了，一定要看时间框真的变成了目标值
  await sleep(600);
  const after = String(await readInput(cdp)).replace(/\n/g, ' ');
  const ok = after.includes(`${Y}-${String(MO).padStart(2, '0')}-${String(D).padStart(2, '0')}`) &&
             after.includes(`${hh}:${mm}`);
  console.log(`\n回读：${after.slice(0, 70)}`);
  if (!ok) {
    console.error(`⛔ 回读对不上目标 ${AT} —— 不点发布，交给人处理。`);
    cdp.close();
    process.exit(1);
  }
  console.log(`✅ 时间已设为 ${AT}`);

  // ⛔ 目标时间必须还在未来（北京时间）。小红书对「定时到过去」不报错，
  // 它会直接把稿**立刻发出去** —— 2026-08-06 实测：设北京 08-07 09:00 时
  // 北京已经 10:30，后台显示 10:36 直接发布，不是定时。
  // 失败得毫无声音，时段轮换的实验数据也全废，所以这里必须硬拦。
  const bjNow = new Date(Date.now() + (8 * 60 + new Date().getTimezoneOffset()) * 60000);
  const targetBj = new Date(Y, MO - 1, D, H, MI);
  if (targetBj - bjNow < 5 * 60000) {
    console.error(`⛔ 目标时间 ${AT} 不在未来（北京当前 ` +
      `${bjNow.getFullYear()}-${String(bjNow.getMonth() + 1).padStart(2, '0')}-` +
      `${String(bjNow.getDate()).padStart(2, '0')} ${String(bjNow.getHours()).padStart(2, '0')}:` +
      `${String(bjNow.getMinutes()).padStart(2, '0')}）。` +
      `\n   继续点发布会变成「立即发布」，不是定时。退出码 1，不发。`);
    cdp.close();
    process.exit(1);
  }

  if (!DO_PUBLISH) {
    console.log('\n（没带 --publish，到此为止。最后一步「点定时发布」没做。）');
    cdp.close();
    return;
  }

  // 提交不点按钮，派发按钮自己的事件。
  //
  // 提交按钮在 <xhs-publish-btn> 的 **closed shadow root** 里，页面里任何基于
  // document.querySelector / innerText 的查找都看不见它 —— 先是「找不到按钮」，
  // 正则放宽后又误匹配到左侧导航栏那个「发布笔记」（<div class="publish-video">），
  // 点下去只是跳到空白发布页，URL 变成 from=menu&target=video，
  // 看起来像发布失败，其实根本没点到发布。
  //
  // 曾经的解法是 CDP DOM.getDocument({pierce:true}) 穿透进去拿文本节点坐标，
  // 再配一条尺寸过滤（开关那行的「定时发布」标签是 56x17，真按钮 120x40，
  // 不过滤会点到标签上、把定时开关关掉），最后按坐标 dispatchMouseEvent。
  // 能用，但**依赖坐标和像素尺寸**：小红书改一次布局或按钮尺寸就会静默失效
  // —— 而静默失效正是这条链路反复出问题的方式。
  //
  // 2026-08-08 找到了不依赖坐标的路子。这个自定义元素的类定义里写着，
  // 它自己只是个事件源（customElements.get('xhs-publish-btn').toString()）：
  //   _onPublish = () => e.dispatchEvent(new CustomEvent("publish",{bubbles:!0,composed:!0}))
  //   _onSave    = () => e.dispatchEvent(new CustomEvent("save",   {bubbles:!0,composed:!0}))
  // 干活的是外面监听 publish 的那段。宿主元素本身在普通 DOM 里，
  // querySelector 找得到 —— 所以既不用穿透 shadow root，也不用碰坐标。
  //
  // ⛔ 只能派发 publish。save 是「暂存离开」，会把稿退回草稿箱，排期就没了。
  const label = '定时发布';
  const fired = await evaluate(cdp, `(() => {
    const h = document.querySelector('xhs-publish-btn');
    if (!h) return 'no-host';
    if (h.getAttribute('submit-disabled') === 'true') return 'disabled';
    h.dispatchEvent(new CustomEvent('publish', { bubbles: true, composed: true }));
    return 'fired:' + (h.getAttribute('submit-text') || '');
  })()`);
  if (fired === 'no-host') throw new Error('页面上找不到 <xhs-publish-btn>（页面结构可能已变）');
  if (fired === 'disabled') throw new Error('发布按钮处于禁用态，不派发事件');
  // submit-text 就是按钮上写的字。它要是变成「立即发布」，说明定时开关掉了，
  // 这时候发出去就是即时发布 —— 上面回读那一关本该拦住，这里再兜一道。
  if (!String(fired).includes('定时发布')) {
    throw new Error(`按钮文案是「${String(fired).replace('fired:', '')}」而不是「定时发布」，不发`);
  }
  console.log(`\n已派发 publish 事件（按钮文案「${label}」，未使用坐标点击）`);
  // ⛔ 点了 ≠ 发出去了。必须看到页面真的变了才算数 —— 退出码 0 会让调用方
  // （auto_publish 的 full_auto 分支）直接记 ✅ 并回填词库，记错比没记严重得多：
  // 稿会被当成已发布，闸门第 3 条从此永远拦着它，这篇就再也发不出去了。
  //
  // 两个信号任一成立即判成功：① URL 离开发布页；② 标题输入框被清空（表单已提交重置）。
  // 只看 URL 不够：小红书发完有时停在同一路由、只把表单重置掉。
  const titleJs = `([...document.querySelectorAll('input')]
      .find(i => (i.placeholder||'').includes('标题'))||{}).value || ''`;
  let published = false, url = '';
  for (let i = 0; i < 12; i++) {
    await sleep(1200);
    url = await evaluate(cdp, 'location.href');
    const nowTitle = await evaluate(cdp, titleJs);
    if (!url.includes('/publish/publish') || !String(nowTitle).trim()) { published = true; break; }
  }
  console.log(`\n点了「${label}」，当前 URL：${url.slice(0, 90)}`);
  if (!published) {
    console.error('⛔ 等了 14 秒，URL 没变、标题也还在 —— 判定未发布成功，退出码 1。');
    cdp.close();
    process.exit(1);
  }
  console.log('✅ 页面已跳走，判定发布成功');
  cdp.close();
}

main().catch(e => { console.error(`\n⛔ ${e.message}`); process.exit(1); });
