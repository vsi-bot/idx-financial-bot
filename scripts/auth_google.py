"""Login Google SEKALI untuk membuat token akses Google Drive.

Cara pakai (di komputer Anda, bukan di GitHub):
  1. Taruh file credentials.json (dari Google Cloud Console) di folder yang sama dengan file ini.
  2. Jalankan:  python auth_google.py
  3. Browser terbuka -> pilih akun Google pemilik folder -> izinkan.
  4. Salin SELURUH teks yang dicetak di layar ke GitHub Secret bernama GOOGLE_TOKEN.
"""
import json
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/drive"]
HERE = Path(__file__).resolve().parent


def main():
    cred = HERE / "credentials.json"
    if not cred.exists():
        raise SystemExit("credentials.json tidak ditemukan di folder scripts/. Lihat README Langkah 3.")
    flow = InstalledAppFlow.from_client_secrets_file(str(cred), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    token = creds.to_json()
    (HERE / "token.json").write_text(token, encoding="utf-8")
    if not json.loads(token).get("refresh_token"):
        print("PERINGATAN: refresh_token kosong. Hapus akses aplikasi di myaccount.google.com/permissions lalu ulangi.")
    print("\n================ SALIN SEMUA TEKS DI BAWAH INI KE GITHUB SECRET 'GOOGLE_TOKEN' ================\n")
    print(token)
    print("\n==============================================================================================")
    print("Token juga tersimpan di scripts/token.json. JANGAN bagikan file ini ke siapa pun.")


if __name__ == "__main__":
    main()
