"""Livres de coloriage : les images des histoires du jour deviennent des pages à colorier (dessin au trait),
gardées dans state/coloring/, puis assemblées en livre PDF (intérieur + couverture) au format Amazon KDP.

Format : 11 x 8,5 pouces (paysage), 300 dpi, une page à colorier au recto, verso blanc (le feutre ne traverse pas).
"""
import json
import re
import shutil
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "state" / "coloring"
BOOKS = ROOT / "output" / "books"
FONT = ROOT / "assets" / "fonts" / "Poppins-Bold.ttf"
DPI = 300
PAGE_W, PAGE_H = 11 * DPI, int(8.5 * DPI)          # 3300 x 2550
MARGIN = 150
ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}$")
TARGET_DENSITY = 0.07   # part de pixels noirs visée : ni page vide, ni page noire


# ---------- Dessin au trait ----------
def line_art(src, width=1600):
    """Image couleur -> tracé noir sur blanc (tableau uint8, 0 = trait), lisible et facile à colorier."""
    import cv2
    import numpy as np
    img = cv2.cvtColor(np.asarray(src.convert("RGB")), cv2.COLOR_RGB2BGR)
    h, w = img.shape[:2]
    img = cv2.resize(img, (width, round(h * width / w)), interpolation=cv2.INTER_AREA)
    img = cv2.pyrMeanShiftFiltering(img, 8, 22)             # aplats : supprime textures et grain
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.bilateralFilter(gray, 7, 40, 40)
    med = float(np.median(gray))
    edges = cv2.Canny(gray, max(20, 0.55 * med), max(60, 1.25 * med))
    edges = cv2.dilate(edges, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))   # traits assez épais pour colorier
    # retire les petites taches isolées
    n, lab, stats, _ = cv2.connectedComponentsWithStats(edges, connectivity=8)
    keep = np.zeros(n, dtype=bool)
    keep[1:] = stats[1:, cv2.CC_STAT_AREA] >= max(40, width * 0.035)
    edges = np.where(keep[lab], 255, 0).astype(np.uint8)
    return 255 - edges


def density(art):
    return float((art < 128).mean())


def score(art):
    """Plus proche de la densité visée = meilleure page à colorier."""
    return -abs(density(art) - TARGET_DENSITY)


# ---------- Page ----------
def _font(size):
    return ImageFont.truetype(str(FONT), size)


def compose_page(art, title, caption, footer=""):
    """Page 11 x 8,5 po : dessin encadré, titre de l'histoire et une phrase du récit en légende."""
    page = Image.new("L", (PAGE_W, PAGE_H), 255)
    d = ImageDraw.Draw(page)
    box_w, box_h = PAGE_W - 2 * MARGIN, 1700            # zone du dessin ; le texte occupe le bas de la page
    im = Image.fromarray(art)
    k = min(box_w / im.width, box_h / im.height)
    aw, ah = round(im.width * k), round(im.height * k)
    im = im.resize((aw, ah), Image.LANCZOS).point(lambda v: 255 if v > 150 else 0)
    left, top = MARGIN + (box_w - aw) // 2, MARGIN
    page.paste(im, (left, top))
    d.rectangle((left - 6, top - 6, left + aw + 6, top + ah + 6), outline=0, width=9)
    y = max(top + ah, MARGIN + 1700 - 40) + 70
    d.text((MARGIN, y), title[:70], font=_font(76), fill=0)
    y += 120
    f = _font(46)
    for line in textwrap.wrap(caption, width=100)[:3]:
        d.text((MARGIN, y), line, font=f, fill=0)
        y += 66
    if footer:
        d.text((PAGE_W - MARGIN, PAGE_H - 90), footer, font=_font(36), fill=0, anchor="rs")
    return page.point(lambda v: 255 if v > 128 else 0).convert("1")


