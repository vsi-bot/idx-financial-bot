"""Pembaca corporate presentation bank (opsional, upaya terbaik, tanpa AI).

Mencari NPL, LAR, coverage, CAR, NIM beserta persentase di dekatnya. Angka di luar rentang wajar ditolak;
bila tidak yakin, metrik dilewati (tidak ditebak).
"""
import logging
import re

log = logging.getLogger(__name__)
PRES_WORDS = ("presentation", "presentasi", "investor", "analyst", "earnings call", "public expose")
PATTERNS = {
    "npl": (r"\b(?:gross\s+)?NP[LF]\b(?!\s*coverage)", (0.0, 15.0)),
    "lar": (r"\bLaR\b|\bLoan[s]?\s+at\s+Risk\b", (0.0, 40.0)),
    "coverage": (r"\b(?:NP[LF]\s+)?coverage(?:\s+ratio)?\b", (40.0, 800.0)),
    "car": (r"\bCAR\b|\bKPMM\b|\bCapital\s+Adequacy", (8.0, 60.0)),
    "nim": (r"\bNIM\b|\bNet\s+Interest\s+Margin\b|\bNOM\b", (0.5, 15.0)),
}
NUM = r"(\d{1,3}(?:[.,]\d{1,2})?)\s?%"


def is_presentation(name: str) -> bool:
    low = name.lower()
    return any(w in low for w in PRES_WORDS)


def extract(pdf_path) -> dict:
    from pypdf import PdfReader
    try:
        r = PdfReader(str(pdf_path))
        if r.is_encrypted:
            r.decrypt("")
        text = " ".join((p.extract_text() or "") for p in r.pages[:40])
    except Exception as e:
        log.info("Presentasi tidak terbaca: %s", e)
        return {}
    text = re.sub(r"\s+", " ", text)
    out = {}
    for key, (label_rx, (lo, hi)) in PATTERNS.items():
        for mt in re.finditer(label_rx, text, re.I):
            win = text[mt.end():mt.end() + 60]
            nm = re.search(NUM, win)
            if not nm:
                continue
            v = float(nm.group(1).replace(",", "."))
            if lo <= v <= hi:
                out[key] = v / 100
                break
    return out
