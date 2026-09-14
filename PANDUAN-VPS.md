# Menjalankan di VPS

Supaya sinyal tetap dikirim walau laptop mati.

---

## Sebelum mulai: apakah Anda benar-benar butuh VPS?

Robot ini jalan **satu kali sehari selama beberapa menit**. Menyewa server 24
jam untuk itu memang boros, tapi juga paling sederhana untuk dipahami dan
dirawat. Dua alternatif yang lebih murah:

| Pilihan | Biaya | Cocok kalau |
|---|---|---|
| **VPS** | Rp30–75rb/bulan | Anda ingin satu tempat yang jelas, mudah diperiksa, dan sekalian belajar Linux |
| **Raspberry Pi / mini PC di rumah** | sekali beli, listrik ±3W | Internet rumah Anda stabil dan jarang mati lampu |
| **HP Android bekas** | gratis | Sudah ada barangnya, tapi paling rewel disiapkan |

Panduan ini memakai VPS. Kalau Anda pilih Raspberry Pi, langkah 3 sampai 7
sama persis — hanya langkah 1 dan 2 yang berbeda.

---

## 1. Sewa VPS

**Spesifikasi yang cukup:**

| | |
|---|---|
| CPU | 1 vCPU |
| RAM | **1 GB minimum** — 512 MB berisiko kehabisan memori saat pandas mengolah data |
| Disk | 10–25 GB (database sekarang 30 MB, tumbuh pelan) |
| OS | **Ubuntu 24.04 LTS** |

**Penyedia:**

- *Lokal* (bayar pakai rupiah, transfer bank/QRIS, dukungan bahasa Indonesia):
  IDCloudHost, Biznet Gio, Domainesia, Rumahweb — sekitar Rp30–75rb/bulan
- *Luar* (perlu kartu kredit/debit internasional): Hetzner, Contabo, Vultr,
  DigitalOcean — sekitar €4–6 atau $5–6/bulan
- *Gratis*: Oracle Cloud "Always Free" — benar-benar gratis selamanya, tapi
  pendaftarannya sering ditolak dari Indonesia dan akunnya bisa ditarik kalau
  lama menganggur. Jangan dijadikan andalan.

Lokasi server **tidak penting** untuk robot ini — ia hanya mengunduh data
sekali sehari, bukan mengejar kecepatan order.

Setelah menyewa, Anda dapat **alamat IP** dan **password root** (atau kunci SSH).

---

## 2. Masuk ke VPS

Windows 10/11 sudah punya SSH bawaan. Buka **PowerShell** di laptop:

```powershell
ssh NAMA_USER@ALAMAT_IP_ANDA
```

Ganti `NAMA_USER` dengan username yang Anda isi saat membuat instance
(di Biznet Gio ini ditanyakan di kolom **SSH/Console Username**).

> **Biznet Gio tidak memberi akses `root` langsung.** Anda masuk sebagai user
> biasa, lalu menaikkan hak akses dengan `sudo` di depan perintah yang perlu.
> Itu sebabnya hampir semua perintah di panduan ini diawali `sudo`.

Ketik `yes` saat ditanya soal fingerprint. Kalau memakai SSH key, Anda tidak
akan dimintai password sama sekali — langsung masuk.

Tandanya berhasil: teks di kiri berubah dari `PS C:\Users\NAMA_ANDA>` menjadi
sesuatu seperti `NAMA_USER@robot-crypto:~$`.

---

## 3. Siapkan Python

```bash
sudo apt update
sudo apt install -y python3.12-venv unzip
```

> **Kenapa `python3.12-venv`, bukan `python3-venv`?** Ubuntu 24.04 sudah
> membawa Python 3.12, tapi bagian pembuat venv-nya dipisah ke paket
> tersendiri. Tanpa paket ini, `python3 -m venv` seolah-olah ada — perintah
> `--help`-nya jalan — lalu gagal di tengah dengan pesan
> `ensurepip is not available`. Ini terlihat seperti kerusakan padahal hanya
> paket yang kurang.

