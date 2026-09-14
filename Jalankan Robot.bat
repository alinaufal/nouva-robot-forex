@echo off
REM Klik dua kali berkas ini untuk menjalankan robot tanpa mengetik perintah.
REM chcp 65001 membuat huruf beraksen dan tanda bullet tampil benar.
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo ==========================================================
echo    ROBOT ANALISIS CRYPTO ^& FOREX
echo    Memperbarui harga, mencari sinyal, mengirim notifikasi
echo ==========================================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [GAGAL] Python tidak ditemukan di komputer ini.
    echo.
    echo Pasang Python lebih dulu dari https://www.python.org/downloads/
    echo Saat memasang, centang "Add Python to PATH".
    echo.
    echo Tekan tombol apa saja untuk menutup...
    pause >nul
    exit /b 1
)

python run_cek.py
set KODE=%errorlevel%

echo.
if %KODE% neq 0 (
    echo ==========================================================
    echo    ADA MASALAH ^(kode %KODE%^)
    echo    Baca pesan di atas untuk mengetahui penyebabnya.
    echo ==========================================================
) else (
    echo ==========================================================
    echo    SELESAI
    echo    Kalau Telegram sudah disiapkan, ringkasannya sudah
    echo    terkirim ke HP Anda.
    echo ==========================================================
)

echo.
echo Tekan tombol apa saja untuk menutup jendela ini...
pause >nul
