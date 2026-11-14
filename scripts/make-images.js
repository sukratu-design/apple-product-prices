// Render the favicon PNGs and the social share image from assets/favicon.svg and scripts/og.html.
// Usage: node scripts/make-images.js   (needs Playwright with Chromium)
// Writes assets/og.png (1200×630), assets/apple-touch-icon.png (180×180), assets/favicon-32.png and assets/favicon-16.png.
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const A = p => path.join(ROOT, 'assets', p);

// Share image rows use current US prices, so the picture stays in step with the data.
global.window = {};
require(path.join(ROOT, 'prices.js'));
const P = window.PRICES, US = 1;
const find = (c, id) => P.categories[c].products.find(p => p.id === id);
const tierDots = p => p.tiers.map((t, i, a) => [t[US], i === 0 ? '--t1' : i === a.length - 1 ? '--t4' : ['--t2', '--t3'][(i - 1) % 2]]);
const macDots = p => [[p.base[US], '--t1'],
  ...p.mem.filter(m => m[US]).map(m => [p.base[US] + m[US], '--t2']),
  ...p.sto.filter(s => s[US]).map(s => [p.base[US] + s[US], '--t3', true]),
  [p.max[US], '--t4']];
const rows = [
  ['iPhone Duo', tierDots(find('iphone', 'duo'))],
  ['MacBook Pro 14', macDots(find('mac', 'mbp14-m6'))],
  ['iPhone 17e', tierDots(find('iphone', '17e'))],
];

(async () => {
  const b = await chromium.launch();
  const og = await b.newPage({ viewport: { width: 1200, height: 630 }, deviceScaleFactor: 1 });
  await og.addInitScript(r => { window.OG_ROWS = r; }, rows);
  await og.goto('file://' + path.join(__dirname, 'og.html'), { waitUntil: 'networkidle' });
  await og.evaluate(() => document.fonts.ready);
  await og.screenshot({ path: A('og.png') });

  const svg = fs.readFileSync(A('favicon.svg'), 'utf8');
  for (const [name, size] of [['apple-touch-icon.png', 180], ['favicon-32.png', 32], ['favicon-16.png', 16]]) {
    const p = await b.newPage({ viewport: { width: size, height: size } });
    // the touch icon fills its square (iOS rounds the corners itself)
    const body = name.startsWith('apple') ? svg.replace('rx="14"', 'rx="0"') : svg;
    await p.setContent(`<style>*{margin:0}body{background:transparent}svg{display:block;width:${size}px;height:${size}px}</style>${body}`);
    await p.screenshot({ path: A(name), omitBackground: true });
  }
  await b.close();
  console.log('Wrote assets/og.png, apple-touch-icon.png, favicon-32.png, favicon-16.png');
})();