Kalau `sudo` meminta password, itu **password console** yang Anda buat saat
memesan VPS. Password tidak terlihat saat diketik; itu normal.

**Periksa zona waktunya:**

```bash
date        # harus menunjukkan WIB
```

Kalau ternyata sudah WIB, lewati saja. Biznet Gio sudah menyetel
`Asia/Jakarta` sejak awal. Kalau penyedia lain memberi UTC:

```bash
sudo timedatectl set-timezone Asia/Jakarta
```

---

## 4. Kirim project dari laptop ke VPS

Di **PowerShell laptop** (bukan di VPS), buat dulu paket bersih tanpa database
dan tanpa berkas sementara — database akan dibangun ulang di VPS:

```powershell
$src = "C:\Users\NAMA_ANDA\Downloads\Robot Trading Crypto dan Forex"
$tmp = "$env:TEMP\robot-kirim"
Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
Copy-Item $src $tmp -Recurse
Remove-Item "$tmp\data","$tmp\__pycache__","$tmp\src\__pycache__" -Recurse -Force -ErrorAction SilentlyContinue
Compress-Archive "$tmp\*" "$env:TEMP\robot.zip" -Force
scp "$env:TEMP\robot.zip" NAMA_USER@ALAMAT_IP_ANDA:~/
```

Lalu di **VPS**:

```bash
sudo apt install -y unzip
mkdir -p ~/robot-crypto
unzip ~/robot.zip -d ~/robot-crypto
cd ~/robot-crypto
ls          # pastikan run_cek.py, config.yaml, src/ ada di sini
```

> **Kenapa di folder rumah, bukan `/opt`?** Karena Biznet memberi Anda user
> biasa, bukan `root`. Folder rumah (`~`) sudah milik Anda sepenuhnya, jadi
> venv, database, dan cron semuanya jalan tanpa `sudo` sama sekali. Menaruhnya
> di `/opt` bisa saja, tapi menambah urusan izin akses tanpa manfaat.

> Kalau `ls` menunjukkan satu folder lagi di dalamnya, masuk ke folder itu
> dan pindahkan isinya ke atas — struktur foldernya harus rata.

---

## 5. Pasang pustaka di dalam venv

**Jangan lewati bagian venv ini.** Ubuntu 24.04 menolak `pip install` langsung
ke sistem dengan pesan `error: externally-managed-environment`. Itu bukan
kerusakan, melainkan perlindungan bawaan Ubuntu.

```bash
cd ~/robot-crypto
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt
```

Uji dulu tanpa jaringan — harus keluar "SEMUA LOLOS":

```bash
./venv/bin/python uji_mandiri.py
```

---

## 6. Buat berkas .env di VPS

`.env` **tidak ikut** dalam paket tadi (memang sengaja — isinya rahasia dan
tercantum di `.gitignore`). Buat langsung di VPS:

```bash
nano ~/robot-crypto/.env
```

Isi dua baris ini, lalu **Ctrl+O**, **Enter**, **Ctrl+X**:

```
TELEGRAM_BOT_TOKEN=token_dari_BotFather
TELEGRAM_CHAT_ID=987654321,-1001234567890
```

Kunci berkasnya supaya hanya bisa dibaca pemiliknya:

```bash
chmod 600 ~/robot-crypto/.env
```

Sekarang jalankan sekali secara manual. Unduhan pertama makan waktu beberapa
menit karena mengambil seluruh riwayat:

```bash
cd ~/robot-crypto
./venv/bin/python run_cek.py
```

Kalau pesan masuk ke Telegram, VPS-nya sudah benar.

---

## 7. Jadwalkan dengan cron

```bash
chmod +x ~/robot-crypto/jalankan.sh
crontab -e
```

Pilih editor `nano` kalau ditanya, lalu tambahkan baris ini di paling bawah:

```cron
30 7 * * * $HOME/robot-crypto/jalankan.sh
```

