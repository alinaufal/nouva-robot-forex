"""Pengambilan data Binance Futures (perpetual USDT-M).

Beda penting dengan sumber spot:

* Alamatnya `fapi.binance.com`. Dari beberapa jaringan rumahan di Indonesia
  alamat ini **tidak bisa dijangkau sama sekali** (connect timeout), sedangkan
  dari VPS lancar. Sudah diperiksa langsung: laptop gagal, VPS berhasil.
* Ada **funding rate** — biaya yang ditagih tiap 8 jam kepada salah satu sisi
  pasar. Ini tidak punya padanan di spot dan wajib ikut dihitung, kalau tidak
  backtest-nya akan terlihat lebih untung daripada kenyataan.
* Riwayatnya lebih pendek: BTCUSDT baru mulai 2019-09-08, bukan 2017.
* Batas per permintaan 1500 lilin, bukan 1000 seperti spot.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import requests

from .galat import GagalUnduh

URL_KLINES ="https://fapi.binance.com/fapi/v1/klines"
URL_FUNDING = "https://fapi.binance.com/fapi/v1/fundingRate"

INTERVAL = {
    "harian": "1d",
    "4jam": "4h",
    "1jam": "1h",
    "30menit": "30m",
    "15menit": "15m",
    "5menit": "5m",
}
DURASI = {
    "harian": timedelta(days=1),
    "4jam": timedelta(hours=4),
    "1jam": timedelta(hours=1),
    "30menit": timedelta(minutes=30),
    "15menit": timedelta(minutes=15),
    "5menit": timedelta(minutes=5),
}

BATAS_KLINES = 1500
BATAS_FUNDING = 500
# Timeframe rendah butuh jauh lebih banyak halaman. 1 jam sejak 2019 saja
# sekitar 61.000 lilin = 41 halaman; 5 menit lebih dari 700.000 lilin.
MAKS_HALAMAN = 700

# Perpetual futures Binance dimulai September 2019
AWAL_MULA = datetime(2019, 9, 1, tzinfo=timezone.utc)

# Funding ditagih tiap 8 jam
JEDA_FUNDING = timedelta(hours=8)


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def unduh(simbol: str, timeframe: str,
          mulai: datetime | None = None) -> list[tuple]:
    """Ambil lilin futures. Bentuk keluarannya sama persis dengan sumber spot.

    Lilin yang belum tertutup dibuang, sama seperti di spot.
    """
    if timeframe not in INTERVAL:
        raise ValueError(f"Timeframe '{timeframe}' tidak dikenal")

    mulai = mulai or AWAL_MULA
    sekarang_ms = _ms(datetime.now(timezone.utc))
    keluar: list[tuple] = []
    start_ms = _ms(mulai)

    for _ in range(MAKS_HALAMAN):
        try:
            r = requests.get(URL_KLINES, params={
                "symbol": simbol,
                "interval": INTERVAL[timeframe],
                "startTime": start_ms,
                "limit": BATAS_KLINES,
            }, timeout=30)
        except Exception as e:
            raise GagalUnduh(
                f"Binance Futures tidak bisa dihubungi ({type(e).__name__})",
                keluar,
                rinci=(f"{e} — dari jaringan rumahan alamat ini sering "
                       "diblokir, jalankan dari VPS")) from e

        if r.status_code != 200:
            raise GagalUnduh(
                f"Binance Futures menolak permintaan (HTTP {r.status_code})",
                keluar, rinci=r.text[:160])

        data = r.json()
        if not data:
            break

        for k in data:
            if k[6] >= sekarang_ms:      # lilin masih berjalan
                continue
            ts = datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc)
            keluar.append((
                ts.strftime("%Y-%m-%d %H:%M:%S"),
                float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]),
            ))

        if len(data) < BATAS_KLINES:
            break
        start_ms = data[-1][0] + 1

    return keluar


def unduh_funding(simbol: str,
                  mulai: datetime | None = None) -> list[tuple[str, float]]:
    """Ambil riwayat funding rate, satu entri tiap 8 jam.

    Mengembalikan daftar (ts, rate). Rate bernilai pecahan, bukan persen:
    0.0001 berarti 0,01%.

    Tanda rate menentukan siapa yang membayar:
      rate > 0  -> pemegang posisi BELI membayar pemegang posisi JUAL
      rate < 0  -> sebaliknya

    Pada BTCUSDT setahun terakhir, rate positif 835 dari 1096 kali — artinya
    posisi beli hampir selalu yang menanggung biaya.
    """
    mulai = mulai or AWAL_MULA
    keluar: list[tuple[str, float]] = []
    start_ms = _ms(mulai)

    for _ in range(MAKS_HALAMAN):
        try:
            r = requests.get(URL_FUNDING, params={
                "symbol": simbol,
                "startTime": start_ms,
                "limit": BATAS_FUNDING,
            }, timeout=30)
        except Exception as e:
            raise GagalUnduh(
                f"funding rate tidak bisa diambil ({type(e).__name__})",
                keluar, rinci=str(e)) from e

        if r.status_code != 200:
            raise GagalUnduh(
                f"funding rate ditolak (HTTP {r.status_code})",
                keluar, rinci=r.text[:160])

        data = r.json()
        if not data:
            break

        for x in data:
            ts = datetime.fromtimestamp(x["fundingTime"] / 1000, tz=timezone.utc)
            keluar.append((ts.strftime("%Y-%m-%d %H:%M:%S"),
                           float(x["fundingRate"])))

        if len(data) < BATAS_FUNDING:
            break
        start_ms = data[-1]["fundingTime"] + 1

    return keluar
