// Scrape Apple Store buy pages (US and India) for every configuration listed in scripts/configs.json.
//
// Usage:
//   node scripts/scrape.js us mac-mini        one page, one country -> prints JSON
//   node scripts/scrape.js all                every page, both countries -> raw/<country>-<page>.json
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
const BASE = { us: 'https://www.apple.com/shop/', in: 'https://www.apple.com/in/shop/' };

function parseTotal(text, country) {
  if (!text) return null;
  const t = text.replace(/\s+/g, ' ');
  const m = country === 'in'
    ? t.match(/MRP\s*₹\s?([\d,]+(?:\.\d\d)?)/) || t.match(/(?:^|or\s)₹\s?([\d,]+(?:\.\d\d)?)(?!\/mo|\.\d*\/mo)(?:\s|$)/)
    : t.match(/(?:Buy (?:for|from)\s*)?\$([\d,]+(?:\.\d\d)?)(?!\/mo)(?:\s|$)/);
  return m ? Math.round(parseFloat(m[1].replace(/,/g, ''))) : null;
}

function parseLabel(text, country) { // full price printed inside an option label
  const t = text.replace(/\s+/g, ' ');
  const m = country === 'in'
    ? t.match(/MRP\s*₹\s?([\d,]+(?:\.\d\d)?)/) || t.match(/or ₹\s?([\d,]+(?:\.\d\d)?)(?![\d.,]*\/mo)/) || t.match(/₹\s?([\d,]+\.\d\d)\s*$/)
    : t.match(/Buy (?:from|for)\s*\$([\d,]+(?:\.\d\d)?)/) || t.match(/(?:^|\s)\$([\d,]+(?:\.\d\d)?)(?![\d.,]*\/mo)(?:\s|$)/);
  return m ? Math.round(parseFloat(m[1].replace(/,/g, ''))) : null;
}

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
  const totalText = () => p.evaluate(() => {
    const e = document.querySelector('[data-autom="full-price"]') || document.querySelector('.rc-prices-currentprice');
    return e ? e.innerText : '';
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
        const text = await totalText();
        const selected = Object.fromEntries(Object.entries(g).map(([n, vs]) => [n, (vs.find(x => x.chk) || {}).lab || null]));
        // India shows only the EMI in the summary; the MRP is printed on the selected option's label
        let total = parseTotal(text, country), from = 'summary';
        if (total == null) {
          const order = [...steps.map(s => s[0]).reverse(), ...Object.keys(selected)];
          for (const n of order) { const v = parseLabel(selected[n] || '', country); if (v != null) { total = v; from = 'label:' + n; break; } }
        }
        if (total == null) {
          // Pages without options: the product's own price follows its monthly-instalment line. Smaller
          // add-ons (AppleCare) are listed first with their own, smaller instalments, so take the price
          // that follows the largest instalment.
          const lines = await p.evaluate(() => document.body.innerText.split('\n').map(s => s.trim()).filter(s => /[₹$]\s?[\d,]/.test(s) && s.length < 140).slice(0, 12));
          let best = -1;
          lines.forEach((l, i) => {
            const mo = l.match(/[₹$]\s?([\d,]+(?:\.\d\d)?)\/mo/);
            if (!mo) return;
            const m = parseFloat(mo[1].replace(/,/g, ''));
            const sameLine = parseLabel(l.replace(/[₹$]\s?[\d,.]+\/mo\.?/, ''), country);
            const next = sameLine != null ? sameLine : parseLabel(lines[i + 1] || '', country);
            if (next != null && m > best) { best = m; total = next; from = 'page'; }
          });
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

(async () => {
  const [mode, page] = process.argv.slice(2);
  const browser = await chromium.launch();
  if (mode === 'all') {
    fs.mkdirSync(path.join(ROOT, 'raw'), { recursive: true });
    const jobs = Object.keys(CONFIGS.pages).flatMap(pg => ['us', 'in'].map(c => [c, pg]));
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