# ---------- Collecte quotidienne ----------
def harvest(day, script, images_dir, per_day=3, footer=""):
    """Choisit les `per_day` meilleures scènes de la vidéo longue du jour et enregistre leurs pages.
    Renvoie la liste des identifiants créés."""
    if script.get("kind") == "recap":
        return []                              # l'actualité ne se colorie pas
    images = sorted(Path(images_dir).glob("scene_*.jpg"))
    scenes = script.get("scenes", [])
    if not images or not scenes:
        return []
    DIR.mkdir(parents=True, exist_ok=True)
    for old in DIR.glob(f"{day}_*"):           # une relance remplace les pages du jour
        shutil.rmtree(old, ignore_errors=True)
    cands = []
    for i, p in enumerate(images[: len(scenes)]):
        narr = scenes[i].get("narration", "")
        if scenes[i].get("cta") or len(narr.split()) < 8:
            continue                            # phrases d'abonnement et plans trop courts
        art = line_art(Image.open(p))
        cands.append((score(art), i, art, p))
    cands.sort(key=lambda c: -c[0])
    chosen = []
    for c in cands:                             # au moins 3 plans d'écart : pages variées
        if all(abs(c[1] - k[1]) >= 3 for k in chosen):
            chosen.append(c)
        if len(chosen) == per_day:
            break
    made = []
    for n, (_, i, art, p) in enumerate(sorted(chosen, key=lambda c: c[1])):
        pid = f"{day}_{n:02d}"
        out = DIR / pid
        out.mkdir(parents=True)
        compose_page(art, script.get("title", ""), scenes[i]["narration"], footer).save(out / "page.png", dpi=(DPI, DPI))
        thumb = Image.open(out / "page.png").convert("L").resize((660, 510), Image.LANCZOS)
        thumb.save(out / "thumb.jpg", quality=80)
        src = Image.open(p).convert("RGB")
        src.thumbnail((1600, 1600))
        src.save(out / "color.jpg", quality=88)  # sert de couverture
        (out / "meta.json").write_text(json.dumps(
            {"id": pid, "day": day, "title": script.get("title", ""), "topic": script.get("topic", ""),
             "narration": scenes[i]["narration"], "scene": i}, ensure_ascii=False, indent=1))
        made.append(pid)
    return made


def list_pages():
    pages = []
    for d in sorted(DIR.glob("*_*")) if DIR.exists() else []:
        try:
            pages.append(json.loads((d / "meta.json").read_text()))
        except (OSError, ValueError):
            continue
    return pages


def delete_page(pid):
    if ID_RE.match(pid):
        shutil.rmtree(DIR / pid, ignore_errors=True)


