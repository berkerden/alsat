#!/usr/bin/env bash
# Mac'te tek komutla kurulum ve fizibilite taraması.
# Kullanım:  bash kurulum.sh
#
# Faz 7: sunucuda (/etc/albsat/sunucu dosyası varsa) aynı betik Docker ile
# çalışır; seçenekleri aşağıda "sunucu_komutu" içinde. Mac'te davranış aynı.
set -u

KIRMIZI=$'\033[31m'; YESIL=$'\033[32m'; SARI=$'\033[33m'; KALIN=$'\033[1m'; SIFIR=$'\033[0m'

baslik() { printf '\n%s%s%s\n' "$KALIN" "$1" "$SIFIR"; }
hata()   { printf '%s%s%s\n' "$KIRMIZI" "$1" "$SIFIR" >&2; }
tamam()  { printf '%s✓ %s%s\n' "$YESIL" "$1" "$SIFIR"; }
uyari()  { printf '%s! %s%s\n' "$SARI" "$1" "$SIFIR"; }

cd "$(dirname "$0")" || exit 1

# --- sunucu (Faz 7) ---------------------------------------------------------------
# Sunucu, sunucu/ilk-kurulum.sh'nin yazdığı /etc/albsat/sunucu dosyasından
# tanınır. Mac'te bu dosya yoktur; Mac'te aşağıdaki bölüm hiç çalışmaz.

SUNUCU_SECENEKLERI="baslat, durum, durdur, gunluk, yedek, geri-yukle, telegram, gozcu,
                     gozcu-sina, anahtar, komisyon, demo-anahtar, demo-sina,
                     canli-anahtar, canli-sina"

goruntu_derle() {
  baslik "Görüntü derleniyor ve testler sunucuda çalıştırılıyor"
  printf '   İlk seferde birkaç dakika sürer; ekrana ilerleme yazılır.\n\n'
  if ! docker build --target sinama -t albsat:sinama .; then
    hata "Derleme ya da testler başarısız. Uygulama başlatılmadı; çalışan sürüm değişmedi."
    printf 'Yukarıdaki çıktının son 30 satırını Claude ile paylaşın.\n'
    return 1
  fi
  if ! docker compose build albsat; then
    hata "Uygulama görüntüsü derlenemedi."
    return 1
  fi
  tamam "Görüntü hazır, testler geçti"
}

goruntu_var_mi() {
  docker image inspect albsat:yerel >/dev/null 2>&1 || goruntu_derle
}

dizin_sahipligi() {
  # Konteynerdeki kullanıcı (10001) veri dizinine yazabilmeli. Mac'ten kopyalanan
  # dosyalar root'un olur; uygulama onlara yazamaz ve kayıt tutamazdı.
  mkdir -p veri
  chown -R 10001:10001 veri 2>/dev/null || uyari "veri dizininin sahibi ayarlanamadı (root değil misiniz?)"
}

sunucu_calistir() {
  # $1: albsat.cli altındaki modül; gerisi o komutun seçenekleri.
  local modul="$1"; shift
  goruntu_var_mi || return 1
  dizin_sahipligi
  docker compose run --rm kurulum "albsat.cli.$modul" "$@"
}

yeniden_baslat_notu() {
  printf '\nUygulama çalışıyorsa yeni ayarı görmesi için yeniden başlatın:\n'
  printf '   %sbash kurulum.sh baslat%s\n' "$KALIN" "$SIFIR"
}

