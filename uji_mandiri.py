"""Uji tanpa jaringan — Langkah 1.

Semuanya memakai data buatan, jadi bisa dijalankan berkali-kali tanpa
mengunduh apa pun. Tujuannya menangkap kesalahan hitung SEBELUM data sungguhan
ikut campur, karena kalau angkanya salah di sini, seluruh backtest ikut salah
tanpa kelihatan.

Dua uji di bawah ini ada karena bug yang benar-benar pernah terjadi:
  * "perpotongan berselang-seling" — dulu `.shift()` pada kolom boolean
    membuat sinyal beli muncul setiap hari (36 palsu vs 3 asli)
  * "Donchian mengecualikan lilin sekarang" — tanpa `.shift(1)`, batas
    tertinggi ikut menghitung lilin yang sedang dinilai
"""
import contextlib
import io
import sqlite3
import sys
import types
from datetime import datetime, timezone

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src import backtest, db, indicators as ind, pasar, strategy, waktu  # noqa: E402

LOLOS = 0
GAGAL = 0


def cek(nama: str, kondisi: bool, detail: str = "") -> None:
    global LOLOS, GAGAL
    if kondisi:
        LOLOS += 1
        print(f"  ok   {nama}" + (f"  ({detail})" if detail else ""))
    else:
        GAGAL += 1
        print(f"  GAGAL {nama}" + (f"  -> {detail}" if detail else ""))


def data_gelombang(n: int = 600, awal: float = 100.0,
                   benih: int = 42, freq: str = "D") -> pd.DataFrame:
    """Harga buatan: gelombang naik-turun, tren naik tipis, plus derau acak.

    Dua hal yang sengaja dibuat begini:

    * **Bukan garis lurus turun.** Percobaan pertama dulu memakai harga yang
      menurun tetap, dan harganya menembus nol — hasilnya jadi tidak masuk akal.
    * **Harus ada derau.** Gelombang sinus murni membuat perpotongan SMA selalu
      jatuh tepat di kemiringan paling curam, yaitu saat RSI pasti sedang
      maksimum. Akibatnya penyaring RSI menolak SEMUA sinyal dan strategi ikut
      tren terlihat rusak padahal tidak. Derau membuat waktu perpotongan tidak
      lagi terkunci pada puncak RSI, seperti pasar sungguhan.

    Derau ditambahkan sebagai pengali eksponensial supaya harga dijamin tetap
    positif, dan benihnya tetap agar hasil ujinya bisa diulang.
    """
    rng = np.random.default_rng(benih)
    t = np.arange(n)
    dasar = awal * (1 + 0.18 * np.sin(t / 23) + 0.10 * np.sin(t / 7) + 0.0009 * t)
    jalan_acak = rng.normal(0, 0.008, n).cumsum() * 0.5
    close = pd.Series(dasar * np.exp(jalan_acak),
                      index=pd.date_range("2020-01-01", periods=n, freq=freq))

    open_ = close.shift(1).fillna(close.iloc[0])
    rentang = np.abs(rng.normal(0.006, 0.003, n)) + 0.002
    high = pd.concat([open_, close], axis=1).max(axis=1) * (1 + rentang)
    low = pd.concat([open_, close], axis=1).min(axis=1) * (1 - rentang)
    return pd.DataFrame({
        "open": open_, "high": high, "low": low, "close": close,
        "volume": 1000.0,
    })


P_KECIL = {
    "ikut_tren": dict(sma_cepat=10, sma_lambat=25, sma_tren=50,
                      rsi_periode=14, rsi_maks_beli=70,
                      atr_periode=14, atr_pengali_stop=3.0),
    "balik_rata2": dict(bb_periode=20, bb_k=2.0, rsi_periode=14,
                        rsi_maks_beli=35, sma_tren=50,
                        atr_periode=14, atr_pengali_stop=2.0),
    "tembus_batas": dict(donchian_masuk=20, donchian_keluar=10, sma_tren=50,
                         atr_periode=14, atr_pengali_stop=3.0),
    "struktur_harga": dict(pivot_kiri=3, pivot_kanan=3, toleransi_atr=0.5,
                           maks_uji=5, maks_level=10, min_uji=2, dekat_atr=0.5,
                           penyangga_atr=0.5, rr_minimal=1.5,
                           rsi_periode=14, rsi_batas_beli=45,
                           macd_cepat=12, macd_lambat=26, macd_signal=9,
                           volume_periode=20, volume_pengali=1.5,
                           sma_tren=50, atr_periode=14, atr_pengali_stop=3.0),
}


# Parameter strategi scalping untuk uji. Sengaja TIDAK ditaruh di P_KECIL:
# uji_strategi() memperlakukan setiap kunci di sana sebagai nama strategi,
# dan strategi ini perlu data jauh lebih panjang daripada yang dipakai di situ.
P_SCALPING = dict(aturan_atas="1h", ema_cepat=10, ema_lambat=30,
                  pivot_kiri=2, pivot_kanan=2, toleransi_atr=0.5,
                  maks_uji=5, maks_level=10, dekat_atr=0.5,
                  dekat_atr_atas=1.5, pin_rasio_ekor=2.0,
                  pin_maks_badan=0.35, penyangga_atr=0.5,
                  rr_minimal=1.5, atr_periode=14, atr_pengali_stop=2.0)


def data_bervolume(n: int = 600, benih: int = 42) -> pd.DataFrame:
    """`data_gelombang` tapi volumenya berubah-ubah, bukan tetap 1000.

    Diperlukan karena penyaring volume tidak bisa diuji pada data yang
    volumenya rata: `volume_relatif` akan bernilai 1,0 di setiap lilin dan
    ambang berapa pun menghasilkan jawaban yang sama.
    """
    df = data_gelombang(n, benih=benih)
    rng = np.random.default_rng(benih + 1)
    df["volume"] = np.abs(rng.lognormal(6.9, 0.55, n))
    return df


# ---------------------------------------------------------------- indikator
def uji_indikator() -> None:
    print("\n[1] Indikator dibandingkan hitungan manual")

    s = pd.Series([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], dtype=float)
    sma3 = ind.sma(s, 3)
    cek("SMA(3) kosong sebelum cukup data", bool(pd.isna(sma3.iloc[1])))
    cek("SMA(3) di indeks 2 = rata-rata 1,2,3",
        abs(sma3.iloc[2] - 2.0) < 1e-12, f"{sma3.iloc[2]}")
    cek("SMA(3) di indeks 9 = rata-rata 8,9,10",
        abs(sma3.iloc[9] - 9.0) < 1e-12, f"{sma3.iloc[9]}")

    naik = pd.Series(np.arange(1, 60), dtype=float)
    turun = pd.Series(np.arange(60, 1, -1), dtype=float)
    cek("RSI harga naik terus = 100",
        abs(float(ind.rsi(naik, 14).iloc[-1]) - 100.0) < 1e-9)
    cek("RSI harga turun terus = 0",
        abs(float(ind.rsi(turun, 14).iloc[-1]) - 0.0) < 1e-9)

    df = data_gelombang(300)
    r = ind.rsi(df["close"], 14).dropna().astype(float)
    cek("RSI selalu di rentang 0-100",
        bool((r >= 0).all() and (r <= 100).all()),
        f"min {r.min():.1f} maks {r.max():.1f}")

    # ATR pada lilin yang rentangnya selalu persis 2.0
    n = 50
    datar = pd.DataFrame({
        "open": [100.0] * n, "close": [100.0] * n,
        "high": [101.0] * n, "low": [99.0] * n,
    })
    a = float(ind.atr(datar, 14).iloc[-1])
    cek("ATR pada rentang tetap 2.0 menghasilkan 2.0", abs(a - 2.0) < 1e-6, f"{a}")


# ----------------------------------------------------------------- donchian
def uji_donchian() -> None:
    print("\n[2] Donchian tidak boleh melihat lilin yang sedang dinilai")

    df = data_gelombang(300)
    d = ind.donchian(df, 20)

    i = 150
    harapan_atas = float(df["high"].iloc[i - 20:i].max())
    harapan_bawah = float(df["low"].iloc[i - 20:i].min())
    cek("dc_atas = tertinggi 20 lilin SEBELUMNYA (tidak termasuk hari ini)",
        abs(float(d["dc_atas"].iloc[i]) - harapan_atas) < 1e-9,
        f"dapat {d['dc_atas'].iloc[i]:.4f} harap {harapan_atas:.4f}")
    cek("dc_bawah = terendah 20 lilin SEBELUMNYA",
        abs(float(d["dc_bawah"].iloc[i]) - harapan_bawah) < 1e-9)

    # Kalau .shift(1) hilang, dc_atas >= high hari ini, sehingga harga tidak
    # akan pernah bisa menembusnya dan sinyal tembus batas selalu nol.
    tembus = (df["close"] > d["dc_atas"]).sum()
    cek("masih ada lilin yang menembus batas atas", tembus > 0,
        f"{tembus} lilin")


# ------------------------------------------------------------- regresi bug
def uji_perpotongan() -> None:
    print("\n[3] Regresi bug .shift() — perpotongan wajib berselang-seling")

    df = data_gelombang(600)
    cepat = ind.sma(df["close"], 10)
    lambat = ind.sma(df["close"], 25)
    naik, turun = strategy._potong(cepat, lambat)

    cek("hasil _potong bertipe bool",
        naik.dtype == bool and turun.dtype == bool,
        f"{naik.dtype} / {turun.dtype}")
    cek("tidak ada lilin yang naik dan turun sekaligus",
        not bool((naik & turun).any()))

    kejadian = []
    for ts in df.index:
        if naik.loc[ts]:
            kejadian.append("naik")
        elif turun.loc[ts]:
            kejadian.append("turun")
    berselang = all(a != b for a, b in zip(kejadian, kejadian[1:]))
    cek("perpotongan naik dan turun berselang-seling", berselang,
        f"{len(kejadian)} kejadian: {''.join(k[0] for k in kejadian)}")

    # Inti bugnya: dengan bug, jumlah "perpotongan" naik hampir sebanyak
    # jumlah lilin. Tanpa bug, jumlahnya sedikit.
    cek("jumlah perpotongan naik jauh lebih sedikit daripada jumlah lilin",
        int(naik.sum()) < len(df) * 0.1,
        f"{int(naik.sum())} dari {len(df)} lilin")

    # Uji langsung penyebabnya: ~ pada Series object memberi angka, bukan bool
    mentah = pd.Series([True, False, True, None])
    aman = strategy._bool(mentah)
    cek("_bool menjadikan NaN sebagai False dan tipenya bool",
        aman.dtype == bool and list(aman) == [True, False, True, False])
    cek("operator ~ pada hasil _bool memberi boolean, bukan angka",
        list(~aman) == [False, True, False, True])


# ---------------------------------------------------------------- biaya
def uji_biaya() -> None:
    print("\n[4] Model biaya berbeda untuk crypto dan forex")

    c = backtest.Biaya(jenis="persen", fee_persen=0.1)
    beli = c.saat_membeli(100.0)
    jual = c.saat_menjual(100.0)
    cek("crypto: membeli jadi lebih mahal 0,1%", abs(beli - 100.1) < 1e-9, f"{beli}")
    cek("crypto: menjual jadi lebih murah 0,1%", abs(jual - 99.9) < 1e-9, f"{jual}")
    cek("crypto: bolak-balik memakan 0,2%",
        abs((beli - jual) / 100.0 * 100 - 0.2) < 1e-9)

    spread = 1.5 * 0.0001          # 1,5 pip pada EURUSD
    f = backtest.Biaya(jenis="spread", spread=spread)
    beli_f = f.saat_membeli(1.1000)
    jual_f = f.saat_menjual(1.1000)
    cek("forex: beli lalu langsung jual rugi persis satu spread",
        abs((beli_f - jual_f) - spread) < 1e-12,
        f"{(beli_f - jual_f):.6f} vs {spread:.6f}")
    cek("forex: spread dibagi rata dua sisi",
        abs((beli_f - 1.1000) - (1.1000 - jual_f)) < 1e-12)


