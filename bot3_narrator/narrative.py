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


EXP_KEYS = {"cogs", "opex", "provision", "core_cost", "tax", "fin_cost", "cf_capex", "selling_exp", "ga_exp"}
VERB_EXP = {("naik", "tipis"): "naik tipis", ("naik", "solid"): "naik", ("naik", "kuat"): "meningkat signifikan",
            ("turun", "tipis"): "turun tipis", ("turun", "solid"): "turun", ("turun", "kuat"): "turun signifikan"}
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
    verbs = VERB_EXP if key in EXP_KEYS else VERB
    return f"{verbs[(direction(g), b)]} {F.pct(g)} {suffix} menjadi {F.money(cur, (key, i_cur))}"


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
    """Penggerak nominal: kenaikan/penurunan bruto -> penyeimbang -> perubahan bersih (tanpa kata 'searah')."""
    F = c.F
    tot_cur, tot_base = c.v(total_key), c.v(total_key, 0)
    if tot_cur is None or tot_base is None:
        return None
    d_tot = tot_cur - tot_base
    comps = [(r, (r["vals"][1] or 0) - (r["vals"][0] or 0)) for r in c.comps(prefix) if r["vals"][1] is not None]
    if not comps:
        return None
    if abs(d_tot) < abs(tot_base) * N.get("flat_pct", 1) / 100:
        return f"Pergerakan komponen {noun} saling mengimbangi, sehingga totalnya relatif tidak berubah."
    same = sorted([(r, d) for r, d in comps if d and (d > 0) == (d_tot > 0)], key=lambda x: -abs(x[1]))
    S = sum(d for _, d in same)
    if not same or not S:
        return None
    opp = sorted([(r, d) for r, d in comps if d and (d > 0) != (d_tot > 0) and abs(d) >= abs(S) * 0.02],
                 key=lambda x: -abs(x[1]))[:3]
    up = d_tot > 0
    word, w2 = ("kenaikan", "penurunan") if up else ("penurunan", "kenaikan")

    def nm(r):
        return "pos lain-lain yang tidak dirinci di laporan" if r["label"].lower().startswith("lainnya") \
            else r["label"][:1].lower() + r["label"][1:]
    r0, d0 = same[0]
    if d0 / S > N.get("driver_main_share", 0.5):
        head = f"terutama berasal dari {nm(r0)} sebesar {F.money(d0, how='delta')} ({F.share(d0 / S)})"
    else:
        head = "terutama berasal dari " + _join([f"{nm(r)} ({F.share(d / S)})" for r, d in same[:2]])
    if opp:
        txt = (f"Secara bruto, {word} {noun} sebesar {F.money(S, how='delta')} {head}, diimbangi {w2} "
               + _join([f"{nm(r)} ({F.money(d, how='delta')})" for r, d in opp])
               + f", sehingga secara bersih {noun} {'naik' if up else 'turun'} {F.money(d_tot, how='delta')}.")
    else:
        txt = f"{word.capitalize()} {noun} sebesar {F.money(d_tot, how='delta')} {head}."
    if r0["label"].lower().startswith("lainnya") and d0 / S > 0.5:
        txt += " Rincian pos tersebut tidak tersedia di laporan dan perlu dicek di catatan laporan keuangan."
    return txt


# ------------------------------------------------------------------ pos sekali jadi & jembatan laba
def oneoffs(c: Ctx, prefix):
    """Pos berpotensi tidak berulang (one-off): hanya pos yang sifatnya teridentifikasi; pos lain-lain -> perlu dicek.
    Pos yang muncul berulang (kuartal ini, kuartal lalu, kuartal sama tahun lalu) atau tercatat rutin di koreksi
    analis tidak ditandai."""
    from core.sysfiles import corrections
    F, out = c.F, []
    groups = set(ONEOFF.get("groups", []))
    skip = set((ONEOFF.get("exceptions") or {}).get(c.s["ticker"], [])) | corrections().get(c.s["ticker"], set())
    pbt = abs(c.v("pbt") or 0)
    for r in c.comps(prefix):
        if r["label"] not in groups or r["label"] in skip:
            continue
        cur, py = r["vals"][1], r["vals"][0]
        if cur is None or not pbt:
            continue
        q = [r["vals"][i] for i in (2, 3, 4)]
        recurring = all(x is not None and x != 0 and (x > 0) == (cur > 0) for x in q)
        if recurring:
            continue
        if abs(cur) >= N.get("oneoff_pbt_share", 0.1) * pbt and (py in (None, 0) or abs(cur) >= N.get("oneoff_jump", 1.5) * abs(py)):
            kind = "pendapatan" if cur > 0 else "beban"
            if r["label"].lower().startswith("lainnya"):
                out.append((abs(cur), f"{kind} dari pos lain-lain sebesar {F.money(cur, (r['key'], 1))}", False))
            else:
                out.append((abs(cur), f"{kind} dari {r['label'].lower()} sebesar {F.money(cur, (r['key'], 1))}", True))
    return out


