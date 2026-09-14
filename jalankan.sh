#!/usr/bin/env bash
# Titik masuk untuk cron di VPS Linux — pengganti "Jalankan Otomatis.bat".
#
# Berkas .bat tidak bisa dipakai di Linux, dan cron menjalankan perintah tanpa
# PATH selengkap sesi login Anda. Karena itu di sini semuanya ditulis eksplisit:
# pindah ke folder project sendiri, dan memanggil python dari dalam venv dengan
# jalur penuh.

set -u

cd "$(dirname "$0")" || exit 1

mkdir -p data

# Seluruh argumen diteruskan apa adanya ke run_cek.py, sehingga satu berkas ini
# bisa melayani beberapa jadwal cron sekaligus:
#
#   jalankan.sh                                              -> harian
#   jalankan.sh --timeframe 15menit --diam-jika-kosong        -> futures 15 menit
#
# Log dipisah per timeframe. Tanpa pemisahan ini, jadwal 15 menit yang jalan
# 96 kali sehari akan menenggelamkan catatan laporan harian.
TF="harian"
for ((i = 1; i <= $#; i++)); do
  if [ "${!i}" = "--timeframe" ]; then
    j=$((i + 1))
    [ $j -le $# ] && TF="${!j}"
  fi
done
LOG="data/log-${TF}.txt"

{
  echo ""
  echo "=========================================================="
  echo " Dijalankan otomatis: $(date '+%Y-%m-%d %H:%M:%S %Z')"
  echo "=========================================================="
} >> "$LOG"

./venv/bin/python run_cek.py "$@" >> "$LOG" 2>&1
KODE=$?

if [ "$KODE" -ne 0 ]; then
  echo "[GAGAL] kode keluar $KODE" >> "$LOG"
else
  echo "[SELESAI]" >> "$LOG"
fi

# ---------------------------------------------------------------------------
# Kabar Telegram saat robot GAGAL jalan, dan saat kembali normal.
#
# Dulu kegagalan hanya ditulis "[GAGAL]" ke log. Log jadwal 15 menit dulu
# dipangkas sampai sekitar 37 jam terakhir, jadi kegagalan dua hari lalu tidak
# meninggalkan jejak — dan tidak ada yang memberi tahu Anda.
#
# Sengaja memakai curl, bukan python: kalau yang rusak justru python atau
# venv-nya, kabar ini tetap bisa terkirim.
#
# Tiga pengaman rahasia:
#   * .env dibaca dengan grep, TIDAK di-source — isinya tidak pernah
#     dijalankan sebagai perintah.
#   * URL yang memuat token diberikan ke curl lewat stdin (-K -), sehingga
#     tidak terlihat di daftar proses (ps) dan tidak tercatat di log.
#   * Pesan TIDAK memuat potongan log. Log bisa berisi galat apa saja, dan
#     tidak semuanya pantas masuk ke grup.
#
# Penanda data/.gagal-<timeframe> membuat kegagalan beruntun hanya dikabarkan
# sekali: Binance mati tiga jam tidak berubah jadi dua belas pesan.
# ---------------------------------------------------------------------------
kabari() {
  local teks="$1" token tujuan id kode terkirim=0
  token=$(grep -m1 '^TELEGRAM_BOT_TOKEN=' .env 2>/dev/null | cut -d= -f2- | tr -d '\r')
  tujuan=$(grep -m1 '^TELEGRAM_CHAT_ID=' .env 2>/dev/null | cut -d= -f2- | tr -d '\r ')
  if [ -z "$token" ] || [ -z "$tujuan" ]; then
    echo "[KABAR] .env belum lengkap — kabar tidak terkirim" >> "$LOG"
    return 1
  fi
  for id in ${tujuan//,/ }; do
    kode=$(printf 'url = "https://api.telegram.org/bot%s/sendMessage"\n' "$token" \
      | curl -sS -m 20 -o /dev/null -w '%{http_code}' -K - \
          --data-urlencode "chat_id=${id}" --data-urlencode "text=${teks}" \
          2>/dev/null)
    echo "[KABAR] ke …${id: -4}: HTTP ${kode:-gagal}" >> "$LOG"
    [ "$kode" = "200" ] && terkirim=$((terkirim + 1))
  done
  [ "$terkirim" -gt 0 ]
}

PENANDA="data/.gagal-${TF}"
ROBOT=$(basename "$PWD")
KAPAN=$(date '+%d %b %H:%M %Z')

if [ "$KODE" -ne 0 ]; then
  echo "$(date '+%Y-%m-%d %H:%M:%S %Z') GAGAL ${TF} kode ${KODE}" >> data/riwayat-gagal.txt
  if [ ! -f "$PENANDA" ]; then
    kabari "⚠ ${ROBOT} (${TF}) GAGAL jalan — kode keluar ${KODE}, ${KAPAN}. Periksa data/log-${TF}.txt di VPS." \
      && touch "$PENANDA"
  fi
elif [ -f "$PENANDA" ]; then
  if kabari "✓ ${ROBOT} (${TF}) jalan normal kembali, ${KAPAN}."; then
    rm -f "$PENANDA"
    echo "$(date '+%Y-%m-%d %H:%M:%S %Z') PULIH ${TF}" >> data/riwayat-gagal.txt
  fi
fi

# Riwayat kegagalan disimpan terpisah dari log dan dipangkas jauh lebih
# jarang, supaya jejaknya tidak ikut hilang saat log dipangkas.
if [ -f data/riwayat-gagal.txt ]; then
  tail -n 500 data/riwayat-gagal.txt > data/riwayat-gagal.txt.tmp \
    && mv data/riwayat-gagal.txt.tmp data/riwayat-gagal.txt
fi

# Log dipangkas supaya tidak tumbuh tanpa batas. 20.000 baris kira-kira
# sepuluh hari untuk jadwal 15 menit — cukup untuk menelusuri masalah minggu
# lalu, dan tetap hanya beberapa megabyte di disk VPS 25 GB.
if [ -f "$LOG" ]; then
  tail -n 20000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi

exit "$KODE"
