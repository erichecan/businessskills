#!/usr/bin/env node
// 日历组件点击探测器 —— 回答一个问题：小红书发布页的日期选择面板，到底怎样才点得开。
//
// 为什么单独写：auto_publish.py 顶部写着「CDP Input.dispatchMouseEvent 试过，元素能点中
// 但面板不展开」。但现有的 /clickAt 只发了 mousePressed + mouseReleased ——
// 没有前置 mouseMoved（组件收不到 hover/pointerenter）、没带 buttons 参数、
// 两个事件之间没有任何延迟（真人按下到抬起有几十毫秒）。所以「CDP 试过了」这句话
// 其实只覆盖了 CDP 能做的一小部分。这个脚本把剩下的部分逐条试完，试到为止。
//
// 它不改任何数据，只点击和观察；每一步截图存证。
//
// 前置条件：
//   1. Chrome 打开 chrome://inspect/#remote-debugging 勾上 Allow remote debugging
//      （Chrome 150 起端口默认监听但不应答，不勾这一步全套自动化都连不上）
//   2. 发布页上已经有一篇预填好的稿，且定时开关已打开（否则日历区域根本不存在）
//
// 用法：
//   node scripts/xhs-publish/click_probe.mjs            # 自动找发布页 tab
//   node scripts/xhs-publish/click_probe.mjs --dump     # 只导出 DOM 结构，不点任何东西
//   node scripts/xhs-publish/click_probe.mjs --shots /tmp/probe

import fs from 'node:fs';
import path from 'node:path';

const PORT = Number(process.env.CHROME_DEBUG_PORT || 9222);
const args = process.argv.slice(2);
const DUMP_ONLY = args.includes('--dump');
const SHOT_DIR = (() => {
  const i = args.indexOf('--shots');
  return i >= 0 && args[i + 1] ? args[i + 1] : '/tmp/xhs-click-probe';
})();

const sleep = ms => new Promise(r => setTimeout(r, ms));

// ── CDP 连接 ────────────────────────────────────────────────────────────────

// ⛔ 不要用 http://localhost:9222/json/list —— Chrome 150 把 HTTP 发现端点全关了，
// 一律回 404（2026-08-06 实测：/json/version、/json、/ 都是 404，端口却在 LISTEN）。
// 唯一还通的入口是 DevToolsActivePort 文件里那个 browser WebSocket 路径，
// 连上之后用 Target.getTargets 拿页面列表、Target.attachToTarget 拿 sessionId。
async function browserWsUrl() {
  // 专用实例（com.eric.xhschrome，端口 9333）优先：命令行带 --remote-debugging-port 起的，
  // HTTP 端点正常应答，不需要那个会过期的 chrome://inspect 开关。
  const port = Number(process.env.CHROME_DEBUG_PORT || 9333);
  try {
    const r = await fetch(`http://127.0.0.1:${port}/json/version`, { signal: AbortSignal.timeout(2500) });
    const j = await r.json();
    if (j.webSocketDebuggerUrl) return j.webSocketDebuggerUrl;
  } catch { /* 没起，兜底到日常 Chrome */ }

  const files = [
    path.join(process.env.HOME, 'Library/Application Support/Google/Chrome/DevToolsActivePort'),
    path.join(process.env.HOME, 'Library/Application Support/Google/Chrome Canary/DevToolsActivePort'),
    path.join(process.env.HOME, 'Library/Application Support/Chromium/DevToolsActivePort'),
  ];
  for (const f of files) {
    if (!fs.existsSync(f)) continue;
    const [p2, wsPath] = fs.readFileSync(f, 'utf8').trim().split('\n');
    if (p2 && wsPath) return `ws://127.0.0.1:${p2}${wsPath}`;
  }
  throw new Error(`连不上 Chrome：专用实例（端口 ${port}）没应答，日常 Chrome 也没有可用入口。
→ 检查 launchctl list com.eric.xhschrome`);
}