def bridge(c: Ctx, bank=False):
    """Jembatan laba nominal (aturan C5). Kembalikan (kalimat, kontributor) atau (None, [])."""
    F = c.F
    g_np, base_key = c.g("net_profit"), ("ppop" if bank else "operating_profit")
    g_base = c.g(base_key)
    diverge = (g_np is None) != (g_base is None) or (
        g_np is not None and g_base is not None and
        (abs(g_np - g_base) * 100 > N.get("bridge_gap_pp", 5) or (g_np > 0) != (g_base > 0)))
    if not diverge:
        return None, []

    def d(k, sign=1):
        a, b = c.v(k), c.v(k, 0)
        return None if a is None or b is None else sign * (a - b)
    parts = [("laba operasional sebelum pencadangan" if bank else "laba operasional", d(base_key))]
    if bank:
        parts += [("beban pencadangan", d("provision", -1)), ("pos operasional lainnya", d("other_op_net"))]
    parts += [("pos non-operasi bersih", d("non_operating")), ("beban pajak", d("tax", -1)),
              ("kepentingan nonpengendali", d("np_nci", -1))]
    parts = [(n, x) for n, x in parts if x]
    target = d("net_profit")
    if target is None or not parts:
        return None, []
    tol = max(abs(c.v("net_profit") or 0) * 0.01, 0.1 * (1e9 if c.s["currency"] == "IDR" else 1e6))
    if abs(sum(x for _, x in parts) - target) > tol:
        raise ValueError("jembatan laba tidak menjumlah")
    others = sorted([p for p in parts if not p[0].startswith("laba operasional")], key=lambda x: -abs(x[1]))
    struct = c.m.get("_structure")

    def label(n, x):
        if n == "beban pajak":
            return "beban pajak yang lebih rendah" if x > 0 else "beban pajak yang lebih tinggi"
        if n == "beban pencadangan":
            return "beban pencadangan yang lebih rendah" if x > 0 else "beban pencadangan yang lebih tinggi"
        if n == "kepentingan nonpengendali" and struct:
            return "perubahan bagian kepentingan nonpengendali (efek perubahan struktur grup)"
        return n
    items = [f"{label(n, x)} yang {'menambah' if x > 0 else 'mengurangi'} laba sebesar {F.money(x, how='delta')}"
             for n, x in others[:3]]
    gb = say(lambda: F.pct(g_base, signed=True)) or "tidak sebanding"
    gn = say(lambda: F.pct(g_np, signed=True)) or "tidak sebanding"
    txt = (f"Laju {F.label('net_profit')} ({gn} YoY) berbeda dari {'PPOP' if bank else 'laba operasional'} "
           f"({gb} YoY). Selisih tersebut dijelaskan oleh {_join(items)}.")

    # pemilahan pos non-operasi: keuangan (terkait kas/utang, relatif berulang) vs lain-lain (fokus peringatan)
    nonop = dict(others).get("pos non-operasi bersih")
    fin_d = oth_d = 0.0
    if nonop and not bank:
        for r in c.comps("nonop_comp"):
            if r["vals"][0] is None or r["vals"][1] is None:
                continue
            dd = r["vals"][1] - r["vals"][0]
            if r["label"] in ("Pendapatan keuangan", "Beban keuangan", "Selisih kurs"):
                fin_d += dd
            else:
                oth_d += dd
        if fin_d and oth_d:
            txt += (f" Dari perubahan pos non-operasi, {F.money(fin_d, how='delta')} berasal dari pos keuangan yang terkait "
                    f"posisi kas dan utang ({'menambah' if fin_d > 0 else 'mengurangi'} laba), dan {F.money(oth_d, how='delta')} "
                    f"dari pos lain-lain ({'menambah' if oth_d > 0 else 'mengurangi'} laba).")
    # pajak: efek volume (PBT berubah) vs efek tarif (ETR berubah)
    etr, etr0, pbt, pbt0 = c.v("r_etr"), c.v("r_etr", 0), c.v("pbt"), c.v("pbt", 0)
    if not bank and None not in (etr, etr0, pbt, pbt0) and "beban pajak" in dict(others):
        vol, rate = (pbt - pbt0) * etr0, (etr - etr0) * pbt
        txt += (f" Perubahan beban pajak terdiri atas efek volume dari laba sebelum pajak sebesar {F.money(vol, how='delta')} "
                f"dan efek tarif sebesar {F.money(rate, how='delta')}: tarif pajak efektif "
                f"{'turun' if etr < etr0 else 'naik'} dari {F.level(etr0, ('r_etr', 0))} menjadi {F.level(etr, ('r_etr', 1))}, "
                f"yang {'menopang' if etr < etr0 else 'menekan'} laba.")
    # arah interpretasi: peringatan hanya bila pos di bawah OP/PPOP MENAMBAH laba
    below = [(n, x) for n, x in others if n != "beban pencadangan" or True]
    adds = sum(x for n, x in below if x > 0)
    top_n, top_x = others[0] if others else (None, 0)
    if top_n == "beban pencadangan" and top_x > 0 and bank:
        txt += " Pertumbuhan laba terutama ditopang beban pencadangan yang lebih rendah; keberlanjutannya perlu dicermati."
    elif top_x > 0 and top_n in ("pos non-operasi bersih", "pos operasional lainnya"):
        txt += (" Kontributor terbesarnya berasal dari pos non-operasi" + (", terutama pos lain-lain" if oth_d > fin_d else "")
                + ", sehingga keberlanjutan laba ini perlu dicermati.")
    elif (c.g(base_key) or 0) > 0 and top_x < 0:
        txt += (f" Laba ditopang kinerja operasional; {label(top_n, top_x).split(' yang')[0]} menjadi penekan.")
    elif adds <= 0:
        txt += " Kualitas laba relatif baik karena pertumbuhan bersumber dari kinerja operasional."
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
    gq = (c.m.get("r_gross_profit") or {}).get("vals")
    if c.p.idx > 0 and gq and None not in (gq[3], gq[4]):
        dq = direction(d_margin=gq[4] - gq[3])
        if dq == "turun" and dm != "turun":
            b = "Margin Kuartalan Tertekan"
        elif dq == "naik" and dm == "turun":
            b = "Margin Kuartalan Membaik"
    title = theme_title(c, False) or f"{tk} {c.p.label}: {a}, {b}"

    # P1 headline
    s1 = say(lambda: c.pick(f"{tk} membukukan pendapatan {L} yang {movement(c, 'revenue')}.",
                            f"Pada {L}, pendapatan {tk} {movement(c, 'revenue')}.",
                            f"Kinerja {tk} pada {L} diawali pendapatan yang {movement(c, 'revenue')}.",
                            f"Dari sisi top-line, pendapatan {tk} pada {L} {movement(c, 'revenue')}."))
    s2 = say(lambda: quarter_clause(c, "revenue", "pendapatan"))
    s3 = say(lambda: c.pick("Di level bottom-line, ", "Sementara itu, ", "Pada baris terbawah, ", "Adapun ")
             + f"{F.label('net_profit')} {movement(c, 'net_profit', label_profit=True)}.")
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
                              f"{F.money(r['vals'][1], (r['key'], 1))}, sehingga perlu dicek di catatan laporan apakah berasal dari akuisisi atau pengembangan bisnis baru."))
        c.watch.append((3, abs(r["vals"][1]), f"kontribusi lanjutan {r['label'].lower()}"))
    losers = [r for r in c.comps("seg_profit") if (r["vals"][1] or 0) < 0 and not r["label"].lower().startswith("lainnya")]
    for r in losers[:2]:
        p2.append(say(lambda: f"Dari sisi segmen, {r['label']} masih mencatatkan rugi sebesar {F.money(r['vals'][1], (r['key'], 1))}."))
        c.watch.append((2, abs(r["vals"][1]), f"kinerja segmen {r['label']} yang masih merugi"))
    if c.p.idx > 0:
        qs = [r for r in c.comps("rev_seg") if r["growth"][1] is not None and not r["label"].lower().startswith("lainnya")]
        up_ = [r for r in qs if r["growth"][1] > 0.10]
        dn_ = [r for r in qs if r["growth"][1] < -0.10]
        if up_ and dn_:
            a_, b_ = max(up_, key=lambda r: r["growth"][1]), min(dn_, key=lambda r: r["growth"][1])
            p2.append(f"Pada {c.p.q_label}, bauran pendapatan bergeser: {a_['label'].lower()} naik {F.pct(a_['growth'][1])} QoQ, "
                      f"sedangkan {b_['label'].lower()} turun {F.pct(b_['growth'][1])} QoQ.")
    paras.append(" ".join(x for x in p2 if x) or None)

    # P3 biaya
    p3 = [say(lambda: c.pick("Dari sisi biaya, ", "Di sisi beban, ", "Pada struktur biaya, ")
              + f"beban pokok pendapatan {movement(c, 'cogs')}."),
          say(lambda: drivers(c, "cogs_comp", "cogs", "beban pokok"))]
    ro, ro0 = c.m.get("r_opex", {}).get("vals", [None] * 5)[1], c.m.get("r_opex", {}).get("vals", [None] * 5)[0]
    if ro is not None and ro0 is not None and abs(ro - ro0) >= 0.005:
        p3.append(say(lambda: f"Beban usaha {movement(c, 'opex')}, dengan rasio terhadap pendapatan "
                              f"{'meningkat' if ro > ro0 else 'menurun'} ({F.bps(ro - ro0)})."))
    qdiv = quarterly_divergence(c)
    if not qdiv and c.p.idx > 0 and None not in (c.v("revenue", 4), c.v("revenue", 3), c.v("gross_profit", 4), c.v("gross_profit", 3)):
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
        txt = c.pick("Dari sisi profitabilitas, ", "Pada level margin, ", "Ditinjau dari profitabilitas, ") \
            + ", ".join(p4) + f" dibanding {c.Lpy}."
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
    for amt, txt, known in oneoffs(c, "nonop_comp"):
        if known:
            p5.append(f"Perlu dicermati, laba periode ini turut dipengaruhi {txt}, yang berpotensi merupakan pos tidak berulang (one-off).")
        else:
            p5.append(f"Laba periode ini turut dipengaruhi {txt}; sifat pos ini perlu dicek di catatan laporan.")
        c.watch.append((1, amt, f"keberlanjutan {txt.split(' sebesar')[0]}"))
    etr, etr0 = c.v("r_etr"), c.v("r_etr", 0)
    if etr is not None and etr0 is not None and abs(etr - etr0) >= 0.03:
        c.watch.append((1, abs((c.v("tax") or 0) - (c.v("tax", 0) or 0)),
                        f"arah tarif pajak efektif yang bergerak dari {F.level(etr0, ('r_etr', 0))} menjadi {F.level(etr, ('r_etr', 1))}"))
    paras.append(" ".join(p5) or None)

    if qdiv:
        paras.append(qdiv)

    # P6 neraca (perubahan nominal; growth net debt selalu n.m.)
    nd, nd0, ndq = c.v("bs_net_debt"), c.v("bs_net_debt", 0), c.v("bs_net_debt", 3)
    if nd is not None:
        kind = "kas bersih" if nd < 0 else "utang bersih"
        nde = c.v("r_nde")
        if nd0 is not None and (nd0 < 0) != (nd < 0):
            k0 = "kas bersih" if nd0 < 0 else "utang bersih"
            paras.append(f"Posisi neraca berbalik dari {k0} {F.money(nd0, ('bs_net_debt', 0))} pada {c.p.same_last_year().q_label} "
                         f"menjadi {kind} {F.money(nd, ('bs_net_debt', 1))}.")
        elif nd0 is not None and abs(nd) != abs(nd0):
            arah = "bertambah" if abs(nd) > abs(nd0) else "menyusut"
            txt = (f"Dari sisi neraca, {kind} {arah} {F.money(abs(nd) - abs(nd0), how='delta')} menjadi {F.money(nd, ('bs_net_debt', 1))}"
                   + (f" ({F.share(abs(nde))} dari ekuitas)" if nde is not None else "")
                   + f" dari {F.money(nd0, ('bs_net_debt', 0))} pada {c.p.same_last_year().q_label}")
            if ndq is not None and (ndq < 0) == (nd < 0) and c.p.idx > 0:
                txt += f" dan {F.money(ndq, ('bs_net_debt', 3))} pada {c.p.prev().q_label}"
            txt += "."
            lim = cfg.SETTINGS.get("watch", {}).get("net_cash_change", 0.25)
            if abs(abs(nd) / abs(nd0) - 1) > lim:
                txt += " Perubahan sebesar ini perlu dicek penyebabnya dari arus kas investasi dan pendanaan."
                fin_dn = kind == "kas bersih" and abs(nd) < abs(nd0) and (c.g("fin_income") or 0) < 0
                c.watch.append((1, abs(abs(nd0) - abs(nd)), f"{kind} yang {arah} menjadi {F.money(nd, ('bs_net_debt', 1))}"
                                + (", sejalan dengan pendapatan keuangan yang turun" if fin_dn else "")))
            paras.append(txt)
        else:
            paras.append(f"Dari sisi neraca, perseroan berada pada posisi {kind} sebesar {F.money(nd, ('bs_net_debt', 1))}"
                         + (f" ({F.share(abs(nde))} dari ekuitas)." if nde is not None else "."))
    ta = c.g("bs_total_assets", 0)
    if ta is not None and ta > N.get("inorganic_asset_jump", 0.3):
        c.watch.append((3, 0, "kemungkinan kontribusi anorganik seiring kenaikan total aset yang signifikan"))

    fw = frameworks_general(c)
    if fw:
        paras.append(fw)

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
    title = theme_title(c, True) or title
    paras = []
    p1 = [say(lambda: c.pick(f"{tk} membukukan {F.label('net_profit')} {L} yang {movement(c, 'net_profit')}.",
                             f"Pada {L}, {F.label('net_profit')} {tk} {movement(c, 'net_profit')}.",
                             f"Kinerja {tk} pada {L} ditandai {F.label('net_profit')} yang {movement(c, 'net_profit')}.")),
          say(lambda: f"Pendapatan operasional bank (NII ditambah pendapatan non-bunga) {movement(c, 'total_op_income')}, "
                      f"dengan {nii_lbl} {movement(c, 'nii')}."),
          say(lambda: quarter_clause(c, "net_profit", "laba bersih"))]
    nim, nim0 = c.v("r_nim"), c.v("r_nim", 0)
    if nim is not None and nim0 is not None:
        p1.append(f"NIM (estimasi dari aset produktif) tercatat {F.level(nim, ('r_nim', 1))} ({F.bps(nim - nim0)} YoY).")
    p1.append(say(lambda: nii_decomposition(c)))
    p1.append(say(lambda: loan_nii_gap(c)))
    p1 = [x for x in p1 if x]
    paras.append(" ".join(x for x in p1 if x))
    paras.append(say(lambda: drivers(c, "noncore_comp", "noncore_income", "pendapatan non-bunga")))
    p3 = [say(lambda: f"Beban operasional {movement(c, 'opex')}."), say(lambda: cir_sentence(c, d_cir)),
          say(lambda: f"Laba operasional sebelum pencadangan (PPOP) {movement(c, 'ppop')}."),
          say(lambda: f"Beban pencadangan {movement(c, 'provision')}" + (f", dengan cost of credit {F.level(c.v('r_coc'), ('r_coc', 1))}." if c.v('r_coc') is not None else ".")),
          say(lambda: provision_link(c))]
    paras.append(" ".join(x for x in p3 if x))
    btxt, others = bridge(c, bank=True)
    p4 = [btxt] if btxt else []
    bq = bank_quarterly(c)
    if bq:
        p4.append(bq)
    for amt, txt, known in oneoffs(c, "noncore_comp"):
        p4.append(f"Pendapatan periode ini turut dipengaruhi {txt}; " + ("pos ini berpotensi tidak berulang (one-off)."
                                                                          if known else "sifat pos ini perlu dicek di catatan laporan."))
        c.watch.append((3, amt, f"keberlanjutan {txt.split(' sebesar')[0]}"))
    if others and others[0][0] == "beban pencadangan":
        c.watch.append((2, abs(others[0][1]), "keberlanjutan beban pencadangan yang rendah"))
    paras.append(" ".join(p4) or None)
    p5 = [say(lambda: f"Dari sisi neraca, {loan} {movement(c, 'bs_loans', gi=2)}"
                      + (f" ({F.pct(c.m['ytd_loans'], signed=True)} YtD)" if c.m.get('ytd_loans') is not None else "") + "."),
          say(lambda: f"Dana pihak ketiga {movement(c, 'bs_deposits', gi=2)}, dengan CASA ratio {F.level(c.v('r_casa'), ('r_casa', 1))} "
                      f"dan {ldr} {F.level(c.v('r_ldr'), ('r_ldr', 1))}.")]
    p5.append(say(lambda: capital_generation(c)))
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
        c.watch.append((1, abs((c.v("core_cost") or 0) - (c.v("core_cost", 0) or 0)),
                        ("hak pihak ketiga atas bagi hasil" if sharia else "beban bunga") + " yang tumbuh lebih cepat dari pendapatan "
                        + ("margin dan bagi hasil" if sharia else "bunga") + ", yang menekan NIM"))
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
    txt = c.pick("Secara keseluruhan, ", "Menimbang hasil tersebut, ", "Kesimpulannya, ", "Dengan demikian, ") + BASE[(dr, dn)]
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
    if c.p.idx > 0 and c.g("net_profit", 1) is not None:
        q1, q2 = c.g("net_profit", 1), c.g("net_profit", 2)
        t_ = f"**Kuartalan:** laba {c.p.q_label} {'naik' if q1 >= 0 else 'turun'} {F.pct(q1)} QoQ"
        if q2 is not None:
            t_ += f", {'namun masih' if (q1 < 0) != (q2 < 0) else 'dan'} {'naik' if q2 >= 0 else 'turun'} {F.pct(q2)} YoY"
        b.insert(1, t_ + ".")
    if top_watch:
        b.append(f"**Perlu dicermati:** {top_watch}.")
    return f"**Ringkasan {L}**\n" + "\n".join("• " + x for x in b if x)


