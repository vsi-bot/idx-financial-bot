"""Mesin narasi v3 (berbasis aturan, berbasis fakta). Semua angka lewat Facts; semua arah lewat direction()."""
import random
import re

from core import config as cfg
from core.periods import Period
from bot3_narrator.facts import NM, Facts

N = cfg.SETTINGS.get("narrative", {})
ONEOFF = cfg.SETTINGS.get("oneoff", {})


# ------------------------------------------------------------------ penentu arah (satu sumber)
def direction(g=None, d_margin=None):
    """'naik' / 'stabil' / 'turun' untuk pertumbuhan nominal (g) atau perubahan margin (d_margin)."""
    if d_margin is not None:
        lim = N.get("margin_flat_bps", 50) / 10000
        return "stabil" if abs(d_margin) < lim else ("naik" if d_margin > 0 else "turun")
    if g is None:
        return None
    return "stabil" if abs(g) * 100 < N.get("flat_pct", 1) else ("naik" if g > 0 else "turun")


def band(g):
    a = abs(g) * 100
    return "stabil" if a < N.get("flat_pct", 1) else "tipis" if a < N.get("thin_pct", 5) else \
        "solid" if a < N.get("solid_pct", 15) else "kuat"


VERB = {("naik", "tipis"): "naik tipis", ("naik", "solid"): "tumbuh solid", ("naik", "kuat"): "tumbuh kuat",
        ("turun", "tipis"): "turun tipis", ("turun", "solid"): "turun", ("turun", "kuat"): "turun signifikan"}


class Ctx:
    def __init__(self, table, store, wl):
        self.t, self.m, self.s = table, table["m"], store
        self.p: Period = table["period"]
        self.F = Facts(store["currency"], table)
        self.r = random.Random(f"{store['ticker']}-{self.p.key}")
        self.seasonal = bool((wl or {}).get("musiman"))
        self.L = self.p.q_label if self.p.idx == 0 else self.p.cum_label
        self.Lpy = self.p.same_last_year().q_label if self.p.idx == 0 else self.p.same_last_year().cum_label
        self.watch = []                       # (prioritas, materialitas, teks)

    def v(self, k, i=1):
        e = self.m.get(k)
        return None if e is None else e["vals"][i]

    def g(self, k, i=0):
        e = self.m.get(k)
        return None if e is None else e["growth"][i]

    def pick(self, *o):
        return self.r.choice(o)

    def comps(self, prefix):
        return [r for r in self.t["rows"] if r["kind"] == "comp" and (r.get("key") or "").startswith(prefix + "::")]

    def rev_base(self):
        return abs(self.v("revenue") or self.v("total_op_income") or 0) or 1


def say(fn):
    try:
        return fn()
    except (NM, TypeError, ZeroDivisionError):
        return None


# ------------------------------------------------------------------ frasa pergerakan (sadar arah & tanda)
def movement(c: Ctx, key, i_cur=1, i_base=0, gi=0, suffix="YoY", label_profit=False):
    """Frasa pergerakan sebuah pos, aman untuk rugi/berbalik/n.m."""
    F, cur, base = c.F, c.v(key, i_cur), c.v(key, i_base)
    if cur is None:
        raise NM()
    if base is not None and (base < 0 or cur < 0):
        if base < 0 and cur < 0:
            arah = "menyempit" if abs(cur) < abs(base) else "melebar"
            return f"mencatatkan rugi yang {arah} dari {F.money(base, (key, i_base))} menjadi {F.money(cur, (key, i_cur))}"
        if base < 0 <= cur:
            return f"berbalik dari rugi {F.money(base, (key, i_base))} menjadi laba {F.money(cur, (key, i_cur))}"
        return f"berbalik dari laba {F.money(base, (key, i_base))} menjadi rugi {F.money(cur, (key, i_cur))}"
    g = c.g(key, gi)
    if g is None:
        return f"tercatat sebesar {F.money(cur, (key, i_cur))}"
    b = band(g)
    if b == "stabil":
        return f"relatif stabil di {F.money(cur, (key, i_cur))} ({F.pct(g, signed=True)} {suffix})"
    return f"{VERB[(direction(g), b)]} {F.pct(g)} {suffix} menjadi {F.money(cur, (key, i_cur))}"


