# PROMPT — Binance Spot Pattern Analizi, Al-Sat Önerisi ve Kontrollü Otomatik İşlem Uygulaması

> Bu metnin tamamını, boş bir proje klasöründe açtığın bir AI kodlama asistanına (ör. Claude Code) yapıştır. Köşeli parantezli alanları `[...]` istersen kendine göre doldur; doldurmazsan varsayılanlar kullanılır.

---

## 1. Rolün ve amacın

Sen; kripto piyasa mikro yapısı, kantitatif analiz, backtest metodolojisi, risk yönetimi ve güvenli yazılım mimarisi konusunda deneyimli kıdemli bir yazılım mühendisisin. Benim için **Binance Global (binance.com) Spot** piyasasında çalışan, Türkçe arayüzlü bir uygulama geliştireceksin.

Uygulamanın amacı:

1. Seçtiğim coinin geçmiş fiyat hareketlerini analiz edip **hangi örüntüler (pattern) oluştuğunda istatistiksel olarak yükseliş veya düşüş geldiğini** bulmak.
2. Bu analize göre bana **uygun zaman periyodunu seçtirmek** (öneri sunarak, gerekçesiyle).
3. **"Şu fiyattan al, şu fiyattan sat, stop şurada"** şeklinde net öneriler ve bu önerilerin **brüt/net marjlarını** göstermek.
4. Amacım **küçük fiyat hareketlerini değerlendirerek** sık al-sat yapmak.
5. İstediğimde sadece önerileri izleyebileyim; istediğimde **benim kontrolümde açılıp kapanan otomatik al-sat** çalışsın.
6. Benim öngöremediğim profesyonel önlemleri (risk, güvenlik, istatistiksel geçerlilik, operasyonel dayanıklılık) varsayılan olarak uygula ve bana öner.

**Temel ilke:** Güvenlik ve dürüstlük önce gelir. Uygulama kâr vaat etmez; bir örüntünün komisyon sonrası gerçekten avantaj sağlayıp sağlamadığını dürüstçe gösterir. Avantaj yoksa "işlem yapma" demesi de geçerli ve değerli bir çıktıdır.

---

## 2. Sabit kararlar (bunları değiştirme)

| Konu | Karar |
|---|---|
| Borsa | Binance Global (binance.com) |
| Piyasa | **Sadece Spot.** Kaldıraç, margin, futures, borç alma **yok**. Kod margin/futures endpoint'lerini hiçbir koşulda çağırmamalı. |
| Kotasyon | Varsayılan USDT pariteleri (ör. `XXXUSDT`). Arayüzde isteğe bağlı TRY karşılığı (`USDTTRY` fiyatından) gösterilebilir. |
| Çalışma yeri | Önce **macOS (Mac) üzerinde** geliştirme, kağıt işlem **ve otomatik işlem**; sonra aynı kodla **VPS'e (7/24)** taşınabilir olmalı. |
| Arayüz dili | Türkçe. Saatler Europe/Istanbul gösterilir, içeride UTC saklanır. |
| Başlangıç modu | Uygulama her açılışta **"Sadece Öneri"** modunda başlar. |

### Bağlantı yöntemi: REST + WebSocket API (MCP değil)

Binance'in resmi bir MCP sunucusu var, ancak bu sunucu sohbet tabanlı AI ajanları için tasarlanmış: her emirden önce insan onayı ister, ayrı bir "agentic" alt hesapla çalışır ve düşük gecikmeli otomatik bot kullanımı için uygun değildir. Bu yüzden:

- **Piyasa verisi:** Binance Spot **WebSocket Streams** (kline, bookTicker, aggTrade, gerekirse depth) + REST (geçmiş veri, exchangeInfo).
- **Emir ve hesap:** Binance Spot **REST API** ve/veya **WebSocket API**.
- **Hesap olayları (dolum, bakiye):** **WebSocket API üzerinden User Data Stream aboneliği.** Eski `listenKey` REST yöntemi emekliye ayrıldı, kullanma.
- **Toplu geçmiş veri:** `data.binance.vision` üzerindeki günlük/aylık kline arşivleri, eksikler REST `GET /api/v3/klines` ile tamamlanır.
- Resmi Binance Python bağlayıcısı veya ccxt'den hangisinin daha uygun olduğunu değerlendir ve seçimini gerekçelendir. Ed25519 anahtar desteği, WebSocket API ve User Data Stream desteği zorunlu kriterler.
- **Kod yazmadan önce güncel Binance Spot API dokümantasyonunu ve changelog'u kontrol et** (developers.binance.com). Endpoint, parametre veya limit uydurma; emin olmadığın her şeyi dokümandan doğrula.

---

## 3. Teknoloji yığını (önerilen, gerekçeyle değiştirebilirsin)

- **Backend:** Python 3.12+, `asyncio`, FastAPI (REST + WebSocket ile arayüze canlı yayın), Pydantic ile tipli konfigürasyon.
- **Analiz:** pandas/polars, numpy, indikatörler için pandas-ta veya TA-Lib, istatistik için scipy/statsmodels.
- **Backtest:** Komisyon, spread, kayma ve limit emir dolum mantığını gerçekçi modelleyen **olay tabanlı (event-driven) kendi backtest motoru**. Hızlı tarama için vektörel ön-eleme yapılabilir.
- **Depolama:** Başlangıçta SQLite (emirler, işlemler, sinyaller, denetim kaydı) + Parquet (mum verisi). İleride PostgreSQL'e geçilebilecek şekilde soyutla.
- **Frontend:** React + Vite + TypeScript, Tailwind, grafik için TradingView **lightweight-charts**. Koyu tema varsayılan, mobil uyumlu.
- **Bildirim:** Telegram botu.
- **Dağıtım:** Mac için `launchd` servisi; VPS için Docker Compose.
- **Parasal hesaplar:** Fiyat, miktar ve bakiye için **her yerde `Decimal`** kullan, `float` kullanma.

---

## 4. Modüller ve gereksinimler

### 4.1 Veri katmanı
- Coin seçildiğinde: `exchangeInfo`'dan sembol filtrelerini (PRICE_FILTER/tickSize, LOT_SIZE/stepSize, MARKET_LOT_SIZE, NOTIONAL, PERCENT_PRICE_BY_SIDE, MAX_NUM_ORDERS, MAX_NUM_ORDER_LISTS vb.) çek ve önbelleğe al.
- Birden çok periyot için geçmiş veri indir: 1m, 3m, 5m, 15m, 30m, 1h, 4h (varsayılan en az 1–2 yıl, 1m için en az 3–6 ay).
- Veri kalite kontrolü: eksik mum, tekrar eden kayıt, zaman boşluğu, sıfır hacim tespiti ve raporu.
- Canlı akışta: bağlantı kopunca otomatik yeniden bağlanma, aradaki boşluğu REST ile doldurma, `serverShutdown` olayını işleme.
- **Bayat veri koruması:** son veri X saniyeden eskiyse yeni işlem açılmaz, arayüzde uyarı çıkar.
- Sunucu saati farkını (`GET /api/v3/time`) periyodik ölç; `recvWindow` ve zaman damgalarını buna göre ayarla.

### 4.2 Örüntü (pattern) keşif motoru
Amaç: "Şu koşullar oluştuğunda sonraki N mumda fiyat şu dağılımla hareket etmiş" bilgisini güvenilir biçimde çıkarmak.

**Özellik (feature) aileleri:**
- Mum formasyonları (engulfing, hammer, doji, pin bar, inside/outside bar vb.)
- İndikatör durumları: RSI bölgeleri ve uyumsuzlukları, MACD kesişimleri, Bollinger sıkışma/genişleme, EMA dizilimi ve kesişimleri, VWAP'a uzaklık, ATR%
- Hacim: hacim patlaması (ortalamanın k katı), hacim–fiyat uyumsuzluğu
- Fiyat hareketi dizileri: son N mumun yön/büyüklük dizisinin kesikli hale getirilmiş kalıpları (motif madenciliği)
- Destek/direnç yakınlığı, önceki gün/hafta yüksek-düşükleri, yuvarlak rakamlar
- Piyasa rejimi: trend/yatay (ADX, Hurst vb.), volatilite rejimi
- **BTC etkisi:** BTC'nin kısa vadeli yönü ve volatilitesi (altcoinler BTC ile yüksek korelasyonlu)
- Zaman etkileri: günün saati, haftanın günü (UTC ve İstanbul saatiyle)
- Emir defteri dengesizliği (canlı modda, opsiyonel)