def watch_paragraph(c: Ctx, items):
    if not items:
        return None
    nxt = c.p.next().label
    intro = c.pick(f"Hal yang perlu dicermati pada {nxt}: ", f"Ke depan, investor perlu memantau beberapa hal pada {nxt}: ",
                   f"Fokus pemantauan untuk {nxt}: ")
    return intro + "; ".join(f"({i}) {t}" for i, t in enumerate(items, 1)) + "."


# ------------------------------------------------------------------ utama
def build(table, store, note=None, wl=None):
    c = Ctx(table, store, wl)
    title, paras = (_general if table["template"] == "general" else _bank)(c)
    for a in table.get("anomalies") or []:
        c.watch.append((2, 0, _register_numbers(c.F, a, convert=True)))
    ranked = sorted(dict(((t, (pr, -mat)) for pr, mat, t in c.watch)).items(), key=lambda x: x[1])
    items, fams = [], set()
    for t, _ in ranked:                       # satu fenomena = satu item (aturan C11)
        fam = next((f for f in ("GPM", "OPM", "margin", "pajak", "kas bersih", "utang bersih", "pencadangan", "CoC",
                                "NIM", "beban bunga", "CIR", "beban usaha", "beban pokok", "segmen", "modal kerja", "arus kas")
                    if f.lower() in t.lower()), t)
        fam = {"OPM": "margin", "GPM": "margin", "CoC": "pencadangan", "beban bunga": "NIM"}.get(fam, fam)
        if fam in fams:
            continue
        fams.add(fam)
        items.append(t)
    top, rest = (items[0] if items else None), items[1:5]
    paras = [p for p in paras if p]
    if note:
        paras.append(_register_numbers(c.F, note.strip(), convert=False))
    from core.sysfiles import macro_notes
    sektor = (wl or {}).get("sektor", "").upper()
    for per, who, text in macro_notes():
        if per == c.p.label and who in (store["ticker"], sektor):
            paras.append("Kondisi makro (input analis): " + _register_numbers(c.F, text, convert=False))
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


