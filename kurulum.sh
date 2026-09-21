#!/usr/bin/env bash
# Mac'te tek komutla kurulum ve fizibilite taraması.
# Kullanım:  bash kurulum.sh
set -u

KIRMIZI=$'\033[31m'; YESIL=$'\033[32m'; SARI=$'\033[33m'; KALIN=$'\033[1m'; SIFIR=$'\033[0m'

baslik() { printf '\n%s%s%s\n' "$KALIN" "$1" "$SIFIR"; }
hata()   { printf '%s%s%s\n' "$KIRMIZI" "$1" "$SIFIR" >&2; }
tamam()  { printf '%s✓ %s%s\n' "$YESIL" "$1" "$SIFIR"; }
uyari()  { printf '%s! %s%s\n' "$SARI" "$1" "$SIFIR"; }

cd "$(dirname "$0")" || exit 1

baslik "1/4  Python sürümü aranıyor (3.12 veya üstü gerekiyor)"

PY=""
for aday in python3.14 python3.13 python3.12 python3 python; do
  if command -v "$aday" >/dev/null 2>&1; then
    if "$aday" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,12) else 1)' 2>/dev/null; then
      PY="$aday"
      break
    fi
  fi
done

if [ -z "$PY" ]; then
  mevcut="$(python3 --version 2>&1 || echo 'Python bulunamadı')"
  hata "Uygun Python bulunamadı. Sistemde: $mevcut"
  cat <<'YARDIM'

Python 3.12 veya üstünü kurmanız gerekiyor. İki yoldan biri:

  A) Kolay yol — resmi kurulum paketi:
     https://www.python.org/downloads/macos/
     Sayfadaki en güncel sürümün ".pkg" dosyasını indirip çift tıklayın,
     sonra bu betiği tekrar çalıştırın.

  B) Homebrew kuruluysa:
     brew install python@3.13

Kurulumdan sonra Terminal'i kapatıp yeniden açın ve şunu çalıştırın:
     bash kurulum.sh
YARDIM
  exit 1
fi
tamam "$($PY --version) bulundu ($PY)"

baslik "2/4  Sanal ortam hazırlanıyor"
if [ ! -d .venv ]; then
  "$PY" -m venv .venv || { hata "Sanal ortam oluşturulamadı."; exit 1; }
fi
# shellcheck disable=SC1091
source .venv/bin/activate || { hata "Sanal ortam etkinleştirilemedi."; exit 1; }
tamam "Sanal ortam hazır (.venv)"

baslik "3/4  Bağımlılıklar kuruluyor (ilk seferde birkaç dakika sürebilir)"
python -m pip install --quiet --upgrade pip || uyari "pip güncellenemedi, devam ediliyor."
if ! python -m pip install --quiet -e ".[dev]"; then
  hata "Bağımlılıklar kurulamadı. İnternet bağlantınızı kontrol edip tekrar deneyin."
  exit 1
fi
tamam "Bağımlılıklar kuruldu"

printf '\n   Testler çalıştırılıyor...\n'
# Çıktıyı dosyaya al: "| tail" kullanılırsa pipeline'ın çıkış kodu tail'inki
# olur ve başarısız test hiç fark edilmez.
TEST_LOG="$(mktemp)"
if python -m pytest -q >"$TEST_LOG" 2>&1; then
  tail -2 "$TEST_LOG"
  tamam "Testler geçti"
  rm -f "$TEST_LOG"
else
  tail -25 "$TEST_LOG"
  hata "Testler geçmedi. Kurulum durduruldu."
  printf '\nYukarıdaki çıktıyı Claude ile paylaşın.\n'
  printf 'Önce şunu deneyin:  git pull && bash kurulum.sh\n'
  rm -f "$TEST_LOG"
  exit 1
fi

baslik "4/4  Fizibilite taraması"
printf '   BTCUSDT ve SOLUSDT için 180 günlük veri indirilecek.\n'
printf '   İlk çalıştırmada indirme birkaç dakika sürebilir; sonraki\n'
printf '   çalıştırmalarda veri önbellekten okunur.\n\n'

CIKTI="fizibilite-sonuc.txt"
python -m albsat.cli.feasibility \
  --semboller BTCUSDT SOLUSDT \
  --periyotlar 1m 5m 15m 1h \
  --gun 180 2>&1 | tee "$CIKTI"

baslik "Bitti"
printf 'Sonuç şu dosyaya da yazıldı: %s%s%s\n' "$KALIN" "$PWD/$CIKTI" "$SIFIR"
printf 'Dosyayı açmak için:  open "%s"\n' "$CIKTI"
printf '\nBu dosyanın içeriğini Claude ile paylaşın.\n'
