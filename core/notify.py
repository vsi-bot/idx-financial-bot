"""File notifikasi laporan yang ditahan: _Sistem/NOTIFIKASI - PERLU DICEK.txt (entri terbaru di atas).
Satu entri per emiten-periode-alasan; alasan yang sama hanya memperbarui tanggal."""
from datetime import datetime, timedelta, timezone

from core import config as cfg
from core.storage import get_storage

WIB = timezone(timedelta(hours=7))
STATE = "notifikasi.json"
FILE = "NOTIFIKASI - PERLU DICEK.txt"


def record(entries: list[dict]) -> None:
    """entries: [{ticker, period, reason, fix}]"""
    if not entries:
        return
    st, sysf = get_storage(), cfg.FOLDERS["system"]
    data = st.read_json(sysf, STATE, {}) or {}
    now = datetime.now(WIB).strftime("%Y-%m-%d %H:%M WIB")
    for e in entries:
        key = f"{e['ticker']}|{e['period']}|{e['reason'][:120]}"
        old = data.get(key, {})
        data[key] = {**e, "first": old.get("first", now), "last": now}
    st.write_json(sysf, STATE, data)
    lines = ["LAPORAN YANG DITAHAN (tidak dikirim ke Discord) - entri terbaru di atas", "=" * 72, ""]
    for v in sorted(data.values(), key=lambda x: x["last"], reverse=True):
        lines += [f"[{v['last']}] {v['ticker']} {v['period']}", f"  Alasan : {v['reason']}",
                  f"  Solusi : {v.get('fix', '-')}", ""]
    path = cfg.WORK_DIR / "notifikasi.txt"
    path.write_text("\n".join(lines), encoding="utf-8")
    st.upload(path, st.folder(sysf), FILE, "text/plain")
