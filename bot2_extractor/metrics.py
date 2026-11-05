"""Perhitungan: angka kumulatif, kuartal murni, QoQ, YoY, rasio, dan model tabel."""
from core.fmt import growth, safe_div
from core.periods import Period


class Calc:
    def __init__(self, store: dict, period: Period):
        self.s, self.p = store, period
        self.P = store["periods"]

    # ------------------------------------------------ pengambilan nilai dasar
    def _own(self, p: Period, key):
        e = self.P.get(p.key)
        return None if e is None else e["cur"].get(key)

    def _from_next(self, p: Period, key):
        e = self.P.get(Period(p.year + 1, p.idx).key)
        return None if e is None else e["prior"].get(key)

    def cum(self, p: Period, key, prefer_next=False):
        a, b = self._own(p, key), self._from_next(p, key)
        return (b if b is not None else a) if prefer_next else (a if a is not None else b)

    def cum_py(self, key):
        """Kumulatif periode sama tahun lalu: pakai kolom pembanding laporan terbaru (sudah disajikan ulang)."""
        e = self.P.get(self.p.key)
        v = None if e is None else e["prior"].get(key)
        return v if v is not None else self.cum(self.p.same_last_year(), key)

    def q(self, p: Period, key):
        """Angka kuartal murni = kumulatif periode ini - kumulatif periode sebelumnya (tahun yang sama)."""
        if p.idx == 0:
            return self.cum(p, key)
        prev = p.prev()
        for nxt in (False, True):
            a, b = self.cum(p, key, nxt), self.cum(prev, key, nxt)
            if a is not None and b is not None:
                return a - b
        return None

    def bs(self, p: Period, key):
        """Pos neraca (posisi akhir periode)."""
        v = self._own(p, key)
        if v is None and p.idx == 3:  # posisi Desember tersedia sebagai pembanding di semua laporan tahun berikutnya
            for i in range(4):
                e = self.P.get(Period(p.year + 1, i).key)
                if e and e["prior"].get(key) is not None:
                    return e["prior"][key]
        return v

    # ------------------------------------------------ paket 5 kolom
    def flow(self, key):
        p = self.p
        return [self.cum_py(key), self.cum(p, key), self.q(p.same_last_year(), key),
                self.q(p.prev(), key), self.q(p, key)]

    def stock(self, key):
        p = self.p
        py, cur = self.bs(p.same_last_year(), key), self.bs(p, key)
        return [py, cur, py, self.bs(p.prev(), key), cur]

    def avg_stock(self, key):
        """Rata-rata saldo awal & akhir untuk tiap kolom (dipakai rasio tahunan seperti ROE, cost of credit)."""
        p, py = self.p, self.p.same_last_year()
        ye, ye_py = Period(p.year - 1, 3), Period(p.year - 2, 3)
        return [avg(self.bs(ye_py, key), self.bs(py, key)), avg(self.bs(ye, key), self.bs(p, key)),
                avg(self.bs(py.prev(), key), self.bs(py, key)),
                avg(self.bs(p.prev().prev(), key), self.bs(p.prev(), key)),
                avg(self.bs(p.prev(), key), self.bs(p, key))]

    def keys_with(self, prefix):
        keys = set()
        for pk in (self.p.key, self.p.prev().key):
            e = self.P.get(pk)
            if e:
                keys |= {k for side in ("cur", "prior") for k in e[side] if k.startswith(prefix + "::")}
        return keys


def growths(vals):
    cum_py, cum, q_py, q_prev, q = vals
    return [growth(cum, cum_py), growth(q, q_prev), growth(q, q_py)]


def ratio_vals(num, den, scale=1.0):
    return [None if (a is None or b in (None, 0)) else a / b * scale for a, b in zip(num, den)]


def annualize(vals, p: Period):
    """Faktor tahunan untuk kolom [kum_py, kum, q_py, q_prev, q]."""
    f_cum = 4 / p.quarters
    return [None if v is None else v * f for v, f in zip(vals, [f_cum, f_cum, 4, 4, 4])]


def avg(a, b):
    if a is None and b is None:
        return None
    if a is None or b is None:
        return a if a is not None else b
    return (a + b) / 2


# ======================================================================= model tabel
import yaml  # noqa: E402

from core import config as _cfg  # noqa: E402

_GROUPS = yaml.safe_load(open(_cfg.CONFIG_DIR / "groups.yaml", encoding="utf-8")) or {}
_TBL = _cfg.SETTINGS.get("table", {"max_groups": 4, "min_share": 0.02})


