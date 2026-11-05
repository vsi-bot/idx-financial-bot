"""Bot 3: membuat narasi + gambar tabel untuk periode baru, lalu mengirimnya ke Discord."""
import logging

from core import config as cfg
from core import discord
from core.periods import Period
from core.storage import get_storage
from core.sysfiles import analyst_notes
from bot2_extractor import metrics, store
from bot2_extractor.extractor import workbook_name
from bot1_collector.collector import period_path
from bot3_narrator import narrative, table_image

log = logging.getLogger(__name__)


def run(ticker: str, data: dict | None = None, force_periods: list[str] | None = None) -> list[str]:
    st = get_storage()
    data = data or store.load(ticker)
    start = Period.parse_label(cfg.DATA["narrate_from"])
    notes = analyst_notes()
    sent = []
    for pk in sorted(data.get("periods", {})):
        P = Period.parse_key(pk)
        forced = force_periods and P.label in force_periods
        if (P < start and not forced) or (pk in data["narrated"] and not forced):
            continue
        table = metrics.build(data, P)
        title, paras = narrative.build(table, data, notes.get((ticker, P.label)))
        img = cfg.WORK_DIR / f"{ticker}_{P.label}_table.png"
        table_image.render(table, data.get("name", ticker), ticker, data["currency"], img)
        attachments = []
        if cfg.DISCORD.get("attach_excel"):
            wb = cfg.WORK_DIR / workbook_name(ticker)
            if not wb.exists():
                f = st.find(st.folder(f"{cfg.FOLDERS['emiten']}/{ticker}"), workbook_name(ticker))
                if f:
                    st.download(f["id"], wb)
            if wb.exists():
                attachments.append(wb)
        folder_id = st.folder(period_path(ticker, P))
        footer = f"Source: IDX | {data.get('name', ticker)} | Data: {P.cum_label} & {P.q_label}"
        discord.report(title, paras, img, attachments, footer)
        # arsip di Drive
        st.upload(img, folder_id, img.name)
        txt = cfg.WORK_DIR / f"{ticker}_{P.label}_analysis.md"
        txt.write_text(f"# {title}\n\n" + "\n\n".join(paras) + "\n", encoding="utf-8")
        st.upload(txt, folder_id, txt.name)
        if pk not in data["narrated"]:
            data["narrated"].append(pk)
        sent.append(P.label)
    if sent:
        store.save({k: v for k, v in data.items() if not k.startswith("_")})
    return sent
