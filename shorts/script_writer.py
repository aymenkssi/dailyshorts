"""Étape 1 : un LLM gratuit (Cloudflare Workers AI) choisit le sujet, écrit et vérifie le script."""
import json
import os
import re

import requests

STYLE = (
    "hand-painted 2D anime film still in the style of classic Japanese animation studios, "
    "soft warm lighting, detailed painted backgrounds, expressive characters, rich colors, "
    "{framing}, no text, no letters, no watermark"
)
FRAMING = {"long": "wide cinematic 16:9 composition", "short": "vertical 9:16 composition"}

SCHEMA_HINT = """Return ONLY a JSON object, no markdown, with exactly these keys:
{
  "topic": "short topic label",
  "title": "catchy title, max 90 chars, no hashtags",
  "description": "2-3 sentence description",
  "hashtags": ["5 to 8 hashtags without #"],
  "characters": {"character name": "fixed visual description: age, face, hair, clothing colors"},
  "scenes": [{"narration": "exact words spoken over this shot",
              "image_prompt": "visual description of the shot, repeating full character descriptions"}]
}"""


def _endpoints(purpose):
    """Ordre d'essai : FreeLLMAPI (si configuré) puis Cloudflare en secours.
    purpose = 'write' (écriture) ou 'check' (vérification des faits)."""
    eps = []
    base = os.environ.get("LLM_BASE_URL", "").rstrip("/")
    if base:
        key = os.environ.get("LLM_API_KEY", "")
        if purpose == "check" and os.environ.get("LLM_FACTCHECK_MODEL", "fusion"):
            eps.append(("freellmapi", f"{base}/chat/completions", key,
                        os.environ.get("LLM_FACTCHECK_MODEL", "fusion")))
        eps.append(("freellmapi", f"{base}/chat/completions", key, os.environ.get("LLM_MODEL", "auto:smart")))
    if os.environ.get("CF_ACCOUNT_ID") and os.environ.get("CF_API_TOKEN"):
        eps.append(("cloudflare",
                    f"https://api.cloudflare.com/client/v4/accounts/{os.environ['CF_ACCOUNT_ID']}/ai/v1/chat/completions",
                    os.environ["CF_API_TOKEN"], os.environ.get("CF_TEXT_MODEL", "@cf/openai/gpt-oss-120b")))
    if not eps:
        raise RuntimeError("Aucun LLM configuré : renseigne LLM_BASE_URL ou CF_ACCOUNT_ID/CF_API_TOKEN dans .env")
    return eps


def _chat(endpoint, system, user, temperature=0.8, max_tokens=8000):
    name, url, key, model = endpoint
    r = requests.post(
        url,
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "max_tokens": max_tokens,
            "temperature": temperature,
        },
        timeout=600,  # 'fusion' interroge plusieurs modèles : peut prendre quelques minutes
    )
    r.raise_for_status()
    via = r.headers.get("X-Routed-Via") or model
    print(f"  texte généré via {name} ({via})")
    return r.json()["choices"][0]["message"]["content"]


def _parse(text):
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    start, end = text.find("{"), text.rfind("}")
    data = json.loads(text[start:end + 1])
    assert data["scenes"] and all(s.get("narration") and s.get("image_prompt") for s in data["scenes"])
    for k in ("topic", "title", "description"):
        assert isinstance(data.get(k), str) and data[k]
    data.setdefault("hashtags", [])
    return data


def _word_count(script):
    return sum(len(sc["narration"].split()) for sc in script["scenes"])


def _call(system, user, purpose="write", tries=2, max_tokens=8000, min_words=0):
    """min_words : longueur minimale de la narration ; un script trop court est refusé et redemandé."""
    err = None
    for ep in _endpoints(purpose):
        for _ in range(tries):
            try:
                data = _parse(_chat(ep, system, user, max_tokens=max_tokens))
                if _word_count(data) < min_words:
                    raise ValueError(f"script trop court : {_word_count(data)} mots au lieu de {min_words} minimum")
                return data
            except Exception as e:  # noqa: BLE001  -> erreur ou JSON invalide : on réessaie puis on passe au suivant
                err = e
                print(f"  {ep[0]}/{ep[3]} : échec ({str(e)[:200]})")
    raise RuntimeError(f"Script impossible à générer : {err}")


