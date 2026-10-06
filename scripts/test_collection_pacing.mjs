import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { pathToFileURL } from 'node:url';
const dir = mkdtempSync(`${tmpdir()}/collection-hook-`);
try {
 const fixture = `${dir}/fake-page.mjs`;
 writeFileSync(fixture, 'export class Page {async goto(){} async evaluate(input){return input} async closeWindow(){} async closeTab(){}}');
 process.env.XHS_PACING_PAGE_MODULE = pathToFileURL(fixture).href;
 process.env.XHS_PACING_SETTINGS = JSON.stringify({search_dwell_seconds:.03,note_dwell_seconds:.05,close_delay_seconds:.02,scroll_step_seconds:5,jitter_ratio:.5});
 const {randomDelay} = await import(new URL('./collection_pacing.mjs', import.meta.url).href);
 const samples = Array.from({length:100}, () => randomDelay(20));
 assert(samples.every(v => v >= 20 && v <= 30));assert(new Set(samples).size > 1);
 const {Page} = await import(process.env.XHS_PACING_PAGE_MODULE);
 const p = new Page();
 let start=performance.now();await p.goto('https://www.xiaohongshu.com/search_result?keyword=test');assert(performance.now()-start>=25);
 start=performance.now();await p.goto('https://www.xiaohongshu.com/explore/test');assert(performance.now()-start>=45);
 start=performance.now();const input=await p.evaluate('const lastHeight = document.body.scrollHeight; window.scrollTo(0, lastHeight);');assert(performance.now()-start>=15);assert(input.includes('5000 * (1 + Math.random() * 0.5)'));
 start=performance.now();await p.closeWindow();assert(performance.now()-start>=15);
 const other=new Page();start=performance.now();await other.goto('https://example.com/');assert(performance.now()-start<20);
 console.log('搜索/正文停留、提取后等待、滚动限速及关闭等待：通过；其他站点不受影响');
} finally {rmSync(dir,{recursive:true,force:true});}
