"""Actualité de la semaine : pages « Portal:Current events » de Wikipédia (anglais).

Un résumé quotidien, neutre et sourcé des événements mondiaux, sous licence CC BY-SA 4.0.
On récupère le texte brut de chaque jour, on le nettoie (liens, modèles, commentaires) et on
garde les sources citées (noms des médias) pour que le script puisse s'appuyer dessus.
"""
import datetime as dt
import re
import time

import requests

UA = "DailyShortsBot/1.0 (weekly news recap; +https://daily.creationapp.academy)"
RAW = "https://en.wikipedia.org/w/index.php"
SOURCE_CREDIT = ("Sources: Wikipedia, Portal:Current events (CC BY-SA 4.0) and the news outlets it cites.")


def page_title(day):
    return f"Portal:Current_events/{day.year}_{day.strftime('%B')}_{day.day}"


def fetch_raw(day, tries=3):
    for i in range(tries):
        try:
            r = requests.get(RAW, params={"title": page_title(day), "action": "raw"},
                             headers={"User-Agent": UA}, timeout=30)
            if r.status_code == 404:
                return ""
            r.raise_for_status()
            return r.text
        except requests.RequestException as e:
            if i == tries - 1:
                print(f"  Wikipédia indisponible pour le {day} : {e}")
                return ""
            time.sleep(5 * (i + 1))
    return ""


def _strip_templates(text):
    """Retire les modèles {{…}}, y compris imbriqués."""
    out, depth, i = [], 0, 0
    while i < len(text):
        if text.startswith("{{", i):
            depth += 1
            i += 2
        elif text.startswith("}}", i) and depth:
            depth -= 1
            i += 2
        else:
            if not depth:
                out.append(text[i])
            i += 1
    return "".join(out)


def clean(raw):
    """Wikitexte d'une journée → texte lisible : rubriques, puces indentées, sources entre parenthèses."""
    m = re.search(r"content\s*=", raw)
    body = raw[m.end():] if m else raw
    body = re.sub(r"<!--.*?-->", "", body, flags=re.S)
    body = re.sub(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>", "", body, flags=re.S)
    body = re.sub(r"\[\[(?:File|Image):[^\]]*\]\]", "", body)
    body = re.sub(r"\[\[[^\]|]*\|([^\]]*)\]\]", r"\1", body)        # [[cible|texte]] → texte
    body = re.sub(r"\[\[([^\]]*)\]\]", r"\1", body)                  # [[cible]] → cible
    body = re.sub(r"\[https?://\S+\s+([^\]]+)\]", r"\1", body)       # [url (Reuters)] → (Reuters)
    body = re.sub(r"\[https?://\S+\]", "", body)
    body = _strip_templates(body)
    body = re.sub(r"<[^>]+>", "", body)
    lines = []
    for line in body.splitlines():
        line = line.rstrip()
        h = re.fullmatch(r"\s*'''(.+?)'''\s*", line)
        if h:
            lines.append(f"## {h.group(1).strip()}")
            continue
        b = re.match(r"^(\*+)\s*(.*)$", line)
        if b and b.group(2).strip():
            lines.append("  " * (len(b.group(1)) - 1) + "- " + b.group(2).strip())
    text = "\n".join(lines).replace("''", "")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def week_digest(end_day, days=6, max_chars=45000):
    """Résumé des `days` jours qui précèdent end_day (exclu). Renvoie (texte, jours couverts)."""
    parts, covered = [], []
    for k in range(days, 0, -1):
        day = end_day - dt.timedelta(days=k)
        text = clean(fetch_raw(day))
        if text:
            parts.append(f"# {day.strftime('%A %d %B %Y')}\n{text}")
            covered.append(day)
    digest = "\n\n".join(parts)
    if len(digest) > max_chars:  # garde le début de chaque journée plutôt que de couper la fin de semaine
        per_day = max_chars // max(1, len(parts))
        digest = "\n\n".join(p[:per_day] for p in parts)
    return digest, covered