WRITER = (
    "You write historically accurate, gripping YouTube scripts about little-known history: forgotten "
    "inventions, overlooked civilizations, the Islamic Golden Age, ancient science, surprising customs. "
    "Tone: vivid storytelling, short clear sentences, concrete details, a contrast or twist. "
    "Never invent facts, names, dates or quotes; prefer well-documented events."
)


def write_long(past_topics, seconds=240, topic=None, niche=""):
    """Vidéo longue horizontale : récit complet (~150 mots/min).
    topic : sujet imposé (file de l'admin) ; niche : ligne éditoriale de la chaîne."""
    words = int(seconds * 2.5)
    if topic:
        subject = f"- Topic (imposed by the channel owner, do not change it): {topic}"
    else:
        subject = ("- Topic must be different from these already published topics: "
                   + json.dumps(past_topics[-300:]))
    if niche:
        subject += f"\n- Channel editorial line to respect: {niche}"
    user = f"""Write today's long-form video (horizontal, about {seconds // 60} minutes) in English.

Constraints:
- Total narration: about {words} words (~{seconds} s spoken). This length is mandatory: a script under
  {int(words * 0.75)} words will be rejected. Count your words before answering.
- Structure: a hook in the first 10 seconds, the setting and the people, the story told step by step
  with concrete details, a turning point, what it changed, and a memorable closing line
  (no "like and subscribe").
- {words // 12} to {words // 9} scenes; each scene's narration is 8-16 words and is one continuous visual shot.
- Concatenating all scene narrations must give the full script, in order.
- image_prompt: concrete subject, setting, action, wide framing. Describe recurring characters identically every time.
{subject}

{SCHEMA_HINT}"""
    return _call(WRITER, user, max_tokens=14000, min_words=int(words * 0.75))


def write_teaser(long_script, seconds=40):
    """Short vertical d'accroche tiré de la vidéo longue : intrigue sans dévoiler la fin."""
    words = int(seconds * 2.5)
    story = " ".join(sc["narration"] for sc in long_script["scenes"])
    user = f"""Here is the narration of today's long-form video, titled "{long_script['title']}":

{story}

Write a vertical teaser Short (about {seconds} s, ~{words} words) for the SAME story, in English.

Constraints:
- First scene = hook that creates curiosity in under 3 seconds (a contrast, a surprising claim).
- Tell only the beginning: build the mystery, stop on a cliffhanger, do NOT reveal the ending.
- Last scene's narration must be exactly: "The full story is on our channel."
- {max(6, words // 11)} to {max(8, words // 8)} scenes; each scene's narration is 6-12 words and is one continuous visual shot.
- Use only facts present in the narration above. Keep the same "topic" and the same character descriptions:
  {json.dumps(long_script.get("characters", {}), ensure_ascii=False)}
- "title": a different, punchy title (max 80 chars, no hashtags).
- image_prompt: concrete subject, setting, action, vertical framing.

{SCHEMA_HINT}"""
    teaser = _call(WRITER, user, min_words=int(words * 0.6))
    teaser["topic"] = long_script["topic"]
    return teaser


def fact_check(script):
    system = (
        "You are a rigorous history fact-checker. You receive a video script as JSON. "
        "Fix anything inaccurate, exaggerated or unverifiable by rewriting it so it is true "
        "(hedge with 'historians believe' etc. if needed). Keep the same structure, number of scenes, "
        "style, length and all other fields. " + SCHEMA_HINT
    )
    try:
        checked = _call(system, json.dumps(script, ensure_ascii=False), purpose="check", max_tokens=14000,
                        min_words=int(_word_count(script) * 0.85))
        n = len(script["scenes"])
        if abs(len(checked["scenes"]) - n) > max(2, n // 10):
            raise RuntimeError("le vérificateur a trop modifié la structure")
        return checked
    except Exception as e:  # noqa: BLE001
        print(f"  vérification impossible, script initial conservé : {e}")
        return script


def full_image_prompt(scene, kind="short"):
    return f"{scene['image_prompt']}. {STYLE.format(framing=FRAMING[kind])}"