def quarter_clause(c: Ctx, key, noun):
    """Kalimat kuartalan dengan aturan efek basis & musiman."""
    p, F = c.p, c.F
    if p.idx == 0 or c.v(key, 4) is None:
        return None
    qoq, yoy = c.g(key, 1), c.g(key, 2)
    parts = []
    lim = N.get("base_effect_qoq_pct", 30) / 100
    yoy_txt = f"{F.pct(yoy, signed=True)} YoY" if yoy is not None else None
    qoq_txt = f"{F.pct(qoq, signed=True)} QoQ" if qoq is not None else None
    order = [yoy_txt, qoq_txt] if c.seasonal else [qoq_txt, yoy_txt]
    parts = [x for x in order if x]
    txt = f"Secara kuartalan, {noun} {p.q_label} tercatat {F.money(c.v(key, 4), (key, 4))}"
    txt += f" ({'; '.join(parts)})" if parts else ""
    if qoq is not None and abs(qoq) > lim:
        txt += (", sehingga perbandingan kuartalan kemungkinan dipengaruhi efek basis" if yoy is not None
                else ", sementara pembanding tahunan tidak tersedia pada basis yang sebanding")
    return txt + "."


# ------------------------------------------------------------------ atribusi penggerak (nominal)
def drivers(c: Ctx, prefix, total_key, noun):
    F = c.F
    tot_cur, tot_base = c.v(total_key), c.v(total_key, 0)
    if tot_cur is None or tot_base is None:
        return None
    d_tot = tot_cur - tot_base
    comps = [(r, (r["vals"][1] or 0) - (r["vals"][0] or 0)) for r in c.comps(prefix)
             if r["vals"][1] is not None]
    if not comps:
        return None
    if abs(d_tot) < abs(tot_base) * N.get("flat_pct", 1) / 100:
        return f"Pergerakan komponen {noun} saling mengimbangi, sehingga totalnya relatif tidak berubah."
    same = [(r, d) for r, d in comps if d and (d > 0) == (d_tot > 0)]
    S = sum(d for _, d in same)
    if not same or not S:
        return None
    same.sort(key=lambda x: -abs(x[1]))
    word = "kenaikan" if d_tot > 0 else "penurunan"

    def nm(r):
        return "pos lain-lain yang tidak dirinci di laporan" if r["label"].lower().startswith("lainnya") \
            else r["label"][:1].lower() + r["label"][1:]
    r0, d0 = same[0]
    if d0 / S > N.get("driver_main_share", 0.5):
        txt = (f"{word.capitalize()} {noun} terutama berasal dari {nm(r0)} sebesar {F.money(d0, how='delta')}, "
               f"atau {F.share(d0 / S)} dari total {word} searah")
        g0 = r0["growth"][0]
        if g0 is not None and abs(r0["vals"][1] or 0) >= c.rev_base() * N.get("immaterial_pct", 1) / 100 \
                and not r0["round_note"]:
            txt += f" ({F.pct(g0, signed=True)} YoY)"
        return txt + "."
    tops = same[:2]
    lst = " dan ".join(f"{nm(r)} ({F.share(d / S)})" for r, d in tops)
    return f"{word.capitalize()} {noun} tersebar, dengan kontributor terbesar {lst} dari total {word} searah."


# ------------------------------------------------------------------ pos sekali jadi & jembatan laba
def oneoffs(c: Ctx, prefix):
    F, out = c.F, []
    groups = set(ONEOFF.get("groups", []))
    skip = set((ONEOFF.get("exceptions") or {}).get(c.s["ticker"], []))
    pbt = abs(c.v("pbt") or 0)
    for r in c.comps(prefix):
        if r["label"] not in groups or r["label"] in skip:
            continue
        cur, py = r["vals"][1], r["vals"][0]
        if cur is None or not pbt:
            continue
        if abs(cur) >= N.get("oneoff_pbt_share", 0.1) * pbt and (py in (None, 0) or abs(cur) >= N.get("oneoff_jump", 1.5) * abs(py)):
            kind = "pendapatan" if cur > 0 else "beban"
            name = "pos lain-lain" if r["label"].lower().startswith("lainnya") else r["label"].lower()
            txt = f"{kind} dari {name} sebesar {F.money(cur, (r['key'], 1))}"
            out.append((abs(cur), txt))
    return out


