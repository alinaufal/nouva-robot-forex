"""Pengiriman notifikasi ke Telegram.

Kalau token belum diisi, pesan hanya dicetak ke layar — jadi program tetap
bisa dipakai sebelum bot Telegram disiapkan.
"""
from __future__ import annotations

import html
import re

import requests

from . import waktu
from .config import load_env

API = "https://api.telegram.org/bot{token}/sendMessage"

# Bentuk token bot Telegram. Jaring kedua saat menyaring teks galat, untuk
# berjaga kalau token muncul dalam bentuk yang tidak persis sama.
POLA_TOKEN = re.compile(r"bot\d{8,10}:[A-Za-z0-9_-]{30,}")


def _daftar_tujuan(nilai: str) -> list[str]:
    """Pisahkan beberapa tujuan yang ditulis dengan koma.

    Contoh: "123456789, -1001234567890" -> chat pribadi dan sebuah grup.
    """
    return [x.strip() for x in nilai.split(",") if x.strip()]


def kirim(teks: str) -> bool:
    """Kirim pesan ke seluruh tujuan di TELEGRAM_CHAT_ID.

    Mengembalikan True kalau minimal satu tujuan berhasil menerima. Satu
    tujuan gagal (misalnya bot dikeluarkan dari grup) tidak menggagalkan
    pengiriman ke tujuan lain.
    """
    env = load_env()
    token = env.get("TELEGRAM_BOT_TOKEN", "")
    tujuan = _daftar_tujuan(env.get("TELEGRAM_CHAT_ID", ""))

    if not token or not tujuan:
        print("\n[Telegram belum disiapkan — pesan ditampilkan di sini saja]\n")
        print(teks)
        return False

    berhasil = 0
    for chat_id in tujuan:
        try:
            r = requests.post(
                API.format(token=token),
                json={"chat_id": chat_id, "text": teks, "parse_mode": "HTML"},
                timeout=20,
            )
            if r.status_code == 200:
                berhasil += 1
                print("  " + _bukti(chat_id, r))
            else:
                # Pesan galat Telegram cukup jelas, mis. "bot is not a member
                # of the channel chat" atau "chat not found"
                print(f"  ! gagal kirim ke {_samar(chat_id)} "
                      f"({r.status_code}): "
                      f"{_saring_rahasia(r.text[:180], token)}")
        except Exception as e:
            print(f"  ! gagal kirim ke {_samar(chat_id)}: "
                  f"{_saring_rahasia(str(e), token)}")

    if berhasil == 0:
        print(teks)
        return False
    if berhasil < len(tujuan):
        print(f"  terkirim ke {berhasil} dari {len(tujuan)} tujuan")
    return True


def _samar(chat_id: str) -> str:
    """Tampilkan id chat tanpa membocorkan seluruhnya ke log."""
    s = str(chat_id)
    return s if len(s) <= 5 else f"…{s[-4:]}"


def _saring_rahasia(teks: str, token: str) -> str:
    """Buang token bot dari teks galat sebelum dicetak ke log.

    Galat jaringan dari `requests` memuat URL lengkap yang sedang diminta —
    dan URL Telegram berisi token: `.../bot<TOKEN>/sendMessage`. Sudah
    dibuktikan dengan token palsu: tanpa penyaring ini, satu kali internet VPS
    putus saat mengirim cukup untuk menulis token ke berkas log.
    """
    if token:
        teks = teks.replace(token, "<token>")
    return POLA_TOKEN.sub("bot<token>", teks)


