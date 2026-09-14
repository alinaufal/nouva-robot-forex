"""Tiga strategi yang diadu, memakai kerangka keluaran yang sama.

Setiap strategi mengisi empat kolom boolean:

    beli        masuk posisi beli (long)
    tutup_beli  keluar dari posisi beli
    jual        masuk posisi jual (short) — hanya kalau pasarnya dua arah
    tutup_jual  keluar dari posisi jual

Bentuk yang seragam ini yang membuat satu mesin backtest bisa menguji ketiganya
tanpa cabang khusus, sehingga perbandingannya adil.

Crypto spot hanya bisa dibeli, jadi `dua_arah=False` dan kolom `jual` selalu
kosong. Di forex, turunnya EURUSD sama saja dengan naiknya USD, jadi
`dua_arah=True` dan sisi jual ikut dipakai.
"""
from __future__ import annotations

import pandas as pd

from . import indicators as ind

NAMA = {
    "ikut_tren": "Ikut tren (potong SMA)",
    "balik_rata2": "Balik ke rata-rata (Bollinger + RSI)",
    "tembus_batas": "Tembus batas (Donchian)",
    "struktur_harga": "Struktur harga (support/resistance + volume)",
    "scalping_h1_m15": "Scalping peta-eksekusi (2 timeframe)",
}


def _bool(s: pd.Series) -> pd.Series:
    """Jadikan Series boolean bersih: NaN dianggap False, tipe dipastikan bool.

    Perbandingan yang melibatkan NaN menghasilkan tipe `object`, dan operator
    `~` pada object memberi angka (-1/-2) yang dua-duanya dianggap benar.
    Melewatkan langkah ini adalah sumber bug yang dulu membuat sinyal muncul
    setiap hari di robot saham.
    """
    return s.fillna(False).astype(bool)


