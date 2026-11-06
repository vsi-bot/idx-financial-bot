"""Pembaca file Excel laporan keuangan IDX (format XBRL, FinancialStatement-YYYY-X-KODE.xlsx).

Pencocokan pos memakai label bahasa Inggris dari template IDX, yang seragam antar-emiten.
Semua nilai dikonversi ke satuan penuh mata uang pelaporan (misal: USD, bukan ribuan USD).
"""
import logging
import re
import warnings
from datetime import date, datetime
from pathlib import Path

from openpyxl import load_workbook

from core.periods import MONTHS, Period

warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")
log = logging.getLogger(__name__)


def _num(x):
    if x is None or isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return float(x)
    try:
        return float(str(x).replace(",", ""))
    except ValueError:
        return None


def _rows(ws):
    return [list(r) for r in ws.iter_rows(values_only=True)]


def _title(ws) -> str:
    for r in ws.iter_rows(max_row=1, values_only=True):
        return str(r[0] or "")
    return ""


# ------------------------------------------------------------------ metadata
def read_meta(path) -> dict:
    wb = load_workbook(path, read_only=True)
    if "1000000" not in wb.sheetnames:
        raise ValueError("Bukan file Excel laporan keuangan IDX (sheet 1000000 tidak ada).")
    info = {}
    for r in _rows(wb["1000000"]):
        cells = [c for c in r if c not in (None, "")]
        if len(cells) >= 3 and isinstance(cells[-1], str):
            info[cells[-1].strip()] = cells[1]
    start = info.get("Current period start date")
    if isinstance(start, str):
        try:
            start = datetime.strptime(start[:10], "%Y-%m-%d").date()
        except ValueError:
            start = None
    elif isinstance(start, datetime):
        start = start.date()
    end = info.get("Current period end date")
    if isinstance(end, str):
        end = datetime.strptime(end[:10], "%Y-%m-%d").date()
    elif isinstance(end, datetime):
        end = end.date()
    if not isinstance(end, date):
        raise ValueError("Tanggal akhir periode tidak terbaca.")
    code = MONTHS.get(end.month)
    if not code:
        raise ValueError(f"Bulan akhir periode tidak lazim: {end}")

    cur_txt = str(info.get("Description of presentation currency", "IDR"))
    currency = cur_txt.split("/")[-1].strip().upper() or "IDR"
    rnd = str(info.get("Level of rounding used in financial statements", "")).lower()
    factor = 1.0
    if "ribu" in rnd or "thousand" in rnd:
        factor = 1e3
    elif "juta" in rnd or "million" in rnd:
        factor = 1e6
    elif "miliar" in rnd or "billion" in rnd:
        factor = 1e9
    return {
        "ticker": str(info.get("Entity code", "")).strip().upper(),
        "name": str(info.get("Entity name", "")).strip(),
        "period": Period.of(end.year, code),
        "currency": currency,
        "factor": factor,
        "industry": str(info.get("Entity main industry", "")),
        "subsector": str(info.get("Subsector", "")),
        "audit": str(info.get("Type of report on financial statements", "")),
        "fiscal_ok": not (isinstance(start, date) and start.month != 1),   # tahun buku Januari-Desember
    }


# ------------------------------------------------------------- statements
def _statement(ws):
    """Laporan 4 kolom: [label ID, nilai kini, nilai pembanding, label EN]."""
    out = []
    for r in _rows(ws):
        r = (r + [None] * 4)[:4]
        en = r[3]
        if isinstance(en, str) and (_num(r[1]) is not None or _num(r[2]) is not None):
            out.append({"en": en.strip(), "id": str(r[0] or "").strip(), "cur": _num(r[1]), "prior": _num(r[2])})
    return out


def _pick(wb, keyword: str):
    """Dari beberapa variasi sheet (by function/nature), pilih yang paling banyak terisi."""
    best, best_n = None, 0
    for s in wb.sheetnames:
        if not s[:1].isdigit():
            continue
        t = _title(wb[s])
        if keyword in t and "Prior Year" not in t:
            rows = _statement(wb[s])
            if len(rows) > best_n:
                best, best_n = rows, len(rows)
    return best or []


def _sheet_by_title(wb, *keywords):
    for s in wb.sheetnames:
        t = _title(wb[s]).lower()
        if s[:1].isdigit() and all(k.lower() in t for k in keywords) and "prior year" not in t:
            return wb[s]
    return None


