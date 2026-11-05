"""Terjemahan label tabel ke bahasa Inggris (hanya untuk tabel; narasi tetap bahasa Indonesia)."""
import re

import yaml

from core import config as cfg

_D = yaml.safe_load(open(cfg.CONFIG_DIR / "labels_en.yaml", encoding="utf-8")) or {}
_EXACT = {k.lower(): v for k, v in (_D.get("exact") or {}).items()}
_PREFIX = _D.get("prefix") or {}
_WORDS = {k.lower(): v for k, v in (_D.get("words") or {}).items()}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _phrase(text: str) -> str:
    parts = [p for p in re.split(r"\s+dan\s+", text.strip()) if p]
    out = []
    for part in parts:
        low = part.lower()
        if low in _EXACT:
            out.append(_EXACT[low])
            continue
        hit = next(((pre, fmt) for pre, fmt in _PREFIX.items() if low.startswith(pre)), None)
        if hit:
            out.append(hit[1].format(_phrase(part[len(hit[0]):])))
            continue
        words = part.split()
        tr = [_WORDS.get(w.lower()) for w in words]
        if all(tr) and 2 <= len(words) <= 3:      # frasa Indonesia "benda + sifat" -> urutan Inggris dibalik
            out.append(" ".join(reversed(tr)))
        else:
            out.append(" ".join(t or w for t, w in zip(tr, words)))
    return " and ".join(out)


def to_en(label: str, store: dict | None = None) -> str:
    if not label:
        return label
    low = label.strip().lower()
    if low in _EXACT:
        return _EXACT[low]
    if store:
        en = (store.get("names_en") or {}).get(_norm(label))
        if en and en.strip() and _norm(en) != _norm(label):
            return re.sub(r"\s*&\s*", " & ", en.strip())
    txt = _phrase(label)
    return txt[:1].upper() + txt[1:] if txt else label
