#!/usr/bin/env python3
"""Build prices.js from the raw Apple Store scrape in raw/*.json.

    python3 scripts/build_prices.py            write prices.js and print what changed
    python3 scripts/build_prices.py --check    only print what would change

Every price in prices.js is [usd, inr]; None means the store does not list it.
Rows that cannot be read from the scrape (iPad, Hermès) are entered by hand below.
"""
import json, re, sys, datetime, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "raw"
OUT = ROOT / "prices.js"
problems = []


def raw(country, page):
    f = RAW / f"{country}-{page}.json"
    if not f.exists():
        problems.append(f"missing scrape file {f.name}")
        return {}
    return json.loads(f.read_text())["configs"]


def money(text, country):
    """Full price printed in an option label or price summary."""
    text = re.sub(r"\s+", " ", text)
    if country == "in":
        m = (re.search(r"MRP\s*₹\s?([\d,]+(?:\.\d\d)?)", text) or re.search(r"or ₹\s?([\d,]+(?:\.\d\d)?)(?![\d.,]*/mo)", text)
             or re.search(r"₹\s?([\d,]+\.\d\d)\s*$", text))
    else:
        m = (re.search(r"Buy (?:from|for)\s*\$([\d,]+(?:\.\d\d)?)", text)
             or re.search(r"(?:^|\s)\$([\d,]+(?:\.\d\d)?)(?![\d.,]*/mo)(?:\s|$)", text))
    return round(float(m.group(1).replace(",", ""))) if m else None


def delta(label):
    """'24GB + $200.00' -> 200, '512GB− ₹36000.00' -> -36000, '1TB' or 'Included' -> 0."""
    m = re.search(r"([+−-])\s?[$₹]\s?([\d,]+(?:\.\d\d)?)", label)
    if not m:
        return 0
    v = round(float(m.group(2).replace(",", "")))
    return -v if m.group(1) in "−-" else v


def size(label):
    m = re.match(r"\s*(\d+\s?(?:GB|TB))", label)
    return m.group(1).replace(" ", "") if m else label.split(" ")[0]


def opt(cfg, pattern):
    for name, labels in (cfg.get("options") or {}).items():
        if re.search(pattern, name):
            return labels
    return []


def both(fn):
    """Run fn(country) for us and in and zip the results into [usd, inr]."""
    return [fn("us"), fn("in")]


# ---------------- iPhone: storage labels carry full prices ----------------
def iphone(id_, n, s, page, path="default", single_label="Standard"):
    tiers = {}
    for c in ("us", "in"):
        cfg = raw(c, page).get(path, {})
        labels = opt(cfg, r"Capacity$") or [l for n, ls in (cfg.get("disabledOptions") or {}).items() if re.search(r"Capacity$", n) for l in ls]
        if not labels and cfg.get("total") is not None:  # single configuration
            tiers.setdefault(single_label, [None, None])[0 if c == "us" else 1] = cfg["total"]
        for lab in labels:
            tiers.setdefault(size(lab), [None, None])[0 if c == "us" else 1] = money(lab, c)
    if not tiers:
        problems.append(f"no prices for {n}")
    return {"id": id_, "n": n, "s": s, "src": "apple", "tiers": [[k, v[0], v[1]] for k, v in tiers.items()]}


# ---------------- Mac: base configuration + memory and storage upgrades ----------------
def mac_variant(cfg, c):
    """Absolute prices for one chip variant: base (cheapest memory + storage), memory and storage options."""
    total = cfg.get("total")
    if total is None:
        return None
    mem = [(size(l), delta(l)) for l in opt(cfg, r"Memory$")]
    sto = [(size(l), delta(l)) for l in opt(cfg, r"Capacity$")]
    mm = min((d for _, d in mem), default=0)
    ms = min((d for _, d in sto), default=0)
    base = total + mm + ms
    return {"base": base,
            "mem": [(k, base + d - mm) for k, d in mem] or [("", base)],
            "sto": [(k, base + d - ms) for k, d in sto] or [("", base)]}


