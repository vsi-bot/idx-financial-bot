"""Menulis workbook Excel per emiten: satu sheet per periode (terbaru di depan) + sheet Data."""
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from core import config as _cfg

_T = _cfg.SETTINGS.get("theme", {})
NAVY = _T.get("header", "C00000")           # warna header (merah Victoria Sekuritas)
GOLD = _T.get("header_growth", "8B0000")    # header kolom YoY/QoQ
GROWTH_FILL = _T.get("growth_fill", "FDECEC")
SECTION = _T.get("section_text", "C00000")
F = "Arial"
thin = Side(style="thin", color="D9D9D9")


def _fmt_num(currency):
    return '#,##0;(#,##0);"-"' if currency == "IDR" else '#,##0.0;(#,##0.0);"-"'


def write_period_sheet(wb: Workbook, title: str, table: dict, company: str, ticker: str, currency: str,
                       scale: float) -> None:
    sheet_name = table["period"].label
    if sheet_name in wb.sheetnames:
        del wb[sheet_name]
    ws = wb.create_sheet(sheet_name, 0)
    cols = table["cols"]
    status = table.get("status", "PASS")
    if status == "FAIL":
        ws.sheet_properties.tabColor = "C00000"
    elif status == "WARN":
        ws.sheet_properties.tabColor = "F4B183"
    heads = [f"{company} - {ticker}.IJ" + ("  [PERLU DICEK]" if status == "FAIL" else ""), cols[0], cols[1], "YoY", cols[2], cols[3], cols[4], "QoQ", "YoY"]
    growth_cols = {4, 8, 9}
    for j, h in enumerate(heads, start=1):
        c = ws.cell(row=1, column=j, value=h)
        c.font = Font(name=F, bold=True, color="FFFFFF", size=10)
        c.fill = PatternFill("solid", fgColor=GOLD if j in growth_cols else NAVY)
        c.alignment = Alignment(horizontal="left" if j == 1 else "center", vertical="center")
    ws.row_dimensions[1].height = 22

    numfmt = _fmt_num(currency)
    row_of = {}
    r = 2
    for row in table["rows"]:
        kind = row["kind"]
        if kind in ("section", "subsection"):
            r += 1 if kind == "section" and r > 2 else 0
            c = ws.cell(row=r, column=1, value=row.get("label_en", row["label"]))
            c.font = Font(name=F, bold=True, size=10, italic=kind == "subsection", color=SECTION)
            r += 1
            continue
        label = ("     " if kind == "comp" else "  ") + row.get("label_en", row["label"])
        c = ws.cell(row=r, column=1, value=label)
        c.font = Font(name=F, size=9 if kind == "comp" else 10, color="595959" if kind == "comp" else "000000")
        if row.get("key"):
            row_of[row["key"]] = r
        vcols = [2, 3, 5, 6, 7]
        for v, col in zip(row["vals"], vcols):
            cell = ws.cell(row=r, column=col)
            if row["fmt"] in ("x", "days"):
                if v is not None:
                    cell.value = round(v, 2) if row["fmt"] == "x" else round(v)
                cell.number_format = '0.00"x"' if row["fmt"] == "x" else '0'
            elif row["fmt"] == "pct":
                ro = row.get("ratio_of")
                if ro and ro[0] in row_of and ro[1] in row_of:
                    L = get_column_letter(col)
                    n, d = f"{L}{row_of[ro[0]]}", f"{L}{row_of[ro[1]]}"
                    cell.value = f'=IF(OR({n}="",{d}="",N({d})=0),"",{n}/{d})'
                elif v is not None:
                    cell.value = v
                cell.number_format = "0.00%"
            elif v is not None:
                cell.value = round(v / scale, table.get("decimals", 0))   # angka sesuai tampilan
                cell.number_format = numfmt
            cell.font = Font(name=F, size=9 if kind == "comp" else 10, color="595959" if kind == "comp" else "000000")
            cell.alignment = Alignment(horizontal="right")
        if row["fmt"] == "num":
            for gcol, (a, b) in zip((4, 8, 9), (("B", "C"), ("F", "G"), ("E", "G"))):
                base, cur = f"{a}{r}", f"{b}{r}"
                g = ws.cell(row=r, column=gcol,
                            value=f'=IF(OR({base}="",{cur}="",N({base})<=0,N({cur})<0),"n.m.",{cur}/{base}-1)')
                g.number_format = "0.00%"
                g.alignment = Alignment(horizontal="right")
                g.font = Font(name=F, size=9 if kind == "comp" else 10)
        for i in row.get("round_note") or []:
            from openpyxl.comments import Comment
            ws.cell(row=r, column=(4, 8, 9)[i]).comment = Comment(
                "Dihitung dari angka yang dibulatkan; beda >3% dari perhitungan presisi penuh.", "bot")
        for gcol in growth_cols:
            ws.cell(row=r, column=gcol).fill = PatternFill("solid", fgColor=GROWTH_FILL)
        for col in range(1, 10):
            ws.cell(row=r, column=col).border = Border(bottom=thin)
        r += 1

    r += 1
    ws.cell(row=r, column=1, value="Source: Company (IDX XBRL & financial statements), processed").font = \
        Font(name=F, size=9, bold=True)
    notes = ["Quarterly figures = current cumulative minus previous cumulative. n.m. = not meaningful "
             "(zero/negative base, swing to loss, or non-comparable basis)."] + list(table.get("footnotes") or [])
    for i, n in enumerate(notes, start=1):
        ws.cell(row=r + i, column=1, value=n).font = Font(name=F, size=8, italic=True, color="7F7F7F")
    ws.column_dimensions["A"].width = 48
    for col in range(2, 10):
        ws.column_dimensions[get_column_letter(col)].width = 12
    ws.freeze_panes = "B2"
    ws.sheet_view.showGridLines = False


