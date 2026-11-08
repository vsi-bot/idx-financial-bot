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
import os

from core import config as cfg
from core import activity, discord
from core.periods import Period
from core.storage import get_storage
from core.sysfiles import analyst_notes
from bot2_extractor import metrics, store
from bot1_collector.collector import period_path
from bot3_narrator import narrative, table_image
from bot3_narrator.facts import check
from core.sysfiles import review_ok, watchlist

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
    wl = watchlist().get(ticker, {})
    holds = []

    def hold(P, reason, fix):
        holds.append({"type": "hold", "ticker": ticker, "period": P.label, "reason": reason, "fix": fix})
    for pk in sorted(data.get("periods", {})):
        P = Period.parse_key(pk)
        forced = bool(force_periods and P.label in force_periods)
        if P < start and not forced:
            continue
        table = metrics.build(data, P)
        m = meta.get(pk, {})
        sig = data.get("files", {}).get(pk)
        if forced:
            reason = "forced"
        elif pk in data["narrated"] and m.get("sig") and sig and (m["sig"] or {}).get("xlsx") != sig.get("xlsx") \
                and (m.get("corrected_sig") or {}).get("xlsx") != sig.get("xlsx"):
            reason = "correction"
        elif pk not in data["narrated"]:
            reason = "new"
        elif not m.get("q_available") and table["q_available"] and not m.get("resent"):
            reason = "update"
        else:
            continue
        entry = data["periods"][pk]
        if entry.get("fiscal_ok") is False:
            hold(P, "tahun buku bukan Januari-Desember", "periode kuartal perlu dipetakan manual; hubungi pengembang")
            continue
        tpl_ok = data.get("template") in cfg.SETTINGS.get("review", {}).get("auto_ok_templates", []) and \
            any(w in (data.get("industry") or "").lower() for w in ("umum", "general", "bank", "keuangan", "financial"))
        if (not tpl_ok or entry["cur"].get("_revenue_proxy")) and ticker not in review_ok():
            hold(P, f"template laporan baru ({data.get('industry') or data.get('template')}) perlu ditinjau sekali",
                 f"cek tabel di Excel emiten; jika benar, tambahkan {ticker} ke _Sistem/review_ok.txt")
            continue
        if os.getenv("HOLD_PUBLISH") == "1":
            hold(P, "gerbang mutu (golden set) gagal untuk versi kode/konfigurasi terbaru",
                 "lihat job 'Gerbang mutu' di GitHub Actions; kembalikan perubahan terakhir atau perbarui snapshot")
            continue
        for u in table.get("untranslated", []):
            holds.append({"type": "suggest", "text": f"{ticker}: {u}"})
        if table["status"] == "FAIL":
            failed = "; ".join(f"{c['name']} ({c['detail']})" for c in table["checks"] if c["status"] == "FAIL")
            activity.add(f"{ticker} {P.label}: TIDAK dikirim ke Discord karena validasi FAIL: {failed}", "error")
            hold(P, f"validasi tabel FAIL: {failed}", "cek sheet Validasi di Excel emiten / file sumber dari IDX")
            continue
        for c in table["checks"]:
            if c["status"] == "WARN":
                activity.add(f"{ticker} {P.label}: WARN {c['name']} - {c['detail']}", "warn")

        try:
            title, paras, facts = narrative.build(table, data, notes.get((ticker, P.label)), wl)
        except ValueError as e:                     # jembatan laba tidak menjumlah, dll.
            hold(P, f"narasi tidak konsisten: {e}", "periksa pemetaan pos laba di Excel; hubungi pengembang bila berulang")
            continue
        if reason == "correction":
            old = m.get("key_values", {})
            ch = []
            for k, nm in (("revenue", "pendapatan"), ("total_op_income", "pendapatan operasional"), ("net_profit", "laba bersih")):
                new = (table["m"].get(k) or {}).get("vals", [None] * 5)[1]
                if old.get(k) is not None and new is not None and abs(new - old[k]) > abs(old[k]) * 0.001:
                    ch.append(f"{nm} dari {facts.money(old[k], how='old')} menjadi {facts.money(new, (k, 1))}")
            paras.insert(0, "**Koreksi:** emiten memperbarui laporan " + P.label
                         + (" dengan perubahan " + "; ".join(ch) + "." if ch else "; angka utama tidak berubah material."))
            title += " (koreksi)"
        if reason == "update":
            title += " (pembaruan: data kuartalan lengkap)"
        # potensi katalis positif dari RSS Google News (link sumber wajib)
        from bot3_narrator import catalysts as cat_mod
        cat_items = cat_mod.fetch(ticker, data.get("name", ticker))
        ctext = cat_mod.to_text(cat_items)
        if ctext:
            ctext = narrative._register_numbers(facts, ctext, convert=False)
            last = paras[-1] if paras else ""
            pos = len(paras) - 1 if last.startswith(("Hal yang", "Ke depan", "Fokus pemantauan")) else len(paras)
            paras.insert(pos, ctext)
        bad = check(paras, facts)
        if bad:
            hold(P, "narasi gagal checklist: " + "; ".join(bad[:4]), "laporan pengembang; data tabel tetap tersimpan di Excel")
            continue
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
        kv = {k: (table["m"].get(k) or {}).get("vals", [None] * 5)[1] for k in ("revenue", "total_op_income", "net_profit")}
        from bot3_narrator import claude_manual as cmn
        await_until = None
        if cmn.enabled_for(ticker):
            from datetime import datetime, timedelta, timezone
            from core.sysfiles import style_replacements
            prompt = cmn.build_prompt(ticker, data.get("name", ticker), P, table, facts, title, paras, cat_items,
                                      list(style_replacements().keys()))
            pf_ = cfg.WORK_DIR / f"{ticker}_{P.label}.md"
            pf_.write_text(prompt, encoding="utf-8")
            st.upload(pf_, st.folder(f"{cfg.FOLDERS['system']}/{cmn.CM.get('prompt_folder', 'untuk_claude')}"), pf_.name, "text/markdown")
            await_until = (datetime.now(timezone.utc) + timedelta(hours=cmn.CM.get("wait_hours", 4))).isoformat()
            activity.add(f"{ticker} {P.label}: prompt narasi untuk Claude siap di _Sistem/untuk_claude (menunggu jawaban "
                         f"maks. {cmn.CM.get('wait_hours', 4)} jam).", "info")
        item = {"ticker": ticker, "period": pk, "title": title, "paragraphs": paras, "footer": footer,
                "image": img.name, "reason": reason, "q_available": table["q_available"], "status": table["status"],
                "sig": sig, "key_values": kv, "await_claude_until": await_until,
                "allowed": sorted({f["text"] for f in facts.items}), "template": table["template"]}
        js = cfg.WORK_DIR / f"{ticker}_{P.label}.json"
        js.write_text(json.dumps(item, ensure_ascii=False, indent=1), encoding="utf-8")
        st.upload(img, ob, img.name)
        st.upload(js, ob, js.name, "application/json")
        staged.append(P.label)
    if holds:                                   # dikonsolidasikan ke file notifikasi oleh tahap kirim
        st = get_storage()
        js = cfg.WORK_DIR / f"hold_{ticker}.json"
        js.write_text(json.dumps(holds, ensure_ascii=False), encoding="utf-8")
        st.upload(js, st.folder(OUTBOX), js.name, "application/json")
    return staged


