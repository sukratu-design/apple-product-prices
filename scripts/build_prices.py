#!/usr/bin/env python3
"""Build prices.js from the raw Apple Store scrape in raw/*.json.

    python3 scripts/build_prices.py            write prices.js and print what changed
    python3 scripts/build_prices.py --check    only print what would change

Every price row in prices.js is [label, price in each country of COUNTRIES, in order]; null means that
store does not list it. Only the Hermès Apple Watch rows are entered by hand (they are sold on a separate store).
"""
import json, re, sys, datetime, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "raw"
OUT = ROOT / "prices.js"
problems = []

# Every store, its currency marker and display format live in scripts/countries.json (shared with scrape.js).
# Order matters: it is the order of prices in every row.
_C = json.loads((ROOT / "scripts" / "countries.json").read_text())["countries"]
COUNTRIES = {}
for k, c in _C.items():
    store = re.sub(r"^https://www\.", "", c["store"]).rstrip("/")
    if k == "in":
        note = "Apple Store India MRP, including GST"
    elif c["tax"] == "excluded":
        note = f"Apple Store {c['label']} prices, before sales tax"
    else:
        note = f"Apple Store {c['label']} prices, including tax"
    COUNTRIES[k] = {key: c[key] for key in ("currency", "label", "short", "symbol", "locale") if key in c}
    COUNTRIES[k].update({"suffix": bool(c.get("suffix")), "store": store, "tax": c["tax"], "note": note})
CC = list(COUNTRIES)

# Same price rules as scripts/scrape.js: a price is a number next to the store's currency marker, in any local format.
NUM = r"\d{1,3}(?:[.,\s  '’]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?"


def raw(country, page):
    f = RAW / f"{country}-{page}.json"
    if not f.exists():
        problems.append(f"missing scrape file {f.name}")
        return {}
    return json.loads(f.read_text())["configs"]


def to_number(s):
    s = re.sub(r"[\s  '’]", "", s)
    m = re.search(r"[.,](\d{1,2})$", s)
    whole = re.sub(r"[.,]", "", s[: -len(m.group(0))] if m else s)
    return round(float(whole + ("." + m.group(1) if m else "")))


# Footnote markers ("Footnote 1", "脚注 1", "Lábjegyzet 1") sit right before prices on some stores; without
# removing them "脚注 1 219,800円" would read as 1,219,800.
FOOTNOTE = r"(?:Footnote|Fußnote|Note de bas de page|Nota al pie|Nota a pie de página|Nota de rodapé|Nota|Voetnoot|Fotnot|Fotnote|Fodnote|Alaviite|Przypis|Poznámka|Lábjegyzet|Dipnot|Dipnotu|حاشية|각주|脚注|註腳|註解|เชิงอรรถ|Chú thích)\s*[\d①-⑳*†‡§◊※#]+"


# Model names with a number ("AirPods 5 149,00 €") would otherwise merge with the price as 5 149,00.
MODEL = r"(?:AirPods|HomePod|iPhone|iPad|Watch|Series|Ultra|SE|Pro|Max|mini|Air)\s\d{1,2}(?![\d.,])"


def price_matches(text, c):
    """[(start, end, value)] for every price written next to this store's currency marker."""
    t = re.sub(r"\s+", " ", re.sub(MODEL, " ", re.sub(FOOTNOTE, " ", text or "")))
    mk = _C[c]["marker"]
    out = []
    for rx in (rf"(?:{mk})\s?({NUM})", rf"({NUM})\s?(?:{mk})"):
        for m in re.finditer(rx, t):
            out.append((m.start(), m.end(), to_number(m.group(1))))
    return t, [x for x in out if x[2] > 0]


def money(text, c):
    """The full price in a label or price box: the largest amount (instalments, lease and AppleCare are smaller)."""
    _, ms = price_matches(text, c)
    return max(v for _, _, v in ms) if ms else None


