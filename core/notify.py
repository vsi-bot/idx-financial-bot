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
    _render(st, sysf, data)


def resolve(pairs: list[tuple[str, str]]) -> None:
    """Tandai semua entri emiten-periode ini sebagai sudah selesai."""
    if not pairs:
        return
    st, sysf = get_storage(), cfg.FOLDERS["system"]
    data = st.read_json(sysf, STATE, {}) or {}
    now = datetime.now(WIB).strftime("%Y-%m-%d %H:%M WIB")
    changed = False
    for v in data.values():
        if (v["ticker"], v["period"]) in pairs and not v.get("resolved"):
            v["resolved"] = now
            changed = True
    if changed:
        st.write_json(sysf, STATE, data)
        _render(st, sysf, data)


def _render(st, sysf, data):
    active = [v for v in data.values() if not v.get("resolved")]
    done = [v for v in data.values() if v.get("resolved")]
    lines = ["LAPORAN YANG DITAHAN (tidak dikirim ke Discord) - entri terbaru di atas", "=" * 72, ""]
    for v in sorted(active, key=lambda x: x["last"], reverse=True):
        lines += [f"[{v['last']}] {v['ticker']} {v['period']}", f"  Alasan : {v['reason']}",
                  f"  Solusi : {v.get('fix', '-')}", ""]
    if not active:
        lines += ["(tidak ada masalah aktif)", ""]
    lines += ["", "SUDAH SELESAI", "-" * 72]
    for v in sorted(done, key=lambda x: x["resolved"], reverse=True)[:100]:
        lines.append(f"[selesai {v['resolved']}] {v['ticker']} {v['period']} - {v['reason']}")
    path = cfg.WORK_DIR / "notifikasi.txt"
    path.write_text("\n".join(lines), encoding="utf-8")
    st.upload(path, st.folder(sysf), FILE, "text/plain")