def pending_count() -> int:
    st = get_storage()
    return sum(1 for f in st.list(st.folder(OUTBOX)) if f["name"].endswith(".json"))


def send_outbox() -> int:
    """Kirim semua antrean, berurutan per emiten lalu per periode."""
    st = get_storage()
    ob, sent_dir = st.folder(OUTBOX), st.folder(SENT)
    files = {f["name"]: f for f in st.list(ob) if not f["is_folder"]}
    from core import notify
    items, holds, suggestions = [], [], []
    for name, f in files.items():
        if name.startswith("hold_") and name.endswith(".json"):
            local = cfg.WORK_DIR / f"ob_{name}"
            st.download(f["id"], local)
            for h in json.loads(local.read_text(encoding="utf-8")):
                (suggestions.append(h["text"]) if h.get("type") == "suggest" else holds.append(h))
            st.move(f["id"], sent_dir, ob)
            continue
        if name.endswith(".json"):
            local = cfg.WORK_DIR / f"ob_{name}"
            st.download(f["id"], local)
            items.append((json.loads(local.read_text(encoding="utf-8")), f))
    items.sort(key=lambda x: (x[0]["ticker"], x[0]["period"]))
    n = 0
    failed_send = False
    sent_pairs = []
    for item, jf in items:
        imgf = files.get(item["image"])
        if not imgf:
            activity.add(f"{item['ticker']}: gambar antrean hilang, dilewati", "warn")
            continue
        if item.get("await_claude_until"):
            use = _claude_answer(st, item, holds)
            if use == "wait":
                continue                         # tetap di antrean; dicek lagi pada putaran berikutnya
        img = cfg.WORK_DIR / item["image"]
        st.download(imgf["id"], img)
        if not discord.report(item["title"], item["paragraphs"], img, [], item["footer"]):
            err = discord.LAST_ERROR or "galat tidak diketahui"
            activity.add(f"{item['ticker']} {Period.parse_key(item['period']).label}: GAGAL dikirim ke Discord - {err}. "
                         f"Laporan tetap di antrean dan dicoba lagi pada putaran berikutnya.", "error")
            holds.append({"type": "hold", "ticker": item["ticker"], "period": Period.parse_key(item["period"]).label,
                          "reason": f"gagal kirim ke Discord: {err}",
                          "fix": "cek GitHub Secret DISCORD_WEBHOOK_URL (webhook masih aktif?); laporan dicoba ulang otomatis"})
            failed_send = True
            continue
        data = store.load(item["ticker"])
        pk = item["period"]
        if pk not in data["narrated"]:
            data["narrated"].append(pk)
        m = _meta(data).setdefault(pk, {})
        m["q_available"] = m.get("q_available") or item["q_available"]
        if item["reason"] == "update":
            m["resent"] = True
        m["sig"] = item.get("sig") or m.get("sig")
        m["key_values"] = item.get("key_values") or m.get("key_values")
        if item["reason"] == "correction":
            m["corrected_sig"] = item.get("sig")
        store.save({k: v for k, v in data.items() if not k.startswith("_")})
        st.move(jf["id"], sent_dir, ob)
        st.move(imgf["id"], sent_dir, ob)
        activity.add(f"{item['ticker']} {Period.parse_key(pk).label}: terkirim ke Discord ({item['reason']}, {item['status']})", "ok")
        sent_pairs.append((item["ticker"], Period.parse_key(pk).label))
        n += 1
    notify.record(holds)
    notify.resolve(sent_pairs)
    for sug in suggestions:
        activity.add(f"usulan kamus: {sug}", "info")
    if suggestions:
        st_ = get_storage()
        old = st_.read_text(cfg.FOLDERS["system"], "usulan_kamus.txt") or "# Label yang belum punya terjemahan / pemetaan (usulan, tinjau lalu tambahkan ke config).\n"
        new = sorted(set(suggestions) - set(old.splitlines()))
        if new:
            path = cfg.WORK_DIR / "usulan_kamus.txt"
            path.write_text(old.rstrip("\n") + "\n" + "\n".join(new) + "\n", encoding="utf-8")
            st_.upload(path, st_.folder(cfg.FOLDERS["system"]), "usulan_kamus.txt", "text/plain")
    if failed_send:
        raise RuntimeError(f"Sebagian laporan gagal dikirim ke Discord: {discord.LAST_ERROR}")
    return n