sunucu_komutu() {
  if ! command -v docker >/dev/null 2>&1; then
    hata "Docker kurulu değil. Önce sunucu/ilk-kurulum.sh çalıştırılmalı (docs/FAZ7-SUNUCU.md)."
    return 1
  fi
  case "${1:-}" in
    baslat)
      goruntu_derle || return 1
      baslik "Uygulama başlatılıyor"
      dizin_sahipligi
      docker compose up -d albsat || { hata "Başlatılamadı."; return 1; }
      printf '   Açılış uzlaştırması sürüyor; en fazla iki dakika beklenir'
      for _ in $(seq 1 24); do
        if docker compose exec -T albsat python -m albsat.cli.yoklama --sessiz >/dev/null 2>&1
        then break; fi
        printf '.'
        sleep 5
      done
      printf '\n\n'
      docker compose exec -T albsat python -m albsat.cli.yoklama
      printf '\nÇökerse ya da sunucu yeniden başlarsa uygulama kendiliğinden açılır.\n'
      printf 'Durumu görmek için:  %sbash kurulum.sh durum%s\n' "$KALIN" "$SIFIR"
      ;;
    durum)
      docker compose ps albsat
      printf '\n'
      if [ -z "$(docker compose ps -q --status running albsat 2>/dev/null)" ]; then
        uyari "Uygulama çalışmıyor. Başlatmak için: bash kurulum.sh baslat"
        return 1
      fi
      docker compose exec -T albsat python -m albsat.cli.yoklama
      ;;
    durdur)
      docker compose stop albsat && tamam "Uygulama durduruldu; siz başlatana kadar kapalı kalır."
      printf '   Borsadaki açık pozisyonların stop ve hedefi yerinde kalır.\n'
      printf '   Gözcü kuruluysa birkaç dakika içinde "çalışmıyor" alarmı gelir; bu beklenen durum.\n'
      ;;
    gunluk)
      docker compose logs --tail 200 albsat
      ;;
    yedek)          sunucu_calistir yedek && sunucu_calistir yedek --listele ;;
    geri-yukle)
      if [ -z "${2:-}" ]; then
        hata "Hangi yedek? Örnek: bash kurulum.sh geri-yukle albsat-yedek-20260926-120000.tar.gz"
        printf 'Yedekleri görmek için: bash kurulum.sh yedek\n'
        return 1
      fi
      sunucu_calistir yedek --geri-yukle "$2"
      ;;
    telegram)       sunucu_calistir telegram && yeniden_baslat_notu ;;
    gozcu)          sunucu_calistir gozcu && yeniden_baslat_notu ;;
    gozcu-sina)     sunucu_calistir gozcu --sina ;;
    anahtar)        sunucu_calistir anahtar ;;
    komisyon)       sunucu_calistir anahtar --olc ;;
    demo-anahtar)   sunucu_calistir demo && yeniden_baslat_notu ;;
    demo-sina)      sunucu_calistir demo --sina ;;
    canli-anahtar)  sunucu_calistir canli && yeniden_baslat_notu ;;
    canli-sina)     sunucu_calistir canli --sina ;;
    *)
      hata "Sunucuda bu seçenek yok: ${1:-(boş)}"
      printf 'Sunucuda kullanılabilecekler: %s\n' "$SUNUCU_SECENEKLERI"
      printf 'Tarama ve fizibilite Mac'"'"'te çalışır; sunucu yalnızca uygulamayı 7/24 çalıştırır.\n'
      return 1
      ;;
  esac
}

if [ "$(uname -s)" != "Darwin" ] && [ -f /etc/albsat/sunucu ]; then
  sunucu_komutu "$@"
  exit $?
fi

