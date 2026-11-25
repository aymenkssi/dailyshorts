"""Infographie quotidienne pour le public américain : une donnée officielle (U.S. Census Bureau, ACS 5 ans)
par État, présentée en carte colorée ou en classement.

Aucune donnée n'est inventée : les chiffres sont téléchargés chez le Census Bureau, contrôlés (51 États,
valeurs plausibles) puis dessinés. Les textes sont écrits d'avance dans le catalogue ci-dessous.

    python -m shorts.infographic --check            # télécharge et affiche haut/bas de chaque jeu de données
    python -m shorts.infographic 2026-11-26 sortie/ # génère l'image du jour dans un dossier
"""
import datetime as dt
import json
import os
import sys
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
FONT = ROOT / "assets" / "fonts" / "Poppins-Bold.ttf"
GEO = ROOT / "assets" / "us_states.json"
CENSUS = "https://api.census.gov/data/{year}/acs/acs5"
YEARS = [int(y) for y in os.environ.get("CENSUS_YEARS", "2024,2023,2022").split(",")]
W, H = 1080, 1350

ABBR = {"01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA", "08": "CO", "09": "CT", "10": "DE", "11": "DC",
        "12": "FL", "13": "GA", "15": "HI", "16": "ID", "17": "IL", "18": "IN", "19": "IA", "20": "KS", "21": "KY",
        "22": "LA", "23": "ME", "24": "MD", "25": "MA", "26": "MI", "27": "MN", "28": "MS", "29": "MO", "30": "MT",
        "31": "NE", "32": "NV", "33": "NH", "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND", "39": "OH",
        "40": "OK", "41": "OR", "42": "PA", "44": "RI", "45": "SC", "46": "SD", "47": "TN", "48": "TX", "49": "UT",
        "50": "VT", "51": "VA", "53": "WA", "54": "WV", "55": "WI", "56": "WY"}

money = lambda v: f"${v:,.0f}"  # noqa: E731
pct = lambda v: f"{v:.1f}%"  # noqa: E731


def short_num(v):
    return f"{v / 1e6:.1f}M" if v >= 1e6 else f"{v / 1e3:.0f}K"


def short_money(v):
    return f"${v / 1e6:.2f}M" if v >= 1e6 else f"${v / 1e3:.0f}K"


def _ratio(num, den):
    return lambda g: 100.0 * sum(g(c) for c in num) / sum(g(c) for c in den)


