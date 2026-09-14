"""Indikator teknikal, ditulis dengan pandas murni.

Sengaja tidak memakai TA-Lib: pemasangannya di Windows merepotkan, sedangkan
rumus yang dibutuhkan di sini pendek dan bisa diperiksa sendiri.
"""
from __future__ import annotations

import pandas as pd


def sma(series: pd.Series, periode: int) -> pd.Series:
    """Rata-rata bergerak sederhana."""
    return series.rolling(window=periode, min_periods=periode).mean()


def ema(series: pd.Series, periode: int) -> pd.Series:
    """Rata-rata bergerak eksponensial."""
    return series.ewm(span=periode, adjust=False).mean()


def rsi(series: pd.Series, periode: int = 14) -> pd.Series:
    """Relative Strength Index dengan pemulusan Wilder (standar industri).

    Nilainya 0-100. Di atas 70 lazim disebut jenuh beli, di bawah 30 jenuh jual.
    """
    selisih = series.diff()
    naik = selisih.clip(lower=0)
    turun = -selisih.clip(upper=0)

    # alpha = 1/periode adalah pemulusan Wilder, bukan EMA biasa
    rata_naik = naik.ewm(alpha=1 / periode, adjust=False).mean()
    rata_turun = turun.ewm(alpha=1 / periode, adjust=False).mean()

    rs = rata_naik / rata_turun.replace(0, pd.NA)
    hasil = 100 - (100 / (1 + rs))
    # Saat tidak ada penurunan sama sekali, RSI bernilai 100
    return hasil.fillna(100).where(rata_turun.notna(), other=pd.NA)


def macd(series: pd.Series, cepat: int = 12, lambat: int = 26,
         sinyal: int = 9) -> pd.DataFrame:
    """MACD beserta garis sinyal dan histogramnya."""
    garis = ema(series, cepat) - ema(series, lambat)
    garis_sinyal = ema(garis, sinyal)
    return pd.DataFrame({
        "macd": garis,
        "macd_signal": garis_sinyal,
        "macd_hist": garis - garis_sinyal,
    })


def atr(df: pd.DataFrame, periode: int = 14) -> pd.Series:
    """Average True Range — ukuran seberapa liar harga bergerak per lilin.

    Dipakai untuk menentukan batas rugi yang menyesuaikan volatilitas aset,
    bukan persentase tetap yang sama untuk semua aset. Ini penting sekali di
    crypto, yang gerakan hariannya bisa berkali-kali lipat forex.
    """
    tinggi, rendah, tutup = df["high"], df["low"], df["close"]
    tutup_kemarin = tutup.shift(1)

    true_range = pd.concat([
        tinggi - rendah,
        (tinggi - tutup_kemarin).abs(),
        (rendah - tutup_kemarin).abs(),
    ], axis=1).max(axis=1)

    return true_range.ewm(alpha=1 / periode, adjust=False).mean()


def bollinger(series: pd.Series, periode: int = 20, k: float = 2.0) -> pd.DataFrame:
    """Pita Bollinger: rata-rata plus/minus sekian simpangan baku."""
    tengah = sma(series, periode)
    simpangan = series.rolling(window=periode, min_periods=periode).std()
    return pd.DataFrame({
        "bb_tengah": tengah,
        "bb_atas": tengah + k * simpangan,
        "bb_bawah": tengah - k * simpangan,
    })


def pivot(df: pd.DataFrame, kiri: int = 3, kanan: int = 3) -> pd.DataFrame:
    """Titik ayun: lilin yang tertinggi/terendah di antara tetangganya.

    Sebuah lilin disebut pivot tinggi kalau `high`-nya adalah yang tertinggi
    di antara `kiri` lilin sebelum dan `kanan` lilin sesudahnya. Inilah bahan
    mentah untuk menandai level support dan resistance.

    PERINGATAN — fungsi ini MENGINTIP `kanan` lilin ke depan, dan memang harus
    begitu: definisi pivot tidak bisa lain. Karena itu keluarannya TIDAK BOLEH
    dipakai langsung untuk mengambil keputusan. Pemakainya wajib menggeser
    hasilnya `kanan` lilin, seperti yang dilakukan `level_struktur()` di bawah.

    Melewatkan pergeseran itu membuat robot seolah tahu masa depan, dan
    backtest akan terlihat indah tanpa sebab yang nyata.
    """
    jendela = kiri + kanan + 1
    tertinggi = df["high"].rolling(jendela, center=True,
                                   min_periods=jendela).max()
    terendah = df["low"].rolling(jendela, center=True,
                                 min_periods=jendela).min()
    return pd.DataFrame({
        "pivot_tinggi": (df["high"] >= tertinggi) & tertinggi.notna(),
        "pivot_rendah": (df["low"] <= terendah) & terendah.notna(),
    })