# "bash kurulum.sh tarama" veri indirme adımını atlar: Faz 2 örüntü taraması
# internete çıkmaz, veriyi diskteki ./veri klasöründen okur.
# "bash kurulum.sh teshis" ayrıca maliyetsiz teşhis turunu da çalıştırır.
# "bash kurulum.sh arayuz" tarama yapmaz; kurulumu tamamlayıp arayüzü açar
# (yalnızca 127.0.0.1; kâğıt işlem yereldir, Demo işlem yalnızca Binance Demo
# Mode'a gider; canlı hesaba yalnızca Canlı işlem sekmesinde Yarı/Tam Otomatik'e
# elle alınmış coinler için emir gider).
# "bash kurulum.sh telegram" Telegram bildirimlerini kurar.
# "bash kurulum.sh anahtar" yalnızca okuma izinli Binance API anahtarını kurar
# ve hesaba özel komisyonu ölçer; "komisyon" yalnızca yeniden ölçer.
# "bash kurulum.sh demo-anahtar" Binance Demo Mode (sahte para) anahtarını kurar;
# "demo-sina" Demo'ya dolmayacak bir sınama emri gönderip iptal eder.
# "bash kurulum.sh canli-anahtar" canlı işlem anahtarını kurar ve yalnızca okur
# (izinler, hesap, komisyon); "canli-sina" canlı hesaba dolmayacak bir sınama
# emri gönderip iptal eder (GERÇEK hesap; göndermeden önce coin adı sorulur).
# Faz 7: "gozcu" uygulama kapanınca alarm veren gözcüyü kurar, "gozcu-sina"
# alarmı sınar; "yedek" hemen yedek alır, "geri-yukle <dosya>" yedeği geri
# yükler (uygulama kapalıyken).
SADECE_TARAMA=0
TESHIS=0
ARAYUZ=0
TEK_ADIM=""
IKINCI="${2:-}"
case "${1:-}" in
  "") ;;
  tarama) SADECE_TARAMA=1 ;;
  teshis) SADECE_TARAMA=1; TESHIS=1 ;;
  arayuz) ARAYUZ=1 ;;
  telegram|anahtar|komisyon|demo-anahtar|demo-sina|canli-anahtar|canli-sina) TEK_ADIM="$1" ;;
  gozcu|gozcu-sina|yedek|geri-yukle) TEK_ADIM="$1" ;;
  *)
    hata "Bilinmeyen seçenek: $1"
    printf 'Kullanılabilecekler: tarama, teshis, arayuz, telegram, anahtar, komisyon,\n'
    printf '                     demo-anahtar, demo-sina, canli-anahtar, canli-sina,\n'
    printf '                     gozcu, gozcu-sina, yedek, geri-yukle\n'
    printf 'Seçeneksiz çalıştırmak için:  bash kurulum.sh\n'
    exit 1
    ;;
esac

ADIM_SAYISI=5
if [ "$ARAYUZ" = "1" ] || [ -n "$TEK_ADIM" ]; then
  ADIM_SAYISI=4
fi

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

if [ "$TEK_ADIM" = "gozcu" ]; then
  baslik "4/$ADIM_SAYISI  Gözcü: uygulama kapanınca ya da takılınca alarm"
  printf "   Ping adresi Mac'inizin Anahtar Zinciri'ne yazılır; ekrana yalnızca sunucu adı yazılır.\n\n"
  python -m albsat.cli.gozcu
  exit $?
fi

if [ "$TEK_ADIM" = "gozcu-sina" ]; then
  baslik "4/$ADIM_SAYISI  Gözcü alarm sınaması"
  printf '   Gözcüye önce "sorun var", 30 saniye sonra "düzeldi" gönderilir.\n'
  printf '   Telefonunuza iki mesaj gelmeli.\n\n'
  python -m albsat.cli.gozcu --sina
  exit $?
fi

if [ "$TEK_ADIM" = "yedek" ]; then
  baslik "4/$ADIM_SAYISI  Yedek"
  python -m albsat.cli.yedek && python -m albsat.cli.yedek --listele
  exit $?
fi

if [ "$TEK_ADIM" = "geri-yukle" ]; then
  baslik "4/$ADIM_SAYISI  Yedeği geri yükleme"
  if [ -z "$IKINCI" ]; then
    hata "Hangi yedek? Örnek: bash kurulum.sh geri-yukle albsat-yedek-20260926-120000.tar.gz"
    python -m albsat.cli.yedek --listele
    exit 1
  fi
  python -m albsat.cli.yedek --geri-yukle "$IKINCI"
  exit $?
fi

