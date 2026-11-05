"""Fact registry: setiap angka di narasi dibuat lewat fungsi di sini, sehingga tercatat sumber & cara hitungnya.

Checker dua lapis sebelum publikasi:
  1. fakta bersumber tabel dicocokkan kembali ke tabel (toleransi = 1/2 digit terakhir yang ditampilkan)
  2. teks dipindai: setiap angka uang/%/bps harus terdaftar sebagai fakta (tidak ada "angka liar")
Pelanggaran keras -> laporan ditahan (lihat narrator.py).
"""
import re


class NM(Exception):
    """Nilai tidak tersedia / tidak bermakna: kalimat yang memakainya dilewati."""


def idn(x: float, dec: int) -> str:
    """Format Indonesia: titik ribuan, koma desimal."""
    s = f"{abs(x):,.{dec}f}"
    return s.replace(",", "§").replace(".", ",").replace("§", ".")


# label baku hierarki laba (aturan integritas label)
PROFIT_LABELS = {
    "np_total": "laba bersih total",
    "net_profit_reported": "laba bersih atribusi induk",
    "np_disc": "laba operasi yang dihentikan",
}


class Facts:
    def __init__(self, currency: str, table: dict):
        self.cur, self.t, self.items = currency, table, []
        has_disc = bool(table["m"].get("net_profit_reported"))
        self.labels = dict(PROFIT_LABELS)
        self.labels["net_profit"] = "laba bersih dari operasi berkelanjutan" if has_disc else "laba bersih atribusi induk"

    def _reg(self, text, value, src, how, tol, label=None):
        self.items.append({"text": text, "value": value, "src": src, "how": how, "tol": tol, "label": label})
        return text

    # ---------------------------------------------------------------- pembentuk angka
    def money(self, v, src=None, how="value"):
        """Nilai absolut (arah ditulis dengan kata kerja). src = (kunci baris, indeks kolom)."""
        if v is None:
            raise NM()
        base, sym, small, big = (1e9, "Rp", "miliar", "triliun") if self.cur == "IDR" else (1e6, self.cur, "juta", "miliar")
        x = round(abs(v) / base, 1)
        if x >= 1000:                       # ambang triliun diterapkan setelah pembulatan
            txt, tol = f"{sym}{idn(x / 1000, 2)} {big}", 0.005 * 1000 * base
        else:
            txt, tol = f"{sym}{idn(x, 1)} {small}", 0.05 * base
        return self._reg(txt, abs(v), src, how, tol)

    def pct(self, g, src=None, how="growth", signed=False):
        if g is None:
            raise NM()
        txt = (("+" if g >= 0 else "-") if signed else "") + f"{idn(abs(g) * 100, 1)}%"
        return self._reg(txt, abs(g), src, how, 0.0005)

    def level(self, r, src=None):
        if r is None:
            raise NM()
        return self._reg(f"{idn(r * 100, 1)}%", r, src, "ratio", 0.0005)

    def bps(self, d, src=None, signed=True):
        if d is None:
            raise NM()
        sign = ("+" if d >= 0 else "-") if signed else ""
        return self._reg(f"{sign}{idn(round(abs(d) * 10000), 0)} bps", abs(d), src, "bps", 0.5)

    def share(self, x, src=None):
        if x is None:
            raise NM()
        return self._reg(f"{idn(abs(x) * 100, 0)}%", abs(x), src, "share", 0.005)

    def label(self, key):
        return self.labels.get(key, key)


MONEY_RE = re.compile(r"(?:Rp|USD)\s?-?\d[\d.,]*\s(?:miliar|triliun|juta)")
PCT_RE = re.compile(r"[+-]?\d[\d.,]*%")
BPS_RE = re.compile(r"[+-]?\d[\d.]*\sbps")


def check(paragraphs: list[str], facts: Facts) -> list[str]:
    """Kembalikan daftar pelanggaran keras (kosong = lolos)."""
    text = "\n".join(p for p in paragraphs if p)
    bad = []
    if re.search(r"(?:Rp|USD)\s?-\d", text):
        bad.append("format nominal negatif (Rp-xx)")
    known = {f["text"] for f in facts.items}
    for rx in (MONEY_RE, PCT_RE, BPS_RE):
        for mt in rx.findall(text):
            if mt not in known:
                bad.append(f"angka tidak terdaftar: {mt}")
    m = facts.t["m"]
    for f in facts.items:
        if f["how"] != "value" or not f["src"]:
            continue
        key, col = f["src"]
        row = m.get(key)
        tv = None if row is None else row["vals"][col]
        if tv is None or abs(abs(tv) - f["value"]) > f["tol"]:
            bad.append(f"angka {f['text']} tidak cocok dengan tabel ({key})")
    if "n.m." in text:
        bad.append("narasi memuat n.m.")
    return sorted(set(bad))