def bridge(c: Ctx, bank=False):
    """Jembatan laba nominal; kembalikan (kalimat, kontributor) atau (None, [])."""
    F = c.F
    np_key = "net_profit"
    g_np = c.g(np_key)
    base_key = "ppop" if bank else "operating_profit"
    g_base = c.g(base_key)
    diverge = (g_np is None) != (g_base is None) or (
        g_np is not None and g_base is not None and abs(g_np - g_base) * 100 > N.get("bridge_gap_pp", 5))
    if not diverge:
        return None, []

    def d(k, sign=1):
        a, b = c.v(k), c.v(k, 0)
        return None if a is None or b is None else sign * (a - b)
    parts = [("laba operasional sebelum pencadangan" if bank else "laba operasional", d(base_key))]
    if bank:
        parts.append(("beban pencadangan", d("provision", -1)))
        parts.append(("pos operasional lainnya", d("other_op_net") if c.v("other_op_net") is not None else None))
    parts += [("pos non-operasi bersih", d("non_operating")), ("beban pajak", d("tax", -1)),
              ("kepentingan nonpengendali", d("np_nci", -1))]
    parts = [(n, x) for n, x in parts if x]
    target = d(np_key)
    if target is None or not parts:
        return None, []
    tol = max(abs(c.v(np_key) or 0) * 0.01, 0.1 * (1e9 if c.s["currency"] == "IDR" else 1e6))
    if abs(sum(x for _, x in parts) - target) > tol:
        raise ValueError("jembatan laba tidak menjumlah")
    others = sorted([p for p in parts if not p[0].startswith("laba operasional")], key=lambda x: -abs(x[1]))
    def desc(n, x):
        t = f"{n} yang {'menambah' if x > 0 else 'mengurangi'} laba sebesar {F.money(x, how='delta')}"
        a0, a1 = c.v("non_operating", 0), c.v("non_operating")
        if n == "pos non-operasi bersih" and a0 is not None and a1 is not None and (a0 < 0) != (a1 < 0):
            k = lambda v: "pendapatan bersih" if v > 0 else "beban bersih"  # noqa: E731
            t += f" (berbalik dari {k(a0)} {F.money(a0, ('non_operating', 0))} menjadi {k(a1)} {F.money(a1, ('non_operating', 1))})"
        return t
    items = [desc(n, x) for n, x in others[:3]]
    lst = items[0] if len(items) == 1 else ", ".join(items[:-1]) + " dan " + items[-1]
    gb = say(lambda: F.pct(g_base, signed=True)) or "tidak sebanding"
    gn = say(lambda: F.pct(g_np, signed=True)) or "tidak sebanding"
    txt = (f"Laju {c.F.label(np_key)} ({gn} YoY) berbeda dari "
           f"{'PPOP' if bank else 'laba operasional'} ({gb} YoY). Selisih tersebut dijelaskan oleh {lst}.")
    nonop_share = sum(abs(x) for n, x in others if n.startswith(("pos non-operasi", "pos operasional lainnya")))
    if others and nonop_share >= abs(others[0][1]) * 0.99 and nonop_share > 0:
        txt += " Karena kontributor terbesarnya berasal dari pos non-operasi, keberlanjutan laba ini perlu dicermati."
    elif bank and others and others[0][0] == "beban pencadangan":
        txt += " Pertumbuhan laba terutama ditopang penurunan beban pencadangan, bukan sepenuhnya dari bisnis inti."
    elif others and others[0][0] == "beban pajak" and others[0][1] < 0:
        txt += " Selisih terutama mencerminkan beban pajak yang lebih tinggi, bukan pelemahan kinerja operasional."
    else:
        txt += " Kontributor tersebut berasal dari pos yang lazim berulang, sehingga kualitas laba relatif terjaga."
    return txt, others


