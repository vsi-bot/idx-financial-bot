"""Pengiriman pesan ke Discord lewat webhook (satu channel untuk semua)."""
import json
import os
import logging
import shutil
import time
from pathlib import Path

import requests

from core import config as cfg

log = logging.getLogger(__name__)
COLORS = {"info": 0x95A5A6, "warn": 0xF1C40F, "error": 0xE74C3C, "ok": 0x2ECC71, "report": 0x1F3864}
OUTBOX = cfg.WORK_DIR / "discord_outbox"


LAST_ERROR = ""


def _post(payload: dict, files: list[Path] | None = None) -> bool:
    """Kirim satu pesan. Kembalikan True bila Discord menerima pesan."""
    global LAST_ERROR
    files = files or []
    if cfg.DISCORD_DRY_RUN or not cfg.DISCORD_WEBHOOK_URL:
        OUTBOX.mkdir(parents=True, exist_ok=True)
        n = len(list(OUTBOX.glob("*.json"))) + 1
        (OUTBOX / f"{n:03d}.json").write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
        for f in files:
            shutil.copyfile(f, OUTBOX / f"{n:03d}_{f.name}")
        if not cfg.DISCORD_DRY_RUN:
            LAST_ERROR = "DISCORD_WEBHOOK_URL kosong / tidak terbaca dari GitHub Secrets"
            log.error(LAST_ERROR)
            return False
        return True

    LAST_ERROR = ""
    for attempt in range(6):
        handles = [open(f, "rb") for f in files]
        try:
          try:
            if files:
                multipart = {f"files[{i}]": (f.name, h) for i, (f, h) in enumerate(zip(files, handles))}
                r = requests.post(cfg.DISCORD_WEBHOOK_URL, data={"payload_json": json.dumps(payload)},
                                  files=multipart, timeout=60)
            else:
                r = requests.post(cfg.DISCORD_WEBHOOK_URL, json=payload, timeout=30)
          except requests.RequestException as e:      # koneksi gagal: coba lagi dengan jeda
            LAST_ERROR = f"tidak bisa terhubung ke Discord ({type(e).__name__})"
            log.warning(LAST_ERROR)
            time.sleep(0 if os.getenv("FAST_TEST") else 5 * (attempt + 1))
            continue
        finally:
            for h in handles:
                h.close()
        if r.status_code == 429:  # batas kecepatan Discord
            wait = float(r.json().get("retry_after", 2))
            time.sleep(wait + 0.5)
            continue
        if r.status_code >= 400:
            LAST_ERROR = f"Discord menolak pesan (HTTP {r.status_code}): {r.text[:200]}"
            log.error(LAST_ERROR)
            return False
        time.sleep(1.2)  # jeda sopan antar pesan
        return True
    LAST_ERROR = LAST_ERROR or "Discord terus membatasi kecepatan (HTTP 429)"
    log.error(LAST_ERROR)
    return False


def status(text: str, level: str = "info") -> None:
    """Pesan status sistem. Selalu dicatat di log Drive; dikirim ke Discord hanya bila
    discord.status_messages = true di config/settings.yaml (bawaan: false)."""
    from core import activity
    activity.add(text, level)
    if not cfg.DISCORD.get("status_messages", False):
        return
    icon = {"info": "ℹ️", "warn": "⚠️", "error": "⛔", "ok": "✅"}.get(level, "ℹ️")
    _post({"embeds": [{"description": f"{icon} {text}"[:4000], "color": COLORS.get(level)}]})


def report(title: str, paragraphs: list[str], image: Path, attachments: list[Path] | None = None,
           footer: str = "") -> bool:
    """Laporan analisis: gambar tabel + narasi, dipecah rapi bila panjang."""
    chunks, cur = [], ""
    for p in paragraphs:
        if len(cur) + len(p) + 2 > 3800:
            chunks.append(cur)
            cur = ""
        cur += ("\n\n" if cur else "") + p
    if cur:
        chunks.append(cur)

    first = {"title": title[:250], "description": chunks[0] if chunks else "", "color": COLORS["report"],
             "image": {"url": f"attachment://{image.name}"}}
    if len(chunks) == 1 and footer:
        first["footer"] = {"text": footer[:2000]}
    if not _post({"embeds": [first]}, [image] + (attachments or [])):
        return False
    for i, ch in enumerate(chunks[1:], start=2):
        emb = {"description": ch, "color": COLORS["report"]}
        if i == len(chunks) and footer:
            emb["footer"] = {"text": footer[:2000]}
        if not _post({"embeds": [emb]}):
            return False
    return True