def _n(s: str) -> str:
    return re.sub(r"[^a-z0-9()]+", " ", str(s).lower()).strip()


def _find(rows, *labels):
    labels_l = {_n(l) for l in labels}
    for r in rows:
        if _n(r["en"]) in labels_l:
            return r
    return None


import yaml as _yaml  # noqa: E402
from core import config as _cfg  # noqa: E402

LINE_ITEMS = _yaml.safe_load(open(_cfg.CONFIG_DIR / "line_items.yaml", encoding="utf-8")) or {}


def _fk(rows, key):
    """Cari pos berdasarkan kamus config/line_items.yaml: cocok persis dulu, lalu potongan kata."""
    spec = LINE_ITEMS.get(key, {})
    r = _find(rows, *(spec.get("exact") or []))
    if r:
        return r
    for frag in spec.get("contains") or []:
        f = _n(frag)
        for row in rows:
            if f in _n(row["en"]) and not _n(row["en"]).startswith(("other comprehensive", "total comprehensive")):
                return row
    return None


class Out:
    """Penampung nilai kini & pembanding, sudah dikali faktor pembulatan."""

    def __init__(self, factor):
        self.f, self.cur, self.prior = factor, {}, {}

    def put(self, key, cur, prior):
        if cur is not None:
            self.cur[key] = cur * self.f
        if prior is not None:
            self.prior[key] = prior * self.f

    def put_row(self, key, row, transform=lambda v: v):
        if row:
            self.put(key, None if row["cur"] is None else transform(row["cur"]),
                     None if row["prior"] is None else transform(row["prior"]))


# ----------------------------------------------------------- general industry
def _L(key):
    return LINE_ITEMS.get(key, {}).get("exact") or []


REV, COGS, GP = _L("revenue"), _L("cogs"), _L("gross_profit")
SELL, GA = _L("selling_exp"), _L("ga_exp")
FIN_COST, FIN_INC = _L("fin_cost"), _L("fin_income")
PBT, TAX, NP_ALL, NP_PAR, NP_NCI = _L("pbt"), _L("tax"), _L("np_total"), _L("np_parent"), _L("np_nci")


def _common_bottom(o: Out, isr):
    o.put_row("pbt", _fk(isr, "pbt"))
    o.put_row("tax", _fk(isr, "tax"), lambda v: -v)           # beban pajak bernilai positif
    o.put_row("np_total", _find(isr, *NP_ALL))
    o.put_row("np_disc", _fk(isr, "np_disc"))
    o.put_row("np_cont", _fk(isr, "np_cont"))
    par = _fk(isr, "np_parent")
    o.put_row("net_profit", par or _find(isr, *NP_ALL))
    o.put_row("np_nci", _fk(isr, "np_nci"))
    for side in ("cur", "prior"):
        d = getattr(o, side)
        disc = d.get("np_disc") or 0.0
        if "np_cont" not in d and "np_total" in d:
            d["np_cont"] = d["np_total"] - disc
        if "np_cont" not in d and "pbt" in d and "tax" in d:
            d["np_cont"] = d["pbt"] - d["tax"]
        # laba inti (continuing) yang diatribusikan ke induk; operasi dihentikan diasumsikan milik induk
        if "net_profit" in d:
            d["net_profit_core"] = d["net_profit"] - disc


