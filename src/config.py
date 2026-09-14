"""Pemuat konfigurasi dan variabel rahasia (.env)."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def load_config(path: str | Path = "config.yaml") -> dict:
    """Baca config.yaml dari akar project."""
    full = ROOT / path
    with open(full, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_env(path: str | Path = ".env") -> dict:
    """Baca file .env sederhana menjadi dictionary.

    Ditulis manual (bukan lewat pustaka) supaya tetap terbaca walau filenya
    pernah disimpan lewat Notepad/PowerShell yang menambahkan BOM atau
    akhiran baris CRLF.
    """
    full = ROOT / path
    result: dict[str, str] = {}
    if not full.exists():
        return result
    with open(full, "r", encoding="utf-8-sig") as f:  # utf-8-sig membuang BOM
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def db_path(cfg: dict) -> Path:
    """Path absolut ke file database, foldernya dibuat kalau belum ada."""
    p = ROOT / cfg["data"]["db_path"]
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


# Setelan yang boleh berbeda PER SIMBOL, ditulis di entri watchlist.
# Daftarnya sengaja eksplisit: kalau semua kunci ikut disalin, salah ketik di
# watchlist akan diam-diam menimpa parameter strategi tanpa ada yang tahu.
PER_SIMBOL = ("sesi_utc", "penyangga_atr", "rr_minimal", "tradingview")


def parameter(cfg: dict, strategi: str | None = None,
              timeframe: str | None = None,
              item: dict | None = None) -> dict:
    """Ambil parameter untuk satu strategi pada satu timeframe.

    Dipisah jadi fungsi sendiri karena dipakai di tiga tempat: pencarian
    sinyal harian, backtest, dan uji mandiri.

    `item` adalah entri watchlist simbol yang sedang diproses. Kunci yang
    terdaftar di PER_SIMBOL akan menimpa parameter strategi — dipakai untuk
    hal yang memang berbeda tiap simbol, misalnya jam sesi ramai: emas
    diperdagangkan saat overlap London-New York, GBPUSD saat London dibuka.
    """
    strategi = strategi or cfg["strategi_aktif"]
    timeframe = timeframe or cfg["timeframe"]
    try:
        p = dict(cfg["strategi"][strategi][timeframe])
    except KeyError as e:
        raise KeyError(
            f"Parameter untuk strategi '{strategi}' timeframe '{timeframe}' "
            f"tidak ada di config.yaml"
        ) from e

    # Take profit diatur sekali di tingkat atas config, lalu disisipkan ke
    # semua strategi dan timeframe. Kalau suatu blok strategi menuliskan
    # tp_rasio sendiri, nilainya yang dipakai — jadi bisa diatur khusus
    # tanpa kehilangan kemudahan satu tombol untuk semuanya.
    p.setdefault("tp_rasio", float(cfg.get("take_profit_rasio", 0) or 0))

    # Lapisan konfirmasi (volume, MACD, gerbang imbalan-risiko, jeda) juga
    # diatur sekali di tingkat atas lalu disisipkan ke setiap strategi.
    # Lewat jalur ini, backtest maupun pencarian sinyal harian ikut memakainya
    # tanpa satu pun pemanggilnya perlu diubah.
    p.setdefault("konfirmasi", cfg.get("konfirmasi") or {})

    if item:
        for kunci in PER_SIMBOL:
            if kunci in item:
                p[kunci] = item[kunci]
    return p
