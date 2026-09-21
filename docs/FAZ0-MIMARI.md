# Faz 0 — Mimari Planı, Doküman İncelemesi ve Risk Listesi

Hazırlayan: Claude · Tarih: 2026-09-21
Kaynak şartname: `SPEC.md` · Bu doküman SPEC.md §12 ve §10/Faz 0 çıktısıdır.

> Bu doküman **onay bekliyor**. Onaylanmadan kod yazılmayacak.

---

## 1. Ne inceledim

Binance Spot API resmi dokümantasyon deposunun **güncel `master` dalı** (CHANGELOG son güncelleme
tarihi: **2026-09-18**) baştan sona okundu. İncelenen dosyalar: `CHANGELOG.md`, `rest-api.md`,
`web-socket-api.md`, `web-socket-streams.md`, `user-data-stream.md`, `filters.md`, `enums.md`,
`demo-mode/general-info.md` ve ilgili SSS dosyaları (`trailing-stop`, `commission`,
`order_count_decrement`, `order_amend_keep_priority`, `pegged_orders`,
`price_range_execution_rules`, `api_key_types`).

Kütüphane tarafında PyPI üzerinden `binance-sdk-spot`, `binance-connector`, `ccxt` ve
`python-binance` paketlerinin **güncel sürüm ve bakım durumu** ile resmi SDK'nın değişiklik
günlüğü doğrudan okundu.

Aşağıdaki maddelerin tamamı dokümandan doğrulanmıştır; hiçbir endpoint, parametre veya limit
tahminle yazılmamıştır.

---

## 2. Şartnamedeki varsayımların doğrulanması

| SPEC.md'deki ifade | Durum | Not |
|---|---|---|
| `listenKey` emekliye ayrıldı, kullanma | ✅ **Doğru** | `POST/PUT/DELETE /api/v3/userDataStream` ve `userDataStream.start/ping/stop` **2026-02-20 07:00 UTC** itibarıyla kaldırıldı (CHANGELOG 2025-10-24, 2026-01-21). |
| User Data Stream, WebSocket API üzerinden | ✅ **Doğru** | `userDataStream.subscribe` (oturum kimlik doğrulaması ile) veya `userDataStream.subscribe.signature` (her oturumda, imza ile). |
| Ed25519 zorunlu | ✅ **Doğru** | `session.logon` ve `userDataStream.subscribe` **yalnızca Ed25519** anahtarlarını destekliyor. Doküman ayrıca Ed25519'u performans ve güvenlik açısından öneriyor. |
| OTOCO ile borsada koruma | ⚠️ **Kısmen** | Endpoint mevcut (`POST /api/v3/orderList/otoco`) ama kritik bir sınırı var → **bkz. Risk #1**. |
| Komisyonu borsadan çek | ✅ **Doğru** | `GET /api/v3/account/commission` (ağırlık 20, `symbol` **zorunlu**) → ama dört ayrı komisyon alanı var → **bkz. Risk #3**. |
| Yerel `trailingDelta` desteği | ⚠️ **Sembole bağlı** | `exchangeInfo` içindeki `allowTrailingStop` bayrağı sembol bazında; her sembolde açık değil. |
| Binance MCP sunucusunu kullanma | ✅ Uyuldu | Tamamen REST + WebSocket API ile ilerlenecek. |
| Testnet ile emir mekaniği doğrulaması | 🔄 **Daha iyisi var** | → **bkz. Öneri #8 (Demo Mode)**. |

---

## 3. Şartname yazıldıktan sonra değişen / şartnamede olmayan konular

Bunlar emir gönderen bir bot için opsiyonel değil, **zorunlu** ele alınması gereken konular.

### 3.1 Price Range Execution Rule (2026-03'ten itibaren kademeli olarak devrede)
Artık emirler, hareketli bir **referans fiyat** etrafındaki bir aralığın dışında eşleşemiyor ve
bu yüzden **iptal olabiliyor**. Ayrıca `PERCENT_PRICE`, `PERCENT_PRICE_BY_SIDE`, `MIN_NOTIONAL`
ve `NOTIONAL` filtreleri artık referans fiyat mevcutsa **onu** kullanıyor (CHANGELOG 2026-05-06).