def delta(label, c):
    """Price difference on an option label: '24GB + $200.00' -> 200, '512GB− 300,00 €' -> -300,
    '24GB ₩340,000 +' -> 340000 (Korea puts the sign after), '1TB' or 'Included' -> 0."""
    t, ms = price_matches(label, c)
    if not ms:
        return 0
    start, end, v = ms[0]
    before, after = t[max(0, start - 3):start], t[end:end + 3]
    sign = re.search(r"[+−-]", before) or re.search(r"[+−-]", after)
    if not sign:
        return 0
    return -v if sign.group(0) in "−-" else v


def size(label):
    """'256GB', '256 Go' (French), '1 To', '24 Gt' (Finnish), '512GB儲存裝置' -> '256GB', '1TB', '24GB', '512GB'."""
    # the size can sit anywhere and touch local text: "512GB儲存裝置", "Dung lượng 256GB", "256 Go"
    m = re.search(r"(?<![\d.,])(\d+)\s?(GB|TB|Go|To|Gt|Tt)(?![A-Za-z])", label)
    if not m:
        return label.split(" ")[0]
    return m.group(1) + ("TB" if m.group(2) in ("TB", "To", "Tt") else "GB")


def total_of(cfg, c):
    """Configured price. Some stores (India) show only the monthly instalment in the price box and print the
    full price on the selected option's label, so take the largest of the box and the selected labels."""
    vals = [cfg.get("total")] + [money(l, c) for l in (cfg.get("selected") or {}).values() if l]
    vals = [v for v in vals if v is not None]
    return max(vals) if vals else None


def opt(cfg, pattern, disabled=False):
    for name, labels in (cfg.get("options") or {}).items():
        if re.search(pattern, name) and labels:
            return labels
    if disabled:
        for name, labels in (cfg.get("disabledOptions") or {}).items():
            if re.search(pattern, name):
                return labels
    return []


def per_country(fn):
    return [fn(c) for c in CC]


def merge(rows_by_country):
    """{country: [(label, price)]} -> [[label, price per country]] keeping first-seen label order."""
    keys = []
    for c in CC:
        for k, _ in rows_by_country.get(c, []):
            if k not in keys:
                keys.append(k)
    lookup = {c: dict(rows_by_country.get(c, [])) for c in CC}
    return [[k] + [lookup[c].get(k) for c in CC] for k in keys]


# ---------------- iPhone and Vision Pro: storage labels carry full prices ----------------
def storage_row(id_, n, s, page, path="default", single_label="Standard"):
    by = {}
    for c in CC:
        cfg = raw(c, page).get(path, {})
        labels = opt(cfg, r"Capacity$", disabled=True)
        if labels:
            by[c] = [(size(l), money(l, c)) for l in labels]
        elif total_of(cfg, c) is not None:
            by[c] = [(single_label, total_of(cfg, c))]
    tiers = merge(by)
    if not tiers:
        problems.append(f"no prices for {n}")
    return {"id": id_, "n": n, "s": s, "src": "apple", "tiers": tiers}


# ---------------- iPad: Wi-Fi storage tiers, cellular and nano-texture add-ons ----------------
def ipad(id_, n, s, page, wifi_path, cell_path, nano_on=(), nano_path=None):
    wifi, cell, nano = {}, {}, {}
    for c in CC:
        r = raw(c, page)
        w = [(size(l), money(l, c)) for l in opt(r.get(wifi_path, {}), r"Capacity$")]
        ce = [(size(l), money(l, c)) for l in opt(r.get(cell_path, {}), r"Capacity$")]
        if not w:
            problems.append(f"{c} {page}/{wifi_path}: no storage prices ({r.get(wifi_path, {}).get('error', '')})")
            continue
        wifi[c] = w
        # storage labels ignore connectivity, but the Wi-Fi and Wi-Fi + Cellular labels carry their own prices
        conn = [money(l, c) for l in opt(r.get(wifi_path, {}), r"Connection$")]
        if len(conn) == 2 and None not in conn and conn[1] > conn[0]:
            cell[c] = conn[1] - conn[0]
        else:
            problems.append(f"{c} {page}/{cell_path}: no cellular price")
        if nano_path:
            fin = [money(l, c) for l in opt(r.get(nano_path, {}), r"Finish$", disabled=True)]
            fin = [x for x in fin if x is not None]
            if len(fin) >= 2:
                nano[c] = max(fin) - min(fin)
            else:
                problems.append(f"{c} {page}/{nano_path}: no nano-texture price")
    return {"id": id_, "n": n, "s": s, "src": "apple", "wifi": merge(wifi),
            "cell": [cell.get(c) for c in CC], "nano": list(nano_on), "nanoAdd": [nano.get(c, 0) for c in CC]}


