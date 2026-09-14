"""Perbarui harga -> cari sinyal -> kirim notifikasi.

Dijalankan lewat "Jalankan Robot.bat", lewat Task Scheduler, atau langsung:

    python run_cek.py
    python run_cek.py --hanya BTCUSDT
    python run_cek.py --timeframe 4jam --strategi tembus_batas
    python run_cek.py --tanpa-kirim      # tampilkan saja, jangan kirim
"""
import argparse
import json
import sys
import warnings
from pathlib import Path

# Konsol Windows bawaannya bukan UTF-8, sehingga karakter seperti "•" atau "—"
# tampil rusak. Baris ini membuat tampilannya benar tanpa mengubah isi teks.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
warnings.filterwarnings("ignore")

from src import db, notify, pasar, signals, strategy, waktu   # noqa: E402
from src.config import db_path, load_config                    # noqa: E402


def baca_status_data(path: Path) -> set[str]:
    """Data yang bermasalah pada kabar terakhir yang berhasil dikirim."""
    try:
        return set(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def simpan_status_data(path: Path, kunci: set[str]) -> None:
    path.write_text(json.dumps(sorted(kunci)), encoding="utf-8")


def main() -> int:
    cfg = load_config()

    ap = argparse.ArgumentParser(description="Robot analisis crypto & forex")
    ap.add_argument("--timeframe", default=cfg["timeframe"],
                    choices=["harian", "4jam", "1jam", "30menit", "15menit", "5menit"])
    ap.add_argument("--strategi", default=cfg["strategi_aktif"],
                    choices=list(strategy.NAMA))
    ap.add_argument("--hanya", default=None,
                    help="periksa satu simbol saja, mis. BTCUSDT")
    ap.add_argument("--tanpa-kirim", action="store_true",
                    dest="tanpa_kirim",
                    help="tampilkan hasilnya saja, jangan kirim ke Telegram")
    ap.add_argument("--penuh", action="store_true",
                    help="unduh ulang seluruh riwayat, bukan hanya yang baru")
    ap.add_argument("--diam-jika-kosong", action="store_true",
                    dest="diam_jika_kosong",
                    help="jangan kirim apa pun kalau tidak ada sinyal, "
                         "walau config menyetel kirim_walau_kosong: true")
    a = ap.parse_args()

    judul = cfg.get("notifikasi", {}).get("judul", "Sinyal Pasar")

    # Selalu WIB, tidak bergantung zona waktu mesin yang menjalankannya.
    sekarang = waktu.sekarang_wib()
    tanggal = f"{sekarang:%d %B %Y %H:%M} WIB"
    print(f"=== {judul} — {tanggal} ===")
    print(f"    timeframe {a.timeframe} · strategi {strategy.NAMA[a.strategi]}\n")

    conn = db.connect(db_path(cfg))
    try:
        print("[1/3] Memperbarui harga")
        hasil_unduh, gagal = pasar.perbarui_semua(conn, cfg, a.timeframe,
                                                  hanya=a.hanya, penuh=a.penuh)
        if not hasil_unduh:
            print("  tidak ada simbol yang cocok — periksa config.yaml")
            return 1

        # Data bermasalah: gagal diunduh, ATAU tidak gagal tapi lilin
        # terakhirnya sudah terlalu tua. Keterangan kegagalan lebih spesifik,
        # jadi yang itu yang dipakai kalau keduanya ada.
        masalah = pasar.data_tertinggal(conn, cfg, a.timeframe, hanya=a.hanya)
        masalah.update(gagal)
        for kunci, sebab in masalah.items():
            print(f"  ! {kunci}: {sebab}")

        # Status disimpan per timeframe supaya jadwal 15 menit dan laporan
        # harian tidak saling menimpa. Saat --hanya dipakai (jalan manual),
        # status tidak disentuh: daftar simbolnya tidak lengkap, sehingga
        # simbol lain akan keliru dianggap sudah pulih.
        berkas_status = db_path(cfg).parent / f"status-data-{a.timeframe}.json"
        pakai_status = not a.hanya
        sebelumnya = baca_status_data(berkas_status) if pakai_status else set()
        kini = set(masalah)
        pulih = sorted(sebelumnya - kini)
        berubah = bool(kini - sebelumnya or pulih)

        print("\n[2/3] Mencari sinyal")
        temuan = signals.periksa_semua(conn, cfg, a.timeframe, a.strategi,
                                       hanya=a.hanya)
        if temuan:
            for s in temuan:
                baris = (f"  {s['aksi']:<11} {s['simbol']:<12} "
                         f"{waktu.ke_wib(s['ts'])}  {s['harga']:,.4f}")
                if s.get("saran_stop"):
                    baris += f"   batas rugi {s['saran_stop']:,.4f}"
                print(baris)
        else:
            print("  tidak ada sinyal baru")

        print("\n[3/3] Mengirim notifikasi")
        # Jadwal yang sering (mis. tiap 15 menit) memakai --diam-jika-kosong
        # supaya tidak membanjiri Telegram dengan pesan "tidak ada sinyal".
        # Jadwal harian tidak memakainya, sehingga laporan pagi tetap datang
        # tiap hari sebagai tanda robot masih hidup.
        #
        # Kabar data tetap boleh dikirim walau tidak ada sinyal, tapi di jadwal
        # yang diam hanya saat keadaannya BERUBAH — gangguan satu jam di jadwal
        # 15 menit tidak boleh jadi empat pesan yang sama.
        boleh_kosong = (cfg["notifikasi"]["kirim_walau_kosong"]
                        and not a.diam_jika_kosong)
        kabar_data = berubah if pakai_status else bool(masalah)
        if not temuan and not boleh_kosong and not kabar_data:
            print("  dilewati — tidak ada yang perlu dikabarkan")
            return 0

        pesan = notify.susun_pesan(
            temuan, tanggal, a.timeframe, strategy.NAMA[a.strategi], judul,
            masalah=masalah, pulih=pulih,
        )
        if a.tanpa_kirim:
            # Status sengaja tidak disimpan: uji coba tidak boleh "memakai"
            # perubahan keadaan yang seharusnya dikabarkan jadwal sungguhan.
            print("  --tanpa-kirim aktif, isi pesannya:\n")
            print(pesan)
        elif notify.kirim(pesan):
            print("  terkirim ke Telegram")
            if pakai_status:
                simpan_status_data(berkas_status, kini)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