# ================================================================== NONBANK
def _general(c: Ctx):
    F, tk, L = c.F, c.s["ticker"], c.L
    paras = []
    # judul
    dr, dn = direction(c.g("revenue")), direction(c.g("net_profit"))
    d_opm = None if None in (c.v("r_operating_profit"), c.v("r_operating_profit", 0)) else \
        c.v("r_operating_profit") - c.v("r_operating_profit", 0)
    dm = direction(d_margin=d_opm) if d_opm is not None else None
    a = {"naik": f"Pendapatan Tumbuh {band(c.g('revenue')).capitalize()}" if c.g("revenue") else "Pendapatan Naik",
         "stabil": "Pendapatan Relatif Stabil", "turun": "Pendapatan Melemah", None: "Kinerja Pendapatan"}[dr]
    if dr == "naik" and band(c.g("revenue")) == "tipis":
        a = "Pendapatan Naik Tipis"
    b = {"naik": "Profitabilitas Membaik", "stabil": "Profitabilitas Terjaga", "turun": "Margin Tertekan",
         None: "Profitabilitas Perlu Dicermati"}[dm]
    title = f"{tk} {c.p.label}: {a}, {b}"

    # P1 headline
    s1 = say(lambda: f"{tk} membukukan pendapatan {L} yang {movement(c, 'revenue')}.")
    s2 = say(lambda: quarter_clause(c, "revenue", "pendapatan"))
    s3 = say(lambda: f"Di level bottom-line, {F.label('net_profit')} {movement(c, 'net_profit', label_profit=True)}.")
    rep = c.m.get("net_profit_reported")
    s4 = None
    if rep:
        s4 = say(lambda: (f"Angka tersebut tidak termasuk {F.label('np_disc')} sebesar {F.money(c.v('np_disc'), ('np_disc', 1))}; "
                          f"secara dilaporkan, {F.label('net_profit_reported')} {movement(c, 'net_profit_reported')}."))
    paras.append(" ".join(x for x in (s1, s2, s3, s4) if x))

    # P2 penggerak pendapatan + segmen
    p2 = [say(lambda: drivers(c, "rev_seg", "revenue", "pendapatan"))]
    new = [r for r in c.comps("rev_seg") if r["vals"][0] in (None, 0) and (r["vals"][1] or 0) > 0]
    for r in new[:1]:
        p2.append(say(lambda: f"Pendapatan {r['label'][:1].lower() + r['label'][1:]} mulai berkontribusi sebesar "
                              f"{F.money(r['vals'][1], (r['key'], 1))}, sehingga sebagian pertumbuhan kemungkinan bersifat anorganik."))
        c.watch.append((3, abs(r["vals"][1]), f"kontribusi lanjutan {r['label'].lower()}"))
    losers = [r for r in c.comps("seg_profit") if (r["vals"][1] or 0) < 0 and not r["label"].lower().startswith("lainnya")]
    for r in losers[:2]:
        p2.append(say(lambda: f"Dari sisi segmen, {r['label']} masih mencatatkan rugi sebesar {F.money(r['vals'][1], (r['key'], 1))}."))
        c.watch.append((2, abs(r["vals"][1]), f"kinerja segmen {r['label']} yang masih merugi"))
    paras.append(" ".join(x for x in p2 if x) or None)

    # P3 biaya
    p3 = [say(lambda: f"Dari sisi biaya, beban pokok pendapatan {movement(c, 'cogs')}."),
          say(lambda: drivers(c, "cogs_comp", "cogs", "beban pokok"))]
    ro, ro0 = c.m.get("r_opex", {}).get("vals", [None] * 5)[1], c.m.get("r_opex", {}).get("vals", [None] * 5)[0]
    if ro is not None and ro0 is not None and abs(ro - ro0) >= 0.005:
        p3.append(say(lambda: f"Beban usaha {movement(c, 'opex')}, dengan rasio terhadap pendapatan "
                              f"{'meningkat' if ro > ro0 else 'menurun'} ({F.bps(ro - ro0)})."))
    if c.p.idx > 0 and None not in (c.v("revenue", 4), c.v("revenue", 3), c.v("gross_profit", 4), c.v("gross_profit", 3)):
        dr_q = c.v("revenue", 4) - c.v("revenue", 3)
        dg_q = c.v("gross_profit", 4) - c.v("gross_profit", 3)
        if dr_q > 0 and dg_q <= 0:
            p3.append(say(lambda: f"Secara kuartalan, kenaikan pendapatan sebesar {F.money(dr_q, how='delta')} dibanding "
                                  f"{c.p.prev().q_label} sepenuhnya tergerus kenaikan beban pokok, sehingga laba kotor "
                                  f"{'turun' if dg_q < 0 else 'tidak bertambah'}."))
    paras.append(" ".join(x for x in p3 if x) or None)

    # P4 margin GPM -> OPM -> NPM
    p4, deltas = [], {}
    for k, nm in (("r_gross_profit", "gross profit margin"), ("r_operating_profit", "operating profit margin"),
                  ("r_net_profit", "net profit margin")):
        cur, base = c.v(k), c.v(k, 0)
        if cur is None or base is None:
            continue
        deltas[nm] = cur - base
        p4.append(f"{nm} {F.level(cur, (k, 1))} ({F.bps(cur - base)})")
    if p4:
        txt = "Dari sisi profitabilitas, " + ", ".join(p4) + f" dibanding {c.Lpy}."
        neg = {k: v for k, v in deltas.items() if v < 0}
        if neg:
            lvl = min(neg, key=neg.get)
            txt += f" Tekanan terbesar terjadi di level {lvl}."
        paras.append(txt)
    d_rev = None if None in (c.v("revenue"), c.v("revenue", 0)) else c.v("revenue") - c.v("revenue", 0)
    if d_rev and c.v("revenue", 0) and abs(d_rev) / abs(c.v("revenue", 0)) * 100 > N.get("incremental_min_pct", 2) and d_rev > 0:
        dgp = None if None in (c.v("gross_profit"), c.v("gross_profit", 0)) else c.v("gross_profit") - c.v("gross_profit", 0)
        if dgp is not None:
            if dgp <= 0:
                paras.append(f"Kenaikan pendapatan sebesar {F.money(d_rev, how='delta')} sepenuhnya tergerus oleh kenaikan beban pokok.")
            elif dgp / d_rev < 0.1:
                paras.append(f"Kenaikan pendapatan sebesar {F.money(d_rev, how='delta')} hampir sepenuhnya tergerus oleh kenaikan beban pokok.")
            else:
                paras.append(f"Secara inkremental, sekitar {F.share(dgp / d_rev)} dari tambahan pendapatan menjadi laba kotor.")

    # P5 jembatan laba + sekali jadi
    p5 = []
    try:
        btxt, _ = bridge(c)
    except ValueError:
        raise
    if btxt:
        p5.append(btxt)
    for amt, txt in oneoffs(c, "nonop_comp"):
        p5.append(f"Perlu dicermati, laba periode ini turut dipengaruhi {txt} yang berpotensi tidak berulang.")
        c.watch.append((1, amt, f"keberlanjutan {txt.split(' sebesar')[0]}"))
    etr, etr0 = c.v("r_etr"), c.v("r_etr", 0)
    if etr is not None and etr0 is not None and abs(etr - etr0) >= 0.03:
        c.watch.append((1, abs((c.v("tax") or 0) - (c.v("tax", 0) or 0)),
                        f"arah tarif pajak efektif yang bergerak dari {F.level(etr0, ('r_etr', 0))} menjadi {F.level(etr, ('r_etr', 1))}"))
    paras.append(" ".join(p5) or None)

    # P6 neraca
    nd, nd0 = c.v("bs_net_debt"), c.v("bs_net_debt", 0)
    if nd is not None:
        kind = "kas bersih" if nd < 0 else "utang bersih"
        if nd0 is not None and (nd0 < 0) != (nd < 0):
            k0 = "kas bersih" if nd0 < 0 else "utang bersih"
            paras.append(f"Posisi neraca berbalik dari {k0} {F.money(nd0, ('bs_net_debt', 0))} menjadi {kind} {F.money(nd, ('bs_net_debt', 1))}.")
        else:
            nde = c.v("r_nde")
            paras.append(f"Dari sisi neraca, perseroan berada pada posisi {kind} sebesar {F.money(nd, ('bs_net_debt', 1))}"
                         + (f" ({F.share(abs(nde))} dari ekuitas)." if nde is not None else "."))
    ta = c.g("bs_total_assets", 0)
    if ta is not None and ta > N.get("inorganic_asset_jump", 0.3):
        c.watch.append((3, 0, "kemungkinan kontribusi anorganik seiring kenaikan total aset yang signifikan"))

    # P7 kesimpulan dari matriks
    paras.append(conclusion(c, dr, dm, dn, bool(btxt)))
    # margin pressure watch
    if d_opm is not None and dm == "turun":
        c.watch.append((2, abs(d_opm) * abs(c.v("revenue") or 0), f"pemulihan operating profit margin setelah turun {F.bps(d_opm, signed=False)}"))
    return title, paras


