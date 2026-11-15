"""Étape 1 : un LLM gratuit (Cloudflare Workers AI) choisit le sujet, écrit et vérifie le script."""
import json
import os
import re

import requests

STYLE = (
    "hand-painted 2D anime film still in the style of classic Japanese animation studios, "
    "soft warm lighting, detailed painted backgrounds, expressive characters, rich colors, "
    "vertical composition, no text, no letters, no watermark"
)

SCHEMA_HINT = """Return ONLY a JSON object, no markdown, with exactly these keys:
{
  "topic": "short topic label",
  "title": "catchy title, max 90 chars, no hashtags",
  "description": "2-3 sentence description",
  "hashtags": ["5 to 8 hashtags without #"],
  "characters": {"character name": "fixed visual description: age, face, hair, clothing colors"},
  "scenes": [{"narration": "exact words spoken over this shot, 6-14 words",
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


def _chat(endpoint, system, user, temperature=0.8):
    name, url, key, model = endpoint
    r = requests.post(
        url,
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "max_tokens": 8000,
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


def _call(system, user, purpose="write", tries=2):
    err = None
    for ep in _endpoints(purpose):
        for _ in range(tries):
            try:
                return _parse(_chat(ep, system, user))
            except Exception as e:  # noqa: BLE001  -> erreur ou JSON invalide : on réessaie puis on passe au suivant
                err = e
                print(f"  {ep[0]}/{ep[3]} : échec ({str(e)[:200]})")
    raise RuntimeError(f"Script impossible à générer : {err}")


def write_script(past_topics, target_seconds=120):
    words = int(target_seconds * 2.5)  # ~150 mots/min
    system = (
        "You write viral, historically accurate YouTube Shorts / TikTok / Reels scripts about "
        "little-known history: forgotten inventions, overlooked civilizations, the Islamic Golden Age, "
        "ancient science, surprising customs. Tone: vivid storytelling, short punchy sentences, "
        "a contrast or twist. Never invent facts, names, dates or quotes; prefer well-documented events."
    )
    user = f"""Write today's video in English.

Constraints:
- Total narration: about {words} words (~{target_seconds} s spoken).
- First scene = hook that creates curiosity in under 3 seconds (a contrast, a surprising claim).
- Last scene = memorable closing line (no "like and subscribe").
- {words // 8} to {words // 6} scenes; each scene's narration is 6-14 words and is one continuous visual shot.
- Concatenating all scene narrations must give the full script, in order.
- image_prompt: concrete subject, setting, action, camera framing. Describe recurring characters identically every time.
- Topic must be different from these already published topics: {json.dumps(past_topics[-300:])}

{SCHEMA_HINT}"""
    return _call(system, user)


def fact_check(script):
    system = (
        "You are a rigorous history fact-checker. You receive a short-video script as JSON. "
        "Fix anything inaccurate, exaggerated or unverifiable by rewriting it so it is true "
        "(hedge with 'historians believe' etc. if needed). Keep the same structure, number of scenes, "
        "style, length and all other fields. " + SCHEMA_HINT
    )
    try:
        checked = _call(system, json.dumps(script, ensure_ascii=False), purpose="check")
        if abs(len(checked["scenes"]) - len(script["scenes"])) > 2:
            raise RuntimeError("le vérificateur a trop modifié la structure")
        return checked
    except Exception as e:  # noqa: BLE001
        print(f"  vérification impossible, script initial conservé : {e}")
        return script


def full_image_prompt(scene):
    return f"{scene['image_prompt']}. {STYLE}"