# -------------------------------------------------------- ukuran posisi
def uji_ukuran_posisi() -> None:
    print("\n[5] Ukuran posisi berbasis risiko")

    kas = 10_000_000.0
    harga, stop = 100.0, 97.0
    risiko = kas * 0.01
    unit = backtest._ukuran_posisi(kas, harga, stop, risiko)
    rugi = unit * (harga - stop)
    cek("kena stop merugikan ≈ 1% modal", abs(rugi - risiko) < 1e-6,
        f"rugi {rugi:,.0f} dari modal {kas:,.0f}")

    # Stop yang sangat lebar tidak boleh membuat posisi melebihi modal
    unit2 = backtest._ukuran_posisi(kas, harga, 1.0, risiko)
    cek("posisi tidak melebihi modal yang ada (tanpa leverage)",
        unit2 * harga <= kas + 1e-6, f"nilai posisi {unit2 * harga:,.0f}")

    # Stop lebih lebar -> unit lebih sedikit, risiko rupiah tetap sama
    unit_sempit = backtest._ukuran_posisi(kas, 100.0, 99.0, risiko)
    unit_lebar = backtest._ukuran_posisi(kas, 100.0, 95.0, risiko)
    cek("stop lebih lebar berarti unit lebih sedikit",
        unit_lebar < unit_sempit,
        f"{unit_lebar:.2f} < {unit_sempit:.2f}")

    cek("jarak stop nol tidak membuat program mati",
        backtest._ukuran_posisi(kas, 100.0, 100.0, risiko) == 0.0)


# ---------------------------------------------------------------- strategi
def uji_strategi() -> None:
    print("\n[6] Tiga strategi harus benar-benar berbeda")

    # 1200 lilin, bukan 600. Alasannya diukur, bukan dikira-kira: penyaring
    # RSI<70 menolak sekitar 75-80% perpotongan naik, sehingga strategi ikut
    # tren hanya menghasilkan 1-4 sinyal beli per ~1200 lilin. Pada 600 lilin
    # hasilnya sering nol — dan itu membuat uji ini seolah menemukan bug
    # padahal strateginya memang sejarang itu.
    df = data_gelombang(1200)
    jumlah = {}
    for nama, p in P_KECIL.items():
        out = strategy.beri_sinyal(df, p, nama, dua_arah=True)
        for kolom in ("beli", "jual", "tutup_beli", "tutup_jual"):
            if out[kolom].dtype != bool:
                cek(f"{nama}: kolom {kolom} bertipe bool", False,
                    str(out[kolom].dtype))
                break
        else:
            cek(f"{nama}: keempat kolom keputusan bertipe bool", True)
        jumlah[nama] = int(out["beli"].sum())

    print(f"       sinyal beli: {jumlah}")
    cek("setiap strategi menghasilkan sinyal", all(v > 0 for v in jumlah.values()),
        str(jumlah))
    cek("jumlah sinyalnya tidak identik (berarti tidak salah kabel)",
        len(set(jumlah.values())) > 1, str(jumlah))

    out = strategy.beri_sinyal(df, P_KECIL["ikut_tren"], "ikut_tren",
                               dua_arah=False)
    cek("pasar satu arah: kolom jual selalu kosong",
        not bool(out["jual"].any()))

    cek("strategi tak dikenal ditolak dengan pesan jelas",
        _menolak(lambda: strategy.beri_sinyal(df, {}, "ngawur")))

    for nama, p in P_KECIL.items():
        w = strategy.masa_pemanasan(p, nama)
        cek(f"{nama}: masa pemanasan masuk akal", 10 < w < 300, f"{w} lilin")


def _menolak(fn) -> bool:
    try:
        fn()
    except Exception:
        return True
    return False


# ---------------------------------------------------------------- backtest
def uji_backtest() -> None:
    print("\n[7] Mesin backtest")

    df = data_gelombang(1200)   # lihat catatan panjang data di uji_strategi()
    biaya = backtest.Biaya(jenis="persen", fee_persen=0.1)
    hasil = backtest.jalankan(
        df, P_KECIL["ikut_tren"], "ikut_tren", biaya,
        modal=10_000_000, risiko_persen=1.0, dua_arah=False, simbol="UJI"
    )
    cek("backtest menghasilkan transaksi", hasil.jumlah_transaksi > 0,
        f"{hasil.jumlah_transaksi} transaksi")
    cek("kurva ekuitas terisi", hasil.kurva_ekuitas is not None
        and len(hasil.kurva_ekuitas) > 0)

    # Tidak melihat masa depan: harga masuk harus sama dengan harga PEMBUKAAN
    # lilin sesudah sinyal, bukan harga penutupan saat sinyal muncul.
    if hasil.transaksi:
        t = hasil.transaksi[0]
        pembukaan = float(df.loc[pd.Timestamp(t.waktu_masuk), "open"])
        cek("harga masuk = harga pembukaan lilin berikutnya + biaya",
            abs(t.harga_masuk - biaya.saat_membeli(pembukaan)) < 1e-6,
            f"{t.harga_masuk:.4f} vs {biaya.saat_membeli(pembukaan):.4f}")
    else:
        cek("harga masuk = harga pembukaan lilin berikutnya + biaya", False,
            "tidak ada transaksi untuk diperiksa")

    salah_tanda = [
        x for x in hasil.transaksi
        if x.laba != 0
        and (x.laba > 0) != (((x.harga_keluar - x.harga_masuk) * x.arah) > 0)
    ]
    cek("tanda laba selalu cocok dengan arah pergerakan harga",
        not salah_tanda, f"{len(salah_tanda)} transaksi janggal")

    rugi_besar = [x for x in hasil.transaksi
                  if x.alasan_keluar == "kena batas rugi"
                  and x.laba < -hasil.modal_awal * 0.05]
    cek("kerugian saat kena stop tetap terkendali", not rugi_besar,
        f"{len(rugi_besar)} transaksi rugi > 5% modal")

    cek("drawdown bernilai nol atau negatif", hasil.max_drawdown <= 0,
        f"{hasil.max_drawdown:.1f}%")

    # Pasar dua arah harus benar-benar membuka posisi jual
    hasil2 = backtest.jalankan(
        df, P_KECIL["ikut_tren"], "ikut_tren",
        backtest.Biaya(jenis="spread", spread=0.0002),
        modal=10_000_000, risiko_persen=1.0, dua_arah=True, simbol="UJI2"
    )
    ada_short = [x for x in hasil2.transaksi if x.arah == -1]
    cek("pasar dua arah membuka posisi jual", len(ada_short) > 0,
        f"{len(ada_short)} dari {hasil2.jumlah_transaksi} transaksi")

    bnh = backtest.beli_dan_tahan(df, biaya, 10_000_000)
    cek("pembanding beli-dan-tahan menghasilkan angka wajar", bnh > 0,
        f"Rp{bnh:,.0f}")


# ---------------------------------------------------------------- database
def uji_database() -> None:
    print("\n[8] Database")

    conn = db.connect(":memory:")
    baris = [("2026-01-01 00:00:00", 1.0, 2.0, 0.5, 1.5, 10.5),
             ("2026-01-02 00:00:00", 1.5, 2.5, 1.0, 2.0, 20.25)]
    n = db.save_prices(conn, "crypto", "BTCUSDT", "harian", baris)
    cek("menyimpan harga", n == 2, f"{n} baris")

    df = db.ambil(conn, "crypto", "BTCUSDT", "harian")
    cek("membaca kembali harga yang sama", len(df) == 2 and
        abs(float(df["close"].iloc[-1]) - 2.0) < 1e-9)
    cek("volume pecahan tersimpan utuh (bukan dibulatkan)",
        abs(float(df["volume"].iloc[-1]) - 20.25) < 1e-9,
        f"{df['volume'].iloc[-1]}")

    # Timeframe berbeda tidak boleh saling menimpa
    db.save_prices(conn, "crypto", "BTCUSDT", "4jam",
                   [("2026-01-01 04:00:00", 1, 2, 0.5, 1.7, 5)])
    cek("data harian dan 4 jam hidup berdampingan",
        db.count_rows(conn, "crypto", "BTCUSDT", "harian") == 2
        and db.count_rows(conn, "crypto", "BTCUSDT", "4jam") == 1)

    a = db.save_signal(conn, "crypto", "BTCUSDT", "harian", "ikut_tren",
                       "2026-01-02 00:00:00", "BELI", 2.0, "uji")
    b = db.save_signal(conn, "crypto", "BTCUSDT", "harian", "ikut_tren",
                       "2026-01-02 00:00:00", "BELI", 2.0, "uji")
    cek("sinyal pertama tercatat", a)
    cek("sinyal yang sama TIDAK tercatat dua kali", not b)

    c = db.save_signal(conn, "crypto", "BTCUSDT", "harian", "tembus_batas",
                       "2026-01-02 00:00:00", "BELI", 2.0, "uji")
    cek("sinyal dari strategi lain tetap dianggap baru", c)
    conn.close()


def uji_pembersihan_data() -> None:
    print("\n[9] Pembersihan cacat data sumber")

    # Kasus nyata dari EURUSD 2004-04-21: close di BAWAH low.
    baris = [
        ("2004-04-21 00:00:00", 1.184806, 1.191199, 1.182103, 1.181000, 0.0),
        ("2005-10-05 00:00:00", 1.191796, 1.201100, 1.191696, 1.201201, 0.0),
        ("2026-01-01 00:00:00", 100.0, 101.0, 99.0, 100.5, 5.0),   # sudah benar
    ]
    bersih, n = pasar.perbaiki_ohlc(baris)
    cek("lilin cacat terdeteksi dan dihitung", n == 2, f"{n} lilin")

    for ts, o, h, l, c, v in bersih:
        if not (h >= max(o, c) and l <= min(o, c)):
            cek("setelah dirapikan, high/low mencakup open dan close", False, ts)
            break
    else:
        cek("setelah dirapikan, high/low mencakup open dan close", True)

    cek("close di bawah low ditarik menjadi low baru",
        abs(bersih[0][3] - 1.181000) < 1e-12, f"low jadi {bersih[0][3]}")
    cek("close di atas high ditarik menjadi high baru",
        abs(bersih[1][2] - 1.201201) < 1e-12, f"high jadi {bersih[1][2]}")
    cek("lilin yang sudah benar tidak diubah", bersih[2] == baris[2])
    cek("harga open dan close tidak pernah diubah",
        all(a[1] == b[1] and a[4] == b[4] for a, b in zip(baris, bersih)))


