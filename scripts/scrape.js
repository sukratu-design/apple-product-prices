// Scrape the Apple Store buy pages in scripts/configs.json for every store in scripts/countries.json.
//
// Usage:
//   node scripts/scrape.js us mac-mini        one page, one country -> prints JSON
//   node scripts/scrape.js all                every page, every country -> raw/<country>-<page>.json
//   COUNTRIES=uk,ae PAGES=imac node scripts/scrape.js all   limit the run
//   FRESH_HOURS=12 node scripts/scrape.js all             resume: skip pages scraped successfully in the last 12 hours
//
// For each configuration path the script loads the page, clicks the listed options in order (filling any
// other required choice, such as colour, with its first enabled value), then records:
//   total   - the configured price read from the page's price summary (number)
//   options - every option group's labels; Apple prints the price difference on each label
//             ("24GB + $200.00", "512GB − $300.00", "1TB" = currently selected)
// Requires Node and Playwright with Chromium.
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const CONFIGS = JSON.parse(fs.readFileSync(path.join(__dirname, 'configs.json'), 'utf8'));
const SKIP = /trade|purchase|Payment|applecare|carrier|software/i;
// Stores, currency markers and number formats: scripts/countries.json
const COUNTRIES = JSON.parse(fs.readFileSync(path.join(__dirname, 'countries.json'), 'utf8')).countries;
const BASE = Object.fromEntries(Object.entries(COUNTRIES).map(([k, c]) => [k, c.store]));