# ================================================================== BANK
def _bank(c: Ctx):
    F, tk, L = c.F, c.s["ticker"], c.L
    sharia = c.t["template"] == "bank_sharia"
    loan, ldr = ("pembiayaan", "FDR") if sharia else ("kredit", "LDR")
    nii_lbl = "pendapatan margin dan bagi hasil bersih" if sharia else "pendapatan bunga bersih (NII)"
    dr, dn = direction(c.g("total_op_income")), direction(c.g("net_profit"))
    d_cir = None if None in (c.v("r_cir"), c.v("r_cir", 0)) else c.v("r_cir") - c.v("r_cir", 0)
    dm = direction(d_margin=-d_cir) if d_cir is not None else None        # CIR turun = efisiensi membaik
    title = f"{tk} {c.p.label}: " + {"naik": "Laba Bersih Tumbuh", "stabil": "Laba Bersih Relatif Stabil",
                                      "turun": "Laba Bersih Melemah", None: "Kinerja Laba"}[dn]
    paras = []
    p1 = [say(lambda: f"{tk} membukukan {F.label('net_profit')} {L} yang {movement(c, 'net_profit')}."),
          say(lambda: f"Pendapatan operasional bank (NII ditambah pendapatan non-bunga) {movement(c, 'total_op_income')}, "
                      f"dengan {nii_lbl} {movement(c, 'nii')}."),
          say(lambda: quarter_clause(c, "net_profit", "laba bersih"))]
    nim, nim0 = c.v("r_nim"), c.v("r_nim", 0)
    if nim is not None and nim0 is not None:
        p1.append(f"NIM (estimasi dari aset produktif) tercatat {F.level(nim, ('r_nim', 1))} ({F.bps(nim - nim0)} YoY).")
    paras.append(" ".join(x for x in p1 if x))
    paras.append(say(lambda: drivers(c, "noncore_comp", "noncore_income", "pendapatan non-bunga")))
    p3 = [say(lambda: f"Beban operasional {movement(c, 'opex')}" + (f", sehingga CIR {'meningkat' if d_cir > 0 else 'membaik'} "
                                                                    f"menjadi {F.level(c.v('r_cir'), ('r_cir', 1))} ({F.bps(d_cir)})." if d_cir is not None else ".")),
          say(lambda: f"Laba operasional sebelum pencadangan (PPOP) {movement(c, 'ppop')}."),
          say(lambda: f"Beban pencadangan {movement(c, 'provision')}" + (f", dengan cost of credit {F.level(c.v('r_coc'), ('r_coc', 1))}." if c.v('r_coc') is not None else "."))]
    paras.append(" ".join(x for x in p3 if x))
    btxt, others = bridge(c, bank=True)
    p4 = [btxt] if btxt else []
    for amt, txt in oneoffs(c, "noncore_comp"):
        p4.append(f"Perlu dicermati, pendapatan periode ini turut dipengaruhi {txt} yang berpotensi tidak berulang.")
        c.watch.append((3, amt, f"keberlanjutan {txt.split(' sebesar')[0]}"))
    if others and others[0][0] == "beban pencadangan":
        c.watch.append((2, abs(others[0][1]), "keberlanjutan beban pencadangan yang rendah"))
    paras.append(" ".join(p4) or None)
    p5 = [say(lambda: f"Dari sisi neraca, {loan} {movement(c, 'bs_loans', gi=2)}"
                      + (f" ({F.pct(c.m['ytd_loans'], signed=True)} YtD)" if c.m.get('ytd_loans') is not None else "") + "."),
          say(lambda: f"Dana pihak ketiga {movement(c, 'bs_deposits', gi=2)}, dengan CASA ratio {F.level(c.v('r_casa'), ('r_casa', 1))} "
                      f"dan {ldr} {F.level(c.v('r_ldr'), ('r_ldr', 1))}.")]
    paras.append(" ".join(x for x in p5 if x) or None)
    aq = [f"{lbl} {F.level(c.v(k), (k, 1))}" for k, lbl in (("p_npl", "NPL"), ("p_lar", "LAR"), ("p_coverage", "coverage NPL"),
                                                             ("p_car", "CAR")) if c.v(k) is not None]
    if aq:
        paras.append("Berdasarkan presentasi perusahaan, " + ", ".join(aq) + ".")
        npl, npl0 = c.v("p_npl"), c.v("p_npl", 0)
        if npl is not None and npl0 is not None and npl > npl0:
            c.watch.append((0, 1e30, f"kualitas aset seiring NPL naik {F.bps(npl - npl0, signed=False)} YoY"))
    gcc, gci = c.g("core_cost"), c.g("core_income")
    if gcc is not None and gci is not None and gcc > gci:
        c.watch.append((1, abs((c.v("core_cost") or 0) - (c.v("core_cost", 0) or 0)), "biaya dana yang tumbuh lebih cepat dari pendapatan inti"))
    if d_cir is not None and d_cir > 0.02:
        c.watch.append((3, 0, f"disiplin biaya operasional setelah CIR meningkat {F.bps(d_cir, signed=False)}"))
    paras.append(conclusion(c, dr, dm, dn, bool(btxt), bank=True))
    return title, paras