def uji_futures() -> None:
    print("\n[10] Mekanisme futures: leverage, likuidasi, funding")

    # --- rumus harga likuidasi ---
    lq = backtest.harga_likuidasi(100.0, 10, 1, 0.005)
    cek("likuidasi beli 10x di harga 100 -> sekitar 90,45",
        abs(lq - 90.452) < 0.01, f"{lq:.3f}")
    cek("artinya modal habis kalau harga turun ~9,5%",
        9.0 < (100 - lq) < 10.0, f"{100-lq:.2f}%")

    lqs = backtest.harga_likuidasi(100.0, 10, -1, 0.005)
    cek("likuidasi jual 10x di harga 100 -> sekitar 109,45",
        abs(lqs - 109.453) < 0.01, f"{lqs:.3f}")

    cek("leverage 1 pada posisi beli tidak pernah likuidasi",
        backtest.harga_likuidasi(100.0, 1, 1, 0.005) == 0.0)

    lq3 = backtest.harga_likuidasi(100.0, 3, 1, 0.005)
    lq20 = backtest.harga_likuidasi(100.0, 20, 1, 0.005)
    cek("leverage makin besar, likuidasi makin dekat ke harga masuk",
        lq3 < lq < lq20,
        f"3x di {lq3:.1f}, 10x di {lq:.1f}, 20x di {lq20:.1f}")
    cek("leverage 20x likuidasi hanya ~5% dari harga masuk",
        4.0 < (100 - lq20) < 6.0, f"{100-lq20:.2f}%")

    # --- ukuran posisi dengan leverage ---
    # Poin penting yang mudah disalahpahami: leverage hanya menaikkan PLAFON,
    # ia tidak memaksa taruhan jadi lebih besar. Selama aturan risiko yang
    # mengikat, hasilnya sama persis dengan tanpa leverage.
    kas, harga = 1_000_000.0, 100.0
    risiko = kas * 0.01

    stop_lebar = 99.0        # jarak 1% -> aturan risiko yang mengikat
    a1 = backtest._ukuran_posisi(kas, harga, stop_lebar, risiko, leverage=1.0)
    a5 = backtest._ukuran_posisi(kas, harga, stop_lebar, risiko, leverage=5.0)
    cek("stop lebar: leverage TIDAK menambah ukuran posisi",
        abs(a1 - a5) < 1e-9, f"keduanya Rp{a1*harga:,.0f}")
    cek("stop lebar: nilai posisi tidak melebihi modal",
        a1 * harga <= kas + 1e-6, f"Rp{a1*harga:,.0f}")

    stop_sempit = 99.5       # jarak 0,5% -> plafon modal yang mengikat
    b1 = backtest._ukuran_posisi(kas, harga, stop_sempit, risiko, leverage=1.0)
    b5 = backtest._ukuran_posisi(kas, harga, stop_sempit, risiko, leverage=5.0)
    cek("stop sempit tanpa leverage: terpotong batas modal",
        abs(b1 * harga - kas) < 1e-6, f"Rp{b1*harga:,.0f}")
    cek("stop sempit dengan leverage 5x: posisi boleh melebihi modal",
        b5 * harga > kas, f"Rp{b5*harga:,.0f} dari modal Rp{kas:,.0f}")
    cek("stop sempit: risiko tetap 1% modal, tidak ikut berlipat",
        abs(b5 * (harga - stop_sempit) - risiko) < 1.0,
        f"rugi saat stop Rp{b5*(harga-stop_sempit):,.0f}")

    cek("leverage tidak pernah membuat risiko per transaksi membengkak",
        a5 * (harga - stop_lebar) <= risiko + 1e-6,
        f"Rp{a5*(harga-stop_lebar):,.0f}")

    # --- pembagian funding ke tiap lilin ---
    idx = pd.date_range("2026-01-01", periods=3, freq="D")
    fund = pd.Series(
        [0.0001, 0.0002, 0.0003, 0.0004],
        index=pd.to_datetime(["2026-01-01 00:00", "2026-01-01 08:00",
                              "2026-01-01 16:00", "2026-01-02 08:00"]),
    )
    per = backtest.funding_per_lilin(idx, fund)
    cek("3 kejadian funding masuk ke lilin harian pertama",
        abs(per[0] - 0.0006) < 1e-12, f"{per[0]}")
    cek("1 kejadian masuk ke lilin kedua", abs(per[1] - 0.0004) < 1e-12)
    cek("lilin tanpa funding bernilai nol", per[2] == 0.0)
    cek("tanpa data funding, semua lilin nol",
        backtest.funding_per_lilin(idx, None) == [0.0, 0.0, 0.0])

    # --- likuidasi benar-benar terjadi di backtest ---
    df = data_gelombang(1200)
    b = backtest.Biaya(jenis="persen", fee_persen=0.05)
    p = dict(P_KECIL["tembus_batas"])
    p["atr_pengali_stop"] = 20.0     # stop sengaja dibuat sangat lebar

    h1 = backtest.jalankan(df, p, "tembus_batas", b, modal=10_000_000,
                           risiko_persen=1.0, simbol="U", leverage=1.0)
    h20 = backtest.jalankan(df, p, "tembus_batas", b, modal=10_000_000,
                            risiko_persen=1.0, simbol="U", leverage=20.0)
    liq1 = [t for t in h1.transaksi if t.alasan_keluar == "LIKUIDASI"]
    liq20 = [t for t in h20.transaksi if t.alasan_keluar == "LIKUIDASI"]
    cek("tanpa leverage tidak pernah ada likuidasi", not liq1,
        f"{len(liq1)} likuidasi")
    cek("stop 20xATR + leverage 20x -> likuidasi terjadi", len(liq20) > 0,
        f"{len(liq20)} dari {h20.jumlah_transaksi} transaksi")
    cek("kerugian saat likuidasi dibatasi margin, tidak lebih",
        all(t.laba_persen > -101 for t in liq20),
        f"terburuk {min((t.laba_persen for t in liq20), default=0):.1f}%")

    # --- funding mengurangi keuntungan posisi beli ---
    tgl = df.index
    rate = pd.Series([0.0005] * (len(tgl) * 3),
                     index=pd.DatetimeIndex(
                         [t + pd.Timedelta(hours=h) for t in tgl
                          for h in (0, 8, 16)]))
    tanpa = backtest.jalankan(df, P_KECIL["tembus_batas"], "tembus_batas", b,
                              modal=10_000_000, risiko_persen=1.0,
                              simbol="U", leverage=3.0)
    dengan = backtest.jalankan(df, P_KECIL["tembus_batas"], "tembus_batas", b,
                               modal=10_000_000, risiko_persen=1.0,
                               simbol="U", leverage=3.0, funding=rate)
    cek("funding positif mengurangi hasil posisi beli",
        dengan.modal_akhir < tanpa.modal_akhir,
        f"tanpa Rp{tanpa.modal_akhir:,.0f} vs dengan Rp{dengan.modal_akhir:,.0f}")

    # --- REGRESI: lilin tempat posisi DIBUKA wajib ikut diperiksa ---
    # Bug nyata yang pernah terjadi: posisi dibuka di harga pembukaan lilin
    # berikutnya, tapi pemeriksaan stop dan likuidasi baru dimulai di lilin
    # SESUDAHNYA lagi. Akibatnya posisi yang seharusnya langsung terlikuidasi
    # di hari pertama tercatat bertahan berbulan-bulan, dan satu transaksi
    # palsu itu menentukan seluruh hasil backtest leverage tinggi.
    p100 = dict(P_KECIL["tembus_batas"])
    p100["atr_pengali_stop"] = 20.0      # stop dibuat jauh, agar likuidasi yang duluan
    h100 = backtest.jalankan(df, p100, "tembus_batas", b, modal=10_000_000,
                             risiko_persen=1.0, simbol="U", leverage=100.0)
    sehari = [t for t in h100.transaksi if t.waktu_masuk == t.waktu_keluar]
    cek("posisi bisa ditutup di lilin yang SAMA dengan pembukaannya",
        len(sehari) > 0,
        f"{len(sehari)} dari {h100.jumlah_transaksi} transaksi")
    cek("di leverage 100x, hampir semua posisi tutup seketika",
        len(sehari) >= h100.jumlah_transaksi * 0.5,
        f"{len(sehari)}/{h100.jumlah_transaksi}")
    cek("dan semuanya berlabel LIKUIDASI, bukan batas rugi",
        all(t.alasan_keluar == "LIKUIDASI" for t in sehari))

    # Jarak likuidasi di 100x hanya 0,5%; rentang lilin data uji lebih lebar
    # dari itu, jadi bertahannya sebuah posisi selama berhari-hari justru
    # tanda pemeriksaannya bolong.
    lama = [t for t in h100.transaksi if t.waktu_masuk != t.waktu_keluar]
    cek("nyaris tidak ada posisi 100x yang bertahan lebih dari satu lilin",
        len(lama) <= h100.jumlah_transaksi * 0.5,
        f"{len(lama)} bertahan lebih lama")

    negatif = pd.Series(-rate.values, index=rate.index)
    untung = backtest.jalankan(df, P_KECIL["tembus_batas"], "tembus_batas", b,
                               modal=10_000_000, risiko_persen=1.0,
                               simbol="U", leverage=3.0, funding=negatif)
    cek("funding negatif justru menambah hasil posisi beli",
        untung.modal_akhir > tanpa.modal_akhir,
        f"Rp{untung.modal_akhir:,.0f}")


def uji_batas_harian() -> None:
    print("\n[12] Batas jumlah pembukaan posisi per hari")

    df = data_gelombang(1200)
    b = backtest.Biaya(jenis="persen", fee_persen=0.05)
    p = P_KECIL["tembus_batas"]

    def per_hari(hasil):
        """Jumlah pembukaan posisi per tanggal WIB — sama dengan cara backtest
        dan robot live menghitung jatahnya."""
        n = {}
        for t in hasil.transaksi:
            tgl = waktu.tanggal_wib(t.waktu_masuk)
            n[tgl] = n.get(tgl, 0) + 1
        return n

    tanpa = backtest.jalankan(df, p, "tembus_batas", b, modal=10_000_000,
                              risiko_persen=1.0, dua_arah=True, simbol="U",
                              maks_per_hari=0)
    cek("batas 0 berarti tanpa batas — transaksi tetap muncul",
        tanpa.jumlah_transaksi > 0, f"{tanpa.jumlah_transaksi} transaksi")

    # Regresi: nilai bawaan harus identik dengan batas 0
    bawaan = backtest.jalankan(df, p, "tembus_batas", b, modal=10_000_000,
                               risiko_persen=1.0, dua_arah=True, simbol="U")
    cek("nilai bawaan sama persis dengan batas 0 (tidak mengubah apa pun)",
        bawaan.jumlah_transaksi == tanpa.jumlah_transaksi
        and abs(bawaan.modal_akhir - tanpa.modal_akhir) < 1e-6,
        f"{bawaan.jumlah_transaksi} vs {tanpa.jumlah_transaksi}")

    for batas in (1, 2, 3):
        h = backtest.jalankan(df, p, "tembus_batas", b, modal=10_000_000,
                              risiko_persen=1.0, dua_arah=True, simbol="U",
                              maks_per_hari=batas)
        n = per_hari(h)
        terbanyak = max(n.values()) if n else 0
        cek(f"batas {batas}: tidak ada hari yang melebihi jatah",
            terbanyak <= batas,
            f"hari terpadat {terbanyak} pembukaan")
        cek(f"batas {batas}: transaksi tidak lebih banyak daripada tanpa batas",
            h.jumlah_transaksi <= tanpa.jumlah_transaksi,
            f"{h.jumlah_transaksi} vs {tanpa.jumlah_transaksi}")

    # Jatah harus pulih tiap ganti hari, bukan habis selamanya
    ketat = backtest.jalankan(df, p, "tembus_batas", b, modal=10_000_000,
                              risiko_persen=1.0, dua_arah=True, simbol="U",
                              maks_per_hari=1)
    hari_dipakai = per_hari(ketat)
    cek("jatah kembali penuh tiap ganti tanggal",
        len(hari_dipakai) > 5,
        f"pembukaan terjadi di {len(hari_dipakai)} tanggal berbeda")

    # Data uji berlilin harian, jadi batas 1 tidak boleh memotong apa pun:
    # satu hari memang cuma bisa membuka satu posisi.
    cek("pada data harian, batas 1 tidak memotong transaksi",
        ketat.jumlah_transaksi == tanpa.jumlah_transaksi,
        f"{ketat.jumlah_transaksi} vs {tanpa.jumlah_transaksi}")

    # --- data intraday: di sinilah batasnya benar-benar bekerja ---
    # Pada data harian, satu hari memang hanya bisa satu pembukaan, sehingga
    # batas apa pun lolos tanpa pernah tersentuh. Dengan lilin 15 menit, satu
    # hari berisi 96 lilin dan bisa banyak pembukaan — barulah batas ini teruji.
    intra = data_gelombang(4000, freq="15min")
    tanpa_i = backtest.jalankan(intra, p, "tembus_batas", b, modal=10_000_000,
                                risiko_persen=1.0, dua_arah=True, simbol="U",
                                maks_per_hari=0)
    padat = max(per_hari(tanpa_i).values()) if tanpa_i.transaksi else 0
    cek("tanpa batas, ada hari dengan lebih dari 2 pembukaan",
        padat > 2, f"hari terpadat {padat} pembukaan dari "
                   f"{tanpa_i.jumlah_transaksi} transaksi")

    # Batas yang lebih KETAT daripada frekuensi alami harus memotong.
    for batas in range(1, padat):
        h = backtest.jalankan(intra, p, "tembus_batas", b, modal=10_000_000,
                              risiko_persen=1.0, dua_arah=True, simbol="U",
                              maks_per_hari=batas)
        n = per_hari(h)
        terpadat = max(n.values()) if n else 0
        cek(f"intraday batas {batas}: hari terpadat tidak melebihi jatah",
            terpadat <= batas, f"{terpadat} pembukaan")
        # Jumlah total tidak selalu berkurang: pembukaan yang tertahan
        # membebaskan slot, lalu sinyal berikutnya yang diambil. Yang pasti
        # berubah adalah SUSUNAN transaksinya, jadi itu yang diperiksa.
        berubah = (h.jumlah_transaksi != tanpa_i.jumlah_transaksi
                   or abs(h.modal_akhir - tanpa_i.modal_akhir) > 1e-6)
        cek(f"intraday batas {batas} (di bawah frekuensi alami {padat}): "
            f"benar-benar mengubah hasil",
            berubah,
            f"{h.jumlah_transaksi} transaksi, "
            f"modal akhir Rp{h.modal_akhir:,.0f} "
            f"vs Rp{tanpa_i.modal_akhir:,.0f}")

    # Batas yang lebih LONGGAR daripada frekuensi alami tidak boleh mengubah
    # apa pun. Inilah keadaan nyata di project: batas 10 pada frekuensi alami
    # 4,3 pembukaan/hari — pengaman yang menganggur, bukan rem harian.
    for batas in (padat, padat + 5, padat + 50):
        h = backtest.jalankan(intra, p, "tembus_batas", b, modal=10_000_000,
                              risiko_persen=1.0, dua_arah=True, simbol="U",
                              maks_per_hari=batas)
        cek(f"batas {batas} (di atas frekuensi alami {padat}): "
            f"tidak memotong apa pun",
            h.jumlah_transaksi == tanpa_i.jumlah_transaksi
            and abs(h.modal_akhir - tanpa_i.modal_akhir) < 1e-6,
            f"{h.jumlah_transaksi} vs {tanpa_i.jumlah_transaksi}")

    ketat_i = backtest.jalankan(intra, p, "tembus_batas", b, modal=10_000_000,
                                risiko_persen=1.0, dua_arah=True, simbol="U",
                                maks_per_hari=1)
    longgar_i = backtest.jalankan(intra, p, "tembus_batas", b, modal=10_000_000,
                                  risiko_persen=1.0, dua_arah=True, simbol="U",
                                  maks_per_hari=padat)
    cek("batas lebih longgar menghasilkan lebih banyak transaksi",
        longgar_i.jumlah_transaksi > ketat_i.jumlah_transaksi,
        f"batas {padat} -> {longgar_i.jumlah_transaksi}, "
        f"batas 1 -> {ketat_i.jumlah_transaksi}")

    # --- sisi sinyal: hitungannya dari database ---
    conn = db.connect(":memory:")
    hari = "2026-09-09"
    cek("mula-mula belum ada pembukaan tercatat",
        db.hitung_entry_hari_ini(conn, "futures", hari) == 0)

    for i, aksi in enumerate(["BELI", "JUAL", "TUTUP BELI", "TUTUP JUAL"]):
        db.save_signal(conn, "futures", f"S{i}", "15menit", "tembus_batas",
                       f"{hari} 0{i}:00:00", aksi, 1.0, "uji")
    cek("hanya BELI dan JUAL yang dihitung, TUTUP tidak",
        db.hitung_entry_hari_ini(conn, "futures", hari) == 2,
        str(db.hitung_entry_hari_ini(conn, "futures", hari)))

    db.save_signal(conn, "crypto", "X", "harian", "tembus_batas",
                   f"{hari} 05:00:00", "BELI", 1.0, "uji")
    cek("pasar lain tidak ikut terhitung",
        db.hitung_entry_hari_ini(conn, "futures", hari) == 2)

    db.save_signal(conn, "futures", "S9", "15menit", "tembus_batas",
                   "2026-09-10 01:00:00", "BELI", 1.0, "uji")
    cek("hari lain tidak ikut terhitung",
        db.hitung_entry_hari_ini(conn, "futures", hari) == 2)
    cek("hari berikutnya dihitung sendiri",
        db.hitung_entry_hari_ini(conn, "futures", "2026-09-10") == 1)

    db.save_signal(conn, "futures", "S9", "15menit", "tembus_batas",
                   f"{hari} 09:00:00", "BELI", 1.0, "uji")
    cek("simbol berbeda digabung dalam satu hitungan",
        db.hitung_entry_hari_ini(conn, "futures", hari) == 3)
    conn.close()