def _general(wb, o: Out, isr, bs):
    rev = _fk(isr, "revenue")
    if not rev:                       # jalur cadangan: baris pertama yang tampak seperti pendapatan
        for r in isr[:6]:
            en = _n(r["en"])
            if any(w in en for w in ("revenue", "income", "sales")) and not any(
                    w in en for w in ("expense", "cost", "tax", "comprehensive", "other", "share of")):
                rev = r
                o.cur["_revenue_proxy"] = 1.0
                break
    if not rev:
        return False
    o.put_row("revenue", rev)
    o.put_row("cogs", _fk(isr, "cogs"), abs)
    gp_row = _fk(isr, "gross_profit")
    o.put_row("gross_profit", gp_row)
    if gp_row:
        o.put_row("gp_reported", gp_row)          # dipakai untuk validasi Rev - COGS = GP
    o.put_row("selling_exp", _fk(isr, "selling_exp"), abs)
    o.put_row("ga_exp", _fk(isr, "ga_exp"), abs)
    o.put_row("fin_cost", _fk(isr, "fin_cost"), abs)
    o.put_row("fin_income", _fk(isr, "fin_income"))
    # rincian pos non-operasi: semua baris di antara beban usaha dan laba sebelum pajak
    pbt_row = _fk(isr, "pbt")
    anchor = _fk(isr, "ga_exp") or _fk(isr, "selling_exp") or gp_row
    if anchor and pbt_row and anchor in isr and pbt_row in isr:
        for r in isr[isr.index(anchor) + 1:isr.index(pbt_row)]:
            en = _n(r["en"])
            if en.startswith(("total", "subtotal")):
                continue
            expense = any(w in en for w in ("costs", "expenses", "expense", "cost")) and not any(
                w in en for w in ("gain", "income", "profit", "reversal"))
            o.put_row(f"nonop_comp::{r['id'] or r['en']}", r, (lambda v: -abs(v)) if expense else (lambda v: v))
    _common_bottom(o, isr)
    for side in ("cur", "prior"):
        d = getattr(o, side)
        if "gross_profit" not in d and "revenue" in d and "cogs" in d:
            d["gross_profit"] = d["revenue"] - d["cogs"]
        if "gross_profit" in d and ("ga_exp" in d or "selling_exp" in d):
            d["operating_profit"] = d["gross_profit"] - d.get("selling_exp", 0) - d.get("ga_exp", 0)
        elif "pbt" in d:  # laporan "by nature": pakai proksi EBIT
            d["operating_profit"] = d["pbt"] + d.get("fin_cost", 0) - d.get("fin_income", 0)
        if "pbt" in d and "operating_profit" in d:
            d["non_operating"] = d["pbt"] - d["operating_profit"]

    # --- rincian pendapatan per jenis produk/jasa
    ws = _sheet_by_title(wb, "Revenue By Type")
    if ws:
        for r in _rows(ws):
            r = (r + [None] * 8)[:8]
            idl = str(r[0] or "")
            if re.match(r"Pendapatan (dari )?(jasa|produk)( \d+| lainnya)$", idl, re.I):
                kind = "jasa" if "jasa" in idl.lower() else "produk"
                for side, ncol, vcol in (("cur", 1, 2), ("prior", 5, 6)):
                    v = _num(r[vcol])
                    if v is None:
                        continue
                    name = (r[ncol] or f"{kind.capitalize()} lainnya").strip()
                    key = f"rev_seg::{name}"
                    d = getattr(o, side)
                    d[key] = d.get(key, 0) + v * o.f
    # --- pendapatan domestik vs ekspor
    ws = _sheet_by_title(wb, "Revenue By Source")
    if ws:
        for r in _rows(ws):
            r = (r + [None] * 8)[:8]
            for en, name in (("Domestic revenue", "Domestik"), ("Export revenue", "Ekspor")):
                if r[3] == en:
                    o.put(f"rev_geo::{name}", _num(r[2]), _num(r[6]))
    # --- rincian beban pokok
    ws = _sheet_by_title(wb, "Cost of Good Sold")
    if ws:
        skip = ("total production cost", "cost of goods manufactured", "cost of sales and revenue",
                "raw inventory used", "cost of good sold")
        for r in _statement(ws):
            en = r["en"].lower()
            if en in skip or "inventory" in en:
                continue
            o.put(f"cogs_comp::{r['id']}", r["cur"], r["prior"])

    # --- neraca
    _balance_general(o, bs)
    return True


DEBT_WORDS = ("bank loans", "bonds payable", "other borrowings", "lease liabilities", "sukuk",
              "short-term loans", "long-term loans", "notes payable", "medium term notes")


def _balance_general(o: Out, bs):
    o.put_row("bs_total_assets", _find(bs, "Total assets"))
    o.put_row("bs_total_liab", _find(bs, "Total liabilities"))
    o.put_row("bs_equity_parent", _find(bs, "Total equity attributable to equity owners of parent entity"))
    o.put_row("bs_equity", _find(bs, "Total equity"))
    o.put_row("bs_cash", _find(bs, "Cash and cash equivalents"))
    liab = False
    debt = {"cur": 0.0, "prior": 0.0}
    for r in bs:
        en = r["en"].lower()
        if en == "total assets":
            liab = True
            continue
        if liab and any(w in en for w in DEBT_WORDS) and "interest" not in en and not en.startswith("total"):
            debt["cur"] += r["cur"] or 0
            debt["prior"] += r["prior"] or 0
    o.put("bs_debt", debt["cur"], debt["prior"])


