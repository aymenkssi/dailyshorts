"""Crée admin.env (mot de passe de l'interface d'administration + secret de session).

À lancer sur le VPS, dans /opt/dailyshorts :  python3 tools/admin_password.py
Le mot de passe n'est jamais stocké : seule son empreinte scrypt l'est.
"""
import getpass
import hashlib
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
pw = getpass.getpass("Mot de passe de l'administration (12 caractères minimum) : ")
if len(pw) < 12:
    sys.exit("Trop court : 12 caractères minimum.")
if pw != getpass.getpass("Confirme le mot de passe : "):
    sys.exit("Les deux saisies sont différentes.")

salt = secrets.token_bytes(16)
digest = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
env = ROOT / "admin.env"
keep = {}
if env.exists():  # on garde le secret de session existant pour ne pas déconnecter tout le monde inutilement
    for line in env.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            keep[k] = v
keep["ADMIN_PASSWORD_HASH"] = f"scrypt${salt.hex()}${digest.hex()}"
keep.setdefault("SESSION_SECRET", secrets.token_hex(32))
env.write_text("".join(f"{k}={v}\n" for k, v in keep.items()))
env.chmod(0o600)
print(f"Écrit : {env}  — relance l'admin : docker compose up -d admin")
