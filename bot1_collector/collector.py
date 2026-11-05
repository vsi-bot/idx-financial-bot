"""Bot 1: mengumpulkan laporan keuangan dari dua sumber (Inbox Drive & IDX) ke folder per emiten."""
import logging
import re
from datetime import date, datetime, timedelta, timezone

from core import config as cfg
from core import discord
from core.periods import Period, all_periods_until
from core.storage import get_storage
from core.sysfiles import watchlist
from bot1_collector.identify import identify, validate

log = logging.getLogger(__name__)
F = cfg.FOLDERS
STATE = "state.json"
OLD_PREFIX = "_lama_"


def period_path(ticker: str, p: Period) -> str:
    return f"{F['emiten']}/{ticker}/{F['period_format'].format(label=p.label)}"


def _load_state(st):
    s = st.read_json(F["system"], STATE, None) or {}
    s.setdefault("have", {})          # {KODE: {periode: true}} periode yang Excel-nya sudah ada
    s.setdefault("last_scrape", None)
    s.setdefault("last_block_notice", None)
    return s


def file_into_period(st, state, file_id, name, src_folder, ticker, p: Period, md5=None) -> str:
    """Pindahkan file ke folder periode. Kembalikan 'ok', 'duplikat', atau 'revisi'."""
    dest = st.folder(period_path(ticker, p))
    existing = [f for f in st.list(dest) if not f["is_folder"]]
    if md5 and any(f.get("md5") == md5 for f in existing):
        st.move(file_id, st.folder(f"{F['inbox']}/_Duplikat"), src_folder)
        return "duplikat"
    result = "ok"
    ext = name.lower().rsplit(".", 1)[-1]
    if ext in ("xlsx", "pdf"):
        for f in existing:
            if f["name"].lower().endswith("." + ext) and not f["name"].startswith(OLD_PREFIX) and \
                    (ext == "xlsx" or f["name"] == name):
                stamp = datetime.now().strftime("%Y%m%d")
                st.move(f["id"], dest, dest, new_name=f"{OLD_PREFIX}{stamp}_{f['name']}")
                result = "revisi"
    st.move(file_id, dest, src_folder)
    if ext == "xlsx":
        state["have"].setdefault(ticker, {})[p.key] = True
    return result


# ------------------------------------------------------------------ Sumber B: Inbox
def process_inbox(st, state, wl) -> set[str]:
    changed = set()
    inbox = st.folder(F["inbox"])
    unknown = st.folder(f"{F['inbox']}/{F['unknown']}")
    items = []
    for f in st.list(inbox):
        if f["is_folder"]:
            if f["name"] in (F["unknown"], "_Duplikat"):
                continue
            items += [(c, f["id"], f["name"]) for c in st.list(f["id"]) if not c["is_folder"]]
        else:
            items.append((f, inbox, None))
    if not items:
        return changed
    limit = cfg.SETTINGS.get("inbox", {}).get("max_files_per_run", 60)
    if len(items) > limit:
        discord.status(f"Inbox berisi {len(items)} file; {limit} diproses sekarang, sisanya di putaran berikutnya.")
        items = items[:limit]
    start = cfg.DATA["start_year"]
    for f, parent, hint in items:
        local = cfg.WORK_DIR / f"inbox_{re.sub(r'[^A-Za-z0-9._-]', '_', f['name'])}"
        res = identify(f["name"], wl, hint)              # 1) cukup dari nama file / subfolder
        is_pdf = f["name"].lower().endswith(".pdf")
        if not res or not is_pdf:                        # 2) baru unduh bila perlu (Excel kecil, atau nama tak jelas)
            st.download(f["id"], local)
            res = res or identify(f["name"], wl, hint, local)
        if not res:
            st.move(f["id"], unknown, parent)
            discord.status(f"File **{f['name']}** was not recognized and moved to `{F['inbox']}/{F['unknown']}`. "
                           f"Rename it as `TICKER_3Q26.pdf` and move it back to the Inbox.", "warn")
            continue
        tk, p, how = res
        err = None if (is_pdf and not local.exists()) else validate(local, tk, p)
        if err:
            st.move(f["id"], unknown, parent)
            discord.status(f"File **{f['name']}** ({tk} {p.label}) rejected: {err}.", "warn")
            continue
        md5 = f.get("md5")
        r = file_into_period(st, state, f["id"], f["name"], parent, tk, p, md5)
        if r == "duplikat":
            discord.status(f"{tk} {p.label}: **{f['name']}** is identical to an existing file (moved to `_Duplikat`).")
            continue
        note = " (revised report detected, previous version kept)" if r == "revisi" else ""
        if tk not in wl:
            discord.status(f"{tk} {p.label}: **{f['name']}** saved, but the company is outside the watchlist and will not be analyzed.")
        elif p.year < start:
            discord.status(f"{tk} {p.label}: **{f['name']}** archived (before {start}).")
        else:
            changed.add(tk)
            discord.status(f"{tk} {p.label}: **{f['name']}** received from Inbox (identified by {how}){note}.", "ok")
    return changed