# ------------------------------------------------------------------- bank
CORE_INC = ("interest income", "revenue from fund management as mudharib", "interest and sharia income",
            "sharia income")
CORE_COST = ("interest expenses", "interest expense", "third parties share on return of temporary syirkah funds",
             "interest and sharia expenses")
LOAN_RE = re.compile(r"^(loans|(murabahah|istishna|ijarah|salam|multijasa) receivables|qardh funds|"
                     r"(mudharabah|musyarakah) financing|sharia receivables|consumer financing receivables)\b")
EA_RE = re.compile(r"^(placements? with|marketable securities|government (bonds|securities)|"
                   r"investments? in (sharia )?securities|securities purchased|sukuk)")
CUR_RE = re.compile(r"(wadiah demand deposits|mudharabah current account|^current accounts? (third|related)|"
                    r"^demand deposits)")
SAV_RE = re.compile(r"(savings|saving deposits)")
TD_RE = re.compile(r"(time deposits|wakalah deposits)")


def _bank(wb, o: Out, isr, bs):
    if not any(r["en"].lower() in CORE_INC for r in isr):
        return False
    sums = {k: {"cur": 0.0, "prior": 0.0} for k in ("core_income", "core_cost", "noncore_income", "opex", "provision")}
    o.diag = {"buckets": {}, "skipped": []}
    for r in isr:
        en = r["en"].lower()
        # subtotal (mis. "Net interest income", "Total ...") dilewati agar tidak terhitung dua kali
        if en.startswith(("total ", "subtotal", "jumlah", "net interest", "net sharia", "net revenue from",
                          "net income from", "interest and sharia income - net")) and \
                not en.startswith(("total profit from operation", "total profit (loss) from operation")):
            o.diag["skipped"].append(r["en"])
            continue
        if en.startswith("total profit from operation") or en.startswith("total profit (loss) from operation"):
            o.put_row("op_reported", r)
            break
        for side in ("cur", "prior"):
            v = r[side]
            if v is None:
                continue
            if en in CORE_INC:
                bucket, val = "core_income", v
            elif en in CORE_COST:
                bucket, val = "core_cost", abs(v)
            elif "impairment" in en or "estimated losses" in en:
                bucket, val = "provision", (-v if en.startswith("reversal") else v)
            elif "expenses" in en or en.startswith(("salaries", "personnel", "employee")):
                bucket, val = "opex", abs(v)
                getattr(o, side)[f"opex_comp::{r['id']}"] = abs(v) * o.f
            else:
                bucket, val = "noncore_income", v
                getattr(o, side)[f"noncore_comp::{r['id']}"] = v * o.f
            sums[bucket][side] += val
            if side == "cur":
                o.diag["buckets"].setdefault(bucket, []).append((r["en"], val * o.f))
    for k, v in sums.items():
        o.put(k, v["cur"], v["prior"])
    for side in ("cur", "prior"):
        d = getattr(o, side)
        d["nii"] = d.get("core_income", 0) - d.get("core_cost", 0)
        d["total_op_income"] = d["nii"] + d.get("noncore_income", 0)
        d["ppop"] = d["total_op_income"] - d.get("opex", 0)
        d["operating_profit"] = d["ppop"] - d.get("provision", 0)
        if "op_reported" in d:
            diff = d["op_reported"] - d["operating_profit"]
            if abs(diff) > 1:  # pos yang belum terpetakan
                d["other_op_net"] = diff
                d["operating_profit"] = d["op_reported"]
    _common_bottom(o, isr)
    for side in ("cur", "prior"):
        d = getattr(o, side)
        if "pbt" in d and "operating_profit" in d:
            d["non_operating"] = d["pbt"] - d["operating_profit"]

    # rincian pendapatan & beban bunga/syariah dari catatan
    for title, prefix in (("Interest and Sharia Income", "core_inc_comp"), ("Interest and Sharia Expense", "core_cost_comp")):
        ws = _sheet_by_title(wb, title)
        if ws:
            for r in _statement(ws):
                en = r["en"].lower()
                if en.startswith(("total", "subtotal", "jumlah")):
                    continue
                o.put(f"{prefix}::{r['id']}", r["cur"], None if r["prior"] is None else r["prior"])
                if prefix == "core_cost_comp":
                    for side in ("cur", "prior"):
                        k = f"{prefix}::{r['id']}"
                        if k in getattr(o, side):
                            getattr(o, side)[k] = abs(getattr(o, side)[k])

    # neraca bank
    o.put_row("bs_total_assets", _find(bs, "Total assets"))
    o.put_row("bs_equity_parent", _find(bs, "Total equity attributable to equity owners of parent entity"))
    o.put_row("bs_equity", _find(bs, "Total equity"))
    liab = False
    acc = {k: {"cur": 0.0, "prior": 0.0} for k in ("bs_loans", "dep_current", "dep_savings", "dep_time", "bs_ea_other")}
    for r in bs:
        en = r["en"].lower()
        if en == "total assets":
            liab = True
            continue
        if en.startswith(("allowance", "total", "other banks", "interbank")) or "sukuk" in en:
            continue
        key = None
        if not liab and LOAN_RE.search(en):
            key = "bs_loans"
        elif not liab and EA_RE.search(en):
            key = "bs_ea_other"        # aset produktif selain kredit (untuk estimasi NIM)
        elif liab and CUR_RE.search(en):
            key = "dep_current"
        elif liab and SAV_RE.search(en):
            key = "dep_savings"
        elif liab and TD_RE.search(en):
            key = "dep_time"
        if key:
            for side in ("cur", "prior"):
                acc[key][side] += r[side] or 0
    for k, v in acc.items():
        o.put(k, v["cur"], v["prior"])
    for side in ("cur", "prior"):
        d = getattr(o, side)
        d["bs_deposits"] = d.get("dep_current", 0) + d.get("dep_savings", 0) + d.get("dep_time", 0)
    return True


