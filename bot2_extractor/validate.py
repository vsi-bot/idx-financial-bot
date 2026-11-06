"""Validasi otomatis sebelum tabel dipublikasikan.

Status per pemeriksaan:  PASS (selisih <= 0,5%)  |  WARN (0,5% - 3%)  |  FAIL (> 3%)
Ambang diatur di config/settings.yaml bagian `validation`. Tabel berstatus FAIL tidak dikirim ke Discord.
Selain itu modul ini menghasilkan anomali (bahan narasi) dan catatan kaki otomatis untuk tabel.
"""
from core import config as cfg
from core.periods import Period

V = cfg.SETTINGS.get("validation", {})
A = cfg.SETTINGS.get("anomaly", {})
PASS_PCT, FAIL_PCT = V.get("pass_pct", 0.5) / 100, V.get("fail_pct", 3.0) / 100
ORDER = {"PASS": 0, "WARN": 1, "FAIL": 2}
REQUIRED = {"general": ["revenue", "net_profit"],
            "bank": ["core_income", "net_profit", "bs_loans", "bs_deposits"],
            "bank_sharia": ["core_income", "net_profit", "bs_loans", "bs_deposits"]}
COST_NAMES = {"cogs": "beban pokok pendapatan", "opex": "beban usaha", "provision": "beban pencadangan",
              "core_cost": "biaya dana"}


def _tier(gap):
    if gap is None:
        return "PASS"
    return "PASS" if gap <= PASS_PCT else "WARN" if gap <= FAIL_PCT else "FAIL"


def _gap(a, b):
    """Selisih relatif |a-b| / |b|."""
    if a is None or b is None or not b:
        return None
    return abs(a - b) / abs(b)


def _en_money(v, currency):
    if v is None:
        return "n.a."
    return f"IDR{v / 1e9:,.0f}bn" if currency == "IDR" else f"{currency}{v / 1e6:,.1f}mn"


