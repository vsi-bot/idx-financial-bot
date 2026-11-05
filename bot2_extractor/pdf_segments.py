"""Pembaca tabel segmen operasi dari PDF laporan keuangan (tanpa AI, tanpa token).

Cara kerja:
1. Cari halaman berjudul "SEGMEN OPERASI" / "OPERATING SEGMENTS" yang berisi tabel angka.
2. Ambil posisi kata (x, y) dengan pdfplumber, kelompokkan menjadi baris.
3. Temukan baris "Pendapatan"/"Revenue" dan "Laba periode berjalan"/"Profit"; posisi angkanya
   menentukan kolom segmen. Nama segmen diambil dari teks judul kolom di atasnya.
4. Hasil diuji silang: kolom konsolidasi harus sama dengan pendapatan di Excel.
Jika tata letak tidak dikenali, fungsi mengembalikan None dan bot menandai "perlu dicek manual".
"""
import logging
import re

log = logging.getLogger(__name__)

NUM_RE = re.compile(r"^\(?-?\d{1,3}(,\d{3})*(\.\d+)?\)?$|^-$")
DATE_RE = re.compile(r"(31\s*Maret|30\s*Juni|30\s*September|31\s*Desember|March\s*31|June\s*30|"
                     r"September\s*30|December\s*31|31\s*March|30\s*June|31\s*December)[^\d]{0,14}(20\d\d)", re.I)
MONTH_OF = {"maret": 3, "march": 3, "juni": 6, "june": 6, "september": 9, "desember": 12, "december": 12}
REV_LABEL = re.compile(r"^(pendapatan( usaha| bersih)?|penjualan( bersih)?|revenue[s]?|"
                       r"hak bagi hasil milik bank|pendapatan bunga( -)? bersih|net interest income)$", re.I)
PROFIT_LABEL = re.compile(r"^(laba|laba \(rugi\)|laba/\(rugi\))( bersih)?( (periode|tahun) berjalan)?$|"
                          r"^profit (\(loss\) )?for the (period|year)$|^net income$", re.I)
TOTAL_WORDS = ("konsolidasi", "consolidated", "jumlah", "total")
ELIM_ALSO = ("penyesuaian", "adjustment")
ELIM_WORDS = ("eliminasi", "elimination")


def _to_num(t: str):
    if t == "-":
        return 0.0
    neg = t.startswith("(")
    v = float(t.strip("()").replace(",", ""))
    return -v if neg else v


def _lines(words, tol=2.5):
    lines = []
    for w in sorted(words, key=lambda w: (round(w["top"]), w["x0"])):
        if lines and abs(lines[-1]["top"] - w["top"]) <= tol:
            lines[-1]["words"].append(w)
        else:
            lines.append({"top": w["top"], "words": [w]})
    for ln in lines:
        ln["words"].sort(key=lambda w: w["x0"])
    return lines


def _split_row(line):
    """Pisahkan label (kiri) dan angka-angka; label bahasa Inggris di kanan diabaikan."""
    label, nums = [], []
    for w in line["words"]:
        if NUM_RE.match(w["text"]):
            nums.append(w)
        elif not nums:
            label.append(w["text"])
    return " ".join(label).strip(), nums


def _month(datestr: str) -> int:
    for k, v in MONTH_OF.items():
        if k in datestr.lower():
            return v
    return 0


def _parse_table(lines, rev_i):
    _, rev_nums = _split_row(lines[rev_i])
    cols = [w["x1"] for w in rev_nums]                      # angka rata kanan
    left_bound = min(w["x0"] for w in rev_nums) - 60

    # baris tanggal periode berada di atas judul kolom
    year = month = None
    start = 0
    for j in range(rev_i - 1, max(rev_i - 30, -1), -1):
        txt = " ".join(w["text"] for w in lines[j]["words"])
        m = DATE_RE.search(txt)
        if m:
            year, month, start = int(m.group(2)), _month(m.group(1)), j + 1
            break
    if year is None:
        return None
    headers = [[] for _ in cols]
    label_x = lines[rev_i]["words"][0]["x0"]
    for ln in lines[start:rev_i]:
        if _split_row(ln)[1]:                                # baris berangka bukan judul
            continue
        first = ln["words"][0]
        if first["x0"] <= label_x + 3 and first["text"].lower() not in ("keterangan", "description", "uraian"):
            break                                            # label baris data: judul kolom selesai
        for w in ln["words"]:
            cx = (w["x0"] + w["x1"]) / 2
            if cx < left_bound or cx > cols[-1] + 15:
                continue
            k = next((c for c in range(len(cols)) if cx <= cols[c] + 10), len(cols) - 1)
            headers[k].append(w["text"])
    names, names_en = [], []
    for h in headers:
        txt = re.sub(r"\s+", " ", " ".join(h)).strip()
        id_part, _, en_part = txt.partition("/")
        id_part = id_part.strip() or "Lainnya"
        en_part = re.sub(r"[*)]+$", "", en_part.strip()).strip() or id_part
        names.append(id_part)
        names_en.append(en_part)

    profit = None
    for ln in lines[rev_i + 1:]:
        label, nums = _split_row(ln)
        if REV_LABEL.match(label) and len(nums) >= 3:        # tabel berikutnya dimulai
            break
        if PROFIT_LABEL.match(label) and len(nums) == len(cols):
            profit = [_to_num(w["text"]) for w in nums]
            break
    return {"year": year, "month": month, "names": names, "names_en": names_en,
            "revenue": [_to_num(w["text"]) for w in rev_nums], "profit": profit}