# ------------------------------------------------------------------ utama
def parse(path) -> dict:
    meta = read_meta(path)
    wb = load_workbook(path, read_only=True)
    isr = _pick(wb, "Statement of profit or loss")
    bs = _pick(wb, "Statement of financial position")
    o = Out(meta["factor"])
    is_sharia = any(r["en"].lower() == "revenue from fund management as mudharib" and r["cur"] for r in isr)
    cf = _pick(wb, "Statement of cash flows")
    o.put_row("cf_dividends", _fk(cf, "dividends_paid"), abs)
    o.put_row("cf_cfo", _fk(cf, "cfo"))
    o.put_row("cf_capex", _fk(cf, "capex"), abs)
    o.put_row("cf_dna", _fk(cf, "dna"), abs)
    for key, item in (("bs_receivables", "receivables"), ("bs_inventories", "inventories"), ("bs_payables", "payables")):
        frags = [_n(x) for x in LINE_ITEMS.get(item, {}).get("contains", [])]
        tot = {"cur": None, "prior": None}
        for r in bs:
            en = _n(r["en"])
            if any(f in en for f in frags) and not en.startswith("total") and "allowance" not in en and "non current" not in en:
                for side in ("cur", "prior"):
                    if r[side] is not None:
                        tot[side] = (tot[side] or 0) + r[side]
        o.put(key, tot["cur"], tot["prior"])
    if _bank(wb, o, isr, bs):
        template = "bank_sharia" if is_sharia else "bank"
    elif _general(wb, o, isr, bs):
        template = "general"
    else:
        template = "unsupported"
    p: Period = meta["period"]
    return {
        "ticker": meta["ticker"], "name": meta["name"], "period": p.key,
        "currency": meta["currency"], "template": template,
        "industry": meta["industry"], "subsector": meta["subsector"], "audit": meta["audit"],
        "fiscal_ok": meta["fiscal_ok"],
        "cur": o.cur, "prior": o.prior, "diag": getattr(o, "diag", None),
        # pembanding laba rugi = periode sama tahun lalu; pembanding neraca = akhir tahun lalu
        "prior_is_period": Period(p.year - 1, p.idx).key,
        "prior_bs_period": Period(p.year - 1, 3).key,
    }
