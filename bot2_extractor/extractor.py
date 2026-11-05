"""Bot 2: membaca file di folder emiten, menghitung QoQ/YoY, menulis Excel per emiten."""
import logging
import re

from core import config as cfg
from core import discord
from core.periods import Period
from core.storage import get_storage
from bot2_extractor import excel_out, metrics, store
from bot2_extractor.idx_excel import parse
from bot2_extractor.pdf_segments import extract_segments

log = logging.getLogger(__name__)
F = cfg.FOLDERS
REQUIRED = {"general": ["revenue", "net_profit"], "bank": ["core_income", "net_profit", "bs_loans", "bs_deposits"],
            "bank_sharia": ["core_income", "net_profit", "bs_loans", "bs_deposits"]}


def workbook_name(ticker: str) -> str:
    return f"{ticker} - Financial Data.xlsx"


def _period_folders(st, ticker):
    base = st.folder(f"{F['emiten']}/{ticker}")
    out = []
    for f in st.list(base):
        m = re.search(r"(1Q|2Q|3Q|FY)(\d{2})$", f["name"])
        if f["is_folder"] and m:
            p = Period.parse_label(m.group(1) + m.group(2))
            if p.year >= cfg.DATA["start_year"]:
                out.append((p, f["id"]))
    return base, sorted(out)


def run(ticker: str, force: bool = False) -> dict:
    st = get_storage()
    data = store.load(ticker)
    base, folders = _period_folders(st, ticker)
    updated = []
    for p, fid in folders:
        files = [f for f in st.list(fid) if not f["is_folder"] and not f["name"].startswith("_lama_")]
        xlsx = next((f for f in files if f["name"].lower().endswith(".xlsx")), None)
        from bot2_extractor.presentation import extract as read_pres, is_presentation
        pres = next((f for f in files if f["name"].lower().endswith(".pdf") and is_presentation(f["name"])), None)
        pdf = max((f for f in files if f["name"].lower().endswith(".pdf") and not is_presentation(f["name"])),
                  key=lambda f: f.get("size") or 0, default=None)
        if not xlsx:
            continue
        sig = {"xlsx": xlsx.get("md5"), "pdf": pdf.get("md5") if pdf else None, "pres": pres.get("md5") if pres else None}
        if not force and data["files"].get(p.key) == sig:
            continue
        local_x = cfg.WORK_DIR / f"{ticker}_{p.label}.xlsx"
        st.download(xlsx["id"], local_x)
        try:
            parsed = parse(local_x)
        except Exception as e:
            discord.status(f"{ticker} {p.label}: Excel file could not be read ({e}).", "error")
            continue
        if parsed["period"] != p.key:
            discord.status(f"{ticker} {p.label}: Excel content shows period {parsed['period']}, skipped.", "warn")
            continue
        if parsed["template"] == "unsupported":
            discord.status(f"{ticker}: report template ({parsed['industry']}) is not mapped yet. "
                           f"Send this file to the bot developer.", "warn")
            continue
        missing = [k for k in REQUIRED.get(parsed["template"], []) if not parsed["cur"].get(k)]
        if missing:
            discord.status(f"{ticker} {p.label}: required items not found in Excel: {', '.join(missing)}. "
                           f"Mapping needs adjustment.", "warn")
        segments = None
        if pdf:
            local_p = cfg.WORK_DIR / f"{ticker}_{p.label}.pdf"
            st.download(pdf["id"], local_p)
            key = "revenue" if parsed["template"] == "general" else "nii"
            try:
                segments = extract_segments(local_p, p.year, p.end_date.month, parsed["cur"].get(key),
                                            parsed["prior"].get(key))
            except Exception as e:
                log.warning("Segmen %s %s gagal dibaca: %s", ticker, p.label, e)
            if not segments:
                log.info("%s %s: tabel segmen di PDF tidak terbaca/tidak cocok, dilewati.", ticker, p.label)
        store.ingest(data, parsed, segments, sig)
        if pres and parsed["template"].startswith("bank"):
            local_s = cfg.WORK_DIR / f"{ticker}_{p.label}_pres.pdf"
            st.download(pres["id"], local_s)
            kpi = read_pres(local_s)
            if kpi:
                data["periods"][p.key]["presentation"] = kpi
            else:
                discord.status(f"{ticker} {p.label}: presentasi tidak memuat metrik yang terbaca dengan yakin.", "info")
        updated.append(p.key)

    if not data["periods"]:
        return data
    if updated or force or not st.find(base, workbook_name(ticker)):
        tables = []
        for pk in sorted(data["periods"]):
            P = Period.parse_key(pk)
            try:
                tables.append(metrics.build(data, P))
            except Exception as e:
                log.exception("Tabel %s %s gagal dibuat: %s", ticker, pk, e)
        path = cfg.WORK_DIR / workbook_name(ticker)
        excel_out.save_workbook(path, tables, data)
        st.upload(path, base, workbook_name(ticker))
        log.info("%s: workbook diperbarui (%d periode).", ticker, len(tables))
    store.save(data)
    data["_updated"] = updated
    return data