# ---------------- Mac: base configuration + memory and storage upgrades ----------------
def mac_variant(cfg, c):
    """Absolute prices for one chip variant: base (cheapest memory + storage), memory and storage options."""
    total = total_of(cfg, c)
    if total is None:
        return None
    mem = [(size(l), delta(l, c)) for l in opt(cfg, r"Memory$")]
    sto = [(size(l), delta(l, c)) for l in opt(cfg, r"Capacity$")]
    mm = min((d for _, d in mem), default=0)
    ms = min((d for _, d in sto), default=0)
    base = total + mm + ms
    return {"base": base, "mem": [(k, base + d - mm) for k, d in mem] or [("", base)],
            "sto": [(k, base + d - ms) for k, d in sto] or [("", base)]}


def addon(page, path, group, choice):
    """Price of one add-on option (such as nano-texture display) in each country, from its label."""
    def f(c):
        for l in opt(raw(c, page).get(path, {}), group):
            if re.search(choice, l):
                return delta(l, c) or None
        return None
    return per_country(f)


def mac(id_, n, s, page, paths, chip_names, notes=None):
    """One row per chip; extra paths are higher-core versions of the same chip."""
    per = {}
    for c in CC:
        r = raw(c, page)
        vs = []
        for p in paths:
            v = mac_variant(r.get(p, {}), c)
            if v is None:
                problems.append(f"{c} {page}/{p}: no total ({r.get(p, {}).get('error', 'not scraped')})")
            else:
                vs.append((p, v))
        per[c] = vs
    ref = next((per[c] for c in CC if per[c]), None)
    if not ref:
        problems.append(f"no prices for {n} {s}")
        return None
    base_path = ref[0][0]

    def pick(c, kind):  # cheapest way to get each option, relative to this country's base
        vs = per[c]
        if not vs:
            return {}
        b = vs[0][1]["base"]
        best = {}
        for p, v in vs:
            for k, price in v[kind]:
                if k not in best or price < best[k][0]:
                    best[k] = (price, p)
        return {k: (price - b, p) for k, (price, p) in best.items()}

    def rows(kind):
        got = {c: pick(c, kind) for c in CC}
        keys = list(dict.fromkeys(k for c in CC for k in got[c]))
        first = lambda k: next(got[c][k] for c in CC if k in got[c])
        keys.sort(key=lambda k: first(k)[0] if CC[0] not in got or k not in got[CC[0]] else got[CC[0]][k][0])
        out = []
        for k in keys:
            p = first(k)[1]
            label = k + (f" (needs {chip_names[p]})" if p != base_path and p in chip_names else "")
            out.append([label] + [got[c][k][0] if k in got[c] else None for c in CC])
        return out

    mem, sto = rows("mem"), rows("sto")

    def full(c):  # most expensive memory on any variant + largest storage
        vs = per[c]
        if not vs:
            return None
        return max(max(x for _, x in v["mem"]) + max(x for _, x in v["sto"]) - v["base"] for _, v in vs)

    base = per_country(lambda c: per[c][0][1]["base"] if per[c] else None)
    top_mem = max(mem, key=lambda m: m[1] if m[1] is not None else -1)[0].split(" (")[0]
    min_mem = mem[0][0] if mem and mem[0][0] else ""
    min_sto = sto[0][0] if sto and sto[0][0] else ""
    row = {"id": id_, "n": n, "s": s, "src": "apple",
           "base": [" · ".join(x for x in (chip_names.get(base_path, ""), min_mem, min_sto) if x)] + base,
           "mem": mem, "sto": sto}
    if len(mem) > 1 or len(sto) > 1:
        row["max"] = [" · ".join(x for x in ("Fully loaded", top_mem, sto[-1][0]) if x)] + per_country(full)
    if notes:
        row["notes"] = notes
    return row