Artinya: **setiap hari jam 07:30 WIB** (karena zona waktu sudah diatur di
langkah 3). Lilin harian crypto tertutup 00:00 UTC = 07:00 WIB, jadi jam 07:30
sudah aman.

> Di dalam crontab dipakai `$HOME`, bukan `~`. Keduanya sering bekerja, tapi
> `$HOME` lebih dapat diandalkan karena cron menyetel variabel itu sendiri.

**Berbeda dengan robot saham, jangan dibatasi hari kerja** — crypto tetap
bergerak di akhir pekan.

Untuk timeframe 4 jam, ganti barisnya jadi:

```cron
5 3,7,11,15,19,23 * * * $HOME/robot-crypto/jalankan.sh
```

Jadwal robot forex (folder `~/robot-forex`) — laporan pagi, pemeriksaan tiap
jam saat pasar buka, dan pemeriksaan Sabtu dini hari:

```cron
35 7 * * * $HOME/robot-forex/jalankan.sh
5 * * * 1-5 $HOME/robot-forex/jalankan.sh --timeframe 1jam --diam-jika-kosong
5 0-6 * * 6 $HOME/robot-forex/jalankan.sh --timeframe 1jam --diam-jika-kosong
```

Baris ketiga ada karena pasar forex dan emas baru tutup Jumat 17:00 New York —
Sabtu 04:00 WIB di musim panas, 05:00 WIB di musim dingin. Tanpa baris itu,
lilin-lilin terakhir hari Jumat baru diperiksa Senin 00:05 WIB, sekitar 45 jam
terlambat.

Periksa jadwal yang tersimpan:

```bash
crontab -l
```

---

### Laptop boleh dimatikan — tapi perhatikan satu hal

Setelah cron terpasang, laptop Anda **tidak dibutuhkan lagi**. Boleh dimatikan,
ditutup, dibawa pergi, atau internetnya diputus — VPS tetap jalan sendiri.

Tapi ada beda penting antara **cron** dan **perintah yang Anda ketik sendiri**:

| Yang berjalan | Kalau SSH terputus / laptop mati |
|---|---|
| Tugas cron | **Tetap jalan.** Cron milik VPS, bukan milik sesi Anda |
| Perintah yang sedang Anda ketik di SSH | **Ikut mati** bersama sesinya |

Ini penting di **langkah 6**, saat menjalankan `run_cek.py` pertama kali:
unduhan awal memakan beberapa menit karena mengambil seluruh riwayat. Kalau
laptop Anda tertidur atau Wi-Fi terputus di tengah jalan, prosesnya ikut
berhenti dan datanya tidak lengkap.

Supaya aman, jalankan unduhan pertama dengan `nohup` agar ia lanjut sendiri
walau sesinya putus:

```bash
cd ~/robot-crypto
nohup ./venv/bin/python run_cek.py > data/unduhan-pertama.log 2>&1 &
```

Pantau kemajuannya kapan saja, bahkan sesudah login ulang:

```bash
tail -f ~/robot-crypto/data/unduhan-pertama.log
```

Tekan **Ctrl+C** untuk berhenti memantau — itu hanya menghentikan tampilannya,
bukan unduhannya.

## 8. Matikan Task Scheduler di laptop

**Ini penting dan mudah terlupa.** Kalau laptop dan VPS sama-sama menjalankan
robot, Anda akan menerima **pesan ganda** — dan lebih buruk lagi, keduanya
punya database sendiri-sendiri, sehingga penyaring "sinyal jangan berulang"
tidak saling tahu. Sinyal yang sama akan dikirim dua kali dari dua tempat.

Di laptop: buka **Task Scheduler** → cari tugas robotnya → klik kanan →
**Disable** (atau **Delete**).

Pilih **satu tempat saja** untuk menjalankan robot.

---

## Memeriksa dan merawat

```bash
# Apakah tadi malam jalan?
tail -40 ~/robot-crypto/data/log-harian.txt

# Apakah cron benar-benar memicunya?
grep CRON /var/log/syslog | tail -20

# Ukuran database
du -h ~/robot-crypto/data/pasar.db
```

