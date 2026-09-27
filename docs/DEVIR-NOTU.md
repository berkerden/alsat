# Devir notu — Faz 7'den sonrasına

27 Eylül 2026. Bu not, koda ve diğer belgelere bakarak öğrenilemeyecek
şeyleri yeni oturuma aktarmak içindir. Şartname `docs/SPEC.md`, mimari
kararlar `docs/FAZ0-MIMARI.md`, fizibilite sonucu `docs/FAZ1-FIZIBILITE.md`,
Faz 2 sonucu `docs/FAZ2-SONUC.md`, Faz 2 yöntemi `docs/FAZ2-ORUNTU-MOTORU.md`,
Faz 3 yöntemi `docs/FAZ3-ONERI-MOTORU.md`, Faz 4 yöntemi
`docs/FAZ4-RISK-KAGIT-TELEGRAM.md`, Faz 5 yöntemi
`docs/FAZ5-DEMO-EMIR-YURUTME.md`, Faz 6 yöntemi `docs/FAZ6-CANLI.md`,
**Faz 7 yöntemi ve sunucuya geçiş sırası `docs/FAZ7-SUNUCU.md`**.

**Şartnamedeki bütün fazlar (0-7) onaylandı.** SPEC §10'da Faz 8 yok. Bundan
sonraki işin ne olacağını Berk söyler; bir sonraki oturum işe başlamadan önce
ona sorar. Olası yönler (Berk'in kararı; hiçbiri başlatılmadı):

* **Sunucuya geçiş:** kod ve rehber hazır (`FAZ7-SUNUCU.md` §12). Önce
  sunucudaki sır deposu sorusu yeniden sorulur (§7.2), sonra kiralama, gözcü
  ve sunucuda canlı anahtar için ayrı ayrı yazılı onay alınır.
* **Kural arayışı:** Faz 2'de kabul edilen kural çıkmadı; uygulama bugün
  yalnızca izler ve öneri üretmez. Yeni bir tarama turu çoklu test sorunudur
  (§5, Faz 2-4 tuzakları); kural uydurulmaz. **İkinci tur yapıldı ve
  kapandı:** Berk 27 Eylül 2026'da istedi (*"evet"*), 28 Eylül'de Mac'te
  çalıştırdı. 730 gün, 12.021 deneme (Faz 2'nin 6.372'si dahil), kabul
  edilen örüntü yok, en yakın aday eşiğin 320 katı uzağında
  (`docs/TUR2-SONUC.md`). Ön kayda göre aynı arama yeniden önerilmez. Kural
  deposu birikimli aday sayısını (12.021) taşıyor; sonraki her tarama onu
  kendiliğinden düzeltmeye ekliyor.
* **Günlük trend testi (yazıldı, Berk'in çalıştırması bekleniyor):** Berk
  "işe yarar bir şeyler bulmak için ne önerirsin" sorusundan sonra karar
  kartında "Günlük trend testi"ni seçti (28 Eylül 2026). Beş klasik kural,
  günlük grafik, BTC ve SOL, al-ve-tut kıyası, bir kez
  (`bash kurulum.sh trend`). Ön kayıt `docs/TREND-ONKAYIT.md` çalıştırmadan
  önce gönderildi; önceki 12.021 adayın bu aileye neden sayılmadığı §2'de.
  Berk'e söylenen: beklenti al-ve-tut'u geçmek değil büyük düşüşlerin bir
  kısmından kaçmak; veri kısa, sonuç "Belirsiz" çıkabilir.
* **Mac'te sınanmayanlar:** Telegram, `caffeinate`/`pmset`, Yarı Otomatik
  önerisi (§7).

Bu not bir sonraki büyük iş bitince aynı sekiz başlıkla baştan yazılır.

## 1. Berk nasıl çalışıyor

* Mac kullanıyor (Retina), kod `~/Desktop/alsat` altında. Terminal, git ve
  GitHub akışları ona tanıdık değil.
* Kendi bilgisayarında atması gereken adımlar tek tek, sade ve elinden
  tutarak anlatılmalı: hangi tuşa basacağı, ekranda ne göreceği, neyi
  kopyalayacağı. Çıplak komut listesi verme. Uzun adım listesi dosya olarak
  verilir (`/mnt/project-files/faz6/FAZ6-MAC-ADIMLARI.md`,
  `/mnt/project-files/faz7/FAZ7-MAC-ADIMLARI.md`). Ekranda görünecek her
  satırı koddaki metinle karşılaştırıp yaz.
* **Komutu her zaman tam satır olarak ver:**
  `cd ~/Desktop/alsat && git pull && bash kurulum.sh <seçenek>`

  | Seçenek | Ne yapar |
  |---|---|
  | (boş) | Veriyi tazeler + fizibilite + örüntü taraması |
  | `tarama` | İnternete çıkmaz; yalnızca örüntü taraması |
  | `teshis` | Tarama + maliyetsiz teşhis turu |
  | `tur2` | İkinci kural arama turu: 730 günlük 15m/1h verisi + Faz 2'nin 6.372 adayı sayılarak tarama; bir kez çalışır (`TUR2-ONKAYIT.md`) |
  | `trend` | Günlük trend testi: BTC ve SOL'un bütün günlük mumları, beş klasik kural, al-ve-tut kıyası; bir kez çalışır (`TREND-ONKAYIT.md`) |
  | `arayuz` | Kurulumu ve testleri kontrol edip arayüzü açar (canlı fiyat akışıyla) |
  | `telegram` | Telegram botunu kurar (jeton Anahtar Zinciri'ne) |
  | `anahtar` | Salt okuma Binance anahtarı kurar, komisyonu ölçer |
  | `komisyon` | Kayıtlı anahtarla komisyonu yeniden ölçer |
  | `demo-anahtar` | Demo Mode anahtarı kurar, hesabı ve Demo komisyonunu okur |
  | `demo-sina` | Demo'ya dolmayacak bir sınama emri gönderip iptal eder (sorarak) |
  | `canli-anahtar` | **Canlı** işlem anahtarını kurar; yalnızca okur |
  | `canli-sina` | **Canlı** hesaba dolmayacak bir sınama emri gönderip iptal eder (coin adını yazdırarak) |
  | `gozcu` | Healthchecks.io ping adresini kaydeder (gizli girişle, Anahtar Zinciri'ne) |
  | `gozcu-sina` | Gözcüye "sorun var", 30 sn sonra "düzeldi" gönderir |
  | `yedek` | Hemen yedek alır ve yedekleri listeler |
  | `geri-yukle <dosya>` | Yedeği geri yükler (uygulama kapalıyken) |

  Sunucuda (`/etc/albsat/sunucu` varsa) aynı betik Docker'la çalışır:
  `baslat`, `durum`, `durdur`, `gunluk` ve yukarıdaki kurulum komutları.
* **Terminal'de soru soran bir adım ona donmuş gibi görünür.** Önceden hangi
  soruyu göreceğini ve ne basacağını yaz. Gizli girişte ekranda hiçbir şey
  görünmediğini ayrıca söyle.
* Uzun süren her adım ekrana ilerleme yazmalı.
* **Bilmediği terimi kısa soruyla soruyor** ("PR ne", "bu ne için izin").
  Faz 7'de iki `WebFetch` isteği ona izin sorusu olarak göründü ve ne
  olduğunu sordu. İzin isteyen bir araç kullanmadan önce ne için olduğunu
  bir cümleyle söyle. Terim kullanma; kullanırsan açıkla.
* **Bir adımın gerekçesini soruyor.** Dış hesap istenince *"buna neden
  gerek var zaten telegram üzerinden bildirim almayacak mıyım"* dedi;
  gerekçe anlatılınca *"evet"* dedi. Onay istemeden önce neden gerektiğini
  ve atlanırsa ne olacağını yaz.
* Kartları bazen düğmeyle değil yazarak cevaplıyor; bazılarını hiç
  cevaplamıyor (Faz 7'de sunucu sır kartı). Cevapsız kartı unutma, gerektiği
  anda yeniden sor.
* Sonucu kısa bildiriyor: *"bütün down ve up mesajları doğru şekilde
  geldi"*; istenen dakika bilgisini vermedi. Kanıt gerektiren şeyi ayrıca ve
  tek başına iste.
* **İstenen yazılı onayı beklemeden adımları kendisi çalıştırabiliyor**
  (Faz 6). Onay, adım dosyasını vermeden **önce** alınır; uygulamanın kendi
  soruları (coin adını yazdırma, onay penceresi) son güvencedir, gevşetilmez.
* Kısa ve net soruyu seviyor; seçenekli sorularda bir öneri işaretlenince
  hızlı karar veriyor.
* Kod GitHub'da `berkerden/alsat`, `main` dalında. Doğrudan `main`'e
  gönderiliyor; Berk'in bilgisayarında da yedeği var.
* Mac'inde Python 3.14.7 var.

## 2. Çalışma ortamının kısıtları ve doğrulama kuralı

* Ağ politikası **Binance adreslerini engelliyor** (proxy 403). Gerçek
  bağlantı gerektiren her şey Berk'in Mac'inde ilk kez çalışır; emir
  yürütme sahte borsayla (`tests/fake_binance.py`) sınanır. Burada çalışan
  uygulama bu yüzden "piyasa verisi gelmiyor" der; arıza değil.
* PyPI erişilebilir. Proje 3.12+ istiyor; sistemin `python3`'ü 3.11,
  `kurulum.sh` 3.12+ bir Python'u kendisi buluyor. Gizli bilgi kancası
  standart kütüphaneyle çalıştığı için 3.11'de de çalışır.
* **Docker burada çalıştırılabiliyor:** `dockerd` elle arka planda
  başlatıldı (Faz 7). Ara vekil HTTPS'i yeniden imzaladığı için görüntü
  derlemesi vekilin kök sertifikası olmadan pip'te düşer; Faz 7'de yalnızca
  bu ortamda kullanılan bir Dockerfile kopyasına sertifika
  `--build-context` ile eklendi. **O sertifikayı ve kopyayı depoya koyma.**
* `ssh` kurulu değil; bir sunucuya buradan bağlanılmadı.
* `git push` yan dala da gidiyor; uzak dalı silmek vekil tarafından
  reddedildi (`claude/faz7-sunucu` dalı GitHub'da kaldı, içeriği `main`'de,
  silinebilir). Faz 6'da bir `push` 403 verdi; `add_repo` çağrılıp yeniden
  denenince geçti.
* Klonladıktan sonra `git config core.hooksPath .githooks` çalıştır: kayıt
  öncesi gizli bilgi taraması etkinleşir.
* `WebFetch` her yeni adres için Berk'e izin sorar (§1).
* macOS'a özgü kod (`security`, `caffeinate`, `pmset`) burada çalışmaz;
  testler sahte süreçlerle sınar.

**Doğrulama kuralı:** Berk'e "çalıştır" demeden önce depoyu **GitHub'dan
geçici bir dizine temiz klonlayıp** tek bir adımla (`bash kurulum.sh gozcu`
gibi) Python bulma, sanal ortam, bağımlılık ve testleri baştan çalıştır;
macOS dışında tek adım "yalnızca Mac'te" deyip temiz çıkar. Buradaki
çalışma dizininde testlerin geçmesi kanıt değil. Bu ortamda
`/etc/albsat/sunucu` dosyası varsa `kurulum.sh` sunucu kipine geçer; temiz
klon denemesinden önce olmadığından emin ol.

**Arayüzü doğrulamanın yolu:** Chromium `/opt/pw-browsers/chromium`.
Faz 7'de Python Playwright geçici bir sanal ortama `pip install playwright`
ile kuruldu; Node Playwright da genel kurulu
(`NODE_PATH=/opt/node22/lib/node_modules node betik.js`). Uç yanıtlarını
`page.route` ile değiştirmek farklı durumları (gözcü kurulu değil, hata,
"sorun var") tek sunucuyla göstermeyi sağlar. 1280 px'te oran 1 ve 2, 390
px'te 2 ve 3; yatay taşma ve konsol hatası. Veri dizini boşsa
`/api/oneriler` 404 döner; düzenekten, arıza değil.

## 3. Berk'in Faz 7'de verdiği kararlar — kendi cümleleriyle

* **Faz 7'yi başlattı** (26 Eylül): *"tamam devam"*.
* **Kapsam** (26 Eylül, kartı yazarak cevapladı): *"tamam yap ama şu an için
  sunucuda iş yapmak zorunda değilim istersen sunucu istersem mac üstünden
  devam edebilirim değil mi"*. Anlamı "Hazırla, kiralama": sunucu kurulumu
  yazılır ve burada sınanır, sunucu kiralanmaz, Mac'te devam edilir.
* **Sunucuda sır deposu kartı cevaplanmadı.** Kodda önerilen "Korumalı
  dosya" var (§6).
* **Healthchecks.io hesabı** (26 Eylül): önce *"buna neden gerek var zaten
  telegram üzerinden bildirim almayacak mıyım"*, gerekçeden sonra
  *"evet"*.
* **Alarm testi** (27 Eylül): *"bütün down ve up mesajları doğru şekilde
  geldi"*.
* **Faz 7, 27 Eylül 2026'da onaylandı.** Berk'in cümlesi: *"onaylıyorum"*.

Önceki fazlardan hâlâ geçerli olan kararları: Testnet yerine Demo Mode;
kapsam A (BTCUSDT ve SOLUSDT, yalnızca 15m ve 1h); 100 USDT doğrulama
bütçesi; Faz 6 kapsamı "Kapı + mini emir" (Tam Otomatik kapıda kilitli);
elle emir kararını Claude'a bırakması (Faz 4, *"sen profesyonel bir
yaklaşımla getiri sağlama amacına uygun şekilde belirleyebilir misin"*).

Berk'in çalışma yöntemi hakkındaki kuralı (21 Eylül): *"Yeni bir oturum ile
yeni fazlara geçiş yap. Bağlam şişip kalite düşmesin bu sayede. Her oturumda
öğrendiğimiz, dikkat ettiğimiz, sonraki faza aktarılması gerekecek bilgileri
toparla ve sonraki faz için sonraki oturuma taşı, eksik bilgi olmadan devam
etmiş olsun."*

## 4. Faz 7 ne yaptı (tek paragraf)

Uygulamanın 7/24 çalışabilmesi için gereken altyapı yazıldı, sunucu
kiralanmadı: **günlük yedek** (SQLite çevrimiçi yedek, bütünlük denetimi,
SHA-256 bildirgesi, son 14, silmeden geri yükleme), **dış gözcü**
(Healthchecks.io'ya iki dakikada bir ping; iki kötü okumada `/fail`,
kapanışta `/log`), **sağlık kararı** (döngü, piyasa verisi, disk), **sunucu
sır dizini** (`ALBSAT_SIR_DIZINI` yalnızca yeri söyler), **sunucuda zorunlu
IP kısıtı**, **tek kopya kilidi**, **Docker** (kilitli bağımlılıklar,
derlemede testler, root olmayan, salt okunur, yalnızca 127.0.0.1),
**sunucu hazırlık betiği** (`sunucu/ilk-kurulum.sh`), `kurulum.sh` sunucu
kipi, **gizli bilgi taraması** (kanca + her test turunda depo taraması) ve
**Mac'ten sunucuya geçiş sırası**. Toplam **725 test**. Docker'da burada
uçtan uca sınandı (çökünce kalkma, ikinci kopya engeli, sahte gözcüyle alarm
yolu, düzgün kapanış, Mac yedeğinin sunucuda geri yüklenmesi). Berk'in
Mac'inde gerçek Healthchecks.io ile alarm testi geçti. Ayrıntı ve
gerekçeler `docs/FAZ7-SUNUCU.md`.

**Eldeki komut satırı araçları**

| Araç | Ne yapar | Ağa çıkar mı |
|---|---|---|
| `albsat-fizibilite` | Veri indirir, filtreleri önbelleğe yazar, fizibilite taraması | Evet |
| `albsat-oruntu` | Örüntü keşfi, istatistik, backtest, rapor, kural deposu (`--maliyetsiz`) | Hayır |
| `albsat-arayuz` | Arayüz + canlı döngü + yürütücüler + yedek + gözcü, yalnızca 127.0.0.1 | Evet |
| `albsat-telegram` | Telegram kurulumu, deneme mesajı, kaldırma | Evet (Telegram) |
| `albsat-anahtar` | Salt okuma anahtar kurulumu, komisyon ölçümü | Evet (2 imzalı okuma) |
| `albsat-demo` | Demo anahtar kurulumu; `--sina`; `--sil` | Evet (Demo) |
| `albsat-canli` | Canlı anahtar kurulumu; `--sina`; `--sil` | Evet (**canlı**) |
| `python -m albsat.cli.gozcu` | Gözcü kurulumu; `--sina`; `--sil` | Evet (Healthchecks) |
| `python -m albsat.cli.yedek` | Yedek al; `--listele`, `--sina`, `--geri-yukle` | Hayır |
| `python -m albsat.cli.yoklama` | Çalışan uygulamanın sağlığı (Docker sağlık denetimi) | Yalnızca 127.0.0.1 |
| `python -m albsat.core.secretscan` | Gizli bilgi taraması (`--staged`: kanca) | Hayır |
| `albsat-tls-teshis` | Sertifika zinciri teşhisi | Evet |

## 5. Faz 7'de öğrenilen, koda bakarak görülmeyecek tuzaklar

1. **uvicorn `SIGTERM`'i yakalayıp kapanır, sonra aynı sinyali yeniden
   yükseltir.** Python'un varsayılan `SIGTERM` davranışı süreci anında
   öldürdüğü için `serve.py`'deki `finally` hiç çalışmıyordu: gözcüye not
   gitmiyor, canlı yürütücü durmuyor, konteyner 143 ile çıkıyordu. Mac'te
   Control-C (`SIGINT`) etkilenmiyordu, bu yüzden altı faz boyunca
   görülmedi. `serve.py` sinyali `StopRequested`'a çeviriyor; uvicorn'u
   çağıran yeni bir giriş noktası yazarsan aynısını yap.
2. **Gözcü bilerek kapatmayı çökmeden ayıramaz.** Mac'te uygulama her
   kapandığında 10-12 dakika sonra "down" gelir. Berk'e kapattıktan sonra
   Healthchecks.io'da **Pause**'a basması söylendi; duraklatılan kontrol
   ilk pingte kendiliğinden devam eder. Otomatik susturma yönetim API
   anahtarı (ikinci bir sır) ister, yazılmadı.
3. **Akış yeni bağlanırken hiç fiyat gelmemiş coin hemen "veri yok"
   sayılıyordu;** her açılışta ilk yoklama yanlış uyarı veriyordu. Artık
   açılıştan 5 dakika geçmeden sayılmıyor (`runner.stale_symbols`).
4. **Gizli bilgi taraması test verisine de bakar.** 64 karakterlik
   büyük-küçük harf ve rakam karışık bir dizi Binance anahtarı sayılır;
   test sabitlerini parçalardan kur ya da satıra `gizli-tarama: sahte` yaz.
   Taramayı gevşetme.
5. **Tek kopya kilidi yalnızca aynı makineyi korur.** Mac ile sunucunun aynı
   canlı hesapta birbirinin emrini silmesini geçiş sırası engeller: önce Mac
   durur, sonra Mac'in canlı anahtarı Binance'ten silinir
   (`FAZ7-SUNUCU.md` §12).
6. **Sunucuda `canli-sina` ve geri yükleme uygulama kapalıyken çalışır**
   (kilit). Sıra: `durdur`, komut, `baslat`.
7. **Healthchecks.io sınırları:** dakikada 5'ten fazla ping atılmaz; ping
   gövdesi 100 kB'ta kesilir; `/log` durumu değiştirmez. Ücretsiz plan $0,
   20 kontrol. Hesap e-postayla, parolasız açılır; hazır "My First Check"
   gelir.
8. **Süreç öldürme:** `pkill -f desen` kendi kabuğunu da öldürür (çıkış
   144), çünkü kabuğun komut satırı da deseni içerir. Sunucuyu `setsid` ile
   başlatıp PID dosyasıyla durdur.
9. **Lint ve tip tabanı:** `ruff check src tests` 30, `mypy src` 35 hata (21
   dosya), hepsi Faz 1-3 dosyalarında. Faz 4-7 kodu temiz; yeni kodu temiz
   tut.

**Faz 6'dan taşınan tuzaklar (hâlâ geçerli):** gerçek sınama emri 7
USDT'den küçük olamaz; zararla kapanan her işlem aynı coinde soğumayı
başlatır; tavan bağlayıcıysa kartta "tutar sınırı" yazar; izin okuması bir
saatten eskiyse yeni giriş gitmez; canlı yürütücü ve piyasa akışı aynı IP
istek bütçesini paylaşır, `canli-sina` arayüz açıkken çalıştırılmaz;
Binance'in IP kısıtı kuralı Berk'in hesabında görülmedi; beş saniyelik
yenileme yazılan alanları korumalı.

**Faz 5'ten:** `canWithdraw` hesabın bayrağıdır, anahtarın izni değil
(`apiRestrictions` kullanılır); iptal olayında emrin kimliği `C`'dedir;
satış miktarı alış miktarı değildir; sonucu bilinmeyen istekte yeni
kimlikle yeniden gönderim yok; Demo defteri canlıdan ayrıdır;
`/api/demo/durum` ve `/api/canli/durum` Binance'e istek göndermez.

**Faz 2-4'ten:** yazma uçları yerel korumadan geçer (`X-Albsat-Istek: 1`,
JSON, Host 127.0.0.1; `TestClient`'a `base_url="http://127.0.0.1"`);
`LiveRunner.tick()` her akış olayında çalışır; spread ısınması 5 dakika;
sayılar nokta ondalıkla; sırlar hata metninde ve denetim kaydında
maskelenir; `Decimal` için `format_for_api`; Retina'da canvas boyutu geri
okunmaz; "hâlâ bozuk" derse önce eski sekmeyi düşün; kapanmamış mum tahmine
giremez; fiyat, miktar ve bakiyede `float` yok; **yeni tarama turu çoklu
test sorunudur.**

## 6. Danışılmadan değiştirilmemesi gereken seçilmiş varsayılanlar

**Faz 7'de seçilenler** (gerekçeler `FAZ7-SUNUCU.md`):

1. **Gözcü:** ping iki dakikada bir; iki art arda kötü okumada `/fail`;
   Healthchecks'te süre 5 + tolerans 5 dakika. Ping adresi sırdır.
2. **Sağlık eşikleri:** döngü 180 sn, piyasa verisi 300 sn, boş disk 200 MB,
   açılış uzlaştırması için 900 sn hoşgörü.
3. **Yedek:** günde bir, son 14; yalnızca `albsat.sqlite3` ve veri kökündeki
   `.json` dosyaları. **Sır ve mum verisi yedeğe girmez.** Geri yükleme
   mevcut dosyaları silmez, kenara taşır.
4. **Sır deposu:** Mac'te Anahtar Zinciri; sunucuda izinli dizin (700/600,
   sahip uygulama kullanıcısı, sembolik bağlantı yok). Ortam değişkeni
   yalnızca yeri söyler. Şifreli değil (gerekçe §6); Berk'in kartı
   cevapsız.
5. **Sunucuda IP kısıtsız canlı anahtar engellenir;** Mac'te yalnızca uyarı.
6. **Sunucuda arayüze SSH tüneliyle erişilir;** port açılmaz. Sağlıksız
   konteyner kendiliğinden yeniden başlatılmaz, gözcü alarm verir.
7. **Docker:** kimlik 10001, salt okunur kök, `cap_drop: ALL`,
   `restart: unless-stopped`, günlük 5 × 10 MB, kurulumda testler
   geçmezse çalışan sürüm değişmez. Bağımlılıklar yalnızca özetli kilitten.

**Önceki fazlardan:**

8. **Emir tavanı varsayılan 10 USDT**; `config/default.yaml` ile
   `execution/live.py` → `DEFAULT_CAP_USDT` aynı olmalı.
9. **Kapıyı elle aşan yol yok;** kapıda "ortalama net > 0" koşulu var.
10. **Yeniden başlatmada her coin Sadece Öneri'de açılır.**
11. İzin denetimi 30 dakikada bir; yeni giriş için izin ≤ 1 saat.
12. Yarı ve Tam Otomatik yalnızca Canlı işlem sekmesinden ve coin adı
    yazılarak açılır.
13. Ayrı adlar: `albsat-binance-canli`, `albsat-canli-`, `canli_`
    tabloları; Demo ile canlı hiç karışmaz.
14. Giriş OTOCO, `LIMIT_MAKER`; piyasa emri kullanılmaz; kısmi dolumda en
    fazla 20 sn korumasız; stopun altında korumalı `LIMIT IOC`, kayma en
    fazla %0.5. Belirsiz sonuçta yeniden gönderim yok.
15. Bir yerde risk sınırı aşılınca bütün coinler Sadece Öneri'ye iner.
16. Bot bütçesi 100 USDT; borsada elle verilen emirlere dokunulmaz;
    `DELETE /api/v3/openOrders` izin listesinde yok.
17. ACİL DURDUR pozisyon kapatmaz; elle emirler kural performansına ve
    kapıya sayılmaz; kabul edilmemiş adaylar otomatik işlenmez.
18. Arayüz derleme adımsız düz HTML/CSS/JS (Node/npm kurdurma); kapsam A.

## 7. Yarım kalanlar ve sonraya taşınan uyarılar

1. **Sunucuya geçiş yapılmadı.** Geçilecekse sıra `FAZ7-SUNUCU.md` §12:
   önce sır deposu Berk'e yeniden sorulur, sonra sunucu kiralama (Avrupa;
   ABD'den Binance HTTP 451 verir), sunucu için ayrı gözcü kontrolü ve
   sunucuda üretilen, IP kısıtlı yeni canlı anahtar için **her birinde
   yazılı onay**. Mac'in canlı anahtarı geçişten sonra Binance'ten silinir.
   Sunucuda `reboot` denemesi ve taşınan pozisyonun uzlaştırılması hiç
   denenmedi.
2. **Sunucuda sır deposu kartı cevapsız** (seçenekler: "Korumalı dosya"
   önerilen, "Parolalı dosya", "Dış sır kasası").
3. **Kabul edilmiş kural yok** (Faz 2). Uygulama yalnızca izler; Tam
   Otomatik kapıda kilitli. Kural uydurma, elle emri kapıya sayma.
4. **Berk'in Mac'inde hâlâ sınanmayanlar:** uygulamanın Telegram botu (Faz
   6 itibarıyla kurulmamıştı; Faz 7'de gözcü alarmı için Healthchecks'in
   kendi Telegram botunu isteğe bağlı olarak önerdik, kurup kurmadığını
   bilmiyoruz), `caffeinate` ve `pmset`, izin bozulunca canlı modların
   kapanması, Yarı Otomatik önerisi, Tam Otomatik.
5. **Yalnızca kaos testiyle sınanan emir yolları** (kısmi dolum → OCO,
   stopun altında korumalı çıkış, yeniden koruma, uykudan sonra uzlaştırma,
   429/418) gerçekte görülmedi.
6. **Açık kalan küçük işler:** gözcünün bilerek kapatmada susması (yönetim
   API anahtarı ister); Mac kurulumunun kilit dosyasından kurmaması; bot
   için Binance alt hesabı önerisi (SPEC §5, doğrulanmadı); yedeğin sunucu
   dışına kendiliğinden kopyalanmaması; `claude/faz7-sunucu` uzak dalının
   silinmesi; Mac'te `launchd` (SPEC'te isteğe bağlı, yapılmadı).
7. **Berk'in hesaplarında kalanlar:** canlı hesapta alış komisyonundan küçük
   bir BTC artığı (toz) ve Berk'in kendi SOL emri (uygulama dokunmadı); Demo
   hesabında Faz 5'ten elle #2 kendiliğinden kapanmış olabilir. Yeni oturum
   bunları arıza sanmasın.
8. **Resmi SDK kullanılmadı** (gerekçe `FAZ5-DEMO-EMIR-YURUTME.md` §9);
   `binance-connector` "deprecated", kullanma.

## 8. Güvenlik (bunlar hiçbir fazda düşmez)

* **TLS sertifika doğrulaması hiçbir koşulda, geçici olarak bile
  kapatılmayacak.** Berk'in ağında HTTPS'i yeniden imzalayan bir katman var;
  uygulama `truststore` ile macOS güven deposunu kullanıyor. WebSocket
  istemcileri ve gözcü de aynı güven deposunu kullanıyor. Sunucuda sistemin
  güven deposu kullanılır.
* **Sırlar** (Binance anahtarları `albsat-binance`, `albsat-binance-demo`,
  `albsat-binance-canli`; Telegram jetonu `albsat-telegram`; gözcü adresi
  `albsat-gozcu`) Mac'te Anahtar Zinciri'nde, sunucuda izinli sır dizininde.
  Depoda, ortam değişkeninde, günlükte, denetim kaydında, arayüzde, yedekte
  yoklar. Özel yarı üretildiği makinede kalır; Mac'teki sunucuya kopyalanmaz.
* Berk 21 Eylül 2026'da sohbete bir Binance API anahtarı yapıştırmıştı;
  kullanılmadı, hiçbir yere yazılmadı, silmesi söylendi. Anahtar ya da ping
  adresi isteme, yazdırma, dosyaya koyma; ikisi de yalnızca Terminal'e gizli
  girişle yazılır.
* **Gerçek para ile emir gönderecek, para harcatacak, dış hesap açtıracak ya
  da API anahtarı gerektirecek her adım öncesinde Berk'in yazılı onayı
  alınır**, adım dosyası verilmeden önce. Kart düğmesi yetmez.
* **Emir gönderen anahtarda çekim izni kapalı olmalı** ve bu `apiRestrictions`
  → `enableWithdrawals` ile denetlenmeli. Margin, vadeli, opsiyon ve transfer
  izinleri de kapalı. Sunucuda IP kısıtı zorunlu.
* **Mac ve sunucu aynı canlı hesapta aynı anda çalışmaz.**
* İmzalı istemciler izin listesiyle çalışır; yalnızca kendi önekli emirlerini
  gönderir ve iptal eder.
* Binance'e giden istek hacmi sınırlı (yerel tavan 600 ağırlık/dk, 429'da
  bekle, 418'de dur, emir sayısı sınırının %50/%80'inde kapı kapanır).
  Beklenmedik büyüklükte bir iş çıkarsa program istek göndermeden durur.
* Komisyon oranı, sembol filtresi veya limit koda sabit yazılmaz.
* Arayüz yalnızca `127.0.0.1` dinler; sunucuda da internete port açılmaz.
* Depoya sır girmesin: gizli bilgi taraması kanca ve test olarak çalışır;
  gevşetilmez.
* Kaldıraç, margin, futures ve borçlanma kodu yok ve olmayacak. Emir yürütme
  için Binance MCP sunucusu kullanılmaz (SPEC §11).