Yapılacaklar:
- `GET /api/v3/executionRules` ile sembolün aralık çarpanlarını çek.
- `<symbol>@referencePrice` akışına abone ol (referans fiyat sürekli değişiyor).
- Emir yanıtlarındaki ve `executionReport` olaylarındaki **`expiryReason` / `eR`** alanını işle.
- `-2043 NO_REFERENCE_PRICE` hatasını ele al.

### 3.2 `CANCEL_ONLY` sembol durumu (2026-07-07)
`exchangeInfo` içinde yeni bir `symbolStatus` değeri. Coin uygunluk kontrolüne eklenmeli;
bu durumda yeni pozisyon açılmamalı.

### 3.3 İşlem endpoint ağırlıkları sıfırlandı (2026-04-02)
`POST /api/v3/order`, `DELETE /api/v3/order`, tüm `orderList/*` ve `order/amend/keepPriority`
istekleri **başarılı olduğunda 0 ağırlık** harcıyor. `RAW_REQUESTS` limiti 5 dakikada 300.000'e
çıktı.
**Sonuç:** Sık al-sat yapan bir bot için darboğaz artık `REQUEST_WEIGHT` değil,
**dolmamış emir sayacı (`ORDERS` rate limit)**. Bu, şartnamenin 4.5'teki "dolmazsa fiyat
güncelle" döngüsünü doğrudan etkiliyor → **bkz. Risk #6**.

### 3.4 `serverShutdown` olayı artık habersiz gelebiliyor (2026-06-09)
Eskiden kapanmadan 10 dakika önce gönderiliyordu; bu garanti **dokümandan kaldırıldı**.
Olay geldiği anda yeni bağlantı açılmalı, "10 dakikam var" varsayımı yapılmamalı.

### 3.5 Pegged (BBO) emirler ve `order.amend.keepPriority`
- **Pegged emirler:** `pegPriceType=PRIMARY` ile "en iyi alış fiyatından al" emri gönderilebiliyor.
  Fiyatı kendimiz hesaplayıp defterle yarışmaktan daha sağlam bir post-only giriş yöntemi.
  OTOCO da `workingPegPriceType` parametresini destekliyor. Sembolde `pegInstructionsAllowed`
  bayrağı kontrol edilmeli.
- **`order.amend.keepPriority` fiyat değiştiremez.** Yalnızca **miktar azaltmaya** izin veriyor.
  Yani limit emrin fiyatını güncellemek zorunda kalırsak tek yol `cancelReplace` ve
  **sıra önceliği kaybedilir**. Sembolde `amendAllowed` / `cancelReplaceAllowed` bayrakları
  kontrol edilmeli.

---

## 4. Kütüphane seçimi ve gerekçesi

**Seçim: resmi `binance-sdk-spot` (sürüm 11.3.0, 2026-09-02).**

| Paket | Güncel sürüm | Durum | Değerlendirme |
|---|---|---|---|
| **`binance-sdk-spot`** | **11.3.0** (2026-09-02) | Resmi, aktif | Binance'in kendi şemasından üretiliyor; `user_data_stream_subscribe()`, `user_data_stream_subscribe_signature()`, `order_list_otoco()`, pegged emir parametreleri, Ed25519 ve RSA anahtar desteği mevcut. Python 3.10–3.14. |
| `binance-connector` | 3.13.0 | **Resmen "deprecated"** | PyPI açıklamasında birebir "This is a deprecated lightweight library" yazıyor. İnternetteki örneklerin çoğu hâlâ bunu kullanıyor — **kullanmayacağız**. |
| `ccxt` | 4.5.81 (2026-09-19) | Aktif, çok borsalı | Spot için `userDataStream.subscribe.signature` desteğini uygulamış, yani kullanılabilir durumda. Ancak OTOCO/OPO, `amend.keepPriority`, pegged emirler, `executionRules` gibi Binance'e özgü yetenekleri soyutlama katmanı arkasında gizliyor veya hiç sunmuyor. |
| `python-binance` | 1.0.37 (2026-06-08) | Üçüncü parti | Kod tabanının büyük bölümü hâlâ `listenKey` merkezli. Önerilmiyor. |

**Gerekçe:** Şartnamenin zorunlu kriterleri (Ed25519 + WebSocket API + WS üzerinden User Data
Stream) üç seçenekten ikisinde karşılanıyor, ama bu uygulama **tek borsada** çalışacak ve
**Binance'e özgü** emir tiplerine (OTOCO, pegged, trailingDelta, executionRules) doğrudan
ihtiyaç duyuyor. Soyutlama katmanı burada fayda değil risk. Resmi SDK ayrıca Binance'in
şema değişiklikleriyle otomatik güncelleniyor.