def _join(items):
    items = [i for i in items if i]
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " dan " + items[-1] if items else ""


def quarterly_divergence(c: Ctx):
    """Paragraf 'kondisi kuartalan berbeda' bila arah margin kuartalan berlawanan dengan kumulatif."""
    F, p = c.F, c.p
    g = c.m.get("r_gross_profit", {}).get("vals")
    if p.idx == 0 or not g or None in (g[0], g[1], g[3], g[4]):
        return None
    d_q, d_cum = g[4] - g[3], g[1] - g[0]
    dq, dc = direction(d_margin=d_q), direction(d_margin=d_cum)
    if dq == "stabil" or dq == dc:
        return None
    s = [f"Kondisi kuartalan berbeda."]
    rq, rq0, cq, cq0 = c.v("revenue", 4), c.v("revenue", 3), c.v("cogs", 4), c.v("cogs", 3)
    if None not in (rq, rq0, cq, cq0):
        dr_, dc_ = rq - rq0, cq - cq0
        if dr_ > 0 and dc_ > dr_:
            s.append(f"Pada {p.q_label}, kenaikan pendapatan {F.money(dr_, how='delta')} QoQ sepenuhnya tergerus kenaikan beban pokok "
                     f"{F.money(dc_, how='delta')}")
        elif dr_ < 0 < dc_:
            s.append(f"Pada {p.q_label}, pendapatan turun {F.money(dr_, how='delta')} QoQ, sementara beban pokok justru naik "
                     f"{F.money(dc_, how='delta')}")
        else:
            s.append(f"Pada {p.q_label}, pendapatan {'naik' if dr_ >= 0 else 'turun'} {F.money(dr_, how='delta')} QoQ dan beban pokok "
                     f"{'naik' if dc_ >= 0 else 'turun'} {F.money(dc_, how='delta')}")
        comps = [(r, (r["vals"][4] or 0) - (r["vals"][3] or 0)) for r in c.comps("cogs_comp")
                 if r["vals"][3] is not None and r["vals"][4] is not None]
        if comps:
            r0, d0 = max(comps, key=lambda x: x[1] if dc_ > 0 else -x[1])
            if d0 and (d0 > 0) == (dc_ > 0):
                nm = "pos lain-lain" if r0["label"].lower().startswith("lainnya") else r0["label"].lower()
                s[-1] += f", terutama dari {nm} yang {'naik' if d0 > 0 else 'turun'} {F.money(d0, how='delta')}"
                if r0["growth"][1] is not None:
                    s[-1] += f" ({F.pct(r0['growth'][1], signed=True)} QoQ)"
        s[-1] += "."
    gp_txt = say(lambda: movement(c, "gross_profit", 4, 3, 1, "QoQ"))
    np_txt = say(lambda: movement(c, "net_profit", 4, 3, 1, "QoQ"))
    tail = []
    if gp_txt:
        tail.append(f"laba kotor {gp_txt}")
    tail.append(f"GPM {'turun' if d_q < 0 else 'naik'} menjadi {F.level(g[4], ('r_gross_profit', 4))} dari {F.level(g[3], ('r_gross_profit', 3))}")
    if np_txt:
        tail.append(f"laba bersih {np_txt}")
    j = _join(tail)
    s.append(j[:1].upper() + j[1:] + ".")
    c.watch.append((1, abs(d_q) * abs(rq or 0), f"GPM {p.q_label} yang {'turun' if d_q < 0 else 'naik'} {F.bps(d_q, signed=False)} QoQ "
                                               f"menjadi {F.level(g[4], ('r_gross_profit', 4))}"))
    return " ".join(s)