# key, variables, calcul, format, plage plausible (sinon on refuse le jeu de données), textes.
# head/pill : titre ; sub : sous-titre ; tags : hashtags.
CATALOG = [
    dict(key="income", vars=["B19013_001E"], calc=lambda g: g("B19013_001E"), fmt=money, rng=(40e3, 150e3),
         head="THE TYPICAL HOUSEHOLD INCOME", pill="IN YOUR STATE", sub="Median household income, by state", tags="#income #money"),
    dict(key="age", vars=["B01002_001E"], calc=lambda g: g("B01002_001E"), fmt=lambda v: f"{v:.1f} yrs", rng=(28, 50),
         head="HOW OLD IS THE MEDIAN RESIDENT", pill="IN YOUR STATE?", sub="Median age, by state", tags="#age #demographics"),
    dict(key="rent", vars=["B25064_001E"], calc=lambda g: g("B25064_001E"), fmt=money, rng=(600, 2800),
         head="THE MEDIAN MONTHLY RENT", pill="IN YOUR STATE", sub="Median gross rent per month, by state", tags="#rent #housing"),
    dict(key="commute", vars=["B08013_001E", "B08301_001E", "B08301_021E"],
         calc=lambda g: g("B08013_001E") / (g("B08301_001E") - g("B08301_021E")), fmt=lambda v: f"{v:.0f} min", rng=(12, 40),
         head="THE AVERAGE COMMUTE TO WORK", pill="IN YOUR STATE", sub="Average one-way commute time, by state", tags="#commute #traffic"),
    dict(key="wfh", vars=["B08301_021E", "B08301_001E"], calc=_ratio(["B08301_021E"], ["B08301_001E"]), fmt=pct, rng=(1, 50),
         head="WHO WORKS FROM HOME THE MOST", pill="ACROSS AMERICA", sub="Share of workers who work from home, by state", tags="#remotework #wfh"),
    dict(key="homevalue", vars=["B25077_001E"], calc=lambda g: g("B25077_001E"), fmt=short_money, rng=(80e3, 1.5e6),
         head="THE MEDIAN HOME VALUE", pill="IN YOUR STATE", sub="Median value of owner-occupied homes, by state", tags="#realestate #homes"),
    dict(key="transit", vars=["B08301_010E", "B08301_001E"], calc=_ratio(["B08301_010E"], ["B08301_001E"]), fmt=pct, rng=(0, 40),
         head="WHO TAKES PUBLIC TRANSIT THE MOST", pill="ACROSS AMERICA", sub="Share of workers commuting by public transportation", tags="#publictransit #commute"),
    dict(key="bachelors", vars=["B15003_022E", "B15003_023E", "B15003_024E", "B15003_025E", "B15003_001E"],
         calc=_ratio(["B15003_022E", "B15003_023E", "B15003_024E", "B15003_025E"], ["B15003_001E"]), fmt=pct, rng=(10, 70),
         head="THE MOST EDUCATED STATES", pill="IN AMERICA", sub="Adults 25+ with a bachelor's degree or higher", tags="#education #college"),
    dict(key="native", vars=["B05002_003E", "B05002_001E"], calc=_ratio(["B05002_003E"], ["B05002_001E"]), fmt=pct, rng=(15, 90),
         head="WHO STAYS PUT: BORN AND LIVING", pill="IN THE SAME STATE", sub="Share of residents born in the state where they live", tags="#hometown #migration"),
    dict(key="homeowners", vars=["B25003_002E", "B25003_001E"], calc=_ratio(["B25003_002E"], ["B25003_001E"]), fmt=pct, rng=(30, 90),
         head="WHERE MOST PEOPLE OWN THEIR HOME", pill="IN AMERICA", sub="Homeownership rate, by state", tags="#homeownership #housing"),
    dict(key="foreign", vars=["B05002_013E", "B05002_001E"], calc=_ratio(["B05002_013E"], ["B05002_001E"]), fmt=pct, rng=(0.5, 40),
         head="SHARE OF RESIDENTS BORN ABROAD", pill="IN YOUR STATE", sub="Foreign-born population, by state", tags="#immigration #demographics"),
    dict(key="novehicle", vars=["B25044_003E", "B25044_010E", "B25044_001E"],
         calc=_ratio(["B25044_003E", "B25044_010E"], ["B25044_001E"]), fmt=pct, rng=(1, 40),
         head="HOUSEHOLDS WITHOUT A CAR", pill="IN YOUR STATE", sub="Share of households with no vehicle available", tags="#cars #transportation"),
    dict(key="poverty", vars=["B17001_002E", "B17001_001E"], calc=_ratio(["B17001_002E"], ["B17001_001E"]), fmt=pct, rng=(4, 30),
         head="THE POVERTY RATE", pill="IN YOUR STATE", sub="Share of people living below the poverty line", tags="#poverty #economy"),
    dict(key="spanish", vars=["B16001_003E", "B16001_001E"], calc=_ratio(["B16001_003E"], ["B16001_001E"]), fmt=pct, rng=(0.5, 55),
         head="WHERE PEOPLE SPEAK SPANISH AT HOME", pill="ACROSS AMERICA", sub="Share of residents (5+) who speak Spanish at home", tags="#spanish #language"),
    dict(key="pci", vars=["B19301_001E"], calc=lambda g: g("B19301_001E"), fmt=money, rng=(20e3, 90e3),
         head="INCOME PER PERSON", pill="IN YOUR STATE", sub="Per capita income in the past 12 months, by state", tags="#income #economy"),
    dict(key="veterans", vars=["B21001_002E", "B21001_001E"], calc=_ratio(["B21001_002E"], ["B21001_001E"]), fmt=pct, rng=(3, 20),
         head="THE MOST VETERANS", pill="PER STATE", sub="Veterans as a share of the adult civilian population", tags="#veterans #military"),
    dict(key="hhsize", vars=["B25010_001E"], calc=lambda g: g("B25010_001E"), fmt=lambda v: f"{v:.2f}", rng=(1.5, 4),
         head="THE BIGGEST HOUSEHOLDS", pill="IN AMERICA", sub="Average household size, by state", tags="#family #households"),
    dict(key="alone", vars=["B11001_008E", "B11001_001E"], calc=_ratio(["B11001_008E"], ["B11001_001E"]), fmt=pct, rng=(15, 60),
         head="WHO LIVES ALONE THE MOST", pill="IN YOUR STATE", sub="Share of households with one person living alone", tags="#livingalone #households"),
    dict(key="internet", vars=["B28002_002E", "B28002_001E"], calc=_ratio(["B28002_002E"], ["B28002_001E"]), fmt=pct, rng=(60, 99),
         head="HOUSEHOLDS WITH INTERNET AT HOME", pill="IN YOUR STATE", sub="Share of households with an internet subscription", tags="#internet #broadband"),
    dict(key="drivealone", vars=["B08301_003E", "B08301_001E"], calc=_ratio(["B08301_003E"], ["B08301_001E"]), fmt=pct, rng=(15, 92),
         head="WHO DRIVES TO WORK ALONE", pill="ACROSS AMERICA", sub="Share of workers who drive alone to work", tags="#driving #commute"),
    dict(key="gini", vars=["B19083_001E"], calc=lambda g: g("B19083_001E"), fmt=lambda v: f"{v:.3f}", rng=(0.3, 0.6),
         head="THE MOST UNEQUAL STATES", pill="IN AMERICA", sub="Gini index of income inequality (0 = equal, 1 = unequal)", tags="#inequality #economy"),
    dict(key="year", vars=["B25035_001E"], calc=lambda g: g("B25035_001E"), fmt=lambda v: f"{v:.0f}", rng=(1940, 2015),
         head="HOW OLD IS THE HOUSING", pill="IN YOUR STATE?", sub="Median year homes were built, by state", tags="#housing #history"),
    dict(key="pop", vars=["B01003_001E"], calc=lambda g: g("B01003_001E"), fmt=short_num, rng=(3e5, 5e7),
         head="THE MOST POPULATED STATES", pill="IN AMERICA", sub="Total population, by state", tags="#population #usa"),
    dict(key="married", vars=["B11001_003E", "B11001_001E"], calc=_ratio(["B11001_003E"], ["B11001_001E"]), fmt=pct, rng=(10, 70),
         head="MARRIED-COUPLE HOUSEHOLDS", pill="IN YOUR STATE", sub="Share of households headed by a married couple", tags="#marriage #family"),
    dict(key="bikewalk", vars=["B08301_018E", "B08301_019E", "B08301_001E"],
         calc=_ratio(["B08301_018E", "B08301_019E"], ["B08301_001E"]), fmt=pct, rng=(0.5, 15),
         head="WHO BIKES OR WALKS TO WORK", pill="IN YOUR STATE", sub="Share of workers commuting by bicycle or on foot", tags="#walking #cycling"),
]
assert len(CATALOG) % 2 == 1, "catalogue : nombre impair requis (carte et classement alternent d'un tour à l'autre)"


