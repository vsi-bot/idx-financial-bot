"""Scraper idx.co.id memakai browser asli (Playwright), dengan jeda yang sopan.

- Bot membuka halaman "Laporan Keuangan dan Tahunan" seperti pengunjung biasa, lalu memanggil
  endpoint data yang dipakai halaman itu sendiri.
- Bot TIDAK menyelesaikan CAPTCHA. Jika IDX meminta verifikasi, bot berhenti (IDXBlocked),
  mengirim pemberitahuan, dan Anda tetap bisa memakai folder Inbox.
"""
import base64
import json
import logging
import random
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote, urlencode

from core import config as cfg
from core.periods import Period

log = logging.getLogger(__name__)
S = cfg.SCRAPER

_JS_TEXT = """async (url) => {
  const r = await fetch(url, {credentials: 'include', headers: {'Accept': 'application/json, text/plain, */*'}});
  return {status: r.status, text: await r.text()};
}"""
_JS_BIN = """async (url) => {
  const r = await fetch(url, {credentials: 'include'});
  const ctype = r.headers.get('content-type') || '';
  if (!r.ok) return {status: r.status, ctype, data: null};
  const buf = new Uint8Array(await r.arrayBuffer());
  let bin = ''; const chunk = 0x8000;
  for (let i = 0; i < buf.length; i += chunk) bin += String.fromCharCode.apply(null, buf.subarray(i, i + chunk));
  return {status: r.status, ctype, data: btoa(bin)};
}"""


class IDXBlocked(Exception):
    pass


def ensure_browser():
    """Pasang Chromium untuk Playwright hanya saat scraper benar-benar dibutuhkan."""
    try:
        subprocess.run([sys.executable, "-m", "playwright", "install", "--with-deps", "chromium"],
                       check=True, capture_output=True, timeout=600)
    except Exception as e:
        log.warning("Instalasi Chromium bermasalah: %s", e)


class IDX:
    def __enter__(self):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.launch(headless=S["headless"])
        self.ctx = self.browser.new_context(locale="id-ID", timezone_id="Asia/Jakarta",
                                            viewport={"width": 1366, "height": 850})
        self.page = self.ctx.new_page()
        self._open()
        return self

    def __exit__(self, *a):
        try:
            self.ctx.close()
            self.browser.close()
        finally:
            self._pw.stop()

    def _challenge(self) -> bool:
        try:
            t = (self.page.title() or "").lower()
        except Exception:
            return False
        return any(x in t for x in ("just a moment", "attention required", "tunggu sebentar"))

    def _open(self):
        url = S["base_url"] + S["report_page"]
        self.page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        for _ in range(30):  # tantangan JS non-interaktif biasanya selesai sendiri
            if not self._challenge():
                break
            time.sleep(1)
        if self._challenge():
            raise IDXBlocked("IDX menampilkan verifikasi bot.")
        time.sleep(random.uniform(2, 4))

    def pause(self):
        time.sleep(random.uniform(S["delay_min"], S["delay_max"]))

    def _json(self, url):
        r = self.page.evaluate(_JS_TEXT, url)
        txt = r["text"].lstrip()
        if r["status"] in (403, 429, 503) or txt.startswith("<"):
            raise IDXBlocked(f"Endpoint IDX menolak request (status {r['status']}).")
        if r["status"] != 200:
            raise RuntimeError(f"Status {r['status']}")
        return json.loads(txt)

    def reports(self, period: Period, ticker: str = "") -> list[dict]:
        """Daftar laporan untuk satu periode (semua emiten jika ticker kosong)."""
        out, start = [], S["index_from"]
        for _ in range(20):
            params = {"indexFrom": start, "pageSize": S["page_size"], "year": period.year, "reportType": "rdf",
                      "EmitenType": "s", "periode": period.code, "kodeEmiten": ticker,
                      "SortColumn": "KodeEmiten", "SortOrder": "asc"}
            data = self._json(f"{S['base_url']}{S['api_path']}?{urlencode(params)}")
            res = data.get("Results") or []
            out += res
            total = data.get("ResultCount") or len(res)
            if not res or len(out) >= total or ticker:
                break
            start += S["page_size"] if S["index_from"] == 0 else 1
            self.pause()
        return out

    def download(self, att: dict, dest_dir: Path) -> Path:
        path = att.get("File_Path") or ""
        name = att.get("File_Name") or Path(path).name
        url = path if path.startswith("http") else S["base_url"] + quote(path, safe="/:")
        r = self.page.evaluate(_JS_BIN, url)
        if r["status"] in (403, 429, 503):
            raise IDXBlocked(f"Download ditolak (status {r['status']})")
        if r["status"] != 200 or not r["data"]:
            raise RuntimeError(f"Gagal download {name} (status {r['status']})")
        if "text/html" in r["ctype"]:
            raise IDXBlocked("Yang terunduh halaman HTML, bukan file")
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / name
        dest.write_bytes(base64.b64decode(r["data"]))
        return dest


def pick_attachments(atts: list[dict]) -> list[dict]:
    """Ambil Excel laporan keuangan + PDF terbesar (biasanya dokumen laporan keuangan lengkap)."""
    xlsx = [a for a in atts if (a.get("File_Name") or "").lower().endswith(".xlsx")
            and "financialstatement" in (a.get("File_Name") or "").lower()]
    pdfs = sorted([a for a in atts if (a.get("File_Name") or "").lower().endswith(".pdf")],
                  key=lambda a: -(a.get("File_Size") or 0))
    return xlsx[:1] + pdfs[:1]
