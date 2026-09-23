# Devir notu — Faz 4'ten Faz 5'e

23 Eylül 2026. Bu not, koda ve diğer belgelere bakarak öğrenilemeyecek
şeyleri yeni oturuma aktarmak içindir. Şartname `SPEC.md`, mimari kararlar
`docs/FAZ0-MIMARI.md`, fizibilite sonucu `docs/FAZ1-FIZIBILITE.md`,
Faz 2 sonucu `docs/FAZ2-SONUC.md`, Faz 2 yöntemi `docs/FAZ2-ORUNTU-MOTORU.md`,
Faz 3 yöntemi `docs/FAZ3-ONERI-MOTORU.md`, **Faz 4 yöntemi
`docs/FAZ4-RISK-KAGIT-TELEGRAM.md`**.

**Faz 5'in işi (SPEC §10):** emir yürütme, **Binance Demo Mode** üzerinde.
Faz 5'e başlamadan önce `FAZ0-MIMARI.md`'deki OTOCO ve ORDERS bulgularını
(§7, madde 2-4) oku.

## 1. Berk nasıl çalışıyor

* Mac kullanıyor (Retina), kod `~/Desktop/alsat` altında. Terminal, git ve
  GitHub akışları ona tanıdık değil.
* Kendi bilgisayarında atması gereken adımlar tek tek, sade ve elinden
  tutarak anlatılmalı: hangi tuşa basacağı, ekranda ne göreceği, neyi
  kopyalayacağı. Çıplak komut listesi verme. Uzun adım listesi dosya olarak
  verilir (Faz 4'te `/mnt/project-files/faz4/FAZ4-MAC-ADIMLARI.md` işe yaradı;
  Berk dosyanın satırlarına yorum yazarak cevap verdi).