**Her örüntü için ölçülecekler (olay çalışması / event study):**
- Oluşum sayısı (n), sonraki 1/3/5/10/20 mumda getiri dağılımı (ortalama, medyan, yüzdelikler)
- İsabet oranı (yükseliş ve düşüş için ayrı ayrı)
- MFE / MAE (işlem süresince görülen en iyi/en kötü fiyat), yani hedefe mi yoksa stopa mı önce değdiği
- **Komisyon ve kayma sonrası beklenen değer (net expectancy)**

**İstatistiksel sağlamlık (zorunlu):**
- **Yalnızca kapanmış mumlarla** hesapla; geleceğe bakma (look-ahead) ve "repaint" hatalarını testlerle engelle.
- Veriyi zamana göre ayır: eğitim / doğrulama / test. **Walk-forward** analiz yap.
- Minimum örnek sayısı eşiği (varsayılan n ≥ 50), bootstrap güven aralıkları.
- **Çoklu test düzeltmesi:** yüzlerce örüntü test edileceği için şans eseri "iyi görünen" örüntüleri ele (Benjamini–Hochberg veya benzeri; strateji düzeyinde deflated Sharpe).
- Her örüntünün farklı dönemlerde ve rejimlerde **kararlılığını** göster (bir yıl çalışıp sonra bozulan örüntüyü işaretle).
- Her sonucu **rastgele giriş** ve **al-ve-tut** karşılaştırma ölçütleriyle kıyasla.
- Aşırı uyum (overfitting) riskini arayüzde açıkça belirt ("Bu örüntü az örneğe dayanıyor", "Test döneminde performans düştü" gibi).
- Makine öğrenmesi (ör. gradient boosting) **sonraki bir faz** olarak eklenebilir; eklenirse purged/embargoed cross-validation kullan.

### 4.3 Periyot seçim sihirbazı
Coin seçildikten sonra her periyot için bir karşılaştırma tablosu göster:
- Tipik mum hareketi (ATR%) ve bunun **gidiş-dönüş maliyete oranı**
- Net beklenen değer, işlem sıklığı (günde/haftada kaç sinyal), maksimum düşüş (drawdown)
- Spread ve likidite uygunluğu
- Önerilen periyot(lar) ve **gerekçesi**; son seçim bende. İstersem birden çok periyodu birlikte kullanabileyim (ör. üst periyot yön filtresi, alt periyot giriş zamanlaması).

**Küçük hareketler için kritik kural:**
`Minimum anlamlı hedef = alış komisyonu + satış komisyonu + spread + beklenen kayma + güvenlik payı`
Hedefi bu eşiğin altında kalan sinyaller **otomatik elenir**. Komisyon oranlarını **sabit yazma**; hesabın gerçek oranlarını `GET /api/v3/account/commission` ile çek (maker/taker farkı ve BNB ile komisyon indirimi dahil). Başa-baş (break-even) fiyatını her öneride göster.

### 4.4 Öneri (sinyal) motoru
Her öneri kartında şunlar olsun:
- **Yön ve aksiyon:** AL / SAT / BEKLE.
  - Spot'ta açığa satış olmadığı için düşüş sinyali şu anlama gelir: "elindeki coini sat", "alım yapma" veya "sat ve daha aşağıdan geri al" (coin adedini artırma stratejisi).