def mac(id_, n, s, page, paths, chip_names, notes=None):
    """One row per chip; extra paths are higher-core versions of the same chip."""
    per = {}
    for c in ("us", "in"):
        r = raw(c, page)
        vs = []
        for p in paths:
            v = mac_variant(r.get(p, {}), c)
            if v is None:
                problems.append(f"{c} {page}/{p}: no total ({r.get(p, {}).get('error', 'not scraped')})")
            else:
                vs.append((p, v))
        per[c] = vs
    ref = per["us"] or per["in"]
    if not ref:
        problems.append(f"no prices for {n} {s}")
        return None
    base_path = ref[0][0]

    def pick(c, kind):
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
        us, inr = pick("us", kind), pick("in", kind)
        keys = list(dict.fromkeys(list(us) + list(inr)))
        keys.sort(key=lambda k: (us.get(k) or inr.get(k))[0])
        out = []
        for k in keys:
            p = (us.get(k) or inr.get(k))[1]
            label = k + (f" (needs {chip_names[p]})" if p != base_path and p in chip_names else "")
            out.append([label, us[k][0] if k in us else None, inr[k][0] if k in inr else None])
        return out

    mem, sto = rows("mem"), rows("sto")

    def full(c):  # most expensive memory on any variant + largest storage
        vs = per[c]
        if not vs:
            return None
        return max(max(x for _, x in v["mem"]) + max(x for _, x in v["sto"]) - v["base"] for _, v in vs)

    base = both(lambda c: per[c][0][1]["base"] if per[c] else None)
    top_mem = max((m for m in mem if m[1] is not None), key=lambda m: m[1], default=mem[-1])[0].split(" (")[0]
    top_sto = sto[-1][0]
    min_mem = mem[0][0] if mem and mem[0][0] else ""
    min_sto = sto[0][0] if sto and sto[0][0] else ""
    row = {"id": id_, "n": n, "s": s, "src": "apple",
           "base": [" · ".join(x for x in (chip_names.get(base_path, ""), min_mem, min_sto) if x)] + base,
           "mem": mem, "sto": sto}
    if len(mem) > 1 or len(sto) > 1:
        row["max"] = [f"Fully loaded · {top_mem} · {top_sto}".replace(" ·  · ", " · ")] + both(full)
    if notes:
        row["notes"] = notes
    return row


# ---------------- simple products: one total per path ----------------
def by_path(id_, n, s, page, paths, notes=None):
    tiers = []
    for p, label in paths:
        us = raw("us", page).get(p, {}).get("total")
        inr = raw("in", page).get(p, {}).get("total")
        if us is None and inr is None:
            problems.append(f"{page}/{p}: no total")
            continue
        tiers.append([label, us, inr])
    row = {"id": id_, "n": n, "s": s, "src": "apple", "tiers": tiers}
    if notes:
        row["notes"] = notes
    return row


def single(id_, n, s, page, notes=None, names=None):
    """One page, default choices. If an option group lists several priced choices (such as a charging case), each becomes a tier."""
    tiers = {}
    for ci, c in enumerate(("us", "in")):
        cfg = raw(c, page).get("default", {})
        for gname, labels in (cfg.get("options") or {}).items():
            if re.search(r"Color|colou?r|engraving|^app_", gname, re.I):
                continue
            priced = [(l, money(l, c)) for l in labels]
            if len(priced) > 1 and all(v is not None for _, v in priced):
                for i, (l, v) in enumerate(priced):
                    key = (names or [])[i] if names and i < len(names) else l.split(" From")[0].split(" Buy")[0].strip()
                    tiers.setdefault(key, [None, None])[ci] = v
                break
        else:
            if cfg.get("total") is not None:
                tiers.setdefault("Starting price", [None, None])[ci] = cfg["total"]
    if not tiers:
        problems.append(f"{page}: no prices")
    row = {"id": id_, "n": n, "s": s, "src": "apple", "tiers": [[k, v[0], v[1]] for k, v in tiers.items()]}
    if notes:
        row["notes"] = notes
    return row