def uji_lacak_posisi() -> None:
    print("\n[13] Ingatan posisi terbuka")

    conn = db.connect(":memory:")
    K = ("futures", "BTCUSDT", "15menit", "tembus_batas")

    cek("mula-mula tidak ada posisi terbuka",
        db.posisi_terbuka(conn, *K) is None)

    pid = db.buka_posisi(conn, *K, arah=1, ts="2026-09-09 08:00:00",
                         harga=100.0, stop=97.0, target=109.0)
    pos = db.posisi_terbuka(conn, *K)
    cek("posisi tercatat setelah dibuka", pos is not None)
    cek("arah, harga, stop, dan target tersimpan benar",
        pos and int(pos["arah"]) == 1 and pos["harga_masuk"] == 100.0
        and pos["stop"] == 97.0 and pos["target"] == 109.0)

    cek("kombinasi lain tidak ikut terbaca",
        db.posisi_terbuka(conn, "futures", "ETHUSDT", "15menit",
                          "tembus_batas") is None)
    cek("timeframe lain punya catatan sendiri",
        db.posisi_terbuka(conn, "futures", "BTCUSDT", "harian",
                          "tembus_batas") is None)

    cek("terdaftar di seluruh posisi terbuka",
        len(db.semua_posisi_terbuka(conn)) == 1)

    db.tutup_posisi(conn, pid, "2026-09-09 12:00:00", 109.0, "KENA TAKE PROFIT")
    cek("setelah ditutup, tidak lagi terbaca sebagai terbuka",
        db.posisi_terbuka(conn, *K) is None)
    cek("tidak ada lagi posisi terbuka sama sekali",
        db.semua_posisi_terbuka(conn) == [])

    db.tutup_posisi(conn, pid, "2026-09-09 13:00:00", 50.0, "coba tutup lagi")
    baris = conn.execute("SELECT * FROM posisi WHERE id = ?", (pid,)).fetchone()
    cek("posisi yang sudah tertutup tidak bisa ditutup ulang",
        baris["harga_keluar"] == 109.0 and baris["alasan_keluar"] == "KENA TAKE PROFIT",
        f"{baris['harga_keluar']} / {baris['alasan_keluar']}")

    # Dua posisi berurutan pada kombinasi yang sama
    p2 = db.buka_posisi(conn, *K, arah=-1, ts="2026-09-10 08:00:00",
                        harga=95.0, stop=98.0, target=86.0)
    pos2 = db.posisi_terbuka(conn, *K)
    cek("posisi baru sesudahnya terbaca, bukan yang lama",
        pos2 and pos2["id"] == p2 and int(pos2["arah"]) == -1)
    conn.close()

    # --- deteksi batas rugi / take profit tersentuh ---
    from src import signals as sig

    def siapkan(arah, stop, target, harga_lilin):
        c = db.connect(":memory:")
        baris = []
        for i, (o, h, l, cl) in enumerate(harga_lilin):
            baris.append((f"2026-09-09 {8+i:02d}:00:00", o, h, l, cl, 1.0))
        db.save_prices(c, "futures", "BTCUSDT", "15menit", baris)
        db.buka_posisi(c, "futures", "BTCUSDT", "15menit", "tembus_batas",
                       arah=arah, ts="2026-09-09 08:00:00", harga=100.0,
                       stop=stop, target=target)
        cfg = {"notifikasi": {}}
        return c, sig.periksa_batas_tersentuh(c, cfg, "15menit", "tembus_batas")

    # beli, harga jatuh menembus stop 97
    c, r = siapkan(1, 97.0, 109.0,
                   [(100, 101, 99, 100), (100, 101, 96, 98)])
    cek("posisi beli: batas rugi tersentuh terdeteksi",
        len(r) == 1 and r[0]["aksi"] == "KENA BATAS RUGI", str(r))
    cek("posisinya ikut ditutup di database",
        db.posisi_terbuka(c, "futures", "BTCUSDT", "15menit",
                          "tembus_batas") is None)
    c.close()

    # beli, harga naik menembus target 109
    c, r = siapkan(1, 97.0, 109.0,
                   [(100, 101, 99, 100), (100, 110, 99.5, 109.5)])
    cek("posisi beli: take profit tersentuh terdeteksi",
        len(r) == 1 and r[0]["aksi"] == "KENA TAKE PROFIT", str(r))
    c.close()

    # jual, harga naik menembus stop 103
    c, r = siapkan(-1, 103.0, 91.0,
                   [(100, 101, 99, 100), (100, 104, 99, 103.5)])
    cek("posisi jual: batas rugi ada di ATAS, terdeteksi benar",
        len(r) == 1 and r[0]["aksi"] == "KENA BATAS RUGI", str(r))
    c.close()

    # jual, harga turun menembus target 91
    c, r = siapkan(-1, 103.0, 91.0,
                   [(100, 101, 99, 100), (100, 101, 90, 90.5)])
    cek("posisi jual: take profit ada di BAWAH, terdeteksi benar",
        len(r) == 1 and r[0]["aksi"] == "KENA TAKE PROFIT", str(r))
    c.close()

    # satu lilin menyentuh keduanya -> batas rugi yang dianggap duluan
    c, r = siapkan(1, 97.0, 109.0,
                   [(100, 101, 99, 100), (100, 110, 96, 105)])
    cek("satu lilin kena keduanya: batas rugi yang dianggap duluan",
        len(r) == 1 and r[0]["aksi"] == "KENA BATAS RUGI",
        "kalau take profit yang dipilih, backtest akan menipu diri sendiri")
    c.close()

    # harga tidak menyentuh apa pun -> posisi tetap terbuka
    c, r = siapkan(1, 97.0, 109.0,
                   [(100, 101, 99, 100), (100, 102, 98, 101)])
    cek("harga aman: tidak ada kabar, posisi tetap terbuka",
        r == [] and db.posisi_terbuka(c, "futures", "BTCUSDT", "15menit",
                                      "tembus_batas") is not None)
    c.close()


