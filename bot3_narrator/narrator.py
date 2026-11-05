"""Bot 3: menyiapkan narasi + gambar tabel (stage), lalu mengirimnya berurutan ke Discord (send).

- stage(): dijalankan paralel per emiten. Hasil ditaruh di _Sistem/outbox (belum dikirim).
- send_outbox(): dijalankan sekali di akhir. Mengirim per emiten secara berurutan sehingga pesan
  antar-emiten tidak bercampur, lalu menandai periode sebagai terkirim.
Aturan:
- Tabel berstatus FAIL tidak dikirim (dicatat di log & sheet Validasi).
- Periode yang sudah terkirim tanpa data kuartalan akan dikirim ulang SATU kali sebagai "pembaruan"
  begitu data pembanding kuartalan tersedia.
"""
import json
import logging

from core import config as cfg
from core import activity, discord
from core.periods import Period
from core.storage import get_storage
from core.sysfiles import analyst_notes
from bot2_extractor import metrics, store
from bot1_collector.collector import period_path
from bot3_narrator import narrative, table_image

log = logging.getLogger(__name__)
OUTBOX = f"{cfg.FOLDERS['system']}/outbox"
SENT = f"{OUTBOX}/_terkirim"


def _meta(data):
    return data.setdefault("narrate_meta", {})


def stage(ticker: str, data: dict | None = None, force_periods: list[str] | None = None) -> list[str]:
    st = get_storage()
    data = data or store.load(ticker)
    start = Period.parse_label(cfg.DATA["narrate_from"])
    notes = analyst_notes()
    meta = _meta(data)
    staged = []
    for pk in sorted(data.get("periods", {})):
        P = Period.parse_key(pk)
        forced = bool(force_periods and P.label in force_periods)
        if P < start and not forced:
            continue
        table = metrics.build(data, P)
        m = meta.get(pk, {})
        if forced:
            reason = "forced"
        elif pk not in data["narrated"]:
            reason = "new"
        elif not m.get("q_available") and table["q_available"] and not m.get("resent"):
            reason = "update"
        else:
            continue
        if table["status"] == "FAIL":
            failed = "; ".join(f"{c['name']} ({c['detail']})" for c in table["checks"] if c["status"] == "FAIL")
            activity.add(f"{ticker} {P.label}: TIDAK dikirim ke Discord karena validasi FAIL: {failed}", "error")
            continue
        for c in table["checks"]:
            if c["status"] == "WARN":
                activity.add(f"{ticker} {P.label}: WARN {c['name']} - {c['detail']}", "warn")

        title, paras = narrative.build(table, data, notes.get((ticker, P.label)))
        if reason == "update":
            title += " (pembaruan: data kuartalan lengkap)"
        img = cfg.WORK_DIR / f"{ticker}_{P.label}_table.png"
        table_image.render(table, data.get("name", ticker), ticker, data["currency"], img)
        footer = f"Sumber: IDX | {data.get('name', ticker)} | Data: {P.cum_label} & {P.q_label}"

        # arsip di folder periode emiten
        pf = st.folder(period_path(ticker, P))
        st.upload(img, pf, img.name)
        md = cfg.WORK_DIR / f"{ticker}_{P.label}_analysis.md"
        md.write_text(f"# {title}\n\n" + "\n\n".join(paras) + "\n", encoding="utf-8")
        st.upload(md, pf, md.name)

        # antrean kirim
        ob = st.folder(OUTBOX)
        item = {"ticker": ticker, "period": pk, "title": title, "paragraphs": paras, "footer": footer,
                "image": img.name, "reason": reason, "q_available": table["q_available"], "status": table["status"]}
        js = cfg.WORK_DIR / f"{ticker}_{P.label}.json"
        js.write_text(json.dumps(item, ensure_ascii=False, indent=1), encoding="utf-8")
        st.upload(img, ob, img.name)
        st.upload(js, ob, js.name, "application/json")
        staged.append(P.label)
    return staged


def pending_count() -> int:
    st = get_storage()
    return sum(1 for f in st.list(st.folder(OUTBOX)) if f["name"].endswith(".json"))


def send_outbox() -> int:
    """Kirim semua antrean, berurutan per emiten lalu per periode."""
    st = get_storage()
    ob, sent_dir = st.folder(OUTBOX), st.folder(SENT)
    files = {f["name"]: f for f in st.list(ob) if not f["is_folder"]}
    items = []
    for name, f in files.items():
        if name.endswith(".json"):
            local = cfg.WORK_DIR / f"ob_{name}"
            st.download(f["id"], local)
            items.append((json.loads(local.read_text(encoding="utf-8")), f))
    items.sort(key=lambda x: (x[0]["ticker"], x[0]["period"]))
    n = 0
    for item, jf in items:
        imgf = files.get(item["image"])
        if not imgf:
            activity.add(f"{item['ticker']}: gambar antrean hilang, dilewati", "warn")
            continue
        img = cfg.WORK_DIR / item["image"]
        st.download(imgf["id"], img)
        discord.report(item["title"], item["paragraphs"], img, [], item["footer"])
        data = store.load(item["ticker"])
        pk = item["period"]
        if pk not in data["narrated"]:
            data["narrated"].append(pk)
        m = _meta(data).setdefault(pk, {})
        m["q_available"] = m.get("q_available") or item["q_available"]
        if item["reason"] == "update":
            m["resent"] = True
        store.save({k: v for k, v in data.items() if not k.startswith("_")})
        st.move(jf["id"], sent_dir, ob)
        st.move(imgf["id"], sent_dir, ob)
        activity.add(f"{item['ticker']} {Period.parse_key(pk).label}: terkirim ke Discord ({item['reason']}, {item['status']})", "ok")
        n += 1
    return n