# ---------------- simple products ----------------
def by_path(id_, n, s, page, paths, notes=None):
    tiers = []
    for p, label in paths:
        prices = per_country(lambda c: total_of(raw(c, page).get(p, {}), c))
        if all(x is None for x in prices):
            problems.append(f"{page}/{p}: no total")
            continue
        tiers.append([label] + prices)
    row = {"id": id_, "n": n, "s": s, "src": "apple", "tiers": tiers}
    if notes:
        row["notes"] = notes
    return row


def single(id_, n, s, page, names=None):
    """One page, default choices. If an option group lists several priced choices (such as a charging case), each becomes a tier."""
    by = {}
    for c in CC:
        cfg = raw(c, page).get("default", {})
        for gname, labels in (cfg.get("options") or {}).items():
            if re.search(r"Color|colou?r|engraving|^app_", gname, re.I):
                continue
            priced = [money(l, c) for l in labels]
            if len(priced) > 1 and all(v is not None for v in priced):
                by[c] = [((names or [])[i] if names and i < len(names) else labels[i].split(" From")[0].strip(), v)
                         for i, v in enumerate(priced)]
                break
        else:
            if total_of(cfg, c) is not None:
                by[c] = [("Starting price", total_of(cfg, c))]
    tiers = merge(by)
    if not tiers:
        problems.append(f"{page}: no prices")
    return {"id": id_, "n": n, "s": s, "src": "apple", "tiers": tiers}


def hand(us_price):
    """A US-only price entered by hand."""
    return [us_price] + [None] * (len(CC) - 1)


NANO_MBP = ["Nano-texture display adds"] + addon("macbook-pro", "14-m6", r"Finish$", r"Nano")
ETH_MINI = ["10 Gigabit Ethernet adds"] + addon("mac-mini", "m6", r"ethernet", r"10 Gigabit")

