"""Potensi katalis positif dari RSS Google News (gratis, tanpa API). Setiap katalis wajib punya link sumber.

Hanya judul berita, sumber, tanggal, dan link yang dipakai (tanpa menyalin isi artikel).
Penyaringan memakai kata kunci positif/negatif di config/settings.yaml bagian `catalysts`.
"""
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote

from core import config as cfg

log = logging.getLogger(__name__)
C = cfg.SETTINGS.get("catalysts", {})


def parse_rss(xml_text: str, days: int, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    out = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return out
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        src = (it.findtext("source") or "").strip()
        try:
            dt = parsedate_to_datetime(it.findtext("pubDate") or "")
        except (TypeError, ValueError):
            continue
        if not title or not link.startswith("http") or now - dt > timedelta(days=days):
            continue
        if src and title.endswith(" - " + src):
            title = title[: -len(" - " + src)]
        out.append({"title": title, "link": link, "source": src or "berita", "date": dt})
    return out


def select(items: list[dict], ticker: str, company: str) -> list[dict]:
    pos = [k.lower() for k in C.get("positive_keywords", [])]
    neg = [k.lower() for k in C.get("negative_keywords", [])]
    core = re.sub(r"\b(pt|tbk|persero)\b", "", company.lower()).split()
    keys = {ticker.lower()} | ({" ".join(core[:2])} if core else set())
    chosen, seen = [], set()
    for it in sorted(items, key=lambda x: x["date"], reverse=True):
        t = it["title"].lower()
        if not any(k and k in t for k in keys):          # harus benar-benar tentang emiten ini
            continue
        if any(n in t for n in neg) or not any(p in t for p in pos):
            continue
        sig = re.sub(r"[^a-z0-9]", "", t)[:60]
        if sig in seen:
            continue
        seen.add(sig)
        chosen.append(it)
        if len(chosen) >= C.get("max_items", 3):
            break
    return chosen


def fetch(ticker: str, company: str) -> list[dict]:
    if not C.get("enabled", True):
        return []
    import requests
    q = quote(f"{ticker} saham OR \"{company}\"")
    url = f"https://news.google.com/rss/search?q={q}&hl=id&gl=ID&ceid=ID:id"
    try:
        r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return []
        return select(parse_rss(r.text, C.get("days", 30)), ticker, company)
    except Exception as e:                                 # berita gagal diambil tidak boleh menghentikan laporan
        log.info("RSS katalis %s gagal: %s", ticker, e)
        return []


def to_text(items: list[dict]) -> str | None:
    if not items:
        return None
    lines = [f"- {it['title']} ({it['source']}, {it['date']:%d/%m/%Y}) — {it['link']}" for it in items]
    return "**Potensi katalis positif (berita terbaru):**\n" + "\n".join(lines)