def uji_sinyal_masuk_beruntun() -> None:
    """Sinyal masuk tidak menunggu posisi sebelumnya ditutup.

    Perilaku ini diminta langsung: peluang baru harus tetap dikabarkan walau
    masih ada posisi terbuka, sementara sinyal keluar tetap wajib sampai.
    """
    print("\n[16] Sinyal masuk beruntun tanpa menunggu penutupan")

    from src import signals as sig

    def cfg_dasar(tahan: bool) -> dict:
        return {
            "futures": {"aktif": True, "dua_arah": True,
                        "watchlist": ["BTCUSDT"],
                        "biaya": {"fee_persen": 0.05},
                        "maks_trade_per_hari": 0},
            "strategi": {"tembus_batas": {"15menit": P_KECIL["tembus_batas"]}},
            "take_profit_rasio": 0,
            "notifikasi": {"lilin_terakhir_diperiksa": 10,
                           "lacak_posisi": True,
                           "tahan_sinyal_masuk": tahan,
                           # Pembatas umur dimatikan di sini supaya uji ini
                           # menguji "sinyal beruntun", bukan tercampur
                           # aturan sinyal basi yang diuji terpisah.
                           "maks_umur_lilin_masuk": -1},
        }

    def db_harga(naik: bool):
        c = db.connect(":memory:")
        baris = []
        for i in range(140):
            # Naik terus -> menembus batas Donchian di hampir tiap lilin.
            harga = 100 + i * 0.5 if naik else 170 - i * 0.5
            baris.append((f"2026-09-09 {i // 60:02d}:{i % 60:02d}:00",
                          harga - 0.1, harga + 0.3, harga - 0.3, harga, 1000.0))
        db.save_prices(c, "futures", "BTCUSDT", "15menit", baris)
        return c

    # --- tanpa menahan: setiap peluang dikabarkan --------------------------
    c = db_harga(naik=True)
    bebas = sig.periksa_semua(c, cfg_dasar(False), "15menit", "tembus_batas")
    masuk_bebas = [s for s in bebas if s["aksi"] in ("BELI", "JUAL")]
    cek("sinyal masuk beruntun semuanya dikabarkan", len(masuk_bebas) >= 5,
        f"{len(masuk_bebas)} sinyal masuk dalam 10 lilin terakhir")
    cek("yang DIPANTAU tetap satu posisi per arah",
        len(db.semua_posisi_terbuka(c)) == 1,
        "supaya satu sentuhan batas rugi tidak mengirim pesan berkali-kali")
    lanjutan = [s for s in masuk_bebas if "posisi searah sudah terbuka"
                in s.get("alasan", "")]
    cek("sinyal susulan diberi keterangan posisi searah masih terbuka",
        len(lanjutan) >= 1, f"{len(lanjutan)} sinyal diberi keterangan")
    c.close()

    # --- perilaku lama masih bisa dipilih ----------------------------------
    c = db_harga(naik=True)
    ditahan = sig.periksa_semua(c, cfg_dasar(True), "15menit", "tembus_batas")
    masuk_tahan = [s for s in ditahan if s["aksi"] in ("BELI", "JUAL")]
    cek("tahan_sinyal_masuk: true kembali ke perilaku lama",
        len(masuk_tahan) == 1,
        f"{len(masuk_tahan)} sinyal — hanya yang pertama")
    cek("menahan memang mengurangi jumlah pesan",
        len(masuk_tahan) < len(masuk_bebas))
    c.close()

    # --- sinyal keluar tetap wajib sampai ----------------------------------
    c = db_harga(naik=False)
    db.buka_posisi(c, "futures", "BTCUSDT", "15menit", "tembus_batas",
                   arah=1, ts="2026-09-09 00:00:00", harga=170.0,
                   stop=None, target=None)
    turun = sig.periksa_semua(c, cfg_dasar(False), "15menit", "tembus_batas")
    keluar = [s for s in turun if s["aksi"] == "TUTUP BELI"]
    cek("sinyal keluar sampai saat ada posisi yang perlu ditutup",
        len(keluar) >= 1, f"{len(keluar)} sinyal TUTUP BELI")
    cek("posisinya benar-benar ditutup di database",
        db.posisi_terbuka(c, "futures", "BTCUSDT", "15menit",
                          "tembus_batas", arah=1) is None)
    c.close()

    # --- tanpa posisi, sinyal keluar tidak dikirim -------------------------
    c = db_harga(naik=False)
    kosong = sig.periksa_semua(c, cfg_dasar(False), "15menit", "tembus_batas")
    cek("tanpa posisi terbuka, tidak ada ajakan menutup",
        not [s for s in kosong if s["aksi"].startswith("TUTUP")],
        "diberi tahu cara menutup posisi yang tak pernah dibuka itu bingung")
    c.close()

    # --- posisi beli dan jual bisa hidup berdampingan ----------------------
    c = db.connect(":memory:")
    K = ("futures", "BTCUSDT", "15menit", "tembus_batas")
    db.buka_posisi(c, *K, arah=1, ts="2026-09-09 08:00:00", harga=100.0,
                   stop=97.0, target=109.0)
    db.buka_posisi(c, *K, arah=-1, ts="2026-09-09 09:00:00", harga=105.0,
                   stop=108.0, target=96.0)
    beli = db.posisi_terbuka(c, *K, arah=1)
    jual = db.posisi_terbuka(c, *K, arah=-1)
    cek("posisi beli tetap ketemu walau ada posisi jual yang lebih muda",
        beli is not None and int(beli["arah"]) == 1,
        "tanpa penyaring arah, TUTUP BELI akan kehilangan posisinya")
    cek("posisi jual ditemukan terpisah",
        jual is not None and int(jual["arah"]) == -1)
    c.close()


def uji_scalping_dua_timeframe() -> None:
    print("\n[17] Scalping peta-eksekusi — dua timeframe")

    # --- lilin konfirmasi, dihitung tangan --------------------------------
    d = pd.DataFrame({
        # 0: ekor bawah 10 badan 0,5   1: ekor atas 10 badan 0,5
        # 2: lilin bertubuh besar      3: merah      4: hijau menelan
        "open":  [100.0, 100.0, 100.0, 105.0, 100.0],
        "high":  [101.0, 110.0, 106.0, 106.0, 107.0],
        "low":   [90.0, 99.0, 94.0, 99.0, 99.0],
        "close": [100.5, 99.5, 105.0, 100.0, 106.0],
    }, index=pd.date_range("2026-01-01", periods=5, freq="15min"))
    pb = ind.pin_bar(d)
    en = ind.engulfing(d)
    cek("pin bar ekor bawah terdeteksi", bool(pb["pin_naik"].iloc[0]))
    cek("pin bar ekor atas terdeteksi", bool(pb["pin_turun"].iloc[1]))
    cek("lilin bertubuh besar bukan pin bar",
        not bool(pb["pin_naik"].iloc[2]) and not bool(pb["pin_turun"].iloc[2]),
        "tanpa syarat badan kecil, lilin besar berekor ikut terhitung")
    cek("engulfing naik terdeteksi", bool(en["telan_naik"].iloc[4]),
        "hijau 100->106 menelan badan merah 105->100")
    cek("lilin yang tidak menelan tidak ditandai",
        not bool(en["telan_naik"].iloc[2]))

    # --- bias struktur ----------------------------------------------------
    def rangka(tinggi, rendah):
        return pd.DataFrame(
            {"open": rendah, "high": tinggi, "low": rendah, "close": tinggi},
            index=pd.date_range("2026-01-01", periods=len(tinggi), freq="h"))

    naik = rangka([10, 12, 11, 14, 13, 16, 15, 18, 17, 20, 19, 22],
                  [8, 9, 9, 11, 11, 13, 13, 15, 15, 17, 17, 19])
    turun = rangka([22, 19, 20, 17, 18, 15, 16, 13, 14, 11, 12, 9],
                   [19, 17, 17, 15, 15, 13, 13, 11, 11, 9, 9, 7])
    b1 = ind.bias_struktur(naik, 1, 1)
    b2 = ind.bias_struktur(turun, 1, 1)
    cek("struktur higher-high + higher-low terbaca NAIK",
        int(b1.iloc[-1]) == 1, f"bias {int(b1.iloc[-1])}")
    cek("struktur lower-high + lower-low terbaca TURUN",
        int(b2.iloc[-1]) == -1, f"bias {int(b2.iloc[-1])}")
    cek("bias hanya bernilai -1, 0, atau 1",
        set(b1.unique()) <= {-1, 0, 1})

    # --- penyaring sesi ---------------------------------------------------
    jam = pd.date_range("2026-01-01 00:00", periods=24, freq="h")
    s1 = ind.dalam_sesi(jam, 12, 16)
    cek("sesi 12-16 UTC memuat tepat 5 jam", int(s1.sum()) == 5,
        f"jam {list(jam[s1].hour)}")
    s2 = ind.dalam_sesi(jam, 22, 2)
    cek("sesi yang melewati tengah malam ikut ditangani",
        sorted(jam[s2].hour) == [0, 1, 2, 22, 23])

    # --- setelan per simbol sampai ke strategi -----------------------------
    from src.config import parameter as ambil_p
    cfg_kecil = {"strategi": {"scalping_h1_m15":
                              {"15menit": dict(P_SCALPING)}},
                 "take_profit_rasio": 0}
    cek("tanpa item, setelan per simbol tidak muncul",
        "sesi_utc" not in ambil_p(cfg_kecil, "scalping_h1_m15", "15menit"))
    cek("sesi_utc dari watchlist sampai ke parameter strategi",
        ambil_p(cfg_kecil, "scalping_h1_m15", "15menit",
                {"simbol": "GC=F", "sesi_utc": [12, 16]}).get("sesi_utc")
        == [12, 16])
    cek("kunci asing di watchlist TIDAK menimpa parameter strategi",
        ambil_p(cfg_kecil, "scalping_h1_m15", "15menit",
                {"simbol": "X", "ema_cepat": 999})["ema_cepat"] != 999,
        "kalau semua kunci disalin, salah ketik di watchlist diam-diam "
        "mengubah strategi")

    # --- strategi utuh ----------------------------------------------------
    rng = np.random.default_rng(12)
    n = 4000
    dasar = np.concatenate([np.linspace(2000, 2200, 1500),
                            2200 + np.sin(np.linspace(0, 25, 1000)) * 40,
                            np.linspace(2200, 2050, 1500)])
    close = dasar + rng.normal(0, 1.2, n).cumsum() * 0.4
    df = pd.DataFrame({
        "open": close + rng.normal(0, 0.6, n),
        "high": close + np.abs(rng.normal(0, 2.0, n)),
        "low": close - np.abs(rng.normal(0, 2.0, n)),
        "close": close, "volume": np.abs(rng.normal(900, 200, n)),
    }, index=pd.date_range("2026-06-01", periods=n, freq="15min"))

    p = dict(P_SCALPING, sesi_utc=[12, 16])
    hasil = strategy.beri_sinyal(df, p, "scalping_h1_m15", dua_arah=True)
    cek("kolom peta timeframe atas ikut terbawa",
        all(k in hasil.columns for k in
            ("bias", "ema_naik", "sup_atas", "res_atas", "target_beli")))

    masuk = hasil[hasil["beli"] | hasil["jual"]]
    cek("seluruh sinyal jatuh di dalam jam sesi",
        set(masuk.index.hour) <= {12, 13, 14, 15, 16},
        f"{len(masuk)} sinyal, jam {sorted(set(masuk.index.hour))}")
    beli = hasil[hasil["beli"]]
    if len(beli):
        cek("batas rugi berada DI LUAR ekor lilin konfirmasi",
            bool((beli["stop_beli"] < beli["low"]).all()),
            "kalau di dalam ekor, stopnya tersentuh di lilin yang sama")

    tanpa_sesi = strategy.beri_sinyal(df, dict(P_SCALPING),
                                      "scalping_h1_m15", dua_arah=True)
    cek("penyaring sesi hanya membuang, tidak pernah menambah",
        int((hasil["beli"] & ~tanpa_sesi["beli"]).sum()) == 0)
    cek("pasar satu arah tidak memberi sinyal jual",
        int(strategy.beri_sinyal(df, p, "scalping_h1_m15",
                                 dua_arah=False)["jual"].sum()) == 0)

    # --- yang paling penting untuk strategi dua timeframe -----------------
    beda = []
    for potong in (2600, 3000, 3400, 3800):
        sebagian = strategy.beri_sinyal(df.iloc[:potong], p,
                                        "scalping_h1_m15", dua_arah=True)
        i = potong - 1
        for k in ("beli", "jual", "bias", "sup_atas", "res_atas",
                  "stop_beli", "target_beli"):
            a, b = hasil[k].iloc[i], sebagian[k].iloc[i]
            if not ((pd.isna(a) and pd.isna(b)) or a == b):
                beda.append((potong, k))
    cek("peta timeframe atas TIDAK mengintip masa depan", not beda,
        "lilin H1 yang belum tutup tidak boleh ikut terbaca"
        if not beda else f"berbeda di {beda[:3]}")