// A price is a number written next to the store's currency marker, before or after it, in any local format:
// 1,199  1.199  1 199  1'199  1199.–  1.549.990  10.999,00  ₩340,000  38.999.000đ  219,800円
const NUM = String.raw`\d{1,3}(?:[.,\s  '’]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?`;
function toNumber(s) {
  s = s.replace(/[\s  '’]/g, '');
  const dec = s.match(/[.,](\d{1,2})$/);
  const whole = (dec ? s.slice(0, -dec[0].length) : s).replace(/[.,]/g, '');
  return Math.round(parseFloat(whole + (dec ? '.' + dec[1] : '')));
}
// Footnote markers ("Footnote 1", "脚注 1", "Lábjegyzet 1") sit right before prices on some stores;
// without removing them "脚注 1 219,800円" would read as 1,219,800.
const FOOTNOTE = new RegExp(String.raw`(?:Footnote|Fußnote|Note de bas de page|Nota al pie|Nota a pie de página|Nota de rodapé|Nota|Voetnoot|Fotnot|Fotnote|Fodnote|Alaviite|Przypis|Poznámka|Lábjegyzet|Dipnot|Dipnotu|حاشية|각주|脚注|註腳|註解|เชิงอรรถ|Chú thích)\s*[\d①-⑳*†‡§◊※#]+`, 'gu');
// Model names with a number ("AirPods 5 149,00 €") would otherwise merge with the price as 5 149,00.
const MODEL = /(?:AirPods|HomePod|iPhone|iPad|Watch|Series|Ultra|SE|Pro|Max|mini|Air)\s\d{1,2}(?![\d.,])/gu;
function pricesIn(text, country) {
  const t = String(text || '').replace(FOOTNOTE, ' ').replace(MODEL, ' ').replace(/\s+/g, ' '), m = COUNTRIES[country].marker, out = [];
  for (const re of [new RegExp(`(?:${m})\\s?(${NUM})`, 'gu'), new RegExp(`(${NUM})\\s?(?:${m})`, 'gu')]) {
    let x; while ((x = re.exec(t))) out.push(toNumber(x[1]));
  }
  return out.filter(n => n > 0);
}
// Labels and price boxes hold the full price plus smaller amounts (monthly instalments, lease, AppleCare),
// so the full price is the largest amount. This works the same in every language.
function maxPrice(text, country) { const v = pricesIn(text, country); return v.length ? Math.max(...v) : null; }
const parseTotal = maxPrice, parseLabel = maxPrice;

async function scrapePage(browser, country, page) {
  const cfg = CONFIGS.pages[page];
  const url = BASE[country] + cfg.url;
  const p = await browser.newPage();
  // prices are text; skipping images, video and fonts keeps the pages light enough to load several at once
  await p.route('**/*', r => ['image', 'media', 'font'].includes(r.request().resourceType()) ? r.abort() : r.continue());
  const wait = ms => p.waitForTimeout(ms);
  const groups = () => p.evaluate(skip => {
    const m = {};
    document.querySelectorAll('input[type=radio]').forEach(i => {
      if (new RegExp(skip, 'i').test(i.name)) return;
      const el = document.querySelector(`label[for="${i.id}"]`) || i.closest('label');
      let lab = '';
      if (el) { const c = el.cloneNode(true); c.querySelectorAll('sup, .footnote, [class*="footnote"]').forEach(x => x.remove()); document.body.appendChild(c); lab = c.innerText || el.innerText || ''; c.remove(); }
      (m[i.name] = m[i.name] || []).push({ v: i.value, dis: i.disabled, chk: i.checked, lab: lab.replace(/\s+/g, ' ').trim().slice(0, 400) });
    });
    return m;
  }, SKIP.source);
  const click = async (n, v) => {
    const ok = await p.evaluate(([n, v]) => {
      const i = document.querySelector(`input[name="${n}"][value="${v}"]`);
      if (!i || i.disabled) return false;
      (document.querySelector(`label[for="${i.id}"]`) || i).click();
      return true;
    }, [n, v]);
    await wait(2200);
    return ok;
  };
  const fillOne = async (except) => { // choose the first enabled value of one unanswered group
    const g = await groups();
    for (const [n, vs] of Object.entries(g)) {
      if (except && except.test(n)) continue;
      if (vs.some(x => x.chk)) continue;
      const f = vs.find(x => !x.dis);
      if (f) { await click(n, f.v); return true; }
    }
    return false;
  };
  // Some pages show an AppleCare price box before the product's own; AppleCare is always cheaper,
  // so the summary price is the largest one on the page.
  // single: also read "fullPrice" boxes, which hold the product's MRP on India's one-configuration pages
  const totalTexts = (single = false) => p.evaluate(single => {
    // summaryHeroPrice: some stores (Italy, Spain) show the product price only there, with AppleCare in full-price
    const els = [...document.querySelectorAll('[data-autom="full-price"], [data-autom="summaryHeroPrice"]' + (single ? ', [data-autom="fullPrice"]' : ''))];
    if (!els.length) { const e = document.querySelector('.rc-prices-currentprice'); if (e) els.push(e); }
    return els.map(e => e.innerText);
  }, single);

  const out = {};
  for (const [id, steps] of Object.entries(cfg.paths || { default: [] })) {
    let tries = 0;
    while (tries++ < 3) {
      try {
        await p.goto(url, { waitUntil: 'networkidle', timeout: 90000 });
        await wait(2500);
        if (!p.url().includes(cfg.url)) { out[id] = { error: 'not sold in this store (page redirects to ' + p.url() + ')' }; break; }
        // On pages with a single configuration, choosing a colour can remove the product's price box (India),
        // so read the boxes before any choice as well.
        const initial = steps.length ? [] : await totalTexts(true);
        let missing = null;
        for (const [n, v] of steps) {
          // a group may stay disabled until an earlier choice (such as colour) is made
          let ok = await click(n, v);
          for (let k = 0; !ok && k < 6; k++) { if (!(await fillOne(new RegExp('^' + n + '$')))) break; ok = await click(n, v); }
          if (!ok) { missing = `${n}=${v}`; break; }
        }
        if (missing) { out[id] = { error: 'option not available: ' + missing }; break; }
        for (let k = 0; k < 12; k++) if (!(await fillOne())) break;
        const g = await groups();
        const texts = (await totalTexts(!steps.length)).concat(initial);
        const parsed = texts.map(t => parseTotal(t, country)).filter(v => v != null);
        const text = texts.join(' | ');
        const selected = Object.fromEntries(Object.entries(g).map(([n, vs]) => [n, (vs.find(x => x.chk) || {}).lab || null]));
        // India shows only the EMI in the summary; the MRP is printed on the selected option's label
        let total = parsed.length ? Math.max(...parsed) : null, from = 'summary';
        if (total == null) {
          const order = [...steps.map(s => s[0]).reverse(), ...Object.keys(selected)];
          for (const n of order) { const v = parseLabel(selected[n] || '', country); if (v != null) { total = v; from = 'label:' + n; break; } }
        }
        if (total == null) {
          // Pages with no price box: the first line that holds one price and no per-period amount ("/mo", "/月")
          const lines = await p.evaluate(() => document.body.innerText.split('\n').map(s => s.trim()).filter(s => s && s.length < 140).slice(0, 400));
          const line = lines.find(l => !l.includes('/') && pricesIn(l, country).length === 1);
          if (line) { total = pricesIn(line, country)[0]; from = 'page-first'; }
        }
        out[id] = {
          total, totalFrom: from,
          totalText: text.replace(/\s+/g, ' ').trim().slice(0, 300),
          selected,
          options: Object.fromEntries(Object.entries(g).map(([n, vs]) => [n, vs.filter(x => !x.dis).map(x => x.lab)])),
          disabledOptions: Object.fromEntries(Object.entries(g).map(([n, vs]) => [n, vs.filter(x => x.dis).map(x => x.lab)]).filter(([, v]) => v.length)),
        };
        // a configuration whose memory/storage options never enabled means the page did not finish switching: retry
        const stuck = steps.length && Object.keys(out[id].disabledOptions || {}).some(n => /Memory|Capacity/.test(n) && !(out[id].options[n] || []).length);
        if (out[id].total != null && !stuck) break;
      } catch (e) {
        out[id] = { error: String(e.message || e).slice(0, 200) };
      }
    }
  }
  await p.close();
  return { page, country, url, scrapedAt: new Date().toISOString(), configs: out };
}

module.exports = { pricesIn, maxPrice, toNumber };
if (require.main === module) (async () => {
  const [mode, page] = process.argv.slice(2);
  const browser = await chromium.launch();
  if (mode === 'all') {
    fs.mkdirSync(path.join(ROOT, 'raw'), { recursive: true });
    const countries = (process.env.COUNTRIES || Object.keys(BASE).join(',')).split(',');
    const pages = process.env.PAGES ? process.env.PAGES.split(',') : Object.keys(CONFIGS.pages);
    // FRESH_HOURS=n skips pages already scraped successfully in the last n hours (to resume an interrupted run)
    const fresh = Number(process.env.FRESH_HOURS || 0) * 3600e3;
    const done = (c, pg) => {
      if (!fresh) return false;
      const f = path.join(ROOT, 'raw', `${c}-${pg}.json`);
      try {
        if (Date.now() - fs.statSync(f).mtimeMs > fresh) return false;
        return Object.values(JSON.parse(fs.readFileSync(f, 'utf8')).configs).every(v => v.total != null || /not sold/.test(v.error || ''));
      } catch (e) { return false; }
    };
    const jobs = pages.flatMap(pg => countries.map(c => [c, pg])).filter(([c, pg]) => !done(c, pg));
    console.log(`${jobs.length} pages to scrape`);
    const workers = Number(process.env.JOBS || 8);
    let next = 0;
    await Promise.all(Array.from({ length: workers }, async () => {
      while (next < jobs.length) {
        const [c, pg] = jobs[next++];
        const res = await scrapePage(browser, c, pg);
        fs.writeFileSync(path.join(ROOT, 'raw', `${c}-${pg}.json`), JSON.stringify(res, null, 1));
        const bad = Object.entries(res.configs).filter(([, v]) => v.total == null).map(([k]) => k);
        console.log(`${c} ${pg}: ${Object.keys(res.configs).length} configs${bad.length ? ' (no total: ' + bad.join(', ') + ')' : ''}`);
      }
    }));
  } else {
    console.log(JSON.stringify(await scrapePage(browser, mode, page), null, 1));
  }
  await browser.close();
})();
