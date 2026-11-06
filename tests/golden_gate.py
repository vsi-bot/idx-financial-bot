"""Gerbang mutu (pertumbuhan terkendali): perubahan kode/konfigurasi hanya boleh dipakai bila output
pada golden set menyimpang <= 1,5% (learning.gate_pct) dari snapshot yang sudah diverifikasi.

  python tests/golden_gate.py --record   # simpan snapshot dari output saat ini (setelah diverifikasi analis)
  python tests/golden_gate.py --check    # bandingkan output saat ini dengan snapshot

Golden set = file Excel/PDF di tests/fixtures/. Snapshot = tests/golden/snapshot.json.
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp())
os.environ.update(STORAGE="local", LOCAL_ROOT=str(TMP / "drive"), WORK_DIR=str(TMP / "work"), DISCORD_DRY_RUN="true")
SNAP = ROOT / "tests" / "golden" / "snapshot.json"
ORDER = {"PASS": 0, "WARN": 1, "FAIL": 2}


def build_outputs() -> dict:
    fx = ROOT / "tests" / "fixtures"
    files = [f for f in fx.iterdir() if f.suffix.lower() in (".xlsx", ".pdf")] if fx.exists() else []
    if not files:
        return {}
    inbox = TMP / "drive" / "Inbox"
    inbox.mkdir(parents=True)
    for f in files:
        shutil.copy(f, inbox / f.name)
    from bot1_collector import collector
    from bot2_extractor import extractor, metrics, store
    from bot3_narrator import narrative
    from bot3_narrator.facts import check
    from core.fmt import unit_info
    from core.periods import Period
    out = {}
    for tk in collector.run(scrape_allowed=False):
        data = extractor.run(tk)
        div = unit_info(data["currency"])[0]
        for pk in sorted(data.get("periods", {})):
            P = Period.parse_key(pk)
            t = metrics.build(data, P)
            cells = {}
            for r in t["rows"]:
                if not r.get("key") or r["kind"] == "section":
                    continue
                for i, v in enumerate(r["vals"]):
                    if v is not None:
                        cells[f"{r['key']}|{i}"] = round(v / div, 1) if r["fmt"] == "num" else round(v, 4)
            try:
                _, paras, facts = narrative.build(t, data)
                violations = check(paras, facts)
            except Exception as e:
                violations = [f"narasi gagal: {e}"]
            out[f"{tk}|{P.label}"] = {"status": t["status"], "cells": cells, "violations": violations}
    return out


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "--check"
    from core import config as cfg
    gate = cfg.SETTINGS.get("learning", {}).get("gate_pct", 1.5) / 100
    if mode == "--check" and not SNAP.exists():
        print("Belum ada snapshot golden set: gerbang dilewati (PASS).")
        _out("pass")
        return
    cur = build_outputs()
    if mode == "--record":
        SNAP.parent.mkdir(parents=True, exist_ok=True)
        SNAP.write_text(json.dumps(cur, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"Snapshot disimpan: {len(cur)} laporan.")
        return
    ref = json.loads(SNAP.read_text(encoding="utf-8"))
    problems = []
    for rep_key, r in ref.items():
        c = cur.get(rep_key)
        if not c:
            problems.append(f"{rep_key}: laporan tidak lagi dihasilkan")
            continue
        if ORDER[c["status"]] > ORDER[r["status"]]:
            problems.append(f"{rep_key}: status validasi memburuk {r['status']} -> {c['status']}")
        if c["violations"]:
            problems.append(f"{rep_key}: narasi gagal checklist: {c['violations'][:2]}")
        for k, rv in r["cells"].items():
            cv = c["cells"].get(k)
            unit = 0.05 if isinstance(rv, float) and abs(rv) >= 1 else 0.00005
            if cv is None or abs(cv - rv) > max(gate * abs(rv), unit):
                problems.append(f"{rep_key} {k}: {rv} -> {cv}")
    print(f"Golden set: {len(ref)} laporan, toleransi {gate * 100:.1f}%.")
    if problems:
        print("GAGAL - perubahan TIDAK diadopsi. Penyimpangan:")
        for p in problems[:40]:
            print("  -", p)
        _out("fail")
        sys.exit(1)
    print("LOLOS - perubahan aman diadopsi.")
    _out("pass")


def _out(v):
    path = os.getenv("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"result={v}\n")


if __name__ == "__main__":
    main()
