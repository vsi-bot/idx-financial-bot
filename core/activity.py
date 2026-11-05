"""Catatan aktivitas bot (pengganti pesan status Discord).

Disimpan di Google Drive: _Sistem/log/YYYY-MM-DD/HHMMSS_<tugas>.txt (hanya bila ada kejadian).
"""
import logging
from datetime import datetime, timedelta, timezone

from core import config as cfg

log = logging.getLogger(__name__)
WIB = timezone(timedelta(hours=7))
EVENTS: list[str] = []
ICON = {"info": "INFO", "ok": "OK", "warn": "PERHATIAN", "error": "GANGGUAN"}


def add(text: str, level: str = "info") -> None:
    stamp = datetime.now(WIB).strftime("%H:%M:%S")
    EVENTS.append(f"[{stamp}] {ICON.get(level, 'INFO'):9} {text}")
    log.info("[%s] %s", level, text)


def flush(job: str) -> None:
    if not EVENTS:
        return
    from core.storage import get_storage
    now = datetime.now(WIB)
    st = get_storage()
    folder = st.folder(f"{cfg.FOLDERS['system']}/log/{now:%Y-%m-%d}")
    path = cfg.WORK_DIR / f"log_{job}.txt"
    path.write_text("\n".join(EVENTS) + "\n", encoding="utf-8")
    try:
        st.upload(path, folder, f"{now:%H%M%S}_{job}.txt", "text/plain")
    except Exception as e:  # catatan gagal disimpan tidak boleh menggagalkan proses utama
        log.warning("Log aktivitas gagal disimpan: %s", e)
    EVENTS.clear()
