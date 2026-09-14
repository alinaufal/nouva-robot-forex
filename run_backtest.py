"""Adu tiga strategi pada data historis, di dua periode terpisah.

Kenapa dua periode? Karena menguji 3 strategi x 2 timeframe x 2 pasar berarti
12 percobaan, dan memilih yang terbaik dari 12 percobaan hampir pasti
menemukan kebetulan. Aturannya ditetapkan SEBELUM hasilnya dilihat:

    Strategi hanya layak dipakai kalau menang di KEDUA periode.
    Menang di periode lama tapi kalah di periode baru = ditolak.

Patokan menangnya berbeda per pasar, dan itu disengaja:
  * crypto — harus mengalahkan sekadar membeli lalu menahan
  * forex  — harus mengalahkan 0%, karena mata uang tidak "tumbuh" seperti
             aset produktif sehingga beli-dan-tahan bukan patokan yang wajar

Pemakaian:
    python run_backtest.py
    python run_backtest.py --timeframe 4jam
    python run_backtest.py --rinci
"""
import argparse
import sys
import warnings

import pandas as pd

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
warnings.filterwarnings("ignore")

from src import backtest, db, pasar, strategy            # noqa: E402
from src.config import db_path, load_config, parameter   # noqa: E402


def tahan_rinci(df, biaya, modal: float) -> dict:
    """Beli-dan-tahan lengkap dengan penurunan terdalamnya.

    Drawdown-nya ikut dihitung supaya perbandingannya jujur: strategi yang
    kalah dalam hal keuntungan bisa saja jauh lebih tenang perjalanannya, dan
    itu tidak terlihat kalau yang dibandingkan hanya angka akhir.
    """
    akhir = backtest.beli_dan_tahan(df, biaya, modal)
    harga_awal = biaya.saat_membeli(float(df["close"].iloc[0]))
    unit = modal / harga_awal if harga_awal > 0 else 0.0
    kurva = df["close"] * unit
    puncak = kurva.cummax()
    dd = float(((kurva - puncak) / puncak * 100).min())

    tahun = (df.index[-1] - df.index[0]).days / 365.25
    imbal = (akhir - modal) / modal * 100
    cagr = (((akhir / modal) ** (1 / tahun) - 1) * 100
            if tahun > 0 and akhir > 0 else 0.0)
    return {"return": imbal, "cagr": cagr, "drawdown": dd, "tahun": tahun}


def jalankan_satu(conn, cfg, nama_pasar, item, timeframe, nama_strategi,
                  mulai, sampai):
    """Backtest satu simbol pada satu potongan waktu."""
    # `item` diteruskan supaya setelan per simbol (jam sesi) ikut dipakai saat
    # backtest — kalau tidak, yang diuji bukan yang dijalankan.
    p = parameter(cfg, nama_strategi, timeframe, item)
    df = pasar.ambil(conn, nama_pasar, item["simbol"], timeframe, mulai, sampai)

    perlu = strategy.masa_pemanasan(p, nama_strategi) + 30
    if df.empty or len(df) < perlu:
        return None, None

    b = pasar.biaya(cfg, nama_pasar, item)
    bt = cfg["backtest"]

    # Futures membawa dua hal yang tidak ada di pasar lain: pinjaman
    # (leverage) dan biaya menginap (funding). Keduanya ikut dihitung supaya
    # angkanya tidak lebih indah daripada kenyataan.
    lev = pasar.leverage(cfg, nama_pasar)
    dana = None
    if nama_pasar == "futures":
        dana = db.ambil_funding(conn, item["simbol"], mulai, sampai)

    # Futures bisa diuji dengan modal nyata dan aturan order minimum bursa.
    # Pasar lain tetap memakai modal besar yang membuat order minimum tidak
    # pernah berpengaruh — perilakunya sama persis seperti dulu.
    modal = pasar.modal_backtest(cfg, nama_pasar)
    hasil = backtest.jalankan(
        df, p, nama_strategi, b,
        modal=modal,
        risiko_persen=float(bt["risiko_per_transaksi_persen"]),
        dua_arah=pasar.dua_arah(cfg, nama_pasar),
        simbol=item["simbol"],
        leverage=lev,
        funding=dana,
        maintenance=pasar.maintenance(cfg, nama_pasar),
        tp_rasio=float(p.get("tp_rasio", 0) or 0),
        maks_per_hari=int(cfg.get(nama_pasar, {})
                          .get("maks_trade_per_hari", 0) or 0),
        batas_order=pasar.batas_order(cfg, nama_pasar, item["simbol"]),
    )
    return hasil, tahan_rinci(df, b, modal)