**Yine de:** SDK'yı doğrudan her yerden çağırmayacağız. `src/albsat/exchange/` altında ince bir
sarmalayıcı (port/adapter) olacak; ileride ccxt'ye veya ham HTTP'ye geçmek gerekirse tek katman
değişecek.

---

## 5. Riskler, eksikler ve çelişkiler

### Risk #1 — OTOCO kısmi dolumda koruma sağlamıyor (ÇELİŞKİ, en kritik)

Şartname §4.5 hem "girişte OTOCO kullan, koruma borsada dursun" diyor, hem de "kısmi dolumları
doğru yönet, koruma emirlerinin miktarını dolan miktara eşitle" diyor.

Dokümanın birebir ifadesi: OTOCO'nun bekleyen emirleri (kâr al + stop çifti) deftere ancak
çalışan giriş emri **tamamen dolduğunda** (`fully filled`) konuluyor.

**Sonuç:** Giriş emri kısmen dolduğu sürece elimizde **borsada koruması olmayan** bir pozisyon
var. Mac uyursa veya uygulama çökerse bu miktar korumasız kalır. Bu, şartnamenin "her açık
pozisyonun borsada stop'u bulunur" güvencesiyle doğrudan çelişiyor.

**Önerim (onay bekliyor):**
1. Giriş boyutunu, sembolün defter derinliğine göre **tek seferde dolması muhtemel** olacak
   şekilde sınırla.
2. Bir **"korumasız süre nöbetçisi"** ekle: giriş kısmen dolduktan sonra `T` saniye (varsayılan
   **20 sn**, ayarlanabilir) içinde tamamlanmazsa kalan miktarı iptal et ve **dolan miktar için
   hemen OCO kur**.
3. Arayüzde ve denetim kaydında **"korumasız geçen süre"** metriğini açıkça göster.
4. Bu riskin sıfırlanamayacağını README'de ve arayüzde dürüstçe yaz: kısmi dolum ile OCO kurulumu
   arasındaki gidiş-dönüş gecikmesi kadar bir pencere her zaman kalır.

### Risk #2 — 100 USDT bütçe ile "işlem başı %0,5–1 risk" kuralı aritmetik olarak çakışıyor

Pozisyon büyüklüğü = (risk tutarı) / (stop mesafesi).
100 USDT bütçe ve %1 risk → 1 USDT risk. Scalping'e uygun dar bir stop (%0,3) ile gereken
pozisyon büyüklüğü **333 USDT** olur — bütçenin üç katı. Bütçe 100 USDT'ye sabitlendiğinde
gerçekleşen risk %0,3'e iner, yani hedeflenen risk kuralı uygulanamaz.
Ters uçta, çok geniş bir stop ile pozisyon `NOTIONAL` minimumunun (tipik olarak 5–10 USDT)
altına düşebilir ve emir reddedilir.

Ayrıca 100 USDT'lik bir pozisyonda %0,5 brüt hedef = 0,50 USDT, komisyon sonrası ≈ 0,35 USDT.
Oransal olarak doğru ama mutlak rakamlar kuruş seviyesinde.

**Önerim:** 100 USDT'yi **doğrulama bütçesi** olarak koru (Faz 6 için doğru karar), ama risk
motoru "bütçe mi yoksa risk kuralı mı bağlayıcı" durumunu her öneride açıkça göstersin ve
`stepSize` yuvarlamasından doğan kuantalama hatasını da hesaba katsın. Gerçek performans ölçümü
kâğıt işlem modunda **oransal** yapılmalı.

### Risk #3 — Komisyon dört bileşenli, BNB indirimi hepsine uygulanmıyor

`GET /api/v3/account/commission` şunları döndürüyor: `standardCommission`, `specialCommission`,
`taxCommission` ve `discount`. Dokümandaki açıklama net: BNB indirimi **yalnızca
`standardCommission`** oranını düşürüyor. Yani efektif komisyon = standart(indirimli) + özel + vergi.

Ek olarak: `discount.enabledForAccount` **ve** `discount.enabledForSymbol` ayrı ayrı açık olmalı,
ve indirimin fiilen uygulanması için hesapta **BNB bakiyesi** bulunmalı.