def _pivot_ke_belakang(harga_pivot: pd.Series, mundur: int,
                       index: pd.Index) -> pd.Series:
    """Harga pivot ke-`mundur` sebelum yang terakhir, disebar ke tiap lilin.

    `harga_pivot` hanya berisi angka pada lilin tempat pivot dikonfirmasi.
    Dengan membuang barisan kosongnya lebih dulu, `shift(mundur)` bergerak
    per PIVOT, bukan per lilin — itulah yang membuat "dua pivot yang lalu"
    berarti apa yang kita maksud, bukan "dua lilin yang lalu".
    """
    hanya = harga_pivot.dropna()
    if hanya.empty:
        return pd.Series(float("nan"), index=index)
    return hanya.shift(mundur).reindex(index).ffill()


def level_struktur(df: pd.DataFrame, kiri: int = 3, kanan: int = 3,
                   toleransi_atr: float = 0.5, atr_periode: int = 14,
                   maks_uji: int = 5, maks_level: int = 10) -> pd.DataFrame:
    """Level support & resistance dari pivot, plus berapa kali diuji.

    Menghasilkan empat kolom:

        support        pivot rendah TERDEKAT DI BAWAH harga sekarang
        resistance     pivot tinggi TERDEKAT DI ATAS harga sekarang
        uji_support    berapa pivot rendah terakhir yang berdekatan dengannya
        uji_resistance sama, untuk sisi atas

    Yang dicari adalah level terdekat DI SISI YANG BENAR, bukan sekadar pivot
    yang paling baru. Bedanya besar sekali dan sempat membuat strategi ini
    tidak pernah memberi sinyal: dalam tren naik, harga biasanya sudah berada
    DI ATAS pivot tinggi terakhir, sehingga "resistance" justru jatuh di bawah
    harga, jarak ke target jadi negatif, dan gerbang imbalan:risiko menolak
    semua setup. Support pun tertinggal jauh — jaraknya terukur 6 ATR di
    bawah harga saat tren naik.

    Karena itu `maks_level` pivot terakhir masing-masing sisi ikut
    dipertimbangkan, lalu dipilih yang paling dekat dengan harga: yang
    tertinggi di antara yang masih di bawah harga, dan yang terendah di antara
    yang masih di atasnya.

    "Berdekatan" diukur dalam kelipatan ATR, bukan persen tetap, supaya satu
    angka bisa dipakai untuk BTC maupun EURUSD yang skalanya jauh berbeda.

    Hitungan uji itu yang mewujudkan kalimat "semakin sering harga memantul di
    level itu, semakin kuat levelnya": level yang hanya sekali tersentuh
    bernilai 1, level yang berkali-kali diuji bernilai lebih besar.

    Seluruh keluaran sudah digeser `kanan` lilin, jadi pada lilin ke-i hanya
    memuat level yang benar-benar sudah diketahui saat itu.
    """
    piv = pivot(df, kiri, kanan)

    # Pivot di lilin j baru KETAHUAN di lilin j+kanan. Harga pivotnya sendiri
    # ikut digeser supaya angka dan waktunya cocok.
    rendah_tampak = piv["pivot_rendah"].shift(kanan, fill_value=False).astype(bool)
    tinggi_tampak = piv["pivot_tinggi"].shift(kanan, fill_value=False).astype(bool)
    harga_rendah = df["low"].shift(kanan).where(rendah_tampak)
    harga_tinggi = df["high"].shift(kanan).where(tinggi_tampak)

    # `maks_level` pivot terakhir dijajarkan sebagai kolom, lalu dipilih yang
    # terdekat di sisi yang benar. Semua operasinya sekali jalan atas seluruh
    # kolom, jadi tetap cepat walau datanya ratusan ribu lilin.
    tutup = df["close"]
    calon_rendah = pd.concat(
        [_pivot_ke_belakang(harga_rendah, k, df.index)
         for k in range(maks_level)], axis=1)
    calon_tinggi = pd.concat(
        [_pivot_ke_belakang(harga_tinggi, k, df.index)
         for k in range(maks_level)], axis=1)

    # Support = yang TERTINGGI di antara pivot rendah yang masih di bawah
    # harga; resistance = yang TERENDAH di antara pivot tinggi di atas harga.
    support = calon_rendah.where(calon_rendah.le(tutup, axis=0)).max(axis=1)
    resistance = calon_tinggi.where(calon_tinggi.ge(tutup, axis=0)).min(axis=1)

    nilai_atr = atr(df, atr_periode)
    toleransi = toleransi_atr * nilai_atr

    def _hitung(harga_pivot: pd.Series, acuan: pd.Series) -> pd.Series:
        # Level terakhir itu sendiri sudah terhitung satu kali sentuhan.
        jumlah = pd.Series(1, index=df.index, dtype=int)
        for mundur in range(1, maks_uji + 1):
            lalu = _pivot_ke_belakang(harga_pivot, mundur, df.index)
            dekat = ((lalu - acuan).abs() <= toleransi).fillna(False)
            jumlah = jumlah + dekat.astype(int)
        return jumlah.where(acuan.notna(), other=0)

    return pd.DataFrame({
        "support": support,
        "resistance": resistance,
        "uji_support": _hitung(harga_rendah, support),
        "uji_resistance": _hitung(harga_tinggi, resistance),
    })