# ---------------- catalogue ----------------
CATEGORIES = {
    "iphone": {"name": "iPhone", "unit": "Storage", "products": [
        storage_row("duo", "iPhone Duo", "Foldable", "iphone-duo"),
        storage_row("18pm", "18 Pro Max", "iPhone", "iphone-18-pro", "promax"),
        storage_row("18p", "18 Pro", "iPhone", "iphone-18-pro", "pro"),
        storage_row("air", "iPhone Air", "iPhone", "iphone-air"),
        storage_row("17", "iPhone 17", "iPhone", "iphone-17"),
        storage_row("16", "iPhone 16", "iPhone", "iphone-16", single_label="128GB"),
        storage_row("17e", "iPhone 17e", "iPhone", "iphone-17e"),
    ]},
    "ipad": {"name": "iPad", "unit": "Storage", "nano": True,
             "variants": [["wifi", "Wi-Fi"], ["cellular", "Wi-Fi + Cellular"]], "products": [
        ipad("pro13", "iPad Pro", "13-inch · M5", "ipad-pro", "13-wifi", "13-cell", ["1TB", "2TB"], "nano"),
        ipad("pro11", "iPad Pro", "11-inch · M5", "ipad-pro", "11-wifi", "11-cell", ["1TB", "2TB"], "nano"),
        ipad("air13", "iPad Air", "13-inch · M4", "ipad-air", "13-wifi", "13-cell"),
        ipad("air11", "iPad Air", "11-inch · M4", "ipad-air", "11-wifi", "11-cell"),
        ipad("mini", "iPad mini", "A20 Pro", "ipad-mini", "wifi", "cell", ["256GB", "512GB"], "nano"),
        ipad("ipad", "iPad", "A16", "ipad", "wifi", "cell"),
    ]},
    # outliers: rows left out when sizing the axis (they show an off-scale tag instead)
    "mac": {"name": "Mac", "unit": "Memory · Storage", "mac": True, "axis": {"outliers": ["studio-ultra"]}, "products": [
        mac("studio-ultra", "Mac Studio", "M5 Ultra", "mac-studio", ["m5ultra-64", "m5ultra-80"],
            {"m5ultra-64": "30-core CPU", "m5ultra-80": "36-core CPU, 80-core GPU"}),
        mac("studio-max", "Mac Studio", "M5 Max", "mac-studio", ["m5max-32", "m5max-40"],
            {"m5max-32": "32-core GPU", "m5max-40": "40-core GPU"}),
        mac("mbp16-max", "MacBook Pro 16", "M5 Max", "macbook-pro", ["16-m5max-32", "16-m5max-40"],
            {"16-m5max-32": "32-core GPU", "16-m5max-40": "40-core GPU"}, [NANO_MBP]),
        mac("mbp16-pro", "MacBook Pro 16", "M5 Pro", "macbook-pro", ["16-m5pro-18"], {"16-m5pro-18": "M5 Pro"}, [NANO_MBP]),
        mac("mbp14-max", "MacBook Pro 14", "M5 Max", "macbook-pro", ["14-m5max-32", "14-m5max-40"],
            {"14-m5max-32": "32-core GPU", "14-m5max-40": "40-core GPU"}, [NANO_MBP]),
        mac("mbp14-pro", "MacBook Pro 14", "M5 Pro", "macbook-pro", ["14-m5pro-15", "14-m5pro-18"],
            {"14-m5pro-15": "15-core CPU", "14-m5pro-18": "18-core CPU, 20-core GPU"}, [NANO_MBP]),
        mac("mbp14-m6", "MacBook Pro 14", "M6", "macbook-pro", ["14-m6"], {"14-m6": "M6"}, [NANO_MBP]),
        mac("imac4", "iMac", "M6 · 4 ports", "imac", ["4port"], {"4port": "12-core GPU"}),
        mac("imac2", "iMac", "M6 · 2 ports", "imac", ["2port"], {"2port": "10-core GPU"}),
        mac("mba15", "MacBook Air 15", "M5", "macbook-air", ["15"], {"15": "M5"}),
        mac("mba13", "MacBook Air 13", "M5", "macbook-air", ["13-8gpu", "13-10gpu"],
            {"13-8gpu": "8-core GPU", "13-10gpu": "10-core GPU"}),
        mac("mini-pro", "Mac mini", "M5 Pro", "mac-mini", ["m5pro-15", "m5pro-18"],
            {"m5pro-15": "15-core CPU", "m5pro-18": "18-core CPU, 20-core GPU"}, [ETH_MINI]),
        mac("mini", "Mac mini", "M6", "mac-mini", ["m6"], {"m6": "M6"}, [ETH_MINI]),
        by_path("neo", "MacBook Neo", "A-series", "macbook-neo", [("256", "256GB"), ("512", "512GB · Touch ID")]),
    ]},
    # zoom: start the axis just below the cheapest model, since Watch prices sit close together
    "watch": {"name": "Watch", "title": "Apple Watch lineup", "unit": "Size · Connectivity", "def": "s12",
              "axis": {"outliers": ["hultra", "hs12"], "zoom": True}, "products": [
        {"id": "hultra", "n": "Hermès Ultra 4", "s": "49mm titanium", "src": "reported",
         "tiers": [["Starting price"] + hand(1499)], "notes": ["Hermès prices are US starting prices from press coverage"]},
        {"id": "hs12", "n": "Hermès Series 12", "s": "Apple Watch", "src": "reported",
         "tiers": [["Starting price"] + hand(1399)], "notes": ["Hermès prices are US starting prices from press coverage"]},
        by_path("s12c", "Series 12", "Ceramic", "apple-watch", [("ce-42", "42mm"), ("ce-46", "46mm")]),
        by_path("ultra", "Watch Ultra 4", "49mm · Cellular", "apple-watch-ultra",
                [("band", "Ocean, Alpine or Trail band"), ("milanese", "Milanese Loop")]),
        by_path("s12t", "Series 12", "Titanium", "apple-watch", [("ti-42", "42mm · Cellular"), ("ti-46", "46mm · Cellular")],
                ["Titanium is cellular only"]),
        by_path("s12", "Series 12", "Aluminum", "apple-watch",
                [("al-42-gps", "42mm · GPS"), ("al-46-gps", "46mm · GPS"), ("al-42-cell", "42mm · Cellular"), ("al-46-cell", "46mm · Cellular")]),
        by_path("se3", "Watch SE 3", "Aluminum", "apple-watch-se",
                [("40-gps", "40mm · GPS"), ("44-gps", "44mm · GPS"), ("40-cell", "40mm · Cellular"), ("44-cell", "44mm · Cellular")]),
    ]},
    "vision": {"name": "Vision", "title": "Apple Vision Pro", "unit": "Storage", "products": [
        storage_row("vision", "Vision Pro", "M5", "apple-vision-pro"),
    ]},
    "airpods": {"name": "AirPods", "title": "AirPods lineup", "unit": "Model", "products": [
        single("max2", "AirPods Max 2", "Over-ear", "airpods-max-2"),
        single("pro3", "AirPods Pro 3", "In-ear · ANC", "airpods-pro-3"),
        single("ap5", "AirPods 5", "In-ear", "airpods-5", names=["Standard case", "Wireless charging case"]),
    ]},
    "home": {"name": "Home", "title": "Home & TV lineup", "unit": "Model", "products": [
        single("homepod", "HomePod", "2nd generation", "homepod"),
        single("tv", "Apple TV 4K", "4th generation", "apple-tv-4k"),
        single("hpmini", "HomePod mini 2", "Speaker", "homepod-mini"),
    ]},
}

