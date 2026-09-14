"""Penghubung antara pengaturan, sumber data, dan database.

Dua pasar dengan dua sumber yang sangat berbeda dirapikan di sini menjadi satu
bentuk yang sama, supaya bagian lain program tidak perlu tahu datanya berasal
dari Binance atau Yahoo.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pandas as pd

from . import db, sumber_crypto, sumber_forex, sumber_futures, waktu
from .backtest import Biaya
from .galat import GagalUnduh

PASAR = ("crypto", "forex", "futures")

SUMBER = {
    "crypto": sumber_crypto,
    "forex": sumber_forex,
    "futures": sumber_futures,
}

DURASI = {
    "harian": timedelta(days=1),
    "4jam": timedelta(hours=4),
    "1jam": timedelta(hours=1),
    "30menit": timedelta(minutes=30),
    "15menit": timedelta(minutes=15),
    "5menit": timedelta(minutes=5),
}


def didukung(pasar_nama: str, timeframe: str) -> bool:
    """Apakah sumber pasar ini menyediakan timeframe tersebut?

    Yahoo tidak menyediakan forex di bawah 1 jam, dan bahkan yang 1 jam pun
    hanya 730 hari terakhir. Binance menyediakan sampai 1 menit. Tanpa
    pemeriksaan ini, menjalankan scalping akan memunculkan galat untuk forex
    padahal yang diinginkan memang cuma futures.
    """
    return timeframe in SUMBER[pasar_nama].INTERVAL


def daftar(cfg: dict, pasar: str) -> list[dict]:
    """Watchlist satu pasar, diseragamkan menjadi daftar dictionary.

    Crypto ditulis sebagai daftar teks biasa di config.yaml, sedangkan forex
    perlu keterangan pip dan spread. Keduanya dipulangkan dalam bentuk yang
    sama supaya pemanggilnya tidak perlu bercabang.
    """
    bagian = cfg.get(pasar, {})
    if not bagian.get("aktif", False):
        return []
    hasil = []
    for item in bagian.get("watchlist", []):
        hasil.append({"simbol": item} if isinstance(item, str) else dict(item))
    return hasil


def semua_simbol(cfg: dict, hanya: str | None = None) -> list[tuple[str, dict]]:
    """Pasangan (pasar, keterangan simbol) untuk seluruh watchlist yang aktif."""
    keluar = []
    for pasar in PASAR:
        for item in daftar(cfg, pasar):
            if hanya and item["simbol"].upper() != hanya.upper():
                continue
            keluar.append((pasar, item))
    return keluar


def dua_arah(cfg: dict, pasar: str) -> bool:
    """Bolehkah membuka posisi jual di pasar ini?

    Crypto spot hanya bisa dibeli, jadi selalu False kecuali diatur lain.
    """
    return bool(cfg.get(pasar, {}).get("dua_arah", False))


def biaya(cfg: dict, pasar: str, item: dict) -> Biaya:
    """Model biaya yang sesuai untuk pasar ini.

    Crypto dan futures ditagih komisi persen, forex ditagih lewat spread —
    dua hal yang tidak bisa diwakili satu rumus. Futures punya komisi lebih
    murah daripada spot, tapi sebagai gantinya ada funding yang ditangani
    terpisah di mesin backtest.
    """
    if pasar in ("crypto", "futures"):
        return Biaya(jenis="persen",
                     fee_persen=float(cfg[pasar]["biaya"]["fee_persen"]))
    spread = float(item.get("spread_pip", 0)) * float(item.get("pip", 0))
    return Biaya(jenis="spread", spread=spread)


def leverage(cfg: dict, pasar: str) -> float:
    """Leverage untuk pasar ini. Selalu 1 kecuali futures."""
    return float(cfg.get(pasar, {}).get("leverage", 1))


def maintenance(cfg: dict, pasar: str) -> float:
    """Maintenance margin sebagai pecahan, mis. 0.005 untuk 0,5%."""
    return float(cfg.get(pasar, {}).get("maintenance_margin_persen", 0.5)) / 100


def batas_order(cfg: dict, pasar: str, simbol: str) -> dict | None:
    """Aturan order minimum bursa untuk satu simbol, atau None.

    Hanya futures yang punya daftarnya. Tanpa entri, backtest berperilaku
    seperti dulu: berapa pun kecilnya posisi, dianggap bisa dibuka.
    """
    return (cfg.get(pasar, {}).get("batas_order") or {}).get(simbol)


def modal_backtest(cfg: dict, pasar: str) -> float:
    """Modal yang dipakai backtest untuk pasar ini.

    Bawaannya `backtest.modal` (10 juta) — cukup besar sehingga order minimum
    tidak pernah berpengaruh. Futures bisa menyetel modal nyata lewat
    `modal_backtest`, dan di situlah batas order bursa mulai terasa.
    """
    return float(cfg.get(pasar, {}).get("modal_backtest")
                 or cfg["backtest"]["modal"])


def kunci_masalah(pasar: str, simbol: str) -> str:
    """Nama simbol di daftar data bermasalah, mis. 'BTCUSDT futures'.

    Nama pasarnya wajib ikut: BTCUSDT ada di crypto spot DAN futures. Tanpa
    itu, spot yang gagal diunduh akan ikut menandai sinyal futures yang
    datanya sebenarnya baik-baik saja.
    """
    return f"{simbol} {pasar}"


def perbaiki_ohlc(baris: list[tuple]) -> tuple[list[tuple], int]:
    """Paksa high/low mencakup open dan close.

    Data forex dari Yahoo punya cacat yang nyata: pada sekitar 2% lilin, harga
    penutupan berada DI LUAR rentang high-low. Itu mustahil menurut definisi —
    kalau harga pernah menyentuh angka penutupan, maka high sudah pasti minimal
    setinggi itu. (Diperiksa pada EURUSD: 128 dari 5.907 lilin, tersebar di
    semua tahun. Data crypto dari Binance tidak punya masalah ini sama sekali.)

    Perbaikannya tidak mengarang angka baru, hanya melebarkan high/low sampai
    memenuhi syarat yang memang harus dipenuhi. Ini penting karena backtest
    memeriksa batas rugi lewat `low`, sehingga `low` yang keliru bisa membuat
    kerugian tampak lebih kecil daripada yang sebenarnya terjadi.
    """
    keluar = []
    diperbaiki = 0
    for ts, o, h, l, c, v in baris:
        h2 = max(h, o, c)
        l2 = min(l, o, c)
        if h2 != h or l2 != l:
            diperbaiki += 1
        keluar.append((ts, o, h2, l2, c, v))
    return keluar, diperbaiki


def perbarui(conn: sqlite3.Connection, pasar: str, simbol: str,
             timeframe: str, penuh: bool = False) -> tuple[int, int]:
    """Ambil data yang belum tersimpan untuk satu simbol.

    Mengembalikan (jumlah baris disimpan, jumlah lilin yang high/low-nya
    dirapikan). Jumlah perbaikan dikembalikan sebagai nilai, bukan dicetak
    di sini — dulu dicetak langsung, dan karena tercetak SEBELUM baris nama
    simbolnya, angkanya mudah terbaca sebagai milik simbol sebelumnya.
    Kesalahan itu sempat masuk ke dokumentasi.

    Beberapa lilin terakhir sengaja diunduh ulang. Sumber data kadang
    memperbaiki angka yang sudah terbit, dan menimpanya lebih aman daripada
    menyimpan versi lama selamanya.

    `penuh=True` mengabaikan apa yang sudah tersimpan dan mengunduh seluruh
    riwayat lagi — dipakai kalau aturan pembersihan data berubah.
    """
    mulai = None
    if not penuh:
        terakhir = db.last_ts(conn, pasar, simbol, timeframe)
        if terakhir:
            mulai = (datetime.fromisoformat(terakhir)
                     .replace(tzinfo=timezone.utc) - 3 * DURASI[timeframe])

    modul = SUMBER[pasar]
    try:
        baris = modul.unduh(simbol, timeframe, mulai)
    except GagalUnduh as e:
        # Data yang sempat terambil sebelum sambungan putus tetap disimpan,
        # lalu kegagalannya diteruskan supaya ikut dilaporkan.
        if e.baris:
            bersih, _ = perbaiki_ohlc(e.baris)
            db.save_prices(conn, pasar, simbol, timeframe, bersih)
        raise
    if not baris:
        return 0, 0

    baris, diperbaiki = perbaiki_ohlc(baris)
    n = db.save_prices(conn, pasar, simbol, timeframe, baris)

    # Futures perlu riwayat funding rate. Diambil sekali per simbol, bukan
    # per timeframe, karena waktunya tetap tiap 8 jam apa pun timeframe-nya.
    if pasar == "futures":
        perbarui_funding(conn, simbol, penuh)
    return n, diperbaiki


def perbarui_funding(conn: sqlite3.Connection, simbol: str,
                     penuh: bool = False) -> int:
    """Ambil funding rate yang belum tersimpan untuk satu simbol futures."""
    mulai = None
    if not penuh:
        terakhir = db.last_funding(conn, simbol)
        if terakhir:
            mulai = (datetime.fromisoformat(terakhir)
                     .replace(tzinfo=timezone.utc) - timedelta(hours=16))
    try:
        baris = sumber_futures.unduh_funding(simbol, mulai)
    except GagalUnduh as e:
        if e.baris:
            db.save_funding(conn, simbol, e.baris)
        raise
    return db.save_funding(conn, simbol, baris)


def perbarui_semua(conn: sqlite3.Connection, cfg: dict, timeframe: str,
                   hanya: str | None = None,
                   penuh: bool = False) -> tuple[dict[str, int], dict[str, str]]:
    """Perbarui seluruh watchlist. Satu simbol gagal tidak menghentikan sisanya.

    Mengembalikan (hasil, gagal): jumlah baris baru per simbol, dan sebab
    kegagalan untuk simbol yang tidak berhasil diperbarui — dengan kunci dari
    kunci_masalah(). Daftar `gagal` diteruskan ke pesan Telegram; dulu
    kegagalan hanya tercetak di log.
    """
    hasil: dict[str, int] = {}
    gagal: dict[str, str] = {}
    dilewati = set()
    for pasar, item in semua_simbol(cfg, hanya):
        simbol = item["simbol"]
        if not didukung(pasar, timeframe):
            if pasar not in dilewati:
                print(f"  {pasar:<12} dilewati — sumbernya tidak menyediakan "
                      f"timeframe {timeframe}")
                dilewati.add(pasar)
            continue
        try:
            n, dirapikan = perbarui(conn, pasar, simbol, timeframe, penuh=penuh)
        except GagalUnduh as e:
            print(f"  {simbol:<12} GAGAL: {e.pesan}"
                  + (f" — {e.rinci}" if e.rinci else ""))
            hasil[simbol] = 0
            gagal[kunci_masalah(pasar, simbol)] = e.pesan
            continue
        except Exception as e:
            print(f"  {simbol:<12} GAGAL: {e}")
            hasil[simbol] = 0
            gagal[kunci_masalah(pasar, simbol)] = (
                f"galat program ({type(e).__name__})")
            continue
        total = db.count_rows(conn, pasar, simbol, timeframe)
        hasil[simbol] = n
        # Keterangan perbaikan ditempel di baris yang sama dengan nama
        # simbolnya, supaya tidak mungkin terbaca sebagai milik simbol lain.
        catatan = f"  [{dirapikan} lilin dirapikan]" if dirapikan else ""
        print(f"  {simbol:<12} {pasar:<7} +{n:>5} baris  "
              f"(total {total}){catatan}")
    return hasil, gagal


def _jam_tutup_forex(mulai: datetime, akhir: datetime) -> timedelta:
    """Lama pasar forex tutup akhir pekan di antara dua waktu UTC.

    Forex dan emas tutup Jumat sore New York sampai Minggu sore — di UTC
    kira-kira Jumat 21:00 sampai Minggu 21:00. Selama itu lilin memang tidak
    terbit, jadi tidak boleh dihitung sebagai data yang tertinggal.
    """
    total = timedelta(0)
    mundur = (mulai.weekday() - 4) % 7          # Jumat = 4
    jumat = (mulai - timedelta(days=mundur)).replace(
        hour=21, minute=0, second=0, microsecond=0)
    if jumat > mulai:
        jumat -= timedelta(days=7)
    while jumat < akhir:
        a = max(jumat, mulai)
        b = min(jumat + timedelta(hours=48), akhir)
        if b > a:
            total += b - a
        jumat += timedelta(days=7)
    return total


def data_tertinggal(conn: sqlite3.Connection, cfg: dict, timeframe: str,
                    hanya: str | None = None,
                    sekarang: datetime | None = None) -> dict[str, str]:
    """Simbol yang lilin terakhirnya jauh lebih tua daripada seharusnya.

    Pelengkap `gagal` dari perbarui_semua(). Tidak setiap kegagalan melempar
    galat: Yahoo sering diam-diam mengembalikan tabel kosong, dan sambungan
    bisa berhasil tapi datanya tidak bertambah. Yang pasti terlihat hanyalah
    akibatnya — lilin terakhir di database makin tua.

    Dianggap tertinggal kalau lilin terakhir tutup lebih dari dua lilin yang
    lalu, ditambah kelonggaran. Untuk forex, jam tutup akhir pekan tidak
    dihitung dan kelonggarannya 3 jam, karena emas punya jeda harian dan jam
    buka hari Minggu bergeser antara musim panas dan musim dingin.
    """
    sekarang = sekarang or datetime.now(timezone.utc)
    durasi = DURASI[timeframe]
    keluar: dict[str, str] = {}
    for pasar_nama, item in semua_simbol(cfg, hanya):
        if not didukung(pasar_nama, timeframe):
            continue
        simbol = item["simbol"]
        kunci = kunci_masalah(pasar_nama, simbol)
        terakhir = db.last_ts(conn, pasar_nama, simbol, timeframe)
        if not terakhir:
            keluar[kunci] = "belum ada data sama sekali"
            continue
        tutup = (datetime.fromisoformat(terakhir).replace(tzinfo=timezone.utc)
                 + durasi)
        umur = sekarang - tutup
        longgar = timedelta(minutes=5)
        if pasar_nama == "forex":
            umur -= _jam_tutup_forex(tutup, sekarang)
            longgar = timedelta(hours=3)
        if umur > 2 * durasi + longgar:
            teks = (f"lilin terakhir tutup {waktu.ke_wib(tutup)}, "
                    f"tertinggal {waktu.lama_teks(umur)}")
            if pasar_nama == "forex":
                teks += " (bisa juga karena libur bursa)"
            keluar[kunci] = teks
    return keluar


def ambil(conn: sqlite3.Connection, pasar: str, simbol: str, timeframe: str,
          mulai: str | None = None, sampai: str | None = None) -> pd.DataFrame:
    """Baca data satu simbol dari database."""
    return db.ambil(conn, pasar, simbol, timeframe, mulai, sampai)
