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

# Order matters: it is the order of prices in every row and of the country buttons on the page.
COUNTRIES = {
    "us": {"label": "United States", "short": "US", "symbol": "$", "locale": "en-US", "store": "apple.com/shop",
           "note": "Apple Store US list prices, before sales tax"},
    "in": {"label": "India", "short": "India", "symbol": "₹", "locale": "en-IN", "store": "apple.com/in/shop",
           "note": "Apple Store India MRP, including GST"},
    "uk": {"label": "United Kingdom", "short": "UK", "symbol": "£", "locale": "en-GB", "store": "apple.com/uk/shop",
           "note": "Apple Store UK prices, including VAT"},
    "ae": {"label": "United Arab Emirates", "short": "UAE", "symbol": "AED ", "locale": "en-AE", "store": "apple.com/ae/shop",
           "note": "Apple Store UAE prices, including VAT"},
    "ca": {"label": "Canada", "short": "Canada", "symbol": "CA$", "locale": "en-CA", "store": "apple.com/ca/shop",
           "note": "Apple Store Canada prices, before sales tax"},
    "au": {"label": "Australia", "short": "Australia", "symbol": "A$", "locale": "en-AU", "store": "apple.com/au/shop",
           "note": "Apple Store Australia prices, including GST"},
}
CC = list(COUNTRIES)
CUR = {"us": r"\$", "ca": r"\$", "au": r"A\$", "uk": "£", "ae": r"AED\s?", "in": "₹"}
NUM = r"([\d,]+(?:\.\d\d)?)"


def raw(country, page):
    f = RAW / f"{country}-{page}.json"
    if not f.exists():
        problems.append(f"missing scrape file {f.name}")
        return {}
    return json.loads(f.read_text())["configs"]


def num(s):
    return round(float(s.replace(",", "")))


def money(text, c):
    """First full price in a label or summary, skipping monthly instalments (same rule as scripts/scrape.js)."""
    t = re.sub(r"\s+", " ", text or "")
    if c == "in":
        m = re.search(r"MRP\s*₹\s?" + NUM, t)
        if m:
            return num(m.group(1))
    for m in re.finditer(r"(?:^|[^A-Za-z])" + CUR[c] + r"\s?" + NUM + r"(?![\d.,]*/mo)", t):
        return num(m.group(1))
    return None


def delta(label):
    """'24GB + $200.00' -> 200, '512GB− £300.00' -> -300, '1TB' or 'Included' -> 0."""
    m = re.search(r"([+−-])\s?(?:A\$|\$|£|₹|AED)\s?" + NUM, label)
    if not m:
        return 0
    v = num(m.group(2))
    return -v if m.group(1) in "−-" else v


def size(label):
    m = re.match(r"\s*(\d+\s?(?:GB|TB))", label)
    return m.group(1).replace(" ", "") if m else label.split(" ")[0]


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
        elif cfg.get("total") is not None:
            by[c] = [(single_label, cfg["total"])]
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
def mac_variant(cfg):
    """Absolute prices for one chip variant: base (cheapest memory + storage), memory and storage options."""
    total = cfg.get("total")
    if total is None:
        return None
    mem = [(size(l), delta(l)) for l in opt(cfg, r"Memory$")]
    sto = [(size(l), delta(l)) for l in opt(cfg, r"Capacity$")]
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
                return delta(l) or None
        return None
    return per_country(f)


def mac(id_, n, s, page, paths, chip_names, notes=None):
    """One row per chip; extra paths are higher-core versions of the same chip."""
    per = {}
    for c in CC:
        r = raw(c, page)
        vs = []
        for p in paths:
            v = mac_variant(r.get(p, {}))
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
        prices = per_country(lambda c: raw(c, page).get(p, {}).get("total"))
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
            if cfg.get("total") is not None:
                by[c] = [("Starting price", cfg["total"])]
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

DATA = {
    "updated": datetime.date.today().isoformat(),
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
