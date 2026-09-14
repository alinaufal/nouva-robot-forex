@echo off
REM Versi untuk Task Scheduler: tanpa "pause", dan hasilnya dicatat ke berkas
REM log supaya bisa diperiksa kalau dijalankan saat Anda tidak menonton.
REM Untuk pemakaian manual, pakai "Jalankan Robot.bat" saja.
chcp 65001 >nul
cd /d "%~dp0"

if not exist "data" mkdir "data"
set LOG=data\log-harian.txt

echo. >> "%LOG%"
echo ========================================================== >> "%LOG%"
echo  Dijalankan otomatis: %date% %time% >> "%LOG%"
echo ========================================================== >> "%LOG%"

python run_cek.py >> "%LOG%" 2>&1
set KODE=%errorlevel%

if %KODE% neq 0 (
    echo [GAGAL] kode keluar %KODE% >> "%LOG%"
) else (
    echo [SELESAI] >> "%LOG%"
)

exit /b %KODE%
