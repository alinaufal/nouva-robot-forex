"""Mengukur pengaruh take profit pada data Anda sendiri.

Take profit terdengar seperti pengaman, dan memang menaikkan win rate hampir
selalu. Pertanyaannya bukan itu, melainkan: apakah ia menaikkan HASIL?

Ketiga strategi di project ini keluar lewat sinyal, bukan lewat target harga.
Sebagian besar keuntungannya datang dari sedikit posisi yang ditahan lama.
Take profit memotong posisi-posisi itu lebih awal — dan yang terpotong justru
yang paling menguntungkan.

Skrip ini menguji apakah dugaan itu benar untuk data Anda, atau tidak.

    python uji_take_profit.py
    python uji_take_profit.py --pasar crypto --timeframe 4jam
"""
import argparse
import sys
import warnings

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
warnings.filterwarnings("ignore")

from src import backtest, db, pasar, strategy              # noqa: E402
from src.config import db_path, load_config, parameter     # noqa: E402

RASIO = [0, 1.0, 1.5, 2.0, 3.0, 5.0]


def main() -> int:
    cfg = load_config()
    ap = argparse.ArgumentParser()
    ap.add_argument("--pasar", default="crypto",
                    choices=["crypto", "forex", "futures"])
    ap.add_argument("--timeframe", default="harian",
                    choices=["harian", "4jam", "1jam", "30menit",
                             "15menit", "5menit"])
    a = ap.parse_args()

    pisah = str(cfg["backtest"]["tanggal_pisah"])
    bt = cfg["backtest"]
    conn = db.connect(db_path(cfg))
    daftar = pasar.daftar(cfg, a.pasar)
    if not daftar:
        print(f"Pasar {a.pasar} tidak aktif di config.yaml")
        return 1

    print("=" * 96)
    print(f"  PENGARUH TAKE PROFIT — {a.pasar}, timeframe {a.timeframe}")
    print(f"  Angka 0 = tanpa take profit. Sisanya kelipatan jarak batas rugi.")
    print("=" * 96)

    for st in strategi_urut(cfg):
        print(f"\n  {strategy.NAMA[st]}")
        print(f"    {'TP':>5} {'periode':<7} {'trx':>5} {'win%':>6} "
              f"{'kena TP':>8} {'kena stop':>10} {'sinyal':>7} "
              f"{'hasil':>9} {'per thn':>9} {'drawdown':>10}")
        print("    " + "-" * 86)
        for rasio in RASIO:
            for label, mulai, sampai in [("lama", None, pisah),
                                         ("baru", pisah, None)]:
                trx, akhir, dd, thn = [], [], [], []
                for item in daftar:
                    p = dict(parameter(cfg, st, a.timeframe))
                    df = pasar.ambil(conn, a.pasar, item["simbol"],
                                     a.timeframe, mulai, sampai)
                    if df.empty or len(df) < strategy.masa_pemanasan(p, st) + 30:
                        continue
                    dana = (db.ambil_funding(conn, item["simbol"], mulai, sampai)
                            if a.pasar == "futures" else None)
                    h = backtest.jalankan(
                        df, p, st, pasar.biaya(cfg, a.pasar, item),
                        modal=float(bt["modal"]),
                        risiko_persen=float(bt["risiko_per_transaksi_persen"]),
                        dua_arah=pasar.dua_arah(cfg, a.pasar),
                        simbol=item["simbol"],
                        leverage=pasar.leverage(cfg, a.pasar),
                        funding=dana,
                        maintenance=pasar.maintenance(cfg, a.pasar),
                        tp_rasio=rasio,
                    )
                    trx += h.transaksi
                    akhir.append(h.total_return)
                    dd.append(h.max_drawdown)
                    thn.append(h.tahun)
                if not akhir:
                    continue
                n = len(trx)
                tp = sum(1 for t in trx if t.alasan_keluar == "kena take profit")
                stp = sum(1 for t in trx if t.alasan_keluar == "kena batas rugi")
                sgn = sum(1 for t in trx if t.alasan_keluar == "sinyal keluar")
                menang = sum(1 for t in trx if t.laba > 0)
                imbal = sum(akhir) / len(akhir)
                tahun = sum(thn) / len(thn)
                cagr = (((1 + imbal / 100) ** (1 / tahun) - 1) * 100
                        if tahun > 0 and imbal > -100 else 0.0)
                nama_r = "tanpa" if rasio == 0 else f"1:{rasio:g}"
                print(f"    {nama_r:>5} {label:<7} {n:>5} "
                      f"{menang/max(1,n)*100:>5.0f}% {tp:>8} {stp:>10} "
                      f"{sgn:>7} {imbal:>8.1f}% {cagr:>8.1f}% {min(dd):>9.1f}%")

    conn.close()
    print("\n" + "=" * 96)
    print("  Baca kolom 'win%' dan 'hasil' bersamaan. Take profit hampir selalu")
    print("  menaikkan win rate — pertanyaannya apakah hasilnya ikut naik.")
    print("=" * 96)
    return 0


def strategi_urut(cfg):
    return list(strategy.NAMA)


if __name__ == "__main__":
    raise SystemExit(main())