**Önerim:** Komisyon modeli bu dört alanı da kullansın; ayrıca **BNB bakiyesi izleyicisi**
eklensin — BNB bittiğinde komisyonlar sessizce yükselir ve küçük hareketlerde kârı tamamen yer.

### Risk #4 — Başa-baş hesabı bacak tipine göre değişmeli

Şartname "giriş `LIMIT_MAKER`" ve "stop varsayılan `STOP_LOSS`" diyor. `STOP_LOSS` tetiklendiğinde
**piyasa emri**, yani **taker** komisyonu uygulanır. Hedef çıkışı `LIMIT_MAKER` ise maker'dır.
Dolayısıyla tek bir "gidiş-dönüş komisyonu" sabiti yanlış olur:
- Hedefe giden işlem: maker + maker
- Stopa giden işlem: maker + **taker**

**Önerim:** Başa-baş ve net marj, senaryo başına ayrı hesaplansın ve öneri kartında ikisi de
gösterilsin.

### Risk #5 — 1 dakikalık periyotta maliyet, tipik harekete yakın veya ondan büyük olabilir

Standart oranlarda gidiş-dönüş komisyonu ≈ %0,20 (BNB indirimiyle ≈ %0,15), üstüne spread ve
kayma biner. Büyük paritelerde 1m mumun tipik ATR'si sıkça bu eşiğin altında kalır.
Bu, şartnamenin kendi kuralı gereği (§4.3) sinyallerin **çoğunun otomatik eleneceği** anlamına
gelir.

**Önerim:** Faz 1'in ilk çıktısı bir **fizibilite taraması** olsun: seçilen coin(ler) için her
periyotta `ATR% / gidiş-dönüş maliyet` oranını hesaplayıp tabloyu göstereyim. Bu oran 1'in
altındaysa o periyotta örüntü aramanın anlamı yok — motoru kurmadan önce bunu bilmek, sonra
öğrenmekten çok daha ucuz.

### Risk #6 — Post-only yeniden fiyatlama döngüsü, dolmamış emir limitini yakar

`ORDERS` rate limit **dolmamış** emirleri sayıyor (dolan emirler sayacı düşürüyor). Bir OTOCO
tek başına bu sayaçtan **3 emir** harcıyor. "Dolmazsa iptal et, fiyatı güncelle, tekrar dene"
döngüsü doğrudan bu limite yükleniyor ve `-1015 "Too many new orders"` / HTTP 429 ile
karşılaşılıyor.
Ayrıca Risk #3.5'te belirtildiği gibi `amend.keepPriority` fiyat değiştiremediği için her
yeniden fiyatlama `cancelReplace` demek — yani **sıra önceliğinin kaybı** demek.

**Önerim:** Yeniden deneme sayısına yerel ve sert bir bütçe koy (varsayılan **3**), `exchangeInfo`
içindeki güncel `ORDERS` limitini okuyup yerel sayaç tut, ve giriş için **pegged (PRIMARY)**
emirleri değerlendir — yeniden fiyatlama ihtiyacını baştan azaltır.

### Risk #7 — Mac'te 7/24 tam otomatik çalışma güvenilir değil

`caffeinate` uykuyu engeller ama kapak kapalıyken pille çalışan bir Mac çoğu yapılandırmada yine
uyur. Şartname bunu kabul ediyor; ben bir adım ileri gidip şunu öneriyorum:
**Tam Otomatik modun Mac'te gözetimsiz açılmasına varsayılan olarak izin verme** (açıkça
onaylanırsa açılsın), ve tam otomatik hedefleniyorsa Faz 7'yi (VPS) öne al.
Her koşulda tek gerçek koruma borsa tarafındaki OCO emirleridir — bu arayüzde kalıcı olarak yazmalı.

### Risk #8 — Spot'ta "pozisyon" aslında coin envanteri

Açığa satış olmadığı için "sat ve aşağıdan geri al" stratejisinde elde tutulan coinin kendisi
maruziyettir. Risk motorundaki "bütçe" kavramı, USDT bakiyesini değil **USDT + o coinin güncel
değerini** birlikte ele almalı; aksi halde maruziyet olduğundan düşük görünür.

### Risk #9 — Anahtar izinlerinin doğrulanması