def run_checks(c, m, rows, store, period: Period, has_disc: bool) -> dict:
    checks = []
    tpl = store["template"]
    entry = store["periods"].get(period.key, {"cur": {}, "prior": {}})

    def add(name, status, detail=""):
        checks.append({"name": name, "status": status, "detail": detail})

    # 1. pos wajib
    for k in REQUIRED.get(tpl, []):
        if entry["cur"].get(k) in (None, 0):
            add(f"Pos wajib '{k}'", "FAIL", "tidak ditemukan di Excel")

    # 2. identitas laba: PBT - Tax + discontinued = Net Profit total (periode berjalan & pembanding)
    for side, lbl in (("cur", period.cum_label), ("prior", period.same_last_year().cum_label)):
        d = entry[side]
        if all(k in d for k in ("pbt", "tax", "np_total")):
            calc = d["pbt"] - d["tax"] + (d.get("np_disc") or 0)
            g = _gap(calc, d["np_total"])
            add(f"PBT - Tax (+ disc. ops) = Net Profit ({lbl})", _tier(g), f"selisih {g * 100:.2f}%")

    # 3. identitas laba kotor (bila laba kotor dilaporkan)
    d = entry["cur"]
    if all(k in d for k in ("revenue", "cogs", "gp_reported")):
        g = _gap(d["revenue"] - d["cogs"], d["gp_reported"])
        add("Revenue - COGS = Gross Profit", _tier(g), f"selisih {g * 100:.2f}%")

    # 4. bank: laba operasional hasil hitung vs dilaporkan
    if tpl.startswith("bank") and d.get("op_reported"):
        g = abs(d.get("other_op_net") or 0) / abs(d["op_reported"])
        detail = f"pos tak terpetakan {g * 100:.2f}%"
        if _tier(g) != "PASS":                       # sebutkan kandidat pos penyebab agar pemetaan bisa diperbaiki
            diag = entry.get("diag") or {}
            cand = sorted(((lbl, v) for lbl, v in (diag.get("buckets") or {}).get("noncore_income", [])),
                          key=lambda x: -abs(x[1]))[:4]
            if cand:
                detail += "; kandidat pos: " + ", ".join(lbl for lbl, _ in cand)
            if diag.get("skipped"):
                detail += "; subtotal dilewati: " + ", ".join(diag["skipped"][:3])
        add("Laba operasional bank (hitung vs laporan)", _tier(g), detail)
    # NP atribusi = PBT - pajak + discontinued - NCI (bila ada NCI)
    if all(k in d for k in ("pbt", "tax", "net_profit")) and d.get("np_nci"):
        calc = d["pbt"] - d["tax"] + (d.get("np_disc") or 0) - d["np_nci"]
        g = _gap(calc, d["net_profit"])
        add("PBT - pajak - NCI = laba atribusi induk", _tier(g), f"selisih {g * 100:.2f}%")
    # Q1 + Q2 + Q3 = kumulatif (dari angka yang ditampilkan)
    if period.idx > 0:
        div = 1e9 if store["currency"] == "IDR" else 1e6
        for k in ("revenue", "net_profit") if tpl == "general" else ("nii", "net_profit"):
            qs = [c.q(Period(period.year, i), k) for i in range(period.idx + 1)]
            cum = c.cum(period, k)
            if None not in qs and cum:
                diff = abs(sum(round(q / div, 1) for q in qs) - round(cum / div, 1))
                st_ = "PASS" if diff <= 0.05 * (period.idx + 1) + 1e-9 else _tier(_gap(sum(qs), cum))
                add(f"Q1+..+Q{period.idx + 1} = kumulatif ({k})", st_, f"selisih {diff:.1f}")

    # 5. baris "Others" terlalu besar -> kemungkinan pos terlewat
    parent = None
    for r in rows:
        if r["kind"] == "main":
            parent = r
        elif r["kind"] == "section":
            parent = None
        elif r["kind"] == "comp" and parent and parent.get("key") not in ("non_operating", "noncore_income") and \
                r["label"].lower().startswith("lainnya") and \
                not r["label"].lower().startswith("lainnya &"):
            a_, b_ = r["vals"][1], parent["vals"][1]
            share = None if a_ is None or not b_ else abs(a_) / abs(b_)
            if share is not None and share > V.get("other_share_warn", 0.25):
                add(f"Porsi 'Others' pada {parent['label']}", "WARN", f"{share * 100:.0f}% dari total")

    # 6. basis kuartal tahun lalu
    bad = sorted(k for k, b in c.basis.items() if b == "not_comparable")
    if bad:
        add("Basis kuartal tahun lalu", "WARN", "tidak sebanding (restated tidak lengkap): " + ", ".join(bad[:5]))

    # 7. pendapatan memakai jalur cadangan
    if d.get("_revenue_proxy"):
        add("Pemetaan pendapatan", "WARN", "label pendapatan tidak dikenal, memakai baris terdekat")

    # 8. roll-forward ekuitas (hanya peringatan: OCI & aksi korporasi juga mengubah ekuitas)
    eq_end = c.bs(period, "bs_equity_parent")
    eq_beg = c.bs(Period(period.year - 1, 3), "bs_equity_parent")
    if None not in (eq_end, eq_beg, d.get("net_profit")):
        exp = eq_beg + d["net_profit"] - (d.get("cf_dividends") or 0)
        g = _gap(exp, eq_end)
        st = _tier(g)
        add("Roll-forward ekuitas", "WARN" if st == "FAIL" else st,
            f"selisih {g * 100:.2f}% (bisa karena OCI/aksi korporasi)")

    status = max((ch["status"] for ch in checks), key=ORDER.get, default="PASS")
    anomalies = _anomalies(c, m, period)
    st_note, jumps = structure_signal(c, m, store, period)
    if jumps and not st_note:
        anomalies += [f"{nm} berubah {g * 100:+.1f}% YtD; perlu dicek di catatan laporan apakah ada perubahan struktur grup"
                      for nm, g in jumps]
    m["_structure"] = st_note
    return {"status": status, "checks": checks,
            "anomalies": anomalies,
            "footnotes": _footnotes(c, m, store, period, has_disc)}


