"""Pencarian sinyal pada data terbaru.

Program ini TIDAK tahu posisi apa yang sedang Anda pegang — ia hanya membaca
harga. Karena itu sinyal "tutup" bersifat pemberitahuan: *kalau* Anda sedang
memegang posisi itu, syarat keluarnya sudah terpenuhi. Keputusannya tetap
di tangan Anda.
"""
from __future__ import annotations

import sqlite3

import pandas as pd

from . import db, pasar, strategy, waktu
from .config import parameter

# Urutannya menentukan urutan tampil di notifikasi.
AKSI = (
    ("beli", "BELI"),
    ("jual", "JUAL"),
    ("tutup_beli", "TUTUP BELI"),
    ("tutup_jual", "TUTUP JUAL"),
)


def periksa(conn: sqlite3.Connection, cfg: dict, nama_pasar: str, item: dict,
            timeframe: str, nama_strategi: str,
            lilin_terakhir: int = 3) -> list[dict]:
    """Cari sinyal pada beberapa lilin terakhir untuk satu simbol.

    Diperiksa beberapa lilin ke belakang, bukan hanya yang paling akhir, supaya
    sinyal tidak terlewat kalau program sempat tidak dijalankan — komputer
    mati, internet putus, atau libur panjang. Sinyal yang sudah pernah dikirim
    disaring oleh database, bukan di sini.
    """
    simbol = item["simbol"]
    # `item` diteruskan supaya setelan per simbol di watchlist — misalnya jam
    # sesi ramai — ikut terbawa ke strateginya.
    p = parameter(cfg, nama_strategi, timeframe, item)

    df = pasar.ambil(conn, nama_pasar, simbol, timeframe)
    perlu = strategy.masa_pemanasan(p, nama_strategi)
    if df.empty or len(df) < perlu:
        return []

    dua = pasar.dua_arah(cfg, nama_pasar)
    data = strategy.beri_sinyal(df, p, nama_strategi, dua)

    hasil = []
    # Umur sinyal dihitung dalam LILIN, bukan menit, supaya artinya sama di
    # semua timeframe: 0 berarti lilin yang baru saja tutup.
    ekor = data.tail(lilin_terakhir)
    jumlah_ekor = len(ekor)
    for urutan, (ts, baris) in enumerate(ekor.iterrows()):
        umur = jumlah_ekor - 1 - urutan
        for kolom, label in AKSI:
            if not bool(baris[kolom]):
                continue
            # Pasar satu arah tidak mengenal posisi jual sama sekali
            if not dua and kolom in ("jual", "tutup_jual"):
                continue

            masuk = kolom in ("beli", "jual")
            arah = 1 if kolom == "beli" else -1
            butir = {
                "pasar": nama_pasar,
                "simbol": simbol,
                "timeframe": timeframe,
                "strategi": nama_strategi,
                "ts": ts.strftime("%Y-%m-%d %H:%M:%S"),
                "aksi": label,
                "harga": float(baris["close"]),
                "alasan": "",
                "umur_lilin": umur,
            }
            if masuk:
                kunci = "alasan_beli" if kolom == "beli" else "alasan_jual"
                butir["alasan"] = str(baris.get(kunci, ""))
                nilai_atr = baris.get("atr")
                if pd.notna(nilai_atr) and nilai_atr > 0:
                    # Sama seperti di backtest: kalau strategi menyediakan
                    # batas rugi struktural, itu yang dipakai.
                    stop = strategy.pilih_stop(
                        butir["harga"], float(nilai_atr),
                        p["atr_pengali_stop"], arah,
                        baris.get("stop_beli" if arah == 1 else "stop_jual"),
                    )
                    butir["saran_stop"] = stop
                    # Take profit sebagai kelipatan jarak batas rugi.
                    # Kalau tp_rasio 0 atau tidak diatur, bagian ini tidak
                    # muncul di notifikasi sama sekali.
                    rasio = float(p.get("tp_rasio", 0) or 0)
                    if rasio > 0:
                        jarak = abs(butir["harga"] - stop)
                        butir["saran_tp"] = butir["harga"] + arah * rasio * jarak
                        butir["tp_rasio"] = rasio
            else:
                butir["alasan"] = "syarat keluar terpenuhi"

            if "rsi" in data.columns and pd.notna(baris.get("rsi")):
                butir["rsi"] = round(float(baris["rsi"]), 1)
            hasil.append(butir)
    return hasil