* Güncelleme komutu tek satır:
  `cd ~/Desktop/alsat && git pull && bash kurulum.sh <seçenek>`

  | Seçenek | Ne yapar |
  |---|---|
  | (boş) | Veriyi tazeler + fizibilite + örüntü taraması |
  | `tarama` | İnternete çıkmaz; yalnızca örüntü taraması |
  | `teshis` | Tarama + maliyetsiz teşhis turu |
  | `arayuz` | Kurulumu ve testleri kontrol edip arayüzü açar (canlı fiyat akışıyla) |
  | `telegram` | Telegram botunu kurar (jeton Anahtar Zinciri'ne) |
  | `anahtar` | Salt okuma Binance anahtarı kurar, komisyonu ölçer |
  | `komisyon` | Kayıtlı anahtarla komisyonu yeniden ölçer |

* Uzun süren her adım ekrana ilerleme yazmalı. Sessiz ekrana bakınca
  takıldığını sanıyor.
* Uzun çıktıları sohbete yapıştıramıyor; dosya ya da ekran görüntüsü yolluyor.
* Kısa ve net soruyu seviyor; seçenekli sorularda bir öneri işaretlenince
  hızlı karar veriyor. Kararı bazen Claude'a bırakıyor (§3).
* Kod GitHub'da `berkerden/alsat`, `main` dalında. Doğrudan `main`'e
  gönderiliyor; Berk'in bilgisayarında da yedeği var.

## 2. Çalışma ortamının kısıtları ve doğrulama kuralı

* Ağ politikası **Binance adreslerini engelliyor** (proxy 403). Buradan
  gerçek piyasa verisine, WebSocket'e ya da imzalı uçlara çıkılamaz. Gerçek
  bağlantı gerektiren her şey Berk'in Mac'inde ilk kez çalışır. Demo Mode
  (`demo-api.binance.com`) de büyük olasılıkla engelli; ilk denemede
  `curl` ile bak, engelliyse emir yürütmeyi sahte borsayla test et.
* PyPI erişilebilir; kurulum ve testler burada çalışır. Kapsayıcıda hem
  3.12 hem 3.13 var; proje 3.12+ istiyor.
* Bu oturumların yetkisi yeni GitHub deposu açmayı kapsamıyor.
* macOS'a özgü kod (`security`, `caffeinate`, `pmset`) burada
  çalıştırılamaz; testler bunları sahte süreçlerle sınar.

**Doğrulama kuralı:** Berk'e "çalıştır" demeden önce depoyu **GitHub'dan
geçici bir dizine temiz klonlayıp** `bash kurulum.sh telegram` (ya da başka
bir tek adım) ile Python bulma, sanal ortam, bağımlılık ve testleri baştan
çalıştır; macOS dışında tek adım "yalnızca Mac'te çalışır" deyip temiz
çıkar. Buradaki çalışma dizininde testlerin geçmesi kanıt değil (21 Eylül:
`.gitignore` kaynak paketini yuttu, burada geçti, Berk'te patladı).

**Arayüzü doğrulamanın yolu:** Chromium kurulu (`/opt/pw-browsers/chromium`),
Node Playwright genel kurulu (`NODE_PATH=$(npm root -g)`). Faz 4'te
kullanılan tarif (karalama dizininde kalmaz, yeniden kur):

1. Demo sunucu: gerçek `LiveMarket` (bir iş parçacığı saniyede bir
   `on_book`/`on_ticker` ile besler; spread ısınması için geçmişe kaydırılmış
   saatle 300 örnek önceden verilir), gerçek `PaperEngine` (birkaç kapanmış,
   iptal edilmiş ve açık işlem önceden oluşturulur), `runner` yerine
   `marks()` ve `status()` dönen sahte bir sınıf. `Runtime(...)` ve
   `AppState(runtime=...)` ile `create_app`'e verilir.
2. Her koşudan önce sunucuyu **temiz veri dizinle yeniden başlat**; durum
   koşular arasında kalıyor.
3. 1280 px'te `deviceScaleFactor` 1 ve 2, 390 px'te 2 ve 3. Her sekmede
   yatay taşma ölç; bir etkileşimi (emir aç, iptal, mod değiştir, limit
   kaydet) birkaç kez tekrarla; tek zamanlayıcı kaldığını istek sayısıyla
   ölç; konsol hatası olmamalı.

## 3. Berk'in Faz 4'te verdiği kararlar — kendi cümleleriyle

* **Elle kâğıt emir kararını Claude'a bıraktı** (22 Eylül): *"sen
  profesyonel bir yaklaşımla getiri sağlama amacına uygun şekilde
  belirleyebilir misin"*. Verilen karar: elle kâğıt emir eklendi, kural
  emirleriyle aynı risk kapılarından geçer, sonuçları **ayrı** sayılır;
  kabul edilmemiş adaylar otomatik işlenmez; hiçbir ayarın getiri garanti
  etmediği açıkça söylendi.
* **API anahtarı adımını onayladı** (23 Eylül): *"api anahtarı adımını
  yapalım sonra onay vereyim"*. Salt okuma Ed25519 anahtarı Mac'inde üretildi,
  komisyon ölçüldü.
* **Ölçülen komisyon %0.1** (23 Eylül): *"evet dediğin gibi."* Faz 1-2
  varsayımıyla aynı; tarama tekrarlanmadı, önceki sonuçlar geçerli.
* **Faz 4, 23 Eylül 2026'da onaylandı.** Berk'in cümlesi: *"onaylıyorum"*.

Berk'in çalışma yöntemi hakkındaki kuralı (21 Eylül): *"Yeni bir oturum ile
yeni fazlara geçiş yap. Bağlam şişip kalite düşmesin bu sayede. Her oturumda
öğrendiğimiz, dikkat ettiğimiz, sonraki faza aktarılması gerekecek bilgileri
toparla ve sonraki faz için sonraki oturuma taşı, eksik bilgi olmadan devam
etmiş olsun."*

## 4. Faz 4 ne yaptı (tek paragraf)

Faz 3'ün öneri kartının üstüne bir risk motoru (§4.6'nın bütün limitleri ve
piyasa filtreleri, her biri gerekçeli bir kapı), canlı fiyatla çalışan
parasız bir kâğıt hesap (SQLite defter, "fiyat içinden geçti" dolum kuralı,
komisyon/kayma/toz, uygulama kapalıyken kaçırılan mumların sonradan
işlenmesi, CSV), Binance WebSocket akışı (REST yedeği, istek bütçesi, uyku
algılama, kâğıt işlem açıkken `caffeinate`), Telegram bildirim ve komutları
ve salt okuma anahtarla komisyon ölçümü eklendi. Arayüze Kâğıt işlem sekmesi
ve ilk yazma uçları (yerel korumanın arkasında) geldi. Binance'e emir gönderen
kod **yok**. Berk'in Mac'inde canlı akış bağlandı ve komisyon ölçüldü.
Ayrıntı ve gerekçeler `docs/FAZ4-RISK-KAGIT-TELEGRAM.md`. 516 test.

## 5. Eldeki komut satırı araçları

| Araç | Ne yapar | Ağa çıkar mı |
|---|---|---|
| `albsat-fizibilite` | Veri indirir, filtreleri önbelleğe yazar, fizibilite taraması | Evet |
| `albsat-oruntu` | Örüntü keşfi, istatistik, backtest, rapor, kural deposu | Hayır |
| `albsat-oruntu --maliyetsiz` | Aynısı, maliyet sıfır sayılarak (teşhis) | Hayır |
| `albsat-arayuz` | Arayüz + canlı döngü, yalnızca 127.0.0.1 (`--cevrimdisi` ile ağsız) | Evet (herkese açık akış) |
| `albsat-telegram` | Telegram kurulumu, deneme mesajı, kaldırma | Evet (Telegram) |
| `albsat-anahtar` | Salt okuma anahtar kurulumu, komisyon ölçümü (`--olc`, `--sil`) | Evet (2 imzalı okuma) |
| `albsat-tls-teshis` | Sertifika zinciri teşhisi | Evet |

## 6. Faz 4'te öğrenilen, koda bakarak görülmeyecek tuzaklar

1. **Yeni yazma ucu yerel korumayı geçmek zorunda.** `app.py`'deki
   `yerel_koruma` her `GET`/`HEAD` dışı isteğe `X-Albsat-Istek: 1` ve
   `Content-Type: application/json` şart koşar, `Host` 127.0.0.1/localhost
   olmalı. FastAPI `TestClient`'ın varsayılan hostu `testserver`'dır ve 403
   alır; testlerde `base_url="http://127.0.0.1"` kullan. Arayüzde istek
   `app.js`'teki `gonder()` ile gider. İstek gövdeleri `extra="forbid"`.
2. **`LiveRunner.tick()` her akış olayında çalışır**, saniyede bir değil:
   `bookTicker` saniyede onlarca mesaj getirebilir. `tick`'e eklenen her iş
   tekdüze saatle seyreltilmeli (`SLEEP_GUARD_SECONDS` örneği). SQLite
   okuması ya da alt süreç çağrısı seyreltilmeden eklenirse döngü tıkanır.
3. **Çevrimdışı mum ≠ çevrimiçi mum.** Kaçırılan mumlarda yalnızca borsa
   tarafı olur (dolum, stop, hedef); süre dolumu gibi uygulama işleri
   ertelenir (`online=False`, `apply_deferred`). Faz 5'te gerçek emirlerde
   de aynı ayrım geçerli: uygulama uyurken borsadaki emir yaşar.
4. **Toz sonucun içinde sayılmazsa her işlem zararlı görünür.** Satış
   `stepSize`'a aşağı yuvarlanır; kalan küsurat bakiyede kalır. İşlem sonucu,
   toz değişimini çıkış fiyatından sayar; aksi hâlde BTC'de her işlem
   ~0,6 USDT zarar gösterirdi. Gerçek hesapta da aynı toz oluşur.
5. **Spread ısınması 5 dakika.** Açılıştan sonra 300 örnek birikmeden spread
   kapısı "ölçülemedi" der ve emir açılmaz. Berk bunu arıza sanabilir; ekran
   yazıyor ama ilk denemede hatırlat.
6. **BTC spread'i dört ondalıkta sıfır görünür** (bir fiyat adımı ≈
   %0,00002). Spread metinleri 6 ondalık (`SPREAD_PLACES`).
