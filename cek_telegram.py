"""Periksa bot Telegram milik robot INI — dan buktikan pesannya sampai.

Dijalankan dari folder robot mana pun. Ia membaca `.env` milik foldernya
sendiri, jadi menjalankannya di ~/robot-forex memeriksa bot forex, bukan bot
crypto.

    python cek_telegram.py              # kirim pesan uji ke semua tujuan
    python cek_telegram.py --tanpa-kirim  # periksa saja, jangan kirim apa pun

Alasan alat ini ada
-------------------
Bot Telegram TIDAK BISA mengirim pesan ke orang yang belum pernah menekan
`/start` di bot itu. Telegram menolaknya dengan kode 403, dan robot hanya
mencatatnya di log — jadi orang itu diam-diam tidak menerima apa pun, dan
tidak ada yang tahu sampai ada yang membuka log.

Setiap kali Anda membuat bot baru, SETIAP penerima harus menekan Start di bot
itu. Alat ini yang memastikan hal tersebut benar-benar sudah dilakukan.

Tokennya sendiri tidak pernah dicetak.
"""
import argparse
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import requests                                          # noqa: E402

from src.config import load_env                          # noqa: E402

try:
    # Dipakai supaya pemisahan tujuan persis sama dengan saat mengirim
    # sungguhan. Kalau robot ini kodebasenya lebih tua dan belum punya
    # fungsi itu, dipakai pemisah sederhana yang setara.
    from src.notify import _daftar_tujuan
except ImportError:                                      # pragma: no cover
    def _daftar_tujuan(nilai: str) -> list[str]:
        return [x.strip() for x in nilai.split(",") if x.strip()]

API = "https://api.telegram.org/bot{token}/{metode}"


def _dari_config(kunci: str) -> str:
    """Ambil satu setelan notifikasi dari config, kosong kalau tidak ada.

    Dibungkus try/except karena alat ini juga dipasang di robot saham yang
    kodebasenya berbeda — lebih baik kehilangan satu keterangan daripada
    seluruh pemeriksaan gagal hanya karena bentuk config-nya lain.
    """
    try:
        from src.config import load_config
        cfg = load_config()
        return str(cfg.get("notifikasi", {}).get(kunci, "") or "")
    except Exception:
        return ""


def _samarkan(chat_id: str) -> str:
    """Tampilkan id chat tanpa membocorkan seluruhnya."""
    s = str(chat_id)
    return s if len(s) <= 4 else f"…{s[-4:]}"


def _nama_tujuan(token: str, chat_id: str) -> tuple[str, str]:
    """(nama, jenis) chat ini. Kosong kalau Telegram tidak mau memberitahu.

    Jenisnya ikut dibaca dari Telegram, tidak ditebak dari tanda minus pada
    id. Menebak dari teks id pernah membuat saya salah melaporkan sebuah GRUP
    sebagai chat pribadi — tanda minusnya ada di id kedua, sedangkan yang
    diperiksa teks gabungan "id1,id2".
    """
    try:
        r = requests.get(API.format(token=token, metode="getChat"),
                         params={"chat_id": chat_id}, timeout=20)
        if r.status_code != 200:
            return "", ""
        h = r.json().get("result", {})
        nama = " ".join(x for x in (h.get("first_name"), h.get("last_name")) if x)
        jenis = {"private": "pribadi", "group": "grup",
                 "supergroup": "grup", "channel": "channel"}.get(
                     h.get("type", ""), h.get("type", ""))
        return (nama or h.get("title") or h.get("username") or ""), jenis
    except Exception:
        return "", ""


def _jelaskan(kode: int, isi: str, username: str) -> str:
    """Terjemahkan galat Telegram jadi kalimat yang bisa ditindaklanjuti."""
    pesan = isi.lower()
    if kode == 403:
        return (f"BELUM /start — orang ini harus membuka @{username} "
                f"di Telegram lalu menekan tombol Start. Sebelum itu, bot "
                f"tidak diizinkan mengirim pesan kepadanya.")
    if kode == 400 and "chat not found" in pesan:
        return ("id chat tidak dikenal — periksa TELEGRAM_CHAT_ID di .env")
    if kode == 400 and "kicked" in pesan:
        return "bot diblokir oleh orang ini"
    if kode == 401:
        return "token ditolak — sudah dicabut, atau salah salin"
    return f"{kode}: {isi[:150]}"