def periksa_batas_tersentuh(conn: sqlite3.Connection, cfg: dict,
                            timeframe: str, nama_strategi: str) -> list[dict]:
    """Cari posisi terbuka yang batas rugi atau take profit-nya sudah kena.

    Ini kabar yang tidak bisa didapat dari sinyal biasa. Batas rugi dan take
    profit tersentuh di tengah lilin, bukan pada penutupan, sehingga tidak
    pernah muncul sebagai sinyal. Tanpa pemeriksaan ini, robot akan terus
    mengira Anda masih memegang posisi yang sebenarnya sudah tertutup.

    Kalau satu lilin menyentuh keduanya, batas rugi dianggap duluan — kita
    tidak tahu urutan sebenarnya di dalam lilin, dan menganggap take profit
    duluan berarti membohongi diri sendiri.
    """
    hasil = []
    for pos in db.semua_posisi_terbuka(conn):
        if pos["timeframe"] != timeframe or pos["strategi"] != nama_strategi:
            continue

        df = pasar.ambil(conn, pos["pasar"], pos["simbol"], timeframe,
                         mulai=pos["ts_masuk"])
        if df.empty:
            continue
        # Lilin pembukaan sendiri ikut diperiksa, sama seperti di backtest.
        arah = int(pos["arah"])
        stop = pos["stop"]
        target = pos["target"]

        kena_ts = None
        kena_harga = None
        kena_alasan = None
        for ts, b in df.iterrows():
            if stop is not None:
                if (arah == 1 and b["low"] <= stop) or \
                   (arah == -1 and b["high"] >= stop):
                    kena_ts, kena_harga, kena_alasan = ts, stop, "KENA BATAS RUGI"
                    break
            if target is not None:
                if (arah == 1 and b["high"] >= target) or \
                   (arah == -1 and b["low"] <= target):
                    kena_ts, kena_harga, kena_alasan = ts, target, "KENA TAKE PROFIT"
                    break
        if kena_ts is None:
            continue

        waktu_kena = kena_ts.strftime("%Y-%m-%d %H:%M:%S")
        db.tutup_posisi(conn, pos["id"], waktu_kena, float(kena_harga),
                        kena_alasan)
        hasil.append({
            "pasar": pos["pasar"],
            "simbol": pos["simbol"],
            "timeframe": timeframe,
            "strategi": nama_strategi,
            "ts": waktu_kena,
            "aksi": kena_alasan,
            "harga": float(kena_harga),
            "alasan": (f"posisi {'beli' if arah == 1 else 'jual'} dibuka "
                       f"{waktu.ke_wib(pos['ts_masuk'])} "
                       f"@ {pos['harga_masuk']:,.4f}"),
        })
    return hasil


def _peringatan_searah(conn: sqlite3.Connection, cfg: dict, nama_pasar: str,
                       simbol: str, timeframe: str, nama_strategi: str,
                       arah: int) -> str:
    """Teks peringatan kalau sinyal ini menumpuk taruhan searah, atau "".

    Sinyal TIDAK ditahan — hanya diberi keterangan. Menahannya akan mengubah
    aliran sinyal dari yang pernah diuji backtest, padahal backtest berjalan
    per simbol dan tidak bisa mengukur efek aturan lintas simbol.

    Yang dihitung: posisi terbuka dengan arah yang sama di SIMBOL LAIN, pada
    pasar, timeframe, dan strategi yang sama. Simbol yang sama tidak ikut
    dihitung — sinyal susulan di simbol itu sudah diberi keterangan sendiri.
    """
    batas = int(cfg.get(nama_pasar, {}).get("maks_posisi_searah", 0) or 0)
    if batas <= 0:
        return ""
    lain = sorted({r["simbol"] for r in db.posisi_searah(
        conn, nama_pasar, timeframe, nama_strategi, arah)
        if r["simbol"] != simbol})
    if len(lain) < batas:
        return ""
    sisi = "BELI" if arah == 1 else "JUAL"
    return (f"sudah ada {len(lain)} posisi {sisi} terbuka "
            f"({', '.join(lain)}) — sinyal ini menambah taruhan yang sama")


