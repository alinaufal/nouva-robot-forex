"""Mengukur apakah tiap konfirmasi tambahan BENAR-BENAR mengurangi lose trade.

Dokumen "ringkasan_indikator_crypto.pdf" menyarankan beberapa konfirmasi di
luar indikator standar: volume saat menembus level, momentum MACD, dan gerbang
imbalan:risiko minimal 1:2. Skrip ini menyalakannya satu per satu pada data
Anda sendiri lalu menyandingkan hasilnya.

Yang diukur SENGAJA dua hal sekaligus, bukan satu:

    win%    berapa persen transaksi yang untung  <- yang ingin dinaikkan
    hasil   berapa persen modal bertambah        <- yang sebenarnya dibayar

Keduanya bisa bergerak berlawanan, dan di project ini sudah terbukti begitu:
take profit 1:1 dulu menaikkan win rate 42% -> 61% sambil memotong hasil
14,4% -> 6,9%. Penyaring membuang transaksi yang rugi, tapi ia juga membuang
yang untung. Menilai penyaring dari win rate saja adalah cara paling mudah
untuk merasa menang sambil kehilangan uang.

ATURAN PENERIMAAN — ditetapkan sebelum angkanya dilihat:

    DITERIMA  win% naik DI KEDUA PERIODE, dan hasil tidak lebih buruk
    PILIHAN   win% naik di kedua periode, tapi hasilnya berkurang
    ditolak   selebihnya (termasuk yang hanya menang di satu periode)

Menang di satu periode saja itu kebetulan, bukan keunggulan.

    python uji_konfirmasi.py
    python uji_konfirmasi.py --pasar futures --timeframe 15menit
"""
import argparse
import sys
import warnings

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
warnings.filterwarnings("ignore")

from src import backtest, db, pasar, strategy              # noqa: E402
from src.config import db_path, load_config, parameter     # noqa: E402

# Penyaring diuji satu per satu lebih dulu, baru digabung. Tanpa urutan itu,
# gabungan yang membaik tidak bisa ditelusuri berasal dari penyaring yang mana.
VARIAN = [
    ("tanpa penyaring", {}),
    ("volume 1,5x", {"volume": {"aktif": True, "periode": 20, "pengali": 1.5}}),
    ("volume 2,0x", {"volume": {"aktif": True, "periode": 20, "pengali": 2.0}}),
    ("MACD", {"macd": {"aktif": True}}),
    ("R:R >= 1:2", {"rr_minimal": 2.0}),
    ("R:R >= 1:3", {"rr_minimal": 3.0}),
    ("jeda 1 lilin", {"tunda_lilin": 1}),
    ("volume + MACD", {"volume": {"aktif": True, "periode": 20, "pengali": 1.5},
                       "macd": {"aktif": True}}),
    ("volume + R:R", {"volume": {"aktif": True, "periode": 20, "pengali": 1.5},
                      "rr_minimal": 2.0}),
    ("semua sekaligus", {"volume": {"aktif": True, "periode": 20,
                                    "pengali": 1.5},
                         "macd": {"aktif": True}, "rr_minimal": 2.0}),
]


class Hasil:
    """Ringkasan satu strategi pada satu periode dengan satu penyaring."""

    def __init__(self, trx, imbal, dd, tahun):
        self.n = len(trx)
        menang = [t for t in trx if t.laba > 0]
        rugi = [t for t in trx if t.laba <= 0]
        self.win = len(menang) / self.n * 100 if self.n else 0.0
        self.rugi_n = len(rugi)
        untung_total = sum(t.laba for t in menang)
        rugi_total = abs(sum(t.laba for t in rugi))
        self.pf = untung_total / rugi_total if rugi_total > 0 else float("inf")
        self.harapan = (sum(t.laba for t in trx) / self.n) if self.n else 0.0
        self.imbal = imbal
        self.dd = dd
        self.cagr = (((1 + imbal / 100) ** (1 / tahun) - 1) * 100
                     if tahun > 0 and imbal > -100 else 0.0)