`GET /api/v3/account` yanıtı `canTrade`, `canWithdraw`, `canDeposit` ve `permissions` alanlarını
içeriyor; açılışta bunlar kontrol edilecek. Ancak **anahtar bazında** izinleri (özellikle
para çekme izninin kapalı olduğunu) kesin doğrulayan uç nokta Spot dokümanında değil,
Wallet/SAPI dokümanında. Faz 1'de bunu Wallet dokümanından doğrulayıp ekleyeceğim —
**tahminle yazmayacağım**. Doğrulanana kadar açılış kontrolü `GET /api/v3/account` üzerinden
yapılacak ve README'de kullanıcıya izinleri Binance arayüzünden görsel olarak da teyit etmesi
söylenecek.

---

## 6. Öneri #8 — Testnet yerine **Demo Mode** (en değerli bulgu)

Şartname §4.7'de Testnet kullanılmasını, ama "Testnet fiyat ve likiditesi gerçekçi olmadığı için
performans ölçümü paper modda yapılır" denmesini istiyor. Bu tespit doğru — ve Binance
**2026-01-29'da tam olarak bu sorunu çözen** bir ortam yayınladı: **Spot Demo Mode**.

Dokümandaki resmi karşılaştırma:

| SPOT Testnet | SPOT Demo Mode |
|---|---|
| Bakiyeler her ay sıfırlanır | Bakiyeyi istediğin zaman arayüzden sıfırlarsın |
| Bazen canlıdan önce yeni özellikler gelir | Her zaman canlı borsayla **aynı** özellikler |
| Fiyat ve emir defteri canlıdan **bağımsız** | Fiyat ve emir defteri canlıya **benzer** |
| Limitler ve filtreler "genel olarak" aynı | Limitler, dolmamış emir sayacı ve filtreler **birebir** aynı |

Uç noktalar: `https://demo-api.binance.com/api`, `wss://demo-ws-api.binance.com/ws-api/v3`,
`wss://demo-stream.binance.com/ws`.

**Önerim:** §4.7'deki 4 numaralı mod **"Testnet"** yerine **"Demo Mode"** olsun (Testnet'i
yalnızca henüz canlıda olmayan bir özelliği denemek gerekirse opsiyonel tutalım). Bu tek değişiklik
Faz 5'in kalitesini belirgin biçimde yükseltir: emir mekaniğini **gerçek filtreler, gerçek rate
limitler ve gerçeğe yakın likidite** altında doğrulamış oluruz.

> Binance'in kendi uyarısı da dokümanda yazıyor ve arayüze aynen koyacağım: "Gerçekçi piyasa
> verisi, gerçek piyasa verisi demek değildir. Demo Mode'da çalışan bir stratejinin canlıda da
> çalışacağını varsaymayın."

---

## 7. Mimari

### 7.1 Genel yapı

Tek bir Python paketi (`albsat`), `asyncio` tabanlı, tek süreçte çalışan ama modülleri birbirinden
bağımsız test edilebilen bir yapı. Tüm parasal değerler `Decimal`. İçeride her zaman UTC,
arayüzde Europe/Istanbul.

```
binance-alsat/
├── pyproject.toml            # bağımlılıklar sürüm bazında sabit
├── README.md                 # Türkçe kurulum + API anahtarı rehberi (ekran ekran)
├── .env.example .env.mac .env.vps
├── config/
│   ├── default.yaml          # risk limitleri, mod varsayılanları
│   └── symbols/              # coin bazlı ayarlar
├── src/albsat/
│   ├── core/                 # money(Decimal), filters, fees, clock, audit, ids
│   ├── exchange/             # keys, rest, ws_api, user_stream, market_stream,
│   │                         # ratelimit, endpoints(live|demo|testnet)
│   ├── data/                 # vision(toplu indirme), backfill, store, quality
│   ├── features/             # §4.2 özellik aileleri
│   ├── research/             # eventstudy, stats(BH/bootstrap), walkforward, report
│   ├── backtest/             # olay tabanlı motor
│   ├── strategy/             # periyot sihirbazı, sinyal motoru
│   ├── risk/                 # risk motoru, kill switch, devre kesiciler
│   ├── execution/            # planner, router, protection(kısmi dolum), reconcile
│   ├── modes/                # kapalı | öneri | paper | demo | yarı | tam
│   ├── notify/               # telegram
│   ├── api/                  # FastAPI (REST + WS yayını)
│   └── platform/             # macOS uyku/caffeinate, sağlık, heartbeat
├── frontend/                 # React + Vite + TS + Tailwind + lightweight-charts
├── tests/                    # birim, entegrasyon, kaos
└── deploy/                   # launchd plist, docker-compose
```

