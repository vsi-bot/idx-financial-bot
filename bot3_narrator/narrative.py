"""Mesin narasi berbasis aturan (tanpa AI, tanpa token).

Alur: angka dari model tabel -> temuan (findings) -> kalimat formal dengan beberapa variasi.
Variasi dipilih secara acak tapi konsisten per emiten & periode (seed), sehingga laporan
antar-emiten tidak seragam namun hasilnya bisa direproduksi.
"""
import random
import re

from core import config as cfg
from core.fmt import growth, money, safe_div
from core.periods import Period

TH = cfg.ANALYSIS


# ------------------------------------------------------------------ util bahasa
def p1(x):
    return f"{abs(x) * 100:.1f}%"


def bps_txt(x):
    return f"{abs(round(x * 10000)):,} bps"


def lower_first(s):
    return s[:1].lower() + s[1:] if s and not s[:2].isupper() else s


class W:
    """Penulis kalimat dengan pemilih variasi."""

    def __init__(self, seed: str, currency: str):
        self.r = random.Random(seed)
        self.cur = currency

    def pick(self, *opts):
        return self.r.choice(opts)

    def m(self, v):
        return money(v, self.cur)

    def up(self):
        return self.pick("tumbuh", "meningkat", "naik")

    def down(self):
        return self.pick("turun", "terkoreksi", "melemah")

    def move(self, g, suffix="YoY"):
        """'tumbuh 9.7% YoY' / 'turun 3.1% YoY' / 'relatif stabil (+0.4% YoY)'."""
        if g is None:
            return None
        if abs(g) * 100 < TH["flat_band_pct"] / 3:
            return f"relatif stabil ({g * 100:+.1f}% {suffix})"
        return f"{self.up() if g > 0 else self.down()} {p1(g)} {suffix}"

    def to(self, g, v, cost=False, suffix="YoY"):
        """'tumbuh 9.7% YoY menjadi Rp...' / 'relatif stabil di level Rp... (-0.8% YoY)'."""
        if g is None:
            return f"tercatat sebesar {self.m(v)}"
        if abs(g) * 100 < TH["flat_band_pct"] / 3:
            return f"relatif stabil di level {self.m(v)} ({g * 100:+.1f}% {suffix})"
        if cost:
            verb = self.pick("naik", "meningkat") if g > 0 else self.pick("turun", "menurun")
        else:
            verb = self.up() if g > 0 else self.down()
        return f"{verb} {p1(g)} {suffix} menjadi {self.m(v)}"

    def sym(self):
        return "Rp" if self.cur == "IDR" else self.cur


def comp_name(name):
    n = name.strip()
    low = n.lower()
    if low in ("lain-lain", "lainnya"):
        return "pendapatan lain-lain"
    if low.startswith(("penjualan", "pendapatan")):
        return lower_first(n)
    return "pendapatan " + lower_first(n)


def cost_name(name):
    n = name.strip()
    low = n.lower()
    if low.startswith(("beban", "biaya", "depresiasi", "amortisasi", "material", "bahan", "royalti", "tenaga")):
        return lower_first(n)
    return "biaya " + lower_first(n)


