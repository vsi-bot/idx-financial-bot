"""File konfigurasi yang bisa diedit dari Google Drive (folder _Sistem).

Saat pertama jalan, salinan dari folder config/ di repo diunggah ke _Sistem.
Setelah itu, versi di Drive yang dipakai, sehingga Anda bisa mengedit watchlist,
pedoman gaya bahasa, dan catatan analis langsung dari Drive.
"""
import csv
import io
import logging

import yaml

from core import config as cfg
from core.storage import get_storage

log = logging.getLogger(__name__)
EDITABLE = ["watchlist.csv", "style_guide.yaml", "catatan_analis.txt"]
_cache: dict[str, str] = {}


def load_text(name: str) -> str:
    if name in _cache:
        return _cache[name]
    st = get_storage()
    text = st.read_text(cfg.FOLDERS["system"], name)
    if text is None:
        local = cfg.CONFIG_DIR / name
        text = local.read_text(encoding="utf-8")
        st.upload(local, st.folder(cfg.FOLDERS["system"]), name)
        log.info("Salinan %s diunggah ke folder %s.", name, cfg.FOLDERS["system"])
    _cache[name] = text
    return text


def watchlist() -> dict[str, dict]:
    rows = csv.DictReader(io.StringIO(load_text("watchlist.csv")))
    return {r["ticker"].strip().upper(): {"nama": r["nama"].strip(), "sektor": r.get("sektor", "").strip()}
            for r in rows if r.get("ticker")}


def style_replacements() -> dict[str, str]:
    data = yaml.safe_load(load_text("style_guide.yaml")) or {}
    return data.get("replace", {}) or {}


def analyst_notes() -> dict[tuple[str, str], str]:
    notes = {}
    for line in load_text("catatan_analis.txt").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|", 2)]
        if len(parts) == 3:
            notes[(parts[0].upper(), parts[1].upper())] = parts[2]
    return notes