def uji_sinyal_basi() -> None:
    """Sinyal MASUK yang sudah lewat beberapa lilin tidak boleh dikirim.

    Bukan soal peluang terlewat, tapi soal bahaya: harga dan batas rugi di
    pesan dihitung dari lilin saat sinyal muncul. Kalau lilin itu sudah lama
    lewat, angkanya tidak lagi menggambarkan pasar — dan mengeksekusinya
    berarti masuk dengan stop yang salah.
    """
    print()
    print("[18] Sinyal masuk yang sudah basi")

    from src import signals as sig

    def cfg_umur(maks: int) -> dict:
        return {
            "futures": {"aktif": True, "dua_arah": True,
                        "watchlist": ["BTCUSDT"],
                        "biaya": {"fee_persen": 0.05},
                        "maks_trade_per_hari": 0},
            "strategi": {"tembus_batas": {"15menit": P_KECIL["tembus_batas"]}},
            "take_profit_rasio": 0,
            "notifikasi": {"lilin_terakhir_diperiksa": 10,
                           "lacak_posisi": True,
                           "tahan_sinyal_masuk": False,
                           "maks_umur_lilin_masuk": maks},
        }

    def db_naik():
        c = db.connect(":memory:")
        baris = [(f"2026-09-09 {i // 60:02d}:{i % 60:02d}:00",
                  100 + i * 0.5 - 0.1, 100 + i * 0.5 + 0.3,
                  100 + i * 0.5 - 0.3, 100 + i * 0.5, 1000.0)
                 for i in range(140)]
        db.save_prices(c, "futures", "BTCUSDT", "15menit", baris)
        return c

    c = db_naik()
    bebas = sig.periksa_semua(c, cfg_umur(-1), "15menit", "tembus_batas")
    n_bebas = len([s for s in bebas if s["aksi"] in ("BELI", "JUAL")])
    c.close()

    c = db_naik()
    ketat = sig.periksa_semua(c, cfg_umur(3), "15menit", "tembus_batas")
    masuk = [s for s in ketat if s["aksi"] in ("BELI", "JUAL")]
    c.close()

    cek("pembatas umur memang membuang sinyal basi",
        len(masuk) < n_bebas, f"{n_bebas} -> {len(masuk)} sinyal masuk")
    cek("yang lolos semuanya berumur di dalam batas",
        all(int(s.get("umur_lilin", 0)) <= 3 for s in masuk),
        f"umur yang lolos: {sorted(s['umur_lilin'] for s in masuk)}")
    cek("umur lilin tercatat di tiap sinyal",
        all("umur_lilin" in s for s in ketat))
    cek("lilin terakhir berumur 0",
        any(int(s["umur_lilin"]) == 0 for s in ketat),
        "tanpa ini, sinyal terbaru pun akan dikira basi")

    # Sinyal keluar TIDAK boleh ikut dibatasi umurnya.
    c = db.connect(":memory:")
    baris = [(f"2026-09-09 {i // 60:02d}:{i % 60:02d}:00",
              170 - i * 0.5 + 0.1, 170 - i * 0.5 + 0.3,
              170 - i * 0.5 - 0.3, 170 - i * 0.5, 1000.0)
             for i in range(140)]
    db.save_prices(c, "futures", "BTCUSDT", "15menit", baris)
    db.buka_posisi(c, "futures", "BTCUSDT", "15menit", "tembus_batas",
                   arah=1, ts="2026-09-09 00:00:00", harga=170.0,
                   stop=None, target=None)
    turun = sig.periksa_semua(c, cfg_umur(0), "15menit", "tembus_batas")
    keluar = [s for s in turun if s["aksi"].startswith("TUTUP")]
    cek("sinyal KELUAR tidak pernah dibatasi umurnya",
        len(keluar) >= 1,
        "kabar bahwa posisi perlu ditutup tetap berguna walau terlambat")
    c.close()

    # Tampilan pesan harus menyebutkan umurnya
    from src import notify
    pesan_basi = notify.susun_pesan(
        [{"pasar": "futures", "simbol": "BTCUSDT", "aksi": "BELI",
          "harga": 100.0, "alasan": "", "umur_lilin": 5,
          "ts": "2026-09-09 01:15:00"}], "x", "15menit", "Uji")
    pesan_baru = notify.susun_pesan(
        [{"pasar": "futures", "simbol": "BTCUSDT", "aksi": "BELI",
          "harga": 100.0, "alasan": "", "umur_lilin": 0,
          "ts": "2026-09-09 01:15:00"}], "x", "15menit", "Uji")
    cek("pesan menyebut umur saat sinyalnya tidak baru",
        "5 lilin lalu" in pesan_basi)
    cek("pesan TIDAK berisik saat sinyalnya baru",
        "lilin lalu" not in pesan_baru)



def uji_pesan() -> None:
    print("\n[11] Penyusunan pesan notifikasi")
    from src import notify, pasar as modul_pasar

    # REGRESI: pernah terjadi sinyal futures hilang diam-diam dari pesan,
    # karena daftar pasar di penyusun pesan ditulis tetap ("crypto","forex")
    # dan tidak ikut diperbarui saat futures ditambahkan. Sinyalnya tercatat
    # di log sebagai ditemukan, tapi tidak pernah sampai ke Telegram.
    contoh = []
    for p in modul_pasar.PASAR:
        contoh.append({"pasar": p, "simbol": f"UJI-{p.upper()}",
                       "aksi": "BELI", "harga": 100.0,
                       "saran_stop": 90.0, "saran_tp": 130.0, "tp_rasio": 3,
                       "alasan": "uji"})
    pesan = notify.susun_pesan(contoh, "09 September 2026", "harian", "Uji")

    for p in modul_pasar.PASAR:
        cek(f"sinyal pasar '{p}' muncul di pesan",
            f"UJI-{p.upper()}" in pesan)

    cek("timeframe tercantum di pesan", "timeframe harian" in pesan)
    cek("nama strategi tercantum di pesan", "Uji" in pesan)
    cek("batas rugi tercantum", "batas rugi" in pesan)
    cek("take profit tercantum saat rasionya diisi", "take profit" in pesan)

    tanpa_tp = [dict(c) for c in contoh]
    for c in tanpa_tp:
        c.pop("saran_tp"); c.pop("tp_rasio")
    p2 = notify.susun_pesan(tanpa_tp, "09 September 2026", "harian", "Uji")
    cek("take profit TIDAK muncul saat rasionya nol",
        "take profit" not in p2)

    kosong = notify.susun_pesan([], "09 September 2026", "4jam", "Uji")
    cek("pesan kosong tetap menyebut timeframe", "timeframe 4jam" in kosong)
    cek("pesan kosong berbunyi jelas", "Tidak ada sinyal baru" in kosong)

    pasar_baru = [{"pasar": "opsi", "simbol": "XYZ", "aksi": "BELI",
                   "harga": 1.0, "alasan": ""}]
    cek("pasar yang belum dikenal pun tetap tampil",
        "XYZ" in notify.susun_pesan(pasar_baru, "x", "harian", "Uji"))

    # Pengingat verifikasi berita: muncul saat ada ajakan membuka posisi,
    # tidak muncul saat hanya ada kabar penutupan atau laporan kosong.
    with_entry = notify.susun_pesan(pasar_baru, "x", "harian", "Uji")
    cek("pengingat cek berita muncul saat ada sinyal masuk",
        "CoinDesk" in with_entry)
    hanya_tutup = [{"pasar": "crypto", "simbol": "BTCUSDT",
                    "aksi": "TUTUP BELI", "harga": 1.0, "alasan": ""}]
    cek("pengingat cek berita TIDAK muncul saat hanya sinyal keluar",
        "CoinDesk" not in notify.susun_pesan(hanya_tutup, "x", "harian", "Uji"),
        "menutup posisi tidak perlu menunggu konfirmasi berita")
    cek("pengingat cek berita TIDAK muncul di laporan kosong",
        "CoinDesk" not in notify.susun_pesan([], "x", "harian", "Uji"))


def uji_struktur_harga() -> None:
    print("\n[14] Struktur harga — pivot, level, dan batas rugi struktural")

    # --- pivot di indeks yang benar, dihitung manual ---------------------
    h = [10, 11, 15, 11, 10, 11, 12, 11, 10]
    l = [9, 8, 7, 8, 9, 8, 6, 8, 9]
    kecil = pd.DataFrame({"open": h, "high": h, "low": l, "close": h},
                         index=pd.date_range("2024-01-01", periods=9))
    piv = ind.pivot(kecil, kiri=2, kanan=2)
    cek("pivot tinggi tepat di puncaknya",
        bool(piv["pivot_tinggi"].iloc[2]) and bool(piv["pivot_tinggi"].iloc[6]))
    cek("lilin biasa bukan pivot",
        not any(bool(piv["pivot_tinggi"].iloc[i]) for i in (3, 4, 5)))
    cek("tepi data tidak pernah jadi pivot",
        not any(bool(piv["pivot_tinggi"].iloc[i]) for i in (0, 1, 7, 8)),
        "jendelanya belum lengkap, jadi belum bisa dinilai")
    cek("pivot rendah tepat di lembahnya",
        bool(piv["pivot_rendah"].iloc[2]) and bool(piv["pivot_rendah"].iloc[6]))

    # --- level yang diuji berulang dihitung lebih kuat -------------------
    pola = [110, 106, 102, 100, 102, 106, 110] * 4
    memantul = pd.DataFrame({
        "open": [x + 2 for x in pola], "high": [x + 4 for x in pola],
        "low": pola, "close": [x + 2 for x in pola],
    }, index=pd.date_range("2024-01-01", periods=len(pola)))
    lv = ind.level_struktur(memantul, kiri=2, kanan=2, toleransi_atr=0.5,
                            atr_periode=14, maks_uji=5)
    cek("support ditemukan di harga pantulan", float(lv["support"].iloc[-1]) == 100.0)
    cek("level yang dipantuli berkali-kali dihitung lebih kuat",
        int(lv["uji_support"].iloc[-1]) >= 3,
        f"uji_support = {int(lv['uji_support'].iloc[-1])} setelah 4 kali memantul")

    # --- volume: pengaman untuk pasar tanpa data volume ------------------
    tanpa_volume = data_gelombang(200).assign(volume=0.0)
    cek("volume nol seluruhnya menghasilkan NaN, bukan angka",
        bool(ind.volume_relatif(tanpa_volume, 20).isna().all()),
        "inilah yang membuat penyaring volume mati sendiri di forex")
    rel = ind.volume_relatif(data_bervolume(200), 20)
    cek("volume yang nyata menghasilkan angka", bool(rel.notna().any()))

    # --- batas rugi diambil dari struktur, bukan jarak ATR ---------------
    cek("batas rugi struktural dipakai kalau masuk akal",
        strategy.pilih_stop(100.0, 2.0, 3.0, 1, 95.0) == 95.0)
    cek("batas rugi di sisi yang salah ditolak, kembali ke ATR",
        strategy.pilih_stop(100.0, 2.0, 3.0, 1, 105.0) == 94.0,
        "stop beli di ATAS harga masuk langsung tersentuh di lilin yang sama")
    cek("tanpa level struktur tetap memakai ATR seperti dulu",
        strategy.pilih_stop(100.0, 2.0, 3.0, 1, None) == 94.0)
    cek("sisi jual: batas rugi struktural di atas harga masuk",
        strategy.pilih_stop(100.0, 2.0, 3.0, -1, 106.0) == 106.0)
    cek("sisi jual: nilai di bawah harga masuk ditolak",
        strategy.pilih_stop(100.0, 2.0, 3.0, -1, 95.0) == 106.0)

    # --- strategi utuh ---------------------------------------------------
    df = data_bervolume(900, benih=5)
    p = dict(P_KECIL["struktur_harga"])
    d = strategy.beri_sinyal(df, p, "struktur_harga", dua_arah=True)
    masuk = d[d["beli"]]
    cek("strategi struktur harga menghasilkan sinyal", len(masuk) > 0,
        f"{len(masuk)} sinyal beli dari {len(df)} lilin")
    if len(masuk):
        cek("batas rugi selalu di bawah harga masuk",
            bool((masuk["stop_beli"] < masuk["close"]).all()))
        pantulan = masuk[masuk["close"] <= masuk["resistance"]]
        if len(pantulan):
            sesuai = ((pantulan["stop_beli"]
                       - (pantulan["support"] - 0.5 * pantulan["atr"])).abs()
                      < 1e-9).all()
            cek("pada pantulan, batas rugi persis di bawah support",
                bool(sesuai), f"{len(pantulan)} sinyal pantulan diperiksa")
    keluar_dua_arah = d[d["jual"]]
    cek("pasar dua arah membuka sinyal jual", len(keluar_dua_arah) >= 0)
    satu_arah = strategy.beri_sinyal(df, p, "struktur_harga", dua_arah=False)
    cek("pasar satu arah tidak pernah memberi sinyal jual",
        int(satu_arah["jual"].sum()) == 0)

    # --- yang paling penting: tidak mengintip masa depan ------------------
    beda = []
    for potong in (500, 620, 740, 860):
        sebagian = strategy.beri_sinyal(df.iloc[:potong], p, "struktur_harga",
                                        dua_arah=True)
        i = potong - 1
        for k in ("beli", "jual", "tutup_beli", "support", "resistance",
                  "uji_support", "stop_beli"):
            a, b = d[k].iloc[i], sebagian[k].iloc[i]
            cocok = (pd.isna(a) and pd.isna(b)) or a == b
            if not cocok:
                beda.append((potong, k))
    cek("TIDAK mengintip masa depan", not beda,
        "4 titik potong x 7 kolom: memotong data tidak mengubah sinyal di "
        "lilin itu" if not beda else f"berbeda di {beda[:3]}")


