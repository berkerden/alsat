#!/usr/bin/env bash
# Sunucunun (VPS) bir kerelik hazırlığı (Faz 7). Yeni kiralanmış bir
# Ubuntu 24.04 / 22.04 ya da Debian 12 sunucuda root olarak çalıştırılır.
# Yeniden çalıştırmak zararsızdır: yapılmış adımı atlar.
#
# Ne yapar:
#   1. Sistemi günceller, güvenlik güncellemelerini otomatik yapar.
#   2. Güvenlik duvarı: yalnızca SSH girişine izin verir.
#   3. SSH'ye anahtarla girildiyse parolayla girişi kapatır.
#   4. Saati eşitler (Binance zaman damgası farkını kabul etmez).
#   5. Belleği azsa takas alanı açar (derleme ve testler için).
#   6. Docker'ı Docker'ın kendi deposundan kurar.
#   7. Kodu /opt/albsat'a indirir; veri ve sır dizinlerini doğru izinlerle
#      açar; bu makineyi "sunucu" olarak işaretler (kurulum.sh bunu okur).
#   8. Binance'e bu konumdan ulaşılabiliyor mu diye bakar (ABD yasaklı).
#
# Sır istemez, sır yazmaz. Uygulamayı başlatmaz; sonraki adım docs/FAZ7-SUNUCU.md.
set -u

KIRMIZI=$'\033[31m'; YESIL=$'\033[32m'; SARI=$'\033[33m'; KALIN=$'\033[1m'; SIFIR=$'\033[0m'
baslik() { printf '\n%s%s%s\n' "$KALIN" "$1" "$SIFIR"; }
hata()   { printf '%s%s%s\n' "$KIRMIZI" "$1" "$SIFIR" >&2; }
tamam()  { printf '%s✓ %s%s\n' "$YESIL" "$1" "$SIFIR"; }
uyari()  { printf '%s! %s%s\n' "$SARI" "$1" "$SIFIR"; }

DEPO="https://github.com/berkerden/alsat.git"
HEDEF="/opt/albsat"
AYAR="/etc/albsat"
SIRLAR="$AYAR/sirlar"
# Konteynerdeki kullanıcının kimliği (Dockerfile ile aynı olmalı).
UYGULAMA_UID=10001

if [ "$(id -u)" != "0" ]; then
  hata "Bu betik root olarak çalışmalı. Sunucuya root ile bağlanıp yeniden çalıştırın."
  exit 1
fi
if [ ! -r /etc/os-release ]; then
  hata "İşletim sistemi tanınmadı (/etc/os-release yok)."
  exit 1
fi
# shellcheck disable=SC1091
. /etc/os-release
case "${ID:-}" in
  ubuntu|debian) ;;
  *) hata "Bu betik Ubuntu ya da Debian içindir; bu sistem: ${PRETTY_NAME:-bilinmiyor}"; exit 1 ;;
esac
tamam "Sistem: ${PRETTY_NAME:-$ID}"
export DEBIAN_FRONTEND=noninteractive

baslik "1/8  Sistem güncelleniyor (birkaç dakika sürebilir)"
apt-get update -q || { hata "Paket listesi alınamadı. İnternet bağlantısını kontrol edin."; exit 1; }
apt-get -y -q upgrade
apt-get -y -q install ca-certificates curl git gnupg ufw unattended-upgrades
# Güvenlik güncellemeleri kendiliğinden kurulsun.
printf 'APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";\n' \
  > /etc/apt/apt.conf.d/20auto-upgrades
tamam "Sistem güncel; güvenlik güncellemeleri otomatik"

baslik "2/8  Güvenlik duvarı"
ufw allow OpenSSH >/dev/null
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw --force enable >/dev/null
tamam "Dışarıdan yalnızca SSH (22) açık. Uygulama internete port açmaz."

baslik "3/8  SSH girişi"
if [ -s /root/.ssh/authorized_keys ]; then
  mkdir -p /etc/ssh/sshd_config.d
  printf 'PasswordAuthentication no\nKbdInteractiveAuthentication no\nPermitRootLogin prohibit-password\n' \
    > /etc/ssh/sshd_config.d/10-albsat.conf
  if sshd -t 2>/dev/null; then
    systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || true
    tamam "Parolayla giriş kapatıldı; yalnızca SSH anahtarıyla girilir."
  else
    rm -f /etc/ssh/sshd_config.d/10-albsat.conf
    uyari "SSH ayarı sınanamadı; değiştirilmedi."
  fi
else
  uyari "Bu sunucuda kayıtlı SSH anahtarı yok; parolayla giriş açık bırakıldı."
  printf '   Rehberdeki "SSH anahtarı" adımını yapıp bu betiği yeniden çalıştırın.\n'
fi