function connect(wsUrl) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(wsUrl);
    const pending = new Map();
    let id = 0;
    ws.addEventListener('open', () => resolve({
      // sessionId 走「平铺模式」：attachToTarget 时传 flatten:true，之后每条消息带上
      // sessionId 即可，不用 Target.sendMessageToTarget 那套嵌套 JSON。
      send(method, params = {}, sessionId) {
        const mid = ++id;
        return new Promise((res, rej) => {
          pending.set(mid, { res, rej });
          ws.send(JSON.stringify(sessionId ? { id: mid, method, params, sessionId }
                                           : { id: mid, method, params }));
          setTimeout(() => pending.has(mid) && (pending.delete(mid), rej(new Error(`${method} 超时`))), 20000);
        });
      },
      close: () => ws.close(),
    }));
    ws.addEventListener('error', e => reject(new Error(
      `WebSocket 连接失败：${e.message || e.type}\n` +
      `→ 确认 chrome://inspect/#remote-debugging 里 Allow remote debugging 是勾上的。`)));
    ws.addEventListener('message', ev => {
      const msg = JSON.parse(ev.data);
      if (msg.id && pending.has(msg.id)) {
        const { res, rej } = pending.get(msg.id);
        pending.delete(msg.id);
        msg.error ? rej(new Error(JSON.stringify(msg.error))) : res(msg.result);
      }
    });
  });
}

/** 连上浏览器 → 找到发布页 → attach，返回一个只对那个页面说话的薄封装。 */
async function attachToPublishPage(wantTid) {
  const raw = await connect(await browserWsUrl());
  const { targetInfos } = await raw.send('Target.getTargets');
  const pages = targetInfos.filter(t => t.type === 'page');
  const t = wantTid ? pages.find(p => p.targetId === wantTid)
                    : pages.find(p => /creator\.xiaohongshu\.com\/publish/.test(p.url || ''));
  if (!t) {
    raw.close();
    const urls = pages.map(p => `  · ${p.url.slice(0, 80)}`).join('\n');
    throw new Error(
      (wantTid ? `没找到 targetId=${wantTid} 的页面。` : '没找到小红书发布页 tab。') +
      `\n当前页面：\n${urls || '  （一个都没有）'}` +
      `\n→ 先跑 python3 scripts/xhs-publish/prep_probe_page.py 备一个页面，或用 --auto。`);
  }
  const { sessionId } = await raw.send('Target.attachToTarget', { targetId: t.targetId, flatten: true });
  return {
    info: t,
    send: (m, p) => raw.send(m, p, sessionId),
    close: () => raw.close(),
  };
}

async function evaluate(cdp, expression) {
  const r = await cdp.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || '页面求值异常');
  return r.result?.value;
}

// ── 页面侧探针 ──────────────────────────────────────────────────────────────

// 「面板到底展没展开」的判据：整页扫一遍疑似弹层，只认**真的占了地方**的那些。
// 不写死 .d-datepicker —— 组件库改个类名，写死的判据会安静地永远返回「没开」。
const PANEL_PROBE = `(() => {
  const vis = el => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 80 && r.height > 80 && s.visibility !== 'hidden' && s.display !== 'none'
           && Number(s.opacity) > 0.1;
  };
  const hits = [...document.querySelectorAll(
    '[class*=picker],[class*=Picker],[class*=calendar],[class*=Calendar],' +
    '[class*=popper],[class*=popover],[class*=dropdown],[class*=panel]')]
    .filter(vis)
    .map(el => ({ cls: el.className.toString().slice(0, 90), w: Math.round(el.getBoundingClientRect().width),
                  h: Math.round(el.getBoundingClientRect().height),
                  txt: (el.innerText || '').replace(/\\s+/g, ' ').slice(0, 60) }));
  // 日历面板一定含有 1..28 这些日子数字，用它把普通下拉框和真日历分开
  const calendarish = hits.filter(x => /\\b(1[0-9]|2[0-8])\\b/.test(x.txt) || /周[一二三日]|Mo|Su/.test(x.txt));
  return { count: hits.length, calendarish: calendarish.length, hits: hits.slice(0, 6) };
})()`;

