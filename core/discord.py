"""Pengiriman pesan ke Discord lewat webhook (satu channel untuk semua)."""
import json
import logging
import shutil
import time
from pathlib import Path

import requests

from core import config as cfg

log = logging.getLogger(__name__)
COLORS = {"info": 0x95A5A6, "warn": 0xF1C40F, "error": 0xE74C3C, "ok": 0x2ECC71, "report": 0x1F3864}
OUTBOX = cfg.WORK_DIR / "discord_outbox"


def _post(payload: dict, files: list[Path] | None = None) -> None:
    files = files or []
    if cfg.DISCORD_DRY_RUN or not cfg.DISCORD_WEBHOOK_URL:
        OUTBOX.mkdir(parents=True, exist_ok=True)
        n = len(list(OUTBOX.glob("*.json"))) + 1
        (OUTBOX / f"{n:03d}.json").write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
        for f in files:
            shutil.copyfile(f, OUTBOX / f"{n:03d}_{f.name}")
        if not cfg.DISCORD_DRY_RUN:
            log.warning("DISCORD_WEBHOOK_URL kosong: pesan hanya disimpan di %s", OUTBOX)
        return

    for attempt in range(6):
        handles = [open(f, "rb") for f in files]
        try:
            if files:
                multipart = {f"files[{i}]": (f.name, h) for i, (f, h) in enumerate(zip(files, handles))}
                r = requests.post(cfg.DISCORD_WEBHOOK_URL, data={"payload_json": json.dumps(payload)},
                                  files=multipart, timeout=60)
            else:
                r = requests.post(cfg.DISCORD_WEBHOOK_URL, json=payload, timeout=30)
        finally:
            for h in handles:
                h.close()
        if r.status_code == 429:  # batas kecepatan Discord
            wait = float(r.json().get("retry_after", 2))
            time.sleep(wait + 0.5)
            continue
        if r.status_code >= 400:
            log.error("Discord menolak pesan (%s): %s", r.status_code, r.text[:300])
        time.sleep(1.2)  # jeda sopan antar pesan
        return
    log.error("Gagal mengirim ke Discord setelah beberapa percobaan.")


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
           footer: str = "") -> None:
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
    _post({"embeds": [first]}, [image] + (attachments or []))
    for i, ch in enumerate(chunks[1:], start=2):
        emb = {"description": ch, "color": COLORS["report"]}
        if i == len(chunks) and footer:
            emb["footer"] = {"text": footer[:2000]}
        _post({"embeds": [emb]})
