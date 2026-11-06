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
EDITABLE = ["watchlist.csv", "style_guide.yaml", "catatan_analis.txt", "review_ok.txt", "struktur.txt", "makro.txt", "koreksi.txt"]
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
    out = {}
    for r in rows:
        if not r.get("ticker"):
            continue
        sektor = (r.get("sektor") or "").strip()
        mus = (r.get("musiman") or "").strip().upper()
        out[r["ticker"].strip().upper()] = {"nama": r["nama"].strip(), "sektor": sektor,
                                            # kolom kosong/tidak ada: bawaan musiman untuk consumer non-cyclicals
                                            "musiman": mus in ("Y", "YA", "YES", "1") if "musiman" in r and r["musiman"] is not None
                                            else sektor == "Consumer Non-Cyclicals"}
    return out


def review_ok() -> set[str]:
    return {l.strip().upper() for l in load_text("review_ok.txt").splitlines() if l.strip() and not l.startswith("#")}


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


def _pipe_rows(name, n=3):
    out = []
    for line in load_text(name).splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            parts = [p.strip() for p in line.split("|", n - 1)]
            if len(parts) == n:
                out.append(parts)
    return out


def structure_notes() -> dict[tuple[str, str], str]:
    return {(a.upper(), b.upper()): c for a, b, c in _pipe_rows("struktur.txt")}


def macro_notes() -> list[tuple[str, str, str]]:
    return [(a.upper(), b.upper(), c) for a, b, c in _pipe_rows("makro.txt")]


def corrections() -> dict[str, set[str]]:
    out = {}
    for tk, item, kind in _pipe_rows("koreksi.txt"):
        if kind.lower().startswith("rutin"):
            out.setdefault(tk.upper(), set()).add(item)
    return out
