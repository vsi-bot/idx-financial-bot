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
    from bot3_narrator import narrator
    changed = set(collector.run(scrape_allowed=not args.no_scrape))
    extra = {t.strip().upper() for t in (args.extra or "").split(",") if t.strip()}
    tickers = sorted(changed | extra)
    print("Emiten untuk diproses:", tickers or "(tidak ada)")
    gh_output("tickers", json.dumps(tickers))
    gh_output("outbox", "true" if narrator.pending_count() else "false")
    return tickers


def cmd_process(args, tickers=None):
    """Bot 2 + tahap persiapan Bot 3 (narasi & gambar masuk antrean, belum dikirim)."""
    from core import activity
    from bot2_extractor import extractor
    from bot3_narrator import narrator
    tickers = tickers or [t.strip().upper() for t in (args.tickers or "").split(",") if t.strip()]
    periods = [p.strip().upper() for p in (args.periods or "").split(",") if p.strip()] or None
    failed = []
    for tk in tickers:
        try:
            data = extractor.run(tk, force=args.force)
            if data.get("periods"):
                staged = narrator.stage(tk, data, force_periods=periods if args.force else None)
                logging.info("%s: masuk antrean kirim: %s", tk, staged or "-")
        except Exception as e:
            logging.exception("%s gagal diproses", tk)
            activity.add(f"{tk}: pemrosesan gagal ({type(e).__name__}: {e})", "error")
            failed.append(tk)
    return failed


def cmd_send(args=None):
    """Bot 3 tahap akhir: kirim antrean ke Discord berurutan per emiten."""
    from bot3_narrator import narrator
    n = narrator.send_outbox()
    print(f"Terkirim ke Discord: {n} laporan")


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
    sub.add_parser("send")
    r = sub.add_parser("run-all")
    r.add_argument("--no-scrape", action="store_true")
    r.add_argument("--force", action="store_true")
    r.add_argument("--periods")
    r.add_argument("--extra")
    args = ap.parse_args()
    from core import activity
    failed = []
    try:
        if args.cmd == "collect":
            cmd_collect(args)
        elif args.cmd == "process":
            failed = cmd_process(args)
        elif args.cmd == "send":
            cmd_send(args)
        else:
            args.tickers = None
            tickers = cmd_collect(args)
            if tickers:
                failed = cmd_process(args, tickers)
            cmd_send(args)
    except Exception as e:
        if any(w in str(e).lower() for w in ("quota", "too many times", "rate limit", "kuota")):
            activity.add(f"Kuota layanan tercapai ({e}); dilanjutkan pada putaran berikutnya.", "warn")
            failed = []
        else:
            raise
    finally:
        activity.flush(args.cmd + (f"_{args.tickers.replace(',', '-')}" if getattr(args, "tickers", None) else ""))
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