def _sum_lists(lists):
    out = []
    for col in zip(*lists):
        vals = [v for v in col if v is not None]
        out.append(sum(vals) if vals else None)
    return out


def _group_of(name, rules):
    low = name.lower()
    for label, words in (rules or {}).items():
        if any(w in low for w in words):
            return label
    return None


def build(store: dict, period: Period) -> dict:
    """Model tabel ringkas untuk investor (Excel & gambar) + metrik untuk narasi."""
    c = Calc(store, period)
    names = store.get("names", {})
    tpl = store["template"]
    rows, m = [], {}

    def add(label, key=None, vals=None, kind="main", fmt="num", ratio_of=None):
        vals = vals if vals is not None else c.flow(key)
        g = growths(vals) if fmt == "num" else [None, None, None]
        rows.append({"label": label, "key": key, "kind": kind, "fmt": fmt, "vals": vals, "growth": g,
                     "ratio_of": ratio_of})
        if key:
            m[key] = {"vals": vals, "growth": g, "label": label}
        return vals

    def components(prefix, total_key=None, other="Lainnya", flow=True):
        """Kelompokkan komponen serupa, ambil maks. N kelompok terbesar, sisanya 'Lainnya'."""
        rules = _GROUPS.get(prefix)
        items = {}
        for k in c.keys_with(prefix):
            v = c.flow(k) if flow else c.stock(k)
            if all(x in (None, 0) for x in v[:2] + v[4:]):
                continue
            nm = names.get(k.split("::", 1)[1], k.split("::", 1)[1])
            if any(w in nm.lower() for w in ("eliminasi", "elimination", "penyesuaian", "adjustment")):
                grp = other
            else:
                grp = _group_of(nm, rules) or (other if rules else nm)
            items.setdefault(grp, []).append(v)
        if not items:
            return []
        agg = {g: _sum_lists(vs) for g, vs in items.items()}
        if total_key and total_key in m:                      # selisih ke total masuk "Lainnya"
            tot = m[total_key]["vals"]
            res = [None if t is None else t - sum((v[i] or 0) for v in agg.values()) for i, t in enumerate(tot)]
            if any(r is not None and t and abs(r) > abs(t) * 0.005 for r, t in zip(res, tot)):
                agg[other] = _sum_lists([agg.get(other, [None] * 5), res])
        base = sum(abs(v[1] or 0) for v in agg.values()) or 1
        ranked = sorted((g for g in agg if g != other), key=lambda g: -abs(agg[g][1] or 0))
        keep = [g for g in ranked if abs(agg[g][1] or 0) / base >= _TBL["min_share"]][:_TBL["max_groups"]]
        rest = [agg[g] for g in agg if g not in keep]
        out = []
        for g in keep:
            add(g, f"{prefix}::grp_{g}", agg[g], kind="comp")
            out.append(g)
        if rest:
            add(other, f"{prefix}::grp_{other}", _sum_lists(rest), kind="comp")
        return out

    unit = "IDR Bn" if store["currency"] == "IDR" else f"{store['currency']} Mn"
    rows.append({"label": f"Income Statement - {unit}", "kind": "section"})

    if tpl == "general":
        add("Revenue", "revenue")
        components("rev_seg", "revenue")
        add("Cost of Revenue", "cogs")
        components("cogs_comp", "cogs")
        add("Gross Profit", "gross_profit")
        opex = [None if (a is None and b is None) else (a or 0) + (b or 0)
                for a, b in zip(c.flow("selling_exp"), c.flow("ga_exp"))]
        add("Operating Expenses", "opex", opex)
        add("Operating Profit", "operating_profit")
        add("Non-operating Items (net)", "non_operating")
        add("Profit Before Tax", "pbt")
        add("Tax Expense", "tax")
        add("Net Profit", "net_profit")
        if c.keys_with("seg_profit"):
            rows.append({"label": f"Segment Profit - {unit}", "kind": "section"})
            components("seg_profit", None, other="Lainnya & eliminasi")

        rows.append({"label": "Key Ratios (%)", "kind": "section"})
        rev = m["revenue"]["vals"]
        for lbl, k in (("Gross Profit Margin (GPM)", "gross_profit"), ("Operating Profit Margin (OPM)", "operating_profit"),
                       ("Net Profit Margin (NPM)", "net_profit")):
            add(lbl, f"r_{k}", ratio_vals(m[k]["vals"], rev), kind="ratio", fmt="pct", ratio_of=(k, "revenue"))
        add("Effective Tax Rate", "r_etr", ratio_vals(m["tax"]["vals"], m["pbt"]["vals"]), kind="ratio", fmt="pct",
            ratio_of=("tax", "pbt"))
        m["r_opex"] = {"vals": ratio_vals(opex, rev), "growth": [None] * 3, "label": "Opex to Revenue"}
        _roe(c, m, add, period)
        cash, debt = c.stock("bs_cash"), c.stock("bs_debt")
        nd = [None if (d is None or ca is None) else d - ca for d, ca in zip(debt, cash)]
        eq = c.stock("bs_equity_parent")
        add("Net Debt to Equity", "r_nde", ratio_vals(nd, eq), kind="ratio", fmt="pct")
        rows.append({"label": f"Balance Sheet - {unit}", "kind": "section"})
        add("Net Debt (Cash)", "bs_net_debt", nd)
        add("Equity (parent)", "bs_equity_parent", eq)

    else:  # bank konvensional / syariah
        sharia = tpl == "bank_sharia"
        add("Pendapatan Margin & Bagi Hasil" if sharia else "Interest Income", "core_income")
        components("core_inc_comp", "core_income")
        add("Hak Pihak Ketiga atas Bagi Hasil" if sharia else "Interest Expense", "core_cost")
        components("core_cost_comp", "core_cost")
        add("Pendapatan Margin & Bagi Hasil Bersih" if sharia else "Net Interest Income (NII)", "nii")
        add("Pendapatan Operasional Lainnya" if sharia else "Non-Interest Income", "noncore_income")
        components("noncore_comp", "noncore_income")
        add("Operating Expenses", "opex")
        add("Pre-Provision Operating Profit (PPOP)", "ppop")
        add("Provision Expenses", "provision")
        add("Net Profit", "net_profit")
        for k in ("operating_profit", "pbt", "tax", "total_op_income"):
            v = c.flow(k)
            m[k] = {"vals": v, "growth": growths(v), "label": k}
        if c.keys_with("seg_profit"):
            rows.append({"label": f"Segment Net Profit - {unit}", "kind": "section"})
            components("seg_profit", None, other="Lainnya & eliminasi")

        rows.append({"label": "Key Ratios (%)", "kind": "section"})
        add("Cost to Income Ratio (CIR)", "r_cir", ratio_vals(m["opex"]["vals"], m["total_op_income"]["vals"]),
            kind="ratio", fmt="pct")
        loans = c.stock("bs_loans")
        coc = ratio_vals(annualize(m["provision"]["vals"], period), c.avg_stock("bs_loans"))
        add("Cost of Credit (annualized)", "r_coc", coc, kind="ratio", fmt="pct")
        _roe(c, m, add, period)
        dep = c.stock("bs_deposits")
        casa = [None if (a is None or b is None) else a + b for a, b in zip(c.stock("dep_current"), c.stock("dep_savings"))]
        add("CASA Ratio", "r_casa", ratio_vals(casa, dep), kind="ratio", fmt="pct")
        add("FDR" if sharia else "LDR", "r_ldr", ratio_vals(loans, dep), kind="ratio", fmt="pct")

        rows.append({"label": f"Balance Sheet - {unit}", "kind": "section"})
        add("Pembiayaan" if sharia else "Loans", "bs_loans", loans)
        add("Dana Pihak Ketiga" if sharia else "Customer Deposits", "bs_deposits", dep)
        for key, lbl in (("dep_current", "Giro" if sharia else "Current Accounts"),
                         ("dep_savings", "Tabungan" if sharia else "Savings"),
                         ("dep_time", "Deposito" if sharia else "Time Deposits")):
            add(lbl, key, c.stock(key), kind="comp")
        ye = Period(period.year - 1, 3)
        m["ytd_loans"] = growth(loans[1], c.bs(ye, "bs_loans"))
        m["ytd_deposits"] = growth(dep[1], c.bs(ye, "bs_deposits"))

    from core.labels import to_en
    for r in rows:                      # label tabel berbahasa Inggris; "label" (Indonesia) dipakai narasi
        r["label_en"] = to_en(r["label"], store)
    cols = [period.same_last_year().cum_label, period.cum_label, period.same_last_year().q_label,
            period.prev().q_label, period.q_label]
    return {"rows": rows, "m": m, "cols": cols, "unit": unit, "period": period, "template": tpl,
            "q_available": m["net_profit"]["vals"][4] is not None}


def _roe(c: Calc, m, add, period: Period):
    add("ROE (annualized)", "r_roe", ratio_vals(annualize(m["net_profit"]["vals"], period),
                                                 c.avg_stock("bs_equity_parent")), kind="ratio", fmt="pct")