7. **Sayı biçimi tek tip:** ondalık ayırıcı nokta, yüzde işareti önde
   (`%0.76`). Sunucudan para metin gelir: bakiye 2, kâr/zarar ve risk tutarı
   4 ondalık (`paper_api.usdt`). `toLocaleString("tr-TR")` kullanma; aynı
   ekranda virgül ve nokta karışıyordu.
8. **Telefonda sabit alt şerit içeriği örtüyordu.** Şeridin yüksekliği
   ölçülüp `--alt-yukseklik` CSS değişkenine yazılıyor; `body` alt boşluğu ve
   bildirim kutusu buna göre. Alt şeride satır eklersen 390 px'te bak.
9. **`change` olay dinleyicisine isteğe bağlı parametre alan fonksiyon
   verme.** Faz 3'ten kalan hata: `sembolEl.addEventListener("change",
   tazele)` Event nesnesini `tazele`'nin ilk parametresine geçiriyordu ve coin
   değişince öneriler yenilenmiyordu. Düzeltildi; aynı kalıbı tekrar etme.
10. **Kâğıt işlem sekmesinin yenilemesi tek zamanlayıcıdır** ve yalnızca
    sekme açık ve sayfa görünürken çalışır. Bölümler yalnızca verisi
    değişince yeniden çizilir; kullanıcının yazdığı formlar (elle emir,
    limitler) yenilemede hiç çizilmez. Bu kuralı bozan değişiklik formu
    kullanıcının elinden alır.
11. **Sırlar üç yerde maskelenir:** Telegram jetonu adresin içinde olduğu
    için hata metinlerinde `<jeton>` olur; imzalı istekte hata mesajına
    yalnızca yol yazılır (imza sorgu metninde); denetim kaydı `token`,
    `jeton`, `secret`, `private`, `signature`, `imza` içeren alanları hiç
    yazmaz. Her birinin testi var.
12. **Anahtar Zinciri'ne yazarken sır komut satırından geçmez**
    (`security -i`, standart girdi). Sırda yalnızca güvenli karakterler
    olabilir.
13. **Kendi üretilmiş Ed25519 anahtarı `/sapi/` ve `/api/v3/` imzalı okumalarda
    çalışıyor** (23 Eylül'de Berk'in hesabında doğrulandı). Anahtar
    Anahtar Zinciri'nde `albsat-binance` hizmet adıyla duruyor, izinleri
    **yalnızca okuma**.
14. **`pkill -f desen` bu ortamda kendi kabuğunu da öldürür** (komut satırı
    deseni içeriyor). Arka plan süreçlerini PID dosyasıyla durdur.
15. **Lint ve tip denetimi tabanı.** `ruff` ~32, `mypy` 35 hata veriyor;
    hepsi Faz 1-3'ten kalma dosyalarda. Faz 4 dosyaları temiz. Yeni kodu
    temiz tut; eskiyi toptan "düzeltme" işi ayrı bir iş.

**Faz 3'ten taşınan tuzaklar (hâlâ geçerli, ayrıntı git geçmişinde):**
`CostAssumptions`'ta oran ile yüzde karışık (bilerek; 100 katlık hata
üretmişti); isabet oranı `olay` üzerinde, güven skoru `kabul_ornegi` üzerinde;
veri kontrolü kural kontrolünden önce; sinyal sıklığı toplanmaz;
`Decimal` için `normalize()` değil `format_for_api`; tur maliyet eşlemesi
araştırmayla birebir (giriş taker, hedef maker, stop taker); **Retina'da
canvas boyutu geri okunmaz** (`bb6c581`); **"hâlâ bozuk" derse önce eski
sekmeyi düşün**, iki yerdeki "Arayüz sürümü" aynı mı diye sor (`559b9eb`).

**Faz 2'den taşınan tuzaklar:** çoklu test düzeltmesi koşunun tamamı
üzerinden; keşif ve sınama ayrı veri; bootstrap p-değeri tabanı
`1/(yineleme+1)`; arşiv zaman damgaları mikrosaniye (tek kapı
`klines.normalize_epoch_ms`); kapanmamış mum tahmine giremez
(`klines.closed_only`); fiyat, miktar ve bakiyede `float` yok.

## 7. Danışılmadan değiştirilmemesi gereken seçilmiş varsayılanlar

1. **Uygulama her açılışta Sadece Öneri modunda başlar.** Önceki oturumda
   kâğıt işlemde olan coin kendiliğinden dönmez (SPEC §2). Faz 5'te Demo modu
   da böyle olmalı.
2. **Ölçülemeyen koşul geçmiş sayılmaz.** Spread, ATR, hacim ya da BTC
   hareketi ölçülemezse kapı kapalıdır.
3. **Aralık dışı limit reddedilir, kırpılmaz.**
4. **Risk sınırı aşılınca** bütün coinler Sadece Öneri'ye iner, bekleyen
   girişler iptal edilir, açık pozisyonun stop ve hedefi yerinde kalır;
   yeniden açmak yalnızca elle. Yeni gün kendiliğinden açmaz.
5. **ACİL DURDUR pozisyon kapatmaz**, yalnızca modları indirir ve bekleyen
   girişleri iptal eder. Kapatmak ayrı seçim (`/durdur kapat`, "Kapat").
6. **Elle emirler ayrı sayılır**; kural performans sınamasına ve canlıya
   geçiş kapısına girmez. Kabul edilmemiş adaylar otomatik işlenmez.
7. **Kâğıt dolum kuralları temkinli** (içinden geçme, aynı mumda stop
   önce, emrin verildiği dakikanın mumu sayılmaz). Kâğıt sonucu gerçekten
   kötü çıkma eğiliminde olmalı, iyi değil.
8. **BNB indirimi hesapta açık olsa bile kâğıt işlem indirimsiz oranı
   kullanır.**
9. **Canlıya geçiş kapısı (7 gün / 30 işlem) şimdilik bilgi amaçlı**;
   Tam Otomatik ile birlikte Faz 6'da zorunlu olur.
10. **Faz 3'ten:** incelenen adaylar karta dönüşmez; teşhis deposu öneri
    üretmez; Hedef 2 uydurulmaz; filtre yoksa varsayılan uydurulmaz; sunucu
    yalnızca 127.0.0.1; kapsam A (BTCUSDT ve SOLUSDT, 15m ve 1h).
11. **Arayüz derleme adımsız düz HTML/CSS/JS** (SPEC §3'ten sapma; Berk
    Faz 3'te bununla onayladı). Node/npm kurdurma.

## 8. Yarım kalanlar ve Faz 5'e taşınan uyarılar

1. **Faz 5 = Demo Mode'da emir yürütme.** Demo Mode'un anahtarları gerçek
   hesabın anahtarlarından ayrıdır; Demo'da **işlem izni olan** yeni bir
   anahtar gerekecek. Oluşturmadan önce Berk'e sor ve §1'deki gibi adım adım
   anlat. Para çekme izni yine kapalı olmalı; `restriction_problems` bunu
   denetliyor.
2. **OTOCO kısmi dolumda koruma sağlamıyor** (FAZ0 Risk #1): bekleyen OCO
   ancak giriş tamamen dolunca deftere konur. Onaylanan çözüm "korumasız süre
   nöbetçisi" (`config/default.yaml` → `emir.korumasiz_azami_saniye`, 20 sn)
   henüz yazılmadı. Kâğıt işlem kısmi dolumu modellemiyor; gerçek emirde
   kısmi dolum olacak.
3. **Darboğaz dolmamış emir sayacı (ORDERS limiti)**, ağırlık değil
   (2026-04'ten beri başarılı emir 0 ağırlık). İstek bütçesi
   (`exchange/ratelimit.py`) şu an yalnızca ağırlığı sayıyor.
4. **User Data Stream** artık yalnızca `userDataStream.subscribe` (Ed25519 +
   `session.logon`); `listenKey` uçları kaldırıldı.
5. **İmzalı istemci izin listesiyle çalışıyor** (`exchange/signed.py`,
   şu an iki okuma adresi). Emir uçları eklenecekse ayrı, açıkça
   adlandırılmış bir sınıf ve ayrı bir izin listesi olsun; salt okuma
   sınıfına emir yöntemi ekleme. `api/` katmanının imzalı koda erişmediğini
   denetleyen test (`test_arayuz_katmani_imzali_istek_ve_anahtar_koduna_erismez`)
   Faz 5'te bilinçli olarak güncellenmeli, silinmemeli.
6. **`binance-sdk-spot`** `pyproject.toml`'da yorum satırında bekliyor.
   Faz 4 kendi imzalı istemcisini yazdı; Faz 5'te SDK'ya geçmek mi, izin
   listeli kendi istemciyi genişletmek mi, gerekçeyle karar ver.
   `binance-connector` "deprecated", kullanma.
7. **Uzlaştırmada doğruluk kaynağı borsadır** (SPEC). `LiveRunner.reconcile`
   bugün kâğıt defteri için; Faz 5'te açılışta, yeniden bağlanmada ve
   uyanınca borsadaki açık emirler ve bakiyelerle karşılaştırma eklenecek.
8. **Modlar:** Demo, Yarı Otomatik ve Tam Otomatik `modes/state.py`'de
   kilitli listede. Faz 5 yalnızca Demo'yu açar.
9. **Berk'in Mac'inde henüz sınanmayanlar:** `caffeinate` uyku engeli,
   `pmset` pil uyarısı, Telegram kurulumu. Telegram `/onayla` Faz 6 için
   yer tutucu.
10. **Kabul edilmiş kural yok** (Faz 2). Kâğıt işlem kendiliğinden emir
    açmıyor; Demo'da da kural sinyali olmayacak. Faz 5'in emir yürütmesi elle
    emirle ve sahte sinyalle sınanmalı; olmayan bir kuralı varmış gibi
    gösterme.
11. **Yeni tarama turu istenirse bu bir çoklu test sorunudur.** Her yeni
    parametre denemesi kabul eşiğini sertleştirir; istenirse yapılır ama
    bedeli söylenir.
12. **Borsa filtreleri artık arayüz açılışında günde bir tazeleniyor**
    (`LiveRunner._refresh_filters`); Faz 3'teki "önce `bash kurulum.sh`
    çalıştır" notu geçersiz.

## 9. Güvenlik (bunlar hiçbir fazda düşmez)

* **TLS sertifika doğrulaması hiçbir koşulda, geçici olarak bile
  kapatılmayacak.** Berk'in ağında HTTPS'i yeniden imzalayan bir katman var;
  uygulama `truststore` ile macOS güven deposunu kullanıyor. WebSocket
  istemcisi de aynı enjekte edilmiş güven deposunu kullanıyor; kendi SSL
  bağlamını veren kod bunu atlar.
* Sırlar Berk'in bilgisayarından çıkmıyor. Binance anahtarı (`albsat-binance`)
  ve Telegram jetonu (`albsat-telegram`) macOS Anahtar Zinciri'nde; depoda,
  dosyada, günlükte, denetim kaydında, arayüzde yoklar. Ortam değişkenine sır
  konmuyor.
* Berk 21 Eylül 2026'da sohbete bir Binance API anahtarı yapıştırmıştı;
  kullanılmadı, hiçbir yere yazılmadı, silmesi söylendi. Anahtar isteme,
  yazdırma, dosyaya koyma. API Key yalnızca Terminal'e gizli girişle yazılır.
* **Gerçek para ile emir gönderecek ve API anahtarı gerektirecek her adım
  öncesinde Berk'e sorulacak.** Demo Mode anahtarı da buna dahil.
* Binance'e giden istek hacmi sınırlı (yerel tavan 600 ağırlık/dk, 429'da
  bekle, 418'de dur). Beklenmedik büyüklükte bir iş çıkarsa program istek
  göndermeden durur.
* Komisyon oranı, sembol filtresi veya limit **koda sabit yazılmaz**.
* Arayüz yalnızca `127.0.0.1` dinler; yazma uçları yerel korumanın
  arkasında (§6, madde 1).
* Kaldıraç, margin, futures ve borçlanma kodu yok ve olmayacak.

## 10. Bu notu sonraki faza devrederken

Faz 5 bitince bu not baştan yazılır ve şunları taşır:

1. Berk nasıl çalışıyor.
2. Çalışma ortamının kısıtları ve temiz-klon doğrulama kuralı.
3. Berk'in o fazda verdiği kararlar, **kendi cümleleriyle**.
4. O fazın sonucu bir paragrafta, ayrıntı için belge adresiyle.
5. O fazda öğrenilen, koda bakarak görülmeyecek tuzaklar.
6. Danışılmadan değiştirilmemesi gereken seçilmiş varsayılanlar.
7. Yarım kalanlar ve sonraki faza taşınan uyarılar.
8. Güvenlik kuralları.

Belgelerin kendisi yerinde duruyor; bu not onların yerine geçmez, hangisinin
ne zaman okunacağını söyler.