# ================================================================== fungsi analisis tambahan
def theme_title(c: Ctx, bank: bool):
    """Headline dari tema terkuat (aturan C1), bukan template generik."""
    tk, L = c.s["ticker"], c.L

    def phrase(g):
        if g is None:
            return None
        b = band(g)
        if b == "stabil":
            return "Relatif Stabil"
        if g > 0:
            return {"tipis": "Naik Tipis", "solid": "Tumbuh Solid", "kuat": "Tumbuh Kuat"}[b]
        return {"tipis": "Turun Tipis", "solid": "Turun", "kuat": "Turun Signifikan"}[b]
    n1, n0 = c.v("net_profit"), c.v("net_profit", 0)
    if n1 is not None and n0 is not None and (n1 < 0) != (n0 < 0):
        A = f"{L} Berbalik {'Laba' if n1 > 0 else 'Rugi'}"
    else:
        ph = phrase(c.g("net_profit"))
        A = f"Laba {L} {ph}" if ph else None
    themes = []
    if c.m.get("_structure"):
        themes.append((5, "Terdampak Perubahan Struktur Grup"))
    if bank:
        gl, gn = c.g("bs_loans", 2), c.g("nii")
        nim, nim0 = c.v("r_nim"), c.v("r_nim", 0)
        if gl is not None and gn is not None and gl - gn > 0.05:
            themes.append((4, "Kredit Melaju, Margin Bunga Tertekan" if None not in (nim, nim0) and nim0 - nim >= 0.0025
                           else "Kredit Melaju, NII Tertinggal"))
        npq, ppq = c.g("net_profit", 1), c.g("ppop", 1)
        if npq is not None and ppq is not None and npq > 0 > ppq:
            themes.append((3, "Laba Kuartalan Ditopang Provisi"))
        pv = c.g("provision")
        if pv is not None and pv < -0.1 and (c.g("net_profit") or 0) > (c.g("ppop") or 0):
            themes.append((2, "Ditopang Penurunan Provisi"))
    else:
        gq = (c.m.get("r_gross_profit") or {}).get("vals")
        if c.p.idx > 0 and gq and None not in (gq[0], gq[1], gq[3], gq[4]):
            dq, dc = direction(d_margin=gq[4] - gq[3]), direction(d_margin=gq[1] - gq[0])
            if dq != "stabil" and dq != dc:
                themes.append((4, f"Margin Kuartalan {'Tertekan' if dq == 'turun' else 'Membaik'}"))
        o1, o0 = c.v("r_operating_profit"), c.v("r_operating_profit", 0)
        if o1 is not None and o0 is not None and abs(o1 - o0) >= 0.02:
            themes.append((2, f"Margin Operasional {'Melebar' if o1 > o0 else 'Menyempit'}"))
        gop, gnp = c.g("operating_profit"), c.g("net_profit")
        if gop is not None and gnp is not None and abs(gnp - gop) > 0.15:
            themes.append((2, "Laba Bersih Tertahan Pos di Bawah Operasional" if gnp < gop else "Laba Bersih Terdorong Pos Non-Operasi"))
    themes.sort(key=lambda x: -x[0])
    B = themes[0][1] if themes else None
    if B and "," in B:
        return f"{tk} {c.p.label}: {B}"
    if A and B:
        return f"{tk} {c.p.label}: {A}, {B}"
    return f"{tk} {c.p.label}: {A}" if A else None