// 定时区域的真实 DOM 长什么样 —— 选择器全靠它，不靠猜
const DUMP_JS = `(() => {
  const w = document.querySelector('.post-time-wrapper');
  if (!w) return { error: '页面上没有 .post-time-wrapper（定时开关没打开？还是没在发布页？）' };
  const walk = (el, depth) => {
    if (depth > 4) return null;
    const r = el.getBoundingClientRect();
    return {
      tag: el.tagName.toLowerCase(),
      cls: el.className.toString().slice(0, 80),
      txt: (el.childElementCount ? '' : (el.textContent || '').trim().slice(0, 40)),
      box: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
      kids: [...el.children].map(c => walk(c, depth + 1)).filter(Boolean),
    };
  };
  return { tree: walk(w, 0), html: w.outerHTML.slice(0, 3000) };
})()`;

/** 找到「时间那一块」的点击目标，返回视口坐标。优先真正显示时间文字的那个元素。 */
const TARGET_JS = `(() => {
  const w = document.querySelector('.post-time-wrapper');
  if (!w) return { error: '没有 .post-time-wrapper' };
  const cands = [...w.querySelectorAll('*')].filter(el => {
    const r = el.getBoundingClientRect();
    if (r.width < 40 || r.height < 14) return false;
    const t = (el.innerText || '').trim();
    return /\\d{1,4}[-/年]\\d{1,2}|\\d{1,2}:\\d{2}/.test(t) && el.childElementCount <= 2;
  });
  const el = cands[cands.length - 1] || w.querySelector('input') || w;
  el.scrollIntoView({ block: 'center', behavior: 'instant' });
  const r = el.getBoundingClientRect();
  return { x: r.x + r.width / 2, y: r.y + r.height / 2, left: r.x + 10, top: r.y + r.height / 2,
           tag: el.tagName.toLowerCase(), cls: el.className.toString().slice(0, 80),
           txt: (el.innerText || '').trim().slice(0, 40) };
})()`;

// ── 点击策略 ────────────────────────────────────────────────────────────────

const mouse = (cdp, type, x, y, extra = {}) =>
  cdp.send('Input.dispatchMouseEvent', {
    type, x, y, button: 'left', clickCount: 1, pointerType: 'mouse', ...extra,
  });

