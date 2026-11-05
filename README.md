# Bot Laporan Keuangan IDX (Bot 1 → Bot 2 → Bot 3)

Sistem otomatis gratis (tanpa AI/token) yang:

1. **Bot 1** mengumpulkan laporan keuangan (Excel + PDF) dari **IDX** dan dari folder **Inbox** di Google Drive, lalu menyortirnya ke folder per emiten.
2. **Bot 2** membaca angka (termasuk rincian segmen & komponen biaya), menghitung kuartal murni, QoQ, YoY, rasio, lalu menulis **Excel per emiten**.
3. **Bot 3** membuat **gambar tabel + narasi analisis** dan mengirimnya ke **Discord**.

```
📁 Folder Induk (Google Drive)
├── 📁 Inbox                  ← upload file manual di sini
│   ├── 📁 _Tidak Dikenali    ← file yang gagal dikenali
│   └── 📁 _Duplikat
├── 📁 Emiten
│   └── 📁 AADI
│       ├── 📊 AADI - Financial Data.xlsx
│       └── 📁 Financial Update - 3Q26  (Excel, PDF, gambar tabel, narasi)
└── 📁 _Sistem                ← watchlist, gaya bahasa, catatan analis, data
```

Waktu setup: sekitar 1–2 jam, cukup sekali. Ikuti langkah berurutan.

---

## Langkah 1 — Webhook Discord

1. Buka server Discord → klik kanan channel tujuan → **Edit Channel**.
2. **Integrations → Webhooks → New Webhook** → **Copy Webhook URL**.
3. Simpan URL ini di catatan sementara (dipakai di Langkah 6). Jangan bagikan ke siapa pun.

## Langkah 2 — Folder Google Drive

1. Buat folder baru di Google Drive, misalnya `Laporan Keuangan IDX`.
2. Buka folder itu. Salin bagian URL setelah `/folders/`.
   Contoh: `https://drive.google.com/drive/folders/1AbCdEfGh` → ID-nya `1AbCdEfGh`.
3. Simpan ID ini (dipakai di Langkah 6 dan 8). Subfolder lain dibuat otomatis oleh bot.

## Langkah 3 — Google Cloud (akses Drive untuk bot)

1. Buka https://console.cloud.google.com → login dengan akun pemilik folder.
2. Klik pemilih project (kiri atas) → **New Project** → nama `idx-bot` → **Create**.
3. Menu ☰ → **APIs & Services → Library** → cari **Google Drive API** → **Enable**.
4. Menu **APIs & Services → OAuth consent screen** (atau "Google Auth Platform"):
   - User type **External**, isi nama aplikasi `idx-bot` dan email Anda → simpan.
   - Bagian **Audience / Test users**: tambahkan email Anda.
   - **PENTING:** klik **Publish app** sampai status menjadi **In production**.
     Kalau tetap "Testing", token kedaluwarsa tiap 7 hari dan bot berhenti.
5. Menu **Credentials → Create Credentials → OAuth client ID**:
   - Application type: **Desktop app** → **Create** → **Download JSON**.
   - Ganti nama file menjadi `credentials.json`.

## Langkah 4 — Buat token Google (sekali, di komputer Anda)

1. Install Python 3.10+ dari https://python.org (Windows: centang **Add Python to PATH**).
2. Ekstrak zip bot ini. Taruh `credentials.json` di folder `scripts/`.
3. Buka Terminal / Command Prompt di folder bot, jalankan:
   ```
   pip install google-auth-oauthlib
   python scripts/auth_google.py
   ```
4. Browser terbuka → pilih akun Google → muncul peringatan *"Google hasn't verified this app"* →
   klik **Advanced → Go to idx-bot (unsafe)** → **Continue**. Ini aman karena aplikasinya milik Anda sendiri.
5. Terminal menampilkan teks panjang berawalan `{"token": ...`. **Salin seluruhnya** (dipakai di Langkah 6).

## Langkah 5 — Repository GitHub

1. Login https://github.com → **New repository** → nama `idx-financial-bot` → pilih **Private** → **Create**.
2. Klik **uploading an existing file** → seret **semua isi** folder bot (bukan folder luarnya) → **Commit changes**.
3. Cek apakah folder `.github/workflows/pipeline.yml` ikut terunggah (folder berawalan titik kadang tidak ikut saat seret-lepas). Jika tidak ada:
   **Add file → Create new file** → nama file `.github/workflows/pipeline.yml` → tempel isi file tersebut dari zip → **Commit**.
4. Jangan unggah `credentials.json` dan `token.json`.