# ---------------- catalogue ----------------
CATEGORIES = {
    "iphone": {"name": "iPhone", "unit": "Storage", "products": [
        iphone("duo", "iPhone Duo", "Foldable", "iphone-duo"),
        iphone("18pm", "18 Pro Max", "iPhone", "iphone-18-pro", "promax"),
        iphone("18p", "18 Pro", "iPhone", "iphone-18-pro", "pro"),
        iphone("air", "iPhone Air", "iPhone", "iphone-air"),
        iphone("17", "iPhone 17", "iPhone", "iphone-17"),
        iphone("16", "iPhone 16", "iPhone", "iphone-16", single_label="128GB"),
        iphone("17e", "iPhone 17e", "iPhone", "iphone-17e"),
    ]},
    # iPad: Wi-Fi storage tiers [label, usd, inr]; cellular and nano-texture glass add a fixed amount.
    "ipad": {"name": "iPad", "unit": "Storage", "nano": True,
             "variants": [["wifi", "Wi-Fi"], ["cellular", "Wi-Fi + Cellular"]], "products": [
        {"id": "pro13", "n": "iPad Pro", "s": "13-inch · M5", "src": "apple",
         "wifi": [["256GB", 1499, 199900], ["512GB", 1699, 223900], ["1TB", 2099, 271900], ["2TB", 2599, 331900]],
         "cell": [200, 24000], "nano": ["1TB", "2TB"], "nanoAdd": [100, 12000]},
        {"id": "pro11", "n": "iPad Pro", "s": "11-inch · M5", "src": "apple",
         "wifi": [["256GB", 1199, 139900], ["512GB", 1399, 163900], ["1TB", 1799, 211900], ["2TB", 2299, 271900]],
         "cell": [200, 24000], "nano": ["1TB", "2TB"], "nanoAdd": [100, 12000]},
        {"id": "air13", "n": "iPad Air", "s": "13-inch · M4", "src": "apple",
         "wifi": [["128GB", 949, 119900], ["256GB", 1049, 131900], ["512GB", 1249, 155900], ["1TB", 1549, 191900]],
         "cell": [150, 18000], "nano": [], "nanoAdd": [0, 0]},
        {"id": "air11", "n": "iPad Air", "s": "11-inch · M4", "src": "apple",
         "wifi": [["128GB", 749, 89900], ["256GB", 849, 101900], ["512GB", 1049, 125900], ["1TB", 1349, 161900]],
         "cell": [150, 18000], "nano": [], "nanoAdd": [0, 0]},
        {"id": "mini", "n": "iPad mini", "s": "A20 Pro", "src": "apple",
         "wifi": [["128GB", 699, 82900], ["256GB", 799, 94900], ["512GB", 999, 118900]],
         "cell": [150, 18000], "nano": ["256GB", "512GB"], "nanoAdd": [100, 12000]},
        {"id": "ipad", "n": "iPad", "s": "A16", "src": "apple",
         "wifi": [["128GB", 449, 49900], ["256GB", 549, 61900], ["512GB", 749, 85900]],
         "cell": [150, 18000], "nano": [], "nanoAdd": [0, 0]},
    ]},
    "mac": {"name": "Mac", "unit": "Memory · Storage", "mac": True,
            "axis": {"us": {"cap": 10000}, "in": {"cap": 1200000}}, "products": [
        mac("studio-ultra", "Mac Studio", "M5 Ultra", "mac-studio", ["m5ultra-64", "m5ultra-80"],
            {"m5ultra-64": "30-core CPU", "m5ultra-80": "36-core CPU, 80-core GPU"}),
        mac("studio-max", "Mac Studio", "M5 Max", "mac-studio", ["m5max-32", "m5max-40"],
            {"m5max-32": "32-core GPU", "m5max-40": "40-core GPU"}),
        mac("mbp16-max", "MacBook Pro 16", "M5 Max", "macbook-pro", ["16-m5max-32", "16-m5max-40"],
            {"16-m5max-32": "32-core GPU", "16-m5max-40": "40-core GPU"}, ["Nano-texture display adds $150 / ₹18,000"]),
        mac("mbp16-pro", "MacBook Pro 16", "M5 Pro", "macbook-pro", ["16-m5pro-18"],
            {"16-m5pro-18": "M5 Pro"}, ["Nano-texture display adds $150 / ₹18,000"]),
        mac("mbp14-max", "MacBook Pro 14", "M5 Max", "macbook-pro", ["14-m5max-32", "14-m5max-40"],
            {"14-m5max-32": "32-core GPU", "14-m5max-40": "40-core GPU"}, ["Nano-texture display adds $150 / ₹18,000"]),
        mac("mbp14-pro", "MacBook Pro 14", "M5 Pro", "macbook-pro", ["14-m5pro-15", "14-m5pro-18"],
            {"14-m5pro-15": "15-core CPU", "14-m5pro-18": "18-core CPU, 20-core GPU"}, ["Nano-texture display adds $150 / ₹18,000"]),
        mac("mbp14-m6", "MacBook Pro 14", "M6", "macbook-pro", ["14-m6"],
            {"14-m6": "M6"}, ["Nano-texture display adds $150 / ₹18,000"]),
        mac("imac4", "iMac", "M6 · 4 ports", "imac", ["4port"], {"4port": "12-core GPU"}),
        mac("imac2", "iMac", "M6 · 2 ports", "imac", ["2port"], {"2port": "10-core GPU"}),
        mac("mba15", "MacBook Air 15", "M5", "macbook-air", ["15"], {"15": "M5"}),
        mac("mba13", "MacBook Air 13", "M5", "macbook-air", ["13-8gpu", "13-10gpu"],
            {"13-8gpu": "8-core GPU", "13-10gpu": "10-core GPU"}),
        mac("mini-pro", "Mac mini", "M5 Pro", "mac-mini", ["m5pro-15", "m5pro-18"],
            {"m5pro-15": "15-core CPU", "m5pro-18": "18-core CPU, 20-core GPU"}, ["10 Gigabit Ethernet adds $100 / ₹12,000"]),
        mac("mini", "Mac mini", "M6", "mac-mini", ["m6"], {"m6": "M6"}, ["10 Gigabit Ethernet adds $100 / ₹12,000"]),
        by_path("neo", "MacBook Neo", "A-series", "macbook-neo", [("256", "256GB"), ("512", "512GB · Touch ID")]),
    ]},
    "watch": {"name": "Watch", "title": "Apple Watch lineup", "unit": "Size · Connectivity", "def": "s12",
              "axis": {"us": {"min": 200, "cap": 1000, "step": 200}, "in": {"min": 20000, "cap": 120000, "step": 20000}},
              "products": [
        {"id": "hultra", "n": "Hermès Ultra 4", "s": "49mm titanium", "src": "reported",
         "tiers": [["Starting price", 1499, None]], "notes": ["Hermès prices are US starting prices from press coverage"]},
        {"id": "hs12", "n": "Hermès Series 12", "s": "Apple Watch", "src": "reported",
         "tiers": [["Starting price", 1399, None]], "notes": ["Hermès prices are US starting prices from press coverage"]},
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
        iphone("vision", "Vision Pro", "M5", "apple-vision-pro"),
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
    "countries": {
        "us": {"label": "United States", "short": "US", "symbol": "$", "locale": "en-US",
               "note": "Apple Store US list prices, before sales tax"},
        "in": {"label": "India", "short": "IN", "symbol": "₹", "locale": "en-IN",
               "note": "Apple Store India MRP, including GST"},
    },
    "order": list(CATEGORIES),
    "categories": {k: {**v, "products": [p for p in v["products"] if p]} for k, v in CATEGORIES.items()},
}


# ---------------- diff against the current prices.js ----------------
def flat(data):
    out = {}
    for ck, c in data["categories"].items():
        for p in c["products"]:
            name = f"{p['n']} {p['s']}"
            rows = p.get("tiers") or p.get("wifi") or []
            if "base" in p:
                rows = [p["base"]] + [[f"+{r[0]} memory"] + r[1:] for r in p["mem"]] + [[f"+{r[0]} storage"] + r[1:] for r in p["sto"]] + ([p["max"]] if "max" in p else [])
            for r in rows:
                for i, cc in ((1, "US"), (2, "IN")):
                    out[f"{name} · {r[0]} · {cc}"] = r[i]
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
for title, items in (("Changed", changes), ("New", added if old else []), ("Removed", gone), ("Problems", ["  " + p for p in problems])):
    if items:
        print(f"\n{title}:\n" + "\n".join(items))

if "--check" not in sys.argv:
    OUT.write_text("// Generated by scripts/build_prices.py from Apple Store scrapes. Do not edit by hand.\nwindow.PRICES = "
                   + json.dumps(DATA, ensure_ascii=False, indent=1) + ";\n")
    print(f"\nWrote {OUT.relative_to(ROOT)}")
