"""Regression test: memastikan perbaikan kode tidak merusak hasil yang sudah benar.

Cara pakai (opsional, di komputer dengan Python):
  1. Taruh file contoh di tests/fixtures/ (Excel + PDF asli dari IDX).
  2. Jalankan:  python tests/run_regression.py
Kasus yang filenya tidak ada akan dilewati.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp())
os.environ.update(STORAGE="local", LOCAL_ROOT=str(TMP / "drive"), WORK_DIR=str(TMP / "work"), DISCORD_DRY_RUN="true")

# (kode, periode, pos, nilai harapan dalam satuan tampilan, toleransi relatif)
CASES = [
    ("AADI", "3Q26", "revenue", 1, 3959.2, 0.001),
    ("AADI", "3Q26", "net_profit", 1, 683.3, 0.001),
    ("BRIS", "2Q26", "net_profit", 1, 4155.7, 0.001),
    ("BRIS", "2Q26", "nii", 1, 9708.5, 0.001),
    # UNVR 9M26 (dari evaluasi): 3Q25 restated, laba inti continuing, operasi dihentikan
    ("UNVR", "3Q26", "revenue", 2, 8242, 0.01),
    ("UNVR", "3Q26", "net_profit", 1, 3314, 0.01),
    ("UNVR", "3Q26", "np_disc", 1, 887, 0.02),
]


def main():
    fx = ROOT / "tests" / "fixtures"
    files = [f for f in fx.iterdir() if f.suffix.lower() in (".xlsx", ".pdf")] if fx.exists() else []
    if not files:
        print("Tidak ada file di tests/fixtures/. Taruh file Excel & PDF contoh dulu.")
        return
    inbox = TMP / "drive" / "Inbox"
    inbox.mkdir(parents=True)
    for f in files:
        shutil.copy(f, inbox / f.name)
    from bot1_collector import collector
    from bot2_extractor import extractor, metrics, store
    from core.fmt import unit_info
    from core.periods import Period
    tickers = collector.run(scrape_allowed=False)
    ok = bad = 0
    for tk in tickers:
        extractor.run(tk)
    for tk, label, key, col, exp, tol in CASES:
        if tk not in tickers:
            continue
        data = store.load(tk)
        P = Period.parse_label(label)
        if P.key not in data["periods"]:
            print(f"SKIP {tk} {label}: periode belum ada")
            continue
        t = metrics.build(data, P)
        v = (t["m"].get(key) or {}).get("vals", [None] * 5)[col]
        got = None if v is None else v / unit_info(data["currency"])[0]
        passed = got is not None and abs(got - exp) <= abs(exp) * tol
        ok += passed
        bad += not passed
        print(f"{'PASS' if passed else 'FAIL'} {tk} {label} {key}[{col}] = {got} (harapan {exp}) | validasi: {t['status']}")
    print(f"\nHasil: {ok} lolos, {bad} gagal")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