## Langkah 6 — GitHub Secrets

Di repo: **Settings → Secrets and variables → Actions → New repository secret**. Buat tiga secret:

| Name | Isi |
|---|---|
| `GOOGLE_TOKEN` | Teks token dari Langkah 4 |
| `DRIVE_ROOT_FOLDER_ID` | ID folder dari Langkah 2 |
| `DISCORD_WEBHOOK_URL` | URL webhook dari Langkah 1 |

## Langkah 7 — Tes pertama

1. Upload beberapa file laporan ke folder **Inbox** di Drive (misalnya file AADI & BRIS yang Anda punya).
2. Di GitHub: tab **Actions** → **IDX Financial Bot** → **Run workflow** → **Run workflow**.
3. Tunggu sampai centang hijau (±5–15 menit). Hasilnya:
   - File pindah ke `Emiten/KODE/Financial Update - xxx`
   - Excel `KODE - Financial Data.xlsx` terbentuk
   - Pesan status & laporan muncul di Discord (narasi hanya untuk periode **3Q26 ke atas**)
4. Kalau merah (gagal): klik job yang gagal, salin pesan error terakhir, lalu lihat bagian *Masalah umum*.

## Langkah 8 — Pemicu otomatis Inbox (Google Apps Script)

Supaya file yang Anda upload langsung diproses dalam hitungan menit tanpa menghabiskan jatah GitHub.

1. Buat token GitHub: **Settings (profil) → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**.
   - Repository access: **Only select repositories** → `idx-financial-bot`
   - Permissions → Repository → **Contents: Read and write**
   - Generate, lalu salin token.
2. Buka https://script.google.com → **New project** → hapus isi bawaan → tempel isi `apps_script/InboxWatcher.gs` → simpan.
3. ⚙️ **Project Settings → Script properties → Add**:
   - `ROOT_FOLDER_ID` = ID folder Langkah 2
   - `GITHUB_REPO` = `namaakun/idx-financial-bot`
   - `GITHUB_TOKEN` = token GitHub tadi
4. Kembali ke editor → pilih fungsi **setup** → **Run** → izinkan akses (Advanced → Go to … → Allow).
5. Selesai. Apps Script memeriksa Inbox tiap 5 menit.

## Langkah 9 — Jadwal otomatis

Tidak perlu diatur. Workflow berjalan tiap 2 jam. Scraper IDX berjalan setiap kali di musim laporan
(Mar–Mei, Jul–Sep, Okt–Nov) dan maksimal tiap 12 jam di luar musim. Atur di `config/settings.yaml`.

---

## Pemakaian sehari-hari

| Ingin… | Caranya |
|---|---|
| Menambah laporan manual | Upload ke **Inbox**. Nama ideal: nama asli IDX (`FinancialStatement-2026-III-AADI.xlsx`) atau `AADI_3Q26.pdf`. Bisa juga taruh di subfolder `Inbox/AADI/`. |
| Mengubah watchlist | Edit `_Sistem/watchlist.csv` di Drive (kolom: ticker, nama, sektor). |
| Menambah kata yang dilarang | Edit `_Sistem/style_guide.yaml` di Drive. |
| Menyisipkan konteks kualitatif | Tambah baris di `_Sistem/catatan_analis.txt`: `AADI \| 3Q26 \| kalimat Anda`. |
| Mengirim ulang narasi | Actions → Run workflow → isi `tickers` = AADI, centang `force`, `periods` = 3Q26. |
| Mengubah warna tema | Edit bagian `theme` di `config/settings.yaml` (kode warna hex, default merah Victoria `C00000`). |
| Mengatur pengelompokan komponen | Edit `config/groups.yaml` (kata kunci per kelompok) dan `table.max_groups` di `config/settings.yaml`. |
| Memperbaiki terjemahan label tabel | Tambah baris di bagian `exact` pada `config/labels_en.yaml` (label Indonesia: label Inggris). |
| File masuk `_Tidak Dikenali` | Ganti nama dengan pola `KODE_3Q26.pdf`, pindahkan kembali ke Inbox. |

## Kalau scraper diblok (pesan merah "Scraper IDX diblok" di Discord)

Jalur Inbox tetap berjalan normal. Untuk menjalankan scraper dari komputer/VPS sendiri:

1. Repo → **Settings → Actions → Runners → New self-hosted runner** → ikuti perintah yang ditampilkan di komputer Anda (Windows/Mac/Linux), lalu jalankan `run`.
2. Repo → **Settings → Secrets and variables → Actions → tab Variables → New variable**:
   `COLLECTOR_RUNNER` = `self-hosted`.
