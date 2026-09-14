"""Pengambilan harga crypto dari Binance.

PENTING — alamatnya sengaja `data-api.binance.vision`, bukan `api.binance.com`.
Alamat kedua itu yang biasa dipakai orang, tapi dari jaringan ini sambungannya
ditolak (sertifikat SSL gagal diverifikasi). `data-api.binance.vision` adalah
cermin resmi Binance khusus data pasar: tanpa API key, tanpa akun, dan sudah
diuji berhasil dari komputer ini.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import requests

from .galat import GagalUnduh

URL = "https://data-api.binance.vision/api/v3/klines"

INTERVAL = {"harian": "1d", "4jam": "4h"}
DURASI = {"harian": timedelta(days=1), "4jam": timedelta(hours=4)}

# Binance memberi paling banyak 1000 lilin sekali minta.
BATAS = 1000
# Pengaman supaya tidak berputar tanpa henti kalau API berperilaku aneh.
MAKS_HALAMAN = 60

AWAL_MULA = datetime(2017, 1, 1, tzinfo=timezone.utc)


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def unduh(simbol: str, timeframe: str,
          mulai: datetime | None = None) -> list[tuple]:
    """Ambil lilin dari Binance, halaman demi halaman.

    Mengembalikan daftar (ts, open, high, low, close, volume) dengan `ts`
    berupa teks 'YYYY-MM-DD HH:MM:SS' dalam waktu UTC.

    Lilin yang BELUM tertutup dibuang. Ini penting: crypto buka 24/7, jadi
    saat program dijalankan selalu ada satu lilin yang masih berjalan.
    Memasukkannya akan membuat indikator dihitung dari harga setengah jadi,
    dan sinyalnya bisa hilang lagi beberapa jam kemudian.
    """
    if timeframe not in INTERVAL:
        raise ValueError(f"Timeframe '{timeframe}' tidak dikenal")

    mulai = mulai or AWAL_MULA
    sekarang_ms = _ms(datetime.now(timezone.utc))
    keluar: list[tuple] = []
    start_ms = _ms(mulai)

    for _ in range(MAKS_HALAMAN):
        try:
            r = requests.get(URL, params={
                "symbol": simbol,
                "interval": INTERVAL[timeframe],
                "startTime": start_ms,
                "limit": BATAS,
            }, timeout=30)
        except Exception as e:
            # Dulu hanya dicetak lalu `break`, dan robot tetap "SELESAI" —
            # pesan pagi berbunyi "Tidak ada sinyal baru" seolah pasar sepi.
            raise GagalUnduh(
                f"Binance tidak bisa dihubungi ({type(e).__name__})",
                keluar, rinci=str(e)) from e

        if r.status_code != 200:
            raise GagalUnduh(
                f"Binance menolak permintaan (HTTP {r.status_code})",
                keluar, rinci=r.text[:160])

        data = r.json()
        if not data:
            break

        for k in data:
            waktu_buka, o, h, l, c, v = k[0], k[1], k[2], k[3], k[4], k[5]
            waktu_tutup = k[6]
            if waktu_tutup >= sekarang_ms:
                continue                      # lilin ini masih berjalan
            ts = datetime.fromtimestamp(waktu_buka / 1000, tz=timezone.utc)
            keluar.append((
                ts.strftime("%Y-%m-%d %H:%M:%S"),
                float(o), float(h), float(l), float(c), float(v),
            ))

        if len(data) < BATAS:
            break
        start_ms = data[-1][0] + 1

    return keluar


def simbol_tersedia(simbol: str) -> bool:
    """Periksa apakah pasangan itu benar-benar ada dan masih diperdagangkan."""
    try:
        r = requests.get(
            "https://data-api.binance.vision/api/v3/exchangeInfo",
            params={"symbol": simbol}, timeout=20,
        )
        if r.status_code != 200:
            return False
        return any(s.get("status") == "TRADING"
                   for s in r.json().get("symbols", []))
    except Exception:
        return False
