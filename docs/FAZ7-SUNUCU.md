# Faz 7: Sunucu, izleme, yedek ve alarm

**Tarih:** 26 Eylül 2026
**Kapsam:** Uygulamanın 7/24 çalışabilmesi için gereken altyapı: günlük yedek,
dış gözcü (uygulama kapanınca alarm), sunucu sır deposu, Docker ile sunucu
kurulumu, Mac'ten sunucuya geçiş rehberi
**Şartname karşılığı:** SPEC.md §5, §6 (heartbeat), §7 ve §10'daki Faz 7 satırı
**Kabul kriteri (SPEC.md §10):** *"7/24 çalışma ve alarm testi."*
**Berk'in kapsam kararı:** bekleniyor. Kart 26 Eylül 2026'da soruldu; önerilen
seçenek "Hazırla, kiralama" (bu belgedeki her şey yazılır ve burada sınanır,
alarm Mac'te ücretsiz gözcüyle denenir, para harcanmaz).

Bu belge Faz 7'nin ne yaptığını ve neden öyle kurulduğunu anlatır. Sunucuya
taşınmanın adım adım sırası §12'de.

---

## 1. Faz 7 ne yapıyor, ne yapmıyor

Faz 2'de kabul edilen kural çıkmadı. 7/24 çalışan uygulama bugün coinleri
Sadece Öneri'de izler, öneri de üretmez; Tam Otomatik kapıda kilitli (Faz 6).
Bu yüzden Faz 7 bir "botu sunucuya taşıma" işi değil, **bir kural kabul
edildiğinde ya da Berk elle canlı pozisyon tuttuğunda uygulamanın kapanıp
fark edilmemesini önleyen altyapı**:

1. **Günlük yedek** (`core/backup.py`, `bash kurulum.sh yedek`). Uygulama
   açıkken günde bir, kendiliğinden; elle de alınır, sınanır, geri yüklenir.
2. **Dış gözcü** (`notify/gozcu.py`, `bash kurulum.sh gozcu`). Uygulama
   Healthchecks.io'ya iki dakikada bir "yaşıyorum" der. Demez ya da "sorun
   var" derse telefona alarm gelir. Uygulama kapanınca haber veren tek parça
   budur; Faz 6'nın "çevrimdışıydı" bildirimi ancak uygulama yeniden
   açılınca gidiyordu.
3. **Sağlık kararı** (`api/runtime.py` → `health_verdict`). Uygulamanın
   gerçekten çalışıp çalışmadığı: döngü dönüyor mu, piyasa verisi geliyor
   mu, disk doluyor mu.
4. **Sunucu sır deposu** (`core/keychain.py`). Anahtar Zinciri yalnızca
   macOS'ta var; sunucuda sırlar izinleri kısıtlı bir dizinde durur.
5. **Tek kopya kilidi** (`core/instance.py`). Aynı veri dizininde ikinci bir
   uygulama açılmaz; iki canlı yürütücü birbirinin emrini silmesin.
6. **Docker kurulumu** (`Dockerfile`, `compose.yaml`) ve **sunucu hazırlık
   betiği** (`sunucu/ilk-kurulum.sh`). `kurulum.sh` sunucuda Docker'la çalışır.
7. **Bağımlılık kilidi** (`requirements.lock`, özetli) ve **gizli bilgi
   taraması** (`core/secretscan.py`, `.githooks/pre-commit`).

Yapmadıkları: sunucu kiralamaz, harici hesap açmaz, anahtar üretmez. Bunlar
para ya da sır gerektirir; Berk'in yazılı onayıyla ve onun elinden yapılır
(§12). Kural uydurmaz, elle emri kapıya saymaz, Tam Otomatik'i açmaz.

## 2. Kabul kriterinin okunuşu

*"7/24 çalışma"* üç şey demek:

* **Çökünce kendiliğinden kalkar.** Docker `restart: unless-stopped`. Burada
  sınandı: süreç `SIGKILL` ile öldürüldü, Docker yeniden başlattı, tek kopya
  kilidi yeniden alındı.
* **Sunucu yeniden başlayınca kalkar.** Aynı politika; `durdur` ile bilerek
  durdurulmadıysa Docker açılışta başlatır. Bu burada denenemedi (ortamın
  kendisi yeniden başlatılamıyor); gerçek sunucuda `reboot` ile denenir.
* **Kapanış düzgün olur.** `bash kurulum.sh durdur` `SIGTERM` gönderir;
  uygulama canlı yürütücüyü, akışları ve gözcüyü kapatır, gözcüye "bilerek
  kapatıldı" notu bırakır, kilidi bırakır. Docker 60 saniye bekler.

*"Alarm testi"* iki parça:

* **Yol çalışıyor mu:** `bash kurulum.sh gozcu-sina` gözcüye "sorun var"
  gönderir, 30 saniye sonra "düzeldi". İki mesaj da telefona gelmeli.
* **Gerçek kapanışta alarm geliyor mu:** uygulama kapatılır, gözcü en geç ~10
  dakikada "down" alarmı verir; açılınca "up" gelir.

Burada ikisi de sahte bir gözcü sunucusuyla uçtan uca sınandı (§14). Gerçek
Healthchecks.io ile deneme Berk'in hesabıyla yapılır.

## 3. Dış gözcü (`notify/gozcu.py`)

**Neden dış hizmet.** Kapanan bir uygulama kendi kapandığını söyleyemez;
çöken bir sunucu da. "Ölü adam anahtarı" (dead man's switch) düzeni: uygulama
düzenli olarak dışarıya "yaşıyorum" der, dışarıdaki hizmet bu ses kesilince
alarm verir. Healthchecks.io bu işi yapan, açık kaynaklı ve ücretsiz planı
olan bir hizmet (planın ayrıntısı hesap açılırken görülecek; burada
doğrulanmadı).

**Akış.** `Watchdog` iki dakikada bir (`INTERVAL_SECONDS = 120`) sağlık
kararını okur:

| Karar | Gözcüye giden | Sonuç |
|---|---|---|
| Sağlıklı | ping | Kontrol "up" kalır |
| İlk kötü okuma | ping, gövdesinde "uyarı: <neden>" | Kontrol "up" kalır, neden kayıtta görünür |
| Art arda ikinci kötü okuma | `/fail`, gövdesinde neden | Kontrol "down": alarm |
| Düzelince | ping | Kontrol "up": "düzeldi" mesajı |
| Kapanışta | `/log`, "Uygulama kapatıldı" | Durum değişmez; ping kesildiği için süre dolunca alarm |
| Uygulama çöktü / sunucu kapandı | hiçbir şey | Süre dolunca alarm |

Tek kötü okumada alarm yok: akış bir an kopup REST yedeğine geçerken
(Faz 4) yanlış alarm çalmasın. Alarm ve düzelme bildirimi Telegram'a da bir
kez gider (Telegram kuruluysa).

**Healthchecks.io'daki ayar.** Süre (period) 5 dakika, tolerans (grace) 5
dakika. Uygulama iki dakikada bir ping attığı için bir ping kaçsa alarm
çalmaz; uygulama kapanırsa en geç ~10 dakikada çalar.

**Ping adresi sırdır.** Adresi bilen alarmı susturabilir ya da sahte alarm
çaldırabilir. Anahtar Zinciri'nde (`albsat-gozcu` / `ping-adresi`), sunucuda
sır dizininde durur. Kurulumda gizli girişle yazılır (ekranda görünmez),
hata metinlerinde yalnızca sunucu adı yazılır, arayüzde de öyle. Adres
doğrulaması: `https://`, sunucu adı, en az 8 karakterlik yol.

**Gözcü kurulu değilse** uygulama çalışır; Kâğıt işlem sekmesinde sarı
"Gözcü kurulu değil (kapanınca alarm yok)" rozeti durur.

## 4. Sağlık kararı (`Runtime.health_verdict`)

Sağlıksız sayılan durumlar ve eşikler:

* **Döngü takıldı:** canlı döngünün son turu 180 saniyeden eski
  (`LOOP_STALL_SECONDS`). Açılış uzlaştırması 15 dakikaya kadar sürebilir
  (`STARTUP_GRACE_SECONDS`); o sürede takılmış sayılmaz.
* **Piyasa verisi gelmiyor:** bir coinin son fiyatı 5 dakikadan eski
  (`DATA_STALE_SECONDS`). Akış yeni bağlanırken hiç fiyat gelmemiş coin de
  ancak açılıştan 5 dakika sonra sayılır.
* **Disk doluyor:** veri dizininde 200 MB'tan az yer (`MIN_FREE_BYTES`).
* **Denetimin kendisi patladı:** hata metni neden olarak yazılır, sağlıksız
  sayılır.

Karar `/api/saglik` ucunda (`calisma`), `albsat.cli.yoklama` komutunda ve
Docker'ın sağlık denetiminde aynıdır.

## 5. Günlük yedek (`core/backup.py`)

* **Ne:** `albsat.sqlite3` (bütün defterler, emir kayıtları, denetim kaydı,
  modlar) ve veri dizininin kökündeki `.json` ayar dosyaları (kural deposu,
  borsa filtreleri, komisyon, Telegram sohbet kimliği). **Sır yok** (sırlar
  Anahtar Zinciri'nde ya da sır dizininde), **mum verisi yok** (Parquet
  dosyaları yeniden indirilir, büyüktür).
* **Nasıl:** SQLite'ın çevrimiçi yedek API'si (`Connection.backup`); açık
  bağlantı ve WAL varken de tutarlı kopya. Kopyaya `PRAGMA integrity_check`
  uygulanır, arşive dosyaların SHA-256 özetleriyle bir `YEDEK-BILGI.json`
  eklenir.
* **Ne zaman:** uygulama açıkken saatte bir bakılır; son yedek 24 saatten
  eskiyse yenisi alınır. İlk bakış açılıştan 60 saniye sonra. Başarısız olursa
  bir kez bildirilir (Telegram) ve rozet sarıya döner.
* **Nerede:** `veri/yedek/albsat-yedek-YYYYMMDD-HHMMSS.tar.gz`, son 14 yedek
  tutulur. Sunucuda bu dizin sunucunun diskinde; sunucu kaybolursa yedek de
  kaybolur. Bu yüzden rehber yedeği ara sıra Mac'e indirmeyi anlatır (§12).
* **Sınama:** `--sina` arşivin özetlerini, dosya adlarını (dizin dışına yazma
  denemesi) ve veritabanının bütünlüğünü denetler.
* **Geri yükleme:** yalnızca uygulama kapalıyken (kilit ve port denetimi).
  Mevcut dosyalar silinmez, `yedek/geri-yukleme-oncesi-<zaman>/` altına
  taşınır. Açılışta borsayla uzlaştırılır.

## 6. Sunucu sır deposu (`core/keychain.py`)

`ALBSAT_SIR_DIZINI` ortam değişkeni tanımlıysa bütün sırlar (Binance
anahtarları, Telegram jetonu, gözcü adresi) o dizindeki dosyalara yazılır;
tanımlı değilse Mac'teki gibi Anahtar Zinciri. **Değişken sırrın yerini
söyler, sırrın kendisini taşımaz**; bu projenin "ortam değişkenine sır
konmaz" kuralı korunur.

Dosya deposunun kuralları:

* Dizin 700, dosyalar 600, sahibi uygulamanın kullanıcısı olmalı; değilse
  okunmaz, neden yazılır.
* Sembolik bağlantı reddedilir; kayıt adında yalnızca harf, rakam, `.`, `_`,
  `-`, `~`.
* Yazma atomik: geçici dosyaya yazılır, sonra yerine taşınır.

Sunucuda dizin `/etc/albsat/sirlar`. Sahibi konteynerdeki kullanıcı
(kimlik 10001); root da okuyabilir. Uygulamanın konteynerine **salt okunur**
bağlanır; yalnızca kurulum komutları (`kurulum` hizmeti) yazabilir.

**Şifrelenmemiş olması bilinçli.** SPEC §5 sunucuda "şifreli gizli dosya veya
ortam değişkeni" diyor. Şifreli dosyanın açılması için bir parola gerekir;
parola sunucuda durursa şifreleme bir şey katmaz, durmazsa uygulama her
yeniden başlamada (çökme, sunucu güncellemesi) biri parolayı girene kadar
kapalı kalır ve 7/24 hedefi düşer. Yerine koruma katmanları: dosya izinleri,
root olmayan ve salt okunur konteyner, anahtarın **sunucunun IP'siyle
kısıtlı** olması (anahtar çalınsa da başka yerden kullanılamaz), para çekme
izninin kapalı olması ve 10 USDT emir tavanı. Bu seçim Berk'e kartla soruldu
("Korumalı dosya" önerilen; "Parolalı dosya" ve "Dış sır kasası" diğer
seçenekler); cevap bekleniyor.

## 7. Sunucuda IP kısıtı zorunlu

Faz 6'da Mac'te IP kısıtsız canlı anahtar yalnızca uyarıydı (özel yarı Mac'te,
ev IP'si değişebiliyor). Sunucuda sabit IP var ve sır bir dosyada; kısıt
zorunlu. `live_key_problems(..., require_ip=True)` kısıtsız anahtarı
**engelleyen** sorun sayar; `LiveTrader` sır dizini tanımlıysa bunu
kendiliğinden ister. Kurulumda da (`canli-anahtar`) 6. adım sunucuda kısıtı
zorunlu olarak anlatır.

## 8. Tek kopya kilidi (`core/instance.py`)

Uygulama veri dizininde `.albsat-kilit` dosyasına `flock` ile özel kilit
alır; içine süreç numarası, amaç ve başlangıç zamanı yazılır. İkinci kopya
açılmaz ve nerede çalışan kopyanın durdurulacağını söyler. `canli-sina`
ve geri yükleme de kilit tutuluyken çalışmaz. Kilit süreç ölünce işletim
sistemi tarafından bırakılır; bayat kilit dosyası sorun olmaz.

Bu kilit **aynı makinedeki** iki kopyayı engeller. Mac ile sunucunun aynı
canlı hesapta aynı anda çalışmasını engelleyemez; onu geçiş sırası engeller
(§12: önce Mac durur, Mac'in canlı anahtarı Binance'ten silinir).

## 9. Docker

**Görüntü** (`Dockerfile`), üç aşama:

* `temel`: sürüm **ve özetle** sabitlenmiş `python:3.12-slim`, özetli kilit
  dosyasından (`pip install --require-hashes`) bağımlılıklar, kaynak kod.
* `sinama`: temel + test bağımlılıkları + testler; derlenirken bütün testler
  çalışır. `kurulum.sh baslat` önce bu aşamayı derler: testler geçmezse
  çalışan sürüm değişmez.
* `calisma`: temel + yalnızca kod, kimliği 10001 olan root olmayan kullanıcı,
  sağlık denetimi (`albsat.cli.yoklama`, 60 saniyede bir, açılışta 180 saniye
  hoşgörü).

**Çalıştırma** (`compose.yaml`):

* `network_mode: host` ve **yayınlanan port yok**. Uygulama yalnızca
  sunucunun `127.0.0.1`'ini dinler; internetten erişilmez. Güvenlik duvarı
  zaten yalnızca SSH'ye izin veriyor.
* `read_only` kök dosya sistemi, `/tmp` bellekte, bütün Linux yetkileri
  kapalı (`cap_drop: ALL`), `no-new-privileges`.
* `restart: unless-stopped`, `stop_grace_period: 60s`.
* Günlük döndürme: en fazla 5 × 10 MB.
* `init: true`: sinyaller uygulamaya iletilir, zombi süreç kalmaz.

**Sağlıksız konteyner yeniden başlatılmaz.** Docker sağlıksız konteyneri
yalnızca işaretler. Kendiliğinden yeniden başlatmak için Docker soketine
erişen ek bir konteyner gerekir; o konteyner sunucunun tamamına hâkim olur.
Ayrıca verisi gelmeyen bir uygulamayı yeniden başlatmak sorunu çözmez (sorun
genellikle ağda ya da borsada), her yeniden başlatmada uzlaştırma yapılır.
Onun yerine gözcü alarm verir, karar Berk'te.

**Düzgün kapanış hatası (bulundu, düzeltildi).** uvicorn `SIGTERM`'i yakalayıp
kapanır, sonra aynı sinyali yeniden yükseltir. Python'un varsayılan `SIGTERM`
davranışı süreci anında öldürdüğü için `serve.py`'deki kapanış bloğu hiç
çalışmıyordu: gözcüye not gitmiyor, canlı yürütücü durdurulmuyor, konteyner
143 koduyla çıkıyordu. Mac'te Control-C (`SIGINT`) etkilenmiyordu. Artık
`SIGTERM` bir istisnaya çevriliyor; test `test_sunucuda_durdurma_sinyali_duzgun_kapatir`.

## 10. Arayüze erişim: SSH tüneli

SPEC §5 sunucuda arayüzün internete açılmamasını, "Tailscale/WireGuard veya
HTTPS + güçlü parola + TOTP" arkasında durmasını istiyor. Seçilen yol **SSH
tüneli**: Mac'teki Terminal'de

```bash
ssh -N -L 8756:127.0.0.1:8756 root@SUNUCU_IP
```

açık kaldıkça tarayıcıda `http://127.0.0.1:8756/` sunucudaki arayüzü açar.
Gerekçe: sunucuyu yönetmek için SSH zaten gerekiyor ve anahtarla, şifreli
çalışıyor; yeni bir port, hizmet, hesap ya da parola eklemiyor. Tarayıcı
adresi `127.0.0.1` olduğu için arayüzün yerel koruması (Host denetimi, istek
başlığı) değişmeden çalışır. WireGuard/Tailscale aynı korumayı sağlar ama
ikisi de ek kurulum ve (Tailscale'de) dış hesap ister.

## 11. Sunucu hazırlığı (`sunucu/ilk-kurulum.sh`)

Yeni kiralanmış Ubuntu 24.04 / 22.04 ya da Debian 12 sunucuda root olarak
bir kez çalışır; yeniden çalıştırmak zararsızdır. Sır istemez, sır yazmaz,
uygulamayı başlatmaz. Sekiz adım, her biri ekrana yazılır:

1. Sistem güncellemesi ve otomatik güvenlik güncellemeleri.
2. Güvenlik duvarı (`ufw`): dışarıdan yalnızca SSH.
3. SSH anahtarı kayıtlıysa parolayla girişi kapatır (ayar `sshd -t` ile
   sınanmadan uygulanmaz).
4. Saat eşitleme (Binance zaman damgası farkını reddeder).
5. Bellek 2 GB'tan azsa 2 GB takas alanı (derleme ve testler için).
6. Docker, Docker'ın kendi deposundan.
7. Kodu `/opt/albsat`'a indirir (depo herkese açık, kimlik gerekmez); veri ve
   sır dizinlerini doğru sahip ve izinle açar; makineyi "sunucu" diye
   işaretler (`/etc/albsat/sunucu`).
8. Binance'e bu konumdan ulaşılıyor mu: **HTTP 451 kısıtlı ülke** (örneğin
   ABD; sunucu Avrupa'da yeniden açılmalı), 403 başka bir ret. Sonunda
   sunucunun genel IP'sini yazar: canlı anahtarın IP kısıtına bu yazılır.

## 12. Mac'ten sunucuya geçiş (sıra)

Bu bölüm sırayı ve gerekçesini verir. Berk'e verilecek adım dosyası bundan,
her adımda ne göreceğini ve ne basacağını yazarak hazırlanır; **sunucu
kiralama, gözcü hesabı ve sunucuda canlı anahtar için Berk'in yazılı onayı
adım dosyası verilmeden önce alınır.**

1. **Sunucu seçimi (Berk'in kararı).** Avrupa'da (Almanya, Finlandiya,
   Hollanda gibi; ABD değil), Ubuntu 24.04, en az 1 çekirdek, 2 GB bellek
   (1 GB takasla çalışır), 20 GB disk, **sabit IPv4**. Kiralarken SSH anahtarı
   eklenir (Mac'te `ssh-keygen` ile üretilir; özel yarısı Mac'te kalır).
2. **Hazırlık:** `ssh root@SUNUCU_IP`, sonra
   `curl -fsSL https://raw.githubusercontent.com/berkerden/alsat/main/sunucu/ilk-kurulum.sh -o ilk-kurulum.sh && bash ilk-kurulum.sh`.
   8. adım HTTP 200 demeli; IP not edilir.
3. **İlk başlatma:** `cd /opt/albsat && bash kurulum.sh baslat`. Görüntü
   derlenir, testler sunucuda çalışır, uygulama anahtarsız açılır (yalnızca
   izler).
4. **Gözcü:** Healthchecks.io'da kontrol (süre 5 dk, tolerans 5 dk), alarm
   kanalı (e-posta, isteğe göre Telegram), `bash kurulum.sh gozcu`, sonra
   `bash kurulum.sh gozcu-sina`. Mac'te kurulmuş bir gözcü varsa aynı adres
   kullanılmaz; sunucuya yeni kontrol açılır.
5. **Telegram (isteğe bağlı):** `bash kurulum.sh telegram`. Aynı bot iki
   yerde aynı anda komut okumasın: Mac'teki uygulama kapalıyken.
6. **Mac'i durdurma ve verinin taşınması:**
   * Mac'te coinler Sadece Öneri'deyken uygulama kapatılır (Control-C).
     Açık canlı pozisyon varsa stop ve hedefi borsada kalır.
   * Mac'te `bash kurulum.sh yedek` (uygulama kapalıyken de alınır).
   * Yedek dosyası sunucuya kopyalanır:
     `scp ~/Desktop/alsat/veri/yedek/<dosya> root@SUNUCU_IP:/opt/albsat/veri/yedek/`
   * Sunucuda `bash kurulum.sh durdur`, `bash kurulum.sh geri-yukle <dosya>`.
7. **Canlı anahtar (sunucuda yeni):** `bash kurulum.sh canli-anahtar`.
   Anahtar çifti **sunucuda** üretilir; Binance'te yeni anahtar oluşturulur,
   IP kısıtına sunucunun IP'si yazılır, para çekme kapalı. Mac'in özel yarısı
   sunucuya kopyalanmaz. Demo anahtarı gerekiyorsa aynı yolla
   (`demo-anahtar`).
8. **Mac'in canlı anahtarı Binance'ten silinir.** Bundan sonra Mac'teki kopya
   açılsa da canlı hesaba emir gönderemez; iki yürütücünün birbirinin
   emrini silmesi (Faz 6 devir notu §7.4) böylece imkânsızlaşır.
9. **Başlatma ve sınama:** `bash kurulum.sh baslat`, `bash kurulum.sh durum`,
   SSH tüneliyle arayüz. Açık pozisyon taşındıysa açılış uzlaştırması onu
   borsadaki emirlerle eşler (emirler hesaba aittir, anahtara değil; bu
   çıkarım, burada gerçek hesapla sınanmadı). `canli-sina` uygulama
   kapalıyken çalışır: `durdur`, `canli-sina`, `baslat`.
10. **7/24 ve alarm testi:** `reboot`; birkaç dakika sonra `durum` uygulamanın
    kendiliğinden açıldığını göstermeli. `durdur` sonrası ~10 dakikada
    "down" alarmı, `baslat` sonrası "up".
11. **Yedeği Mac'e indirme** (haftada bir yeterli):
    `scp root@SUNUCU_IP:/opt/albsat/veri/yedek/albsat-yedek-*.tar.gz ~/Desktop/alsat-yedekler/`
12. **Güncelleme:** `cd /opt/albsat && git pull && bash kurulum.sh baslat`.
    Testler sunucuda geçmezse çalışan sürüm değişmez.

Geri dönüş (sunucudan Mac'e) aynı sıranın tersidir: sunucu durur, yedek
Mac'e iner, Mac'te geri yüklenir, sunucunun anahtarı Binance'ten silinir,
Mac'te canlı anahtar yeniden kurulur.

## 13. Bağımlılık kilidi ve gizli bilgi taraması

* **Kilit:** `requirements.lock` ve `requirements-dev.lock`,
  `uv pip compile pyproject.toml [--extra dev] --universal --generate-hashes
  --python-version 3.12` ile üretildi. Her paketin sürümü ve özeti sabit;
  hem x86 hem ARM, hem Linux hem macOS için geçerli. Docker yalnızca kilitten
  kurar. Mac kurulumu (`kurulum.sh`) şimdilik `pyproject.toml`'dan kurmaya
  devam ediyor; Berk'in Mac'indeki Python 3.14 için kilit ayrıca üretilmedi.
* **Açık taraması:** `pip-audit -r requirements.lock` bilinen açık bulmadı
  (26 Eylül 2026). Kilit güncellenince yeniden çalıştırılmalı.
* **Gizli bilgi taraması** (`core/secretscan.py`): PEM özel anahtar, Telegram
  jetonu, Healthchecks ping adresi ve Binance anahtarına benzeyen 64
  karakterlik diziler (büyük-küçük harf ve rakam karışık; küçük harfli
  onaltılık özetler sayılmaz). İki yerde çalışır: `.githooks/pre-commit`
  kaydedilecek dosyaları tarar (etkinleştirme: `git config core.hooksPath
  .githooks`), `tests/test_gizli_tarama.py` depodaki bütün dosyaları tarar;
  testler her kurulumda ve Docker derlemesinde çalıştığı için kanca etkin
  olmasa da yakalanır. Bulgu yazarken sırrın kendisi yazılmaz. Bilerek
  yazılmış sahte test verisi satırına `gizli-tarama: sahte` yazılır.

## 14. Burada yapılan sınamalar

Bu ortamda Docker servisi başlatılabildi; ağ Binance'i engellediği için
uygulama burada "piyasa verisi gelmiyor" diyor, bu beklenen.

* **Görüntü:** derlendi; testler görüntünün içinde geçti.
* **Konteyner:** yalnızca `127.0.0.1:8756` dinliyor (dış adresten
  erişilemiyor), kullanıcı 10001, kök dosya sistemi ve `/sirlar` salt okunur,
  sır dosyası 600 / dizin 700.
* **Tek kopya:** ikinci kopya "zaten çalışıyor" deyip çıktı.
* **Çökme:** `SIGKILL` sonrası Docker yeniden başlattı, kilit yeniden alındı.
* **Gözcü uçtan uca** (sahte, TLS'li gözcü sunucusu): kurulum pingi →
  pingler → ilk kötü okumada "uyarı" → ikincide `/fail` → `durdur`'da `/log`
  "Uygulama kapatıldı".
* **kurulum.sh sunucu kipi:** `baslat` (derleme, testler, iki dakikaya kadar
  bekleyip durum yazma), `durum`, `yedek`, `durdur`, `geri-yukle` (Mac'te
  alınmış bir yedekle; dosya sahipleri 10001'e çevrildi).
* **Zamanlayıcı:** açılıştan 60 saniye sonra ilk yedeği aldı.
* **Sunucu hazırlığı:** `ubuntu:24.04` konteynerinde (ufw, systemctl ve
  timedatectl sahte) baştan sona çalıştı, Docker ve compose kuruldu, dizinler
  ve işaret dosyası doğru.
* **Arayüz:** gözcü ve yedek rozetleri dört durumda (kurulu değil, çalışıyor,
  ulaşılamıyor, "sorun var"; yedek var, yok, hata) gerçek tarayıcıda; 1280 px
  (oran 1 ve 2) ve 390 px (oran 2 ve 3), yatay taşma ve konsol hatası yok.

Denenemeyenler: gerçek Healthchecks.io, gerçek sunucuda `reboot`, sunucudan
Binance'e bağlantı, sunucuda canlı anahtar ve taşınan açık pozisyonun
uzlaştırılması.

## 15. Şartnameden sapmalar

1. **`.env.mac` / `.env.vps` yok.** Ortama göre değişen tek şey sırların yeri;
   o da `compose.yaml`'daki `ALBSAT_SIR_DIZINI`. Mac'te ayar dosyası gerekmez.
   `.env.example` buna göre yeniden yazıldı.
2. **Sunucuda sır dosyası şifreli değil** (§6; Berk'in cevabı bekleniyor).
3. **Arayüze SSH tüneliyle erişilir,** Tailscale/WireGuard ya da HTTPS +
   TOTP değil (§10).
4. **Sağlıksız konteyner kendiliğinden yeniden başlatılmaz;** gözcü alarm
   verir (§9).
5. **Mac'te `launchd` ile otomatik başlatma yapılmadı** (SPEC'te isteğe
   bağlı). 7/24'ün yeri sunucu; Mac'te uygulama `kurulum.sh arayuz` ile
   açılır.

## 16. Açık kalanlar

* Binance'in IP kısıtı kuralı Berk'in hesabında hâlâ görülmedi (Faz 6 devir
  notu §5.6); sunucuda kısıt zaten zorunlu olduğu için sonucu etkilemiyor.
* Bot için Binance alt hesabı önerisi (SPEC §5) README'ye yazılmadı; alt
  hesap koşulları hesap türüne göre değişiyor, doğrulanmadı.
* Mac kurulumu kilit dosyasından kurmuyor (§13).
* Yedek sunucunun diskinde; sunucu dışına kendiliğinden kopyalanmıyor. Elle
  indirme §12.11'de. Bir dış depolama (S3 benzeri) eklemek hesap ve sır
  gerektirir; istenirse ayrı karar.

## 17. Onay

Faz 7 onayı Berk'te. Kabul kriterinin gerçek karşılığı (gerçek gözcüyle alarm
testi; tam kapsamda sunucuda 7/24 ve `reboot` denemesi) Berk'in kararına ve
onayına bağlı adımlardır.
