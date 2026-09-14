"""Penyimpanan data harga dan sinyal dengan SQLite.

Beda utama dengan project saham: kunci barisnya bukan lagi (ticker, tanggal),
melainkan **(pasar, simbol, timeframe, ts)**. Tanpa `timeframe` di kunci, data
harian dan data 4 jam akan saling menimpa. Dan `ts` menyimpan waktu lengkap,
bukan hanya tanggal, karena satu hari berisi enam lilin 4-jam.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from . import waktu

SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
    pasar     TEXT NOT NULL,       -- 'crypto' atau 'forex'
    simbol    TEXT NOT NULL,
    timeframe TEXT NOT NULL,       -- 'harian' atau '4jam'
    ts        TEXT NOT NULL,       -- 'YYYY-MM-DD HH:MM:SS'
    open   REAL,
    high   REAL,
    low    REAL,
    close  REAL,
    volume REAL,                   -- REAL, bukan INTEGER: volume crypto pecahan
    PRIMARY KEY (pasar, simbol, timeframe, ts)
);

-- UNIQUE di bawah mencegah sinyal yang sama dikirim dua kali kalau program
-- tidak sengaja dijalankan berulang. Nama strategi ikut masuk kunci supaya
-- mengganti strategi tidak membuat sinyal lama dianggap sudah pernah dikirim.
CREATE TABLE IF NOT EXISTS signals (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    pasar      TEXT NOT NULL,
    simbol     TEXT NOT NULL,
    timeframe  TEXT NOT NULL,
    strategi   TEXT NOT NULL,
    ts         TEXT NOT NULL,
    action     TEXT NOT NULL,      -- 'BELI' atau 'JUAL'
    close      REAL,
    reason     TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (pasar, simbol, timeframe, strategi, ts, action)
);

-- Funding rate hanya ada di futures. Ditagih tiap 8 jam kepada salah satu
-- sisi pasar, jadi waktunya tidak sejajar dengan lilin harga dan disimpan
-- di tabelnya sendiri.
CREATE TABLE IF NOT EXISTS funding (
    simbol TEXT NOT NULL,
    ts     TEXT NOT NULL,       -- 'YYYY-MM-DD HH:MM:SS', tiap 8 jam
    rate   REAL NOT NULL,       -- pecahan, bukan persen: 0.0001 = 0,01%
    PRIMARY KEY (simbol, ts)
);

-- Ingatan posisi yang sedang terbuka.
--
-- Tanpa tabel ini, robot tidak tahu apa yang sedang Anda pegang, sehingga
-- sinyal "TUTUP" terkirim berulang kali walau tidak ada posisi apa pun.
-- Dengan tabel ini, sinyal keluar hanya muncul kalau ada yang perlu ditutup,
-- dan robot bisa memberi tahu saat batas rugi atau take profit tersentuh.
--
-- Isinya adalah catatan posisi menurut ATURAN STRATEGI, bukan posisi
-- sungguhan di bursa. Robot tidak pernah menyentuh akun Anda dan tidak tahu
-- apakah sinyalnya benar-benar Anda eksekusi.
CREATE TABLE IF NOT EXISTS posisi (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    pasar         TEXT NOT NULL,
    simbol        TEXT NOT NULL,
    timeframe     TEXT NOT NULL,
    strategi      TEXT NOT NULL,
    arah          INTEGER NOT NULL,   -- 1 = beli, -1 = jual
    ts_masuk      TEXT NOT NULL,
    harga_masuk   REAL NOT NULL,
    stop          REAL,
    target        REAL,
    ts_keluar     TEXT,               -- NULL selama posisi masih terbuka
    harga_keluar  REAL,
    alasan_keluar TEXT
);

CREATE INDEX IF NOT EXISTS idx_posisi_terbuka
    ON posisi(pasar, simbol, timeframe, strategi, ts_keluar);

CREATE INDEX IF NOT EXISTS idx_prices_lookup
    ON prices(pasar, simbol, timeframe, ts);
"""


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def last_ts(conn: sqlite3.Connection, pasar: str, simbol: str,
            timeframe: str) -> str | None:
    """Waktu terakhir yang sudah tersimpan untuk satu simbol."""
    row = conn.execute(
        "SELECT MAX(ts) AS t FROM prices "
        "WHERE pasar = ? AND simbol = ? AND timeframe = ?",
        (pasar, simbol, timeframe),
    ).fetchone()
    return row["t"] if row and row["t"] else None