### 7.2 Bağlantı topolojisi

- **Piyasa verisi:** `wss://stream.binance.com` üzerinden `<symbol>@kline_<interval>`,
  `<symbol>@bookTicker`, `<symbol>@aggTrade`, `<symbol>@referencePrice`.
- **Emir + hesap:** `wss://ws-api.binance.com/ws-api/v3` üzerinde **Ed25519 ile `session.logon`**,
  ardından aynı bağlantıda `userDataStream.subscribe`. REST yalnızca geçmiş veri, `exchangeInfo`,
  `executionRules`, komisyon ve uzlaştırma için.
- **Toplu geçmiş veri:** `data.binance.vision` aylık/günlük arşivler, boşluklar
  `GET /api/v3/klines` ile tamamlanır.
- Ortam (canlı / demo / testnet) tek bir `endpoints.py` üzerinden değişir; kodun geri kalanı
  ortamdan habersizdir.

### 7.3 Tekrar eden tasarım kararları

- **Idempotency:** Her emre `albsat-<mod>-<strateji>-<ulid>` biçiminde `newClientOrderId`.
  Bot yalnızca `albsat-` önekli emirleri yönetir; elle açılan emirlere dokunmaz.
- **Doğruluk kaynağı borsadır.** Açılışta, yeniden bağlanmada ve uyanışta uzlaştırma çalışır.
- **Yalnızca kapanmış mum.** Kline akışındaki `x` bayrağı `true` olmadan hiçbir özellik veya
  sinyal hesaplanmaz; bunu doğrulayan bir birim testi olacak.
- **Bayat veri kilidi.** Son mum/tick `X` saniyeden eskiyse yeni pozisyon açılmaz.
- **Denetim kaydı** her mod değişikliği, ayar değişikliği ve emir için SQLite'a yazılır.

---

## 8. Faz planına önerdiğim düzeltmeler

Şartnamedeki faz yapısı korunuyor; üç ekleme öneriyorum:

- **Faz 1'e ek:** Fizibilite taraması (Risk #5) — veri indikten hemen sonra,
  `ATR% / maliyet` tablosu. Örüntü motoruna geçmeden önce "bu coin+periyot matematiksel olarak
  anlamlı mı" sorusunu cevaplar.
- **Faz 5:** "Testnet" yerine **Demo Mode** (Öneri #8). Kaos testlerine "kısmi dolum sırasında
  uygulama çökmesi" senaryosu eklenir (Risk #1).
- **Faz 6/7 sırası:** Tam Otomatik hedefleniyorsa VPS adımının Faz 6'dan **önce** yapılması
  değerlendirilmeli (Risk #7).

---

## 9. Onay bekleyen kararlar

1. **Demo Mode**, Testnet yerine geçsin mi? (önerim: evet)
2. **Kod nerede dursun?** GitHub deposu mu açalım, yoksa proje dosyaları klasöründe mi kalsın?
   (önerim: GitHub deposu — sürüm geçmişi, Mac'e ve VPS'e taşınabilirlik ve gizli bilgi taraması
   için gerekli. İzin verilmeden açılmayacak.)
3. **Hangi coin ve hangi periyotlarla başlayalım?** Faz 1 veri indirmesi buna bağlı.
   (önerim: `BTCUSDT` + tek bir altcoin ile başlayıp 1m/5m/15m/1h indirelim)
4. Risk #1 için önerdiğim **korumasız süre nöbetçisi** yaklaşımı ve 20 sn varsayılanı uygun mu?
5. Risk #2'deki bütçe/risk çelişkisinde 100 USDT bir **doğrulama bütçesi** olarak mı kalsın?

---

## 10. Sorumluluk notu

Bu uygulama kâr vaat etmez. Örüntü analizleri geçmiş verilere dayalı istatistiksel çıkarımlardır
ve yatırım tavsiyesi değildir; geçmiş performans geleceği garanti etmez. Faz 2'nin çoklu test
düzeltmesi ve maliyet modeli sonrasında **"işlem yapmaya değer bir avantaj bulunamadı"** sonucunun
çıkması gerçekçi ve geçerli bir sonuçtur; bu durumda bunu açıkça raporlayacağım.