def choose(day):
    """(jeu de données, style) du jour : rotation déterministe, carte et classement en alternance."""
    n = dt.date.fromisoformat(day).toordinal()
    i = n % len(CATALOG)
    return i, "map" if (n // len(CATALOG) + i) % 2 == 0 else "bars"


# ---------------- données ----------------
def fetch(ds):
    """{fips: valeur} pour 50 États + DC, avec l'année la plus récente disponible. Lève RuntimeError si douteux."""
    last = None
    if not os.environ.get("CENSUS_API_KEY"):
        raise RuntimeError("clé Census manquante : CENSUS_API_KEY dans .env (gratuite : https://api.census.gov/data/key_signup.html)")
    for year in YEARS:
        try:
            params = {"get": "NAME," + ",".join(ds["vars"]), "for": "state:*"}
            if os.environ.get("CENSUS_API_KEY"):
                params["key"] = os.environ["CENSUS_API_KEY"]
            r = requests.get(CENSUS.format(year=year), params=params, timeout=60,
                             headers={"User-Agent": "Mozilla/5.0 (compatible; dailyshorts/1.0)", "Accept": "application/json"})
            if "missing_key" in r.url or "Invalid Key" in r.text[:600] or "Missing Key" in r.text[:600]:
                raise RuntimeError("clé Census refusée : vérifie CENSUS_API_KEY (activée par le lien reçu par e-mail ?)")
            if not r.ok or not r.text.strip().startswith("["):
                last = (last + " | " if last else "") + f"{year} : HTTP {r.status_code} {r.text.strip()[:120]!r}"
                continue
            rows = r.json()
            head = rows[0]
            out = {}
            for row in rows[1:]:
                rec = dict(zip(head, row))
                fips = rec["state"]
                if fips not in ABBR:
                    continue
                vals = {v: float(rec[v]) for v in ds["vars"]}
                if any(x < 0 for x in vals.values()):  # codes « donnée absente » du Census
                    raise RuntimeError(f"valeur manquante pour {fips}")
                out[fips] = ds["calc"](lambda c: vals[c])
            if len(out) != 51:
                raise RuntimeError(f"{len(out)} États au lieu de 51")
            lo, hi = ds["rng"]
            bad = [k for k, v in out.items() if not lo <= v <= hi]
            if bad:
                raise RuntimeError(f"valeurs hors de la plage plausible {ds['rng']} : {[ABBR[b] for b in bad][:5]}")
            return year, out
        except (requests.RequestException, ValueError, KeyError, ZeroDivisionError, RuntimeError) as e:
            if "clé Census" in str(e):
                raise
            last = (last + " | " if last else "") + f"{year} : {e}"
    raise RuntimeError(f"jeu de données « {ds['key']} » indisponible ({last})")


# ---------------- dessin ----------------
def font(size):
    return ImageFont.truetype(str(FONT), size)


def wrap(d, text, f, width):
    lines, cur = [], ""
    for w in text.split():
        t = f"{cur} {w}".strip()
        if d.textlength(t, font=f) <= width or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w
    return lines + [cur]


PALETTE = ["#DCE9FB", "#A9C8F5", "#6FA0EA", "#3C72D6", "#1E4BAF"]


def header(img, ds):
    d = ImageDraw.Draw(img)
    for y in range(H):  # fond bleu très clair, dégradé vertical
        t = y / H
        d.line([(0, y), (W, y)], fill=(int(226 + 29 * t), int(238 + 17 * t), 255))
    f = font(62)
    y = 60
    for line in wrap(d, ds["head"], f, W - 120):
        d.text((60, y), line, font=f, fill="#101828")
        y += 76
    fp = font(62)
    tw = d.textlength(ds["pill"], font=fp)
    d.rounded_rectangle((60, y + 2, 60 + tw + 44, y + 84), radius=10, fill="#2B63D9")
    d.text((82, y + 43), ds["pill"], font=fp, fill="white", anchor="lm")
    y += 110
    fs = font(28)
    d.text((60, y), ds["sub"], font=fs, fill="#475467")
    return d, y + 50


def footer(d, year):
    f = font(20)
    d.text((60, H - 46), f"Source: U.S. Census Bureau, American Community Survey 5-Year Estimates ({year - 4}–{year}).",
           font=f, fill="#667085")


def legend(d, y, ds, vals, bins):
    f = font(22)
    lo, hi = ds["fmt"](min(vals)), ds["fmt"](max(vals))
    d.text((60, y + 13), lo, font=f, fill="#344054", anchor="lm")
    x = 60 + int(d.textlength(lo, font=f)) + 16
    for c in PALETTE:
        d.rectangle((x, y - 2, x + 56, y + 28), fill=c)
        x += 56
    d.text((x + 16, y + 13), hi, font=f, fill="#344054", anchor="lm")


def quantile_bins(vals):
    s = sorted(vals)
    return [s[min(len(s) - 1, round(len(s) * q / 5))] for q in range(1, 5)]


def bin_of(v, bins):
    return sum(v >= b for b in bins)


def draw_map(ds, data, year):
    img = Image.new("RGB", (W, H))
    d, y0 = header(img, ds)
    geo = json.loads(GEO.read_text())
    vals = list(data.values())
    bins = quantile_bins(vals)
    S = 2  # supersampling pour des bords lisses
    area_w, area_h = W - 80, 600
    sc = min(area_w / 1015, area_h / 595)
    ox, oy = 40 + (area_w - 1015 * sc) / 2 + 57.6 * sc, y0 + 10 - 13 * sc
    layer = Image.new("RGBA", (W * S, H * S), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    labels = []
    for fips, g in geo.items():
        col = PALETTE[bin_of(data[fips], bins)]
        for ring in g["rings"]:
            pts = [(ox + x * sc, oy + y * sc) for x, y in ring]
            ld.polygon([(x * S, y * S) for x, y in pts], fill=col, outline="white")
            ld.line([(x * S, y * S) for x, y in pts + pts[:1]], fill="white", width=2 * S)
        biggest = max(g["rings"], key=lambda r: (max(p[0] for p in r) - min(p[0] for p in r)) * (max(p[1] for p in r) - min(p[1] for p in r)))
        xs = [ox + p[0] * sc for p in biggest]
        ys = [oy + p[1] * sc for p in biggest]
        labels.append((fips, (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, max(xs) - min(xs)))
    img.paste(layer.resize((W, H), Image.LANCZOS), (0, 0), layer.resize((W, H), Image.LANCZOS))
    d = ImageDraw.Draw(img)
    fl = font(17)
    for fips, cx, cy, w in labels:
        if w < 34:
            continue
        dark = bin_of(data[fips], bins) >= 3
        d.text((cx, cy), ABBR[fips], font=fl, fill="white" if dark else "#101828", anchor="mm")
    y = y0 + 10 + int(595 * sc) + 20
    legend(d, y, ds, vals, bins)
    top = sorted(data.items(), key=lambda kv: -kv[1])
    y += 62
    names = {k: v["name"] for k, v in geo.items()}
    for col, (title, items) in enumerate([("HIGHEST", top[:3]), ("LOWEST", top[::-1][:3])]):
        x = 60 + col * 500
        d.text((x, y), title, font=font(22), fill="#2B63D9")
        for i, (fips, v) in enumerate(items):
            d.text((x, y + 36 + i * 34), f"{i + 1}. {names[fips]}", font=font(24), fill="#101828")
            d.text((x + 440, y + 36 + i * 34), ds["fmt"](v), font=font(24), fill="#101828", anchor="ra")
    footer(d, year)
    return img


def draw_bars(ds, data, year):
    img = Image.new("RGB", (W, H))
    d, y0 = header(img, ds)
    geo = json.loads(GEO.read_text())
    names = {k: v["name"] for k, v in geo.items()}
    top = sorted(data.items(), key=lambda kv: -kv[1])
    shown = top[:10]
    lo, hi = min(data.values()), max(data.values())
    base = lo * 0.6 if lo > 0 and (hi - lo) / hi < 0.5 else 0  # axe tronqué quand les valeurs sont proches
    y = y0 + 20
    row = 62
    x0, bw = 330, 560
    for i, (fips, v) in enumerate(shown):
        yy = y + i * row
        d.text((60, yy + 24), f"{i + 1}", font=font(26), fill="#667085", anchor="lm")
        d.text((100, yy + 24), names[fips], font=font(25), fill="#101828", anchor="lm")
        w = max(12, int(bw * (v - base) / (hi - base)))
        d.rounded_rectangle((x0 + 40, yy + 6, x0 + 40 + w, yy + 42), radius=8, fill="#2B63D9" if i else "#1E4BAF")
        d.text((x0 + 40 + w + 10, yy + 24), ds["fmt"](v), font=font(23), fill="#101828", anchor="lm")
    y += 10 * row + 20
    d.line([(60, y), (W - 60, y)], fill="#C7D7F5", width=2)
    d.text((60, y + 18), "AND AT THE BOTTOM", font=font(22), fill="#2B63D9")
    for i, (fips, v) in enumerate(top[::-1][:3]):
        yy = y + 58 + i * 36
        d.text((60, yy), f"{len(top) - i}. {names[fips]}", font=font(25), fill="#101828")
        d.text((560, yy), ds["fmt"](v), font=font(25), fill="#101828", anchor="ra")
    d.text((60, H - 78), "50 states + DC · Where does yours land?", font=font(22), fill="#2B63D9")
    footer(d, year)
    return img


def caption(ds, data, year):
    geo = json.loads(GEO.read_text())
    names = {k: v["name"] for k, v in geo.items()}
    top = sorted(data.items(), key=lambda kv: -kv[1])
    (hf, hv), (lf, lv) = top[0], top[-1]
    title = f"{ds['head'].capitalize()} {ds['pill'].lower()}"
    text = (f"{ds['sub']}.\n\n🥇 Highest: {names[hf]} ({ds['fmt'](hv)})\n🔻 Lowest: {names[lf]} ({ds['fmt'](lv)})\n\n"
            f"Where does your state land? Tell us in the comments 👇\n\n"
            f"Source: U.S. Census Bureau, ACS 5-Year Estimates ({year - 4}–{year}).")
    tags = ["USA", "Maps", "Data", "Census"] + ds["tags"].replace("#", "").split()
    return {"title": title, "description": text, "hashtags": tags}


def generate(day, out_dir, start=0):
    """Crée l'image du jour (PNG + JPEG) et ses textes dans out_dir. Essaie le jeu de données suivant si besoin."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    i, style = choose(day)
    errors = []
    for k in range(len(CATALOG)):
        ds = CATALOG[(i + k) % len(CATALOG)]
        try:
            year, data = fetch(ds)
        except RuntimeError as e:
            errors.append(str(e))
            continue
        img = (draw_map if style == "map" else draw_bars)(ds, data, year)
        img.save(out_dir / "infographic.png")
        img.save(out_dir / "infographic.jpg", quality=93)
        meta = caption(ds, data, year) | {"dataset": ds["key"], "style": style, "year": year}
        (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
        return meta
    raise RuntimeError("aucun jeu de données utilisable : " + " | ".join(errors[:3]))


def check():
    """Contrôle de bout en bout (à lancer sur le VPS) : chaque jeu de données, avec son haut et son bas."""
    geo = json.loads(GEO.read_text())
    names = {k: v["name"] for k, v in geo.items()}
    bad = 0
    for ds in CATALOG:
        try:
            year, data = fetch(ds)
            t = sorted(data.items(), key=lambda kv: -kv[1])
            print(f"OK   {ds['key']:11} {year}  haut {names[t[0][0]]} {ds['fmt'](t[0][1])} · bas {names[t[-1][0]]} {ds['fmt'](t[-1][1])}")
        except RuntimeError as e:
            bad += 1
            print(f"ECHEC {ds['key']:10} {e}")
    return bad


if __name__ == "__main__":
    if sys.argv[1:] == ["--check"]:
        sys.exit(1 if check() else 0)
    print(json.dumps(generate(sys.argv[1], sys.argv[2]), ensure_ascii=False, indent=1))
