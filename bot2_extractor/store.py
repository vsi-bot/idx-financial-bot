"""Gudang data per emiten (_Sistem/data/KODE.json) berisi angka hasil ekstraksi semua periode."""
import re

from core import config as cfg
from core.storage import get_storage

DATA_DIR = f"{cfg.FOLDERS['system']}/data"


def norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def load(ticker: str) -> dict:
    d = get_storage().read_json(DATA_DIR, f"{ticker}.json", None)
    return d or {"ticker": ticker, "periods": {}, "files": {}, "names": {}, "narrated": []}


def save(d: dict) -> None:
    get_storage().write_json(DATA_DIR, f"{d['ticker']}.json", d)


def ingest(d: dict, parsed: dict, segments: dict | None, file_md5: dict) -> None:
    """Simpan hasil ekstraksi satu periode. Komponen disimpan dengan kunci ternormalisasi."""
    d.update({k: parsed[k] for k in ("name", "currency", "template", "industry", "subsector")})
    entry = {"cur": {}, "prior": {}, "audit": parsed.get("audit", ""), "has_segments": bool(segments)}
    for side in ("cur", "prior"):
        for k, v in parsed[side].items():
            if "::" in k:
                group, name = k.split("::", 1)
                nk = norm(name)
                d["names"].setdefault(nk, name)
                if side == "cur":
                    d["names"][nk] = name
                entry[side][f"{group}::{nk}"] = v
            else:
                entry[side][k] = v
        for seg_name, vals in (segments or {}).get(side, {}).items():
            nk = norm(seg_name)
            if side == "cur" or nk not in d["names"]:
                d["names"][nk] = seg_name
            if side == "cur" and vals.get("name_en"):
                d.setdefault("names_en", {})[nk] = vals["name_en"]
            entry[side][f"seg_rev::{nk}"] = vals["revenue"]
            if vals.get("profit") is not None:
                entry[side][f"seg_profit::{nk}"] = vals["profit"]
    entry["currency"] = parsed["currency"]
    entry["fiscal_ok"] = parsed.get("fiscal_ok", True)
    if parsed.get("diag"):
        entry["diag"] = parsed["diag"]
    for side in ("cur", "prior"):                     # aset produktif bank (untuk estimasi NIM)
        e = entry[side]
        if "bs_loans" in e:
            e["bs_earning_assets"] = e["bs_loans"] + (e.get("bs_ea_other") or 0)
    old = d["periods"].get(parsed["period"], {})
    if old.get("presentation"):
        entry["presentation"] = old["presentation"]
    d["periods"][parsed["period"]] = entry
    d["files"][parsed["period"]] = file_md5
