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

# "bash kurulum.sh tarama" veri indirme adımını atlar: Faz 2 örüntü taraması
# internete çıkmaz, veriyi diskteki ./veri klasöründen okur.
# "bash kurulum.sh teshis" ayrıca maliyetsiz teşhis turunu da çalıştırır.
# "bash kurulum.sh arayuz" tarama yapmaz; kurulumu tamamlayıp Faz 3
# arayüzünü açar (yalnızca 127.0.0.1, emir göndermez).
SADECE_TARAMA=0
TESHIS=0
ARAYUZ=0
case "${1:-}" in
  tarama) SADECE_TARAMA=1 ;;
  teshis) SADECE_TARAMA=1; TESHIS=1 ;;
  arayuz) ARAYUZ=1 ;;
esac

ADIM_SAYISI=5
[ "$ARAYUZ" = "1" ] && ADIM_SAYISI=4

baslik "1/$ADIM_SAYISI  Python sürümü aranıyor (3.12 veya üstü gerekiyor)"

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

baslik "2/$ADIM_SAYISI  Sanal ortam hazırlanıyor"
if [ ! -d .venv ]; then
  "$PY" -m venv .venv || { hata "Sanal ortam oluşturulamadı."; exit 1; }
fi
# shellcheck disable=SC1091
source .venv/bin/activate || { hata "Sanal ortam etkinleştirilemedi."; exit 1; }
tamam "Sanal ortam hazır (.venv)"

baslik "3/$ADIM_SAYISI  Bağımlılıklar kuruluyor (ilk seferde birkaç dakika sürebilir)"
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

if [ "$ARAYUZ" = "1" ]; then
  baslik "4/$ADIM_SAYISI  Arayüz açılıyor"

  if [ ! -f veri/kurallar.json ]; then
    uyari "Kural deposu (veri/kurallar.json) bulunamadı."
    printf '   Arayüz yine de açılacak, ama öneri üretemez.\n'
    printf '   Önce örüntü taramasını çalıştırın:  bash kurulum.sh tarama\n\n'
  fi

  printf '   Arayüz yalnızca kendi bilgisayarınızdan erişilebilir (127.0.0.1).\n'
  printf '   İnternete çıkmaz, API anahtarı kullanmaz, emir göndermez.\n'
  printf '   Tarayıcı birkaç saniye içinde kendiliğinden açılacak.\n'
  printf '   %sKapatmak için bu pencerede Control-C tuşlayın.%s\n\n' "$KALIN" "$SIFIR"

  python -m albsat.cli.serve
  exit $?
fi

CIKTI="fizibilite-sonuc.txt"

if [ "$SADECE_TARAMA" = "1" ]; then
  baslik "4/$ADIM_SAYISI  Veri tazeleme atlandı"
  printf '   "tarama" seçeneğiyle çalıştırıldı; internete çıkılmayacak.\n'
  printf '   Diskteki veri kullanılacak.\n'
else
  baslik "4/$ADIM_SAYISI  Veri tazeleme ve fizibilite taraması"
  printf '   BTCUSDT ve SOLUSDT için 204 günlük veri kontrol edilecek.\n'
  printf '   Daha önce indirilmiş dosyalar tekrar indirilmez; yalnızca\n'
  printf '   eksik kalan son mumlar borsadan tamamlanır.\n\n'

  python -m albsat.cli.feasibility \
    --semboller BTCUSDT SOLUSDT \
    --periyotlar 1m 5m 15m 1h \
    --gun 204 2>&1 | tee "$CIKTI"

  # Bu adım internete çıkar ve başarısız olabilir. Örüntü taraması ise
  # tamamen yereldir; ağ yüzünden onu da iptal etmenin anlamı yok.
  if [ ! -s "$CIKTI" ]; then
    uyari "Fizibilite taraması çıktı üretmedi; örüntü taramasına yine de geçiliyor."
  fi
fi

baslik "5/$ADIM_SAYISI  Örüntü keşfi ve backtest (Faz 2)"
printf '   Bu adım internete çıkmaz; diskteki veriyi okur.\n'
printf '   BTCUSDT ve SOLUSDT, 15m ve 1h, 2-3-4 mumluk hedef pencereleri.\n'
printf '   Birkaç dakika sürebilir; ekrana ilerleme yazar.\n\n'

ORUNTU="faz2-oruntu-sonuc.txt"
python -m albsat.cli.research --rapor "$ORUNTU"
TARAMA_SONUC=$?

TESHIS_SONUC=1
TESHIS_DOSYA="faz2-teshis-sonuc.txt"
if [ "$TESHIS" = "1" ]; then
  baslik "Ek adım  Teşhis turu: ortada yön bilgisi var mı?"
  printf '   Bu tur maliyeti SIFIR sayar ve işlem önerisi ÜRETMEZ.\n'
  printf '   Tek bir soruyu ayırır: kâr çıkmamasının sebebi maliyetin\n'
  printf '   ağırlığı mı, yoksa ortada hiç yön bilgisi olmaması mı?\n'
  printf '   Yukarıdaki tarama kadar sürer; ekrana ilerleme yazar.\n\n'

  python -m albsat.cli.research --rapor "$TESHIS_DOSYA" --maliyetsiz
  TESHIS_SONUC=$?
fi

baslik "Bitti"
if [ "$SADECE_TARAMA" != "1" ] && [ -s "$CIKTI" ]; then
  printf 'Fizibilite sonucu:   %s%s%s\n' "$KALIN" "$PWD/$CIKTI" "$SIFIR"
fi
if [ "$TARAMA_SONUC" = "0" ]; then
  printf 'Örüntü raporu:       %s%s%s\n' "$KALIN" "$PWD/$ORUNTU" "$SIFIR"
  if [ "$TESHIS_SONUC" = "0" ]; then
    printf 'Teşhis raporu:       %s%s%s\n' "$KALIN" "$PWD/$TESHIS_DOSYA" "$SIFIR"
    printf '\nRaporları açmak için:  open "%s" "%s"\n' "$ORUNTU" "$TESHIS_DOSYA"
    printf '\nHer iki dosyanın içeriğini de Claude ile paylaşın.\n'
  else
    printf '\nRaporu açmak için:  open "%s"\n' "$ORUNTU"
    printf '\nBu dosyanın içeriğini Claude ile paylaşın.\n'
  fi
  printf '\nArayüzü açmak için:  %sbash kurulum.sh arayuz%s\n' "$KALIN" "$SIFIR"
else
  hata "Örüntü taraması veriyi bulamadı."
  printf 'Önce veriyi indirmek için şunu çalıştırın:  bash kurulum.sh\n'
fi