const STRATEGIES = [
  {
    name: 'S1 JS el.click()',
    note: '最朴素的一种，isTrusted=false',
    async run(cdp, t) {
      const hit = await evaluate(cdp, `(() => {
        const el = document.elementFromPoint(${t.x}, ${t.y});
        if (!el) return 'elementFromPoint 没命中';
        el.click();
        return el.tagName.toLowerCase();
      })()`);
      console.log(`     └ 点了 <${hit}>`);
    },
  },
  {
    name: 'S2 CDP press+release（现有 /clickAt 的行为）',
    note: '没有 mouseMoved、没有 buttons、无延迟 —— 这是此前唯一试过的 CDP 路径',
    async run(cdp, t) {
      await mouse(cdp, 'mousePressed', t.x, t.y);
      await mouse(cdp, 'mouseReleased', t.x, t.y);
    },
  },
  {
    name: 'S3 完整手势：moved → 延迟 → pressed(buttons:1) → 延迟 → released',
    note: '补齐 hover/pointerenter 与按压时长，最像真人的一种',
    async run(cdp, t) {
      await mouse(cdp, 'mouseMoved', t.x - 40, t.y - 20, { buttons: 0 });
      await sleep(120);
      await mouse(cdp, 'mouseMoved', t.x, t.y, { buttons: 0 });
      await sleep(180);
      await mouse(cdp, 'mousePressed', t.x, t.y, { buttons: 1 });
      await sleep(90);
      await mouse(cdp, 'mouseReleased', t.x, t.y, { buttons: 0 });
    },
  },
  {
    name: 'S4 同 S3，但点文字左端而非容器中心',
    note: '有些组件的可点区只覆盖文字，容器中心可能落在 padding 上',
    async run(cdp, t) {
      await mouse(cdp, 'mouseMoved', t.left, t.top, { buttons: 0 });
      await sleep(150);
      await mouse(cdp, 'mousePressed', t.left, t.top, { buttons: 1 });
      await sleep(90);
      await mouse(cdp, 'mouseReleased', t.left, t.top, { buttons: 0 });
    },
  },
  {
    name: 'S5 点最内层命中元素（elementFromPoint）',
    note: '容器可能不是事件绑定方，真正的监听者是它下面某个 span',
    async run(cdp, t) {
      const inner = await evaluate(cdp, `(() => {
        const el = document.elementFromPoint(${t.x}, ${t.y});
        if (!el) return null;
        const r = el.getBoundingClientRect();
        return { x: r.x + r.width / 2, y: r.y + r.height / 2,
                 tag: el.tagName.toLowerCase(), cls: el.className.toString().slice(0, 60) };
      })()`);
      if (!inner) throw new Error('elementFromPoint 没命中任何元素');
      console.log(`     └ 命中 <${inner.tag} class="${inner.cls}">`);
      await mouse(cdp, 'mouseMoved', inner.x, inner.y, { buttons: 0 });
      await sleep(150);
      await mouse(cdp, 'mousePressed', inner.x, inner.y, { buttons: 1 });
      await sleep(90);
      await mouse(cdp, 'mouseReleased', inner.x, inner.y, { buttons: 0 });
    },
  },
  {
    name: 'S6 键盘：focus 后按 Enter',
    note: '可访问性路径。组件若支持键盘打开，这条最稳，且完全绕开坐标问题',
    async run(cdp, t) {
      await evaluate(cdp, `(() => {
        const el = document.elementFromPoint(${t.x}, ${t.y});
        const f = el?.closest('input,[tabindex],[role=button]') || el;
        f?.focus?.(); return f?.tagName || 'none';
      })()`);
      await sleep(120);
      for (const type of ['keyDown', 'keyUp']) {
        await cdp.send('Input.dispatchKeyEvent', {
          type, key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13,
        });
        await sleep(60);
      }
    },
  },
  {
    name: 'S7 双击',
    note: '少数组件只在 dblclick 上开面板',
    async run(cdp, t) {
      await mouse(cdp, 'mouseMoved', t.x, t.y, { buttons: 0 });
      await sleep(100);
      for (const cc of [1, 2]) {
        await mouse(cdp, 'mousePressed', t.x, t.y, { buttons: 1, clickCount: cc });
        await sleep(60);
        await mouse(cdp, 'mouseReleased', t.x, t.y, { buttons: 0, clickCount: cc });
        await sleep(60);
      }
    },
  },
];

// ── 主流程 ──────────────────────────────────────────────────────────────────

async function shot(cdp, name) {
  fs.mkdirSync(SHOT_DIR, { recursive: true });
  const r = await cdp.send('Page.captureScreenshot', { format: 'png' });
  const f = path.join(SHOT_DIR, `${name}.png`);
  fs.writeFileSync(f, Buffer.from(r.data, 'base64'));
  return f;
}

/** 每次尝试后把页面恢复原状：按 Esc + 点一个远离日历的空白处 */
async function reset(cdp) {
  for (const type of ['keyDown', 'keyUp']) {
    await cdp.send('Input.dispatchKeyEvent', {
      type, key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27, nativeVirtualKeyCode: 27,
    }).catch(() => {});
  }
  await mouse(cdp, 'mousePressed', 12, 12).catch(() => {});
  await mouse(cdp, 'mouseReleased', 12, 12).catch(() => {});
  await sleep(400);
}

/** --auto：自己把前置状态备好（预填 + 打开定时开关），省掉「先手工干 5 分钟才能开始探测」。 */
async function autoPrepare() {
  const { spawnSync } = await import('node:child_process');
  const script = path.join(path.dirname(new URL(import.meta.url).pathname), 'prep_probe_page.py');
  console.log('▶ 自动备页（预填一篇 + 打开定时开关）…\n');
  const r = spawnSync('python3', [script], { encoding: 'utf8' });
  const out = (r.stdout || '') + (r.stderr || '');
  console.log(out.split('\n').map(l => '   ' + l).join('\n'));
  if (r.status !== 0) throw new Error('备页失败，看上面的日志');
  const m = out.match(/tid=([0-9A-F]+)/i);
  if (!m) throw new Error('备页脚本没输出 tid');
  return m[1];
}