def uji_konfirmasi_lapisan() -> None:
    print("\n[15] Lapisan konfirmasi — volume, MACD, imbalan:risiko, jeda")

    df = data_bervolume(700, benih=9)
    p = dict(P_KECIL["tembus_batas"])
    dasar = strategy.beri_sinyal(df, p, "tembus_batas", dua_arah=True)

    def dengan(konf: dict, data: pd.DataFrame = df) -> pd.DataFrame:
        return strategy.beri_sinyal(data, dict(p, konfirmasi=konf),
                                    "tembus_batas", dua_arah=True)

    # --- regresi: mati = tidak berubah sama sekali -----------------------
    mati = {"volume": {"aktif": False}, "macd": {"aktif": False},
            "rr_minimal": 0.0, "tunda_lilin": 0}
    d_mati = dengan(mati)
    cek("semua penyaring mati -> sinyal identik",
        bool(d_mati["beli"].equals(dasar["beli"])
             and d_mati["jual"].equals(dasar["jual"])),
        "inilah yang menjamin hasil backtest lama tidak bergeser")
    cek("konfirmasi kosong -> sinyal identik",
        bool(dengan({})["beli"].equals(dasar["beli"])))

    # --- volume ----------------------------------------------------------
    d_vol = dengan({"volume": {"aktif": True, "periode": 20, "pengali": 1.5}})
    cek("penyaring volume hanya membuang, tidak pernah menambah",
        int((d_vol["beli"] & ~dasar["beli"]).sum()) == 0)
    cek("penyaring volume benar-benar membuang sesuatu",
        int(d_vol["beli"].sum()) < int(dasar["beli"].sum()),
        f"{int(dasar['beli'].sum())} -> {int(d_vol['beli'].sum())} sinyal beli")

    nol = df.assign(volume=0.0)
    dasar_nol = strategy.beri_sinyal(nol, p, "tembus_batas", dua_arah=True)
    d_nol = dengan({"volume": {"aktif": True, "pengali": 1.5,
                               "lewati_jika_kosong": True}}, nol)
    cek("pasar tanpa data volume MELEWATI penyaring, bukan kehilangan sinyal",
        bool(d_nol["beli"].equals(dasar_nol["beli"])),
        "tanpa pengaman ini, seluruh sinyal forex akan hilang")
    d_keras = dengan({"volume": {"aktif": True, "pengali": 1.5,
                                 "lewati_jika_kosong": False}}, nol)
    cek("lewati_jika_kosong: false memang menghentikan sinyalnya",
        int(d_keras["beli"].sum()) == 0)

    # --- MACD ------------------------------------------------------------
    d_macd = dengan({"macd": {"aktif": True}})
    cek("penyaring MACD hanya membuang, tidak pernah menambah",
        int((d_macd["beli"] & ~dasar["beli"]).sum()) == 0
        and int((d_macd["jual"] & ~dasar["jual"]).sum()) == 0)

    # --- gerbang imbalan:risiko ------------------------------------------
    d_rr = dengan({"rr_minimal": 2.0})
    cek("gerbang imbalan:risiko hanya membuang",
        int((d_rr["beli"] & ~dasar["beli"]).sum()) == 0)
    d_rr_mustahil = dengan({"rr_minimal": 999.0})
    cek("ambang yang mustahil menolak semua setup",
        int(d_rr_mustahil["beli"].sum()) == 0)

    # --- jeda sebelum bereaksi -------------------------------------------
    d_tunda = dengan({"tunda_lilin": 1})
    cek("jeda 1 lilin menggeser sinyal tepat satu lilin",
        bool((d_tunda["beli"].values[1:] == dasar["beli"].values[:-1]).all()))
    cek("lilin pertama sesudah jeda tidak memberi sinyal",
        not bool(d_tunda["beli"].iloc[0]))

    # --- yang tidak boleh terjadi ----------------------------------------
    semua_nyala = {"volume": {"aktif": True, "pengali": 1.5},
                   "macd": {"aktif": True}, "rr_minimal": 2.0,
                   "tunda_lilin": 1}
    d_semua = dengan(semua_nyala)
    cek("SINYAL KELUAR tidak pernah ikut tersaring",
        bool(d_semua["tutup_beli"].equals(dasar["tutup_beli"])
             and d_semua["tutup_jual"].equals(dasar["tutup_jual"])),
        "posisi yang terbuka wajib punya jalan keluar apa pun penyaringnya")
    cek("semua penyaring menyala tetap menghasilkan subset",
        int((d_semua["beli"] & ~dasar["beli"].shift(1, fill_value=False)
             ).sum()) == 0)


def uji_waktu_wib() -> None:
    """Jam di pesan dan tanggal jatah harian memakai WIB."""
    print()
    print("[19] Jam WIB di pesan dan jatah harian")
    from src import notify

    cek("jam UTC dari database tampil sebagai WIB",
        waktu.ke_wib("2026-09-13 00:30:00") == "13 Sep 07:30 WIB",
        waktu.ke_wib("2026-09-13 00:30:00"))
    cek("pergantian tanggal ikut bergeser ke WIB",
        waktu.ke_wib("2026-09-09 20:00:00") == "10 Sep 03:00 WIB")
    cek("satu hari WIB = 17:00 UTC kemarin sampai 17:00 UTC",
        waktu.rentang_utc_hari_wib("2026-09-10")
        == ("2026-09-09 17:00:00", "2026-09-10 17:00:00"),
        str(waktu.rentang_utc_hari_wib("2026-09-10")))
    cek("lama tertinggal ditulis dengan satuan yang wajar",
        waktu.lama_teks(pd.Timedelta(minutes=45)) == "45 menit"
        and waktu.lama_teks(pd.Timedelta(hours=5)) == "5 jam"
        and waktu.lama_teks(pd.Timedelta(days=3)) == "3 hari")

    # REGRESI: sinyal 20:00 UTC = 03:00 WIB tanggal berikutnya. Dulu dihitung
    # dengan `ts LIKE 'tanggal%'`, sehingga tidak masuk jatah hari WIB mana pun.
    conn = db.connect(":memory:")
    db.save_signal(conn, "futures", "BTCUSDT", "15menit", "uji",
                   "2026-09-09 20:00:00", "JUAL", 1.0, "uji")
    cek("sinyal 03:00 WIB dihitung untuk tanggal WIB-nya",
        db.hitung_entry_hari_ini(conn, "futures", "2026-09-10") == 1)
    cek("dan tidak lagi dihitung untuk tanggal UTC-nya",
        db.hitung_entry_hari_ini(conn, "futures", "2026-09-09") == 0)
    conn.close()

    pesan = notify.susun_pesan(
        [{"pasar": "futures", "simbol": "BTCUSDT", "aksi": "BELI",
          "harga": 100.0, "alasan": "", "umur_lilin": 2,
          "ts": "2026-09-09 01:15:00"}], "x", "15menit", "Uji")
    cek("umur sinyal di pesan memakai jam WIB",
        "09 Sep 08:15 WIB" in pesan and "2026-09-09 01:15" not in pesan)

    # Backtest berganti hari di tengah malam WIB, sama dengan robot live
    df = data_gelombang(4000, freq="15min")
    b = backtest.Biaya(jenis="persen", fee_persen=0.05)
    h = backtest.jalankan(df, P_KECIL["tembus_batas"], "tembus_batas", b,
                          modal=10_000_000, risiko_persen=1.0, dua_arah=True,
                          simbol="U", maks_per_hari=1)
    per_wib: dict = {}
    for t in h.transaksi:
        tgl = waktu.tanggal_wib(t.waktu_masuk)
        per_wib[tgl] = per_wib.get(tgl, 0) + 1
    terpadat = max(per_wib.values(), default=0)
    cek("backtest: batas 1 per hari berlaku per tanggal WIB",
        0 < terpadat <= 1, f"hari WIB terpadat {terpadat} pembukaan")


def uji_data_bermasalah() -> None:
    """Data yang gagal diperbarui harus terlihat di pesan, bukan hanya di log."""
    print()
    print("[20] Data yang gagal diperbarui harus terlihat")
    from src import notify, sumber_crypto, sumber_futures
    from src.galat import GagalUnduh

    # --- sumber data melempar galat, tidak lagi diam ------------------------
    class Ditolak:
        status_code = 451
        text = "Service unavailable from a restricted location"

    def putus(*a, **k):
        raise ConnectionError("Max retries exceeded")

    asli_get = sumber_futures.requests.get
    try:
        sumber_futures.requests.get = lambda *a, **k: Ditolak()
        try:
            sumber_futures.unduh("BTCUSDT", "15menit")
            galat_http = None
        except GagalUnduh as e:
            galat_http = e
        cek("HTTP ditolak -> GagalUnduh, bukan daftar kosong yang diam",
            galat_http is not None and "451" in galat_http.pesan,
            galat_http.pesan if galat_http else "tidak ada galat")

        sumber_crypto.requests.get = putus
        try:
            sumber_crypto.unduh("BTCUSDT", "harian")
            galat_putus = None
        except GagalUnduh as e:
            galat_putus = e
        cek("sambungan putus -> GagalUnduh dengan sebab singkat",
            galat_putus is not None and "ConnectionError" in galat_putus.pesan,
            galat_putus.pesan if galat_putus else "tidak ada galat")
    finally:
        # Satu modul `requests` dipakai bersama semua sumber — wajib dipulihkan
        sumber_futures.requests.get = asli_get

    # --- perbarui_semua melaporkan, data parsial tetap tersimpan -----------
    conn = db.connect(":memory:")
    parsial = [("2026-09-09 00:00:00", 100.0, 101.0, 99.0, 100.5, 5.0)]

    def unduh_gagal(simbol, timeframe, mulai=None):
        raise GagalUnduh("Binance Futures tidak bisa dihubungi (ConnectTimeout)",
                         parsial, rinci="uji")

    asli_sumber = pasar.SUMBER["futures"]
    cfg = {"futures": {"aktif": True, "watchlist": ["BTCUSDT"]}}
    try:
        pasar.SUMBER["futures"] = types.SimpleNamespace(
            INTERVAL={"15menit": "15m"}, unduh=unduh_gagal)
        hasil, gagal = pasar.perbarui_semua(conn, cfg, "15menit")
    finally:
        pasar.SUMBER["futures"] = asli_sumber
    cek("simbol yang gagal tercatat beserta pasar dan sebabnya",
        "ConnectTimeout" in gagal.get("BTCUSDT futures", ""), str(gagal))
    cek("data yang sempat terambil sebelum gagal tetap disimpan",
        db.count_rows(conn, "futures", "BTCUSDT", "15menit") == 1)
    conn.close()

    # --- data tidak gagal, tapi diam-diam tidak bertambah ------------------
    senin_siang = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)
    conn = db.connect(":memory:")
    db.save_prices(conn, "futures", "BTCUSDT", "15menit",
                   [("2026-09-14 11:45:00", 1, 1, 1, 1, 1)])    # tutup 12:00
    db.save_prices(conn, "futures", "ETHUSDT", "15menit",
                   [("2026-09-14 09:00:00", 1, 1, 1, 1, 1)])    # tutup 09:15
    cfg = {"futures": {"aktif": True,
                       "watchlist": ["BTCUSDT", "ETHUSDT", "SOLUSDT"]}}
    tertinggal = pasar.data_tertinggal(conn, cfg, "15menit",
                                       sekarang=senin_siang)
    cek("lilin yang baru tutup tidak dianggap tertinggal",
        "BTCUSDT futures" not in tertinggal)
    cek("lilin terakhir hampir 3 jam lalu dianggap tertinggal",
        "ETHUSDT futures" in tertinggal, str(tertinggal))
    cek("simbol tanpa data sama sekali ikut dilaporkan",
        "SOLUSDT futures" in tertinggal)
    conn.close()

    conn = db.connect(":memory:")
    db.save_prices(conn, "forex", "GC=F", "1jam",
                   [("2026-09-11 20:00:00", 1, 1, 1, 1, 0)])    # Jumat
    cfg = {"forex": {"aktif": True, "watchlist": [{"simbol": "GC=F"}]}}
    minggu_sore = datetime(2026, 9, 13, 20, 0, tzinfo=timezone.utc)
    senin_pagi = datetime(2026, 9, 14, 6, 0, tzinfo=timezone.utc)
    cek("forex: akhir pekan tidak dihitung sebagai data tertinggal",
        pasar.data_tertinggal(conn, cfg, "1jam", sekarang=minggu_sore) == {})
    cek("forex: Senin tanpa lilin baru dianggap tertinggal",
        "GC=F forex" in pasar.data_tertinggal(conn, cfg, "1jam",
                                              sekarang=senin_pagi))
    conn.close()

    # --- pesan -------------------------------------------------------------
    masalah = {"BTCUSDT futures": "ditolak (HTTP 451) <html>"}
    pesan = notify.susun_pesan([], "x", "15menit", "Uji", masalah=masalah)
    cek("pesan memuat bagian DATA BERMASALAH", "DATA BERMASALAH" in pesan)
    cek("teks galat di-escape supaya HTML Telegram tidak rusak",
        "<html>" not in pesan and "&lt;html&gt;" in pesan)
    cek("pesan tanpa masalah tidak memuat bagian itu",
        "DATA BERMASALAH" not in notify.susun_pesan([], "x", "15menit", "Uji"))

    def sinyal(pasar_nama):
        return [{"pasar": pasar_nama, "simbol": "BTCUSDT", "aksi": "BELI",
                 "harga": 1.0, "alasan": ""}]
    cek("sinyal dari data yang bermasalah diberi peringatan",
        "data simbol ini bermasalah" in notify.susun_pesan(
            sinyal("futures"), "x", "15menit", "Uji", masalah=masalah))
    cek("simbol yang sama di pasar lain TIDAK ikut diberi peringatan",
        "data simbol ini bermasalah" not in notify.susun_pesan(
            sinyal("crypto"), "x", "harian", "Uji", masalah=masalah),
        "BTCUSDT spot dan futures datanya terpisah")
    cek("data yang pulih dikabarkan",
        "kembali normal: ETHUSDT futures" in notify.susun_pesan(
            [], "x", "15menit", "Uji", pulih=["ETHUSDT futures"]))

    # --- token bot tidak boleh tercetak ke log -----------------------------
    token_palsu = "123456789:PALSUxPALSUxPALSUxPALSUxPALSUxPALSU"
    asli_env, asli_post = notify.load_env, notify.requests.post

    def post_putus(url, **k):
        # Meniru galat asli requests, yang memuat jalur URL lengkap
        raise ConnectionError("Max retries exceeded with url: "
                              + url.split("api.telegram.org", 1)[1])

    tangkap = io.StringIO()
    try:
        notify.load_env = lambda: {"TELEGRAM_BOT_TOKEN": token_palsu,
                                   "TELEGRAM_CHAT_ID": "123456789"}
        notify.requests.post = post_putus
        with contextlib.redirect_stdout(tangkap):
            notify.kirim("uji")
    finally:
        notify.load_env, notify.requests.post = asli_env, asli_post
    keluaran = tangkap.getvalue()
    cek("token bot TIDAK tercetak saat pengiriman gagal",
        token_palsu not in keluaran and "PALSU" not in keluaran,
        "galat requests memuat URL .../bot<TOKEN>/sendMessage")
    cek("galatnya tetap tercatat, hanya tokennya yang disamarkan",
        "<token>" in keluaran and "gagal kirim" in keluaran)


