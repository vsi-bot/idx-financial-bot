"""Narasi semi-manual lewat chat Claude (langganan Team, tanpa API).

Alur:
1. Bot menulis file prompt ke _Sistem/untuk_claude/KODE_3Q26.md (tabel, daftar angka resmi, temuan, kandidat katalis).
2. Anda membuka chat Claude (konektor Google Drive aktif) dan mengetik: "proses antrean narasi".
   Claude membaca file prompt dan menyimpan jawabannya ke _Sistem/dari_claude/KODE_3Q26_narasi.md.
3. Bot membaca jawaban, memeriksanya (angka harus persis dari daftar resmi, kata terlarang, katalis wajib link),
   lalu mengirim ke Discord. Bila jawaban tidak masuk dalam batas waktu, narasi otomatis yang dikirim.
"""
import re

from core import config as cfg

CM = cfg.SETTINGS.get("claude_manual", {})


def enabled_for(ticker: str) -> bool:
    t = [x.upper() for x in (CM.get("tickers") or [])]
    return "ALL" in t or ticker.upper() in t


def _table_md(table: dict) -> str:
    cols = table["cols"]
    lines = [f"| Item | {cols[0]} | {cols[1]} | YoY | {cols[2]} | {cols[3]} | {cols[4]} | QoQ | YoY |",
             "|---|---|---|---|---|---|---|---|---|"]
    div = 1e9 if table.get("decimals", 0) == 0 else 1e6
    for r in table["rows"]:
        if r["kind"] in ("section", "subsection"):
            lines.append(f"| **{r.get('label_en', r['label'])}** | | | | | | | | |")
            continue
        if r["fmt"] == "pct":
            v = ["" if x is None else f"{x * 100:.2f}%" for x in r["vals"]]
            g = ["", "", ""]
        elif r["fmt"] in ("x", "days"):
            v = ["" if x is None else f"{x:.2f}" for x in r["vals"]]
            g = ["", "", ""]
        else:
            v = ["-" if x is None else f"{x / div:,.1f}" for x in r["vals"]]
            g = ["n.m." if x is None else f"{x * 100:+.1f}%" for x in r["growth"]]
        ind = "&nbsp;&nbsp;" if r["kind"] == "comp" else ""
        lines.append(f"| {ind}{r['label']} | {v[0]} | {v[1]} | {g[0]} | {v[2]} | {v[3]} | {v[4]} | {g[1]} | {g[2]} |")
    return "\n".join(lines)


