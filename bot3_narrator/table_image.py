"""Membuat gambar tabel (PNG) bergaya laporan riset, dari model tabel yang sama dengan Excel."""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

from core.fmt import unit_info  # noqa: E402

from core import config as _cfg  # noqa: E402

_T = _cfg.SETTINGS.get("theme", {})
NAVY = "#" + _T.get("header", "C00000")
GOLD = "#" + _T.get("header_growth", "8B0000")
LIGHT_GOLD = "#" + _T.get("growth_fill", "FDECEC")
SECTION = "#" + _T.get("section_text", "C00000")


def _num(v, decimals):
    if v is None:
        return "-"
    s = f"{abs(v):,.{decimals}f}"
    return f"({s})" if v < 0 else s


def _pct(v):
    return "" if v is None else f"{v * 100:.2f}%"


def render(table: dict, company: str, ticker: str, currency: str, out: Path, max_comp_rows: int = 8) -> Path:
    scale = unit_info(currency)[0]
    dec = 0 if currency == "IDR" else 1
    cols = table["cols"]
    heads = [f"{company} - {ticker}.IJ", cols[0], cols[1], "YoY", cols[2], cols[3], cols[4], "QoQ", "YoY"]
    widths = [3.9, 0.95, 0.95, 0.85, 0.95, 0.95, 0.95, 0.85, 0.85]
    gold_cols = {3, 7, 8}

    # susun baris (batasi jumlah komponen per kelompok agar gambar tetap ringkas)
    lines, comp_run = [], 0
    for r in table["rows"]:
        if r["kind"] == "comp":
            comp_run += 1
            if comp_run > max_comp_rows:
                continue
        else:
            comp_run = 0
        lines.append(r)

    row_h, head_h = 0.24, 0.36
    W = sum(widths)
    notes = list(table.get("footnotes") or [])
    H = head_h + row_h * (len(lines) + 2) + 0.17 * len(notes)
    fig = plt.figure(figsize=(W, H), dpi=170)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W)
    ax.set_ylim(H, 0)
    ax.axis("off")

    xs = [sum(widths[:i]) for i in range(len(widths))]
    for j, h in enumerate(heads):
        ax.add_patch(Rectangle((xs[j], 0), widths[j], head_h, color=GOLD if j in gold_cols else NAVY, lw=0))
        ax.text(xs[j] + (0.08 if j == 0 else widths[j] / 2), head_h / 2, h, va="center",
                ha="left" if j == 0 else "center", fontsize=8.5 if j else 9, fontweight="bold",
                color="white")

    y = head_h
    for r in lines:
        kind = r["kind"]
        if kind in ("section", "subsection"):
            ax.text(0.06, y + row_h * 0.62, r.get("label_en", r["label"]), fontsize=8.5, fontweight="bold",
                    fontstyle="italic" if kind == "subsection" else "normal", va="center", color=SECTION)
            y += row_h
            continue
        for j in gold_cols:
            ax.add_patch(Rectangle((xs[j], y), widths[j], row_h, color=LIGHT_GOLD, lw=0))
        comp = kind == "comp"
        color = "#595959" if comp else "black"
        fs = 7.4 if comp else 8.2
        lab = r.get("label_en", r["label"])
        label = lab if len(lab) <= 52 else lab[:50] + "…"
        ax.text(0.30 if comp else 0.16, y + row_h / 2, label, fontsize=fs, color=color, va="center")
        if r["fmt"] in ("x", "days"):
            vals = ["" if v is None else (f"{v:.2f}x" if r["fmt"] == "x" else f"{v:.0f}") for v in r["vals"]]
            grs = ["", "", ""]
        elif r["fmt"] == "pct":
            vals = [_pct(v) for v in r["vals"]]
            grs = ["", "", ""]
        else:
            vals = [_num(None if v is None else v / scale, dec) for v in r["vals"]]
            grs = [_pct(g) if g is not None else "n.m." for g in r["growth"]]
        cells = [vals[0], vals[1], grs[0], vals[2], vals[3], vals[4], grs[1], grs[2]]
        for j, txt in enumerate(cells, start=1):
            ax.text(xs[j] + widths[j] - 0.08, y + row_h / 2, txt, fontsize=fs, color=color, va="center", ha="right")
        ax.plot([0, W], [y + row_h, y + row_h], color="#E7E6E6", lw=0.5)
        y += row_h

    for x in xs[1:]:                     # garis kolom ala spreadsheet
        ax.plot([x, x], [head_h, y], color="#E7E6E6", lw=0.5)
    ax.plot([0, W, W, 0, 0], [0, 0, y, y, 0], color="#BFBFBF", lw=0.8)
    ax.text(0.06, y + row_h * 0.9, "Source : Company (IDX) | processed", fontsize=7.5, fontweight="bold", va="center")
    for i, n in enumerate(notes):
        ax.text(0.06, y + row_h * 1.6 + 0.17 * i, n, fontsize=6.6, color="#7F7F7F", fontstyle="italic", va="center")
    fig.savefig(out, dpi=170, facecolor="white")
    plt.close(fig)
    return out
