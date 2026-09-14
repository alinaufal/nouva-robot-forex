"""Pengambilan harga forex (dan emas) dari Yahoo Finance.

Catatan kode simbol yang sudah diperiksa langsung:
  * pasangan mata uang memakai akhiran `=X`, mis. `EURUSD=X`, `USDIDR=X`
  * `XAUUSD=X` TIDAK ADA di Yahoo — emas memakai `GC=F` (kontrak berjangka)
  * data harian tersedia sangat panjang (EURUSD sejak 2003, USDIDR sejak 2001)
  * data 4 jam hanya 730 hari terakhir — itu batas dari Yahoo, bukan dari kode ini
  * data 1 jam juga 730 hari terakhir
  * di bawah 1 jam TERNYATA ADA, hanya 60 hari. Catatan lama di berkas ini
    menyebut Yahoo tidak menyediakannya sama sekali; itu keliru, sudah diuji
    ulang langsung: GC=F 4.578 lilin 15 menit, GBPUSD=X 5.688 lilin.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import yfinance as yf

from .galat import GagalUnduh

INTERVAL ={"harian": "1d", "4jam": "4h", "1jam": "1h",
            "30menit": "30m", "15menit": "15m", "5menit": "5m"}
DURASI = {"harian": timedelta(days=1), "4jam": timedelta(hours=4),
          "1jam": timedelta(hours=1), "30menit": timedelta(minutes=30),
          "15menit": timedelta(minutes=15), "5menit": timedelta(minutes=5)}
# Batas panjang riwayat dari Yahoo, diukur langsung bukan disalin dokumentasi.
#
# PERINGATAN untuk timeframe di bawah 1 jam: 60 hari itu PENDEK SEKALI untuk
# menilai sebuah strategi. Pemisah periode backtest ada di 2023-01-01,
# sehingga uji dua periode — aturan penerimaan yang dipakai di seluruh project
# ini — MUSTAHIL diterapkan di sana. Apa pun hasil backtest 15 menit,
# perlakukan sebagai pemeriksaan kasar, bukan bukti.
PERIODE_MAKS = {"harian": "max", "4jam": "730d", "1jam": "730d",
                "30menit": "60d", "15menit": "60d", "5menit": "60d"}

# Seberapa panjang yang diminta saat datanya SUDAH ada dan tinggal disusul.
# Tanpa ini, jadwal yang jalan tiap jam akan menarik 730 hari penuh untuk
# setiap simbol setiap kali jalan — lambat, dan tidak sopan pada Yahoo.
# Dibuat jauh lebih panjang daripada satu lilin supaya akhir pekan, hari
# libur, dan jadwal yang sempat tidak jalan tetap tersusul sendiri.
PERIODE_SUSULAN = {"4jam": "60d", "1jam": "30d", "30menit": "15d",
                   "15menit": "10d", "5menit": "5d"}

KOLOM = ["Open", "High", "Low", "Close", "Volume"]


def _rapikan(df: pd.DataFrame) -> pd.DataFrame:
    """Samakan bentuk DataFrame dari yfinance agar mudah diolah.

    yfinance kadang mengembalikan kolom bertingkat (MultiIndex) walau hanya
    satu simbol, dan indeksnya kadang membawa zona waktu — dua hal itu
    diseragamkan di sini. Waktunya dijadikan UTC lalu zona waktunya dilepas,
    supaya sebanding dengan data crypto yang memang UTC.
    """
    if df is None or df.empty:
        return pd.DataFrame()

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    idx = pd.to_datetime(df.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_convert("UTC").tz_localize(None)
    df.index = idx

    tersedia = [k for k in KOLOM if k in df.columns]
    return df[tersedia].dropna(how="all")


def unduh(simbol: str, timeframe: str,
          mulai: datetime | None = None) -> list[tuple]:
    """Ambil lilin dari Yahoo Finance.

    Mengembalikan daftar (ts, open, high, low, close, volume).

    Sama seperti sumber crypto, lilin yang belum tertutup dibuang. Untuk data
    harian ini berarti hari berjalan tidak ikut — jadi sinyal yang Anda terima
    selalu berdasarkan hari yang sudah benar-benar selesai.
    """
    if timeframe not in INTERVAL:
        raise ValueError(f"Timeframe '{timeframe}' tidak dikenal")

    opsi = dict(interval=INTERVAL[timeframe], progress=False,
                auto_adjust=True, actions=False)
    if mulai is not None and timeframe == "harian":
        opsi["start"] = mulai.strftime("%Y-%m-%d")
    elif mulai is not None and timeframe in PERIODE_SUSULAN:
        # Sudah punya data, tinggal menyusul yang baru. Yahoo menolak `start`
        # untuk permintaan intraday, jadi yang bisa diatur hanya panjang
        # periodenya — dan yang pendek sudah cukup untuk menyusul.
        opsi["period"] = PERIODE_SUSULAN[timeframe]
    else:
        opsi["period"] = PERIODE_MAKS[timeframe]

    try:
        df = _rapikan(yf.download(simbol, **opsi))
    except Exception as e:
        # Yahoo jarang melempar galat — lebih sering diam-diam mengembalikan
        # tabel kosong. Kasus diam itu ditangkap pasar.data_tertinggal().
        raise GagalUnduh(
            f"Yahoo Finance tidak bisa dihubungi ({type(e).__name__})",
            [], rinci=str(e)) from e

    if df.empty:
        return []

    sekarang = datetime.now(timezone.utc).replace(tzinfo=None)
    durasi = DURASI[timeframe]

    keluar: list[tuple] = []
    for ts, r in df.iterrows():
        if ts + durasi > sekarang:
            continue                      # lilin ini masih berjalan
        if pd.isna(r.get("Close")):
            continue
        volume = r.get("Volume")
        keluar.append((
            ts.strftime("%Y-%m-%d %H:%M:%S"),
            float(r["Open"]), float(r["High"]), float(r["Low"]),
            float(r["Close"]),
            float(volume) if pd.notna(volume) else 0.0,
        ))
    return keluar
