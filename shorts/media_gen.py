"""Étapes 2-3 : images (Cloudflare Workers AI, gratuit) et voix off (Kokoro, local et gratuit)."""
import base64
import json
import os
import time
from pathlib import Path

import requests


def _retry(fn, tries=3, wait=10):
    for i in range(tries):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            if i == tries - 1:
                raise
            print(f"  retry après erreur : {e}")
            time.sleep(wait * (i + 1))


# ---------- Images ----------
def _cf_image(prompt, seed):
    acc, tok = os.environ["CF_ACCOUNT_ID"], os.environ["CF_API_TOKEN"]
    model = os.environ.get("CF_IMAGE_MODEL", "@cf/black-forest-labs/flux-2-klein-4b")
    url = f"https://api.cloudflare.com/client/v4/accounts/{acc}/ai/run/{model}"
    headers = {"Authorization": f"Bearer {tok}"}
    if "flux-2" in model:  # FLUX.2 : formulaire multipart, format vertical natif
        fields = {"prompt": prompt[:2000], "width": "768", "height": "1344", "seed": str(seed)}
        r = requests.post(url, headers=headers, files={k: (None, v) for k, v in fields.items()}, timeout=180)
    else:  # flux-1-schnell : JSON, image carrée recadrée ensuite
        r = requests.post(url, headers=headers, json={"prompt": prompt[:2000], "steps": 8, "seed": seed}, timeout=180)
    r.raise_for_status()
    if r.headers.get("content-type", "").startswith("image/"):
        return r.content
    data = r.json()
    img = (data.get("result") or data).get("image")
    if not img:
        raise RuntimeError(f"Pas d'image dans la réponse : {str(data)[:300]}")
    return base64.b64decode(img.split(",", 1)[-1])


def _freellmapi_image(prompt, seed=None):
    """Secours : FreeLLMAPI route vers NVIDIA, SiliconFlow, Cloudflare (autre compte)…"""
    base = os.environ["LLM_BASE_URL"].rstrip("/")
    r = requests.post(
        f"{base}/images/generations",
        headers={"Authorization": f"Bearer {os.environ.get('LLM_API_KEY', '')}"},
        json={"model": os.environ.get("IMAGE_FALLBACK_MODEL", "auto"), "prompt": prompt[:2000],
              "n": 1, "size": "768x1344", "response_format": "b64_json"},
        timeout=300,
    )
    r.raise_for_status()
    item = r.json()["data"][0]
    if item.get("b64_json"):
        return base64.b64decode(item["b64_json"])
    img = requests.get(item["url"], timeout=120)
    img.raise_for_status()
    return img.content


def _image_sources():
    src = []
    if os.environ.get("CF_ACCOUNT_ID") and os.environ.get("CF_API_TOKEN"):
        src.append(("cloudflare", _cf_image))
    if os.environ.get("LLM_BASE_URL"):
        src.append(("freellmapi", _freellmapi_image))
    if not src:
        raise RuntimeError("Aucune source d'images : renseigne CF_* ou LLM_BASE_URL dans .env")
    return src


def generate_images(prompts, out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    sources = _image_sources()
    paths = []
    for i, prompt in enumerate(prompts):
        dest = out_dir / f"scene_{i:02d}.jpg"
        if not dest.exists():
            err = None
            for name, fn in list(sources):
                try:
                    dest.write_bytes(_retry(lambda: fn(prompt, 1000 + i), tries=2, wait=5))
                    print(f"  image {i + 1}/{len(prompts)} ok ({name})")
                    break
                except Exception as e:  # noqa: BLE001
                    err = e
                    print(f"  {name} indisponible pour l'image {i + 1} : {str(e)[:200]}")
                    # quota Cloudflare épuisé : inutile de le retenter pour les images suivantes
                    if name == "cloudflare" and "429" in str(e) and len(sources) > 1:
                        sources.remove((name, fn))
            else:
                raise RuntimeError(f"Image {i + 1} impossible à générer : {err}")
        paths.append(dest)
    return paths


# ---------- Voix off (Kokoro, open source, tourne sur le CPU du VPS) ----------
_PIPELINE = None


def synthesize_voice(text, out_dir):
    """Renvoie (chemin_wav, liste de mots [{word,start,end}])."""
    out_dir = Path(out_dir)
    wav = out_dir / "voice.wav"
    words_json = out_dir / "words.json"
    if wav.exists() and words_json.exists():
        return wav, json.loads(words_json.read_text())

    global _PIPELINE
    import numpy as np
    import soundfile as sf
    from kokoro import KPipeline

    voice = os.environ.get("KOKORO_VOICE", "am_michael")
    if _PIPELINE is None:
        _PIPELINE = KPipeline(lang_code=voice[0])  # 'a' = anglais US, 'b' = anglais UK
    chunks, tokens, offset = [], [], 0.0
    for res in _PIPELINE(text, voice=voice, speed=float(os.environ.get("KOKORO_SPEED", 1.05))):
        audio = res.audio.cpu().numpy() if hasattr(res.audio, "cpu") else np.asarray(res.audio)
        for t in res.tokens or []:
            tokens.append({
                "text": t.text, "ws": t.whitespace,
                "start": None if t.start_ts is None else t.start_ts + offset,
                "end": None if t.end_ts is None else t.end_ts + offset,
            })
        chunks.append(audio)
        offset += len(audio) / 24000
    sf.write(wav, np.concatenate(chunks), 24000)
    words = words_from_tokens(tokens)
    words_json.write_text(json.dumps(words, indent=1))
    return wav, words


def words_from_tokens(tokens):
    """Regroupe les tokens Kokoro (mots + ponctuation) en mots séparés par des espaces,
    pour correspondre exactement à text.split()."""
    words, cur = [], None
    for t in tokens:
        if cur is None:
            cur = {"word": "", "start": None, "end": None}
        cur["word"] += t["text"]
        if t["start"] is not None and cur["start"] is None:
            cur["start"] = t["start"]
        if t["end"] is not None:
            cur["end"] = t["end"]
        if t["ws"]:
            words.append(cur)
            cur = None
    if cur and cur["word"]:
        words.append(cur)
    # combler les trous (ponctuation seule, mots sans minutage)
    for i, w in enumerate(words):
        if w["start"] is None:
            w["start"] = words[i - 1]["end"] if i else 0.0
        if w["end"] is None:
            w["end"] = w["start"] + 0.2
    return words
