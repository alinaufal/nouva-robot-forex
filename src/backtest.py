"""Uji strategi pada data historis.

Empat hal yang membuat backtest ini tidak menipu diri sendiri:

1. **Tidak melihat masa depan.** Sinyal dihitung dari harga penutupan lilin
   ini, tapi transaksinya baru dilakukan pada harga PEMBUKAAN lilin berikutnya
   — persis seperti kalau Anda membaca notifikasi lalu memasang order setelahnya.
2. **Biaya dihitung, dan modelnya beda per pasar.** Crypto ditagih komisi
   persen; forex ditagih lewat spread. Memakai satu model untuk keduanya akan
   memberi angka yang salah di salah satunya.
3. **Ukuran posisi berbasis risiko.** Tiap transaksi mempertaruhkan jumlah yang
   sama (mis. 1% modal), berapa pun lebar batas ruginya. Tanpa ini, strategi
   dengan stop sempit akan terlihat unggul hanya karena diam-diam memasang
   taruhan yang jauh lebih besar.
4. **Ada pembanding.** Hasilnya selalu disandingkan dengan sekadar membeli dan
   menahan aset itu sendiri.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

from . import strategy


@dataclass
class Biaya:
    """Model biaya. Crypto memakai komisi persen, forex memakai spread.

    Spread dibagi dua: separuh saat masuk, separuh saat keluar. Totalnya tetap
    satu spread penuh per transaksi bolak-balik, tapi pembagiannya membuat
    posisi beli dan posisi jual diperlakukan setara.
    """
    jenis: str                  # "persen" atau "spread"
    fee_persen: float = 0.0     # dipakai kalau jenis == "persen"
    spread: float = 0.0         # dalam satuan harga, dipakai kalau "spread"

    def saat_membeli(self, harga: float) -> float:
        """Harga yang benar-benar Anda bayar saat membeli (lebih mahal)."""
        if self.jenis == "persen":
            return harga * (1 + self.fee_persen / 100)
        return harga + self.spread / 2

    def saat_menjual(self, harga: float) -> float:
        """Harga yang benar-benar Anda terima saat menjual (lebih murah)."""
        if self.jenis == "persen":
            return harga * (1 - self.fee_persen / 100)
        return harga - self.spread / 2


@dataclass
class Transaksi:
    waktu_masuk: str
    harga_masuk: float
    waktu_keluar: str
    harga_keluar: float
    unit: float
    arah: int                # 1 = beli, -1 = jual
    alasan_keluar: str
    laba: float
    laba_persen: float


@dataclass
class Hasil:
    simbol: str
    strategi: str
    modal_awal: float
    modal_akhir: float
    transaksi: list[Transaksi] = field(default_factory=list)
    kurva_ekuitas: pd.Series | None = None
    posisi_masih_terbuka: bool = False
    # Pembukaan posisi yang batal karena ukurannya di bawah batas order bursa.
    # Hanya terisi kalau jalankan() diberi batas_order.
    ditolak_bursa: int = 0

    @property
    def total_return(self) -> float:
        return (self.modal_akhir - self.modal_awal) / self.modal_awal * 100

    @property
    def jumlah_transaksi(self) -> int:
        return len(self.transaksi)

    @property
    def menang(self) -> list[Transaksi]:
        return [t for t in self.transaksi if t.laba > 0]

    @property
    def kalah(self) -> list[Transaksi]:
        return [t for t in self.transaksi if t.laba <= 0]

    @property
    def win_rate(self) -> float:
        if not self.transaksi:
            return 0.0
        return len(self.menang) / len(self.transaksi) * 100

    @property
    def profit_factor(self) -> float:
        """Total untung dibagi total rugi. Di bawah 1 berarti merugi."""
        untung = sum(t.laba for t in self.menang)
        rugi = abs(sum(t.laba for t in self.kalah))
        if rugi == 0:
            return float("inf") if untung > 0 else 0.0
        return untung / rugi

    @property
    def max_drawdown(self) -> float:
        """Penurunan terdalam dari puncak modal, dalam persen."""
        if self.kurva_ekuitas is None or self.kurva_ekuitas.empty:
            return 0.0
        puncak = self.kurva_ekuitas.cummax()
        turun = (self.kurva_ekuitas - puncak) / puncak * 100
        return float(turun.min())

    @property
    def tahun(self) -> float:
        """Panjang periode uji dalam tahun, dibaca dari indeks waktunya."""
        if self.kurva_ekuitas is None or len(self.kurva_ekuitas) < 2:
            return 0.0
        rentang = self.kurva_ekuitas.index[-1] - self.kurva_ekuitas.index[0]
        return rentang.days / 365.25

    @property
    def cagr(self) -> float:
        """Pertumbuhan rata-rata per tahun (majemuk)."""
        t = self.tahun
        if t <= 0 or self.modal_awal <= 0 or self.modal_akhir <= 0:
            return 0.0
        return ((self.modal_akhir / self.modal_awal) ** (1 / t) - 1) * 100


def _ukuran_margin(kas: float, harga_masuk: float, persen: float,
                   leverage: float) -> float:
    """Ukuran posisi dari MARGIN, bukan dari risiko.

    Ini cara yang paling umum dipakai orang di futures: "sekali buka pakai
    2% modal, leverage 20x". Artinya jaminan yang disetor 2% modal, dan nilai
    posisinya 2% x 20 = 40% modal.

    Bedanya dengan cara berbasis risiko sangat besar, dan inilah sumber
    kebanyakan akun habis: di sini **leverage langsung memperbesar posisi**,
    sehingga kerugian per transaksi ikut berlipat. Pada cara berbasis risiko,
    leverage tidak menambah ukuran posisi sama sekali.
    """
    if harga_masuk <= 0 or kas <= 0:
        return 0.0
    margin = kas * persen / 100
    return margin * max(1.0, leverage) / harga_masuk


def _ukuran_posisi(kas: float, harga_masuk: float, stop: float,
                   risiko_uang: float, leverage: float = 1.0) -> float:
    """Berapa unit yang dibeli agar kerugian saat kena stop ≈ risiko_uang.

    Dibatasi oleh modal yang ada dikalikan leverage. Dengan leverage 1 —
    nilai bawaan, dipakai spot dan forex — batasnya persis uang yang benar-
    benar dimiliki, jadi tidak ada pinjaman sama sekali.
    """
    jarak = abs(harga_masuk - stop)
    if jarak <= 0 or harga_masuk <= 0 or kas <= 0:
        return 0.0
    unit = risiko_uang / jarak
    maks = kas * max(1.0, leverage) / harga_masuk
    return max(0.0, min(unit, maks))


def bulatkan_order(unit: float, harga: float, batas: dict | None) -> float:
    """Sesuaikan jumlah unit dengan aturan order bursa, atau 0 kalau ditolak.

    Binance membulatkan jumlah ke kelipatan `step_qty` dan menolak order yang
    jumlahnya di bawah `min_qty` atau nilainya di bawah `min_notional`. Dulu
    backtest mengabaikan semuanya: posisi $3 tetap dihitung terbuka. Diukur
    13 September 2026, di modal $50 seluruh sinyal BTC dan ETH harian ditolak
    bursa — padahal backtest mencatatnya sebagai transaksi.

    Pembulatannya ke BAWAH, persis seperti yang terjadi saat Anda mengisi
    jumlah di aplikasi: tidak pernah membeli lebih dari yang dihitung.
    """
    if not batas or unit <= 0:
        return unit
    langkah = float(batas.get("step_qty", 0) or 0)
    if langkah > 0:
        # 1e-9 menahan galat pembulatan float: 0.3 / 0.1 = 2.9999999999999996
        unit = round(math.floor(unit / langkah + 1e-9) * langkah, 12)
    if unit <= 0 or unit < float(batas.get("min_qty", 0) or 0) - 1e-12:
        return 0.0
    if unit * harga < float(batas.get("min_notional", 0) or 0) - 1e-9:
        return 0.0
    return unit


def harga_likuidasi(harga_masuk: float, leverage: float, arah: int,
                    maintenance: float = 0.005) -> float:
    """Harga saat bursa menutup paksa posisi karena margin habis.

    Diturunkan dari syarat likuidasi bursa: posisi ditutup ketika
    margin + laba berjalan tidak lagi menutupi maintenance margin.

        beli : harga_masuk × (1 − 1/leverage) ÷ (1 − maintenance)
        jual : harga_masuk × (1 + 1/leverage) ÷ (1 + maintenance)

    Contoh: leverage 10 pada harga masuk 100 memberi likuidasi di 90,45 —
    hanya 9,55% bergerak melawan Anda.

    Dengan leverage 1, hasilnya 0 untuk posisi beli (tidak pernah likuidasi)
    dan sekitar 2× harga masuk untuk posisi jual — dua-duanya benar, karena
    tanpa pinjaman kerugian memang baru habis saat harga jatuh ke nol atau
    berlipat dua.

    Inilah angka yang paling sering diabaikan orang: kalau batas rugi ATR
    Anda lebih lebar daripada jarak ke likuidasi, batas rugi itu tidak pernah
    terpakai — Anda dilikuidasi lebih dulu.
    """
    if leverage <= 0:
        return 0.0
    if arah == 1:
        return harga_masuk * (1 - 1 / leverage) / (1 - maintenance)
    return harga_masuk * (1 + 1 / leverage) / (1 + maintenance)


def funding_per_lilin(indeks, funding) -> "list[float]":
    """Jumlahkan funding rate yang jatuh di dalam tiap lilin harga.

    Funding ditagih tiap 8 jam, sedangkan lilin bisa 4 jam atau 1 hari —
    waktunya tidak sejajar. Tiap kejadian funding dimasukkan ke lilin yang
    memuatnya, sehingga lilin harian biasanya berisi 3 kejadian dan lilin
    4 jam kadang 1 kadang 0.
    """
    import numpy as np
    hasil = [0.0] * len(indeks)
    if funding is None or len(funding) == 0 or len(indeks) == 0:
        return hasil
    pos = indeks.searchsorted(funding.index, side="right") - 1
    nilai = np.asarray(funding.values, dtype=float)
    for p, r in zip(pos, nilai):
        if 0 <= p < len(hasil):
            hasil[p] += float(r)
    return hasil


def jalankan(df: pd.DataFrame, p: dict, nama_strategi: str, biaya: Biaya,
             modal: float, risiko_persen: float = 1.0,
             dua_arah: bool = False, simbol: str = "",
             leverage: float = 1.0, funding=None,
             maintenance: float = 0.005,
             mode_ukuran: str = "risiko",
             tp_rasio: float = 0.0,
             maks_per_hari: int = 0,
             batas_order: dict | None = None) -> Hasil:
    """Jalankan satu strategi pada satu simbol.

    Parameter futures (leverage, funding, maks_per_hari, batas_order) punya
    nilai bawaan yang membuat perilaku spot dan forex tidak berubah sedikit
    pun: leverage 1 berarti tanpa pinjaman, tanpa funding tidak ada biaya
    menginap, dan tanpa batas_order tidak ada order yang ditolak.
    """
    data = strategy.beri_sinyal(df, p, nama_strategi, dua_arah)
    pengali = p["atr_pengali_stop"]
    pakai_futures = leverage > 1.0 or funding is not None

    kas = modal
    posisi = 0          # 1 = beli, -1 = jual, 0 = kosong
    unit = 0.0
    harga_masuk = 0.0
    waktu_masuk = ""
    stop = 0.0
    target = 0.0            # take profit; 0 berarti tidak dipakai
    likuidasi = 0.0
    margin = 0.0
    biaya_funding = 0.0     # terkumpul selama posisi dipegang

    transaksi: list[Transaksi] = []
    ekuitas: list[float] = []
    indeks: list = []

    baris = list(data.itertuples())
    dana = funding_per_lilin(data.index, funding)

    # Batas jumlah pembukaan posisi per tanggal WIB. Yang dibatasi HANYA
    # pembukaan — penutupan tidak pernah dihalangi, karena posisi yang sudah
    # terbuka wajib punya jalan keluar berapa pun sisa jatahnya hari itu.
    #
    # Tanggalnya WIB, bukan tanggal UTC lilin, supaya sama dengan robot live
    # yang menghitung jatah per hari WIB. Dulu backtest berganti hari pukul
    # 00:00 UTC (07:00 WIB) sementara robot live berganti hari 00:00 WIB —
    # keduanya menegakkan "10 per hari" dengan arti yang berbeda.
    tanggal_kini = None
    entry_hari_ini = 0
    tanggal_wib = (data.index + pd.Timedelta(hours=7)).date
    ditolak_bursa = 0

    def tutup(kini_bar, harga_keluar, alasan, kena_likuidasi):
        """Tutup posisi dan catat transaksinya."""
        nonlocal kas, posisi, unit, harga_masuk, margin, likuidasi
        nonlocal biaya_funding, target
        laba = (harga_keluar - harga_masuk) * unit * posisi

        # Pada likuidasi, kerugian dibatasi margin yang disetor. Bursa menutup
        # posisi dan dana asuransinya menanggung sisa kalau harga melompat.
        if kena_likuidasi and margin > 0:
            laba = max(laba, -margin)

        # Funding sudah dipotong dari kas tiap lilin, jadi di sini hanya
        # dicatat ke laporan transaksi — bukan dipotong lagi. Untuk futures,
        # laba diukur terhadap MARGIN yang disetor, bukan terhadap nilai
        # posisi: itulah imbal hasil yang benar-benar Anda rasakan.
        modal_terpakai = margin if margin > 0 else unit * harga_masuk
        kas += laba

        transaksi.append(Transaksi(
            waktu_masuk=waktu_masuk,
            harga_masuk=harga_masuk,
            waktu_keluar=str(kini_bar.Index),
            harga_keluar=harga_keluar,
            unit=unit,
            arah=posisi,
            alasan_keluar=alasan,
            laba=laba - biaya_funding,
            laba_persen=((laba - biaya_funding) / modal_terpakai * 100)
                        if modal_terpakai else 0.0,
        ))
        posisi = 0
        unit = 0.0
        harga_masuk = 0.0
        margin = 0.0
        likuidasi = 0.0
        target = 0.0
        biaya_funding = 0.0

    # Sinyal dibaca dari lilin SEBELUMNYA lalu dieksekusi di pembukaan lilin
    # ini. Urutan di dalam satu lilin sengaja dibuat: eksekusi sinyal dulu di
    # harga pembukaan, baru rentang lilin itu diperiksa terhadap batas rugi
    # dan likuidasi.
    #
    # Urutan ini yang membetulkan bug nyata: dulu lilin tempat posisi DIBUKA
    # tidak pernah diperiksa, sehingga posisi yang seharusnya langsung
    # terlikuidasi di hari pertama tercatat bertahan berbulan-bulan. Di
    # leverage rendah pengaruhnya kecil, di leverage tinggi menentukan.
    for i in range(1, len(baris)):
        kini = baris[i]
        lalu = baris[i - 1]

        # Jatah pembukaan posisi kembali penuh setiap ganti tanggal WIB.
        tanggal_baris = tanggal_wib[i]
        if tanggal_baris != tanggal_kini:
            tanggal_kini = tanggal_baris
            entry_hari_ini = 0

        # ---------------- 1. eksekusi sinyal di harga pembukaan ------------
        if posisi != 0:
            sinyal_keluar = bool(lalu.tutup_beli if posisi == 1 else lalu.tutup_jual)
            if sinyal_keluar:
                harga_keluar = (biaya.saat_menjual(kini.open) if posisi == 1
                                else biaya.saat_membeli(kini.open))
                tutup(kini, harga_keluar, "sinyal keluar", False)

        jatah_habis = 0 < maks_per_hari <= entry_hari_ini
        if (posisi == 0 and not jatah_habis
                and not pd.isna(lalu.atr) and lalu.atr > 0):
            arah = 0
            if lalu.beli:
                arah = 1
            elif dua_arah and lalu.jual:
                arah = -1

            if arah != 0:
                harga_masuk_calon = (biaya.saat_membeli(kini.open) if arah == 1
                                     else biaya.saat_menjual(kini.open))
                # Strategi berbasis struktur menitipkan batas ruginya lewat
                # kolom stop_beli/stop_jual. Strategi lain tidak punya kolom
                # itu, dan getattr mengembalikan NaN sehingga perhitungan ATR
                # yang lama tetap dipakai persis seperti sebelumnya.
                stop_struktur = getattr(
                    lalu, "stop_beli" if arah == 1 else "stop_jual",
                    float("nan"),
                )
                stop_calon = strategy.pilih_stop(
                    harga_masuk_calon, lalu.atr, pengali, arah, stop_struktur
                )
                if mode_ukuran == "margin":
                    unit_calon = _ukuran_margin(
                        kas, harga_masuk_calon, risiko_persen, leverage
                    )
                else:
                    risiko_uang = kas * risiko_persen / 100
                    unit_calon = _ukuran_posisi(
                        kas, harga_masuk_calon, stop_calon, risiko_uang, leverage
                    )
                # Bursa membulatkan jumlah dan menolak order yang terlalu
                # kecil. Tanpa ini, modal kecil tampak bisa membuka posisi
                # yang di Binance langsung ditolak.
                if batas_order and unit_calon > 0:
                    unit_calon = bulatkan_order(unit_calon, harga_masuk_calon,
                                                batas_order)
                    if unit_calon <= 0:
                        ditolak_bursa += 1
                if unit_calon > 0:
                    posisi = arah
                    unit = unit_calon
                    harga_masuk = harga_masuk_calon
                    stop = stop_calon
                    waktu_masuk = str(kini.Index)
                    biaya_funding = 0.0
                    entry_hari_ini += 1
                    # Take profit diukur sebagai kelipatan jarak batas rugi.
                    # tp_rasio 2 berarti target untung dua kali lebih jauh
                    # daripada jarak ke batas rugi — rasio 1:2 yang lazim.
                    # Nilai 0 berarti tanpa take profit sama sekali.
                    if tp_rasio > 0:
                        jarak = abs(harga_masuk - stop)
                        target = harga_masuk + arah * tp_rasio * jarak
                    else:
                        target = 0.0
                    if pakai_futures:
                        margin = unit * harga_masuk / max(1.0, leverage)
                        likuidasi = harga_likuidasi(harga_masuk, leverage,
                                                    arah, maintenance)
                    else:
                        margin = 0.0
                        likuidasi = 0.0 if arah == 1 else float("inf")

        # ---------------- 2. biaya funding untuk lilin ini -----------------
        if posisi != 0 and dana[i]:
            tagihan = posisi * unit * kini.close * dana[i]
            biaya_funding += tagihan
            kas -= tagihan

        # ---------------- 3. periksa batas rugi dan likuidasi --------------
        # Termasuk lilin tempat posisi baru saja dibuka.
        if posisi != 0:
            if posisi == 1:
                # Harga jatuh: yang lebih TINGGI di antara stop dan likuidasi
                # tersentuh lebih dulu.
                ambang = max(stop, likuidasi)
                tersentuh = kini.low <= ambang
                kena_likuidasi = tersentuh and likuidasi > stop
                if tersentuh:
                    harga_keluar = biaya.saat_menjual(min(kini.open, ambang)
                                                      if kini.open < ambang
                                                      else ambang)
            else:
                # Harga naik: yang lebih RENDAH tersentuh lebih dulu.
                ambang = min(stop, likuidasi) if likuidasi > 0 else stop
                tersentuh = kini.high >= ambang
                kena_likuidasi = (tersentuh and likuidasi > 0
                                  and likuidasi < stop)
                if tersentuh:
                    harga_keluar = biaya.saat_membeli(max(kini.open, ambang)
                                                      if kini.open > ambang
                                                      else ambang)
            if tersentuh:
                tutup(kini, harga_keluar,
                      "LIKUIDASI" if kena_likuidasi else "kena batas rugi",
                      kena_likuidasi)

        # ---------------- 4. periksa take profit ---------------------------
        # Sengaja diperiksa SESUDAH batas rugi. Kalau satu lilin menyentuh
        # keduanya, kita tidak tahu mana yang lebih dulu terjadi di dalam
        # lilin itu — dan menganggap batas rugi yang duluan adalah asumsi
        # yang merugikan diri sendiri, yaitu asumsi yang benar untuk dipakai.
        # Menganggap take profit duluan akan membuat hasil backtest tampak
        # jauh lebih bagus daripada kenyataan.
        if posisi != 0 and target > 0:
            if posisi == 1:
                kena_tp = kini.high >= target
                harga_tp = biaya.saat_menjual(max(kini.open, target)
                                              if kini.open > target else target)
            else:
                kena_tp = kini.low <= target
                harga_tp = biaya.saat_membeli(min(kini.open, target)
                                              if kini.open < target else target)
            if kena_tp:
                tutup(kini, harga_tp, "kena take profit", False)

        # Nilai akun ditandai ke harga pasar saat ini
        nilai = kas + (kini.close - harga_masuk) * unit * posisi if posisi else kas
        ekuitas.append(nilai)
        indeks.append(kini.Index)

    # Posisi yang masih terbuka di akhir periode ditutup pada harga terakhir
    masih_terbuka = posisi != 0
    if masih_terbuka:
        akhir = baris[-1].close
        harga_keluar = (biaya.saat_menjual(akhir) if posisi == 1
                        else biaya.saat_membeli(akhir))
        kas += (harga_keluar - harga_masuk) * unit * posisi

    kurva = (pd.Series(ekuitas, index=pd.DatetimeIndex(indeks))
             if ekuitas else pd.Series(dtype=float))

    return Hasil(
        simbol=simbol,
        strategi=nama_strategi,
        modal_awal=modal,
        modal_akhir=kas,
        transaksi=transaksi,
        kurva_ekuitas=kurva,
        posisi_masih_terbuka=masih_terbuka,
        ditolak_bursa=ditolak_bursa,
    )


def beli_dan_tahan(df: pd.DataFrame, biaya: Biaya, modal: float) -> float:
    """Pembanding: beli di lilin pertama, tahan sampai lilin terakhir.

    Biaya masuk dan keluar tetap dihitung supaya perbandingannya adil.

    Catatan kejujuran: untuk forex, pembanding ini kurang bermakna. Mata uang
    tidak "tumbuh" seperti aset produktif — patokan sesungguhnya di forex
    adalah mengalahkan 0% setelah biaya.
    """
    if df.empty or len(df) < 2:
        return modal
    harga_awal = biaya.saat_membeli(float(df["close"].iloc[0]))
    harga_akhir = biaya.saat_menjual(float(df["close"].iloc[-1]))
    if harga_awal <= 0:
        return modal
    unit = modal / harga_awal
    return unit * harga_akhir