def join_and(items):
    items = [i for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " dan " + items[-1]


# ------------------------------------------------------------------ akses metrik
class M:
    def __init__(self, table):
        self.m = table["m"]
        self.rows = table["rows"]

    def v(self, key, i=1):
        e = self.m.get(key)
        return None if e is None else e["vals"][i]

    def g(self, key, i=0):
        e = self.m.get(key)
        return None if e is None else e["growth"][i]

    def comps(self, prefix, include_other=False):
        out = []
        for r in self.rows:
            k = r.get("key") or ""
            if not include_other and r["label"].lower().startswith("lainnya"):
                continue
            if r["kind"] == "comp" and k.startswith(prefix + "::") and not k.endswith("_residual"):
                out.append(r)
        return out


# ------------------------------------------------------------------ narasi utama
def build(table: dict, store: dict, note: str | None = None) -> tuple[str, list[str]]:
    p: Period = table["period"]
    tk = store["ticker"]
    w = W(f"{tk}-{p.key}", store["currency"])
    M_ = M(table)
    if table["template"] == "general":
        title, paras, watch = _general(w, M_, table, store, p)
    else:
        title, paras, watch = _bank(w, M_, table, store, p)
    paras.insert(0, _summary(w, M_, table, p, watch))
    if note:
        paras.insert(-1, note.strip())
    return title, [_polish(x) for x in paras if x]


def _polish(text: str) -> str:
    from core.sysfiles import style_replacements
    for bad, good in style_replacements().items():
        text = re.sub(re.escape(bad), good, text, flags=re.I)
    text = re.sub(r"[ \t]{2,}", " ", text).replace(" ,", ",").replace(" .", ".")
    return text.strip()


def _labels(p: Period):
    L = p.q_label if p.idx == 0 else p.cum_label
    L_py = p.same_last_year().q_label if p.idx == 0 else p.same_last_year().cum_label
    return L, L_py


def _quarter_sentence(w, M_, key, name, p: Period):
    if p.idx == 0:
        return None
    q, qoq, yoy = M_.v(key, 4), M_.g(key, 1), M_.g(key, 2)
    if q is None:
        return None
    parts = [f"{qoq * 100:+.1f}% QoQ" if qoq is not None else None, f"{yoy * 100:+.1f}% YoY" if yoy is not None else None]
    tail = f" ({'; '.join(x for x in parts if x)})" if any(parts) else ""
    return w.pick(f"Secara kuartalan, {name} {p.q_label} tercatat sebesar {w.m(q)}{tail}.",
                  f"Pada kuartal {p.q_label} saja, {name} mencapai {w.m(q)}{tail}.",
                  f"Untuk {p.q_label} secara terpisah, {name} tercatat {w.m(q)}{tail}.")


# ================================================================== INDUSTRI UMUM
def _general(w: W, M_: M, table, store, p: Period):
    tk = store["ticker"]
    L, L_py = _labels(p)
    rev, rev_py, g_rev = M_.v("revenue"), M_.v("revenue", 0), M_.g("revenue")
    np_, g_np = M_.v("net_profit"), M_.g("net_profit")
    opm, opm_py = M_.v("r_operating_profit"), M_.v("r_operating_profit", 0)
    gpm, gpm_py = M_.v("r_gross_profit"), M_.v("r_gross_profit", 0)
    etr, etr_py = M_.v("r_etr"), M_.v("r_etr", 0)
    d_opm = None if None in (opm, opm_py) else opm - opm_py
    d_gpm = None if None in (gpm, gpm_py) else gpm - gpm_py
    d_etr = None if None in (etr, etr_py) else etr - etr_py
    tax_flag = d_etr is not None and d_etr * 100 >= TH["tax_jump_pp"]
    margin_bps = TH["margin_move_bps"] / 10000
    watch = []

    # ---------- judul
    if g_rev is None:
        a = "Kinerja Pendapatan"
    elif g_rev * 100 >= TH["strong_growth_pct"]:
        a = "Pertumbuhan Pendapatan Solid"
    elif g_rev * 100 >= TH["flat_band_pct"]:
        a = "Pendapatan Tumbuh Moderat"
    elif g_rev * 100 > -TH["flat_band_pct"]:
        a = "Pendapatan Relatif Stabil"
    else:
        a = "Pendapatan Terkoreksi"
    og = M_.g("operating_profit")
    if tax_flag and g_np is not None and og is not None and g_np < og - 0.05:
        b = "Laba Bersih Tertahan Kenaikan Beban Pajak"
    elif d_opm is not None and d_opm >= margin_bps:
        b = "Profitabilitas Membaik"
    elif d_opm is not None and d_opm <= -margin_bps:
        b = "Tekanan Margin Menjadi Perhatian"
    else:
        b = "Profitabilitas Terjaga"
    title = f"{tk} {p.label}: {a}, {b}"

    # ---------- P1 headline
    mv = w.move(g_rev)
    s1 = w.pick(
        f"{tk} membukukan pendapatan sebesar {w.m(rev)} pada {L}, {mv} dari {w.m(rev_py)} pada {L_py}.",
        f"Pada {L}, {tk} mencatatkan pendapatan sebesar {w.m(rev)}, {mv} dibandingkan {L_py}.",
        f"Pendapatan {tk} pada {L} tercatat sebesar {w.m(rev)}, {mv} dari {w.m(rev_py)} pada periode yang sama tahun lalu.",
    ) if mv else f"{tk} membukukan pendapatan sebesar {w.m(rev)} pada {L}."
    s2 = _quarter_sentence(w, M_, "revenue", "pendapatan", p)
    s3 = None
    if np_ is not None:
        npmv = w.to(g_np, np_) if g_np is not None else (f"berbalik menjadi laba sebesar {w.m(np_)}" if np_ > 0
                                                          else f"mencatatkan rugi sebesar {w.m(abs(np_))}")
        rel = ""
        if g_np is not None and g_rev is not None:
            if g_np > g_rev + 0.02:
                rel = w.pick(", tumbuh lebih cepat dibandingkan pendapatan", ", melampaui laju pertumbuhan pendapatan")
            elif g_np < g_rev - 0.02:
                rel = w.pick(", lebih lambat dibandingkan laju pertumbuhan pendapatan",
                             ", tertinggal dari laju pertumbuhan pendapatan")
        s3 = (f"Di level bottom-line, laba bersih yang dapat diatribusikan kepada pemilik entitas induk "
              f"{npmv}{rel}.")
    verdict = _verdict(w, g_rev, g_np, L)
    para1 = " ".join(x for x in (s1, s2, s3, verdict) if x)

    # ---------- P2 komposisi pendapatan
    para2 = _composition(w, M_, rev, rev_py, L_py, watch)

    # ---------- P3 biaya
    para3 = _costs_general(w, M_, g_rev, rev, rev_py, p, watch)

    # ---------- P4 profitabilitas
    s = []
    gp, g_gp = M_.v("gross_profit"), M_.g("gross_profit")
    if gp is not None and gpm is not None:
        dir_ = ("membaik" if d_gpm and d_gpm > 0 else "menyempit") if d_gpm is not None else ""
        s.append(f"Laba kotor {w.to(g_gp, gp)}, dengan gross profit margin {dir_} "
                 f"menjadi {gpm * 100:.1f}% (vs. {gpm_py * 100:.1f}% pada {L_py}; "
                 f"{'+' if d_gpm >= 0 else '-'}{bps_txt(d_gpm)})." if d_gpm is not None else
                 f"Laba kotor tercatat sebesar {w.m(gp)} dengan gross profit margin {gpm * 100:.1f}%.")
        if d_gpm is not None and abs(d_gpm) >= margin_bps:
            watch.append("arah gross profit margin setelah " + ("perbaikan" if d_gpm > 0 else "penurunan") +
                         f" {bps_txt(d_gpm)} pada {L}")
    op, g_op = M_.v("operating_profit"), M_.g("operating_profit")
    if op is not None and opm is not None and d_opm is not None:
        s.append(w.pick(
            f"Laba operasional {w.to(g_op, op)}, sehingga operating profit margin "
            f"{'meningkat' if d_opm > 0 else 'turun'} {bps_txt(d_opm)} menjadi {opm * 100:.1f}%.",
            f"Di level operasional, laba {w.to(g_op, op)} dengan operating profit margin "
            f"sebesar {opm * 100:.1f}% ({'+' if d_opm >= 0 else '-'}{bps_txt(d_opm)} YoY)."))
    d_rev = None if None in (rev, rev_py) else rev - rev_py
    if d_rev and d_rev > 0 and M_.v("gross_profit", 0) is not None and np_ is not None and M_.v("net_profit", 0) is not None:
        inc_gp = (gp - M_.v("gross_profit", 0)) / d_rev * 100
        inc_np = (np_ - M_.v("net_profit", 0)) / d_rev * 100
        s.append(f"Secara inkremental, setiap tambahan {w.sym()}100 pendapatan menghasilkan sekitar "
                 f"{w.sym()}{inc_gp:.0f} laba kotor dan {w.sym()}{inc_np:.0f} laba bersih.")
    if tax_flag:
        s.append(f"Di bawah level operasional, beban pajak {w.to(M_.g('tax'), M_.v('tax'), True)}, sehingga tarif pajak efektif naik menjadi {etr * 100:.1f}% dari "
                 f"{etr_py * 100:.1f}%, yang menahan pertumbuhan laba bersih.")
        watch.append("normalisasi tarif pajak efektif")
    conv, conv_py = safe_div(np_, op), safe_div(M_.v("net_profit", 0), M_.v("operating_profit", 0))
    if conv is not None and conv_py is not None and abs(conv - conv_py) >= 0.05 and not tax_flag:
        s.append(f"Rasio konversi laba operasional menjadi laba bersih {'meningkat' if conv > conv_py else 'menurun'} "
                 f"menjadi {conv * 100:.1f}% dari {conv_py * 100:.1f}%, yang mengindikasikan "
                 f"{'berkurangnya' if conv > conv_py else 'meningkatnya'} beban di bawah level operasional.")
        if conv < conv_py:
            watch.append("pos-pos di bawah laba operasional yang menekan konversi laba")
    npm, npm_py = M_.v("r_net_profit"), M_.v("r_net_profit", 0)
    # NPM sudah tercantum di Ringkasan
    if p.idx > 0 and M_.v("r_gross_profit", 4) is not None and M_.v("r_gross_profit", 3) is not None:
        qg, qg_prev = M_.v("r_gross_profit", 4), M_.v("r_gross_profit", 3)
        dq = qg - qg_prev
        if abs(dq) >= margin_bps:
            s.append(f"Secara kuartalan, gross profit margin {p.q_label} {'meningkat' if dq > 0 else 'turun'} "
                     f"{bps_txt(dq)} QoQ menjadi {qg * 100:.1f}%.")
    para4 = " ".join(s)

    # ---------- P5 kesimpulan
    para5 = _conclusion_general(w, g_rev, d_opm, tax_flag, L, M_)

    # ---------- P6 hal yang dicermati
    para6 = _watch(watch, p)
    return title, [para1, para2, para3, para4, para5, para6], watch


def _verdict(w, g_rev, g_np, L):
    if g_rev is None or g_np is None:
        return None
    strong = TH["strong_growth_pct"] / 100
    if g_rev >= strong and g_np >= strong:
        return w.pick(f"Secara umum, kinerja {L} tergolong solid dengan pertumbuhan di level pendapatan maupun laba bersih.",
                      f"Kinerja {L} tergolong solid, tercermin dari pertumbuhan dua digit di level pendapatan dan laba bersih.")
    if g_rev > 0 and g_np > 0:
        return w.pick(f"Secara umum, kinerja {L} menunjukkan pertumbuhan yang moderat.",
                      f"Kinerja {L} secara umum tumbuh moderat di level pendapatan maupun laba bersih.")
    if g_rev > 0 >= g_np:
        return "Dengan demikian, pertumbuhan pendapatan belum tercermin di level laba bersih."
    if g_rev <= 0 < g_np:
        return "Laba bersih tetap tumbuh di tengah pendapatan yang melemah, ditopang oleh perbaikan profitabilitas."
    return f"Secara umum, kinerja {L} melemah di level pendapatan maupun laba bersih."


def _composition(w, M_, rev, rev_py, L_py, watch):
    comps = M_.comps("rev_seg")
    s = []
    if len(comps) >= 2 and rev:
        main = max(comps, key=lambda r: r["vals"][1] or 0)
        sh = (main["vals"][1] or 0) / rev
        sh_py = safe_div(main["vals"][0], rev_py)
        g = main["growth"][0]
        txt = (f"Dari sisi komposisi, {comp_name(main['label'])} tetap menjadi kontributor utama dengan nilai "
               f"{w.m(main['vals'][1])}")
        txt += f" ({w.move(g)})" if g is not None else ""
        txt += f", atau {sh * 100:.1f}% dari total pendapatan"
        txt += f" (vs. {sh_py * 100:.1f}% pada {L_py})." if sh_py else "."
        s.append(txt)
        d_tot = None if None in (rev, rev_py) else rev - rev_py
        if d_tot:
            deltas = [(r, (r["vals"][1] or 0) - (r["vals"][0] or 0)) for r in comps]
            same = [x for x in deltas if (x[1] > 0) == (d_tot > 0) and x[1] != 0]
            if same:
                drv, dv = max(same, key=lambda x: abs(x[1]))
                share = dv / d_tot * 100
                if drv is not main and 0 < share <= 300:
                    s.append(f"{'Pertumbuhan' if d_tot > 0 else 'Penurunan'} pendapatan terutama "
                             f"{'ditopang oleh' if d_tot > 0 else 'berasal dari'} {comp_name(drv['label'])}, "
                             f"yang menyumbang sekitar {share:.0f}% dari {'kenaikan' if d_tot > 0 else 'penurunan'} "
                             f"pendapatan.")
        for r in comps:
            if (r["vals"][0] in (None, 0)) and (r["vals"][1] or 0) > 0:
                s.append(f"Di sisi lain, {comp_name(r['label'])} mulai berkontribusi sebesar {w.m(r['vals'][1])} "
                         f"atau {r['vals'][1] / rev * 100:.1f}% dari total pendapatan.")
                watch.append(f"kontribusi lanjutan {comp_name(r['label'])}")
        weak = [r for r in comps if r is not main and r["growth"][0] is not None and r["growth"][0] < -0.05]
        if weak:
            s.append("Sebaliknya, " + join_and([f"{comp_name(r['label'])} turun {p1(r['growth'][0])} YoY" for r in weak[:2]]) + ".")
    # laba per segmen (dari PDF)
    seg_p = [r for r in M_.comps("seg_profit") if not r["label"].lower().startswith(("eliminasi", "elimination"))]
    if seg_p:
        top = max(seg_p, key=lambda r: r["vals"][1] or 0)
        txt = (f"Berdasarkan informasi segmen, segmen {top['label']} membukukan laba sebesar {w.m(top['vals'][1])}"
               + (f" ({w.move(top['growth'][0])})" if top["growth"][0] is not None else "") + ".")
        losers = [r for r in seg_p if (r["vals"][1] or 0) < 0]
        if losers:
            txt += " Sementara itu, " + join_and(
                [f"segmen {r['label']} mencatatkan rugi sebesar {w.m(abs(r['vals'][1]))}" for r in losers]) + "."
            for r in losers:
                watch.append(f"kinerja segmen {r['label']} yang masih mencatatkan rugi")
        s.append(txt)
    return " ".join(s) if s else None


def _costs_general(w, M_, g_rev, rev, rev_py, p, watch):
    s = []
    cogs, g_c = M_.v("cogs"), M_.g("cogs")
    if cogs is not None and g_c is not None:
        rel = ""
        if g_rev is not None:
            rel = (", lebih rendah dibandingkan pertumbuhan pendapatan" if g_c < g_rev - 0.01 else
                   ", lebih tinggi dibandingkan pertumbuhan pendapatan" if g_c > g_rev + 0.01 else
                   ", sejalan dengan pertumbuhan pendapatan")
        s.append(w.pick(f"Dari sisi biaya, beban pokok pendapatan {w.to(g_c, cogs, True)}{rel}.",
                        f"Beban pokok pendapatan {w.to(g_c, cogs, True)}{rel}."))
        comps = [r for r in M_.comps("cogs_comp") if r["vals"][0] is not None and r["vals"][1] is not None]
        base = M_.v("cogs", 0) or 0
        deltas = sorted(((r, r["vals"][1] - r["vals"][0]) for r in comps), key=lambda x: x[1])
        ups = [x for x in reversed(deltas) if x[1] > 0 and base and x[1] / base >= 0.005][:2]
        downs = [x for x in deltas if x[1] < 0 and base and abs(x[1]) / base >= 0.005][:1]
        parts = []
        if ups:
            parts.append(("Kenaikan tersebut terutama berasal dari " if g_c > 0 else "Di dalamnya, kenaikan terjadi pada ")
                         + join_and([f"{cost_name(r['label'])} ({r['growth'][0] * 100:+.1f}% YoY)" for r, _ in ups
                                     if r["growth"][0] is not None]))
        if downs:
            r, _ = downs[0]
            if r["growth"][0] is not None:
                parts.append(f"sementara {cost_name(r['label'])} turun {p1(r['growth'][0])} YoY")
        if parts:
            s.append(", ".join(parts) + ".")
        for r, dv in ups[:1]:
            if r["growth"][0] is not None and r["growth"][0] > 0.25:
                watch.append(f"tren {cost_name(r['label'])} yang tumbuh {p1(r['growth'][0])} YoY")
    opex, g_o = M_.v("opex"), M_.g("opex")
    ro, ro_py = M_.v("r_opex"), M_.v("r_opex", 0)
    if opex is not None and g_o is not None and ro is not None and ro_py is not None and abs(ro - ro_py) >= 0.005:
        s.append(f"Beban usaha {w.to(g_o, opex, True)}, dengan rasio terhadap pendapatan "
                 f"{'meningkat' if ro > ro_py else 'menurun'} menjadi {ro * 100:.1f}% (vs. {ro_py * 100:.1f}%).")
    if p.idx > 0:
        rq, rq_prev = M_.v("revenue", 4), M_.v("revenue", 3)
        gq, gq_prev = M_.v("gross_profit", 4), M_.v("gross_profit", 3)
        if None not in (rq, rq_prev, gq, gq_prev) and rq - rq_prev > 0:
            inc = (gq - gq_prev) / (rq - rq_prev)
            gpm_prev = safe_div(gq_prev, rq_prev) or 0
            if inc < gpm_prev * 0.5:
                s.append(f"Secara kuartalan, tambahan pendapatan sebesar {w.m(rq - rq_prev)} dibandingkan "
                         f"{p.prev().q_label} hanya menghasilkan tambahan laba kotor sebesar {w.m(gq - gq_prev)}, "
                         f"yang menunjukkan bahwa sebagian besar kenaikan pendapatan diimbangi oleh kenaikan beban pokok.")
    return " ".join(s) if s else None


def _conclusion_general(w, g_rev, d_opm, tax_flag, L, M_):
    mb = TH["margin_move_bps"] / 10000
    if g_rev is None:
        return None
    up = g_rev > 0
    if up and d_opm is not None and d_opm >= mb:
        core = w.pick(f"kinerja {L} mencerminkan pertumbuhan yang berkualitas, dengan kenaikan pendapatan yang diikuti "
                      f"perbaikan profitabilitas operasional",
                      f"hasil {L} menunjukkan pertumbuhan yang disertai perbaikan profitabilitas operasional")
    elif up and d_opm is not None and d_opm <= -mb:
        core = f"pertumbuhan pendapatan pada {L} belum diikuti oleh profitabilitas, seiring margin operasional yang menyempit"
    elif not up and d_opm is not None and d_opm >= mb:
        core = f"perbaikan efisiensi menopang profitabilitas di tengah pendapatan yang melemah pada {L}"
    elif not up and d_opm is not None and d_opm <= -mb:
        core = f"kinerja {L} tertekan baik dari sisi pendapatan maupun margin"
    else:
        core = f"kinerja {L} relatif sejalan dengan pergerakan pendapatan, dengan profitabilitas operasional yang terjaga"
    extra = ""
    if tax_flag:
        extra = (" Meski demikian, kenaikan tarif pajak efektif menjadi faktor yang menahan laju laba bersih, "
                 "sehingga keberlanjutannya perlu dipantau.")
    return f"Secara keseluruhan, {core}.{extra}"


def _watch(items, p: Period):
    items = list(dict.fromkeys(items))[:4]
    if not items:
        return None
    nxt = p.next().label
    numbered = [f"({i}) {t}" for i, t in enumerate(items, 1)]
    return f"Hal yang perlu dicermati pada {nxt}: " + join_and(numbered) + "."


# ================================================================== BANK
def _bank(w: W, M_: M, table, store, p: Period):
    tk = store["ticker"]
    sharia = table["template"] == "bank_sharia"
    L, L_py = _labels(p)
    core = "pendapatan margin dan bagi hasil bersih" if sharia else "pendapatan bunga bersih (NII)"
    loan = "pembiayaan" if sharia else "kredit"
    ldr = "FDR" if sharia else "LDR"
    watch = []

    np_, g_np = M_.v("net_profit"), M_.g("net_profit")
    nii, g_nii = M_.v("nii"), M_.g("nii")
    ppop, g_ppop = M_.v("ppop"), M_.g("ppop")
    prov, prov_py, g_prov = M_.v("provision"), M_.v("provision", 0), M_.g("provision")
    op, op_py = M_.v("operating_profit"), M_.v("operating_profit", 0)

    # judul
    a = ("Laba Bersih Tumbuh Solid" if (g_np or 0) * 100 >= TH["strong_growth_pct"] else
         "Laba Bersih Tumbuh Moderat" if (g_np or 0) * 100 >= TH["flat_band_pct"] else
         "Laba Bersih Relatif Stabil" if (g_np or 0) * 100 > -TH["flat_band_pct"] else "Laba Bersih Terkoreksi")
    prov_share = None
    if None not in (prov, prov_py, op, op_py) and op - op_py > 0:
        prov_share = (prov_py - prov) / (op - op_py)
    b = ("Ditopang Penurunan Provisi" if prov_share is not None and prov_share >= 0.3 else
         f"Didukung Pertumbuhan {'Pendapatan Inti' if (g_nii or 0) > 0 else 'Non-Inti'}")
    title = f"{tk} {p.label}: {a}, {b}"

    # P1
    s = [w.pick(f"{tk} membukukan laba bersih sebesar {w.m(np_)} pada {L}, {w.move(g_np)}.",
                f"Pada {L}, laba bersih {tk} tercatat sebesar {w.m(np_)}, {w.move(g_np)}.",
                f"Laba bersih {tk} pada {L} mencapai {w.m(np_)}, {w.move(g_np)} dari {w.m(M_.v('net_profit', 0))} pada {L_py}.")]
    if nii is not None:
        s.append(f"Di sisi pendapatan inti, {core} {w.to(g_nii, nii)}.")
    q = _quarter_sentence(w, M_, "net_profit", "laba bersih", p)
    if q:
        s.append(q)
    para1 = " ".join(x for x in s if x)

    # P2 komposisi pendapatan
    s = []
    inc = [r for r in M_.comps("core_inc_comp") if r["vals"][0] is not None and r["vals"][1] is not None]
    if inc:
        deltas = sorted(((r, r["vals"][1] - r["vals"][0]) for r in inc), key=lambda x: x[1])
        up_ = [x for x in reversed(deltas) if x[1] > 0][:1]
        dn_ = [x for x in deltas if x[1] < 0][:1]
        g_ci = M_.g("core_income")
        txt = f"Pendapatan {'margin dan bagi hasil' if sharia else 'bunga'} {w.to(g_ci, M_.v('core_income'))}"
        det = []
        if up_ and up_[0][0]["growth"][0] is not None:
            det.append(f"kenaikan {lower_first(up_[0][0]['label'])} ({up_[0][0]['growth'][0] * 100:+.1f}% YoY)")
        if dn_ and dn_[0][0]["growth"][0] is not None:
            det.append(f"penurunan {lower_first(dn_[0][0]['label'])} ({dn_[0][0]['growth'][0] * 100:+.1f}% YoY)")
        s.append(txt + (", dengan " + join_and(det) if det else "") + ".")
    cc, g_cc = M_.v("core_cost"), M_.g("core_cost")
    if cc is not None and g_cc is not None:
        lbl = "hak pihak ketiga atas bagi hasil" if sharia else "beban bunga"
        s.append(f"Di sisi biaya dana, {lbl} {w.to(g_cc, cc, True)}"
                 + (", sehingga mendukung pertumbuhan pendapatan inti bersih." if g_cc < (M_.g('core_income') or 0)
                    else ", lebih cepat dari pertumbuhan pendapatan inti."))
        if g_cc > (M_.g("core_income") or 0):
            watch.append("tren biaya dana terhadap imbal hasil " + loan)
    nc, g_nc = M_.v("noncore_income"), M_.g("noncore_income")
    if nc is not None and g_nc is not None:
        comps = [r for r in M_.comps("noncore_comp") if r["vals"][0] is not None and r["vals"][1] is not None]
        drv = max(comps, key=lambda r: abs(r["vals"][1] - r["vals"][0]), default=None)
        txt = f"Pendapatan non-inti {w.to(g_nc, nc)}"
        if drv and drv["growth"][0] is not None:
            txt += f", terutama dipengaruhi oleh {lower_first(drv['label'])} ({drv['growth'][0] * 100:+.1f}% YoY)"
        s.append(txt + ".")
    para2 = " ".join(s) if s else None

    # P3 biaya operasional & provisi
    s = []
    ox, g_ox = M_.v("opex"), M_.g("opex")
    cir, cir_py = M_.v("r_cir"), M_.v("r_cir", 0)
    if ox is not None and g_ox is not None:
        txt = f"Beban operasional {w.to(g_ox, ox, True)}"
        if cir is not None and cir_py is not None:
            txt += (f", sehingga cost to income ratio {'meningkat' if cir > cir_py else 'membaik'} menjadi "
                    f"{cir * 100:.1f}% (vs. {cir_py * 100:.1f}%)")
            if cir - cir_py > 0.02:
                watch.append("disiplin biaya operasional (CIR)")
        s.append(txt + ".")
    if ppop is not None:
        s.append(f"Dengan demikian, laba operasional sebelum pencadangan (PPOP) {w.to(g_ppop, ppop)}.")
    if prov is not None and g_prov is not None:
        coc = M_.v("r_coc")
        txt = f"Beban pencadangan {w.to(g_prov, prov, True)}"
        if coc is not None:
            txt += f", dengan cost of credit (disetahunkan) sebesar {coc * 100:.2f}%"
        s.append(txt + ".")
    if prov_share is not None and prov_share >= 0.3:
        s.append(f"Penurunan beban pencadangan menyumbang sekitar {prov_share * 100:.0f}% dari kenaikan laba operasional, "
                 f"sehingga sebagian pertumbuhan laba ditopang oleh faktor provisi, bukan sepenuhnya oleh bisnis inti.")
        watch.append("keberlanjutan beban pencadangan yang rendah")
    para3 = " ".join(s) if s else None

    # P4 neraca
    s = []
    lv, dv = M_.v("bs_loans"), M_.v("bs_deposits")
    ytd_l, ytd_d = table["m"].get("ytd_loans"), table["m"].get("ytd_deposits")
    if lv:
        txt = f"Dari sisi neraca, {loan} tercatat sebesar {w.m(lv)}"
        g = [f"{x * 100:+.1f}% {lbl}" for x, lbl in ((ytd_l, "YtD"), (M_.g("bs_loans", 2), "YoY")) if x is not None]
        s.append(txt + (f" ({'; '.join(g)})" if g else "") + ".")
    if dv:
        txt = f"Dana pihak ketiga mencapai {w.m(dv)}"
        g = [f"{x * 100:+.1f}% {lbl}" for x, lbl in ((ytd_d, "YtD"), (M_.g("bs_deposits", 2), "YoY")) if x is not None]
        txt += f" ({'; '.join(g)})" if g else ""
        casa, ld = M_.v("r_casa"), M_.v("r_ldr")
        if casa is not None:
            txt += f", dengan CASA ratio sebesar {casa * 100:.1f}%"
        if ld is not None:
            txt += f" dan {ldr} sebesar {ld * 100:.1f}%"
        s.append(txt + ".")
    seg = [r for r in M_.comps("seg_profit") if not r["label"].lower().startswith(("eliminasi", "penyesuaian", "elimination", "adjustment"))]
    if seg and np_:
        top = max(seg, key=lambda r: r["vals"][1] or 0)
        s.append(f"Berdasarkan informasi segmen, segmen {top['label']} menjadi kontributor laba terbesar dengan "
                 f"{w.m(top['vals'][1])} ({(top['vals'][1] or 0) / np_ * 100:.0f}% dari laba bersih)"
                 + (f", {w.move(top['growth'][0])}" if top["growth"][0] is not None else "") + ".")
    para4 = " ".join(s) if s else None

    # P5 kesimpulan
    if prov_share is not None and prov_share >= 0.3:
        c = (f"Secara keseluruhan, pertumbuhan laba {tk} pada {L} lebih banyak ditopang oleh penurunan beban pencadangan, "
             f"sementara pertumbuhan pendapatan inti bersih relatif {'terbatas' if (g_nii or 0) < 0.05 else 'moderat'}. "
             f"Kualitas pertumbuhan laba ke depan akan bergantung pada akselerasi pendapatan inti dan terjaganya kualitas aset.")
    elif (g_nii or 0) > 0 and (g_np or 0) > 0:
        c = (f"Secara keseluruhan, kinerja {L} didukung oleh pertumbuhan pendapatan inti yang diikuti oleh kenaikan laba, "
             f"mencerminkan kualitas pertumbuhan yang baik.")
    else:
        c = f"Secara keseluruhan, kinerja {L} menunjukkan tekanan pada pendapatan inti yang perlu dicermati lebih lanjut."
    watch = watch + [f"kualitas aset dan arah {ldr}"]
    para6 = _watch(watch, p)
    return title, [para1, para2, para3, para4, c, para6], watch


# ================================================================== RINGKASAN INVESTOR
def _pp(x):
    return "n.a." if x is None else f"{x * 100:.1f}%"


def _chg(cur, prev):
    if cur is None or prev is None:
        return ""
    d = round((cur - prev) * 10000)
    return f" ({d:+,} bps)"


def _summary(w: W, M_: M, table, p: Period, watch) -> str:
    """Empat poin inti yang bisa dibaca dalam 30 detik."""
    L, _ = _labels(p)
    g = lambda k, i=0: M_.g(k, i)  # noqa: E731
    f = lambda x: "n.m." if x is None else f"{x * 100:+.1f}%"  # noqa: E731
    lines = []
    q_txt = ""
    if p.idx > 0 and g("net_profit", 1) is not None:
        q_txt = f"; laba {p.q_label} {f(g('net_profit', 1))} QoQ"
    if table["template"] == "general":
        lines.append(f"**Kinerja:** pendapatan {w.m(M_.v('revenue'))} ({f(g('revenue'))} YoY), "
                     f"laba bersih {w.m(M_.v('net_profit'))} ({f(g('net_profit'))} YoY){q_txt}.")
        lines.append(f"**Profitabilitas:** GPM {_pp(M_.v('r_gross_profit'))}{_chg(M_.v('r_gross_profit'), M_.v('r_gross_profit', 0))}, "
                     f"OPM {_pp(M_.v('r_operating_profit'))}{_chg(M_.v('r_operating_profit'), M_.v('r_operating_profit', 0))}, "
                     f"NPM {_pp(M_.v('r_net_profit'))}, ROE {_pp(M_.v('r_roe'))}.")
        comps = [r for r in M_.comps("rev_seg") if r["vals"][0] is not None and r["vals"][1] is not None]
        rev_d = (M_.v("revenue") or 0) - (M_.v("revenue", 0) or 0)
        if comps and rev_d:
            drv = max(comps, key=lambda r: ((r["vals"][1] - r["vals"][0]) * (1 if rev_d > 0 else -1)))
            lines.append(f"**Pendorong utama:** {lower_first(drv['label'])} ({f(drv['growth'][0])} YoY), "
                         f"porsi {drv['vals'][1] / M_.v('revenue') * 100:.0f}% dari pendapatan.")
        nde = M_.v("r_nde")
        if nde is not None:
            lines.append(f"**Neraca:** {'posisi kas bersih' if nde < 0 else 'utang bersih terhadap ekuitas'} "
                         f"{abs(nde) * 100:.0f}% dari ekuitas.")
    else:
        lines.append(f"**Kinerja:** laba bersih {w.m(M_.v('net_profit'))} ({f(g('net_profit'))} YoY){q_txt}; "
                     f"pendapatan inti bersih {f(g('nii'))} YoY.")
        lines.append(f"**Profitabilitas & efisiensi:** ROE {_pp(M_.v('r_roe'))}, CIR {_pp(M_.v('r_cir'))}"
                     f"{_chg(M_.v('r_cir'), M_.v('r_cir', 0))}, cost of credit {_pp(M_.v('r_coc'))}.")
        yl, yd = table["m"].get("ytd_loans"), table["m"].get("ytd_deposits")
        lines.append(f"**Neraca:** {'pembiayaan' if table['template'] == 'bank_sharia' else 'kredit'} "
                     f"{f(yl)} YtD, DPK {f(yd)} YtD, CASA {_pp(M_.v('r_casa'))}, "
                     f"{'FDR' if table['template'] == 'bank_sharia' else 'LDR'} {_pp(M_.v('r_ldr'))}.")
    if watch:
        prio = ("pajak", "pencadangan", "rugi", "margin", "biaya", "cir")
        top = min(watch, key=lambda t: next((i for i, k in enumerate(prio) if k in t.lower()), len(prio)))
        lines.append(f"**Perlu dicermati:** {top}.")
    return "**Ringkasan " + L + "**\n" + "\n".join("• " + x for x in lines)