# ---------------- exchange rates, for the world comparison page ----------------
def exchange_rates():
    """Units of each country's currency per 1 USD, from open.er-api.com (daily rates, no key needed).
    If the service is down, keep the rates already in prices.js and report it."""
    import urllib.request
    try:
        with urllib.request.urlopen("https://open.er-api.com/v6/latest/USD", timeout=30) as r:
            d = json.load(r)
        if d.get("result") != "success":
            raise ValueError(d.get("error-type", "bad response"))
        per = {c: d["rates"][COUNTRIES[c]["currency"]] for c in CC}
        day = datetime.datetime.fromtimestamp(d["time_last_update_unix"], datetime.timezone.utc).date().isoformat()
        return {"date": day, "source": "open.er-api.com", "perUSD": per}
    except Exception as e:
        problems.append(f"exchange rates not refreshed ({e}); keeping the previous ones")
        try:
            prev = json.loads(OUT.read_text().split("=", 1)[1].rstrip().rstrip(";")).get("fx")
            if prev and all(c in prev["perUSD"] for c in CC):
                return prev
        except Exception:
            pass
        return None


DATA = {
    "updated": datetime.date.today().isoformat(),
    "fx": exchange_rates(),
    "countryOrder": CC,
    "countries": COUNTRIES,
    "order": list(CATEGORIES),
    "categories": {k: {**v, "products": [p for p in v["products"] if p]} for k, v in CATEGORIES.items()},
}


# ---------------- diff against the current prices.js ----------------
def flat(data):
    order = data.get("countryOrder") or ["us", "in"]
    out = {}
    for c in data["categories"].values():
        for p in c["products"]:
            name = f"{p['n']} {p['s']}"
            rows = p.get("tiers") or p.get("wifi") or []
            if "base" in p:
                rows = ([p["base"]] + [[f"+{r[0]} memory"] + r[1:] for r in p["mem"]]
                        + [[f"+{r[0]} storage"] + r[1:] for r in p["sto"]] + ([p["max"]] if "max" in p else []))
            if "cell" in p:
                rows = rows + [["Cellular adds"] + p["cell"], ["Nano-texture adds"] + p["nanoAdd"]]
            for r in rows:
                for i, cc in enumerate(order):
                    out[f"{name} · {r[0]} · {cc.upper()}"] = r[i + 1] if i + 1 < len(r) else None
    return out


old = {}
if OUT.exists():
    try:
        old = flat(json.loads(OUT.read_text().split("=", 1)[1].rstrip().rstrip(";")))
    except Exception:
        old = {}
new = flat(DATA)
changes = [f"  {k}: {old.get(k)} -> {v}" for k, v in new.items() if k in old and old[k] != v]
added = [f"  {k}: {v}" for k, v in new.items() if k not in old]
gone = [f"  {k}" for k in old if k not in new]