def _bukti(chat_id: str, r) -> str:
    """Susun catatan ke mana pesan SEBENARNYA mendarat.

    Jawaban `sendMessage` memuat chat tujuan yang sesungguhnya beserta
    message_id-nya, dan selama ini seluruhnya dibuang — pengiriman yang
    berhasil tidak mencatat apa pun. Akibatnya, saat ada pesan yang "hilang",
    tidak ada bukti apa pun untuk ditelusuri: log hanya berkata "terkirim ke
    Telegram" tanpa menyebut ke mana.

    Yang paling penting ditangkap di sini: kalau sebuah grup pernah di-upgrade
    jadi supergroup, Telegram tetap menerima pengiriman ke id LAMA lalu
    menaruh pesannya di chat dengan id BARU. Dua id itu berbeda, dan hanya
    jawaban inilah yang menunjukkannya.
    """
    try:
        hasil = r.json().get("result", {})
        chat = hasil.get("chat", {}) or {}
        nama = (chat.get("title")
                or " ".join(x for x in (chat.get("first_name"),
                                        chat.get("last_name")) if x)
                or chat.get("username") or "?")
        jenis = {"private": "pribadi", "group": "grup",
                 "supergroup": "grup", "channel": "channel"}.get(
                     chat.get("type", ""), chat.get("type", "?"))
        nyata = str(chat.get("id", ""))
        catatan = (f"terkirim -> {jenis} \"{nama}\" "
                   f"[{_samar(nyata)}] pesan #{hasil.get('message_id', '?')}")
        # Dituju id X, mendarat di id Y: hampir selalu berarti grupnya sudah
        # berganti id dan .env masih memakai yang lama.
        if nyata and nyata != str(chat_id):
            catatan += (f"  !! id BERUBAH: dituju {_samar(chat_id)}, "
                        f"mendarat di {_samar(nyata)} — perbarui .env")
        return catatan
    except Exception:
        return f"terkirim -> {_samar(chat_id)} (jawaban tidak terbaca)"


def _angka(nilai: float) -> str:
    """Tampilkan harga dengan jumlah desimal yang masuk akal.

    Harga di project ini rentangnya ekstrem: BTC puluhan ribu dolar, sedangkan
    beberapa koin bernilai pecahan sen. Satu format tetap akan salah di salah
    satu ujungnya.
    """
    n = abs(nilai)
    if n >= 1000:
        return f"{nilai:,.2f}"
    if n >= 1:
        return f"{nilai:,.4f}"
    return f"{nilai:,.8f}".rstrip("0").rstrip(".")


