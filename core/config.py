"""Membaca pengaturan dari config/settings.yaml dan variabel lingkungan (GitHub Secrets)."""
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
WORK_DIR = Path(os.getenv("WORK_DIR", ROOT / ".work"))
WORK_DIR.mkdir(parents=True, exist_ok=True)

with open(CONFIG_DIR / "settings.yaml", encoding="utf-8") as f:
    SETTINGS = yaml.safe_load(f)

# --- Rahasia & lingkungan --------------------------------------------------
BRIDGE_URL = os.getenv("BRIDGE_URL", "").strip()          # URL Web App Apps Script (opsi tanpa Cloud Console)
BRIDGE_KEY = os.getenv("BRIDGE_KEY", "").strip()
# "bridge" otomatis dipakai bila BRIDGE_URL diisi; "drive" = Google Drive API; "local" = uji coba
STORAGE = os.getenv("STORAGE", "").strip() or ("bridge" if BRIDGE_URL else "drive")                 # "drive" atau "local" (untuk uji coba)
LOCAL_ROOT = Path(os.getenv("LOCAL_ROOT", ROOT / "local_drive"))
DRIVE_ROOT_FOLDER_ID = os.getenv("DRIVE_ROOT_FOLDER_ID", "").strip()
GOOGLE_TOKEN = os.getenv("GOOGLE_TOKEN", "").strip()    # isi token.json (JSON)
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
DISCORD_DRY_RUN = os.getenv("DISCORD_DRY_RUN", "false").lower() == "true"
FORCE_SCRAPE = os.getenv("FORCE_SCRAPE", "false").lower() == "true"

FOLDERS = SETTINGS["folders"]
DATA = SETTINGS["data"]
SCRAPER = SETTINGS["scraper"]
DISCORD = SETTINGS["discord"]
ANALYSIS = SETTINGS["analysis"]
