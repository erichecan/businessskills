// Loaded through NODE_OPTIONS for collection commands only. No package edits.
const settings = JSON.parse(process.env.XHS_PACING_SETTINGS);
const { Page } = await import(process.env.XHS_PACING_PAGE_MODULE);
const sleep = seconds => new Promise(resolve => setTimeout(resolve, seconds * 1000));
export const randomDelay = base => base * (1 + Math.random() * (settings.jitter_ratio ?? 0.5));
const goto = Page.prototype.goto;
Page.prototype.goto = async function (url, ...args) {
  const result = await goto.call(this, url, ...args);
  if (/^https:\/\/(www\.)?(xiaohongshu\.com|rednote\.com)\//.test(url)) {
    this._collectionPaced = true;
    await sleep(randomDelay(url.includes('/search_result') ? settings.search_dwell_seconds : settings.note_dwell_seconds));
  }
  return result;
};
const evaluate = Page.prototype.evaluate;
Page.prototype.evaluate = async function (input, ...args) {
  // Bound the existing search adapter's lazy-loading loop as well as goto().
  // It otherwise scrolls again just 200 ms after the results change.
  if (this._collectionPaced && typeof input === 'string' && input.includes('const lastHeight = document.body.scrollHeight;')) {
    input = input.replace('window.scrollTo(0, lastHeight);',
      `await new Promise(resolve => setTimeout(resolve, ${settings.scroll_step_seconds * 1000} * (1 + Math.random() * ${settings.jitter_ratio ?? 0.5}))); window.scrollTo(0, lastHeight);`);
  }
  const result = await evaluate.call(this, input, ...args);
  // Keep the page visible after extraction, even if the CLI releases its lease
  // without calling Page.closeWindow().
  if (this._collectionPaced) await sleep(randomDelay(settings.close_delay_seconds));
  return result;
};
for (const name of ['closeWindow', 'closeTab']) {
  const original = Page.prototype[name];
  Page.prototype[name] = async function (...args) {
    if (this._collectionPaced) await sleep(randomDelay(settings.close_delay_seconds));
    return original.apply(this, args);
  };
}