const main = async () => {
  const tid = args.includes('--auto') ? await autoPrepare() : null;
  const cdp = await attachToPublishPage(tid);
  console.log(`目标 tab：${cdp.info.title}\n         ${cdp.info.url}\n`);
  await cdp.send('Runtime.enable');
  await cdp.send('Page.enable');

  const dump = await evaluate(cdp, DUMP_JS);
  if (dump.error) {
    console.error(`⛔ ${dump.error}`);
    console.error('   先跑 auto_publish.py 预填一篇并打开定时开关，再回来跑本脚本。');
    cdp.close();
    process.exit(2);
  }
  fs.mkdirSync(SHOT_DIR, { recursive: true });
  const dumpFile = path.join(SHOT_DIR, 'post-time-wrapper.json');
  fs.writeFileSync(dumpFile, JSON.stringify(dump, null, 2));
  console.log(`定时区域 DOM 已导出 → ${dumpFile}`);
  const line = (n, d = 0) => {
    console.log(`${'  '.repeat(d + 1)}<${n.tag}${n.cls ? ` class="${n.cls}"` : ''}>` +
                `${n.txt ? ` "${n.txt}"` : ''}  [${n.box.join(',')}]`);
    n.kids.forEach(k => line(k, d + 1));
  };
  line(dump.tree);

  if (DUMP_ONLY) { cdp.close(); return; }

  const t = await evaluate(cdp, TARGET_JS);
  if (t.error) { console.error(`⛔ ${t.error}`); cdp.close(); process.exit(2); }
  console.log(`\n点击目标：<${t.tag} class="${t.cls}"> "${t.txt}"  @ (${Math.round(t.x)}, ${Math.round(t.y)})`);

  const before = await evaluate(cdp, PANEL_PROBE);
  console.log(`基线：可见弹层 ${before.count} 个，其中像日历的 ${before.calendarish} 个\n`);

  const results = [];
  for (const s of STRATEGIES) {
    process.stdout.write(`▶ ${s.name}\n     ${s.note}\n`);
    let err = null;
    try { await s.run(cdp, t); } catch (e) { err = e.message; }
    await sleep(900);
    const after = await evaluate(cdp, PANEL_PROBE).catch(e => ({ count: -1, calendarish: -1, hits: [], err: e.message }));
    const opened = after.calendarish > before.calendarish ||
                   (after.count > before.count && after.calendarish > 0);
    const f = await shot(cdp, s.name.split(' ')[0]);
    results.push({ name: s.name, opened, after, err, shot: f });
    console.log(`     ${opened ? '✅ 面板展开了' : '❌ 没展开'}` +
                `（弹层 ${before.count}→${after.count}，日历样 ${before.calendarish}→${after.calendarish}）` +
                `${err ? `　执行报错：${err}` : ''}`);
    if (opened) {
      console.log(`     结构：${JSON.stringify(after.hits.slice(0, 2))}`);
      console.log(`     截图：${f}`);
      break;
    }
    await reset(cdp);
  }

  console.log('\n══ 结论 ══');
  const win = results.find(r => r.opened);
  if (win) {
    console.log(`✅ ${win.name} 能打开日历面板。`);
    console.log(`   下一步：把这套事件序列搬进 auto_publish.py，接着解决「在面板里选中日期和时分」。`);
  } else {
    console.log('❌ 七种策略全部没能展开面板。');
    console.log('   下一步只剩系统级真事件（cliclick / CGEvent，需要辅助功能授权），');
    console.log(`   或改走小红书的发布接口。逐条截图在 ${SHOT_DIR}/，先看它们是不是真点在了时间上。`);
  }
  cdp.close();
};

main().catch(e => { console.error(`\n⛔ ${e.message}`); process.exit(1); });