baslik "4/8  Saat eşitleme"
timedatectl set-ntp true 2>/dev/null || true
if timedatectl show -p NTPSynchronized --value 2>/dev/null | grep -q yes; then
  tamam "Saat eşitlendi (NTP)."
else
  uyari "Saat henüz eşitlenmedi; birkaç dakika içinde eşitlenir. 'timedatectl' ile bakılabilir."
fi

baslik "5/8  Bellek"
BELLEK_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
if [ "$BELLEK_MB" -lt 1900 ] && ! swapon --show 2>/dev/null | grep -q .; then
  if [ ! -f /swapfile ]; then
    fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null
  fi
  swapon /swapfile 2>/dev/null || true
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  tamam "Bellek ${BELLEK_MB} MB; 2 GB takas alanı açıldı."
else
  tamam "Bellek ${BELLEK_MB} MB; takas gerekmiyor ya da zaten var."
fi

baslik "6/8  Docker kuruluyor"
if docker compose version >/dev/null 2>&1; then
  tamam "Docker zaten kurulu: $(docker --version)"
else
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL "https://download.docker.com/linux/$ID/gpg" -o /etc/apt/keyrings/docker.asc \
    || { hata "Docker'ın imza anahtarı indirilemedi."; exit 1; }
  chmod a+r /etc/apt/keyrings/docker.asc
  printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/%s %s stable\n' \
    "$(dpkg --print-architecture)" "$ID" "$VERSION_CODENAME" > /etc/apt/sources.list.d/docker.list
  apt-get update -q
  apt-get -y -q install docker-ce docker-ce-cli containerd.io docker-buildx-plugin \
    docker-compose-plugin || { hata "Docker kurulamadı."; exit 1; }
  systemctl enable --now docker >/dev/null 2>&1 || true
  tamam "Docker kuruldu: $(docker --version)"
fi

baslik "7/8  Uygulama kodu ve dizinler"
if [ -d "$HEDEF/.git" ]; then
  git -C "$HEDEF" pull --ff-only -q && tamam "Kod güncellendi: $HEDEF"
else
  git clone -q "$DEPO" "$HEDEF" || { hata "Kod indirilemedi: $DEPO"; exit 1; }
  tamam "Kod indirildi: $HEDEF"
fi
install -d -m 0755 "$AYAR"
install -d -m 0700 -o "$UYGULAMA_UID" -g "$UYGULAMA_UID" "$SIRLAR"
chown "$UYGULAMA_UID:$UYGULAMA_UID" "$SIRLAR"
chmod 0700 "$SIRLAR"
install -d -m 0750 -o "$UYGULAMA_UID" -g "$UYGULAMA_UID" "$HEDEF/veri"
printf 'Bu makine albsat sunucusudur; kurulum.sh Docker ile çalışır.\n' > "$AYAR/sunucu"
tamam "Sır dizini $SIRLAR (yalnızca uygulama okur), veri dizini $HEDEF/veri"

baslik "8/8  Binance'e bu konumdan ulaşılıyor mu?"
KOD=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 https://api.binance.com/api/v3/ping)
case "$KOD" in
  200) tamam "Binance erişilebilir (HTTP 200)." ;;
  451)
    hata "Binance bu sunucunun konumundan hizmet vermiyor (HTTP 451)."
    printf '   ABD gibi kısıtlı bir ülkede. Sunucuyu Avrupa'"'"'da (örneğin Almanya)\n'
    printf '   yeniden oluşturun; bu sunucu uygulamayı çalıştıramaz.\n'
    ;;
  403)
    hata "Binance isteği reddetti (HTTP 403)."
    printf '   Sunucu firmasının ağı ya da konumu engelleniyor olabilir. Bu çıktıyı Claude ile\n'
    printf '   paylaşın; uygulamayı başlatmadan önce çözülmeli.\n'
    ;;
  *) uyari "Binance'e ulaşılamadı (yanıt: ${KOD:-yok}). Birazdan yeniden deneyin." ;;
esac
# Genel IP'yi sunucu firmasının panelinde de görürsünüz; burada kolaylık için sorulur.
GENEL_IP=$(curl -fsS --max-time 10 https://api.ipify.org 2>/dev/null || true)
if ! printf '%s' "$GENEL_IP" | grep -Eq '^[0-9]{1,3}(\.[0-9]{1,3}){3}$|^[0-9a-fA-F:]+$'; then
  GENEL_IP="bulunamadı; sunucu firmasının panelinde yazar"
fi

baslik "Hazır"
printf 'Sunucunun genel IP adresi: %s%s%s\n' "$KALIN" "$GENEL_IP" "$SIFIR"
printf 'Canlı işlem anahtarını oluştururken Binance'"'"'e bu IP yazılacak.\n\n'
printf 'Sonraki adım uygulamayı başlatmak:\n'
printf '   %scd %s && bash kurulum.sh baslat%s\n' "$KALIN" "$HEDEF" "$SIFIR"
