// Scrape Apple Store buy pages (US, India, UK, UAE, Canada, Australia, Japan, Singapore) for every configuration listed in scripts/configs.json.
//
// Usage:
//   node scripts/scrape.js us mac-mini        one page, one country -> prints JSON
//   node scripts/scrape.js all                every page, every country -> raw/<country>-<page>.json
//   COUNTRIES=uk,ae PAGES=imac node scripts/scrape.js all   limit the run
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
// Store URL per country. Add a country here and in COUNTRIES in scripts/build_prices.py.
const BASE = {
  us: 'https://www.apple.com/shop/', in: 'https://www.apple.com/in/shop/', uk: 'https://www.apple.com/uk/shop/',
  ae: 'https://www.apple.com/ae/shop/', ca: 'https://www.apple.com/ca/shop/', au: 'https://www.apple.com/au/shop/',
  jp: 'https://www.apple.com/jp/shop/', sg: 'https://www.apple.com/sg/shop/',
};
// Currency marker in front of prices on each store
const CUR = { us: '\\$', ca: '\\$', au: 'A\\$', uk: '£', ae: 'AED\\s?', in: '₹', sg: 'S\\$' };
const NUM = '([\\d,]+(?:\\.\\d\\d)?)';
function firstPrice(text, country) { // first full price that is not a monthly instalment
  const t = text.replace(/\s+/g, ' ');
  if (country === 'in') { const m = t.match(new RegExp('MRP\\s*₹\\s?' + NUM)); if (m) return num(m[1]); }
  if (country === 'jp') { // Japan writes the currency after the number: 219,800円, instalments as 6,105円/月
    const m = t.match(/(?:^|[^\d,.])([\d,]+)\s?円(?!\s?\/月)/);
    return m ? num(m[1]) : null;
  }
  const re = new RegExp('(?:^|[^A-Za-z])' + CUR[country] + '\\s?' + NUM + '(?![\\d.,]*\\/mo)', 'g');
  let m;
  while ((m = re.exec(t))) {
    if (country === 'us' || country === 'ca') { // "$" also appears inside "A$"; skip if preceded by A or C
      const before = t[m.index + (m[0].startsWith('$') ? 0 : 1) - 1];
      if (before === 'A') continue;
    }
    return num(m[1]);
  }
  return null;
}
const num = s => Math.round(parseFloat(s.replace(/,/g, '')));

function parseTotal(text, country) { return text ? firstPrice(text, country) : null; }

function parseLabel(text, country) { return firstPrice(text || '', country); }

async function scrapePage(browser, country, page) {
  const cfg = CONFIGS.pages[page];
  const url = BASE[country] + cfg.url;
  const p = await browser.newPage();
  const wait = ms => p.waitForTimeout(ms);
  const groups = () => p.evaluate(skip => {
    const m = {};
    document.querySelectorAll('input[type=radio]').forEach(i => {
      if (new RegExp(skip, 'i').test(i.name)) return;
      const lab = (document.querySelector(`label[for="${i.id}"]`) || i.closest('label') || {}).innerText || '';
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
  const totalTexts = () => p.evaluate(() => {
    const els = [...document.querySelectorAll('[data-autom="full-price"]')];
    if (!els.length) { const e = document.querySelector('.rc-prices-currentprice'); if (e) els.push(e); }
    return els.map(e => e.innerText);
  });

  const out = {};
  for (const [id, steps] of Object.entries(cfg.paths || { default: [] })) {
    let tries = 0;
    while (tries++ < 3) {
      try {
        await p.goto(url, { waitUntil: 'networkidle', timeout: 90000 });
        await wait(2500);
        if (!p.url().includes(cfg.url)) { out[id] = { error: 'not sold in this store (page redirects to ' + p.url() + ')' }; break; }
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
        const texts = await totalTexts();
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
          // Pages without options: the product's own price follows its monthly-instalment line. Smaller
          // add-ons (AppleCare) are listed first with their own, smaller instalments, so take the price
          // that follows the largest instalment.
          const lines = await p.evaluate(() => document.body.innerText.split('\n').map(s => s.trim()).filter(s => /(₹|\$|£|AED)\s?[\d,]|[\d,]+円/.test(s) && s.length < 140).slice(0, 12));
          let best = -1;
          lines.forEach((l, i) => {
            const mo = l.match(/(?:₹|\$|£|AED\s?)([\d,]+(?:\.\d\d)?)\/mo/) || l.match(/([\d,]+)円\/月/);
            if (!mo) return;
            const m = parseFloat(mo[1].replace(/,/g, ''));
            const sameLine = parseLabel(l.replace(/(?:₹|[AS]?\$|£|AED\s?)[\d,.]+\/mo\.?/, '').replace(/[\d,]+円\/月/, ''), country);
            const next = sameLine != null ? sameLine : parseLabel(lines[i + 1] || '', country);
            if (next != null && m > best) { best = m; total = next; from = 'page'; }
          });
          if (total == null) { // no instalment lines: the first price that is not a subscription
            const l = lines.find(l => !/\/(mo|yr|月|年)/.test(l) && parseLabel(l, country) != null);
            if (l) { total = parseLabel(l, country); from = 'page-first'; }
          }
        }
        out[id] = {
          total, totalFrom: from,
          totalText: text.replace(/\s+/g, ' ').trim().slice(0, 300),
          selected,
          options: Object.fromEntries(Object.entries(g).map(([n, vs]) => [n, vs.filter(x => !x.dis).map(x => x.lab)])),
          disabledOptions: Object.fromEntries(Object.entries(g).map(([n, vs]) => [n, vs.filter(x => x.dis).map(x => x.lab)]).filter(([, v]) => v.length)),
        };
        if (out[id].total != null) break;
      } catch (e) {
        out[id] = { error: String(e.message || e).slice(0, 200) };
      }
    }
  }
  await p.close();
  return { page, country, url, scrapedAt: new Date().toISOString(), configs: out };
}

module.exports = { firstPrice };
if (require.main === module) (async () => {
  const [mode, page] = process.argv.slice(2);
  const browser = await chromium.launch();
  if (mode === 'all') {
    fs.mkdirSync(path.join(ROOT, 'raw'), { recursive: true });
    const countries = (process.env.COUNTRIES || Object.keys(BASE).join(',')).split(',');
    const pages = process.env.PAGES ? process.env.PAGES.split(',') : Object.keys(CONFIGS.pages);
    const jobs = pages.flatMap(pg => countries.map(c => [c, pg]));
    const workers = Number(process.env.JOBS || 4);
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
