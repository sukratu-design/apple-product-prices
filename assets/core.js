// Shared by index.html and world/index.html: turns the compact rows in prices.js into configurations.
// A configuration is [label, [price per country in PRICES.countryOrder], meta]; null = not sold there.
(function () {
  const P = window.PRICES, ORDER = P.countryOrder;
  const add = (a, b) => a.map((x, i) => x == null || b[i] == null ? null : x + b[i]);
  const zero = () => ORDER.map(() => 0);

  function ipad(p, variant) { // Wi-Fi tiers, + cellular, + nano-texture glass on some sizes
    const out = [], n = p.wifi.length;
    p.wifi.forEach((row, si) => {
      const prices = add(row.slice(1), variant === "cellular" ? p.cell : zero());
      out.push([row[0], prices, {s: si, n}]);
      if ((p.nano || []).includes(row[0]))
        out.push([row[0] + " · Nano-texture", add(prices, p.nanoAdd.map(x => x || null)), {s: si, n, ring: true}]);
    });
    return out;
  }
  function mac(p) { // base configuration, each memory step, each storage step, then fully loaded
    const name = (l, kind) => l.replace(/^(\S+)/, "$1 " + kind); // "512GB (needs …)" -> "512GB memory (needs …)"
    const base = p.base.slice(1), out = [[p.base[0], base, {k: "base"}]];
    const upgrade = r => r.slice(1).some(x => x);
    p.mem.filter(upgrade).forEach(r => out.push([name(r[0], "memory"), add(base, r.slice(1)), {k: "mem"}]));
    p.sto.filter(upgrade).forEach(r => out.push([name(r[0], "storage"), add(base, r.slice(1)), {k: "sto", ring: true}]));
    if (p.max) out.push([p.max[0], p.max.slice(1), {k: "max"}]);
    return out;
  }
  const configs = (p, variant = "wifi") =>
    p.wifi ? ipad(p, variant) : p.base ? mac(p) : p.tiers.map(t => [t[0], t.slice(1), null]);

  const fmt = (country, n) => { const c = P.countries[country]; return c.symbol + Math.round(n).toLocaleString(c.locale); };
  const toUSD = (country, n) => P.fx ? n / P.fx.perUSD[country] : null;

  window.APP = { P, ORDER, configs, fmt, toUSD };
})();
