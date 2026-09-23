# Faz 5 — Demo Mode'da emir yürütme

**Tarih:** 23 Eylül 2026
**Kapsam:** BTCUSDT ve SOLUSDT, 15m ve 1h, 100 USDT doğrulama bütçesi, Binance **Demo Mode** (sahte para)
**Şartname karşılığı:** SPEC.md §4.5, §4.6, §5 ve §10'daki Faz 5 satırı
**Kabul kriteri (SPEC.md §10):** *"Kaos testleri geçer."*

Bu belge Faz 5'in **nasıl kurulduğunu ve neden öyle kurulduğunu** anlatır.

---

## 1. Faz 5 ne yapıyor, ne yapmıyor

Faz 4'ün sonunda uygulama kâğıt üzerinde işlem yapıyordu: emirler yalnızca bu
bilgisayardaki deftere yazılıyordu. Faz 5 aynı emirleri **Binance Demo Mode**
hesabına gerçek emir olarak gönderir ve pozisyon kapanana kadar izler:

1. **Emir yürütücüsü** (`execution/executor.py`). Risk motorundan geçen bir
   emri borsada bir OTOCO listesine çevirir: giriş `LIMIT_MAKER` alış, giriş
   dolunca borsanın kendisinin koyduğu hedef (`LIMIT_MAKER` satış) ve stop.
   Kısmi dolum, reddedilen emir, yanıtı kaybolan istek, kopan akış, uyuyan
   Mac ve yeniden açılan uygulama durumlarını yönetir.
2. **İmzalı istemci** (`exchange/trading.py`) ve **hesap akışı**
   (`exchange/user_stream.py`). Ed25519 ile imzalı REST istekleri ve
   WebSocket API üzerinden dolum/iptal olayları.
3. **Demo işlem sekmesi**, Telegram `/durum` ve `/durdur`'un Demo'yu da
   kapsaması, acil durdurmanın borsadaki bekleyen girişleri iptal etmesi.
4. **Kurulum komutları:** `bash kurulum.sh demo-anahtar` (Demo anahtarı) ve
   `bash kurulum.sh demo-sina` (dolmayacak bir sınama emriyle uçtan uca
   deneme).

Yapmadıkları:

- **Canlı hesaba emir göndermez.** `DemoTrader` kurucusu Demo dışındaki her
  ortamı reddeder ve adresi kendisi `exchange/endpoints.py`'den alır;
  dışarıdan adres verilemez. Canlıya geçiş Faz 6'nın ayrı kararıdır.
- Kabul edilmiş kural olmadığı için (Faz 2 sonucu) Demo'da da kendiliğinden
  emir **açılmaz**. Yürütücü elle girilen Demo emirleriyle sınanır; kural
  çıkarsa aynı yol sinyalden emir açar.
- Kaldıraç, margin, futures ve borçlanma kodu yoktur.

---

## 2. Kabul kriterinin okunuşu

**"Kaos testleri geçer."** Demo Mode'un kendisi bu uzak ortamdan erişilemez
(ağ politikası Binance adreslerini engelliyor), ayrıca kopan bağlantıyı,
kaybolan yanıtı ya da kısmi dolumu gerçek borsada istenen anda üretmek
mümkün değil. Bu yüzden kaos testleri **sahte bir Binance** ile yapılır
(`tests/fake_binance.py`). Sahte borsa:

- Gerçek istek biçimini alır: `X-MBX-APIKEY` başlığını ve **Ed25519 imzasını
  doğrular** (bozuksa `-1022`), `timestamp`/`recvWindow` penceresini sınar
  (`-1021`).
- Emir defteri, eşleştirme, bakiye kilitleri (OCO bacakları aynı kilidi
  paylaşır), alınan varlıktan %0.1 komisyon ve işlem kayıtları tutar.
- Hesap akışına `executionReport`, `listStatus` ve
  `outboundAccountPosition` olaylarını yayınlar; akış kopukken olaylar
  **kaybolur** (gerçekte de öyle).
- İstendiğinde arıza üretir: yanıtın kaybolması (istek işlenmiş ya da
  işlenmemiş), 500, `-1007`, 429, 418, saat kayması, defterin incelmesi,
  stopun süresinin dolması, elle verilmiş emirler.

Her testin sonunda aynı değişmezler denetlenir (`check_invariants`):