# ------------------------------------------------------------------ kesimpulan (matriks)
BASE = {("naik", "naik"): "pertumbuhan terjadi di level pendapatan maupun laba",
        ("naik", "stabil"): "pertumbuhan pendapatan belum diterjemahkan menjadi pertumbuhan laba",
        ("naik", "turun"): "pertumbuhan pendapatan tidak diikuti laba yang justru turun",
        ("stabil", "naik"): "laba tumbuh di tengah pendapatan yang relatif stabil",
        ("stabil", "stabil"): "kinerja relatif stabil di level pendapatan maupun laba",
        ("stabil", "turun"): "laba turun di tengah pendapatan yang relatif stabil",
        ("turun", "naik"): "laba tetap tumbuh meski pendapatan turun",
        ("turun", "stabil"): "laba relatif terjaga meski pendapatan turun",
        ("turun", "turun"): "kinerja melemah di level pendapatan maupun laba"}
MARGIN = {"naik": "dengan profitabilitas operasional yang membaik", "stabil": "dengan profitabilitas operasional yang terjaga",
          "turun": "dengan profitabilitas operasional yang menyempit"}


def conclusion(c: Ctx, dr, dm, dn, bridged, bank=False):
    if dr is None or dn is None:
        return f"Secara keseluruhan, sebagian pembanding {c.L} tidak tersedia pada basis yang sebanding, sehingga kesimpulan difokuskan pada level kinerja periode berjalan."
    txt = f"Secara keseluruhan, {BASE[(dr, dn)]}"
    if dm:
        txt += " " + ({"naik": "dengan efisiensi biaya yang membaik", "stabil": "dengan efisiensi biaya yang terjaga",
                        "turun": "dengan efisiensi biaya yang melemah"}[dm] if bank else MARGIN[dm])
    txt += "."
    op_dir = direction(c.g("ppop" if bank else "operating_profit"))
    if bridged and op_dir and op_dir != dn:
        txt += f" Arah laba bersih berbeda dengan arah {'PPOP' if bank else 'laba operasional'}, sehingga perubahan laba terutama berasal dari pos di bawahnya."
    return txt