def save_prices(conn: sqlite3.Connection, pasar: str, simbol: str,
                timeframe: str, rows: list[tuple]) -> int:
    """Simpan baris harga; baris lama dengan waktu sama ditimpa.

    rows: (ts, open, high, low, close, volume)
    """
    if not rows:
        return 0
    conn.executemany(
        """INSERT INTO prices
               (pasar, simbol, timeframe, ts, open, high, low, close, volume)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(pasar, simbol, timeframe, ts) DO UPDATE SET
               open=excluded.open, high=excluded.high, low=excluded.low,
               close=excluded.close, volume=excluded.volume""",
        [(pasar, simbol, timeframe, *r) for r in rows],
    )
    conn.commit()
    return len(rows)


def save_signal(conn: sqlite3.Connection, pasar: str, simbol: str,
                timeframe: str, strategi: str, ts: str, action: str,
                close: float, reason: str) -> bool:
    """Simpan sinyal. False berarti sinyal itu sudah pernah dicatat."""
    cur = conn.execute(
        """INSERT OR IGNORE INTO signals
               (pasar, simbol, timeframe, strategi, ts, action, close, reason)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (pasar, simbol, timeframe, strategi, ts, action, close, reason),
    )
    conn.commit()
    return cur.rowcount > 0


def posisi_terbuka(conn: sqlite3.Connection, pasar: str, simbol: str,
                   timeframe: str, strategi: str,
                   arah: int | None = None) -> sqlite3.Row | None:
    """Posisi yang masih terbuka untuk satu kombinasi, atau None.

    Satu posisi terbuka PER ARAH per (pasar, simbol, timeframe, strategi),
    sehingga posisi beli dan posisi jual bisa hidup berdampingan.

    Isi `arah` setiap kali jawabannya dipakai untuk mengambil keputusan.
    Tanpa penyaring arah, saat posisi beli dan jual sama-sama terbuka fungsi
    ini mengembalikan yang paling baru saja — dan sinyal "TUTUP BELI" bisa
    gagal menemukan posisi belinya hanya karena ada posisi jual yang lebih
    muda. Akibatnya posisi menggantung tanpa pernah dikabari jalan keluarnya.
    """
    sql = ("SELECT * FROM posisi WHERE pasar = ? AND simbol = ? "
           "AND timeframe = ? AND strategi = ? AND ts_keluar IS NULL")
    nilai: list = [pasar, simbol, timeframe, strategi]
    if arah is not None:
        sql += " AND arah = ?"
        nilai.append(int(arah))
    return conn.execute(sql + " ORDER BY id DESC LIMIT 1", nilai).fetchone()


def buka_posisi(conn: sqlite3.Connection, pasar: str, simbol: str,
                timeframe: str, strategi: str, arah: int, ts: str,
                harga: float, stop: float | None,
                target: float | None) -> int:
    """Catat pembukaan posisi. Mengembalikan id barunya."""
    cur = conn.execute(
        """INSERT INTO posisi (pasar, simbol, timeframe, strategi, arah,
                               ts_masuk, harga_masuk, stop, target)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (pasar, simbol, timeframe, strategi, arah, ts, harga, stop, target),
    )
    conn.commit()
    return int(cur.lastrowid)


def tutup_posisi(conn: sqlite3.Connection, id_posisi: int, ts: str,
                 harga: float, alasan: str) -> None:
    conn.execute(
        "UPDATE posisi SET ts_keluar = ?, harga_keluar = ?, alasan_keluar = ? "
        "WHERE id = ? AND ts_keluar IS NULL",
        (ts, harga, alasan, id_posisi),
    )
    conn.commit()


