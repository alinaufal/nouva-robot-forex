"""Galat bersama yang perlu dikenali lintas modul.

Dipisah ke berkas sendiri karena sumber data (`sumber_*.py`) dan penghubungnya
(`pasar.py`) sama-sama memakainya, sementara `pasar.py` mengimpor sumber data —
menaruhnya di `pasar.py` akan membuat impor melingkar.
"""
from __future__ import annotations


class GagalUnduh(Exception):
    """Sumber data tidak bisa dihubungi atau menolak permintaan.

    Dulu kegagalan seperti ini hanya dicetak ke log lalu robot tetap
    "SELESAI". Pesan pagi berbunyi "Tidak ada sinyal baru" — persis sama
    dengan hari yang memang sepi — sehingga data yang macet tidak pernah
    ketahuan dari Telegram.

    `pesan` pendek dan aman ditampilkan di Telegram. `rinci` berisi galat
    aslinya untuk log. `baris` membawa data yang sempat terambil sebelum
    gagal (misalnya halaman pertama berhasil, halaman kedua putus), supaya
    tidak ikut terbuang.
    """

    def __init__(self, pesan: str, baris: list | None = None,
                 rinci: str = ""):
        super().__init__(pesan)
        self.pesan = pesan
        self.baris = list(baris or [])
        self.rinci = rinci