def _parse_page(page) -> list[dict]:
    lines = _lines(page.extract_words(use_text_flow=False))
    tables = []
    for i, ln in enumerate(lines):
        label, nums = _split_row(ln)
        if len(nums) >= 3 and REV_LABEL.match(label):
            t = _parse_table(lines, i)
            if t:
                tables.append(t)
    return tables


def _scale(table, check):
    """Cocokkan kolom total dengan angka Excel; kembalikan faktor skala atau None."""
    names = table["names"]
    total_idx = next((k for k, n in enumerate(names) if any(w in n.lower() for w in TOTAL_WORDS)), len(names) - 1)
    pdf_total = table["revenue"][total_idx]
    if not check or not pdf_total:
        return None
    ratio = check / pdf_total
    return next((s for s in (1, 1e3, 1e6, 1e9) if abs(ratio / s - 1) < 0.005), None)


def extract_segments(pdf_path, period_year: int, end_month: int, check_cur: float | None,
                     check_prior: float | None = None) -> dict | None:
    """Kembalikan {"cur": {nama: {"revenue": x, "profit": y}}, "prior": {...}} dalam satuan penuh.

    check_cur/check_prior: angka pembanding dari Excel (pendapatan; untuk bank: pendapatan inti bersih)
    untuk memastikan tabel yang terbaca adalah tabel yang benar.
    """
    import pdfplumber
    from pypdf import PdfReader

    try:
        reader = PdfReader(str(pdf_path))
        if reader.is_encrypted:          # banyak LK hanya dikunci izin cetak/salin, tetap bisa dibaca
            reader.decrypt("")
        n = len(reader.pages)
    except Exception as e:  # PDF rusak / tidak bisa dibaca
        log.warning("PDF tidak bisa dibaca: %s", e)
        return None

    # Catatan segmen hampir selalu berada di bagian akhir laporan, jadi halaman dibaca dari
    # belakang dan pembacaan BERHENTI begitu tabel tahun berjalan & pembanding ditemukan.
    picked, checked = {}, 0
    with pdfplumber.open(str(pdf_path)) as pdf:
        for i in range(n - 1, -1, -1):
            try:
                t = (reader.pages[i].extract_text() or "").upper()
            except Exception:
                continue
            if not (("SEGMEN OPERASI" in t or "OPERATING SEGMENT" in t or "INFORMASI SEGMEN" in t)
                    and "PENDAPATAN" in t):
                if picked and checked >= 3:
                    break                    # sudah melewati blok catatan segmen
                continue
            checked += 1
            try:
                found = _parse_page(pdf.pages[i])
            except Exception as e:
                log.debug("Halaman %s gagal diparse: %s", i, e)
                continue
            for tb in found:
                if tb["month"] != end_month or tb["year"] in picked:
                    continue
                check = check_cur if tb["year"] == period_year else check_prior if tb["year"] == period_year - 1 else None
                sc = _scale(tb, check)
                if sc:
                    picked[tb["year"]] = (tb, sc)
            if period_year in picked and period_year - 1 in picked:
                break                        # kedua tabel lengkap: berhenti membaca
            if checked >= 12:
                break
    if period_year not in picked:
        return None

    out = {}
    for side, yr in (("cur", period_year), ("prior", period_year - 1)):
        if yr not in picked:
            continue
        tb, scale = picked[yr]
        seg = {}
        for k, name in enumerate(tb["names"]):
            low = name.lower()
            if any(w in low for w in TOTAL_WORDS):
                continue
            key = "Eliminations" if any(w in low for w in ELIM_WORDS + ELIM_ALSO) else name
            seg[key] = {"revenue": tb["revenue"][k] * scale, "name_en": tb["names_en"][k],
                        "profit": tb["profit"][k] * scale if tb["profit"] else None}
        out[side] = seg
    return out