if [ "$TEK_ADIM" = "telegram" ]; then
  baslik "4/$ADIM_SAYISI  Telegram bildirimleri kuruluyor"
  printf "   Bot jetonu Mac'inizin Anahtar Zinciri'ne yazılır; dosyaya yazılmaz.\n\n"
  python -m albsat.cli.telegram
  exit $?
fi

if [ "$TEK_ADIM" = "anahtar" ]; then
  baslik "4/$ADIM_SAYISI  Binance API anahtarı (yalnızca okuma) ve komisyon ölçümü"
  printf '   Anahtar yalnızca iki şeyi okumak için kullanılır: anahtarın izinleri ve\n'
  printf '   hesabınıza özel komisyon oranı. Emir gönderilmez, para çekilemez.\n\n'
  python -m albsat.cli.anahtar
  exit $?
fi

if [ "$TEK_ADIM" = "komisyon" ]; then
  baslik "4/$ADIM_SAYISI  Hesaba özel komisyon yeniden ölçülüyor"
  python -m albsat.cli.anahtar --olc
  exit $?
fi

if [ "$TEK_ADIM" = "demo-anahtar" ]; then
  baslik "4/$ADIM_SAYISI  Binance Demo Mode anahtarı (sahte para)"
  printf '   Demo Mode sahte parayla çalışır; canlı hesabınıza hiçbir istek gitmez.\n'
  printf "   Anahtarın özel yarısı Mac'inizin Anahtar Zinciri'nde kalır.\n\n"
  python -m albsat.cli.demo
  exit $?
fi

if [ "$TEK_ADIM" = "demo-sina" ]; then
  baslik "4/$ADIM_SAYISI  Demo Mode uçtan uca sınaması (sahte para)"
  printf "   Demo'ya dolmayacak küçük bir alış emri gönderilir, izlenir ve iptal edilir.\n"
  printf '   Emir göndermeden önce size sorulur.\n\n'
  python -m albsat.cli.demo --sina
  exit $?
fi

if [ "$TEK_ADIM" = "canli-anahtar" ]; then
  baslik "4/$ADIM_SAYISI  Binance CANLI işlem anahtarı (gerçek hesap)"
  printf '   Bu adım emir göndermez: anahtarı kurar, izinlerini, hesabı ve komisyonu okur.\n'
  printf "   Anahtarın özel yarısı Mac'inizin Anahtar Zinciri'nde kalır.\n"
  printf '   Para çekme izni açık bir anahtar kullanılmaz.\n\n'
  python -m albsat.cli.canli
  exit $?
fi

if [ "$TEK_ADIM" = "canli-sina" ]; then
  baslik "4/$ADIM_SAYISI  Canlı hesap uçtan uca sınaması (GERÇEK hesap)"
  printf '   Canlı hesaba dolmaması beklenen küçük bir alış emri gönderilir, izlenir ve\n'
  printf '   birkaç saniye içinde iptal edilir. Göndermeden önce coin adını yazmanız istenir.\n\n'
  python -m albsat.cli.canli --sina
  exit $?
fi

if [ "$ARAYUZ" = "1" ]; then
  baslik "4/$ADIM_SAYISI  Arayüz açılıyor"

  if [ ! -f veri/kurallar.json ]; then
    uyari "Kural deposu (veri/kurallar.json) bulunamadı."
    printf '   Arayüz yine de açılacak, ama öneri üretemez.\n'
    printf '   Önce örüntü taramasını çalıştırın:  bash kurulum.sh tarama\n\n'
  fi

  printf '   Arayüz yalnızca kendi bilgisayarınızdan erişilebilir (127.0.0.1).\n'
  printf "   Binance'in herkese açık fiyat akışına bağlanır. Canlı hesabınıza emir\n"
  printf "   göndermez. Kâğıt işlemler yalnızca bu bilgisayarda kaydedilir; Demo anahtarı\n"
  printf "   kuruluysa Demo işlem sekmesi Binance Demo Mode'a (sahte para) emir gönderir.\n"
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