def ringkas(baris: list[dict]) -> dict:
    """Rata-ratakan hasil beberapa simbol menjadi satu angka per strategi."""
    if not baris:
        return {}
    n = len(baris)
    total_transaksi = sum(b["transaksi"] for b in baris)
    return {
        "simbol": n,
        "return": sum(b["return"] for b in baris) / n,
        "cagr": sum(b["cagr"] for b in baris) / n,
        "tahan": sum(b["tahan"] for b in baris) / n,
        "tahan_cagr": sum(b["tahan_cagr"] for b in baris) / n,
        "tahan_dd": min(b["tahan_dd"] for b in baris),
        "transaksi": total_transaksi,
        "win_rate": (sum(b["win_rate"] * b["transaksi"] for b in baris)
                     / max(1, total_transaksi)),
        "drawdown": min(b["drawdown"] for b in baris),
        "tahun": sum(b["tahun"] for b in baris) / n,
        "menang_vs_tahan": sum(1 for b in baris if b["return"] > b["tahan"]),
        "likuidasi": sum(b.get("likuidasi", 0) for b in baris),
        "ditolak_bursa": sum(b.get("ditolak_bursa", 0) for b in baris),
    }


def main() -> int:
    cfg = load_config()

    ap = argparse.ArgumentParser()
    ap.add_argument("--timeframe", default=cfg["timeframe"],
                    choices=["harian", "4jam", "1jam", "30menit", "15menit", "5menit"])
    ap.add_argument("--rinci", action="store_true",
                    help="tampilkan hasil tiap simbol, bukan hanya rata-rata")
    a = ap.parse_args()

    pisah = str(cfg["backtest"]["tanggal_pisah"])
    periode = [("lama", None, pisah), ("baru", pisah, None)]

    print("=" * 74)
    print(f"  ADU STRATEGI — timeframe {a.timeframe}")
    print(f"  periode lama: sampai {pisah}   |   periode baru: sejak {pisah}")
    print("=" * 74)

    conn = db.connect(db_path(cfg))
    kumpulan: dict = {}

    try:
        for nama_pasar in pasar.PASAR:
            daftar = pasar.daftar(cfg, nama_pasar)
            if not daftar:
                continue

            print(f"\n\n{'=' * 74}\n  {nama_pasar.upper()}"
                  f"{'  (bisa beli dan jual)' if pasar.dua_arah(cfg, nama_pasar) else '  (hanya beli)'}"
                  f"\n{'=' * 74}")
            if cfg.get(nama_pasar, {}).get("batas_order"):
                print(f"  modal {pasar.modal_backtest(cfg, nama_pasar):,.0f} "
                      f"per simbol · order di bawah batas minimum bursa "
                      f"ditolak, persis seperti di Binance")

            for label, mulai, sampai in periode:
                print(f"\n  --- periode {label} ---")
                print(f"  {'strategi':<15} {'total':>8} {'per thn':>8} "
                      f"{'drawdown':>9} {'transaksi':>10} {'win%':>6} {'unggul':>7}")
                print("  " + "-" * 70)

                acuan = None
                for nama_strategi in strategy.NAMA:
                    # Tidak setiap strategi diatur untuk setiap timeframe.
                    # Dilewati dengan keterangan yang jujur — bukan dibiarkan
                    # mati dengan KeyError, dan bukan pula dilaporkan sebagai
                    # "data tidak cukup" yang artinya berbeda sama sekali.
                    if a.timeframe not in cfg["strategi"].get(nama_strategi, {}):
                        print(f"  {nama_strategi:<15} parameter timeframe "
                              f"{a.timeframe} belum diatur di config — dilewati")
                        continue
                    baris = []
                    for item in daftar:
                        hasil, tahan = jalankan_satu(
                            conn, cfg, nama_pasar, item, a.timeframe,
                            nama_strategi, mulai, sampai)
                        if hasil is None:
                            continue
                        baris.append({
                            "simbol": item["simbol"],
                            "return": hasil.total_return,
                            "cagr": hasil.cagr,
                            "tahan": tahan["return"],
                            "tahan_cagr": tahan["cagr"],
                            "tahan_dd": tahan["drawdown"],
                            "tahun": tahan["tahun"],
                            "transaksi": hasil.jumlah_transaksi,
                            "win_rate": hasil.win_rate,
                            "drawdown": hasil.max_drawdown,
                            "likuidasi": sum(
                                1 for t in hasil.transaksi
                                if t.alasan_keluar == "LIKUIDASI"),
                            "ditolak_bursa": hasil.ditolak_bursa,
                        })

                    r = ringkas(baris)
                    if not r:
                        print(f"  {nama_strategi:<15} data tidak cukup")
                        continue

                    kumpulan[(nama_pasar, label, nama_strategi)] = r
                    acuan = r
                    tanda = (f"  <-- {r['likuidasi']} LIKUIDASI"
                             if r.get("likuidasi") else "")
                    if r.get("ditolak_bursa"):
                        tanda += (f"  <-- {r['ditolak_bursa']} ditolak bursa "
                                  f"(di bawah order minimum)")
                    print(f"  {nama_strategi:<15} {r['return']:>7.1f}% "
                          f"{r['cagr']:>7.1f}% {r['drawdown']:>8.1f}% "
                          f"{r['transaksi']:>10} {r['win_rate']:>5.0f}% "
                          f"{r['menang_vs_tahan']}/{r['simbol']:>1}{tanda}")

                    if a.rinci:
                        for b in baris:
                            print(f"      {b['simbol']:<12} {b['return']:>7.1f}% "
                                  f"(tahan {b['tahan']:>8.1f}%)  "
                                  f"{b['transaksi']:>3} transaksi")

                if acuan:
                    print("  " + "-" * 70)
                    print(f"  {'beli & tahan':<15} {acuan['tahan']:>7.1f}% "
                          f"{acuan['tahan_cagr']:>7.1f}% {acuan['tahan_dd']:>8.1f}%"
                          f"{'':>10} {'':>6}   (pembanding)")
                    print(f"  panjang periode {acuan['tahun']:.1f} tahun")

        # ------------------------------------------------------- kesimpulan
        print(f"\n\n{'=' * 74}\n  PENERAPAN ATURAN PENERIMAAN\n{'=' * 74}")
        print("  Aturan ini ditetapkan sebelum hasilnya dilihat:")
        print("    crypto -> harus mengalahkan beli-dan-tahan di KEDUA periode")
        print("    forex  -> harus mengalahkan 0% di KEDUA periode\n")

        lolos_semua = []
        for nama_pasar in pasar.PASAR:
            if not pasar.daftar(cfg, nama_pasar):
                continue
            print(f"  {nama_pasar.upper()}")
            for nama_strategi in strategy.NAMA:
                lama = kumpulan.get((nama_pasar, "lama", nama_strategi))
                baru = kumpulan.get((nama_pasar, "baru", nama_strategi))
                if not lama or not baru:
                    print(f"    {nama_strategi:<16} data tidak cukup")
                    continue

                if nama_pasar in ("crypto", "futures"):
                    # Futures diukur dengan patokan yang sama seperti spot:
                    # kalau tidak bisa mengalahkan sekadar memegang asetnya,
                    # tidak ada alasan menanggung risiko likuidasi.
                    ok_lama = lama["return"] > lama["tahan"]
                    ok_baru = baru["return"] > baru["tahan"]
                else:
                    ok_lama = lama["return"] > 0
                    ok_baru = baru["return"] > 0

                if lama.get("likuidasi", 0) or baru.get("likuidasi", 0):
                    ok_lama = ok_baru = False   # kena likuidasi = gugur

                tanda = "LOLOS" if (ok_lama and ok_baru) else "ditolak"
                sebab = ""
                if not ok_lama and not ok_baru:
                    sebab = "kalah di kedua periode"
                elif not ok_baru:
                    sebab = "menang di periode lama, KALAH di periode baru"
                elif not ok_lama:
                    sebab = "kalah di periode lama"
                print(f"    {nama_strategi:<16} {tanda:<8} "
                      f"lama {lama['cagr']:>6.2f}%/thn  "
                      f"baru {baru['cagr']:>6.2f}%/thn   {sebab}")
                if ok_lama and ok_baru:
                    lolos_semua.append((nama_pasar, nama_strategi, lama, baru))
            print()

        print("=" * 74)
        if lolos_semua:
            print("  Lolos aturan penerimaan:")
            for pas, st, lama, baru in lolos_semua:
                print(f"    - {st} di {pas}: "
                      f"{lama['cagr']:.2f}%/tahun lalu {baru['cagr']:.2f}%/tahun")
            print()
            print("  TAPI PERIKSA BESARNYA, bukan hanya lolos atau tidak.")
            print("  Deposito rupiah memberi sekitar 4-6% per tahun tanpa risiko")
            print("  dan tanpa usaha. Strategi yang lolos aturan tapi hanya")
            print("  menghasilkan di bawah itu tidak layak dipakai untuk uang")
            print("  sungguhan — lolosnya benar secara statistik, tapi tidak")
            print("  berarti apa-apa secara ekonomi.")
        else:
            print("  TIDAK ADA satu pun strategi yang lolos aturan.")
            print("  Artinya: jangan pakai untuk uang sungguhan.")

        print()
        print("  Catatan cara membaca tabel di atas:")
        print("  * Strategi hanya mempertaruhkan "
              f"{cfg['backtest']['risiko_per_transaksi_persen']}% modal per "
              "transaksi,")
        print("    sedangkan beli-dan-tahan memakai 100% modal sepanjang waktu.")
        print("    Jadi wajar kalau keuntungannya jauh lebih kecil — bandingkan")
        print("    juga kolom drawdown, di situlah bedanya terlihat.")
        print("  * Sebagian periode terpakai untuk pemanasan indikator")
        print("    (SMA200 perlu 200 lilin sebelum sinyal pertama bisa muncul).")
        print("=" * 74)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
