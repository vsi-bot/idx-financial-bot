"""Mengenali kode emiten & periode dari sebuah file (nama file, subfolder, lalu isi file)."""
import logging
import re
from pathlib import Path

from core.periods import Period

log = logging.getLogger(__name__)

IDX_RE = re.compile(r"FinancialStatement-(\d{4})-(I{1,3}|IV|Tahunan)-([A-Z]{4})", re.I)
USER_RE = re.compile(r"(?<![A-Za-z])([A-Z]{4})[\s_\-]+((?i:1Q|2Q|3Q|4Q|FY))[\s_\-]*(\d{4}|\d{2})(?!\d)")
PERIOD_ONLY_RE = re.compile(r"(?<![A-Za-z0-9])((?i:1Q|2Q|3Q|4Q|FY))[\s_\-]*(\d{4}|\d{2})(?!\d)")
DATE_RE = re.compile(r"(31|30)[\s_\-]*(mar(?:et|ch)?|jun[ie]?|sep(?:t(?:ember)?)?|des(?:ember)?|dec(?:ember)?)"
                     r"[\s_\-,]*((?:20)?\d{2})(?!\d)", re.I)
ROMAN = {"I": "tw1", "II": "tw2", "III": "tw3", "IV": "audit", "TAHUNAN": "audit"}
PREFIX = {"1Q": "tw1", "2Q": "tw2", "3Q": "tw3", "4Q": "audit", "FY": "audit"}
MONTH = {"mar": "tw1", "jun": "tw2", "sep": "tw3", "des": "audit", "dec": "audit"}
STOP = {"pt", "tbk", "persero", "the", "and", "dan"}


def _year(s: str) -> int:
    return int(s) if len(s) == 4 else 2000 + int(s)


def _norm_words(text: str) -> str:
    return " ".join(w for w in re.split(r"[^a-z0-9]+", text.lower()) if w and w not in STOP)


def _period_from_text(text: str) -> Period | None:
    m = PERIOD_ONLY_RE.search(text)
    if m:
        return Period.of(_year(m.group(2)), PREFIX[m.group(1).upper()])
    m = DATE_RE.search(text)
    if m:
        return Period.of(_year(m.group(3)), MONTH[m.group(2).lower()[:3]])
    return None


def _ticker_from_name(text: str, watchlist: dict) -> str | None:
    t = f" {_norm_words(text)} "
    best, best_len = None, 0
    for code, info in watchlist.items():
        core = _norm_words(info["nama"])
        if core and f" {core} " in t and len(core) > best_len:
            best, best_len = code, len(core)
    return best


def identify(name: str, watchlist: dict, folder_hint: str | None = None, local: Path | None = None):
    """Kembalikan (kode, Period, sumber_pengenalan) atau None."""
    m = IDX_RE.search(name)
    if m:
        return m.group(3).upper(), Period.of(int(m.group(1)), ROMAN[m.group(2).upper()]), "IDX file name"
    m = USER_RE.search(name)
    if m:
        return m.group(1), Period.of(_year(m.group(3)), PREFIX[m.group(2).upper()]), "file name"
    hint = folder_hint.strip().upper() if folder_hint and re.fullmatch(r"[A-Za-z]{4}", folder_hint.strip()) else None
    per = _period_from_text(name)
    tk = hint or _ticker_from_name(name.replace("_", " "), watchlist)
    if tk and per:
        return tk, per, "subfolder" if hint else "company name in file name"

    # isi file
    if local and local.suffix.lower() == ".xlsx":
        try:
            from bot2_extractor.idx_excel import read_meta
            meta = read_meta(local)
            if meta["ticker"]:
                return meta["ticker"], meta["period"], "Excel content"
        except Exception as e:
            log.info("Isi Excel %s tidak dikenali: %s", name, e)
    if local and local.suffix.lower() == ".pdf":
        try:
            from pypdf import PdfReader
            r = PdfReader(str(local))
            if r.is_encrypted:
                r.decrypt("")
            text = " ".join((r.pages[i].extract_text() or "") for i in range(min(3, len(r.pages))))
            per2 = per or _period_from_text(text)
            tk2 = tk or _ticker_from_name(text, watchlist)
            if tk2 and per2:
                return tk2, per2, "PDF content"
        except Exception as e:
            log.info("Isi PDF %s tidak terbaca: %s", name, e)
    return None


def validate(local: Path, ticker: str, period: Period) -> str | None:
    """Kembalikan pesan kesalahan, atau None jika file sehat."""
    suf = local.suffix.lower()
    if suf == ".xlsx":
        try:
            from bot2_extractor.idx_excel import read_meta
            meta = read_meta(local)
        except Exception as e:
            return f"Excel unreadable ({e})"
        if meta["ticker"] and meta["ticker"] != ticker:
            return f"Excel belongs to {meta['ticker']}, not {ticker}"
        if meta["period"] != period:
            return f"Excel period is {meta['period'].label}, not {period.label}"
    elif suf == ".pdf":
        try:
            from pypdf import PdfReader
            r = PdfReader(str(local))
            if r.is_encrypted and not r.decrypt(""):
                return "PDF is password-protected"
            if len(r.pages) == 0:
                return "PDF is empty"
        except Exception as e:
            return f"PDF is corrupted ({e})"
    return None