def ukur(conn, cfg, nama_pasar, timeframe, st, konf, mulai, sampai):
    """Jalankan backtest satu strategi di seluruh watchlist satu periode."""
    bt = cfg["backtest"]
    trx, akhir, dd, thn = [], [], [], []
    for item in pasar.daftar(cfg, nama_pasar):
        p = dict(parameter(cfg, st, timeframe))
        p["konfirmasi"] = konf          # menimpa setelan config untuk uji ini
        df = pasar.ambil(conn, nama_pasar, item["simbol"], timeframe,
                         mulai, sampai)
        if df.empty or len(df) < strategy.masa_pemanasan(p, st) + 30:
            continue
        dana = (db.ambil_funding(conn, item["simbol"], mulai, sampai)
                if nama_pasar == "futures" else None)
        h = backtest.jalankan(
            df, p, st, pasar.biaya(cfg, nama_pasar, item),
            modal=float(bt["modal"]),
            risiko_persen=float(bt["risiko_per_transaksi_persen"]),
            dua_arah=pasar.dua_arah(cfg, nama_pasar),
            simbol=item["simbol"],
            leverage=pasar.leverage(cfg, nama_pasar),
            funding=dana,
            maintenance=pasar.maintenance(cfg, nama_pasar),
            tp_rasio=float(p.get("tp_rasio", 0) or 0),
            maks_per_hari=int(cfg.get(nama_pasar, {})
                              .get("maks_trade_per_hari", 0) or 0),
        )
        trx += h.transaksi
        akhir.append(h.total_return)
        dd.append(h.max_drawdown)
        thn.append(h.tahun)
    if not akhir:
        return None
    return Hasil(trx, sum(akhir) / len(akhir), min(dd), sum(thn) / len(thn))


MIN_TRANSAKSI = 20


def vonis(dasar_lama, dasar_baru, uji_lama, uji_baru):
    """Terapkan aturan penerimaan yang sudah ditulis di atas.

    Dua pengaman di sini ada karena percobaan pertama menghasilkan label yang
    menyesatkan, dan keduanya membuat aturannya lebih KETAT, bukan lebih longgar:

    * **Jumlah transaksi minimal.** Sebuah penyaring yang menyisakan satu
      transaksi lalu kebetulan menang tampil sebagai "win rate 100%". Itu bukan
      keunggulan, itu sampel yang terlalu kecil untuk berarti apa pun.
    * **Hasilnya harus positif.** Tanpa syarat ini, penyaring yang membuat
      strategi rugi menjadi kurang rugi ikut berlabel DITERIMA — padahal
      memakainya tetap berarti kehilangan uang, hanya lebih pelan.
    """
    if not all((dasar_lama, dasar_baru, uji_lama, uji_baru)):
        return "data kurang"
    if uji_lama.n < MIN_TRANSAKSI or uji_baru.n < MIN_TRANSAKSI:
        return f"sampel terlalu kecil ({uji_lama.n}/{uji_baru.n} transaksi)"

    win_naik = (uji_lama.win > dasar_lama.win and uji_baru.win > dasar_baru.win)
    if not win_naik:
        return "ditolak"

    hasil_terjaga = (uji_lama.imbal >= dasar_lama.imbal
                     and uji_baru.imbal >= dasar_baru.imbal)
    if not hasil_terjaga:
        return "PILIHAN"
    # Kurang rugi tetaplah rugi. Strategi yang merugi di kedua periode tidak
    # bisa diselamatkan oleh penyaring apa pun, dan menyebutnya DITERIMA
    # membuat orang memakainya untuk uang sungguhan.
    if uji_lama.imbal <= 0 or uji_baru.imbal <= 0:
        return "tetap rugi"
    return "DITERIMA"