def semua_posisi_terbuka(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Seluruh posisi yang masih terbuka, untuk ditampilkan atau diperiksa."""
    return conn.execute(
        "SELECT * FROM posisi WHERE ts_keluar IS NULL ORDER BY ts_masuk"
    ).fetchall()


def posisi_searah(conn: sqlite3.Connection, pasar: str, timeframe: str,
                  strategi: str, arah: int) -> list[sqlite3.Row]:
    """Seluruh posisi terbuka dengan arah yang sama, di SEMUA simbol.

    Dipakai untuk memperingatkan taruhan yang menumpuk. Tanggal 10 September
    robot mengirim JUAL BTC, SOL, dan ETH dalam 90 menit — tiga pesan yang
    tampak seperti tiga peluang, padahal ketiga koin itu bergerak searah
    (korelasi 0,59) sehingga sebenarnya satu taruhan yang dilipat tiga.
    """
    return conn.execute(
        "SELECT * FROM posisi WHERE pasar = ? AND timeframe = ? "
        "AND strategi = ? AND arah = ? AND ts_keluar IS NULL "
        "ORDER BY ts_masuk",
        (pasar, timeframe, strategi, int(arah)),
    ).fetchall()


def hitung_entry_hari_ini(conn: sqlite3.Connection, pasar: str, tanggal: str,
                          timeframe: str | None = None) -> int:
    """Berapa sinyal PEMBUKAAN posisi yang sudah tercatat pada tanggal itu.

    `tanggal` adalah tanggal WIB. Kolom `ts` disimpan dalam UTC, jadi satu hari
    WIB dihitung sebagai rentang 17:00 UTC hari sebelumnya sampai 17:00 UTC.
    Dulu tanggal WIB dicocokkan langsung dengan `ts LIKE 'tanggal%'` — sinyal
    antara 00:00 dan 07:00 WIB (tanggal UTC-nya masih kemarin) tidak pernah
    masuk hitungan, sehingga batasnya bisa terlampaui di jam-jam itu.

    Dihitung dari database, bukan dari variabel di dalam program, karena robot
    dijalankan cron sebagai proses baru tiap 15 menit — variabel apa pun akan
    hilang begitu proses selesai. Hanya database yang ingat.

    Digabung untuk seluruh simbol: batas 10 berarti 10 untuk BTC+ETH+SOL
    bersama-sama, bukan 10 masing-masing.

    'TUTUP BELI' dan 'TUTUP JUAL' sengaja tidak dihitung — sinyal keluar tidak
    pernah dibatasi.
    """
    mulai, akhir = waktu.rentang_utc_hari_wib(tanggal)
    sql = ("SELECT COUNT(*) AS n FROM signals "
           "WHERE pasar = ? AND action IN ('BELI', 'JUAL') "
           "AND ts >= ? AND ts < ?")
    params: list = [pasar, mulai, akhir]
    if timeframe:
        sql += " AND timeframe = ?"
        params.append(timeframe)
    row = conn.execute(sql, params).fetchone()
    return row["n"] if row else 0


def count_rows(conn: sqlite3.Connection, pasar: str, simbol: str,
               timeframe: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM prices "
        "WHERE pasar = ? AND simbol = ? AND timeframe = ?",
        (pasar, simbol, timeframe),
    ).fetchone()
    return row["n"]


def save_funding(conn: sqlite3.Connection, simbol: str,
                 rows: list[tuple]) -> int:
    """Simpan riwayat funding rate. rows: (ts, rate)"""
    if not rows:
        return 0
    conn.executemany(
        """INSERT INTO funding (simbol, ts, rate) VALUES (?, ?, ?)
           ON CONFLICT(simbol, ts) DO UPDATE SET rate=excluded.rate""",
        [(simbol, *r) for r in rows],
    )
    conn.commit()
    return len(rows)


def last_funding(conn: sqlite3.Connection, simbol: str) -> str | None:
    row = conn.execute(
        "SELECT MAX(ts) AS t FROM funding WHERE simbol = ?", (simbol,)
    ).fetchone()
    return row["t"] if row and row["t"] else None


def ambil_funding(conn: sqlite3.Connection, simbol: str,
                  mulai: str | None = None,
                  sampai: str | None = None) -> pd.Series:
    """Riwayat funding sebagai Series ber-index waktu."""
    sql = "SELECT ts, rate FROM funding WHERE simbol = ?"
    params: list = [simbol]
    if mulai:
        sql += " AND ts >= ?"
        params.append(mulai)
    if sampai:
        sql += " AND ts < ?"
        params.append(sampai)
    sql += " ORDER BY ts"

    df = pd.read_sql_query(sql, conn, params=params, parse_dates=["ts"])
    if df.empty:
        return pd.Series(dtype=float)
    return df.set_index("ts")["rate"]


def ambil(conn: sqlite3.Connection, pasar: str, simbol: str, timeframe: str,
          mulai: str | None = None, sampai: str | None = None) -> pd.DataFrame:
    """Baca data satu simbol sebagai DataFrame ber-index waktu."""
    sql = ("SELECT ts, open, high, low, close, volume FROM prices "
           "WHERE pasar = ? AND simbol = ? AND timeframe = ?")
    params: list = [pasar, simbol, timeframe]
    if mulai:
        sql += " AND ts >= ?"
        params.append(mulai)
    if sampai:
        sql += " AND ts < ?"
        params.append(sampai)
    sql += " ORDER BY ts"

    df = pd.read_sql_query(sql, conn, params=params, parse_dates=["ts"])
    if df.empty:
        return df
    return df.set_index("ts")