def _anomalies(c, m, period: Period) -> list[str]:
    out = []
    lim = A.get("cost_qoq_pct", 15) / 100
    for k, nm in COST_NAMES.items():
        g = (m.get(k) or {}).get("growth", [None] * 3)[1]
        if g is not None and abs(g) > lim:
            out.append(f"{nm} bergerak {g * 100:+.1f}% QoQ pada {period.q_label}")
    # margin di luar rentang beberapa kuartal terakhir
    n = A.get("margin_lookback_quarters", 4)
    for num, nm in (() if period.code == "audit" else (("gross_profit", "GPM"), ("operating_profit", "OPM"))):
        if num not in m or "revenue" not in m:
            continue
        cur = _q_margin(c, period, num)
        window, p = [], period
        for _ in range(n):                    # kuartal berurutan, satu basis (restated untuk tahun lalu)
            p = p.prev()
            window.append((p, _q_margin(c, p, num, strict=True)))
        if cur is None or any(v is None for _, v in window):
            continue                          # histori tidak lengkap: anomali dilewati
        vals = [v for _, v in window]
        if not (min(vals) <= cur <= max(vals)):
            out.append(f"{nm} {period.q_label} sebesar {cur * 100:.1f}% berada di luar rentang "
                       f"{window[-1][0].q_label}–{window[0][0].q_label} ({min(vals) * 100:.1f}%–{max(vals) * 100:.1f}%)")
    g_ = (m.get("r_gross_profit") or {}).get("vals")
    o_ = (m.get("r_operating_profit") or {}).get("vals")
    if g_ and o_ and None not in (g_[3], g_[4], o_[3], o_[4]) and g_[4] < g_[3] and o_[4] > o_[3]:
        out.append(f"OPM {period.q_label} naik saat GPM turun QoQ; penyebab penurunan beban usaha perlu dicek di "
                   f"catatan laporan")
    npv = (m.get("net_profit") or {}).get("vals")
    if npv and None not in (npv[3], npv[4]) and (npv[3] > 0) != (npv[4] > 0):
        out.append(f"laba bersih kuartalan berbalik {'menjadi rugi' if npv[4] < 0 else 'menjadi laba'} pada {period.q_label}")
    return out


def structure_signal(c, m, store, period: Period):
    """Sinyal perubahan struktur grup: catatan manual/PDF, atau aset/kredit/DPK berubah > ambang YtD."""
    e = store["periods"].get(period.key, {})
    note = e.get("structure_note") or e.get("structure_kw")
    lim = cfg.SETTINGS.get("structure", {}).get("asset_jump_ytd", 0.10)
    jumps = []
    for k, nm in (("bs_total_assets", "total aset"), ("bs_loans", "kredit"), ("bs_deposits", "DPK")):
        cur, ye = c.bs(period, k), c.bs(Period(period.year - 1, 3), k)
        if cur and ye and abs(cur / ye - 1) > lim:
            jumps.append((nm, cur / ye - 1))
    return note, jumps


def _q_margin(c, p: Period, num, strict=False):
    if not strict:
        a, b = c.q(p, num), c.q(p, "revenue")
    else:
        vint = ("own", "next") if p.year >= c.p.year else ("next",)
        a = next((x for v in vint if (x := c._q_strict(p, num, v)) is not None), None)
        b = next((x for v in vint if (x := c._q_strict(p, "revenue", v)) is not None), None)
    return None if a is None or not b else a / b


def _footnotes(c, m, store, period: Period, has_disc: bool) -> list[str]:
    cur = store["currency"]
    notes = ["Growth rates are calculated from the displayed (rounded) figures."]
    if store["template"] == "general":
        notes.append("Operating profit = gross profit - selling expenses - G&A expenses.")
    notes.append("ROE = annualized net profit / average parent equity (beginning & end of period); "
                 + ("uses profit from continuing operations attributable to parent." if has_disc
                    else "uses net profit attributable to parent."))
    gaps = [c.restatement_gap(k) for k in ("revenue", "net_profit", "core_income")]
    if c.restated or any(g is not None and g > 0.005 for g in gaps):
        notes.append(f"{period.year - 1} comparatives are restated as presented in the latest report.")
    if has_disc:
        disc = (m.get("np_disc") or {}).get("vals", [None] * 5)[1]
        notes.append(f"{period.cum_label} net profit includes profit from discontinued operations of "
                     f"{_en_money(disc, cur)}; margins and ROE use core (continuing) profit.")
    if any(b == "not_comparable" for b in c.basis.values()):
        notes.append("n.m. on quarterly YoY: prior-year quarter not available on a comparable (restated) basis.")
    elif any(b == "as_reported" for b in c.basis.values()):
        notes.append("Prior-year quarter uses as-reported figures (no restatement detected).")
    if period.code == "audit":
        notes.append("4Q figures are derived as FY minus 9M and may include year-end audit adjustments.")
    st_note, _ = structure_signal(c, m, store, period)
    if st_note:
        notes.append(f"Group structure change: {st_note}. YoY/YtD comparisons of affected items are not like-for-like.")
    if store["template"].startswith("bank"):
        notes.append("NIM (est.) = annualized net interest income / average earning assets (loans, placements, securities); "
                     "LDR/FDR = loans / customer deposits; may differ from company-reported figures.")
    if getattr(c, "currency_mismatch", False):
        notes.append("Some comparatives are not shown because the reporting currency changed.")
    eq = c.bs(period, "bs_equity_parent")
    if eq is not None and eq <= 0:
        notes.append("Negative equity: equity-based ratios are not meaningful (n.m.).")
    return notes