def _claude_answer(st, item, holds):
    """Pakai jawaban Claude bila sudah ada & lolos cek; tunggu bila belum lewat batas waktu; selain itu narasi otomatis."""
    from datetime import datetime, timezone
    from bot3_narrator import claude_manual as cmn
    label = Period.parse_key(item["period"]).label
    folder = st.folder(f"{cfg.FOLDERS['system']}/{cmn.CM.get('answer_folder', 'dari_claude')}")
    ans = next((f for f in st.list(folder) if not f["is_folder"] and f["name"].upper().startswith(f"{item['ticker']}_{label}".upper())), None)
    expired = datetime.now(timezone.utc) >= datetime.fromisoformat(item["await_claude_until"])
    if ans:
        local = cfg.WORK_DIR / f"ans_{ans['name']}"
        st.download(ans["id"], local)
        title, paras, cats = cmn.parse_answer(local.read_text(encoding="utf-8"))
        bad = cmn.check_answer(title, paras, cats, set(item.get("allowed", [])), item.get("template", ""))
        used = st.folder(f"{cfg.FOLDERS['system']}/{cmn.CM.get('answer_folder', 'dari_claude')}/_terpakai")
        if not bad:
            if cats:
                paras.insert(len(paras) - 1 if len(paras) > 1 else len(paras), "**Potensi katalis positif:**\n" + cats)
            item["title"], item["paragraphs"] = title, paras
            item["footer"] = item["footer"] + " | Narasi: Claude"
            st.move(ans["id"], used, folder)
            activity.add(f"{item['ticker']} {label}: narasi Claude lolos cek dan dipakai.", "ok")
            return "use"
        holds.append({"type": "hold", "ticker": item["ticker"], "period": label,
                      "reason": "jawaban Claude ditolak: " + "; ".join(bad[:4]),
                      "fix": "minta Claude memperbaiki sesuai ATURAN WAJIB di file prompt, simpan ulang dengan nama yang sama"})
        st.move(ans["id"], used, folder, new_name="DITOLAK_" + ans["name"])
    if not expired:
        return "wait"
    activity.add(f"{item['ticker']} {label}: narasi Claude tidak tersedia dalam batas waktu; narasi otomatis dikirim.", "info")
    return "fallback"
