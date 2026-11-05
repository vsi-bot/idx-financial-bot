"""Format angka gaya riset: Rp783.6 miliar, USD1,020.8 juta, +12.4%, -1,920 bps."""
import math

SCALE = {"IDR": (1e9, "Rp", "miliar"), "USD": (1e6, "USD", "juta")}   # format narasi (bahasa Indonesia)


def unit_info(currency: str):
    return SCALE.get(currency, (1e6, currency, "juta"))


def unit_header(currency: str) -> str:
    return "IDR Bn" if currency == "IDR" else f"{currency} Mn"


def scaled(value, currency):
    if value is None:
        return None
    return value / unit_info(currency)[0]


def money(value, currency, decimals=1) -> str:
    div, sym, word = unit_info(currency)
    if value is None:
        return "n.a."
    return f"{sym}{value / div:,.{decimals}f} {word}"


def num(value, decimals=0) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "-"
    s = f"{abs(value):,.{decimals}f}"
    return f"({s})" if value < 0 else s


def pct(x, decimals=1, sign=True) -> str:
    if x is None:
        return "n.m."
    return f"{x * 100:+.{decimals}f}%" if sign else f"{x * 100:.{decimals}f}%"


def pct_abs(x, decimals=1) -> str:
    return "n.m." if x is None else f"{abs(x) * 100:.{decimals}f}%"


def bps(x) -> str:
    if x is None:
        return "n.m."
    v = round(x * 10000)
    return f"{v:+,} bps"


def growth(cur, base):
    """Pertumbuhan; None (n.m.) jika basis kosong, nol, atau negatif."""
    if cur is None or base is None or base == 0 or (cur < 0) != (base < 0):
        return None          # basis nol atau tanda berubah (laba<->rugi, pendapatan<->beban): n.m.
    return abs(cur) / abs(base) - 1   # tanda sama: dari nilai absolut (beban dalam kurung: positif = beban naik)


def safe_div(a, b):
    if a is None or b in (None, 0):
        return None
    return a / b