print(f"{len(changes)} changed, {len(added)} new, {len(gone)} removed, {len(problems)} problems")
for title, items in (("Changed", changes), ("New", added if len(added) < 60 else added[:60] + [f"  … {len(added) - 60} more"]),
                     ("Removed", gone), ("Problems", ["  " + p for p in problems])):
    if items:
        print(f"\n{title}:\n" + "\n".join(items))

if "--check" not in sys.argv:
    OUT.write_text("// Generated by scripts/build_prices.py from Apple Store scrapes. Do not edit by hand.\nwindow.PRICES = "
                   + json.dumps(DATA, ensure_ascii=False, indent=1) + ";\n")
    print(f"\nWrote {OUT.relative_to(ROOT)}")


    # ---------- machine-readable exports for agents and other tools ----------
    # Same expansion as assets/core.js: every configuration with its full price in every country.
    def add(a, b):
        return [None if x is None or y is None else x + y for x, y in zip(a, b)]

    def configurations(p):
        if "wifi" in p:  # iPad: Wi-Fi tiers, Wi-Fi + Cellular tiers, nano-texture glass on some sizes
            out = []
            for conn, extra in (("Wi-Fi", [0] * len(CC)), ("Wi-Fi + Cellular", p["cell"])):
                for row in p["wifi"]:
                    base = add(row[1:], extra)
                    out.append((f"{conn} · {row[0]}", base))
                    if row[0] in p.get("nano", []):
                        out.append((f"{conn} · {row[0]} · Nano-texture", add(base, [x or None for x in p["nanoAdd"]])))
            return out
        if "base" in p:  # Mac: base configuration, + each memory step, + each storage step, fully loaded
            base = p["base"][1:]
            out = [(p["base"][0], base)]
            out += [(f"{r[0].split(' (')[0]} memory" + (f" ({r[0].split(' (', 1)[1]}" if " (" in r[0] else ""), add(base, r[1:]))
                    for r in p["mem"] if any(r[1:])]
            out += [(f"{r[0].split(' (')[0]} storage" + (f" ({r[0].split(' (', 1)[1]}" if " (" in r[0] else ""), add(base, r[1:]))
                    for r in p["sto"] if any(r[1:])]
            if "max" in p:
                out.append((p["max"][0], p["max"][1:]))
            return out
        return [(t[0], t[1:]) for t in p["tiers"]]

    fx = (DATA.get("fx") or {}).get("perUSD", {})
    rows, products = [], []
    for ck in DATA["order"]:
        cat = DATA["categories"][ck]
        for p in cat["products"]:
            name = p["n"] if (p["s"] in p["n"] or p["s"] == "iPhone") else f"{p['n']} {p['s']}"
            confs = []
            for label, prices in configurations(p):
                per = {}
                for c, v in zip(CC, prices):
                    if v is None:
                        continue
                    usd = round(v / fx[c], 2) if fx.get(c) else None
                    per[c] = {"price": v, "currency": COUNTRIES[c]["currency"], "usd": usd}
                    rows.append([cat["name"], name, label, c, COUNTRIES[c]["label"], COUNTRIES[c]["currency"], v, "" if usd is None else usd,
                                 "excluded" if COUNTRIES[c]["tax"] == "excluded" else "included"])
                if per:
                    confs.append({"configuration": label, "prices": per})
            products.append({"category": cat["name"], "id": f"{ck}-{p['id']}", "name": name,
                             "source": "Apple Store" if p.get("src") == "apple" else "press coverage (US only)",
                             "configurations": confs})
    export = {
        "about": "List prices of every current Apple product in every country with an Apple online store, read from Apple's own stores. "
                 "Each configuration lists its full price per country (ISO-style country code) in local currency, plus a USD conversion.",
        "source": "https://apple-product-prices.com",
        "checked": DATA["updated"],
        "exchangeRates": {"perUSD": {COUNTRIES[c]["currency"]: fx.get(c) for c in CC}, "date": (DATA.get("fx") or {}).get("date"),
                          "source": (DATA.get("fx") or {}).get("source")},
        "countries": {c: {"name": COUNTRIES[c]["label"], "currency": COUNTRIES[c]["currency"], "store": "https://www." + COUNTRIES[c]["store"],
                          "taxes": "excluded (sales tax added at checkout)" if COUNTRIES[c]["tax"] == "excluded" else "included",
                          "note": COUNTRIES[c]["note"]} for c in CC},
        "products": products,
    }
    import csv, io
    (ROOT / "data").mkdir(exist_ok=True)
    (ROOT / "data" / "prices.json").write_text(json.dumps(export, ensure_ascii=False, indent=1) + "\n")
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["category", "product", "configuration", "country_code", "country", "currency", "price", "price_usd", "taxes"])
    w.writerows(rows)
    (ROOT / "data" / "prices.csv").write_text(buf.getvalue())
    print(f"Wrote data/prices.json and data/prices.csv ({len(rows)} prices)")

    # llms.txt: plain-text guide for AI agents (https://llmstxt.org)
    names = ", ".join(f"{COUNTRIES[c]['label']} ({c}, {COUNTRIES[c]['currency']})" for c in CC)
    cats = "\n".join(f"- {DATA['categories'][k]['name']}: " + ", ".join(
        (p["n"] if (p["s"] in p["n"] or p["s"] == "iPhone") else f"{p['n']} {p['s']}") for p in DATA["categories"][k]["products"])
        for k in DATA["order"])
    (ROOT / "llms.txt").write_text(f"""# Apple Product Prices

> List prices of every current Apple product, from the entry configuration to fully loaded, in the {len(CC)} countries that have an Apple online store. Prices are read directly from Apple's own stores and checked monthly. Last checked: {DATA['updated']}. Not affiliated with Apple.

For one product, read its page under /prices/ (listed below): every price is written in the HTML. For bulk use, read the data files; the price ranges and world map pages draw their charts with JavaScript, so their HTML contains no prices.

## Data

- [All prices, JSON](https://apple-product-prices.com/data/prices.json): every product and configuration with its price in each country (local currency and a USD conversion), plus the exchange rates used and each country's tax treatment.
- [All prices, CSV](https://apple-product-prices.com/data/prices.csv): one row per product, configuration and country. Columns: category, product, configuration, country_code, country, currency, price, price_usd, taxes.

## How to read the prices

- `price` is the full list price in the country's currency, as shown on that country's Apple Store, rounded to a whole unit.
- `price_usd` converts it at the exchange rate in `exchangeRates` (dated); use it to compare countries, not as an Apple price.
- US and Canada prices exclude sales tax (added at checkout); every other country includes VAT, GST or a similar tax. India prices are MRP.
- A product or configuration missing for a country is not sold in that country's online store.
- iPad configurations are listed for Wi-Fi and Wi-Fi + Cellular, with nano-texture glass where offered.
- Mac configurations are: the base configuration; the base with each memory upgrade; the base with each storage upgrade; and fully loaded (most memory and storage). Memory and storage upgrades can be combined: add both differences to the base price.
- Hermès Apple Watch prices are US starting prices from press coverage, because Hermès models are sold on a separate store.

## Countries

{names}

## Products

{cats}

## Pages

- [By product](https://apple-product-prices.com/prices/): every product with its cheapest and most expensive country, linking to a page per product.
- [Price ranges](https://apple-product-prices.com/): every product in one country, entry to fully loaded. Link to a view with #<category>-<country>, for example https://apple-product-prices.com/#mac-in
- [World map](https://apple-product-prices.com/world/): one configuration compared across all countries in one currency. Link format #<category>-<product id>-<configuration number>-<currency country>, for example https://apple-product-prices.com/world/#iphone-18p-0-us

## Source

- [GitHub repository](https://github.com/sukratu-design/apple-product-prices): scraper, build script and raw store data.
- Developed by Anand Bongir (https://x.com/anand_bongir).
""")
    print("Wrote llms.txt")

    # keep the structured data's dateModified in both pages in step with the price check
    for page in (ROOT / "index.html", ROOT / "world" / "index.html"):
        html = page.read_text()
        page.write_text(re.sub(r'"dateModified": "[\d-]+"', f'"dateModified": "{DATA['updated']}"', html))

    # static product pages, sitemap and product links (scripts/build-pages.mjs reads the prices.js written above)
    import subprocess
    subprocess.run(["node", str(ROOT / "scripts" / "build-pages.mjs")], check=True)