def write_data_sheet(wb: Workbook, store: dict, scale: float) -> None:
    if "Data" in wb.sheetnames:
        del wb["Data"]
    ws = wb.create_sheet("Data")
    ws.append(["Period", "Item", "Name", "Current period", "Comparative", "Unit"])
    for c in ws[1]:
        c.font = Font(name=F, bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor=NAVY)
    unit = "IDR Bn" if store["currency"] == "IDR" else f"{store['currency']} Mn"
    names = store.get("names", {})
    for pk in sorted(store["periods"], reverse=True):
        e = store["periods"][pk]
        for k in sorted(set(e["cur"]) | set(e["prior"])):
            nm = names.get(k.split("::", 1)[1], k) if "::" in k else k
            cur, pr = e["cur"].get(k), e["prior"].get(k)
            ws.append([pk, k, nm, None if cur is None else cur / scale, None if pr is None else pr / scale, unit])
    for col, w in zip("ABCDEF", (10, 34, 48, 20, 20, 9)):
        ws.column_dimensions[col].width = w
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.font = Font(name=F, size=9)
        row[3].number_format = row[4].number_format = "#,##0.0"


def write_validation_sheet(wb: Workbook, tables: list[dict]) -> None:
    ws = wb.create_sheet("Validasi")
    ws.append(["Periode", "Status", "Pemeriksaan", "Keterangan"])
    for c in ws[1]:
        c.font = Font(name=F, bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor=NAVY)
    color = {"PASS": "E2EFDA", "WARN": "FFF2CC", "FAIL": "F8CBAD"}
    for t in sorted(tables, key=lambda t: t["period"], reverse=True):
        for ch in t.get("checks", []) or [{"name": "-", "status": "PASS", "detail": ""}]:
            ws.append([t["period"].label, ch["status"], ch["name"], ch["detail"]])
            ws.cell(row=ws.max_row, column=2).fill = PatternFill("solid", fgColor=color[ch["status"]])
        for a in t.get("anomalies", []):
            ws.append([t["period"].label, "ANOMALI", a, ""])
    for col, w in zip("ABCD", (10, 10, 55, 55)):
        ws.column_dimensions[col].width = w


def save_workbook(path: Path, tables: list[dict], store: dict) -> Path:
    from core.fmt import unit_info
    scale = unit_info(store["currency"])[0]
    wb = Workbook()
    wb.remove(wb.active)
    for t in sorted(tables, key=lambda t: t["period"]):
        write_period_sheet(wb, "", t, store.get("name", store["ticker"]), store["ticker"], store["currency"], scale)
    write_data_sheet(wb, store, scale)
    write_validation_sheet(wb, tables)
    wb.save(path)
    return path
