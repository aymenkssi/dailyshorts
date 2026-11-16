"""À lancer UNE fois sur ton PC (navigateur requis), puis copier state/youtube_token.json sur le VPS."""
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

ROOT = Path(__file__).resolve().parent.parent
secrets = sys.argv[1] if len(sys.argv) > 1 else ROOT / "state" / "youtube_client_secret.json"
flow = InstalledAppFlow.from_client_secrets_file(str(secrets), [
    "https://www.googleapis.com/auth/youtube.upload",      # publier
    "https://www.googleapis.com/auth/youtube.readonly",    # lire les statistiques (interface d'administration)
])
creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
out = ROOT / "state" / "youtube_token.json"
out.write_text(creds.to_json())
print(f"Jeton enregistré : {out}")