def susun_pesan(sinyal: list[dict], tanggal: str, timeframe: str,
                strategi: str, judul: str = "Sinyal Pasar",
                masalah: dict[str, str] | None = None,
                pulih: list[str] | None = None) -> str:
    """Rangkai isi notifikasi.

    `judul` diatur lewat `notifikasi.judul` di config. Gunanya saat beberapa
    robot memakai bot Telegram yang sama: tanpa judul yang berbeda, pesan
    crypto dan pesan forex tampak serupa di daftar obrolan dan harus dibuka
    dulu untuk tahu yang mana.

    `masalah` berisi data yang gagal diperbarui atau tertinggal, dengan kunci
    'SIMBOL pasar' (lihat pasar.kunci_masalah). `pulih` berisi kunci yang
    tadinya bermasalah dan kini normal kembali.
    """
    baris = [
        f"<b>{judul} — {tanggal}</b>",
        f"<i>{strategi} · timeframe {timeframe}</i>",
    ]
    masalah = masalah or {}

    # Data bermasalah ditaruh PALING ATAS. Tanpa bagian ini, "Tidak ada sinyal
    # baru" di bawah tidak bisa dibedakan dari pasar yang memang sepi —
    # padahal bisa saja robot sedang membaca data kemarin.
    if masalah:
        baris.append("\n<b>⚠ DATA BERMASALAH</b>")
        for kunci, sebab in masalah.items():
            baris.append(f"• {html.escape(kunci)}: {html.escape(sebab)}")
        baris.append("<i>Sinyal simbol ini bisa terlambat atau tidak muncul "
                     "sampai datanya pulih.</i>")
    if pulih:
        baris.append("\n✓ Data kembali normal: "
                     + ", ".join(html.escape(k) for k in pulih))

    if not sinyal:
        baris.append("\nTidak ada sinyal baru.")
    else:
        # Urutan tampil yang diinginkan, lalu SEMUA pasar lain yang muncul di
        # sinyal ikut ditambahkan. Dulu daftarnya ditulis tetap sebagai
        # ("crypto", "forex") — dan saat futures ditambahkan ke program,
        # sinyalnya hilang diam-diam dari pesan: tercatat di log sebagai
        # ditemukan, tapi tidak pernah sampai ke Telegram. Pola di bawah
        # membuat kesalahan itu tidak bisa terulang untuk pasar baru.
        urutan = ["crypto", "futures", "forex"]
        for p in sinyal:
            if p["pasar"] not in urutan:
                urutan.append(p["pasar"])

        for pasar in urutan:
            isi = [s for s in sinyal if s["pasar"] == pasar]
            if not isi:
                continue
            baris.append(f"\n<b>{pasar.upper()}</b>")
            for s in isi:
                arah = s["aksi"]
                # Kabar bahwa batas rugi / take profit tersentuh diberi tanda
                # supaya tidak tertukar dengan ajakan membuka posisi baru.
                tanda = "⚑ " if arah.startswith("KENA") else ""
                baris.append(f"• {tanda}{arah} {s['simbol']} "
                             f"@ {_angka(s['harga'])}")
                # Umur sinyal HARUS terlihat. Harga dan batas rugi di bawah
                # dihitung dari lilin saat sinyal muncul; kalau lilin itu
                # sudah lewat, angkanya tidak lagi menggambarkan pasar
                # sekarang. Dulu pesan ini tidak memuat waktu sama sekali,
                # sehingga sinyal basi dan sinyal baru tampak persis sama.
                umur = int(s.get("umur_lilin", 0) or 0)
                if umur > 0:
                    baris.append(
                        f"  ⏱ <b>sinyal {umur} lilin lalu</b> "
                        f"({waktu.ke_wib(s['ts'])}) — periksa harga sekarang, "
                        f"angka di bawah dihitung dari lilin itu")
                # Kunci yang sama dengan pasar.kunci_masalah(). Ditulis ulang
                # di sini karena mengimpor pasar akan menyeret seluruh sumber
                # data (termasuk yfinance) hanya untuk menyusun teks.
                if f"{s['simbol']} {s['pasar']}" in masalah:
                    baris.append("  ⚠ data simbol ini bermasalah — pastikan "
                                 "harga sekarang sebelum eksekusi")
                if s.get("peringatan_searah"):
                    baris.append(f"  ⚠ {s['peringatan_searah']}")
                if s.get("saran_stop"):
                    baris.append(f"  batas rugi  : {_angka(s['saran_stop'])}")
                if s.get("saran_tp"):
                    rasio = s.get("tp_rasio", 0)
                    baris.append(f"  take profit : {_angka(s['saran_tp'])}"
                                 f"  (1:{rasio:g})")
                if s.get("alasan"):
                    baris.append(f"  <i>{s['alasan']}</i>")

    # Panduan verifikasi berita hanya bisa dikerjakan manusia — robot tidak
    # bisa membaca artikel, menilai kredibilitas medianya, atau tahu siapa
    # penulisnya. Yang bisa dilakukannya adalah mengingatkan pada saat yang
    # tepat: ketika ada ajakan membuka posisi, bukan pada laporan kosong.
    if any(s["aksi"] in ("BELI", "JUAL") for s in sinyal):
        baris.append(
            "\n<i>Sebelum eksekusi: cek 2-3 sumber berita (CoinDesk, "
            "Cointelegraph, The Block), pastikan ada sumber primernya, dan "
            "beri jeda kalau beritanya baru beberapa jam.</i>"
        )

    baris.append(
        "\n<i>Hasil hitungan otomatis, bukan rekomendasi investasi. "
        "Periksa sendiri sebelum memasang order.</i>"
    )
    return "\n".join(baris)