def main() -> int:
    ap = argparse.ArgumentParser(description="Periksa bot Telegram robot ini")
    ap.add_argument("--tanpa-kirim", action="store_true", dest="tanpa_kirim",
                    help="periksa saja, jangan kirim pesan uji")
    a = ap.parse_args()

    env = load_env()
    token = env.get("TELEGRAM_BOT_TOKEN", "")
    tujuan = _daftar_tujuan(env.get("TELEGRAM_CHAT_ID", ""))

    judul = _dari_config("judul")
    seharusnya = _dari_config("bot_username").lstrip("@")
    print("=" * 68)
    print(f"  PERIKSA BOT TELEGRAM{' — ' + judul if judul else ''}")
    print("=" * 68)

    if not token:
        print("  TELEGRAM_BOT_TOKEN kosong di .env folder ini.")
        return 1
    if not tujuan:
        print("  TELEGRAM_CHAT_ID kosong di .env folder ini.")
        return 1

    # --- bot ini siapa? ---
    try:
        r = requests.get(API.format(token=token, metode="getMe"), timeout=20)
    except Exception as e:
        print(f"  tidak bisa menghubungi Telegram: {e}")
        return 1

    if r.status_code != 200:
        print(f"  token DITOLAK Telegram ({r.status_code}).")
        print("  Kemungkinan sudah dicabut lewat BotFather, atau salah salin.")
        return 1

    bot = r.json().get("result", {})
    username = bot.get("username", "?")
    print(f"  bot      : {bot.get('first_name', '?')}  (@{username})")

    # Token yang tertukar antar robot tetap mengirim dengan sukses — hanya ke
    # chat yang salah. Tanpa pemeriksaan ini, tidak ada satu pun tanda bahwa
    # ada yang keliru.
    if seharusnya and username.lower() != seharusnya.lower():
        print(f"  SALAH BOT: robot ini seharusnya memakai @{seharusnya},")
        print(f"             tapi token di .env milik @{username}.")
        print(f"             Kemungkinan tokennya tertukar dengan robot lain.")
        print(f"             Perbaiki: bash ~/pasang_token.sh")
        return 1
    if seharusnya:
        print(f"  sesuai   : cocok dengan bot_username di config")

    print(f"  tujuan   : {len(tujuan)} penerima")
    print()

    # --- tiap tujuan diuji satu per satu ---
    gagal = 0
    for chat_id in tujuan:
        nama, jenis = _nama_tujuan(token, chat_id)
        label = (f"{jenis or '?':<8} {nama or '(tak terbaca)'} "
                 f"({_samarkan(chat_id)})")

        if a.tanpa_kirim:
            # getChat sudah berhasil berarti bot mengenal chat itu, tapi itu
            # BUKAN jaminan boleh mengirim. Karena itu dibedakan tegas.
            status = "dikenali" if nama else "tidak bisa dibaca"
            print(f"  {label:<40} {status}  (belum diuji kirim)")
            continue
        print(f"  {label}")

    if not a.tanpa_kirim:
        # Dikirim lewat notify.kirim() — JALUR YANG SAMA PERSIS dengan yang
        # dipakai robot sungguhan.
        #
        # Dulu alat ini menyusun permintaannya sendiri, dan itu ternyata
        # berbahaya: pesan uji bisa sampai sementara pesan robot tidak, tanpa
        # ada cara menemukan bedanya — karena yang diuji memang bukan jalur
        # yang dipakai. Selama keduanya satu jalur, "uji berhasil" benar-benar
        # berarti "robot berhasil".
        print()
        from src import notify
        pesan = (f"<b>Uji koneksi{' — ' + judul if judul else ''}</b>\n"
                 f"Bot @{username} mengirim lewat jalur yang sama dengan "
                 f"robot.\n<i>Pesan uji, bukan sinyal trading.</i>")
        if not notify.kirim(pesan):
            gagal = len(tujuan)

    print()
    if gagal:
        print(f"  {gagal} dari {len(tujuan)} tujuan BELUM bisa dikirimi.")
        print("  Robot akan tetap jalan, tapi orang itu tidak menerima apa pun.")
        return 1
    if not a.tanpa_kirim:
        print(f"  Semua {len(tujuan)} tujuan menerima pesan uji.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