def volume_relatif(df: pd.DataFrame, periode: int = 20) -> pd.Series:
    """Volume dibanding rata-ratanya: 1,5 berarti satu setengah kali biasanya.

    Dipakai untuk memisahkan tembusan yang sungguhan dari yang palsu — sebuah
    tembusan yang tidak disertai kenaikan volume biasanya jebakan.

    Mengembalikan NaN seluruhnya kalau pasar itu tidak punya data volume.
    Ini BUKAN kelalaian melainkan pengaman: Yahoo Finance melaporkan volume 0
    untuk seluruh 5.908 baris EURUSD=X. Penyaring volume yang tidak
    memperhatikan hal ini akan memblokir SEMUA sinyal forex, padahal forex
    satu-satunya pasar yang pernah lolos aturan penerimaan di project ini.
    """
    if "volume" not in df.columns:
        return pd.Series(float("nan"), index=df.index)

    v = pd.to_numeric(df["volume"], errors="coerce").astype(float)
    if not (v > 0).any():
        return pd.Series(float("nan"), index=df.index)

    rata = v.rolling(periode, min_periods=periode).mean()
    return v / rata.replace(0, float("nan"))


def pin_bar(df: pd.DataFrame, rasio_ekor: float = 2.0,
            maks_badan: float = 0.35) -> pd.DataFrame:
    """Lilin berekor panjang — tanda harga ditolak di suatu level.

    Dua kolom: `pin_naik` (ekor bawah panjang, penolakan ke atas) dan
    `pin_turun` (ekor atas panjang, penolakan ke bawah).

    Syaratnya dua: ekor di sisi itu minimal `rasio_ekor` kali panjang badan,
    dan badannya tidak lebih dari `maks_badan` bagian dari seluruh rentang
    lilin. Syarat kedua penting — tanpa itu lilin besar yang kebetulan
    berekor ikut terhitung, padahal artinya berbeda sama sekali.
    """
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    rentang = (h - l).replace(0, float("nan"))
    badan = (c - o).abs()
    atas = h - pd.concat([o, c], axis=1).max(axis=1)
    bawah = pd.concat([o, c], axis=1).min(axis=1) - l

    badan_kecil = ((badan / rentang) <= maks_badan).fillna(False)
    # Badan nol (open == close) itu doji — penolakan paling murni, dan
    # pembagian dengannya akan meledak. Diperlakukan sebagai badan sangat
    # kecil, bukan dibuang.
    b = badan.where(badan > 0, rentang * 1e-6)

    return pd.DataFrame({
        "pin_naik": (((bawah / b) >= rasio_ekor) & badan_kecil
                     & (bawah > atas)).fillna(False),
        "pin_turun": (((atas / b) >= rasio_ekor) & badan_kecil
                      & (atas > bawah)).fillna(False),
    })


def engulfing(df: pd.DataFrame) -> pd.DataFrame:
    """Lilin yang badannya menelan badan lilin sebelumnya.

    `telan_naik`: lilin hijau yang badannya menutupi seluruh badan lilin merah
    sebelumnya — pembeli mengambil alih. `telan_turun` kebalikannya.

    Yang dibandingkan hanya BADAN (open-close), bukan seluruh rentang termasuk
    ekor. Itu definisi yang lazim dan yang dimaksud dokumen rujukan.
    """
    o, c = df["open"], df["close"]
    o_lalu, c_lalu = o.shift(1), c.shift(1)

    atas_kini = pd.concat([o, c], axis=1).max(axis=1)
    bawah_kini = pd.concat([o, c], axis=1).min(axis=1)
    atas_lalu = pd.concat([o_lalu, c_lalu], axis=1).max(axis=1)
    bawah_lalu = pd.concat([o_lalu, c_lalu], axis=1).min(axis=1)

    menelan = (bawah_kini <= bawah_lalu) & (atas_kini >= atas_lalu)
    return pd.DataFrame({
        "telan_naik": (menelan & (c > o) & (c_lalu < o_lalu)).fillna(False),
        "telan_turun": (menelan & (c < o) & (c_lalu > o_lalu)).fillna(False),
    })