# ---------- Livre ----------
def _text_page(lines, sizes, center=True):
    page = Image.new("1", (PAGE_W, PAGE_H), 1)
    d = ImageDraw.Draw(page)
    y = PAGE_H // 2 - sum(s * 1.6 for s in sizes) // 2
    for text, size in zip(lines, sizes):
        d.text((PAGE_W // 2 if center else MARGIN, y), text, font=_font(size), fill=0, anchor="ma" if center else "la")
        y += size * 1.6
    return page


def spine_width(n_pages, paper="white"):
    """Épaisseur du dos en pouces (KDP : 0,002252 po/page papier blanc, 0,0025 papier crème)."""
    return n_pages * (0.002252 if paper == "white" else 0.0025)


def build_book(title, author, subtitle="", ids=None, blank_between=True, paper="white", name=None):
    """Assemble intérieur PDF + couverture (face avant et couverture complète) + fiche de publication."""
    pages = [p for p in list_pages() if ids is None or p["id"] in ids]
    if len(pages) < 12:
        raise ValueError(f"Il faut au moins 12 pages à colorier ({len(pages)} disponibles) ; KDP en exige 24 en tout.")
    name = name or re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40] or "livre"
    out = BOOKS / name
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    seq = [_text_page([title, subtitle or " "], [110, 60]),
           _text_page(["This book belongs to:", "______________________"], [70, 70]),
           ]
    import datetime as _dt
    seq.append(_text_page([f"Copyright © {_dt.date.today().year} {author}", "All rights reserved.",
                           "Illustrations created with the help of AI and edited by the author."], [48, 48, 40]))
    seq.append(Image.new("1", (PAGE_W, PAGE_H), 1))
    for p in pages:
        seq.append(Image.open(DIR / p["id"] / "page.png").convert("1"))
        if blank_between:
            seq.append(Image.new("1", (PAGE_W, PAGE_H), 1))
    if len(seq) % 2:                                      # nombre pair de pages
        seq.append(Image.new("1", (PAGE_W, PAGE_H), 1))
    if len(seq) < 24:
        raise ValueError(f"Le livre ferait {len(seq)} pages ; KDP en exige 24 au moins : ajoute des pages ou coche le verso blanc.")
    seq[0].save(out / "interior.pdf", save_all=True, append_images=seq[1:], resolution=DPI)
    n = len(seq)

    # couverture : image couleur de la meilleure page + titre
    cover_src = Image.open(DIR / pages[len(pages) // 2]["id"] / "color.jpg").convert("RGB")
    front = _cover_front(cover_src, title, subtitle, author)
    front.save(out / "cover_front.png", dpi=(DPI, DPI))
    sp = spine_width(n, paper)
    bleed = 0.125
    wrap_w = round((2 * 11 + sp + 2 * bleed) * DPI)
    wrap_h = round((8.5 + 2 * bleed) * DPI)
    wrap = Image.new("RGB", (wrap_w, wrap_h), (30, 36, 58))
    fx = wrap_w - round((11 + bleed) * DPI)
    wrap.paste(front.resize((round(11 * DPI), round(8.5 * DPI))), (fx, round(bleed * DPI)))
    if sp > 0.1:
        sw = round(sp * DPI)
        txt = Image.new("RGBA", (round(8.5 * DPI), sw), (0, 0, 0, 0))
        ImageDraw.Draw(txt).text((txt.width // 2, sw // 2), title[:40], font=_font(max(24, sw // 2)), fill="white", anchor="mm")
        wrap.paste(txt.rotate(90, expand=True), (round((11 + bleed) * DPI), round(bleed * DPI)), txt.rotate(90, expand=True))
    wrap.save(out / "cover_wrap.pdf", resolution=DPI)
    (out / "listing.txt").write_text(_listing(title, subtitle, author, n, sp, len(pages)), encoding="utf-8")
    return {"name": name, "pages": n, "illustrations": len(pages), "spine": round(sp, 3)}


def _cover_front(src, title, subtitle, author):
    W, H = 11 * DPI, round(8.5 * DPI)
    im = src.copy()
    scale = max(W / im.width, H / im.height)
    im = im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS)
    im = im.crop(((im.width - W) // 2, (im.height - H) // 2, (im.width - W) // 2 + W, (im.height - H) // 2 + H))
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    d.rectangle((0, 0, W, 560), fill=(20, 24, 40, 205))
    d.text((W // 2, 150), title[:46], font=_font(150), fill="white", anchor="mm")
    d.text((W // 2, 330), (subtitle or "Coloring Book")[:60], font=_font(70), fill=(255, 214, 120), anchor="mm")
    d.text((W // 2, 460), author, font=_font(56), fill="white", anchor="mm")
    return Image.alpha_composite(im.convert("RGBA"), ov).convert("RGB")


def _listing(title, subtitle, author, n_pages, spine, n_illus):
    return f"""FICHE DE PUBLICATION AMAZON KDP (à vérifier dans KDP avant envoi)

Titre : {title}
Sous-titre : {subtitle}
Auteur : {author}
Format : 11 x 8,5 po (27,94 x 21,59 cm), paysage, intérieur noir et blanc, papier blanc, finition mate
Nombre de pages : {n_pages} dont {n_illus} pages à colorier (verso blanc)
Dos de la couverture : {spine:.3f} po (calcul : pages x 0,002252) ; confirme les dimensions exactes avec le calculateur KDP
Fichiers : interior.pdf (intérieur) ; cover_wrap.pdf (couverture complète) ; cover_front.png (face avant)

Contenu généré avec l'aide d'une IA : à DÉCLARER dans le formulaire KDP (« contenu généré par IA : oui »).

Description (anglais) :
Travel through forgotten history, one page at a time. This coloring book turns {n_illus} little-known stories
of the past into detailed illustrations to color, each with a short caption telling the story behind the scene.
Single-sided pages so markers do not bleed through. Perfect for curious adults, teens and history lovers.

Mots-clés (7 max) : history coloring book; coloring book for adults; anime coloring pages; relaxing coloring;
educational coloring book; ancient history gifts; mindfulness coloring
"""