# ------------------------------------------------------------------ Sumber A: IDX
def in_season(today: date) -> bool:
    md = today.strftime("%m-%d")
    return any(a <= md <= b for a, b in cfg.SCRAPER["seasons"])


def should_scrape(state) -> bool:
    if not cfg.SCRAPER["enabled"]:
        return False
    if cfg.FORCE_SCRAPE or not state["last_scrape"]:
        return True
    last = datetime.fromisoformat(state["last_scrape"])
    if datetime.now(timezone.utc) - last < timedelta(minutes=cfg.SCRAPER.get("min_interval_minutes", 60)):
        return False
    if in_season(date.today()):
        return True
    return datetime.now(timezone.utc) - last >= timedelta(hours=cfg.SCRAPER["offseason_interval_hours"])


def scrape(st, state, wl) -> set[str]:
    from bot1_collector.idx_scraper import IDX, IDXBlocked, ensure_browser, pick_attachments

    changed = set()
    todo = {}
    for p in all_periods_until(cfg.DATA["start_year"]):
        need = [t for t in wl if not state["have"].get(t, {}).get(p.key)]
        if need:
            todo[p] = set(need)
    if not todo:
        log.info("Semua laporan watchlist sudah lengkap, scraper tidak perlu jalan.")
        return changed
    ensure_browser()
    try:
        with IDX() as idx:
            for p, need in sorted(todo.items()):
                try:
                    results = idx.reports(p)
                except IDXBlocked:
                    raise
                except Exception as e:
                    log.warning("Daftar laporan %s gagal: %s", p.label, e)
                    continue
                idx.pause()
                for r in results:
                    tk = (r.get("KodeEmiten") or "").strip().upper()
                    if tk not in need:
                        continue
                    atts = pick_attachments(r.get("Attachments") or [])
                    if not atts:
                        continue
                    tmp = cfg.WORK_DIR / "idx" / tk / p.label
                    for a in atts:
                        try:
                            local = idx.download(a, tmp)
                        except IDXBlocked:
                            raise
                        except Exception as e:
                            log.warning("%s %s: %s", tk, p.label, e)
                            continue
                        if validate(local, tk, p):
                            continue
                        f = st.upload(local, st.folder(period_path(tk, p)), local.name)
                        if local.suffix.lower() == ".xlsx":
                            state["have"].setdefault(tk, {})[p.key] = True
                        idx.pause()
                    if state["have"].get(tk, {}).get(p.key):
                        changed.add(tk)
                        discord.status(f"{tk} {p.label}: new financial report downloaded from IDX.", "ok")
        state["last_scrape"] = datetime.now(timezone.utc).isoformat()
    except IDXBlocked as e:
        last = state.get("last_block_notice")
        if not last or datetime.now(timezone.utc) - datetime.fromisoformat(last) > timedelta(hours=24):
            discord.status(f"IDX scraper blocked ({e}). Files in `{F['inbox']}` are still processed. "
                           f"See the setup guide, section 'If the IDX scraper is blocked'.", "error")
            state["last_block_notice"] = datetime.now(timezone.utc).isoformat()
    except Exception as e:
        log.exception("Scraper error")
        discord.status(f"IDX scraper error: {e}", "error")
    return changed


def sync_have(st, state, wl):
    """Sinkronkan catatan 'have' dengan isi folder Drive (dijalankan saat catatan masih kosong)."""
    if state["have"]:
        return
    root = st.folder(F["emiten"])
    for f in st.list(root):
        if not f["is_folder"] or f["name"] not in wl:
            continue
        for pf in st.list(f["id"]):
            m = re.search(r"(1Q|2Q|3Q|FY)(\d{2})$", pf["name"])
            if pf["is_folder"] and m and any(x["name"].lower().endswith(".xlsx") for x in st.list(pf["id"])):
                state["have"].setdefault(f["name"], {})[Period.parse_label(m.group(1) + m.group(2)).key] = True


def ensure_structure(st):
    """Buat folder & file sistem sekali di sini, agar job paralel berikutnya tidak membuat duplikat."""
    from core.sysfiles import EDITABLE, load_text
    for path in (F["inbox"], f"{F['inbox']}/{F['unknown']}", f"{F['inbox']}/_Duplikat", F["emiten"],
                 F["system"], f"{F['system']}/data"):
        st.folder(path)
    for name in EDITABLE:
        load_text(name)


def run(scrape_allowed: bool = True) -> list[str]:
    st = get_storage()
    ensure_structure(st)
    wl = watchlist()
    state = _load_state(st)
    sync_have(st, state, wl)
    changed = process_inbox(st, state, wl)
    if scrape_allowed and should_scrape(state):
        changed |= scrape(st, state, wl)
    st.write_json(F["system"], STATE, state)
    return sorted(changed)
