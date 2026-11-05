"""Pintu masuk semua bot.

  python main.py collect                 # Bot 1: Inbox + scraper IDX, keluarkan daftar emiten yang berubah
  python main.py process --tickers AADI  # Bot 2 + Bot 3 untuk emiten tertentu
  python main.py run-all                 # semuanya berurutan (untuk uji coba / self-hosted)
  python main.py process --tickers AADI --force --periods 3Q26   # paksa hitung & kirim ulang
"""
import argparse
import json
import logging
import os
import sys


def setup_logging():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    for noisy in ("googleapiclient", "pdfminer", "pypdf", "matplotlib", "PIL", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def gh_output(name, value):
    path = os.getenv("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{name}={value}\n")


def cmd_collect(args):
    from bot1_collector import collector
    changed = set(collector.run(scrape_allowed=not args.no_scrape))
    extra = {t.strip().upper() for t in (args.extra or "").split(",") if t.strip()}
    tickers = sorted(changed | extra)
    print("Emiten untuk diproses:", tickers or "(tidak ada)")
    gh_output("tickers", json.dumps(tickers))
    return tickers


def cmd_process(args, tickers=None):
    from core import discord
    from bot2_extractor import extractor
    from bot3_narrator import narrator
    tickers = tickers or [t.strip().upper() for t in (args.tickers or "").split(",") if t.strip()]
    periods = [p.strip().upper() for p in (args.periods or "").split(",") if p.strip()] or None
    failed = []
    for tk in tickers:
        try:
            data = extractor.run(tk, force=args.force)
            if data.get("periods"):
                sent = narrator.run(tk, data, force_periods=periods if args.force else None)
                logging.info("%s: narasi terkirim untuk %s", tk, sent or "-")
        except Exception as e:
            logging.exception("%s gagal diproses", tk)
            discord.status(f"{tk}: processing failed ({type(e).__name__}: {e}).", "error")
            failed.append(tk)
    if failed:
        sys.exit(1)


def main():
    setup_logging()
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--extra", help="emiten tambahan untuk diproses ulang, pisahkan koma")
    c.add_argument("--no-scrape", action="store_true")
    p = sub.add_parser("process")
    p.add_argument("--tickers", required=True)
    p.add_argument("--force", action="store_true", help="hitung ulang & kirim ulang narasi")
    p.add_argument("--periods", help="dengan --force: periode yang dikirim ulang, mis. 3Q26")
    r = sub.add_parser("run-all")
    r.add_argument("--no-scrape", action="store_true")
    r.add_argument("--force", action="store_true")
    r.add_argument("--periods")
    r.add_argument("--extra")
    args = ap.parse_args()
    if args.cmd == "collect":
        cmd_collect(args)
    elif args.cmd == "process":
        cmd_process(args)
    else:
        args.tickers = None
        tickers = cmd_collect(args)
        if tickers:
            cmd_process(args, tickers)


if __name__ == "__main__":
    main()