# ------------------------------------------------------------------ ringkasan & watch items
def summary(c: Ctx, top_watch):
    F, bank = c.F, c.t["template"] != "general"
    L = c.L
    b = []
    if not bank:
        b.append(say(lambda: f"**Kinerja:** pendapatan {F.money(c.v('revenue'), ('revenue', 1))} ({F.pct(c.g('revenue'), signed=True)} YoY); "
                             f"{F.label('net_profit')} {F.money(c.v('net_profit'), ('net_profit', 1))}"
                             + (f" ({F.pct(c.g('net_profit'), signed=True)} YoY)." if c.g('net_profit') is not None else ".")))
        mg = [f"{nm} {F.level(c.v(k), (k, 1))}" + (f" ({F.bps(c.v(k) - c.v(k, 0))})" if c.v(k, 0) is not None else "")
              for k, nm in (("r_gross_profit", "GPM"), ("r_operating_profit", "OPM"), ("r_net_profit", "NPM")) if c.v(k) is not None]
        roe = say(lambda: f"ROE {F.level(c.v('r_roe'), ('r_roe', 1))}")
        b.append("**Profitabilitas:** " + ", ".join(mg + ([roe] if roe else [])) + "." if mg else None)
    else:
        b.append(say(lambda: f"**Kinerja:** {F.label('net_profit')} {F.money(c.v('net_profit'), ('net_profit', 1))} "
                             f"({F.pct(c.g('net_profit'), signed=True)} YoY); pendapatan operasional {F.pct(c.g('total_op_income'), signed=True)} YoY."))
        ef = [say(lambda: f"ROE {F.level(c.v('r_roe'), ('r_roe', 1))}"), say(lambda: f"CIR {F.level(c.v('r_cir'), ('r_cir', 1))}"),
              say(lambda: f"cost of credit {F.level(c.v('r_coc'), ('r_coc', 1))}"), say(lambda: f"NIM (est.) {F.level(c.v('r_nim'), ('r_nim', 1))}")]
        ef = [x for x in ef if x]
        b.append("**Profitabilitas & efisiensi:** " + ", ".join(ef) + "." if ef else None)
    if top_watch:
        b.append(f"**Perlu dicermati:** {top_watch}.")
    return f"**Ringkasan {L}**\n" + "\n".join("• " + x for x in b if x)