def periksa_semua(conn: sqlite3.Connection, cfg: dict, timeframe: str,
                  nama_strategi: str, hanya: str | None = None,
                  hanya_baru: bool = True) -> list[dict]:
    """Periksa seluruh watchlist yang aktif.

    `hanya_baru=True` menyaring sinyal yang sudah pernah dicatat, supaya
    notifikasi tidak mengulang kejadian yang sama tiap kali program jalan.
    """
    lilin = int(cfg.get("notifikasi", {}).get("lilin_terakhir_diperiksa", 3))
    lacak = bool(cfg.get("notifikasi", {}).get("lacak_posisi", True))
    tahan_masuk = bool(cfg.get("notifikasi", {})
                       .get("tahan_sinyal_masuk", False))
    maks_umur = int(cfg.get("notifikasi", {})
                    .get("maks_umur_lilin_masuk", 3))

    # Konfirmasi TradingView, kalau dinyalakan. Dibaca SEKALI di awal untuk
    # seluruh simbol, bukan per sinyal — satu permintaan jaringan, bukan
    # sepuluh. Nilainya adalah keadaan SEKARANG, jadi hanya masuk akal untuk
    # lilin yang baru saja lewat; itu sudah dibatasi lilin_terakhir_diperiksa.
    tv_cfg = (cfg.get("konfirmasi", {}) or {}).get("tradingview") or {}
    tv_nilai: dict = {}
    tv_ambang = float(tv_cfg.get("ambang", 0.1))
    if tv_cfg.get("aktif"):
        from . import tradingview as tv
        peta_ticker = {}
        for nama_pasar_, item_ in pasar.semua_simbol(cfg, hanya):
            t = item_.get("tradingview")
            if t:
                peta_ticker[item_["simbol"]] = t
        if peta_ticker:
            bacaan = tv.baca(sorted(set(peta_ticker.values())))
            tv_nilai = {sim: bacaan.get(tick, {})
                        for sim, tick in peta_ticker.items()}
            terbaca = sum(1 for v in tv_nilai.values() if v)
            print(f"  TradingView: {terbaca}/{len(tv_nilai)} simbol terbaca")
    semua = []
    sisa: dict[str, int] = {}      # jatah pembukaan posisi tersisa hari ini

    # Batas rugi dan take profit tersentuh di tengah lilin, jadi tidak pernah
    # muncul sebagai sinyal. Diperiksa lebih dulu supaya posisi yang sudah
    # tertutup tidak menghalangi sinyal masuk yang baru.
    if lacak:
        semua += periksa_batas_tersentuh(conn, cfg, timeframe, nama_strategi)

    for nama_pasar, item in pasar.semua_simbol(cfg, hanya):
        # Batas hanya berlaku di pasar yang menyetelnya — futures. Crypto spot
        # dan forex tidak punya kunci ini, jadi tidak pernah terbatasi.
        batas = int(cfg.get(nama_pasar, {}).get("maks_trade_per_hari", 0) or 0)
        if batas > 0 and nama_pasar not in sisa:
            # Tanggal WIB — db.hitung_entry_hari_ini menghitung dengan
            # tanggal yang sama, apa pun zona waktu mesinnya.
            hari_ini = waktu.sekarang_wib().date().isoformat()
            sudah = db.hitung_entry_hari_ini(conn, nama_pasar, hari_ini)
            sisa[nama_pasar] = max(0, batas - sudah)
            if sisa[nama_pasar] == 0:
                print(f"  {nama_pasar}: batas {batas} pembukaan posisi per hari "
                      f"sudah tercapai — sinyal keluar tetap dikirim")

        for s in periksa(conn, cfg, nama_pasar, item, timeframe,
                         nama_strategi, lilin):
            masuk = s["aksi"] in ("BELI", "JUAL")
            arah = 1 if s["aksi"] in ("BELI", "TUTUP BELI") else -1

            # Posisi searah yang sedang terbuka, kalau ada. Dipakai dua kali
            # di bawah, jadi diambil sekali saja.
            pos = None
            if lacak:
                pos = db.posisi_terbuka(conn, nama_pasar, item["simbol"],
                                        timeframe, nama_strategi, arah)
                if masuk:
                    # Sinyal masuk TIDAK menunggu posisi sebelumnya ditutup.
                    # Peluang baru tetap dikabarkan walau masih ada posisi
                    # terbuka — Anda yang memutuskan menambah atau melewatkan.
                    #
                    # Setel tahan_sinyal_masuk: true untuk kembali ke perilaku
                    # lama, yang menahan sinyal masuk sampai posisi sebelumnya
                    # tertutup.
                    if tahan_masuk and pos is not None:
                        continue
                else:
                    # Sinyal keluar tetap hanya berarti kalau memang ada yang
                    # ditutup. Tanpa syarat ini Anda diberi tahu cara menutup
                    # posisi yang tidak pernah dibuka.
                    if pos is None:
                        continue

            # Sinyal keluar tidak pernah dibatasi jatah harian: posisi yang
            # sudah terbuka wajib punya jalan keluar.
            if masuk and batas > 0 and sisa.get(nama_pasar, 0) <= 0:
                continue

            # Sinyal MASUK yang sudah basi tidak dikirim.
            #
            # Ini bukan soal peluang terlewat, tapi soal bahaya: harga dan
            # batas rugi di pesan dihitung dari lilin saat sinyal muncul.
            # Kalau sinyalnya sudah lewat berjam-jam, harga sudah pindah
            # sementara angka batas ruginya tidak — mengeksekusinya berarti
            # masuk dengan stop yang salah. Diukur pada futures 15 menit,
            # 32% sinyal muncul antara 23:00-07:00 WIB, jadi keadaan ini
            # sering terjadi, bukan kasus langka.
            #
            # Sinyal KELUAR sengaja TIDAK dibatasi umurnya. Kabar bahwa
            # posisi perlu ditutup tetap berguna walau terlambat — justru
            # semakin penting kalau terlambat.
            if (masuk and maks_umur >= 0
                    and int(s.get("umur_lilin", 0)) > maks_umur):
                print(f"  {item['simbol']:<12} {s['aksi']:<11} dilewati — "
                      f"sinyal sudah {s['umur_lilin']} lilin yang lalu")
                continue

            # Konfirmasi TradingView — hanya untuk sinyal MASUK, dan hanya
            # kalau dinyalakan. Bacaan kosong dianggap setuju, supaya jaringan
            # yang bermasalah tidak diam-diam menghilangkan sinyal.
            if masuk and tv_cfg.get("aktif"):
                from . import tradingview as tv
                bacaan = tv_nilai.get(item["simbol"], {})
                if not tv.setuju(bacaan, arah, tv_ambang):
                    print(f"  {item['simbol']:<12} {s['aksi']:<11} ditahan — "
                          f"TradingView menunjuk arah "
                          f"{tv.arah(bacaan, tv_ambang):+d}")
                    continue

            baru = db.save_signal(
                conn, s["pasar"], s["simbol"], s["timeframe"], s["strategi"],
                s["ts"], s["aksi"], s["harga"], s["alasan"],
            )
            if not baru and hanya_baru:
                continue

            if lacak:
                if masuk:
                    peringatan = _peringatan_searah(
                        conn, cfg, nama_pasar, item["simbol"], timeframe,
                        nama_strategi, arah)
                    if peringatan:
                        s["peringatan_searah"] = peringatan
                    # Sinyalnya selalu dikabarkan, tapi yang DIPANTAU batas
                    # rugi dan take profit-nya tetap satu posisi per arah.
                    # Tanpa pembatasan ini, sepuluh sinyal beli berturut-turut
                    # akan melahirkan sepuluh catatan posisi, lalu satu
                    # sentuhan batas rugi mengirim sepuluh pesan yang sama.
                    if pos is None:
                        db.buka_posisi(conn, nama_pasar, item["simbol"],
                                       timeframe, nama_strategi, arah,
                                       s["ts"], s["harga"],
                                       s.get("saran_stop"), s.get("saran_tp"))
                    else:
                        s["alasan"] = (s["alasan"] + "  (posisi searah sudah "
                                       "terbuka sejak "
                                       f"{waktu.ke_wib(pos['ts_masuk'])})"
                                       ).strip()
                elif pos is not None:
                    db.tutup_posisi(conn, pos["id"], s["ts"], s["harga"],
                                    "sinyal keluar")
            if masuk and batas > 0:
                sisa[nama_pasar] -= 1

            s["baru"] = baru
            semua.append(s)
    return semua