- **Giriş fiyatı** (limit), **Hedef 1 / Hedef 2** (kademeli kâr alma), **Stop fiyatı**, opsiyonel iz süren stop (trailing)
- **Brüt marj %**, **net marj %** (komisyon ve tahmini kayma sonrası), USDT ve TRY karşılığı
- **Başa-baş fiyatı**, **Risk/Ödül oranı**, stopta oluşacak zarar tutarı
- **Tarihsel isabet oranı (n=…)**, beklenen değer, güven skoru ve bu skorun nasıl hesaplandığı
- **Geçerlilik süresi** (ör. "3 mum içinde dolmazsa geçersiz")
- **Gerekçe:** hangi örüntüler/koşullar tetikledi, grafikte işaretli
- Önerilen pozisyon büyüklüğü (risk motorundan)
- Tüm fiyatlar tickSize'a, miktarlar stepSize'a yuvarlanmış ve NOTIONAL minimumu kontrol edilmiş olmalı.
- Her sinyal sonradan performans ölçümü için kayıt altına alınır (sinyal günlüğü): "önerilen vs gerçekleşen".

### 4.5 Emir yürütme
- Küçük hareketlerde maliyeti düşürmek için girişte öncelik **`LIMIT_MAKER` (post-only)**; belirlenen sürede dolmazsa iptal veya fiyat güncelleme (en fazla N deneme).
- **Pozisyon açılır açılmaz koruma borsada durmalı:** giriş için tercihen **OTOCO** emir listesi (`POST /api/v3/orderList/otoco`): giriş dolunca borsa otomatik olarak **kâr al + stop OCO** çiftini kurar. Alternatif: dolumdan hemen sonra `POST /api/v3/orderList/oco`.
  - Böylece Mac uyusa, internet kopsa veya uygulama çökse bile her açık pozisyonun borsada stop'u ve hedefi bulunur.
  - Stop bacağında hızlı düşüşlerde dolmama riskine karşı `STOP_LOSS` (tetiklenince piyasa emri) ile `STOP_LOSS_LIMIT` (yeterli ofsetle) arasında seçim yapılabilsin. Varsayılan `STOP_LOSS` olsun, riski arayüzde açıkla.
- Kısmi dolumları doğru yönet (koruma emirlerinin miktarını dolan miktara eşitle).
- Her emre önekli, benzersiz `newClientOrderId` ver (idempotency). Yeniden denemelerde çift emir oluşmasın.
- Piyasa emri kullanılırsa **kayma koruması** uygula (beklenen fiyattan maksimum sapma).
- Hata kodlarını sınıflandır ve anlamlı Türkçe mesajlarla göster (filtre ihlali, yetersiz bakiye, rate limit, zaman damgası hatası vb.).
- **Rate limit yönetimi:** yanıt başlıklarındaki ağırlık/emir sayaçlarını izle, 429'da geri çekil (exponential backoff), 418'de (IP banı) tüm işlemleri durdur ve alarm ver.
- **Uzlaştırma (reconciliation):** Doğruluk kaynağı her zaman borsadır. Açılışta, yeniden bağlanmada ve Mac uykudan uyandığında açık emirleri, emir listelerini, bakiyeleri ve son işlemleri borsadan çekip yerel durumla karşılaştır; tutarsızlığı düzelt ve raporla.
- Bot yalnızca kendi açtığı emirleri yönetir (clientOrderId önekiyle ayırt edilir); elle açtığım emirlere dokunmaz.