def bias_struktur(df: pd.DataFrame, kiri: int = 2, kanan: int = 2) -> pd.Series:
    """Arah pasar menurut STRUKTUR: +1 naik, -1 turun, 0 tidak jelas.

    Naik berarti puncak terakhir lebih tinggi dari puncak sebelumnya DAN
    lembah terakhir lebih tinggi dari lembah sebelumnya (higher high + higher
    low). Turun kebalikannya.

    Kalau keduanya tidak sejalan — puncak naik tapi lembah turun — hasilnya 0.
    Dokumen rujukan memang meminta keadaan seperti itu (sideways/choppy)
    dilewati, bukan ditebak arahnya.

    Seperti `level_struktur`, pivotnya digeser `kanan` lilin supaya hanya
    memakai titik ayun yang sudah benar-benar terkonfirmasi saat itu.
    """
    piv = pivot(df, kiri, kanan)
    tinggi_tampak = piv["pivot_tinggi"].shift(kanan, fill_value=False).astype(bool)
    rendah_tampak = piv["pivot_rendah"].shift(kanan, fill_value=False).astype(bool)
    harga_tinggi = df["high"].shift(kanan).where(tinggi_tampak)
    harga_rendah = df["low"].shift(kanan).where(rendah_tampak)

    t1 = _pivot_ke_belakang(harga_tinggi, 0, df.index)
    t2 = _pivot_ke_belakang(harga_tinggi, 1, df.index)
    r1 = _pivot_ke_belakang(harga_rendah, 0, df.index)
    r2 = _pivot_ke_belakang(harga_rendah, 1, df.index)

    hasil = pd.Series(0, index=df.index, dtype=int)
    hasil[((t1 > t2) & (r1 > r2)).fillna(False)] = 1
    hasil[((t1 < t2) & (r1 < r2)).fillna(False)] = -1
    return hasil


def dalam_sesi(index: pd.DatetimeIndex, mulai_utc: int,
               akhir_utc: int) -> pd.Series:
    """Apakah lilin jatuh di dalam jam sesi tertentu? (jam UTC, 0-23)

    Dipakai membatasi entry ke sesi ramai saja sesuai dokumen rujukan: emas
    saat overlap London-New York, GBPUSD saat London dibuka. Di luar jam itu
    spread melebar dan gerakannya lebih acak.

    Harga disimpan dalam UTC tanpa zona waktu, sedangkan jam yang disebut
    orang biasanya WIB. WIB = UTC + 7, jadi 19:00 WIB = 12:00 UTC.

    `akhir_utc` inklusif. Rentang yang melewati tengah malam (mis. 22 sampai 2)
    ikut ditangani.
    """
    jam = pd.Series(index.hour, index=index)
    if mulai_utc <= akhir_utc:
        return (jam >= mulai_utc) & (jam <= akhir_utc)
    return (jam >= mulai_utc) | (jam <= akhir_utc)


def donchian(df: pd.DataFrame, periode: int) -> pd.DataFrame:
    """Saluran Donchian: tertinggi dan terendah sekian lilin SEBELUMNYA.

    Dipanggil dua kali oleh strategi tembus batas — sekali dengan periode
    masuk (mis. 20) dan sekali dengan periode keluar (mis. 10) — supaya
    aturan masuk dan keluar tidak terpaksa memakai jendela yang sama.

    `.shift(1)` di sini bukan hiasan, melainkan syarat mutlak. Tanpa itu,
    lilin hari ini ikut dihitung sebagai bagian dari "tertinggi 20 lilin",
    sehingga harga tidak akan pernah bisa menembus batasnya sendiri ke atas.
    Backtest jadi berbohong karena memakai informasi yang belum ada saat
    keputusan seharusnya diambil.
    """
    return pd.DataFrame({
        "dc_atas": df["high"].rolling(periode, min_periods=periode)
                             .max().shift(1),
        "dc_bawah": df["low"].rolling(periode, min_periods=periode)
                             .min().shift(1),
    })