def _potong(cepat: pd.Series, lambat: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Deteksi perpotongan dua garis: (potong_naik, potong_turun).

    Kedua garis harus sudah punya nilai hari ini MAUPUN kemarin. Tanpa syarat
    itu, lilin pertama sesudah masa pemanasan terbaca sebagai "perpotongan"
    padahal sebelumnya memang belum ada angka untuk dibandingkan.
    """
    valid = cepat.notna() & lambat.notna()
    valid_kemarin = valid.shift(1, fill_value=False).astype(bool)

    di_atas = ((cepat > lambat) & valid).astype(bool)
    # fill_value + astype(bool) wajib: shift() pada kolom boolean menghasilkan
    # tipe object, lihat penjelasan di _bool() di atas.
    di_atas_kemarin = di_atas.shift(1, fill_value=False).astype(bool)

    bisa_dibanding = valid & valid_kemarin
    return (bisa_dibanding & di_atas & ~di_atas_kemarin,
            bisa_dibanding & ~di_atas & di_atas_kemarin)


# --------------------------------------------------------------------------
# Strategi 1 — ikut tren
# --------------------------------------------------------------------------

def _ikut_tren(df: pd.DataFrame, p: dict, dua_arah: bool) -> pd.DataFrame:
    out = df.copy()
    out["sma_cepat"] = ind.sma(out["close"], p["sma_cepat"])
    out["sma_lambat"] = ind.sma(out["close"], p["sma_lambat"])
    out["sma_tren"] = ind.sma(out["close"], p["sma_tren"])
    out["rsi"] = ind.rsi(out["close"], p["rsi_periode"])
    out["atr"] = ind.atr(out, p["atr_periode"])

    naik, turun = _potong(out["sma_cepat"], out["sma_lambat"])
    tren_naik = _bool(out["close"] > out["sma_tren"])
    tren_turun = _bool(out["close"] < out["sma_tren"])
    rsi_aman = _bool(out["rsi"] < p["rsi_maks_beli"])

    out["beli"] = naik & tren_naik & rsi_aman
    out["tutup_beli"] = turun
    out["jual"] = (turun & tren_turun) if dua_arah else False
    out["tutup_jual"] = naik

    out["alasan_beli"] = (f"SMA{p['sma_cepat']} memotong ke atas "
                          f"SMA{p['sma_lambat']}, harga di atas "
                          f"SMA{p['sma_tren']}, RSI belum jenuh beli")
    out["alasan_jual"] = (f"SMA{p['sma_cepat']} memotong ke bawah "
                          f"SMA{p['sma_lambat']}, harga di bawah "
                          f"SMA{p['sma_tren']}")
    return out


# --------------------------------------------------------------------------
# Strategi 2 — balik ke rata-rata
# --------------------------------------------------------------------------

def _balik_rata2(df: pd.DataFrame, p: dict, dua_arah: bool) -> pd.DataFrame:
    out = df.copy()
    bb = ind.bollinger(out["close"], p["bb_periode"], p["bb_k"])
    out[bb.columns] = bb
    out["sma_tren"] = ind.sma(out["close"], p["sma_tren"])
    out["rsi"] = ind.rsi(out["close"], p["rsi_periode"])
    out["atr"] = ind.atr(out, p["atr_periode"])

    batas_bawah = p["rsi_maks_beli"]          # mis. 30
    batas_atas = 100 - batas_bawah            # cerminnya, mis. 70

    tren_naik = _bool(out["close"] > out["sma_tren"])
    tren_turun = _bool(out["close"] < out["sma_tren"])

    # Penyaring tren wajib ada. Tanpanya strategi ini membeli terus-menerus
    # sepanjang pasar rontok — persis "menangkap pisau jatuh".
    out["beli"] = (_bool(out["close"] <= out["bb_bawah"])
                   & _bool(out["rsi"] < batas_bawah)
                   & tren_naik)
    out["tutup_beli"] = _bool(out["close"] >= out["bb_tengah"])

    if dua_arah:
        out["jual"] = (_bool(out["close"] >= out["bb_atas"])
                       & _bool(out["rsi"] > batas_atas)
                       & tren_turun)
    else:
        out["jual"] = False
    out["tutup_jual"] = _bool(out["close"] <= out["bb_tengah"])

    out["alasan_beli"] = (f"harga menyentuh pita bawah Bollinger, "
                          f"RSI di bawah {batas_bawah}, tren panjang masih naik")
    out["alasan_jual"] = (f"harga menyentuh pita atas Bollinger, "
                          f"RSI di atas {batas_atas}, tren panjang sedang turun")
    return out


# --------------------------------------------------------------------------
# Strategi 3 — tembus batas
# --------------------------------------------------------------------------

def _tembus_batas(df: pd.DataFrame, p: dict, dua_arah: bool) -> pd.DataFrame:
    out = df.copy()
    masuk = ind.donchian(out, p["donchian_masuk"])
    keluar = ind.donchian(out, p["donchian_keluar"])
    out["dc_masuk_atas"] = masuk["dc_atas"]
    out["dc_masuk_bawah"] = masuk["dc_bawah"]
    out["dc_keluar_atas"] = keluar["dc_atas"]
    out["dc_keluar_bawah"] = keluar["dc_bawah"]
    out["sma_tren"] = ind.sma(out["close"], p["sma_tren"])
    out["atr"] = ind.atr(out, p["atr_periode"])

    tren_naik = _bool(out["close"] > out["sma_tren"])
    tren_turun = _bool(out["close"] < out["sma_tren"])

    out["beli"] = _bool(out["close"] > out["dc_masuk_atas"]) & tren_naik
    out["tutup_beli"] = _bool(out["close"] < out["dc_keluar_bawah"])

    if dua_arah:
        out["jual"] = _bool(out["close"] < out["dc_masuk_bawah"]) & tren_turun
    else:
        out["jual"] = False
    out["tutup_jual"] = _bool(out["close"] > out["dc_keluar_atas"])

    out["alasan_beli"] = (f"harga menembus tertinggi {p['donchian_masuk']} "
                          f"lilin terakhir, tren panjang masih naik")
    out["alasan_jual"] = (f"harga menembus terendah {p['donchian_masuk']} "
                          f"lilin terakhir, tren panjang sedang turun")
    return out


# --------------------------------------------------------------------------
# Strategi 4 — struktur harga
# --------------------------------------------------------------------------

def _struktur_harga(df: pd.DataFrame, p: dict, dua_arah: bool) -> pd.DataFrame:
    """Tujuh langkah entry dari dokumen rujukan, dijalankan apa adanya.

    Bedanya dengan tiga strategi lain: level acuannya diambil dari STRUKTUR
    harga (titik ayun yang sudah berkali-kali diuji), bukan dari rata-rata
    atau pita. Batas ruginya pun diletakkan di bawah level itu, bukan sejauh
    sekian ATR — persis yang diminta langkah 5, "bukan angka sembarangan".

    Ada dua cara masuk, keduanya disebut dokumen:

      pantulan  harga mendekati level yang kuat, momentum mengonfirmasi,
                dan rasio imbalan-risikonya layak            (langkah 1,2,3,5)
      tembusan  harga menembus level DISERTAI lonjakan volume  (langkah 4)

    Tembusan tanpa volume sengaja tidak diambil. Di pasar yang datanya tidak
    punya volume sama sekali (forex Yahoo), jalur tembusan otomatis mati dan
    hanya jalur pantulan yang bekerja — lebih baik melewatkan peluang daripada
    menebak-nebak keabsahan sebuah tembusan.
    """
    out = df.copy()
    lv = ind.level_struktur(out, p["pivot_kiri"], p["pivot_kanan"],
                            p["toleransi_atr"], p["atr_periode"],
                            p["maks_uji"], p.get("maks_level", 10))
    out[lv.columns] = lv
    out["atr"] = ind.atr(out, p["atr_periode"])
    out["rsi"] = ind.rsi(out["close"], p["rsi_periode"])
    mcd = ind.macd(out["close"], p["macd_cepat"], p["macd_lambat"],
                   p["macd_signal"])
    out[mcd.columns] = mcd
    out["sma_tren"] = ind.sma(out["close"], p["sma_tren"])
    out["vol_relatif"] = ind.volume_relatif(out, p["volume_periode"])

    nilai_atr = out["atr"]
    tren_naik = _bool(out["close"] > out["sma_tren"])
    tren_turun = _bool(out["close"] < out["sma_tren"])

    # Langkah 1 & 2 — harga benar-benar mendekati level, dan levelnya kuat
    dekat_sup = _bool((out["close"] - out["support"]).abs()
                      <= p["dekat_atr"] * nilai_atr)
    dekat_res = _bool((out["close"] - out["resistance"]).abs()
                      <= p["dekat_atr"] * nilai_atr)
    kuat_sup = _bool(out["uji_support"] >= p["min_uji"])
    kuat_res = _bool(out["uji_resistance"] >= p["min_uji"])

    # Langkah 3 — konfirmasi momentum. RSI ATAU MACD, salah satu cukup:
    # dokumen menyebut keduanya sebagai konfirmasi kedua, bukan syarat ganda.
    hist = out["macd_hist"]
    momentum_beli = (_bool(out["rsi"] < p["rsi_batas_beli"])
                     | _bool(hist > hist.shift(1)))
    momentum_jual = (_bool(out["rsi"] > 100 - p["rsi_batas_beli"])
                     | _bool(hist < hist.shift(1)))

    # Langkah 4 — tembusan wajib disertai volume
    vol_cukup = _bool(out["vol_relatif"] >= p["volume_pengali"])
    tembus_beli = (_bool(out["close"] > out["resistance"]) & vol_cukup
                   & tren_naik)
    tembus_jual = (_bool(out["close"] < out["support"]) & vol_cukup
                   & tren_turun)

    # Langkah 5 — batas rugi dari struktur. Untuk tembusan, level yang baru
    # ditembus berbalik peran menjadi penopang, jadi stopnya di sana.
    penyangga = p["penyangga_atr"] * nilai_atr
    out["stop_beli"] = (out["resistance"] - penyangga).where(
        tembus_beli, out["support"] - penyangga)
    out["stop_jual"] = (out["support"] + penyangga).where(
        tembus_jual, out["resistance"] + penyangga)

    # Gerbang imbalan-risiko, hanya untuk pantulan — di situ targetnya
    # diketahui (level seberang). Untuk tembusan targetnya belum ada level
    # baru, dan rasionya dijamin oleh take_profit_rasio yang sudah 1:3.
    risiko_beli = out["close"] - (out["support"] - penyangga)
    rr_beli = ((out["resistance"] - out["close"])
               / risiko_beli.where(risiko_beli > 0))
    risiko_jual = (out["resistance"] + penyangga) - out["close"]
    rr_jual = ((out["close"] - out["support"])
               / risiko_jual.where(risiko_jual > 0))

    pantul_beli = (dekat_sup & kuat_sup & momentum_beli & tren_naik
                   & _bool(rr_beli >= p["rr_minimal"]))
    pantul_jual = (dekat_res & kuat_res & momentum_jual & tren_turun
                   & _bool(rr_jual >= p["rr_minimal"]))

    out["beli"] = pantul_beli | tembus_beli
    out["jual"] = (pantul_jual | tembus_jual) if dua_arah else False

    # Langkah 7 — keluar begitu thesisnya batal, jangan menunggu batas rugi.
    # Support yang jebol dengan volume tinggi berarti alasan masuk sudah
    # hilang; menunggu stop tersentuh hanya membayar kerugian lebih mahal.
    out["tutup_beli"] = (_bool(out["close"] < out["support"]) & vol_cukup) | tren_turun
    out["tutup_jual"] = (_bool(out["close"] > out["resistance"]) & vol_cukup) | tren_naik

    out["alasan_beli"] = (f"harga di level support yang sudah diuji "
                          f"≥{p['min_uji']}x, momentum mengonfirmasi, "
                          f"imbalan:risiko ≥ 1:{p['rr_minimal']:g}")
    out["alasan_jual"] = (f"harga di level resistance yang sudah diuji "
                          f"≥{p['min_uji']}x, momentum mengonfirmasi, "
                          f"imbalan:risiko ≥ 1:{p['rr_minimal']:g}")
    return out


# --------------------------------------------------------------------------
# Strategi 5 — scalping H1 -> M15
# --------------------------------------------------------------------------

def _peta_atas(df: pd.DataFrame, aturan: str, kiri: int, kanan: int,
               ema_cepat: int, ema_lambat: int) -> pd.DataFrame:
    """Baca 'peta' timeframe di atasnya, dari data timeframe kecil itu sendiri.

    Data M15 di-resample jadi H1, bukan diunduh terpisah. Dua alasan: indeksnya
    dijamin sejajar tanpa perlu dicocokkan, dan tidak ada peluang salah
    memasangkan dua berkas data yang berbeda panjangnya.

    `.shift(1)` di akhir adalah bagian yang paling mudah salah dan paling
    merusak. Tanpa itu, saat menilai lilin M15 pukul 10:15 kita akan memakai
    lilin H1 pukul 10:00 yang BELUM TUTUP — artinya memakai harga yang belum
    terjadi. Dengan pergeseran itu, lilin M15 sepanjang pukul 10 hanya melihat
    lilin H1 pukul 09 yang sudah benar-benar selesai.
    """
    atas = df.resample(aturan).agg({
        "open": "first", "high": "max", "low": "min", "close": "last",
    }).dropna()

    peta = pd.DataFrame(index=atas.index)
    peta["bias"] = ind.bias_struktur(atas, kiri, kanan)
    ec = ind.ema(atas["close"], ema_cepat)
    el = ind.ema(atas["close"], ema_lambat)
    peta["ema_naik"] = (ec > el).fillna(False)
    peta["ema_turun"] = (ec < el).fillna(False)
    lv = ind.level_struktur(atas, kiri, kanan, 0.5, 14, 5)
    peta["sup_atas"] = lv["support"]
    peta["res_atas"] = lv["resistance"]

    # Hanya lilin atas yang sudah tutup yang boleh terbaca.
    return peta.shift(1).reindex(df.index, method="ffill")


def _scalping_h1_m15(df: pd.DataFrame, p: dict, dua_arah: bool) -> pd.DataFrame:
    """Tujuh langkah teknik scalping H1 -> M15 dari dokumen rujukan.

    Bedanya dengan empat strategi lain: strategi ini membaca DUA timeframe.
    Timeframe besar menentukan arah ("peta"), timeframe kecil menentukan titik
    masuk ("eksekusi"). Entry yang berlawanan dengan peta tidak pernah diambil,
    berapa pun bagusnya tampilan lilin di timeframe kecil.

    Yang tidak bisa dikerjakan dari data harga, dan karena itu tidak ada di
    sini: penyaring berita berdampak tinggi (perlu kalender ekonomi) dan
    pembesaran lot saat konfluensi kuat (menambah risiko tanpa bisa diukur).
    """
    out = df.copy()
    peta = _peta_atas(out, p["aturan_atas"], p["pivot_kiri"], p["pivot_kanan"],
                      p["ema_cepat"], p["ema_lambat"])
    out[peta.columns] = peta

    lv = ind.level_struktur(out, p["pivot_kiri"], p["pivot_kanan"],
                            p["toleransi_atr"], p["atr_periode"],
                            p["maks_uji"], p.get("maks_level", 10))
    out[lv.columns] = lv
    out["atr"] = ind.atr(out, p["atr_periode"])
    pin = ind.pin_bar(out, p["pin_rasio_ekor"], p["pin_maks_badan"])
    out[pin.columns] = pin
    telan = ind.engulfing(out)
    out[telan.columns] = telan

    nilai_atr = out["atr"]

    # --- langkah 1: bias dari peta timeframe atas ------------------------
    # Struktur DAN rata-rata harus sepakat. Dokumen menyebut keduanya, dan
    # meminta keadaan yang tidak jelas (sideways) dilewati — bias 0 otomatis
    # menggugurkan kedua sisi.
    peta_naik = _bool(out["bias"] == 1) & _bool(out["ema_naik"])
    peta_turun = _bool(out["bias"] == -1) & _bool(out["ema_turun"])

    # --- langkah 2: konfluensi — dekat level kecil YANG JUGA level besar --
    dekat = p["dekat_atr"] * nilai_atr
    dekat_besar = p["dekat_atr_atas"] * nilai_atr
    konfluensi_beli = (_bool((out["close"] - out["support"]).abs() <= dekat)
                       & _bool((out["close"] - out["sup_atas"]).abs()
                               <= dekat_besar))
    konfluensi_jual = (_bool((out["close"] - out["resistance"]).abs() <= dekat)
                       & _bool((out["close"] - out["res_atas"]).abs()
                               <= dekat_besar))

    # --- langkah 3: konfirmasi lilin --------------------------------------
    konfirmasi_beli = _bool(out["pin_naik"]) | _bool(out["telan_naik"])
    konfirmasi_jual = _bool(out["pin_turun"]) | _bool(out["telan_turun"])

    # --- langkah 4: batas rugi DI LUAR ekor lilin konfirmasi --------------
    # Penyangganya kelipatan ATR, jadi otomatis lebih lebar untuk emas yang
    # memang bergerak lebih liar — persis yang diminta dokumen, tanpa perlu
    # angka khusus per simbol.
    penyangga = float(p["penyangga_atr"]) * nilai_atr
    out["stop_beli"] = out["low"] - penyangga
    out["stop_jual"] = out["high"] + penyangga

    # --- langkah 5: imbalan minimal sekian kali risiko --------------------
    # Targetnya diambil dari LEVEL KUNCI PETA, bukan level kecil. Dokumen
    # menyebut "level S/R kunci" di timeframe atas, dan itu memang yang masuk
    # akal: level M15 sering hanya beberapa ATR dari harga, sehingga rasio
    # imbalan-risikonya mustahil tercapai — apalagi karena batas rugi di sini
    # ditaruh di luar ekor, dan justru lilin konfirmasi yang bagus (pin bar)
    # yang ekornya paling panjang. Level M15 dipakai kalau level peta
    # kebetulan tidak berada di sisi yang benar.
    target_b = out["res_atas"].where(out["res_atas"] > out["close"],
                                     out["resistance"])
    target_j = out["sup_atas"].where(out["sup_atas"] < out["close"],
                                     out["support"])
    out["target_beli"] = target_b
    out["target_jual"] = target_j

    rr_minimal = float(p["rr_minimal"])
    risiko_b = out["close"] - out["stop_beli"]
    rr_b = (target_b - out["close"]) / risiko_b.where(risiko_b > 0)
    risiko_j = out["stop_jual"] - out["close"]
    rr_j = (out["close"] - target_j) / risiko_j.where(risiko_j > 0)

    # --- langkah 6: hanya sesi ramai --------------------------------------
    sesi = p.get("sesi_utc")
    if sesi:
        aktif = _bool(ind.dalam_sesi(out.index, int(sesi[0]), int(sesi[1])))
    else:
        aktif = pd.Series(True, index=out.index)

    out["beli"] = (peta_naik & konfluensi_beli & konfirmasi_beli & aktif
                   & _bool(rr_b >= rr_minimal))
    sinyal_jual = (peta_turun & konfluensi_jual & konfirmasi_jual & aktif
                   & _bool(rr_j >= rr_minimal))
    out["jual"] = sinyal_jual if dua_arah else False

    # --- keluar begitu peta berubah arah ----------------------------------
    # "Tidak menikahi opini": begitu struktur timeframe atas berbalik, alasan
    # masuk sudah hilang. Tidak menunggu batas rugi tersentuh.
    out["tutup_beli"] = ~peta_naik
    out["tutup_jual"] = ~peta_turun

    jam = (f", sesi {sesi[0]:02d}-{sesi[1]:02d} UTC" if sesi else "")
    out["alasan_beli"] = (f"peta {p['aturan_atas']} naik (struktur + EMA"
                          f"{p['ema_cepat']}/{p['ema_lambat']}), harga di "
                          f"konfluensi support, lilin konfirmasi naik, "
                          f"imbalan:risiko ≥ 1:{rr_minimal:g}{jam}")
    out["alasan_jual"] = (f"peta {p['aturan_atas']} turun (struktur + EMA"
                          f"{p['ema_cepat']}/{p['ema_lambat']}), harga di "
                          f"konfluensi resistance, lilin konfirmasi turun, "
                          f"imbalan:risiko ≥ 1:{rr_minimal:g}{jam}")
    return out


_PEMBUAT = {
    "ikut_tren": _ikut_tren,
    "balik_rata2": _balik_rata2,
    "tembus_batas": _tembus_batas,
    "struktur_harga": _struktur_harga,
    "scalping_h1_m15": _scalping_h1_m15,
}


def beri_sinyal(df: pd.DataFrame, p: dict, strategi: str,
                dua_arah: bool = False) -> pd.DataFrame:
    """Jalankan satu strategi pada satu DataFrame harga."""
    if strategi not in _PEMBUAT:
        raise ValueError(
            f"Strategi '{strategi}' tidak dikenal. Pilihannya: "
            f"{', '.join(_PEMBUAT)}"
        )
    out = _PEMBUAT[strategi](df, p, dua_arah)

    # Pastikan keempatnya benar-benar boolean walau strategi mengisinya
    # dengan False biasa (skalar Python), bukan Series.
    for kolom in ("beli", "jual", "tutup_beli", "tutup_jual"):
        if not isinstance(out[kolom], pd.Series):
            out[kolom] = False
        out[kolom] = _bool(pd.Series(out[kolom], index=out.index))

    return terapkan_konfirmasi(out, p.get("konfirmasi") or {})


def terapkan_konfirmasi(data: pd.DataFrame, konf: dict) -> pd.DataFrame:
    """Saring sinyal MASUK dengan konfirmasi tambahan dari dokumen rujukan.

    Dipakai agar tiga strategi lama pun bisa diberi konfirmasi volume, MACD,
    dan gerbang imbalan-risiko — lalu diukur satu per satu apakah masing-masing
    benar-benar mengurangi transaksi yang merugi.

    Dua hal yang dijaga ketat:

    1. **Hanya sinyal masuk yang disaring.** Sinyal keluar tidak pernah
       disentuh. Posisi yang sudah terbuka wajib punya jalan keluar, apa pun
       keadaan penyaringnya — sama seperti aturan batas transaksi harian.
    2. **Semua penyaring mati secara bawaan.** Dengan `konf` kosong fungsi ini
       mengembalikan data apa adanya, sehingga hasil backtest yang sudah ada
       tidak bergeser sedikit pun sampai penyaringnya sengaja dinyalakan.
    """
    if not konf:
        return data

    beli = _bool(data["beli"])
    jual = _bool(data["jual"])

    # ---- konfirmasi volume (langkah 4 dokumen) ----------------------------
    vol = konf.get("volume") or {}
    if vol.get("aktif"):
        if "vol_relatif" in data.columns:
            relatif = data["vol_relatif"]
        else:
            relatif = ind.volume_relatif(data, int(vol.get("periode", 20)))
        kosong = not relatif.notna().any()
        # Pasar tanpa data volume (forex Yahoo) melewati penyaring ini alih-alih
        # kehilangan seluruh sinyalnya. Setel lewati_jika_kosong: false kalau
        # Anda memang ingin pasar seperti itu berhenti memberi sinyal.
        if not (kosong and vol.get("lewati_jika_kosong", True)):
            cukup = _bool(relatif >= float(vol.get("pengali", 1.5)))
            beli &= cukup
            jual &= cukup

    # ---- konfirmasi MACD (langkah 3 dokumen) ------------------------------
    mc = konf.get("macd") or {}
    if mc.get("aktif"):
        if "macd_hist" in data.columns:
            hist = data["macd_hist"]
        else:
            hist = ind.macd(data["close"], int(mc.get("cepat", 12)),
                            int(mc.get("lambat", 26)),
                            int(mc.get("signal", 9)))["macd_hist"]
        beli &= _bool(hist > 0)
        jual &= _bool(hist < 0)

    # ---- gerbang imbalan:risiko (langkah 5 dokumen) -----------------------
    rr_minimal = float(konf.get("rr_minimal", 0) or 0)
    if rr_minimal > 0:
        if "support" in data.columns and "resistance" in data.columns:
            sup, res = data["support"], data["resistance"]
        else:
            lv = ind.level_struktur(
                data, int(konf.get("pivot_kiri", 3)),
                int(konf.get("pivot_kanan", 3)),
                float(konf.get("toleransi_atr", 0.5)),
                int(konf.get("atr_periode", 14)),
                int(konf.get("maks_uji", 5)),
            )
            sup, res = lv["support"], lv["resistance"]
        nilai_atr = (data["atr"] if "atr" in data.columns
                     else ind.atr(data, int(konf.get("atr_periode", 14))))
        penyangga = float(konf.get("penyangga_atr", 0.5)) * nilai_atr

        risiko_b = data["close"] - (sup - penyangga)
        rr_b = (res - data["close"]) / risiko_b.where(risiko_b > 0)
        beli &= _bool(rr_b >= rr_minimal)

        risiko_j = (res + penyangga) - data["close"]
        rr_j = (data["close"] - sup) / risiko_j.where(risiko_j > 0)
        jual &= _bool(rr_j >= rr_minimal)

    # ---- tunggu sebelum bereaksi (langkah 7 dokumen berita) ---------------
    # Satu-satunya bagian panduan verifikasi berita yang bisa diotomatiskan:
    # jangan bertindak pada lilin yang baru saja tutup, beri jeda dulu.
    tunda = int(konf.get("tunda_lilin", 0) or 0)
    if tunda > 0:
        beli = beli.shift(tunda, fill_value=False).astype(bool)
        jual = jual.shift(tunda, fill_value=False).astype(bool)

    hasil = data.copy()
    hasil["beli"] = beli
    hasil["jual"] = jual
    return hasil


def masa_pemanasan(p: dict, strategi: str) -> int:
    """Berapa lilin pertama yang indikatornya belum bisa dihitung.

    Dipakai untuk menolak simbol yang datanya terlalu pendek, supaya tidak
    menghasilkan "sinyal" dari kolom yang seluruhnya masih kosong.
    """
    kunci = {
        "ikut_tren": ("sma_cepat", "sma_lambat", "sma_tren",
                      "rsi_periode", "atr_periode"),
        "balik_rata2": ("bb_periode", "sma_tren", "rsi_periode", "atr_periode"),
        "tembus_batas": ("donchian_masuk", "donchian_keluar", "sma_tren",
                         "atr_periode"),
        "struktur_harga": ("sma_tren", "atr_periode", "rsi_periode",
                           "macd_lambat", "volume_periode"),
        # Yang menentukan di sini bukan panjang indikatornya, melainkan
        # berapa lilin kecil yang dibutuhkan supaya peta timeframe atas
        # punya cukup lilin untuk dinilai. Dihitung di bawah.
        "scalping_h1_m15": ("atr_periode",),
    }[strategi]
    dasar = max(int(p[k]) for k in kunci) + 1
    if strategi == "scalping_h1_m15":
        # EMA lambat di timeframe atas perlu sekian lilin ATAS, dan tiap
        # lilin atas terdiri dari beberapa lilin kecil. Tanpa perhitungan ini
        # strategi akan dijalankan pada data yang petanya masih kosong, lalu
        # dikira tidak pernah memberi sinyal.
        per_atas = {"1h": 4, "4h": 16, "1D": 96}.get(p["aturan_atas"], 4)
        dasar = max(dasar, (int(p["ema_lambat"]) + int(p["pivot_kiri"])
                            + 2 * int(p["pivot_kanan"]) + 2) * per_atas)
    if strategi == "struktur_harga":
        # Pivot perlu tetangga di kiri dan kanannya, lalu hasilnya masih
        # digeser `pivot_kanan` lilin lagi sebelum boleh dipakai.
        dasar += int(p["pivot_kiri"]) + 2 * int(p["pivot_kanan"])
    return dasar


def batas_rugi(harga_masuk: float, nilai_atr: float, pengali: float,
               arah: int = 1) -> float:
    """Batas rugi yang menyesuaikan keliaran harga.

    Aset yang bergerak liar diberi ruang lebih lebar supaya tidak terlempar
    keluar hanya karena gerakan wajar. Ini jauh lebih penting di crypto
    daripada di saham: gerakan harian 5% pada BTC itu biasa, pada saham besar
    tidak.

    arah  1 = posisi beli, batas rugi di BAWAH harga masuk
    arah -1 = posisi jual, batas rugi di ATAS harga masuk
    """
    return harga_masuk - arah * (pengali * nilai_atr)


def pilih_stop(harga_masuk: float, nilai_atr: float, pengali: float,
               arah: int, stop_struktur: float | None = None) -> float:
    """Batas rugi dari struktur harga kalau ada, kalau tidak dari ATR.

    Dokumen rujukan meminta batas rugi diletakkan di bawah support (atau di
    atas resistance), "bukan angka sembarangan". Strategi `struktur_harga`
    menyediakan angka itu lewat kolom `stop_beli`/`stop_jual`; tiga strategi
    lama tidak punya level struktur sehingga tetap memakai jarak ATR.

    Nilai struktural yang tidak masuk akal — di sisi yang salah dari harga
    masuk, nol, atau kosong — diabaikan dan diganti perhitungan ATR. Batas rugi
    di sisi yang salah bukan sekadar meleset: ia langsung tersentuh di lilin
    yang sama dan mengubah backtest jadi omong kosong.
    """
    if stop_struktur is not None and pd.notna(stop_struktur):
        calon = float(stop_struktur)
        if arah == 1 and 0 < calon < harga_masuk:
            return calon
        if arah == -1 and calon > harga_masuk:
            return calon
    return batas_rugi(harga_masuk, nilai_atr, pengali, arah)