def uji_posisi_searah() -> None:
    """Sinyal yang menumpuk taruhan searah diberi peringatan, tidak ditahan."""
    print()
    print("[21] Peringatan posisi searah")
    from src import notify
    from src import signals as sig

    def cfg_searah(watchlist, maks=2):
        return {
            "futures": {"aktif": True, "dua_arah": True,
                        "watchlist": watchlist,
                        "biaya": {"fee_persen": 0.05},
                        "maks_trade_per_hari": 0,
                        "maks_posisi_searah": maks},
            "strategi": {"tembus_batas": {"15menit": P_KECIL["tembus_batas"]}},
            "take_profit_rasio": 0,
            "notifikasi": {"lilin_terakhir_diperiksa": 10,
                           "lacak_posisi": True,
                           "tahan_sinyal_masuk": False,
                           "maks_umur_lilin_masuk": -1},
        }

    def db_naik(daftar_simbol):
        c = db.connect(":memory:")
        for simbol in daftar_simbol:
            baris = [(f"2026-09-09 {i // 60:02d}:{i % 60:02d}:00",
                      100 + i * 0.5 - 0.1, 100 + i * 0.5 + 0.3,
                      100 + i * 0.5 - 0.3, 100 + i * 0.5, 1000.0)
                     for i in range(140)]
            db.save_prices(c, "futures", simbol, "15menit", baris)
        return c

    # --- tiga simbol naik bersamaan dalam satu kali jalan ------------------
    tiga = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    c = db_naik(tiga)
    semua = sig.periksa_semua(c, cfg_searah(tiga), "15menit", "tembus_batas")

    def diperingatkan(simbol):
        return any(s.get("peringatan_searah") for s in semua
                   if s["simbol"] == simbol and s["aksi"] == "BELI")

    cek("simbol pertama dan kedua tidak diperingatkan",
        not diperingatkan("BTCUSDT") and not diperingatkan("ETHUSDT"))
    cek("simbol ketiga yang searah diperingatkan", diperingatkan("SOLUSDT"),
        next((s["peringatan_searah"] for s in semua
              if s.get("peringatan_searah")), "tidak ada peringatan"))
    cek("sinyalnya tetap dikirim, tidak ditahan",
        any(s["simbol"] == "SOLUSDT" and s["aksi"] == "BELI" for s in semua))
    cek("posisinya tetap dicatat seperti biasa",
        len(db.semua_posisi_terbuka(c)) == 3)
    c.close()

    c = db_naik(tiga)
    mati = sig.periksa_semua(c, cfg_searah(tiga, maks=0), "15menit",
                             "tembus_batas")
    cek("maks_posisi_searah 0 mematikan peringatan",
        not any(s.get("peringatan_searah") for s in mati))
    c.close()

    # --- hanya arah dan timeframe yang sama yang dihitung ------------------
    def sol_diperingatkan(posisi_lain) -> bool:
        c = db_naik(["SOLUSDT"])
        for simbol, arah, tf in posisi_lain:
            db.buka_posisi(c, "futures", simbol, tf, "tembus_batas",
                           arah=arah, ts="2026-09-09 00:00:00", harga=100.0,
                           stop=None, target=None)
        hasil = sig.periksa_semua(c, cfg_searah(["SOLUSDT"]), "15menit",
                                  "tembus_batas")
        c.close()
        return any(s.get("peringatan_searah") for s in hasil)

    cek("dua posisi BELI lain di timeframe sama -> diperingatkan",
        sol_diperingatkan([("BTCUSDT", 1, "15menit"),
                           ("ETHUSDT", 1, "15menit")]))
    cek("posisi berlawanan arah tidak dihitung",
        not sol_diperingatkan([("BTCUSDT", -1, "15menit"),
                               ("ETHUSDT", -1, "15menit")]))
    cek("posisi di timeframe lain tidak dihitung",
        not sol_diperingatkan([("BTCUSDT", 1, "harian"),
                               ("ETHUSDT", 1, "harian")]))

    pesan = notify.susun_pesan(
        [{"pasar": "futures", "simbol": "SOLUSDT", "aksi": "JUAL",
          "harga": 1.0, "alasan": "",
          "peringatan_searah": "sudah ada 2 posisi JUAL terbuka "
                               "(BTCUSDT, ETHUSDT) — sinyal ini menambah "
                               "taruhan yang sama"}], "x", "15menit", "Uji")
    cek("peringatan tampil di pesan", "sudah ada 2 posisi JUAL" in pesan)


def uji_order_minimum() -> None:
    """Backtest menolak order yang di bursa sungguhan juga ditolak."""
    print()
    print("[22] Order minimum bursa di backtest")

    sol = {"min_qty": 0.01, "step_qty": 0.01, "min_notional": 5}
    cek("jumlah dibulatkan ke bawah ke kelipatan langkah",
        abs(backtest.bulatkan_order(0.0149, 1000.0, sol) - 0.01) < 1e-12,
        str(backtest.bulatkan_order(0.0149, 1000.0, sol)))
    cek("galat float tidak membuang satu langkah (0,3 / 0,1)",
        abs(backtest.bulatkan_order(0.3, 100.0, {"step_qty": 0.1}) - 0.3)
        < 1e-12)
    cek("di bawah jumlah minimum -> ditolak",
        backtest.bulatkan_order(0.009, 1000.0, sol) == 0.0)
    cek("nilai di bawah min_notional -> ditolak",
        backtest.bulatkan_order(0.04, 100.0, sol) == 0.0,
        "0,04 x 100 = $4, di bawah $5")
    cek("tanpa aturan, jumlah tidak diubah",
        backtest.bulatkan_order(0.0149, 1000.0, None) == 0.0149)

    df = data_gelombang(1200)
    b = backtest.Biaya(jenis="persen", fee_persen=0.05)
    p = P_KECIL["tembus_batas"]
    kw = dict(risiko_persen=1.0, dua_arah=True, simbol="U", leverage=3.0)

    dasar = backtest.jalankan(df, p, "tembus_batas", b, modal=100.0, **kw)
    tanpa = backtest.jalankan(df, p, "tembus_batas", b, modal=100.0,
                              batas_order=None, **kw)
    cek("batas_order None identik dengan sebelumnya",
        tanpa.jumlah_transaksi == dasar.jumlah_transaksi
        and abs(tanpa.modal_akhir - dasar.modal_akhir) < 1e-9
        and tanpa.ditolak_bursa == 0)

    mustahil = backtest.jalankan(df, p, "tembus_batas", b, modal=100.0,
                                 batas_order={"min_notional": 1e12}, **kw)
    cek("order yang mustahil dipenuhi: tidak ada transaksi",
        mustahil.jumlah_transaksi == 0)
    cek("dan penolakannya dihitung",
        mustahil.ditolak_bursa >= dasar.jumlah_transaksi > 0,
        f"{mustahil.ditolak_bursa} ditolak; tanpa aturan "
        f"{dasar.jumlah_transaksi} transaksi")
    cek("modal tidak berubah kalau semua order ditolak",
        abs(mustahil.modal_akhir - 100.0) < 1e-9)

    kasar = backtest.jalankan(df, p, "tembus_batas", b, modal=100.0,
                              batas_order=sol, **kw)
    cek("dengan aturan yang wajar, transaksi tetap terjadi",
        kasar.jumlah_transaksi > 0, f"{kasar.jumlah_transaksi} transaksi")
    cek("setiap posisi berukuran kelipatan langkah",
        all(abs(t.unit / 0.01 - round(t.unit / 0.01)) < 1e-6
            for t in kasar.transaksi))
    cek("setiap posisi memenuhi nilai minimum",
        all(t.unit * t.harga_masuk >= 5 - 1e-9 for t in kasar.transaksi))

    cfg = {"backtest": {"modal": 10_000_000},
           "futures": {"modal_backtest": 100, "batas_order": {"SOLUSDT": sol}}}
    cek("modal backtest futures memakai modal nyata",
        pasar.modal_backtest(cfg, "futures") == 100.0)
    cek("pasar lain tetap memakai modal besar seperti dulu",
        pasar.modal_backtest(cfg, "crypto") == 10_000_000.0)
    cek("aturan order dibaca per simbol dan per pasar",
        pasar.batas_order(cfg, "futures", "SOLUSDT") == sol
        and pasar.batas_order(cfg, "futures", "BTCUSDT") is None
        and pasar.batas_order(cfg, "crypto", "SOLUSDT") is None)


def main() -> int:
    print("=" * 62)
    print("  UJI MANDIRI — tanpa jaringan, memakai data buatan")
    print("=" * 62)

    uji_indikator()
    uji_donchian()
    uji_perpotongan()
    uji_biaya()
    uji_ukuran_posisi()
    uji_strategi()
    uji_backtest()
    uji_database()
    uji_pembersihan_data()
    uji_futures()
    uji_batas_harian()
    uji_lacak_posisi()
    uji_pesan()
    uji_struktur_harga()
    uji_konfirmasi_lapisan()
    uji_sinyal_masuk_beruntun()
    uji_sinyal_basi()
    uji_scalping_dua_timeframe()
    uji_waktu_wib()
    uji_data_bermasalah()
    uji_posisi_searah()
    uji_order_minimum()

    print("\n" + "=" * 62)
    if GAGAL == 0:
        print(f"  SEMUA LOLOS — {LOLOS} pemeriksaan")
        print("=" * 62)
        return 0
    print(f"  ADA YANG GAGAL — {GAGAL} gagal, {LOLOS} lolos")
    print("=" * 62)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