3. Komputer itu harus menyala saat jadwal berjalan. Hapus variable ini untuk kembali ke server GitHub.

## Masalah umum

| Gejala | Solusi |
|---|---|
| `invalid_grant` / token error | Pastikan OAuth consent **In production**, ulangi Langkah 4, perbarui secret `GOOGLE_TOKEN`. |
| `DRIVE_ROOT_FOLDER_ID / GOOGLE_TOKEN belum diisi` | Cek nama secret persis sama (huruf besar). |
| Tidak ada pesan Discord | Cek secret `DISCORD_WEBHOOK_URL`; pastikan webhook belum dihapus. |
| "pos wajib tidak ditemukan" | Template emiten berbeda; kirim file Excel-nya ke pengembang untuk penyesuaian pemetaan. |
| Segmen tidak muncul di tabel | Tabel segmen di PDF tidak terbaca atau tidak cocok dengan total Excel; data utama tetap diproses. |
| Apps Script tidak memicu | Cek Script properties dan izin token (Contents: Read and write). Lihat log di menu Executions. |

## Catatan metodologi

- Kuartal murni = kumulatif periode berjalan − kumulatif periode sebelumnya. Pembanding YoY memakai kolom pembanding laporan terbaru (sudah disajikan ulang).
- Operating Profit non-bank = laba kotor − beban penjualan − beban umum & administrasi.
- Mata uang sesuai pelaporan (Rp miliar / USD juta). n.m. = tidak bermakna (basis nol/negatif atau berbalik rugi).
- Data operasional (volume, NPL/NPF, CAR) tidak tersedia di laporan keuangan standar, sehingga belum dibahas.
- Narasi bersifat otomatis berbasis aturan; review beberapa minggu pertama sebelum disebarluaskan.

## Uji coba lokal (opsional, butuh Python)

```
pip install -r requirements.txt
STORAGE=local LOCAL_ROOT=./local_drive DISCORD_DRY_RUN=true python main.py run-all --no-scrape
```
Taruh file contoh di `local_drive/Inbox/`. Hasil "pesan Discord" tersimpan di `.work/discord_outbox/`.

## Struktur kode

| Folder | Isi |
|---|---|
| `bot1_collector/` | Pengenalan file, pemrosesan Inbox, scraper IDX |
| `bot2_extractor/` | Pembaca Excel IDX, pembaca segmen PDF, perhitungan, penulis Excel |
| `bot3_narrator/` | Mesin narasi, gambar tabel, pengiriman |
| `core/` | Konfigurasi, Google Drive, Discord, format angka |
| `config/` | Pengaturan, watchlist, gaya bahasa, catatan analis |
| `apps_script/` | Pemantau Inbox |
| `.github/workflows/` | Jadwal & alur GitHub Actions |

## Opsi tanpa Google Cloud Console (jembatan Apps Script)

Jika secret `BRIDGE_URL` diisi, bot otomatis memakai jembatan Apps Script (`apps_script/DriveBridge.gs`)
alih-alih Google Drive API. Langkah 3, Langkah 4, serta secret `GOOGLE_TOKEN` dan `DRIVE_ROOT_FOLDER_ID` tidak diperlukan.
Secret yang dibutuhkan: `BRIDGE_URL`, `BRIDGE_KEY`, `DISCORD_WEBHOOK_URL`. Batas ukuran file lewat jembatan: 35 MB.
Setiap kali kode `DriveBridge.gs` diubah, buat versi deployment baru (Deploy → Manage deployments → Edit → New version).


## Perubahan versi 2 (ringkas)

- Discord hanya menerima foto tabel + narasi (tanpa lampiran Excel, tanpa pesan status).
- Pesan status kini dicatat di Drive: `_Sistem/log/TANGGAL/`.
- Jadwal: setiap 15 menit pukul 07.00–17.30 WIB. Inbox adalah jalur utama; scraper IDX nonaktif (aktifkan di `config/settings.yaml`).
- Pembanding tahun lalu selalu basis restated; laba inti (continuing) dipisah dari operasi yang dihentikan.
- Validasi otomatis PASS/WARN/FAIL (toleransi 3%); tabel FAIL tidak dikirim. Riwayat ada di sheet **Validasi** pada Excel emiten.
- Banyak emiten dihitung paralel, lalu dikirim berurutan per emiten.
- Laporan yang dulu terkirim tanpa data kuartalan otomatis dikirim ulang satu kali saat data lengkap.
- Kamus pos laporan lintas sektor: `config/line_items.yaml`. Regression test: `tests/run_regression.py`.