1. Her emir kimliği borsaya **en fazla bir kez** gönderildi.
2. Kayıttaki her dolum borsada var ve miktarı aynı.
3. Kapanan pozisyonların borsadaki bütün dolumları kayıtta.
4. Elde coin varsa ya borsada canlı bir stop var ya da korumasızlık
   ölçülüyor (ya da çıkış isteniyor).

---

## 3. Neden Demo Mode

Berk 21 Eylül 2026'da Testnet yerine Demo Mode'u seçti. Demo Mode'un
filtreleri ve istek limitleri canlıyla aynıdır; fiyatı canlıya yakındır ama
**emir defteri ayrıdır**. Bu yüzden:

- "Hemen eşleşir mi?" (limit-maker) kapısı canlı defterle değil **Demo
  defteriyle** sınanır; Demo defterinin en iyi alış/satışı ayrı bir akıştan
  (`@bookTicker`, Demo adresi) okunur.
- Bir Demo pozisyonunun anlık değeri de Demo defterinin en iyi alışıyla
  hesaplanır. Arayüz iki defteri yan yana gösterir.

Adresler (`demo-mode/general-info.md`): REST `https://demo-api.binance.com/api`,
WebSocket API `wss://demo-ws-api.binance.com/ws-api/v3`, akış
`wss://demo-stream.binance.com/stream`. Demo anahtarı
`https://demo.binance.com/en/my/settings/api-management` sayfasında oluşturulur.

---

## 4. Emir akışı

### 4.1 Giriş: OTOCO

Bir emir niyeti (kuraldan ya da elle) risk kapılarından geçince tek bir
`POST /api/v3/orderList/otoco` gönderilir:

| Bacak | Tür | Ne zaman borsada |
|---|---|---|
| Giriş | `LIMIT_MAKER` alış | hemen; defterde bekler |
| Hedef | `LIMIT_MAKER` satış | giriş **tamamen** dolunca |
| Stop | `STOP_LOSS` (varsayılan) ya da `STOP_LOSS_LIMIT` satış | giriş tamamen dolunca |

Hedef ve stop borsanın kendi mekanizmasıyla konur: giriş dolduğu an uygulama
kapalı ya da Mac uykuda olsa bile pozisyon korunur. Hedef dolarsa stop, stop
dolarsa hedef borsada kendiliğinden düşer (OCO).

Satılacak miktar, alışta komisyonun coin olarak düşülmesi hesaba katılarak
`stepSize`'a **aşağı** yuvarlanır; kalan küsurat (toz) kaydedilir ve bir
sonraki pozisyonun satışına eklenir (`planner.sell_quantity`).

**Limit-maker reddi.** Giriş fiyatı Demo defterinde hemen eşleşecekse borsa
emri reddeder. Kural emrinde fiyat en iyi alışa çekilip en fazla
`yeniden_fiyatlama_denemesi` kez yeniden gönderilir (her denemede yeni
kimlik, çünkü önceki kesin olarak reddedilmiştir). **Elle emir yeniden
fiyatlanmaz**: kullanıcının yazdığı fiyat değiştirilmez.

### 4.2 Tek değişmez kural: elde coin varsa borsada canlı bir stop olmalı

Yürütücü her saniye (ve her olayda) her pozisyon için bunu sınar. Stop
yoksa pozisyon **korumasızdır** ve sebebi ne olursa olsun aynı yoldan
korunur:

1. **Kısmi dolum.** OTOCO'nun hedef/stopu giriş tamamen dolmadan borsaya
   konmaz. Giriş yarım dolarsa (ya da hedef yarım dolup stop düşerse) kalan
   emir en fazla `korumasiz_azami_saniye` (varsayılan 20 sn) beklenir.
   Dolarsa borsanın OCO'su devreye girer; dolmazsa liste iptal edilir.
2. **Yeniden koruma.** Hiçbir emir çalışmıyorsa elde kalan miktar için hemen
   yeni bir OCO (`POST /api/v3/orderList/oco`: hedef + stop) kurulur.
3. **Korumalı çıkış.** Fiyat stopun altına inmiş ya da hedefin üstüne
   çıkmışsa OCO kurulamaz. Bunun yerine en iyi alışın en fazla
   `azami_kayma_yuzde` altına fiyat sınırlı, anında-ya-iptal bir satış
   (`LIMIT IOC`) gönderilir. İnce defterde kısmen dolarsa kalan için yeniden
   denenir. Piyasa emri kullanılmaz: kayma sınırsız olurdu.
4. **Satılamayan küsurat.** Kalan miktar borsanın en küçük emir sınırının
   altındaysa satılamaz; pozisyon küsurat olarak kapanır, kullanıcıya
   söylenir ve küsurat bir sonraki satışa eklenir.