def watch_paragraph(c: Ctx, items):
    if not items:
        return None
    nxt = c.p.next().label
    return f"Hal yang perlu dicermati pada {nxt}: " + "; ".join(f"({i}) {t}" for i, t in enumerate(items, 1)) + "."


# ------------------------------------------------------------------ utama
def build(table, store, note=None, wl=None):
    c = Ctx(table, store, wl)
    title, paras = (_general if table["template"] == "general" else _bank)(c)
    for a in table.get("anomalies") or []:
        c.watch.append((2, 0, _register_numbers(c.F, a, convert=True)))
    ranked = sorted(dict(((t, (pr, -mat)) for pr, mat, t in c.watch)).items(), key=lambda x: x[1])
    items = [t for t, _ in ranked]
    top, rest = (items[0] if items else None), items[1:5]
    paras = [p for p in paras if p]
    if note:
        paras.append(_register_numbers(c.F, note.strip(), convert=False))
    paras.append(watch_paragraph(c, rest))
    paras.insert(0, summary(c, top))
    paras = [_polish(p) for p in paras if p]
    return title, paras, c.F


def _polish(text: str) -> str:
    from core.sysfiles import style_replacements
    for bad, good in style_replacements().items():
        text = re.sub(re.escape(bad), good, text, flags=re.I)
    text = re.sub(r"[ \t]{2,}", " ", text).replace(" ,", ",").replace(" .", ".").replace("..", ".")
    return text[:1].upper() + text[1:] if text else text


_PCT_EN = re.compile(r"([+-]?)(\d+(?:\.\d+)?)%")


def _register_numbers(F, text, convert=True):
    """Daftarkan angka dari teks luar (anomali / catatan analis) ke fact registry.
    convert=True: ubah persen format Inggris (12.3%) menjadi format Indonesia (12,3%)."""
    from bot3_narrator.facts import BPS_RE, MONEY_RE, PCT_RE
    if convert:
        text = _PCT_EN.sub(lambda m: F.pct(float(m.group(2)) / 100 * (-1 if m.group(1) == "-" else 1),
                                           how="anomaly", signed=bool(m.group(1))), text)
    for rx in (MONEY_RE, PCT_RE, BPS_RE):
        for mt in rx.findall(text):
            F._reg(mt, None, None, "external", 0)
    return text