def cir_sentence(c: Ctx, d_cir):
    """CIR dijelaskan dari laju pendapatan operasional vs beban operasional (kausalitas berbasis angka)."""
    F = c.F
    if d_cir is None:
        raise NM()
    g_inc, g_opx = c.g("total_op_income"), c.g("opex")
    dr = direction(d_margin=d_cir)
    state = "relatif stabil di" if dr == "stabil" else ("naik menjadi" if d_cir > 0 else "turun menjadi")
    txt = f"CIR {state} {F.level(c.v('r_cir'), ('r_cir', 1))} ({F.bps(d_cir)})"
    if g_inc is not None and g_opx is not None:
        rel = "lebih cepat dari" if g_inc > g_opx + 0.005 else "lebih lambat dari" if g_inc < g_opx - 0.005 else "sejalan dengan"
        txt += (f" karena pendapatan operasional ({F.pct(g_inc, signed=True)}) tumbuh {rel} beban operasional "
                f"({F.pct(g_opx, signed=True)})")
    return txt + "."


def provision_link(c: Ctx):
    """Kaitkan provisi dengan pertumbuhan kredit (aturan E5)."""
    F = c.F
    gp, gl = c.g("provision"), c.g("bs_loans", 2)
    coc, coc0 = c.v("r_coc"), c.v("r_coc", 0)
    if gp is None or gl is None or coc is None or coc0 is None or gp <= 0:
        raise NM()
    if direction(d_margin=coc - coc0) == "stabil":
        return (f"Kenaikan beban pencadangan sejalan dengan pertumbuhan kredit ({F.pct(gl, signed=True)} YoY), tercermin dari "
                f"cost of credit yang relatif stabil ({F.level(coc0, ('r_coc', 0))} menjadi {F.level(coc, ('r_coc', 1))}).")
    c.watch.append((0, abs((c.v("provision") or 0) - (c.v("provision", 0) or 0)),
                    f"kualitas aset seiring cost of credit naik dari {F.level(coc0, ('r_coc', 0))} menjadi {F.level(coc, ('r_coc', 1))}")
                   if coc > coc0 else (2, 0, f"keberlanjutan cost of credit yang turun menjadi {F.level(coc, ('r_coc', 1))}"))
    return None


