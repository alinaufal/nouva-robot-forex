# Robot Analisis Crypto & Forex

Alat bantu pribadi untuk memantau pasar crypto dan forex.
Program ini **memberi sinyal, bukan memasang order**. Keputusan dan
eksekusinya tetap di tangan Anda, lewat aplikasi bursa atau broker masing-masing.

**Stack:** Python 3.14 · pandas · yfinance · Binance public API · SQLite · Telegram

---

## Baca ini lebih dulu

Program ini sudah diuji pada data sungguhan, dan hasilnya **tidak mendukung
pemakaian untuk uang sungguhan**. Rinciannya ada di bagian
[Hasil pengujian](#hasil-pengujian) di bawah — jangan dilewati.

Aturan penerimaan ditetapkan **sebelum** hasilnya dilihat, supaya tidak ada
pembenaran yang dikarang belakangan:

> Strategi hanya layak dipakai kalau menang di **kedua** periode uji.
> Menang di periode lama tapi kalah di periode baru = ditolak.

Dari 12 kombinasi yang diuji, **satu** lolos aturan — dan keuntungannya
**0,4% per tahun**, jauh di bawah bunga deposito.

---

## Yang bisa dan tidak bisa dilakukan

| | |
|---|---|
| ✅ | Mengunduh harga crypto (Binance) dan forex/emas (Yahoo) ke database lokal |
| ✅ | Menghitung indikator teknikal dan mencari sinyal |
| ✅ | Menguji tiga strategi berdampingan pada dua periode terpisah |
| ✅ | Mengirim sinyal ke Telegram, termasuk ke grup atau channel |
| ✅ | Bekerja pada timeframe harian maupun 4 jam |
| ❌ | Memasang order otomatis — sengaja tidak dibuat |
| ❌ | Tahu posisi apa yang sedang Anda pegang |
| ❌ | Memperhitungkan biaya menginap (swap) posisi forex |

---

## Cara menjalankan

### Klik dua kali

| Berkas | Gunanya |
|---|---|
| **Jalankan Robot.bat** | Perbarui harga, cari sinyal, kirim notifikasi |
| **Adu Strategi.bat** | Bandingkan ketiga strategi pada data historis |

### Lewat terminal

```bash
pip install -r requirements.txt

python uji_mandiri.py              # uji tanpa jaringan, harus "SEMUA LOLOS"
python run_cek.py                  # seluruh watchlist
python run_cek.py --hanya BTCUSDT  # satu simbol saja
python run_cek.py --tanpa-kirim    # tampilkan saja, jangan kirim
python run_cek.py --timeframe 4jam
python run_backtest.py             # adu strategi
python run_backtest.py --rinci     # per simbol, bukan rata-rata
```

Pengambilan pertama mengunduh seluruh riwayat — ribuan baris per simbol,
beberapa menit. Sesudah itu hanya menambah lilin baru.

---

## Tiga strategi

### 1. Ikut tren — potong SMA
```
beli : SMA cepat memotong ke ATAS SMA lambat
       DAN harga di atas SMA tren
       DAN RSI belum jenuh beli
jual : SMA cepat memotong ke BAWAH SMA lambat
stop : harga masuk − 3 × ATR
```
Masuk telat, keluar telat. Kuat saat tren panjang, tergerus saat harga
bergerak mendatar.

> Penyaring RSI di strategi ini sangat galak: **sekitar 75–80% perpotongan
> naik ditolak** karena RSI sudah di atas 70 saat perpotongan terjadi. Itu
> memang disengaja, tapi akibatnya sinyal beli jarang sekali muncul.

### 2. Balik ke rata-rata — Bollinger + RSI
```
beli : harga menyentuh pita BAWAH Bollinger
       DAN RSI di bawah 30
       DAN harga masih di atas SMA tren
jual : harga kembali ke pita TENGAH
stop : harga masuk − 2 × ATR
```
Penyaring SMA tren wajib ada — tanpanya strategi ini membeli terus-menerus
sepanjang pasar rontok.

### 3. Tembus batas — Donchian
```
beli : harga menembus TERTINGGI 20 lilin sebelumnya
jual : harga menembus TERENDAH 10 lilin sebelumnya
stop : harga masuk − 3 × ATR
```
Aturan klasik "Turtle".

**Crypto spot hanya bisa beli** (tidak bisa dijual duluan).
**Forex dan futures bisa dua arah** — turunnya EURUSD sama saja dengan naiknya USD.

---

## Futures (perpetual USDT-M)

Sumbernya `fapi.binance.com`. **Tidak bisa dijangkau dari banyak jaringan
rumahan di Indonesia** — sudah diuji: laptop timeout, VPS lancar. Jadi futures
praktis hanya bisa dijalankan dari VPS.

Diatur di `config.yaml` bagian `futures:`. Setel `aktif: false` untuk mematikan.

### Tiga hal yang ikut dihitung

Backtest futures **tidak** memakai model spot yang dipoles. Tiga hal khas
futures dimodelkan sungguhan:

**1. Leverage.** Ukuran posisi boleh melebihi modal, sampai `leverage` kali.
Yang sering disalahpahami: leverage hanya menaikkan **plafon**, bukan memaksa
taruhan lebih besar. Selama aturan risiko 1% yang mengikat, hasilnya sama
persis dengan tanpa leverage. Leverage baru terasa saat batas ruginya sempit.

**2. Likuidasi.** Dihitung dari syarat margin bursa:

```
beli : harga_masuk × (1 − 1/leverage) ÷ (1 − maintenance)
jual : harga_masuk × (1 + 1/leverage) ÷ (1 + maintenance)
```

Leverage 10× pada harga masuk 100 berarti likuidasi di **90,45** — cukup harga
turun **9,55%**. Kerugiannya dibatasi margin yang disetor, sesuai cara bursa
menutup posisi.

> **Yang paling sering diabaikan orang:** kalau batas rugi ATR Anda lebih lebar
> daripada jarak ke likuidasi, batas rugi itu **tidak pernah terpakai** — Anda
> dilikuidasi lebih dulu. Program ini memeriksa keduanya dan menandai transaksi
> yang berakhir `LIKUIDASI`.

**3. Funding.** Perpetual futures menagih tiap 8 jam. Riwayat aslinya diunduh
dari Binance dan diterapkan per lilin, bukan diperkirakan.

Data sungguhan BTCUSDT setahun terakhir: rata-rata **+0,0031% per 8 jam**
= **+3,4% setahun**, dan **positif 835 dari 1096 kali**. Rate positif berarti
pemegang posisi **beli** yang membayar — jadi strategi yang kebanyakan membeli
menanggung biaya ini hampir sepanjang waktu.

### Aturan penerimaan untuk futures

Sama seperti crypto spot: harus mengalahkan sekadar memegang asetnya, di kedua
periode. Ditambah satu syarat mutlak — **satu likuidasi saja membuat
konfigurasi itu gugur**, berapa pun keuntungannya. Modal yang habis tidak bisa
menunggu pemulihan.

---

## Hasil pengujian

Diuji 8 September 2026. Modal Rp10 juta per simbol, risiko 1% per transaksi,
biaya transaksi dihitung, eksekusi selalu di harga pembukaan lilin berikutnya.

### Timeframe harian

**Crypto** (BTC, ETH, BNB, SOL, XRP)

| strategi | periode lama (4,6 th) | periode baru (3,7 th) | drawdown |
|---|---|---|---|
| ikut tren | 6,2%/thn | 1,6%/thn | −33% |
| balik rata-rata | −0,1%/thn | 0,0%/thn | −2% |
| tembus batas | 5,7%/thn | 3,7%/thn | −15% |
| **beli & tahan** | **52,9%/thn** | **49,0%/thn** | **−96%** |

**Forex** (EURUSD, GBPUSD, USDJPY, USDIDR, emas)

| strategi | periode lama (21,6 th) | periode baru (3,7 th) |
|---|---|---|
| ikut tren | 0,37%/thn | 0,47%/thn | ← satu-satunya yang lolos |
| balik rata-rata | −0,03%/thn | 0,13%/thn |
| tembus batas | 0,72%/thn | −0,31%/thn |

### Timeframe 4 jam

Crypto jauh lebih baik daripada harian, tapi tetap kalah telak:

| strategi | periode lama | periode baru | drawdown |
|---|---|---|---|
| ikut tren | 10,3%/thn | 4,5%/thn | −22% |
| tembus batas | 11,3%/thn | 7,4%/thn | −20% |
| **beli & tahan** | **53,5%/thn** | **49,0%/thn** | **−97%** |

Forex 4 jam tidak bisa diuji dua periode: Yahoo hanya menyediakan 730 hari
data intraday, semuanya sesudah tanggal pisah.

### Apa artinya

**Untuk crypto, tidak ada satu pun strategi yang mendekati sekadar membeli
lalu menahan.** Sebagian besar karenanya masuk akal: strategi hanya
mempertaruhkan 1% modal per transaksi (kira-kira 10% modal terpakai),
sedangkan beli-dan-tahan memakai 100% modal sepanjang waktu.

Tapi ada sisi lain yang jujur juga: **drawdown-nya jauh berbeda.**
Beli-dan-tahan crypto pernah turun **96%** dari puncaknya. Kalau Anda benar-benar
memegangnya sepanjang 2018 dan 2022, Rp10 juta pernah tinggal Rp400 ribu.
Strategi tembus batas 4 jam turunnya "hanya" 20%. Jadi kalimat yang jujur
bukan "strateginya jelek", melainkan **"strateginya menukar sebagian besar
keuntungan demi tidur yang lebih nyenyak"** — dan pertukaran itu, pada angka
di atas, terlalu mahal.

**Untuk forex, hasilnya mendekati nol.** Yang lolos aturan pun hanya
menghasilkan 0,4% per tahun selama 21 tahun. Deposito rupiah memberi 4–6%
per tahun tanpa risiko dan tanpa usaha.

**Kesimpulan saya: jangan pakai ini untuk uang sungguhan.** Pakailah sebagai
alat belajar, pemantau pasar, dan pengingat bahwa strategi yang terdengar
masuk akal belum tentu menghasilkan.

---

## Mengatur watchlist dan strategi

Semua di `config.yaml`, tanpa menyentuh kode.

```yaml
timeframe: harian          # atau "4jam"
strategi_aktif: ikut_tren  # ikut_tren | balik_rata2 | tembus_batas

crypto:
  watchlist: [BTCUSDT, ETHUSDT, ...]
  biaya: {fee_persen: 0.1}

forex:
  watchlist:
    - {simbol: "EURUSD=X", pip: 0.0001, spread_pip: 1.5}
```

> **Peringatan yang serius.** Mengubah angka-angka ini berulang kali sampai
> hasil backtest terlihat bagus bukan "mengoptimalkan strategi" — itu
> menemukan kebetulan. Makin sering Anda mengubahnya sambil melihat hasil,
> makin besar kemungkinan angka yang akhirnya Anda pilih hanya cocok untuk
> masa lalu dan gagal di masa depan.

### Catatan kode simbol yang sudah diperiksa langsung

| | |
|---|---|
| Crypto | pasangan Binance tanpa tanda, mis. `BTCUSDT` |
| Forex | akhiran `=X`, mis. `EURUSD=X`, `USDIDR=X` |
| Emas | **`GC=F`** — `XAUUSD=X` TIDAK ADA di Yahoo, hasilnya kosong |

---

## Notifikasi Telegram

Selama belum disiapkan, pesan hanya ditampilkan di layar — program tetap jalan.

1. Buka Telegram, cari **@BotFather**, kirim `/newbot`, ikuti langkahnya.
   BotFather memberi **token** seperti `123456789:AAE...`
2. Kirim satu pesan apa saja ke bot yang baru dibuat.
3. Buka di browser: `https://api.telegram.org/bot<TOKEN>/getUpdates` —
   cari angka pada `"chat":{"id":...}`. Itu **chat id** Anda.
4. Salin `.env.example` menjadi `.env`, lalu isi keduanya.

### Mengirim ke grup atau channel

`TELEGRAM_CHAT_ID` boleh berisi beberapa tujuan, dipisah koma:

```
TELEGRAM_CHAT_ID=987654321,-1001234567890
```

**Id grup dan channel selalu negatif**, biasanya diawali `-100`.

**Untuk grup:** tambahkan bot ke grup, kirim `/start@namabot_bot` di dalamnya
(harus menyebut nama botnya — bot tidak melihat pesan biasa di grup), lalu
buka `getUpdates`.

**Untuk channel:** bot harus dijadikan **administrator** dengan izin
**Post Messages**. Di `getUpdates`, carinya di bagian `channel_post`.

> Kalau sebuah grup biasa berubah menjadi *supergroup*, id-nya ikut berubah
> dan harus diperbarui.

---

## Menjalankan otomatis

Crypto buka 24/7 dan forex 24/5, jadi tidak ada "jam tutup bursa" seperti saham.

- **Timeframe harian** — lilin harian tertutup pukul **00:00 UTC = 07:00 WIB**.
  Jadwalkan sekitar **07:30 WIB**, tiap hari.
- **Timeframe 4 jam** — lilin tertutup tiap 4 jam mengikuti UTC
  (07:00, 11:00, 15:00, 19:00, 23:00, 03:00 WIB).

**Task Scheduler (Windows):**

1. *Create Basic Task* → nama `Robot Crypto Forex`, pemicu **Daily**, jam **07:30**
2. Action: *Start a program* → `Jalankan Otomatis.bat` di folder ini
3. Hasil tiap jalan tercatat di `data/log-harian.txt`

Berbeda dengan robot saham, **jangan** dibatasi hari kerja saja — crypto tetap
bergerak di akhir pekan.

---

## Struktur berkas

```
config.yaml            Watchlist, strategi, parameter, biaya, risiko
.env                   Token Telegram (jangan dibagikan)
uji_mandiri.py         56 pemeriksaan tanpa jaringan
run_cek.py             Perbarui -> cari sinyal -> notifikasi
run_backtest.py        Adu tiga strategi, dua periode
data/pasar.db          Database harga & riwayat sinyal
src/
  sumber_crypto.py     Binance (data-api.binance.vision)
  sumber_forex.py      Yahoo Finance
  pasar.py             Penyatu dua sumber + pembersihan cacat data
  indicators.py        SMA, EMA, RSI, MACD, ATR, Bollinger, Donchian
  strategy.py          Tiga strategi
  backtest.py          Mesin uji historis
  signals.py           Sinyal pada data terbaru
  notify.py            Telegram
  db.py, config.py     Database dan pemuat pengaturan
```

---

## Batasan yang harus Anda tahu

**Alamat Binance yang biasa dipakai orang tidak jalan di sini.**
`api.binance.com` ditolak dari jaringan ini (sertifikat SSL gagal
diverifikasi). Program memakai `data-api.binance.vision`, cermin resmi Binance
untuk data pasar. Jangan diganti.

**Data forex Yahoo punya cacat nyata.** Pada sebagian lilin, harga penutupan
berada di luar rentang high–low — mustahil menurut definisi, karena kalau
harga pernah menyentuh angka penutupan itu, high sudah pasti minimal setinggi
itu. Jumlahnya bukan sedikit:

| simbol | lilin | cacat | |
|---|---|---|---|
| USDIDR=X | 6.361 | **518** | **8,1%** |
| GC=F (emas) | 6.528 | **441** | **6,8%** |
| USDJPY=X | 7.741 | 276 | 3,6% |
| EURUSD=X | 5.907 | 128 | 2,2% |
| GBPUSD=X | 5.919 | 103 | 1,7% |
| seluruh crypto (Binance) | 15.114 | **0** | 0% |

Program merapikannya dengan melebarkan high/low secukupnya agar mencakup open
dan close; angka open dan close tidak pernah diubah. Data crypto dari Binance
tidak punya masalah ini sama sekali.

Perhatikan bahwa **rupiah dan emas justru yang terparah** — dua yang paling
mungkin Anda minati. Kalau suatu saat Anda memakai sumber data lain, periksa
hal yang sama sebelum mempercayainya.

**Menguji 12 kombinasi lalu memilih yang terbaik itu sendiri berisiko.**
Pemisahan dua periode mengurangi bahayanya, tidak menghapusnya.

**Harga Binance bukan harga yang Anda bayar.** Sumbernya pasangan USDT global.
Kalau Anda membeli di bursa lokal, harganya berbeda dan ada selisih kurs.

**Spread forex dianggap tetap.** Kenyataannya melebar saat rilis berita —
justru saat sinyal sering muncul. Jadi biaya sungguhan lebih besar daripada
yang dihitung di sini.

**Biaya menginap (swap) tidak dihitung.** Posisi forex yang ditahan berhari-hari
kena biaya ini tiap malam, dan itu bisa membalik hasil strategi yang tipis
seperti di atas.

**Leverage tidak dimodelkan.** Ukuran posisi dibatasi modal yang benar-benar
ada, jadi angka forex di sini konservatif dibanding akun ber-leverage — tapi
begitu juga risikonya.

**Program tidak tahu posisi Anda.** Sinyal "TUTUP" berarti syarat keluar
terpenuhi *seandainya* Anda sedang memegang posisi itu.

**Crypto di Indonesia diawasi OJK** sejak pengalihan dari Bappebti pada Januari
2025. Gunakan bursa yang terdaftar.

**"Robot trading" adalah kategori penipuan yang terkenal di Indonesia.**
Alat ini aman karena dipakai sendiri dan tidak pernah memasang order. Kalau
sinyalnya suatu saat dibagikan ke orang lain, itu berubah menjadi nasihat
investasi yang memerlukan izin OJK.

**Ini bukan rekomendasi investasi.** Program hanya menjalankan aturan matematis
pada data harga. Ia tidak tahu kondisi pasar, berita, maupun keadaan Anda.
Semua keputusan dan risikonya milik Anda.
