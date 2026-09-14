"""Waktu yang ditampilkan ke pengguna selalu WIB.

Database tetap menyimpan UTC, karena itulah yang dipakai Binance dan hasil
perapian data Yahoo. Tapi pesan Telegram dibaca di Indonesia. Dulu judul pesan
sudah WIB (mengikuti jam VPS), sementara jam lilin di badan pesan masih UTC
tanpa keterangan — "07:30" di judul dan "00:30" di badan pesan merujuk ke saat
yang sama. Mencocokkan riwayat Binance dengan sinyal jadi harus menambah
7 jam di kepala.

Sengaja memakai offset tetap, bukan `zoneinfo("Asia/Jakarta")`: WIB tidak
mengenal jam musim panas, dan Windows tidak punya basis data zona waktu
bawaan — `zoneinfo` akan gagal di laptop tanpa paket tambahan.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

WIB = timezone(timedelta(hours=7), "WIB")

BULAN = ("Jan", "Feb", "Mar", "Apr", "Mei", "Jun",
         "Jul", "Agu", "Sep", "Okt", "Nov", "Des")

_FORMAT_DB = "%Y-%m-%d %H:%M:%S"


def _utc(ts) -> datetime:
    """Teks dari database, Timestamp pandas, atau datetime -> datetime UTC.

    Waktu tanpa zona dianggap UTC, karena begitulah semua waktu disimpan.
    """
    if isinstance(ts, str):
        dt = datetime.fromisoformat(ts.strip())
    elif hasattr(ts, "to_pydatetime"):
        dt = ts.to_pydatetime()
    else:
        dt = ts
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def ke_wib(ts) -> str:
    """'2026-09-13 00:30:00' (UTC) -> '13 Sep 07:30 WIB'."""
    dt = _utc(ts).astimezone(WIB)
    return f"{dt.day:02d} {BULAN[dt.month - 1]} {dt:%H:%M} WIB"


def sekarang_wib() -> datetime:
    """Jam sekarang dalam WIB, tidak bergantung zona waktu mesin."""
    return datetime.now(WIB)


def tanggal_wib(ts) -> date:
    """Tanggal WIB dari sebuah waktu UTC."""
    return _utc(ts).astimezone(WIB).date()


def rentang_utc_hari_wib(tanggal: str) -> tuple[str, str]:
    """Satu tanggal WIB sebagai rentang teks UTC setengah terbuka [mulai, akhir).

    '2026-09-10' -> ('2026-09-09 17:00:00', '2026-09-10 17:00:00')

    Bentuk teksnya sama dengan kolom `ts` di database, sehingga bisa langsung
    dibandingkan dengan `>=` dan `<` di SQL.
    """
    awal = datetime.fromisoformat(tanggal).replace(tzinfo=WIB)
    mulai = awal.astimezone(timezone.utc)
    akhir = (awal + timedelta(days=1)).astimezone(timezone.utc)
    return mulai.strftime(_FORMAT_DB), akhir.strftime(_FORMAT_DB)


def lama_teks(durasi: timedelta) -> str:
    """timedelta -> '45 menit' / '3 jam' / '2 hari', dibulatkan ke bawah."""
    menit = int(durasi.total_seconds() // 60)
    if menit < 120:
        return f"{max(menit, 0)} menit"
    jam = menit // 60
    if jam < 48:
        return f"{jam} jam"
    return f"{jam // 24} hari"
