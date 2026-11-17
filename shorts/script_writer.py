"""Étape 1 : le sujet, le script et sa vérification.

Ordre d'essai : Claude (ton abonnement Pro, via Claude Code) si configuré, puis FreeLLMAPI, puis Cloudflare.
"""
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import requests

STYLE = (
    "anime film still, hand-painted 2D cel animation in the style of classic Japanese animation studios, "
    "clean confident line art, painterly detailed background, cinematic lighting with soft volumetric light, "
    "rich harmonious colors, expressive faces, {framing}, sharp focus, high detail, "
    "no text, no letters, no watermark, no logo"
)
FRAMING = {"long": "wide cinematic 16:9 composition", "short": "vertical 9:16 composition"}

SCHEMA_HINT = """Return ONLY a JSON object, no markdown, with exactly these keys:
{
  "topic": "short topic label",
  "title": "catchy title, max 90 chars, no hashtags",
  "description": "2-3 sentence description",
  "hashtags": ["5 to 8 hashtags without #"],
  "setting": "one line of art direction shared by every shot: era, place, architecture, clothing, color palette",
  "characters": {"Name": "fixed visual description: gender, age, face, hair, clothing and its colors"},
  "scenes": [{"narration": "exact words spoken over this shot",
              "image_prompt": "one shot: camera framing, subject and action, setting, time of day, lighting, mood"}]
}"""

IMAGE_RULES = (
    "image_prompt rules: one concrete, filmable shot per scene, 25-60 words. Start with the framing and vary "
    "it (establishing wide shot, medium shot, close-up on hands or an object, over-the-shoulder, low angle). "
    "Name recurring people exactly as in \"characters\" (at most 3 characters, the ones seen most). "
    "Describe light and mood. No text, signs, maps with labels or written pages in the image."
)


_CLAUDE_OFF = False  # passe à True pour le reste du passage si la limite de l'abonnement est atteinte


def claude_enabled():
    tok = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")
    return (not _CLAUDE_OFF and tok and tok != "..."
            and os.environ.get("USE_CLAUDE", "true").lower() in ("1", "true", "yes"))


def _claude(system, user, model):
    """Claude Code en mode non interactif (claude -p), connecté à ton abonnement par CLAUDE_CODE_OAUTH_TOKEN.
    Aucun outil, aucun fichier de configuration du projet : un simple échange texte, comme une API."""
    global _CLAUDE_OFF
    work = Path(tempfile.mkdtemp(prefix="claude-"))  # dossier vide : rien à découvrir ni à charger
    (work / "system.txt").write_text(system)
    env = {**os.environ, "DISABLE_AUTOUPDATER": "1", "DISABLE_TELEMETRY": "1",
           "CLAUDE_CONFIG_DIR": os.environ.get("CLAUDE_CONFIG_DIR", "/app/cache/claude")}
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):  # sinon prioritaires sur le jeton d'abonnement
        env.pop(k, None)
    cmd = [os.environ.get("CLAUDE_BIN", "claude"), "-p", "--model", model, "--output-format", "json",
           "--system-prompt-file", str(work / "system.txt"), "--tools", "", "--max-turns", "2",
           "--no-session-persistence", "--disable-slash-commands", "--strict-mcp-config",
           "--setting-sources", "user"]
    try:
        r = subprocess.run(cmd, input=user, capture_output=True, text=True, cwd=work, env=env,
                           timeout=int(os.environ.get("CLAUDE_TIMEOUT", 900)))
    finally:
        for f in work.iterdir():
            f.unlink()
        work.rmdir()
    try:
        out = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise RuntimeError(f"réponse illisible (code {r.returncode}) : {(r.stderr or r.stdout)[-300:]}")
    text = str(out.get("result", ""))
    if out.get("is_error") or r.returncode:
        if re.search(r"limit|quota|usage|rate", text, re.I):
            _CLAUDE_OFF = True  # inutile de réessayer avant la remise à zéro de l'abonnement
        raise RuntimeError(text[:300] or f"code {r.returncode}")
    return text


