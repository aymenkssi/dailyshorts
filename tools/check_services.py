"""Vérifie que chaque service répond avant de lancer le cron : python tools/check_services.py"""
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT))
ok = True


def check(label, fn):
    global ok
    try:
        print(f"[OK]  {label} : {fn()}")
    except Exception as e:  # noqa: BLE001
        ok = False
        print(f"[KO]  {label} : {str(e)[:300]}")


base = os.environ.get("LLM_BASE_URL", "").rstrip("/")
if base:
    h = {"Authorization": f"Bearer {os.environ.get('LLM_API_KEY', '')}"}

    def models():
        r = requests.get(f"{base}/models", headers=h, timeout=30)
        r.raise_for_status()
        return f"{len(r.json().get('data', []))} modèles visibles"

    def chat(model):
        def f():
            r = requests.post(f"{base}/chat/completions", headers=h, timeout=300, json={
                "model": model, "max_tokens": 20,
                "messages": [{"role": "user", "content": "Reply with the single word: ready"}]})
            r.raise_for_status()
            return f"{r.headers.get('X-Routed-Via', model)} → {r.json()['choices'][0]['message']['content'][:40]!r}"
        return f

    check("FreeLLMAPI joignable", models)
    check(f"FreeLLMAPI texte ({os.environ.get('LLM_MODEL', 'auto:smart')})", chat(os.environ.get("LLM_MODEL", "auto:smart")))
    check(f"FreeLLMAPI vérification ({os.environ.get('LLM_FACTCHECK_MODEL', 'fusion')})",
          chat(os.environ.get("LLM_FACTCHECK_MODEL", "fusion")))
else:
    print("[--]  FreeLLMAPI non configuré (LLM_BASE_URL vide) : Cloudflare seul")

if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
    from shorts import script_writer

    def claude():
        model = os.environ.get("CLAUDE_MODEL", "sonnet")
        out = script_writer._claude("Reply with one word.", "Reply with the single word: ready", model)
        return f"{model} → {out.strip()[:40]!r}"
    check("Claude (abonnement)", claude)
else:
    print("[--]  Claude non configuré (CLAUDE_CODE_OAUTH_TOKEN vide) : FreeLLMAPI écrit les scripts")

if os.environ.get("CF_ACCOUNT_ID"):
    from shorts import media_gen
    check("Cloudflare image", lambda: f"{len(media_gen._cf_image('a lighthouse at dusk, anime style', 1)) // 1024} Ko")
if base:
    from shorts import media_gen
    check("FreeLLMAPI image (secours)", lambda: f"{len(media_gen._freellmapi_image('a lighthouse at dusk, anime style')) // 1024} Ko")


def kokoro():
    import kokoro  # noqa: F401
    return "module installé"


check("Kokoro (voix)", kokoro)
check("ffmpeg", lambda: os.popen("ffmpeg -version").readline().strip() or 1 / 0)
sys.exit(0 if ok else 1)
