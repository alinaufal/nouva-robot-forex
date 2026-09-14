"""Pembacaan indikator SAAT INI dari TradingView.

Kenapa modul ini ada
--------------------
Harga emas di Yahoo (`GC=F`, kontrak berjangka) berbeda sekitar 40 poin dari
XAUUSD spot yang tampil di chart TradingView dan di broker. Modul ini membaca
angka yang benar-benar sama dengan yang Anda lihat, lalu dipakai sebagai
konfirmasi terakhir sebelum sinyal dikirim.

Batas yang harus dipahami sebelum memakainya
--------------------------------------------
TradingView **tidak menyediakan riwayat harga** ke publik. Sudah diuji
langsung: tiga alamat riwayat menjawab 404/403. Yang tersedia hanya nilai
SAAT INI lewat `scanner.tradingview.com`.

Akibatnya besar dan tidak bisa dihindari: **penyaring dari modul ini TIDAK
BISA di-backtest.** Seluruh project ini berjalan dengan aturan "backtest yang
memutuskan", dan modul ini adalah satu-satunya bagian yang berada di luar
aturan itu. Karena itu ia mati secara bawaan, dan menyalakannya berarti
menerima sesuatu yang belum pernah diukur.

Satu-satunya cara mendapatkan riwayat dari TradingView adalah membongkar
WebSocket internal mereka. Itu melanggar ketentuan layanan TradingView dan
rusak tiap kali mereka mengubah sistemnya — sengaja tidak dikerjakan di sini.
"""
from __future__ import annotations

import requests

API = "https://scanner.tradingview.com/{grup}/scan"

# Kolom yang diminta. Akhiran "|15" dan "|60" berarti timeframe 15 menit dan
# 60 menit — persis dua timeframe yang dipakai teknik peta-eksekusi.
KOLOM = [
    "close",
    "Recommend.All|15",   # ringkasan ~26 indikator TradingView di M15
    "Recommend.All|60",   # sama, di H1 — ini "peta"-nya
    "RSI|15",
    "EMA50|60",
    "EMA200|60",
]

# Grup scanner berbeda per jenis instrumen. Sudah diuji: emas TIDAK ADA di
# grup "forex" (jawabannya kosong), harus lewat "cfd".
GRUP_BAWAAN = "forex"


def _grup(ticker: str) -> str:
    atas = ticker.upper()
    if "XAU" in atas or "GOLD" in atas or "XAG" in atas:
        return "cfd"
    return GRUP_BAWAAN


def baca(tickers: list[str], timeout: int = 20) -> dict[str, dict]:
    """Baca indikator saat ini untuk beberapa ticker TradingView.

    Ticker memakai bentuk "BURSA:SIMBOL", mis. "OANDA:XAUUSD", "FX:GBPUSD".

    Mengembalikan dictionary ticker -> nilai. Ticker yang gagal dibaca
    TIDAK muncul di hasil — dan itu disengaja: pemanggilnya harus
    memperlakukan ketiadaan data sebagai "tidak tahu", bukan sebagai "tidak
    setuju". Penyaring yang rusak tidak boleh membungkam robot.

    Tidak pernah melempar exception. Jaringan putus, TradingView berubah,
    atau ticker salah ketik semuanya berakhir sebagai hasil kosong.
    """
    hasil: dict[str, dict] = {}
    per_grup: dict[str, list[str]] = {}
    for t in tickers:
        per_grup.setdefault(_grup(t), []).append(t)

    for grup, daftar in per_grup.items():
        try:
            r = requests.post(
                API.format(grup=grup),
                json={"symbols": {"tickers": daftar, "query": {"types": []}},
                      "columns": KOLOM},
                timeout=timeout,
            )
            if r.status_code != 200:
                continue
            for baris in r.json().get("data", []):
                nilai = dict(zip(KOLOM, baris.get("d", [])))
                nilai["ticker"] = baris.get("s", "")
                hasil[baris.get("s", "")] = nilai
        except Exception:
            # Sengaja ditelan: modul ini penyaring tambahan, bukan sumber
            # utama. Kegagalannya tidak boleh menggagalkan pencarian sinyal.
            continue
    return hasil


def arah(nilai: dict, ambang: float = 0.1) -> int:
    """Terjemahkan pembacaan jadi arah: +1 beli, -1 jual, 0 tidak jelas.

    Dua hal harus sepakat, meniru cara teknik peta-eksekusi bekerja:
    ringkasan H1 (peta) dan ringkasan M15 (eksekusi). Kalau keduanya berbeda
    arah, jawabannya 0 — sama seperti bias struktur yang menolak menebak saat
    pasar sedang tidak jelas.

    `Recommend.All` bernilai -1 sampai +1. Ambang kecil dipakai supaya angka
    yang nyaris nol tidak terbaca sebagai pendapat.
    """
    h1 = nilai.get("Recommend.All|60")
    m15 = nilai.get("Recommend.All|15")
    if h1 is None or m15 is None:
        return 0
    if h1 >= ambang and m15 >= ambang:
        return 1
    if h1 <= -ambang and m15 <= -ambang:
        return -1
    return 0


def setuju(nilai: dict, arah_sinyal: int, ambang: float = 0.1) -> bool:
    """Apakah TradingView sejalan dengan arah sinyal kita?

    Nilai kosong dianggap SETUJU. Ini keputusan sadar: kalau TradingView
    sedang tidak bisa dibaca, lebih baik sinyal tetap dikirim daripada
    hilang diam-diam karena jaringan bermasalah.
    """
    if not nilai:
        return True
    return arah(nilai, ambang) == arah_sinyal
