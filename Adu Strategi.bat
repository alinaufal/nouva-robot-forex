@echo off
REM Klik dua kali untuk mengadu ketiga strategi pada data historis.
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo ==========================================================
echo    ADU TIGA STRATEGI
echo    Ikut tren  vs  Balik ke rata-rata  vs  Tembus batas
echo    Diuji pada dua periode terpisah
echo ==========================================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [GAGAL] Python tidak ditemukan. Pasang dari python.org lebih dulu.
    pause >nul
    exit /b 1
)

python run_backtest.py

echo.
echo ==========================================================
echo    PENTING: baca bagian PENERAPAN ATURAN di bawah tabel.
echo    Perhatikan kolom "per thn", bukan kolom "total".
echo    Strategi yang lolos aturan tapi hasilnya di bawah
echo    bunga deposito tidak layak dipakai untuk uang sungguhan.
echo ==========================================================
echo.
echo Tekan tombol apa saja untuk menutup jendela ini...
pause >nul