Korumasız geçen süre her pozisyonda ölçülür (şu anki, toplam ve en uzun) ve
arayüzde, Telegram'da ve özetlerde "stopsuz süre" olarak yazar.

Bir adım tek turda bitmeyebilir: iptal → koruma → kapanış zinciri aynı
saniyede ilerlesin diye her pozisyonun adımı durumu değişmeyene kadar (en
fazla dört kez) tekrarlanır.

### 4.3 Çıkışlar

| Sebep | Nasıl |
|---|---|
| Hedef | Borsadaki `LIMIT_MAKER` satış dolar (maker) |
| Stop | Borsadaki stop tetiklenir (piyasa stopta taker) |
| Giriş süresi doldu | Giriş listesi iptal edilir; dolan kısım varsa korunur |
| Azami tutma süresi | Hedef/stop iptal, korumalı çıkış |
| Elle kapatma / acil durdur (kapatarak) | Hedef/stop iptal, korumalı çıkış |
| Küsurat | Satılamayacak kadar küçük kalan; kayıtta kapanır |

Sonuç (net USDT, net %, komisyon) **borsanın bildirdiği gerçek dolumlardan**
hesaplanır (`GET /api/v3/myTrades` ve akıştaki dolumlar), tahminden değil.

---

## 5. Kimlikler ve sonucu bilinmeyen istek

Her emir kimliği `albsat-demo-<jeton>-<rol>` biçimindedir (`execution/ids.py`):
jeton 8 karakter zaman + 4 karakter rastgele, rol `L1`/`G1`/`H1`/`S1` (giriş
listesi), `K2L`/`K2H`/`K2S` (yeniden koruma), `C3` (çıkış). En uzun kimlik 30
karakterdir (sınır 36).

Kimlik emir gönderilmeden **önce** diske yazılır. Emir ya da iptal isteğinde
zaman aşımı, HTTP 5xx ya da `-1006`/`-1007` gelirse borsa isteği işlemiş de
olabilir, işlememiş de (Binance belgesi bunu başarısız saymamayı söyler):

- Emir **aynı kimlikle sorgulanır**; asla yeni kimlikle yeniden gönderilmez.
- Borsa `timestamp + recvWindow`'dan sonra gelen isteği kabul etmez. Bu
  sürenin üstüne 5 saniye pay eklendikten sonra "bu kimlik yok" (`-2013`)
  yanıtı "emir hiç ulaşmadı" demektir; pozisyon "borsaya ulaşmadı" diye
  kapanır ve emir yeniden gönderilmez.
- Sonucu bilinmeyen bir emir varken aynı coinde yeni emir açılmaz (kapı).

İptal isteğinin yanıtı kaybolursa emir sorgulanır; borsada canlı görünen ama
kayıtta "bitti" sanılan bir bacak geri alınır ("borsada görüldü").

---

## 6. Uzlaştırma: borsanın söylediği doğrudur

Yürütücünün kaydı borsayla şu anlarda karşılaştırılır (`reconcile`):

- açılışta ve hesap akışı her yeniden bağlandığında,
- Mac uykudan uyandığında (30 saniyeden uzun zaman sıçraması),
- hesap akışı 15 saniyeden uzun kopuk kaldığında (REST ile),
- beş dakikada bir, bir de arayüzdeki "Borsayla uzlaştır" düğmesiyle.

Uzlaştırmada kayıttaki her açık emir sorgulanır, eksik dolumlar
`myTrades`'ten tamamlanır, bakiye okunur. Borsadaki açık emirler şöyle
ayrılır:

- **Elle verilmiş emirler** (`albsat-demo-` ile başlamayan): **dokunulmaz**,
  sayılır ve raporlanır.
- **Yetim albsat alışı** (önek bizim, kayıtta yok): iptal edilir, çünkü
  dolarsa korumasız bir pozisyon açar.
- **Yetim albsat satışı:** dokunulmaz ve uyarı yazılır; bir coini koruyor
  olabilir.

Uygulama pozisyonun ortasında kapanırsa (ya da çökerse) aynı veritabanıyla
açılan yeni yürütücü kaldığı yerden devam eder; kaos testlerinden biri bunu
kısmi dolumun ortasında sınar.

---

## 7. Risk

Demo emri Faz 4'ün bütün risk kapılarından geçer; limitler kâğıt işlemle
ortaktır ve aynı arayüzden değiştirilir. Demo'ya özel kapılar:

| Kapı | Kapalıysa |
|---|---|
| Demo bağlantısı | Anahtar yok, hesap akışı kopuk, hazırlık ya da uzlaştırma bitmemiş, 418 engeli |
| Sonucu bilinmeyen emir | Aynı coinde sonucu bilinmeyen bir emir var |
| Borsa filtreleri | Filtreler yok, coin işlemde değil ya da OTOCO desteklenmiyor |
| Emir sayısı sınırı | Borsanın 10 saniyelik sınırının %50'si ya da günlük sınırının %80'i dolmuş |
| Limit-maker kuralı (Demo defteri) | Giriş Demo defterinde hemen eşleşir |
| Demo hesabı bakiyesi | Borsadaki serbest USDT emre yetmiyor |

**Büyüklük.** Pozisyon büyüklüğü bot bütçesinden (100 USDT) ve işlem başı
risk kuralından hesaplanır; Demo hesabındaki serbest USDT daha azsa emir o
kadar küçülür, en küçük emir sınırının altına düşerse açılmaz. Demo hesabında
Binance'in verdiği sahte bakiye bütçeden çok büyük olabilir; bot yalnızca
kendi bütçesi kadar kullanır ve sonuçları o bütçeye göre hesaplar.

**Sınır aşımı.** Kâğıtta ya da Demo'da bir sınır (günlük/haftalık/aylık
zarar, art arda kayıp) aşılırsa **ikisi birden durur**: bütün coinler Sadece
Öneri'ye döner, bekleyen girişler iptal edilir, açık pozisyonların stop ve
hedefi borsada kalır.

**Acil durdur.** Arayüzdeki düğme ve Telegram `/durdur` kâğıt işlemi ve
Demo'yu kapatır, Demo'daki bekleyen girişleri **borsada** iptal eder; stoplar
yerinde kalır. Pozisyonları da kapatan seçenek (`pozisyonlari_kapat`) korumalı
çıkışla satar.

---

## 8. Ayarlar (`config/default.yaml` → `emir`)

| Ayar | Varsayılan | Aralık | Anlamı |
|---|---|---|---|
| `stop_tipi` | `STOP_LOSS` | `STOP_LOSS` / `STOP_LOSS_LIMIT` | Piyasa stop her koşulda satar (boşlukta stopun altında dolabilir); limitli stop dolmayabilir ve pozisyon korumasız kalabilir |
| `stop_limit_ofset_yuzde` | 0.5 | 0.05 – 5 | Limitli stopta limit fiyatının stopun ne kadar altında olacağı |
| `azami_kayma_yuzde` | 0.5 | 0.05 – 5 | Korumalı çıkışta en iyi alışın en fazla ne kadar altına satılacağı |
| `korumasiz_azami_saniye` | 20 | 5 – 300 | Kısmi dolumda en uzun bekleme |
| `yeniden_fiyatlama_denemesi` | 3 | 0 – 5 | Kural emrinde limit-maker reddinden sonra deneme |

Varsayılan piyasa stop seçildi: stopun görevi zararı sınırlamaktır, dolmayan
bir stop o görevi yapmaz. Arayüzden değiştirilebilir; değişiklik yeni
emirlerde geçerlidir ve denetim kaydına yazılır. `settings.py` ile YAML'ın
ayrışmadığını bir test denetler.

---

## 9. İstemci ve güvenlik (`exchange/trading.py`)

- **İzin listesi yapısaldır.** `DemoTrader` yalnızca listedeki
  (yöntem, adres) çiftlerini gönderebilir; listede olmayan istek gönderilmeden
  reddedilir. Bütün açık emirleri silen `DELETE /api/v3/openOrders` bilerek
  listede yok.
- **Yalnızca kendi emirleri.** Gönderilen ve iptal edilen her kimlik
  `albsat-demo-` ile başlamak zorundadır; elle verilen emirler iptal
  edilemez.
- **Emir türleri sınırlı:** giriş yalnızca `LIMIT_MAKER` alış, çıkış yalnızca
  `LIMIT` + `IOC` satış, stop yalnızca `STOP_LOSS`/`STOP_LOSS_LIMIT`.