### 4.6 Risk yönetimi motoru (tüm limitler arayüzden ayarlanabilir, varsayılanlar temkinli)
- **Bot bütçesi:** Bot sadece ayırdığım tutarı kullanır `[varsayılan: 100 USDT]`; hesaptaki diğer bakiyeye dokunmaz.
- **İşlem başı risk:** bot bütçesinin %0,5–1'i. Pozisyon büyüklüğü stop mesafesine göre hesaplanır.
- **Günlük maksimum zarar:** `[varsayılan %3]` → aşılınca otomatik işlem kapanır, ertesi gün **ancak ben elle açarsam** devam eder.
- **Haftalık/aylık maksimum düşüş limiti.**
- **Art arda kayıp limiti:** `[varsayılan 4]` → otomatik işlem duraklar ve bildirim gelir.
- **Kayıp sonrası soğuma süresi** (ör. 2 mum boyunca yeni işlem yok).
- Maksimum eşzamanlı pozisyon sayısı, coin başına maksimum maruziyet, günlük maksimum işlem sayısı.
- **Piyasa koşulu filtreleri:** aşırı volatilite, spread'in normalin k katına çıkması, düşük likidite, BTC'de sert hareket, bayat veri → yeni işlem açılmaz.
- **Coin uygunluk kontrolü:** düşük hacim, geniş spread, Binance'in "Monitoring/Seed" etiketleri veya delist duyurusu olan coinlerde uyarı; otomatik modda engel.
- **Performans bozulma koruması:** canlı/kağıt sonuçlar backtest beklentisinden istatistiksel olarak anlamlı biçimde saparsa strateji otomatik devre dışı kalır ve beni uyarır.
- **KILL SWITCH (acil durdurma):** Tek tuşla (arayüzden ve Telegram'dan) → otomatik işlemi kapat + botun tüm açık emirlerini iptal et + (seçime bağlı) botun pozisyonlarını piyasadan kapat.

### 4.7 Çalışma modları ve kontrol
Her coin için ayrı ayarlanabilen modlar:

1. **Kapalı**
2. **Sadece Öneri:** sinyaller ve bildirimler, emir yok (varsayılan)
3. **Kağıt İşlem (Paper):** canlı veriyle gerçekçi simülasyon; komisyon, kayma ve limit emir dolum mantığı dahil
4. **Testnet:** emir mekaniğini Binance Spot Testnet'te doğrulama. Testnet fiyat ve likiditesi gerçekçi olmadığı için performans ölçümü paper modda yapılır.
5. **Yarı Otomatik:** her öneri için "Emri Gönder" butonu (arayüz ve Telegram'da onay)
6. **Tam Otomatik:** onaysız işlem, tüm risk limitleri aktif

Kurallar:
- **Canlıya geçiş kapısı:** Bir strateji paper modda en az `[7 gün / 30 işlem]` çalışıp beklentiyle uyumlu sonuç vermeden Tam Otomatik açılamaz. Bu kontrolü geçersiz kılmak için açık bir uyarı ekranı ve ek onay gerekir.
- Tam Otomatik'i açarken özet ekranı gösterilir (bütçe, risk limitleri, coin, periyot, strateji) ve onay için PIN veya coin adının yazılması istenir.
- Beklenmedik yeniden başlatmadan sonra borsadaki koruma emirleri yerinde kalır ama **Tam Otomatik kendiliğinden yeniden açılmaz** (ayarlanabilir, varsayılan kapalı).
- Tüm mod değişiklikleri, ayar değişiklikleri ve emirler **denetim kaydına (audit log)** yazılır.

### 4.8 Backtest ve raporlama
- Gerçek hesap komisyonları, spread, kayma modeli; limit emir için "fiyat dokundu" değil **"fiyat içinden geçti"** dolum varsayımı; gecikme simülasyonu.
- Metrikler: net kâr/zarar, isabet oranı, profit factor, beklenen değer, Sharpe/Sortino, maksimum düşüş, ortalama işlem süresi, piyasada kalma oranı, **toplam komisyonun brüt kâra oranı**.
- Monte Carlo (işlem sırası karıştırma) ile düşüş dağılımı.
- Backtest ↔ paper ↔ canlı sonuçlarını yan yana gösteren sapma raporu.
- Tüm işlemler için CSV dışa aktarım (muhasebe ve vergi kaydı için; tarih, çift, yön, fiyat, miktar, komisyon, USDT ve TRY karşılığı).

---

## 5. Güvenlik (zorunlu)

- **API anahtarı:** Binance'in önerdiği **Ed25519** tipinde oluşturulsun. Sadece **okuma + Spot işlem** izni açık olsun; **para çekme (withdrawal) izni kesinlikle kapalı**, margin/futures izinleri kapalı. Uygulama açılışta anahtarın izinlerini kontrol etsin; çekim izni açıksa **çalışmayı reddetsin**.
- **IP kısıtlaması:** VPS'te statik IP ile zorunlu. Mac'te ev IP'si değişebileceği için riskleri ve Binance'in IP kısıtlamasız anahtarlarla ilgili güncel kurallarını README'de açıkla.
- Mümkünse bot için **ayrı bir Binance alt hesabı (sub-account)** kullanılmasını öner; mümkün değilse bütçe sınırını yazılım uygulasın.
- Anahtarlar macOS'ta **Keychain**'de, VPS'te şifreli gizli dosya veya ortam değişkeninde tutulsun. Asla repoya, loglara ya da frontend'e gitmesin; `.gitignore` ve gizli bilgi taraması (pre-commit) eklensin.
- Arayüz: Mac'te sadece `127.0.0.1`'e bağlansın. VPS'te internete doğrudan açılmasın; Tailscale/WireGuard veya HTTPS + güçlü parola + TOTP (2FA) arkasında çalışsın.
- Bağımlılık sürümleri sabitlensin; güvenlik açığı taraması yapılsın.

---

## 6. Mac'e özel gereksinimler (Mac'te otomatik işlem de yapılacak)

- Otomatik mod açıkken uyku engellensin (`caffeinate` veya eşdeğeri). Pilde çalışıyorsa, kapak kapanırsa veya uyku riski varsa uyarı versin.
- **Uyku/uyanma ve ağ kopması algılansın** (saat sıçraması ve bağlantı durumu ile). Uyanınca önce uzlaştırma ve veri boşluğu doldurma yapılsın, sonra işlem devam etsin.
- Yazılımsal (borsada olmayan) iz süren stop gibi özelliklerin Mac uyurken çalışmayacağı arayüzde açıkça belirtilsin; kritik koruma **her zaman borsa tarafındaki emirlerle** sağlansın. Mümkünse Binance'in yerel `trailingDelta` desteği kullanılsın.
- `launchd` ile oturum açılışında otomatik başlatma (opsiyonel), çökmede yeniden başlatma.
- Uygulama kapalıyken veya bağlantı koptuğunda Telegram'a "bot çevrimdışı" bildirimi için basit bir heartbeat mekanizması (VPS aşamasında harici izleme ile güçlendirilecek).

## 7. VPS aşaması

- Aynı kod tabanı, ortam bazlı konfigürasyon (`.env.mac`, `.env.vps`).
- Docker Compose, otomatik yeniden başlatma politikası, sağlık kontrolleri.
- Log rotasyonu, veritabanının günlük yedeği, harici uptime izleme ve alarm.
- Mac'ten VPS'e geçiş rehberi (veri ve ayarların taşınması, IP kısıtlamasının güncellenmesi).

---

## 8. Arayüz (Türkçe, sade ve şık)

1. **Coin seçici:** arama; 24s hacim, spread, volatilite ve uygunluk rozetleri, uyarılar.
2. **Periyot sihirbazı:** karşılaştırma tablosu ve önerilen seçim.
3. **Ana grafik:** mum grafiği; örüntü işaretleri, giriş/hedef/stop çizgileri, geçmiş sinyaller ve sonuçları.
4. **Öneri kartları:** bölüm 4.4'teki tüm alanlar; yarı otomatik modda "Emri Gönder".
5. **Örüntü kütüphanesi:** keşfedilen örüntüler, istatistikleri, güven aralıkları, kararlılık grafikleri, "neden güvenilir/güvenilmez" açıklaması.
6. **Otomatik işlem paneli:** büyük AÇ/KAPAT anahtarı, mod seçimi, bütçe ve risk limitleri, günlük zarar limitine kalan tutar, belirgin kırmızı **ACİL DURDUR** butonu.
7. **Pozisyonlar ve emirler:** açık pozisyonlar ve koruma emirlerinin durumu, işlem geçmişi, günlük/haftalık kâr-zarar, ödenen toplam komisyon.
8. **Sistem sağlığı:** WebSocket durumu, veri gecikmesi, rate limit kullanımı, saat farkı, son uzlaştırma zamanı.
9. **Denetim kaydı ve loglar.**
10. **Telegram entegrasyonu:** sinyal, dolum, stop, limit aşımı ve bağlantı kopması bildirimleri; `/durum`, `/durdur`, `/onayla` gibi komutlar (sadece benim chat ID'me yanıt versin).

Arayüzde kalıcı ve kısa bir not bulunsun: "Öneriler geçmiş verilere dayalı istatistiksel çıkarımlardır, yatırım tavsiyesi değildir; geçmiş performans geleceği garanti etmez."

---

## 9. Kalite ve test

- Birim testleri: fiyat/miktar yuvarlama ve filtre kontrolleri, komisyon ve marj hesapları, risk motoru limitleri, look-ahead kontrolü, kısmi dolum ve uzlaştırma mantığı.
- Entegrasyon testleri: Binance Spot Testnet üzerinde emir, OCO/OTOCO, iptal, kill switch senaryoları.
- Kaos testleri: WebSocket kopması, uygulamanın işlem ortasında çökmesi, Mac uyku simülasyonu, rate limit, borsa hata yanıtları.
- Yapılandırılmış loglama (JSON), her işlem için uçtan uca izlenebilir kimlik.
- Kurulum ve kullanım anlatan Türkçe README; API anahtarı oluşturma adımları (hangi izinler açık/kapalı olacak) ekran ekran anlatılsın.

---

## 10. Geliştirme aşamaları (her fazın sonunda DUR, bana özet ver ve onay iste)

| Faz | Kapsam | Kabul kriteri |
|---|---|---|
| **0** | Güncel Binance dokümanlarını incele, mimari dokümanı, klasör yapısı, kütüphane seçimi ve gerekçeleri | Onayım |
| **1** | Veri katmanı: geçmiş veri indirme, kalite raporu, canlı akış, sağlık paneli | Seçtiğim coin için veri eksiksiz ve canlı |
| **2** | Örüntü keşif motoru + backtest motoru + raporlar | Look-ahead testleri geçer; rapor rastgele girişle kıyas içerir |
| **3** | Periyot sihirbazı + öneri motoru + arayüz (**Sadece Öneri** modu) | Öneri kartları eksiksiz; marjlar komisyon sonrası doğru |
| **4** | Risk motoru + **Kağıt İşlem** modu + Telegram | Limitler test edilmiş; paper sonuçları kaydediliyor |
| **5** | Emir yürütme (Testnet): LIMIT_MAKER, OTOCO/OCO, kısmi dolum, uzlaştırma, kill switch | Kaos testleri geçer |
| **6** | Canlı **Yarı Otomatik** → küçük bütçeyle **Tam Otomatik** | Canlıya geçiş kapısı çalışıyor; güvenlik kontrolleri aktif |
| **7** | VPS dağıtımı (Docker), izleme, yedekleme | 7/24 çalışma ve alarm testi |

---

## 11. Yapmaman gerekenler

- Kâr garantisi veya "kesin" ifadesi kullanma; iddialı pazarlama dili kullanma.
- Kaldıraç, margin, futures veya borçlanma içeren kod yazma.
- Komisyon oranlarını, sembol filtrelerini veya limitleri koda sabit yazma; borsadan çek.
- Emir yürütme için Binance MCP sunucusunu kullanma.
- Doğrulanmamış endpoint veya parametre uydurma.
- Tahmin veya sinyal için kapanmamış mumu kullanma.
- API anahtarlarını loglama, ekranda gösterme veya frontend'e gönderme.
- Belirsiz bir konuda varsayım yapıp ilerleme; kritik kararlarda bana sor.

---

## 12. İlk adımın

Kod yazmaya başlamadan önce:

1. Güncel Binance Spot API dokümantasyonunu ve changelog'u incele.
2. Bu gereksinimlerde gördüğün **eksik, çelişkili veya riskli noktaları** ve **ek profesyonel önerilerini** listele.
3. Faz 0 mimari planını sun ve onayımı bekle.