def main() -> int:
    cfg = load_config()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--pasar", default="crypto",
                    choices=["crypto", "forex", "futures"])
    ap.add_argument("--timeframe", default="harian",
                    choices=["harian", "4jam", "1jam", "30menit",
                             "15menit", "5menit"])
    a = ap.parse_args()

    pisah = str(cfg["backtest"]["tanggal_pisah"])
    conn = db.connect(db_path(cfg))
    if not pasar.daftar(cfg, a.pasar):
        print(f"Pasar {a.pasar} tidak aktif di config.yaml")
        return 1

    print("=" * 104)
    print(f"  PENGARUH KONFIRMASI TAMBAHAN — {a.pasar}, timeframe {a.timeframe}")
    print(f"  Periode dipisah di {pisah}. Penyaring harus menang di KEDUANYA.")
    print("=" * 104)

    ringkasan = []
    for st in strategy.NAMA:
        print(f"\n  {strategy.NAMA[st]}")
        print(f"    {'penyaring':<18} {'periode':<6} {'trx':>5} {'rugi':>5} "
              f"{'win%':>6} {'PF':>6} {'hasil':>9} {'per thn':>9} "
              f"{'drawdown':>10}")
        print("    " + "-" * 94)

        dasar = {}
        for nama, konf in VARIAN:
            hasil_periode = {}
            for label, mulai, sampai in (("lama", None, pisah),
                                         ("baru", pisah, None)):
                h = ukur(conn, cfg, a.pasar, a.timeframe, st, konf,
                         mulai, sampai)
                hasil_periode[label] = h
                if h is None:
                    continue
                pf = "  inf" if h.pf == float("inf") else f"{h.pf:>6.2f}"
                print(f"    {nama:<18} {label:<6} {h.n:>5} {h.rugi_n:>5} "
                      f"{h.win:>5.0f}% {pf} {h.imbal:>8.1f}% "
                      f"{h.cagr:>8.1f}% {h.dd:>9.1f}%")
            if nama == "tanpa penyaring":
                dasar = hasil_periode
            else:
                ringkasan.append((st, nama,
                                  vonis(dasar.get("lama"), dasar.get("baru"),
                                        hasil_periode.get("lama"),
                                        hasil_periode.get("baru"))))
        print()

    conn.close()

    print("=" * 104)
    print("  VONIS menurut aturan yang ditetapkan sebelum angkanya dilihat")
    print("=" * 104)
    diterima = [r for r in ringkasan if r[2] == "DITERIMA"]
    pilihan = [r for r in ringkasan if r[2] == "PILIHAN"]
    tetap_rugi = [r for r in ringkasan if r[2] == "tetap rugi"]
    for st, nama, v in diterima:
        print(f"    DITERIMA    {strategy.NAMA[st]:<44} {nama}")
    for st, nama, v in pilihan:
        print(f"    PILIHAN     {strategy.NAMA[st]:<44} {nama}")
    for st, nama, v in tetap_rugi:
        print(f"    tetap rugi  {strategy.NAMA[st]:<44} {nama}")
    if not diterima and not pilihan:
        print("    Tidak ada penyaring yang menaikkan win rate di KEDUA periode.")
        print("    Itu jawaban yang sah: konfirmasi tambahan tidak membantu di")
        print("    data ini, dan memasangnya hanya akan mengurangi sinyal tanpa")
        print("    memperbaiki apa pun.")
    print()
    print("  DITERIMA   = win rate naik di dua periode, hasil tidak turun,")
    print("               dan hasilnya positif — layak dipertimbangkan.")
    print("  PILIHAN    = win rate naik di dua periode, TAPI hasilnya berkurang.")
    print("               Lebih jarang rugi, tapi uangnya juga lebih sedikit.")
    print("               Keputusannya milik Anda, bukan milik skrip ini.")
    print("  tetap rugi = penyaringnya memang memperbaiki, tapi strateginya")
    print("               masih merugi di kedua periode. Kurang rugi tetap rugi.")
    print(f"  Sampel di bawah {MIN_TRANSAKSI} transaksi tidak pernah diberi vonis:")
    print("               satu transaksi yang kebetulan menang terbaca 'win 100%'.")
    print("=" * 104)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