- **Anahtar** macOS Anahtar Zinciri'nde `albsat-binance-demo` kaydında durur
  (Faz 4'ün salt okuma anahtarı `albsat-binance` ayrı kalır). Özel yarı bu
  bilgisayarda üretilir; Binance'e yalnızca genel yarı verilir. API Key
  Terminal'e gizli girişle yazılır. Depoda, dosyada, günlükte, denetim
  kaydında ve arayüzde anahtar yoktur; `repr` bile gizler.
- **Para çekme.** Demo Mode'da para çekme yoktur: anahtar izinleri Demo'da
  değiştirilemiyor ve çekim seçeneği hiç sunulmuyor, Demo API cüzdan uçlarını
  vermiyor, `DemoTrader`'ın izin listesinde çekim adresi yok. `GET
  /api/v3/account`'taki `canWithdraw` **hesabın** bayrağıdır, anahtarın izni
  değil; Demo hesabı onu `true` döndürüyor (23 Eylül 2026'da Berk'in Mac'inde
  görüldü) ve Demo'da engel sayılmaz. Hesap `canTrade` kapalı derse yürütücü
  emir göndermez. **Faz 6 için:** canlı anahtarın çekim izni bu bayrakla değil,
  `/sapi/v1/account/apiRestrictions`'taki `enableWithdrawals` ile denetlenmeli
  (Faz 4'ün `signed.restriction_problems`'ı bunu yapar).
- **İstek bütçesi:** yerel tavan 600 ağırlık/dk; 429'da borsanın söylediği
  süre boyunca hiç istek gitmez; 418'de engel bitene kadar Demo işlem durur ve
  bildirim gider. `-1021`'de saat eşitlenip bir kez yeniden denenir.
- **TLS doğrulaması** macOS güven deposuyla yapılır (`truststore`) ve hiçbir
  koşulda kapatılmaz.
- **Arayüz katmanı** (`api/`) imzalı istemciye, anahtara ve Anahtar
  Zinciri'ne dokunmaz; yalnızca kurulmuş yürütücüyle konuşur. Bir test `api/`
  altındaki dosyaları tarayıp bunu denetler.

**Neden resmi SDK değil:** Berk'in ağında HTTPS yeniden imzalanıyor ve
güven deposu standart kütüphanenin `ssl`/`urllib` katmanına bağlı; izin
listesi yapısal kalıyor; sahte borsayla sınanabiliyor; yeni bağımlılık yok.

---

## 10. Arayüz (`api/demo_api.py`, `api/static/demo.js`)

**Demo işlem** sekmesi:

- Bağlantı şeridi: hazır mı, değilse neden; hesap akışı, Demo defteri, son
  uzlaştırma, "Borsayla uzlaştır".
- Hangi coin Demo'da: tek düğmeyle Demo Mode'a al / Sadece Öneri'ye al.
  Hazır değilken düğme kapalıdır ve nedeni yazar.
- Demo hesabı (bot bütçesi), borsadaki serbest USDT, Demo defteri ile canlı
  piyasa yan yana.
- Açık pozisyonlar kart olarak: borsadaki her emir ve durumu (Türkçe),
  alınan/elde miktar, anlık net, stopta zarar, stopsuz süre; "Girişi iptal
  et" ve "Pozisyonu kapat".
- Elle Demo emri: "Önizle" borsaya gidecek emirleri ve risk/Demo kapılarını
  gösterir, hiçbir şey göndermez; "Demo emrini gönder" önce sorar.
- Sonuçlar (kural ve elle ayrı), kapanan işlemler, dolum CSV'si.
- Açılır kutular: emir ayarları, bağlantı/istek bütçesi/olaylar, dönem
  sıfırlama (Binance'teki Demo bakiyesine dokunmaz).

Sekme açıkken beş saniyede bir `/api/demo/durum` okunur; bu okuma Binance'e
istek göndermez (yürütücünün elindeki son durum döner; bir test bunu
denetler). Borsaya giden her eylem `POST`'tur ve yerel korumadan geçer.

Kâğıt işlem sekmesindeki mod seçicisi Demo Mode'u da gösterir; hazır değilse
seçenek kapalıdır. Üstteki rozet hangi coinin Demo'da olduğunu yazar.

---

## 11. Komutlar (`cli/demo.py`)

```bash
bash kurulum.sh demo-anahtar   # Demo anahtarını kurar, izinleri ve komisyonu okur
bash kurulum.sh demo-sina      # dolmayacak bir sınama emriyle uçtan uca deneme
```

`demo-anahtar` bu bilgisayarda bir Ed25519 çifti üretir, genel yarıyı ve
Demo API yönetimi adımlarını yazar, API Key'i gizli girişle alır, sonra
yalnızca okuma istekleriyle saat farkını, izinleri, bakiyeyi ve Demo
komisyonunu okur (`veri/demo-komisyon.json`).

`demo-sina` altı adımda ilerler ve her adımı ekrana yazar: saat ve hesap,
Demo borsa kuralları ve fiyat, hesap akışına abonelik, **sormadan
göndermediği** küçük bir OTOCO (giriş en iyi alışın yaklaşık %5 altında,
tutar en küçük emir sınırının 1,5 katı; dolmaz), emrin akıştan ve borsa
kaydından görülmesi, iptal ve iptalin doğrulanması. Arayüz açıkken
çalıştırılmamalıdır: arayüzün uzlaştırması kaydında olmayan albsat alışını
iptal eder.

---

## 12. Testler

| Dosya | Test | Ne sınıyor |
|---|---|---|
| `test_demo_kaos.py` | 36 | Sahte borsayla uçtan uca: hedef, stop, giriş süresi; kısmi giriş (20 sn sonra OCO), kalanın dolması, kısmi hedef; fiyat stopun altındayken korumalı çıkış, ince defter; yanıtı kaybolan emir (borsaya ulaşmış üç türü, ulaşmamış iki türü); kaybolan iptal yanıtı; sorguda görünmeyen bekleyen bacaklar; kopuk akış; kısmi dolumun ortasında çökme ve yeniden açılış; uyku sıçraması; `-1021`, 429, 418; limit-maker reddinde yeniden fiyatlama (kural) ve fiyatlamama (elle); bakiyeyle sınırlanan ve açılmayan emir; süresi dolan stop; acil durdurun üç türü; azami tutma; elle emirlere dokunmama; yetim alış/satış; günlük zarar sınırı; küsuratın taşınması ve küsuratla kapanış; CSV |
| `test_demo_yurutme.py` | 22 | Ortam koruması, izin listesi, önek ve emir türü koruması, GET/POST ağ hatası ayrımı, imza, kimlikler, planlayıcı (yuvarlama, limitli stop, filtre sorunları, OCO, çıkış), hata sınıflandırma, akış ayrıştırma ve abonelik imzası, `serverShutdown`, ayarlar ve YAML aynılığı, anahtar yok/çevrimdışı/anahtar var kurulumu, hesabın çekim bayrağının engel sayılmaması ve işlem kapalı hesabın reddi, Demo uçları, arayüzden kapatma ve acil durdurma, Telegram |
| `test_demo_cli.py` | 9 | Anahtar kurulumunda yalnızca genel yarının gösterilmesi, geçersiz API Key, yarım kalan denemenin aynı çifti kullanması, doğrulamanın yalnızca okuması, çekim bayrağının engel sayılmaması, işlem kapalı hesabın reddi, sınama emrinin dolmadan gönderilip iptal edilmesi, onaysız gönderilmemesi, Mac dışında çalışmaması |

Bütün paket: **583 test** geçiyor.

Arayüz gerçek tarayıcıda (Chromium; 1280 px 1×/2×, 390 px 2×/3×) sahte
borsaya bağlı bir sunucuyla sınandı: yatay taşma yok, sekme değiştirirken tek
zamanlayıcı, konsolda Demo kaynaklı hata yok; önizleme, gönderme (onay
penceresiyle), iptal, kapatma, ayar kaydı, acil durdurma, kâğıt sekmesindeki
Demo seçeneği.

---

## 13. Bu fazın söylemediği şeyler

- **Demo sonuç canlı sonuç değildir.** Demo defteri ayrıdır; dolumlar,
  kuyruk sırası ve kayma canlıdan farklı olabilir.
- **Demo bir avantaj üretmez, yürütmeyi sınar.** Faz 2'nin bulgusu geçerli:
  bu kapsamda maliyet sonrası yön bilgisi bulunmadı.
- **Berk'in Mac'inde henüz sınanmayanlar:** Demo'da "Kendi ürettiğim"
  (Ed25519) anahtar seçeneği, gerçek Demo hesap akışı, OTOCO'nun gerçek
  borsadaki davranışı, girişi bekleyen hedef/stopun kimlikle sorgulanıp
  sorgulanamadığı. `demo-sina` bunların hepsini ekrana yazar.
- Mac uyurken borsadaki stop ve hedef çalışır, ama **yeniden koruma** (kısmi
  dolum, süresi dolan stop) uygulama uyanınca yapılır.

---

## 14. Onay

Faz 5, Berk'in onayını bekliyor. Onaydan önce Berk'in Mac'inde yapılacaklar:
`demo-anahtar`, `demo-sina`, arayüzde küçük bir elle Demo emri.