Log otomatis dipangkas 20.000 baris terakhir oleh `jalankan.sh` (sekitar
sepuluh hari untuk jadwal 15 menit), jadi tidak akan memenuhi disk.

Kalau robot **gagal jalan**, `jalankan.sh` mengirim satu pesan Telegram, lalu
satu pesan lagi saat robot jalan normal kembali — kegagalan beruntun tidak
dikabarkan berulang. Jejak setiap kegagalan disimpan terpisah:

```bash
tail -20 ~/robot-crypto/data/riwayat-gagal.txt
```

Kalau yang bermasalah **datanya** (Binance atau Yahoo tidak bisa dihubungi,
atau lilin terakhirnya sudah terlalu tua), robot tetap jalan dan pesannya
memuat bagian **⚠ DATA BERMASALAH** di paling atas.

---

## Kalau ada masalah

**Cron tidak jalan padahal manual berhasil.**
Cron memakai PATH yang sangat terbatas. Karena itu `jalankan.sh` memanggil
`./venv/bin/python`, bukan `python`. Jangan diganti jadi `python` saja.

**`bad interpreter: No such file or directory`.**
Berkas `.sh` tersimpan dengan akhiran baris Windows (CRLF). Perbaiki:

```bash
sudo apt install -y dos2unix && dos2unix ~/robot-crypto/jalankan.sh
```

**`Permission denied` saat cron menjalankannya.**
`chmod +x ~/robot-crypto/jalankan.sh` terlewat.

**Program terbunuh sendiri saat mengunduh (`Killed`).**
RAM habis. Tambah swap 1 GB:

```bash
fallocate -l 1G /swapfile && chmod 600 /swapfile
mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
```

**Waktunya meleset beberapa jam.**
Zona waktu belum diatur. Ulangi `timedatectl set-timezone Asia/Jakarta`,
lalu periksa dengan `date`.

---

## Menjalankan robot saham sekaligus

Langkahnya sama persis, hanya foldernya berbeda (`~/robot-saham`). Yang perlu
diperhatikan cuma empat hal:

| | Robot crypto/forex | Robot saham |
|---|---|---|
| Folder | `~/robot-crypto` | `~/robot-saham` |
| Skrip | `run_cek.py` | **`run_daily.py`** |
| Jadwal | tiap hari 07:30 | **16:30, Senin–Jumat** |
| Alasan jadwal | lilin harian tutup 00:00 UTC | bursa BEI tutup 16:00 WIB |

Masing-masing folder punya `jalankan.sh` sendiri yang sudah memanggil skrip
yang benar — jangan menyalin `jalankan.sh` dari satu folder ke folder lain
tanpa mengganti nama skripnya.

Baris cron untuk robot saham:

```cron
30 16 * * 1-5 $HOME/robot-saham/jalankan.sh
```

`1-5` berarti Senin sampai Jumat. Bursa tutup di akhir pekan, jadi tidak ada
gunanya jalan di hari Sabtu–Minggu — berbeda dengan crypto yang buka terus.

Menambah baris cron tanpa menghapus yang sudah ada:

```bash
( crontab -l 2>/dev/null | grep -v 'robot-saham/jalankan.sh' ; \
  echo '30 16 * * 1-5 $HOME/robot-saham/jalankan.sh' ) | crontab -
```

Pola `grep -v` di depan membuat perintah ini aman diulang — jadwal lama untuk
folder yang sama dibuang dulu, sehingga tidak pernah tercipta jadwal ganda.

### Robot saham perlu satu penyesuaian tambahan

Berkas `portfolio_bonds.yaml` berisi daftar obligasi. Kalau isinya masih
**contoh bawaan** (ORI025 dan SR021), robot akan mengirimkan pengingat kupon
untuk obligasi yang tidak Anda miliki — tiap hari, ke Telegram Anda. Ganti
dengan kepemilikan sungguhan, atau kosongkan daftarnya:

```yaml
obligasi: []
```