def _endpoints(purpose):
    """Ordre d'essai : Claude (abonnement) puis FreeLLMAPI puis Cloudflare.
    purpose = 'write' (écriture) ou 'check' (vérification des faits)."""
    eps = []
    if claude_enabled():
        eps.append(("claude", None, None, os.environ.get("CLAUDE_MODEL", "sonnet")))
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
    if name == "claude":
        text = _claude(system, user, model)
        print(f"  texte généré via Claude ({model}, abonnement)")
        return text
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
                if ep[0] == "claude" and _CLAUDE_OFF:
                    print("  limite de l'abonnement Claude atteinte : FreeLLMAPI prend le relais")
                    break
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
- {words // 20} to {words // 15} scenes; each scene's narration is 15-22 words and is one continuous visual shot.
- Concatenating all scene narrations must give the full script, in order.
- {IMAGE_RULES} Wide 16:9 framing.
{subject}

{SCHEMA_HINT}"""
    return _call(WRITER, user, max_tokens=14000, min_words=int(words * 0.75))


def write_teaser(long_script, seconds=40, recap=False):
    """Short vertical d'accroche tiré de la vidéo longue : intrigue sans dévoiler la fin.
    recap=True : Short du récapitulatif de la semaine (les titres forts, renvoi vers la vidéo complète)."""
    words = int(seconds * 2.5)
    story = " ".join(sc["narration"] for sc in long_script["scenes"])
    if recap:
        user = f"""Here is the narration of this week's world news recap, titled "{long_script['title']}":

{story}

Write a vertical teaser Short (about {seconds} s, ~{words} words) for this recap, in English.

Constraints:
- First scene = hook in under 3 seconds ("Here is what changed the world this week").
- Present the 3 most important stories of the recap, one or two scenes each, factual and neutral.
- Last scene's narration must be exactly: "The full weekly recap is on our channel."
- {max(6, words // 11)} to {max(8, words // 8)} scenes; each scene's narration is 6-12 words.
- Use only facts present in the narration above, with the same wording for numbers and names.
- "title": a punchy, neutral title (max 80 chars, no hashtags, no clickbait about tragedies).
- image_prompt: symbolic vertical scene (place, landscape, object, anonymous crowd). Never a real,
  named or recognisable person, politician or celebrity; no flags burning, no blood, no weapons aimed at people.

{SCHEMA_HINT}"""
        teaser = _call(WRITER, user, min_words=int(words * 0.6))
        teaser["topic"] = long_script["topic"]
        return teaser
    user = f"""Here is the narration of today's long-form video, titled "{long_script['title']}":

{story}

Write a vertical teaser Short (about {seconds} s, ~{words} words) for the SAME story, in English.

Constraints:
- First scene = hook that creates curiosity in under 3 seconds (a contrast, a surprising claim).
- Tell only the beginning: build the mystery, stop on a cliffhanger, do NOT reveal the ending.
- Last scene's narration must be exactly: "The full story is on our channel."
- {max(6, words // 11)} to {max(8, words // 8)} scenes; each scene's narration is 6-12 words and is one continuous visual shot.
- Use only facts present in the narration above. Keep the same "topic", "setting" and characters (same names,
  same descriptions): setting = {json.dumps(long_script.get("setting", ""), ensure_ascii=False)};
  characters = {json.dumps(long_script.get("characters", {}), ensure_ascii=False)}
- "title": a different, punchy title (max 80 chars, no hashtags).
- {IMAGE_RULES} Vertical 9:16 framing, the subject large in the frame.

{SCHEMA_HINT}"""
    teaser = _call(WRITER, user, min_words=int(words * 0.6))
    teaser["topic"] = long_script["topic"]
    return teaser


NEWS_WRITER = (
    "You write a weekly world news recap for a YouTube channel, narrated over anime-style illustrations. "
    "You are a careful, neutral news editor: you report only what the provided source says, attribute "
    "claims and casualty figures to their source, never speculate, never take sides, and give "
    "proportionate weight to every region of the world."
)


def write_recap(digest, start, end, seconds=360):
    """Récapitulatif de l'actualité mondiale de la semaine, à partir du résumé Wikipédia."""
    words = int(seconds * 2.5)
    user = f"""Source: Wikipedia "Current events" entries from {start:%d %B %Y} to {end:%d %B %Y}.
Each bullet is a sourced news item; outlet names are in parentheses.

<source>
{digest}
</source>

Write this week's world news recap video (horizontal, about {seconds // 60} minutes) in English.

Constraints:
- Total narration: about {words} words (~{seconds} s spoken). A script under {int(words * 0.75)} words will be rejected.
- Pick the 6 to 8 most significant stories for a worldwide audience, across several regions and themes
  (conflicts, politics, economy, science, climate, culture). One clear segment per story.
- Opening: one scene that says this is the world news recap for the week of {start:%d %B} to {end:%d %B %Y}.
- Use ONLY facts stated in the source: no added numbers, names, causes or predictions. If the source
  attributes a claim ("according to", "the ministry says"), keep the attribution.
- Neutral wording: no adjectives that judge, no opinions, no jokes about victims.
- Closing scene: "That was the week. See you next Sunday for the next recap."
- {words // 20} to {words // 15} scenes; each scene's narration is 15-22 words and is one continuous visual shot.
- Concatenating all scene narrations must give the full script, in order.
- "characters": {{}} (empty: no real person is ever drawn). "setting": "contemporary world, symbolic news illustration".
- image_prompt: 25-60 words, starting with the framing (wide, medium, close-up on an object): a symbolic scene for the story (a place, a landscape, a building, an object, a map, an
  anonymous crowd). NEVER a real, named or recognisable person (no politicians, leaders, celebrities,
  victims); no blood, no gore, no weapons aimed at people, no national flags being destroyed.
- "topic": "World news recap {start:%d %b} - {end:%d %b %Y}".
- "title": neutral and informative, max 90 chars, e.g. "World News Recap: <two biggest stories> ({start:%b %d}-{end:%d})".
- "hashtags": include worldnews, news, weeklyrecap.

{SCHEMA_HINT}"""
    script = _call(NEWS_WRITER, user, max_tokens=16000, min_words=int(words * 0.75))
    script["kind"] = "recap"
    return script


def fact_check_recap(script, digest):
    """Vérifie chaque phrase du récapitulatif contre la source ; retire ce qui n'y figure pas."""
    system = (
        "You are a rigorous news fact-checker. You receive a source (Wikipedia Current events entries) and a "
        "news recap script as JSON. Every statement in the narration must be supported by the source: fix "
        "numbers, names, places and attributions that differ; rewrite or remove anything not in the source; "
        "remove any opinion or loaded wording. Also make sure no image_prompt depicts a real, named or "
        "recognisable person. Keep the same structure, number of scenes, length and all other fields. "
        + SCHEMA_HINT
    )
    user = f"<source>\n{digest}\n</source>\n\n<script>\n{json.dumps(script, ensure_ascii=False)}\n</script>"
    try:
        checked = _call(system, user, purpose="check", max_tokens=16000,
                        min_words=int(_word_count(script) * 0.85))
        n = len(script["scenes"])
        if abs(len(checked["scenes"]) - n) > max(2, n // 10):
            raise RuntimeError("le vérificateur a trop modifié la structure")
        checked["kind"] = "recap"
        return checked
    except Exception as e:  # noqa: BLE001
        print(f"  vérification impossible, script initial conservé : {e}")
        return script


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


def characters_in(scene, script):
    """Personnages du script nommés dans le plan (nom complet ou premier mot du nom)."""
    text = scene.get("image_prompt", "").lower()
    found = []
    for name in (script or {}).get("characters") or {}:
        keys = {name.lower(), name.split()[0].lower()} if name.strip() else set()
        if any(re.search(rf"\b{re.escape(k)}\b", text) for k in keys if len(k) > 2):
            found.append(name)
    return found


def full_image_prompt(scene, kind="short", recap=False, script=None, refs=None):
    """Prompt complet d'un plan : le plan, le décor commun, la description fixe de chaque personnage présent
    (répétée à l'identique dans chaque image), puis le style de la chaîne.
    refs : {nom: n° de l'image de référence envoyée avec la requête} pour garder les mêmes visages."""
    parts = [scene["image_prompt"].strip().rstrip(".")]
    script = script or {}
    if script.get("setting"):
        parts.append(f"Setting: {script['setting'].strip().rstrip('.')}")
    for name in characters_in(scene, script):
        desc = str(script["characters"][name]).strip().rstrip(".")
        if refs and name in refs:
            parts.append(f"{name} looks exactly like the character in image {refs[name]} (same face, hair "
                         f"and clothes; use that image only for the character's appearance): {desc}")
        else:
            parts.append(f"{name}: {desc}")
    if recap:
        parts.append("Symbolic news illustration, anonymous figures only, no recognisable real people, "
                     "no logos, no blood")
    return ". ".join(parts) + ". " + STYLE.format(framing=FRAMING[kind])


def character_sheet_prompt(name, desc, script):
    """Portrait de référence d'un personnage, réutilisé dans toutes les images où il apparaît."""
    setting = (script.get("setting") or "").strip().rstrip(".")
    return (f"Character reference portrait of {name}: {desc}. Head and shoulders, facing the viewer, neutral "
            f"expression, plain soft background{', period: ' + setting if setting else ''}. "
            + STYLE.format(framing="centered square composition"))