def nii_decomposition(c: Ctx):
    F = c.F
    vol, price = c.m.get("_nii_vol"), c.m.get("_nii_price")
    if vol is None or price is None or not (vol + price):
        raise NM()
    return (f"Secara estimasi, perubahan NII berasal dari efek volume aset produktif sebesar {F.money(vol, how='derived')} "
            f"({'menambah' if vol > 0 else 'mengurangi'}) dan efek margin sebesar {F.money(price, how='derived')} "
            f"({'menambah' if price > 0 else 'mengurangi'}).")


def loan_nii_gap(c: Ctx):
    F = c.F
    gl, gn, gnp = c.g("bs_loans", 2), c.g("nii"), c.g("net_profit")
    if gl is None or gn is None or gl - gn <= 0.05:
        raise NM()
    txt = f"Kredit tumbuh {F.pct(gl)} YoY, lebih cepat dari NII ({F.pct(gn, signed=True)})"
    if gnp is not None:
        txt += f" dan laba ({F.pct(gnp, signed=True)})"
    nim, nim0 = c.v("r_nim"), c.v("r_nim", 0)
    if nim is not None and nim0 is not None and nim < nim0:
        txt += f", sejalan dengan NIM (est.) yang turun {F.bps(nim - nim0, signed=False)}"
    c.watch.append((1, 0, f"tekanan NIM seiring kredit ({F.pct(gl, signed=True)}) yang tumbuh lebih cepat dari NII ({F.pct(gn, signed=True)})"))
    return txt + "."


