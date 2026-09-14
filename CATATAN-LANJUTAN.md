# Catatan Robot Forex

Keadaan per **12 September 2026**, ditulis supaya pekerjaan bisa dilanjutkan
tanpa mengukur ulang apa pun.

---

## Robot ini bagian dari tiga robot terpisah

| robot | folder di VPS | mengurus |
|---|---|---|
| crypto | `~/robot-crypto` | crypto spot + futures perpetual |
| **forex** | **`~/robot-forex`** | **5 simbol forex + emas** |
| saham | `~/robot-saham` | saham (kodebase berbeda sama sekali) |

Ketiganya memakai bot Telegram yang sama, dibedakan lewat judul pesan
(`notifikasi.judul` di config): "Sinyal Forex" dan "Sinyal Crypto".

### Isi `src/` sengaja identik dengan robot-crypto

Diperiksa dengan `md5sum` saat pemisahan: seluruh 12 berkas di `src/` sama
persis. Itu bukan kebetulan melainkan keputusan — memindahkan perbaikan bug
cukup dengan menyalin folder `src/`, tidak perlu menulis ulang:

```bash
cp ~/robot-crypto/src/*.py ~/robot-forex/src/         # atau sebaliknya
cd ~/robot-forex && ./venv/bin/python uji_mandiri.py  # wajib, harus 183 lolos
```

Yang berbeda hanya `config.yaml` (pasar mana yang aktif, database mana yang
dipakai, judul pesan) dan alat khusus futures yang tidak disertakan di sini
(`uji_leverage.py`, `uji_scalping.py`).

---

## Jadwal

```cron
35 7 * * *   $HOME/robot-forex/jalankan.sh                                   # laporan harian
5 * * * 1-5  $HOME/robot-forex/jalankan.sh --timeframe 1jam --diam-jika-kosong
```

Menitnya (`35`, `5`) sengaja digeser dari jadwal crypto yang di menit `30`
dan `0` — RAM VPS hanya 961 MB, jadi lebih baik tidak jalan bersamaan.

Laporan harian **tidak** memakai `--diam-jika-kosong`, jadi tiap pagi selalu
ada satu pesan. Hilangnya pesan itu sendiri adalah tanda bahaya. Jadwal tiap
jam memakainya supaya tidak membanjiri Telegram.

---

## Batas dari sumber data — bukan dari kode

| timeframe | tersedia? | riwayat |
|---|---|---|
| harian | ya | sangat panjang (USDJPY sejak 1996) |
| 4jam | ya | **730 hari terakhir saja** |
| 1jam | ya | **730 hari terakhir saja** |
| di bawah 1 jam | **tidak** | Yahoo tidak menyediakan forex |

Jadi scalping 15 menit memang mustahil di robot ini. Itu batas Yahoo.

**Volume forex selalu nol.** EURUSD=X punya 5.910 baris harian dan semuanya
volume 0 — Yahoo tidak melaporkan volume untuk pasangan mata uang. Hanya emas
(`GC=F`) yang punya volume sungguhan karena itu kontrak berjangka.

Akibatnya: `konfirmasi.volume` **tidak boleh** dinyalakan dengan
`lewati_jika_kosong: false` — itu akan mematikan seluruh sinyal robot ini.

---

## Hasil backtest — apa adanya

### Harian (bisa diuji dua periode)

`ikut_tren` adalah satu-satunya strategi yang pernah lolos aturan penerimaan
di forex: menang di periode lama **dan** periode baru. Besarnya
**0,47%/tahun lalu 0,10%/tahun** — di bawah bunga deposito. Lolos secara
statistik, tapi tidak berarti banyak secara ekonomi.

### 1 jam (2,7 tahun, **hanya satu periode**)

| strategi | per tahun | drawdown | transaksi | win% |
|---|---|---|---|---|
| `ikut_tren` | **+1,0%** | −10,1% | 764 | 31% |
| `tembus_batas` | +0,3% | −35,8% | 1.922 | 32% |
| `balik_rata2` | −0,5% | −10,9% | 251 | 52% |
| `struktur_harga` | −2,5% | −18,5% | 1.209 | 24% |
| beli & tahan | +7,8% | −29,0% | | |

**Aturan dua periode tidak bisa diterapkan di 1 jam, dan tidak akan pernah
bisa.** Data 1 jam hanya ada 730 hari ke belakang, sedangkan pemisah periode
ada di 2023-01-01 — periode lama kosong sama sekali. Jadi angka di atas belum
pernah diuji pada data yang tidak dipakai untuk memilihnya.

Bacalah dengan itu di kepala: `ikut_tren` 1 jam terlihat lebih baik daripada
harian (+1,0% vs +0,10%), tapi "terlihat lebih baik di satu periode" persis
bentuk kesimpulan yang aturan dua periode dibuat untuk mencegah.

Perhatikan juga `balik_rata2`: win rate-nya **paling tinggi (52%)** tapi
hasilnya **negatif**. Itu pengingat yang sudah berkali-kali muncul di project
ini — sering menang tidak sama dengan untung.

---

## Yang belum dikerjakan

- Lapisan `konfirmasi` (volume, MACD, imbalan:risiko, jeda) belum pernah
  diadu khusus di forex. Jalankan `uji_konfirmasi.py --pasar forex`.
- `struktur_harga` di forex belum diperiksa apakah jalur tembusannya mati
  total — seharusnya ya, karena butuh volume yang tidak ada di pasangan mata
  uang, sehingga hanya jalur pantulan yang bekerja.

## Yang tidak berubah

Robot **tidak pernah memasang order**. Ia hanya mengirim sinyal; eksekusi
tetap Anda lakukan sendiri.