def build_prompt(ticker, company, P, table, facts, rule_title, rule_paras, catalyst_items, banned) -> str:
    allowed = []
    for f in facts.items:
        if f["text"] not in [a.split(" ← ")[0] for a in allowed]:
            src = f"{f['src'][0]} kolom {f['src'][1]}" if f.get("src") else f["how"]
            allowed.append(f"{f['text']} ← {src}")
    bank = table["template"].startswith("bank")
    cats = "\n".join(f"- {c['title']} ({c['source']}, {c['date']:%d/%m/%Y}) {c['link']}" for c in catalyst_items) or "- (tidak ada)"
    return f"""# TUGAS NARASI: {ticker} {P.label} ({company})

Tulis narasi analisis laporan keuangan untuk investor & nasabah dalam Bahasa Indonesia baku, gaya riset ekuitas
profesional, luwes, tidak kaku, dan mudah dipahami dalam 5 menit.

## ATURAN WAJIB (jawaban yang melanggar akan ditolak otomatis)
1. Setiap angka (nominal, %, bps) HANYA boleh diambil dari DAFTAR ANGKA RESMI dan ditulis PERSIS sama.
   Jangan membulatkan ulang, menghitung angka baru, atau memakai minus untuk nominal (tulis "turun"/"berbalik").
2. Perubahan margin/rasio dalam bps. Rasio berubah < 25 bps disebut "relatif stabil".
3. Kata positif (solid, kuat) hanya untuk pendapatan/laba/aset produktif; untuk biaya pakai kata netral.
4. Jangan menulis kata: "searah", "n.m."{', "laba kotor", "GPM", "beban pokok"' if bank else ''}, serta: {", ".join(banned)}.
5. Jangan berspekulasi soal penyebab yang tidak ada di data; tulis "perlu dicek di catatan laporan".
6. Label laba mengikuti tabel (hierarki: laba total → atribusi induk → operasi berkelanjutan).
7. Katalis positif: boleh mencari di web; MAKSIMAL 3 butir; setiap butir WAJIB ada link sumber (https://) dan tanggal.
   Hanya katalis yang benar-benar ada sumbernya. Angka di bagian katalis boleh dari sumber berita.

## FORMAT JAWABAN
Simpan sebagai file baru di Google Drive folder `_Sistem/{CM.get('answer_folder', 'dari_claude')}` dengan nama
`{ticker}_{P.label}_narasi.md`, isi:

# <judul bertema, maks. 12 kata>

**Ringkasan {table['cols'][1]}**
• **Kinerja:** ...
• **Profitabilitas:** ...
• **Perlu dicermati:** ...

<3–6 paragraf analisis>

## Katalis Positif
- <katalis> (sumber, dd/mm/yyyy) https://...

<paragraf "Hal yang perlu dicermati pada {P.next().label}: (1) ...; (2) ...">

## DATA TABEL
{_table_md(table)}

## DAFTAR ANGKA RESMI (hanya ini yang boleh dipakai)
{chr(10).join("- " + a for a in allowed)}

## TEMUAN ANALISIS BOT (bahan, boleh disusun ulang dengan bahasa sendiri)
Judul usulan: {rule_title}

{chr(10).join(rule_paras)}

## KANDIDAT KATALIS DARI BERITA (boleh dipakai/ditambah dari pencarian web)
{cats}
"""


def parse_answer(text: str):
    """Kembalikan (judul, paragraf, blok_katalis)."""
    text = text.replace("\r\n", "\n").strip()
    m = re.search(r"^#\s+(.+)$", text, re.M)
    title = m.group(1).strip() if m else None
    body = text[m.end():].strip() if m else text
    cat_block = None
    cm = re.search(r"^##\s*Katalis Positif\s*$(.*?)(?=^[^\-\s]|\Z)", body, re.M | re.S | re.I)
    if cm:
        cat_block = cm.group(1).strip()
        body = (body[:cm.start()] + body[cm.end():]).strip()
    paras = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    return title, paras, cat_block


def check_answer(title, paras, cat_block, allowed: set[str], template: str) -> list[str]:
    from bot3_narrator.facts import BPS_RE, MONEY_RE, PCT_RE
    bad = []
    if not title:
        bad.append("judul tidak ada (baris pertama harus '# Judul')")
    text = "\n".join([title or ""] + paras)
    if re.search(r"(?:Rp|USD)\s?-\d", text, re.I):
        bad.append("nominal bertanda minus")
    for rx in (MONEY_RE, PCT_RE, BPS_RE):
        for mt in rx.findall(text):
            if mt not in allowed:
                bad.append(f"angka di luar daftar resmi: {mt}")
    if re.search(r"\bsearah\b", text, re.I) or "n.m." in text:
        bad.append("kata terlarang (searah / n.m.)")
    if template.startswith("bank") and re.search(r"laba kotor|\bGPM\b|beban pokok", text, re.I):
        bad.append("istilah nonbank di narasi bank")
    if cat_block:
        items = [l for l in cat_block.splitlines() if l.strip().startswith("-")]
        if len(items) > 3:
            bad.append("katalis lebih dari 3 butir")
        for l in items:
            if not re.search(r"https?://\S+", l):
                bad.append(f"katalis tanpa link sumber: {l[:60]}")
    return sorted(set(bad))