def capital_generation(c: Ctx):
    F = c.F
    icg, gl = c.m.get("_icg"), c.g("bs_loans", 2)
    if icg is None or gl is None:
        raise NM()
    rel = "di bawah" if icg < gl else "di atas"
    return (f"Pembentukan modal internal (ROE dikurangi porsi dividen) sekitar {F.pct(icg)}, {rel} pertumbuhan kredit "
            f"{F.pct(gl)} YoY" + (", sehingga laju kredit saat ini bergantung pada modal yang sudah ada." if icg < gl else "."))


def bank_quarterly(c: Ctx):
    """Divergensi kuartalan bank (aturan C6): laba vs PPOP, NII vs beban bunga."""
    F, p = c.F, c.p
    if p.idx == 0:
        return None
    out = []
    npq, ppq, pvq = c.g("net_profit", 1), c.g("ppop", 1), c.g("provision", 1)
    if npq is not None and ppq is not None and (npq > 0) != (ppq > 0):
        t = f"Kondisi kuartalan berbeda: pada {p.q_label}, laba bersih {'naik' if npq > 0 else 'turun'} {F.pct(npq)} QoQ, " \
            f"sementara PPOP {'naik' if ppq > 0 else 'turun'} {F.pct(ppq)} QoQ"
        dp = None if None in (c.v("provision", 4), c.v("provision", 3)) else c.v("provision", 4) - c.v("provision", 3)
        if dp is not None and (dp < 0) == (npq > 0):
            t += f", sehingga selisihnya berasal dari beban pencadangan yang {'turun' if dp < 0 else 'naik'} {F.money(dp, how='delta')}"
        out.append(t + ".")
    nq, iq, eq = c.g("nii", 1), c.g("core_income", 1), c.g("core_cost", 1)
    if nq is not None and iq is not None and eq is not None and nq < 0 and eq > iq:
        out.append(f"NII {p.q_label} turun {F.pct(nq)} QoQ karena beban bunga naik {F.pct(eq)} sementara pendapatan bunga "
                   f"hanya {'naik' if iq >= 0 else 'turun'} {F.pct(iq)}.")
    return " ".join(out) or None


def frameworks_general(c: Ctx):
    """Kerangka analisis nonbank: DuPont, operating leverage, arus kas, modal kerja, kemampuan bayar."""
    F = c.F
    s = []
    roe, npm, at, em = c.v("r_roe"), c.v("r_net_profit"), c.v("r_at"), c.v("r_em")
    if None not in (roe, npm, at, em):
        s.append(f"Secara DuPont, ROE {F.level(roe, ('r_roe', 1))} terbentuk dari net profit margin {F.level(npm, ('r_net_profit', 1))}, "
                 f"perputaran aset {F.mult(at, ('r_at', 1))}, dan pengganda ekuitas {F.mult(em, ('r_em', 1))}.")
    ol = c.m.get("_op_leverage")
    if ol is not None and abs(ol) >= 1.5:
        s.append(f"Operating leverage tercatat {F.mult(ol)}, artinya laba operasional bergerak sekitar {F.mult(ol)} "
                 f"laju perubahan pendapatan.")
    cfo = c.v("r_cfo_np")
    if cfo is not None:
        s.append(f"Arus kas operasi setara {F.share(cfo)} dari laba bersih.")
        if cfo < cfg.SETTINGS.get("frameworks", {}).get("cfo_to_profit_warn", 0.7):
            c.watch.append((1, 0, f"konversi laba menjadi kas yang rendah (arus kas operasi {F.share(cfo)} dari laba)"))
    cc, cc0 = c.v("r_ccc"), c.v("r_ccc", 0)
    if cc is not None and cc0 is not None and abs(cc - cc0) > cfg.SETTINGS.get("frameworks", {}).get("wc_days_change", 10):
        s.append(f"Siklus konversi kas {'memanjang' if cc > cc0 else 'memendek'} dari {F.days(cc0, ('r_ccc', 0))} menjadi "
                 f"{F.days(cc, ('r_ccc', 1))}.")
        if cc > cc0:
            c.watch.append((2, 0, f"modal kerja seiring siklus konversi kas memanjang menjadi {F.days(cc, ('r_ccc', 1))}"))
    icr = c.v("r_icr")
    if icr is not None and 0 < icr < 3:
        s.append(f"Interest coverage tergolong rendah di {F.mult(icr, ('r_icr', 1))}.")
        c.watch.append((1, 0, f"kemampuan membayar bunga dengan interest coverage {F.mult(icr, ('r_icr', 1))}"))
    return " ".join(s) or None
