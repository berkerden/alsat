# Faz 4 — Risk motoru, kâğıt işlem ve Telegram

**Tarih:** 23 Eylül 2026
**Kapsam:** BTCUSDT ve SOLUSDT, 15m ve 1h, 100 USDT doğrulama bütçesi
**Şartname karşılığı:** SPEC.md §4.6, §4.7, §4.8, §5, §6, §8.10 ve §10'daki Faz 4 satırı
**Kabul kriteri (SPEC.md §10):** *"Limitler test edilmiş; paper sonuçları kaydediliyor."*

Bu belge Faz 4'ün **nasıl kurulduğunu ve neden öyle kurulduğunu** anlatır.

---

## 1. Faz 4 ne yapıyor, ne yapmıyor

Faz 3'ün sonunda uygulama bir öneri kartı gösterebiliyordu ama kartın
söylediği işlemi hiçbir yere yazmıyordu. Faz 4 üç şey ekler:

1. **Risk motoru.** Bir emrin açılıp açılamayacağına karar veren kapılar ve
   kapanan bir işlemden sonra sınır aşılıp aşılmadığını sınayan kurallar.
2. **Kâğıt işlem.** Gerçek canlı fiyatla çalışan, parası olmayan bir hesap.
   Emirler bu bilgisayardaki bir SQLite defterine yazılır, 1 dakikalık
   mumlarla doldurulur, komisyon ve kayma düşülür.
3. **Telegram.** Sinyal, emir, dolum, limit ve bağlantı bildirimleri; `/durum`,
   `/durdur` komutları.

Yapmadıkları da aynı derecede önemli:

- **Binance'e emir göndermez.** Depoda emir gönderen kod yok. `api/` altında
  imzalı istek kodunun hiç çağrılmadığını bir test dosyaları tarayarak denetler
  (`test_arayuz_katmani_imzali_istek_ve_anahtar_koduna_erismez`).
- API anahtarı yalnızca bir iş için kullanılır: hesaba özel **komisyon
  oranını okumak**. Anahtar salt okumalıdır ve kullanıcı istemedikçe hiç
  oluşturulmaz (§11).
- Kabul edilmiş kural olmadığı için (Faz 2 sonucu) kâğıt işlem kendiliğinden
  emir **açmaz**. Kural çıkarsa açar; o zamana kadar kâğıt işlem elle
  girilen emirlerle sınanır (§7).

---

## 2. Kabul kriterinin okunuşu

**"Limitler test edilmiş."** Şartnamenin §4.6'da saydığı her limit için en az
bir test var: sınırın hemen altında emir açılır, sınırda ya da üstünde açılmaz,
aşıldığında otomatik işlem kapanır ve bildirim gider. Testler gerçek zamanı
beklemez; risk motoru saf fonksiyondur ve "şimdi" parametredir. Gün, hafta ve
ay sınırları İstanbul saatiyle, sınırın iki yanındaki anlarla sınanır.

**"Paper sonuçları kaydediliyor."** Her kâğıt emrin bütün hayatı (verildi,
doldu, kapandı ya da iptal edildi) SQLite'ta tek satırdır. Sonuçlar arayüzde
özetlenir, CSV olarak indirilir ve uygulama kapanıp açıldığında kaldığı yerden
devam eder. Bakiye ayrıca saklanmaz, satırlardan hesaplanır; ikinci bir bakiye
kaydı er ya da geç satırlarla ayrışırdı.

---

## 3. Risk motoru (`risk/engine.py`, `risk/limits.py`)

### 3.1 İşlem öncesi kapılar

Bir emir açılmadan önce her kural ayrı bir kapıdır ve sonucu gerekçesiyle
döner. Tek kapı kapalıysa emir açılmaz; arayüz hangi kapının neden kapalı
olduğunu satır satır yazar.

| Kapı | Kapalı olduğu durum |
|---|---|
| Mod | Coin Kâğıt İşlem modunda değil |
| Fiyatlar | Stop girişin altında değil, hedef girişin üstünde değil |
| Performans bozulması | Kural, beklentisinin anlamlı ölçüde altında kaldığı için durdurulmuş (§8) |
| Günlük zarar | Bugünkü net zarar sınıra ulaşmış |
| Günlük zarar (ileriye bakış) | Bugünkü zarar + açık pozisyonların stop zararı + bu emrin stop zararı sınırı aşıyor |
| Haftalık / aylık düşüş | Dönemin en yüksek bakiyesinden düşüş sınıra değmiş |
| Art arda kayıp | Üst üste zararla kapanan işlem sayısı sınırda |
| Kayıp sonrası soğuma | Bu coinde zararla kapanan işlemden sonra N mum geçmemiş |
| Eşzamanlı pozisyon | Açık pozisyon + bekleyen giriş sayısı sınırda |
| Günlük işlem sayısı | Bugün açılan giriş emri sınırda |
| Pozisyon büyüklüğü | Miktar, borsanın en küçük miktar/tutar filtresinin altında kalıyor ya da nakit yetmiyor |
| Coin maruziyeti | Bu coine bağlı tutar sınırı aşıyor |
| Piyasa kapıları | §4 |

"İleriye bakış" kapısı şunun için var: günlük sınırın hemen altında açılan tek
bir işlem stopa giderse sınır bir işlem boyu aşılırdı. Kapı, olabilecek en kötü
sonucu şimdiden sayar.

### 3.2 Varsayılan limitler

Hepsi arayüzden değişir; değer geçerli aralığın dışındaysa **reddedilir**,
sessizce kırpılmaz. "Günlük zarar %300" yazan biri yazım hatası yapmıştır ve
bunu duymalıdır. Her değişiklik denetim kaydına eski ve yeni değeriyle yazılır
ve bildirim olarak gider.

| Limit | Varsayılan | Geçerli aralık |
|---|---|---|
| İşlem başına risk | bütçenin %1'i | %0.1 – %2 |
| Günlük azami zarar | %3 | %0.5 – %20 |
| Haftalık azami düşüş | %6 | %1 – %50 |
| Aylık azami düşüş | %10 | %1 – %80 |
| Art arda kayıp sınırı | 4 işlem | 1 – 20 |
| Kayıp sonrası soğuma | 2 mum | 0 – 50 |
| Aynı anda en fazla pozisyon | 1 | 1 – 10 |
| Coin başına azami maruziyet | %100 | %5 – %100 |
| Günlük azami işlem | 20 | 1 – 500 |
| Bayat veri eşiği | 180 sn | 60 – 3600 |
| Aşırı oynaklık eşiği | ATR medyanının 3 katı | 1.5 – 20 |
| Spread eşiği | normalin 5 katı | 1.5 – 50 |
| Spread üst sınırı | %0.10 | %0.01 – %2 |
| Asgari 24 saatlik hacim | 5.000.000 USDT | 0 – 100 milyar |
| BTC sert hareket eşiği | 60 dakikada %3 | %0.5 – %20 |
| Performans sınaması için asgari işlem | 10 | 5 – 200 |
| Performans bozulma eşiği | 2.33 standart hata | 1.64 – 5 |

Varsayılanlar `config/default.yaml` ile aynıdır; `test_risk_motoru.py` iki
yerin ayrışmadığını dosyayı okuyarak denetler.

### 3.3 İşlem sonrası sınırlar

Kapanan her işlemden sonra günlük zarar, haftalık/aylık düşüş ve art arda
kayıp sınanır. Biri aşıldıysa:

1. Bütün coinler **Sadece Öneri** moduna çekilir.
2. Bekleyen giriş emirleri iptal edilir. Açık pozisyonlar hedef ve stopuyla
   kalır; gerçek hesapta da borsadaki emirler moddan bağımsızdır.
3. Telegram'a ve arayüze bildirim gider.

Kâğıt işlem ancak kullanıcı elle yeniden açarsa sürer. Yeni gün başlaması
kendiliğinden açmaz.

### 3.4 Pozisyon büyüklüğü ve maliyet

Faz 3'ün `size_position` fonksiyonuna iki seçenek eklendi (Faz 3 kartının
davranışı değişmedi):

- **Kayma stopun içinde.** Stop tetiklenince piyasa emriyle satılır ve aleyhe
  kayar. Mesafe ve zarar, stop fiyatının kayma kadar altından hesaplanır.
- **Komisyon riskin içinde** (`costs_in_risk`). Miktar, *komisyon dahil* stop
  zararı hedeflenen riski aşmayacak biçimde hesaplanır. İşlem başına risk
  sınırı kesin sınırdır; komisyon onu delemez.

Kart, bütçenin mi risk kuralının mı bağlayıcı olduğunu ve `stepSize`
yuvarlamasının kaybını göstermeye devam eder.

---

## 4. Piyasa kapıları (`risk/market.py`, `data/live.py`)

Şartnamenin beş koşulu ve coin uygunluğu:

| Kapı | Ölçüm |
|---|---|
| Bayat veri | Son fiyatın yaşı |
| Aşırı oynaklık | İşlem periyodunda güncel ATR(14)% ÷ son 30 günün medyanı |
| Spread | En iyi alış-satış farkı; hem normalin katı hem mutlak üst sınır |
| Likidite | Borsanın 24 saatlik USDT hacmi |
| BTC sert hareket | Son 60 adet 1m BTC mumunda ilk açılışa göre en büyük sapma |
| Coin uygunluğu | Borsanın yayımladığı sembol durumu `TRADING` mi |

**Ölçülemeyen koşul geçmiş sayılmaz.** Spread hiç ölçülemediyse kapı
"ölçülemedi" der ve işlem açılmaz. Bilinmeyeni iyi saymak, filtrenin tam
gerektiği anda (bağlantı bozukken, piyasa çalkantılıyken) devre dışı kalması
demektir.

**Spread ısınması.** Spread'in "normal" değeri, saniyede bir alınan
örneklerin son bir saatlik medyanıdır. En az 300 örnek (5 dakika) birikmeden
medyan hesaplanmaz. Uygulama açıldıktan sonraki ilk 5 dakikada yeni kâğıt emir
açılamaması bu yüzdendir ve arayüz bunu söyler.

**Monitoring/Seed etiketi uydurulmadı.** Binance bu etiketi Spot API'de
yayımlamıyor. Uygunluk, yayımlanan sembol durumuna ve hacme dayanıyor.

---

## 5. Kâğıt işlem motoru (`paper/`)

### 5.1 Dolum kuralları

Şartname limit emirde "fiyat dokundu" değil **"fiyat içinden geçti"**
varsayımını istiyor. Kurallar `paper/fills.py`'nin başında dokuz madde olarak
yazılı; özü şu:

- Giriş `LIMIT_MAKER` alıştır ve yalnızca bir 1m mumun en düşüğü giriş
  fiyatının **altına indiyse** dolar. Eşit olması yetmez.
- Emrin verildiği dakikanın mumu sayılmaz; o mumun en düşüğü emirden önce
  gelmiş olabilir.
- Stop piyasa emriyle satar: dolum `min(stop, mumun açılışı)` fiyatından kayma
  kadar aşağıdadır. Boşlukla açılan mumda açılıştan dolar. Komisyon taker'dır.
- Hedef `LIMIT_MAKER` satıştır, mumun en yükseği hedefin üstüne çıkarsa dolar.
- Aynı mumda stop da hedef de görülürse **stop** kabul edilir.
- Komisyon alınan varlıktan düşülür. Satış miktarı `stepSize`'a aşağı
  yuvarlanır; artan küsurat ("toz") hesapta kalır ve sonraki satışa eklenir.
  Böylece toz birikmez, en fazla bir `stepSize` kadar kalır.

Belirsizlik varsa sonuç aleyhimize seçilir. Kâğıt sonuç gerçek sonuçtan
**kötü** çıkma eğilimindedir; tersi olsaydı kâğıt işlem yanıltırdı.

**Post-only kapısı.** Giriş fiyatı en iyi satışın üstündeyse `LIMIT_MAKER`
borsada reddedilirdi (hemen eşleşirdi). Kâğıt işlem de reddeder. Kural
sinyalinde giriş, en iyi satışın altına (en iyi alışa) çekilir ve bu not
edilir.

Kısmi dolum modellenmez. 100 USDT'lik emirler BTCUSDT ve SOLUSDT defterinde
tek seferde dolar; bütçe büyürse bu varsayım yeniden düşünülmeli.

### 5.2 Uygulama kapalıyken ne olur

Mac uyurken ya da uygulama kapalıyken kaçırılan 1m mumlar açılışta REST ile
çekilir (en fazla 30 gün geriye) ve işlenir. O mumlarda **borsa tarafında**
olacak şeyler olur: limit giriş dolabilir, stop ve hedef çalışabilir.
**Uygulama tarafında** olacak şeyler olmaz: süresi dolan girişi iptal etmek ya
da süresi dolan pozisyonu kapatmak uygulamanın işidir ve uygulama o an
çalışmıyordu. Bu işler açılışta yapılır ve bildirimde "uygulama kapalıyken"
diye yazar. Gerçek hesapta da böyle olur; Binance'te bir limit emri kendi
kendine süresi dolup silinmez.

Uyku, duvar saati ile tekdüze saat arasındaki sıçramadan anlaşılır. Uyanınca
akış yeniden bağlanır ve aynı uzlaştırma yapılır.

### 5.3 Uyku engeli (`core/awake.py`)

Şartname §6: "Otomatik mod açıkken uyku engellensin." Kâğıt işlemde en az bir
coin varken macOS'un kendi aracı `caffeinate -i -w <pid>` çalışır; Mac boşta
kaldı diye uyumaz. `-w` sayesinde uygulama kapanınca caffeinate de kapanır.
Kapak kapanınca Mac yine uyur; bunu hiçbir kullanıcı aracı engelleyemez. Mac
pille çalışıyorsa arayüz bunu sarı yazıyla uyarır.

### 5.4 Hesap dönemleri

Kâğıt hesabı sıfırlamak yeni bir dönem açar: bakiye bot bütçesine döner, özetler
sıfırdan sayılır. Eski işlemler silinmez, CSV'de kalır. Bekleyen emir ya da
açık pozisyon varken sıfırlama yapılamaz ve onay için `SIFIRLA` yazmak gerekir.

---

## 6. Modlar ve acil durdurma (`modes/state.py`)

Faz 4'te seçilebilen modlar: **Kapalı**, **Sadece Öneri**, **Kâğıt İşlem**.
Demo, Yarı Otomatik ve Tam Otomatik listede görünür ama kilitlidir.

**Uygulama her açılışta bütün coinleri Sadece Öneri'ye alır** (SPEC §2).
Önceki oturumda kâğıt işlemde olan coin kendiliğinden kâğıt işleme dönmez;
arayüz "önceki oturumda kâğıt işlemdeydi" der ve kullanıcı açar.

**ACİL DURDUR** arayüzün üst şeridinde her sekmede görünür (telefonda da).
Bütün coinleri Sadece Öneri'ye çeker ve bekleyen emirleri iptal eder. Açık
pozisyonları kapatmak ayrı bir seçimdir (`/durdur kapat`, arayüzde "Kapat"
düğmesi), çünkü stopu ve hedefi olan bir pozisyonu piyasa fiyatından kapatmak
çoğu zaman gereksiz bir zarardır.

---

## 7. Elle kâğıt emir

Kabul edilmiş kural olmadığı için kâğıt işlem kendiliğinden hiç emir açmayacak.
Berk bu durumda ne yapılacağına "getiri sağlama amacına uygun şekilde" karar
vermeyi Claude'a bıraktı (22 Eylül 2026). Karar:

- **Elle kâğıt emir eklendi.** Kullanıcı giriş, hedef ve stopu kendisi yazar;
  emir kural emirleriyle **aynı risk kapılarından** geçer. "Kapıları sına"
  düğmesi emir açmadan hangi kapının neden kapalı olduğunu gösterir.
- **Sonuçları ayrı sayılır.** Kural işlemleri ve elle işlemler ayrı özetlenir;
  kuralların beklentiyle kıyası elle emirlerle karışmaz.
- **Kabul edilmemiş adaylar otomatik işlenmez.** Faz 2'nin "incelenen
  adaylar" listesindekiler çoklu test düzeltmesinden geçmedi; onları kâğıtta
  otomatik çalıştırmak, gürültüyü sınamak için zaman harcamak olurdu.
- **Hiçbir ayar getiri garanti etmez.** Faz 2 bu kapsamda ölçülebilir bir yön
  bilgisi bulamadı. Elle emirlerin kâğıt sonucu, kullanıcının kendi kararlarının
  maliyet sonrası ne getirdiğini ölçer; bir kuralın kanıtı değildir.

Fiyatlar `tickSize`'a yuvarlanır: giriş aşağı, hedef ve stop yukarı. Yuvarlama
olduysa ekranda yazar.

---

## 8. Kural performans sınaması

Bir kural en az `performans_min_islem` (varsayılan 10) kâğıt işlemden sonra
beklentisiyle kıyaslanır. Sınama **tek yönlüdür**: gerçekleşen ortalama net,
beklenenin `performans_z_esigi` (varsayılan 2.33, yaklaşık %1 yanılma payı)
standart hata altındaysa kural durdurulur ve bildirim gider. Beklentiden iyi
gitmek durdurma sebebi değildir. Durdurulan kural arayüzden elle yeniden
açılır; sayım o andan başlar.

Beş işlemle bir kuralı yargılamak gürültüyü yargılamaktır; asgari sayı bu
yüzden var.

## 9. Canlıya geçiş kapısı

Şartname: bir strateji kâğıtta en az **7 gün ve 30 işlem** çalışıp beklentiyle
uyumlu sonuç vermeden Tam Otomatik açılamaz. Faz 4'te bu kapı **bilgi
amaçlıdır**: arayüz her kural için gün ve işlem sayısını gösterir. Zorunlu
hâle gelmesi, Tam Otomatik modun kendisiyle birlikte Faz 6'dadır.

---

## 10. Canlı veri (`exchange/market_stream.py`, `paper/runner.py`)

- **Birincil kaynak WebSocket.** Tek bağlantı, birleşik akış adresiyle
  bütün coinlerin 1m/15m/1h mumlarını, en iyi alış-satışı (`bookTicker`) ve
  24 saatlik özetini (`miniTicker`) taşır. USDTTRY kuru CSV'deki TRY karşılığı
  için ayrıca izlenir. Bağlantı 24 saatte bir kendiliğinden yenilenir.
- **Yedek REST.** Akış 90 saniyeden uzun kopuksa fiyat ve mumlar REST ile
  yoklanır (defter 10 sn'de, özet ve mum dakikada bir). Akışta boşluk olursa
  önce REST ile doldurulur.
- **İstek bütçesi** (`exchange/ratelimit.py`). Borsa dakikada 6000 ağırlık
  tanır; uygulamanın kendi tavanı 600'dür. Tavanı aşacak istek
  **gönderilmez**. 429 gelirse `Retry-After` süresince, 418 (engel) gelirse
  engel süresince hiç istek gitmez. Normal çalışma dakikada birkaç on ağırlık
  harcar.
- **Kural sinyali.** Kapanmış 15m/1h mumda öneri motoru çalışır. AL sinyali
  bildirim olur; coin Kâğıt İşlem modundaysa ve kapılar açıksa kâğıt emir
  açılır. 2 dakikadan eski sinyal mumu emre dönüşmez.

Bu uzak geliştirme ortamı Binance adreslerine çıkamadığı için canlı akış burada
**gerçek Binance'e karşı sınanamadı**. Mesaj biçimleri Binance belgelerinden
alınan örneklerle, yeniden bağlanma ve yedek yoklama sahte akışla sınandı.
İlk gerçek bağlantı 23 Eylül 2026'da Berk'in Mac'inde kuruldu ve hatasız
çalıştı.

---

## 11. Komisyon ölçümü (`cli/anahtar.py`, `exchange/signed.py`, `data/commission.py`)

Komisyon koda sabit yazılmaz. Ölçülmediyse Binance'in genel standart oranı
(%0.1) **varsayım** olarak kullanılır ve arayüz bunu "varsayım" diye yazar.

`bash kurulum.sh anahtar`:

1. Bu bilgisayarda bir **Ed25519 anahtar çifti** üretir. Özel yarısı Mac'in
   Anahtar Zinciri'ne yazılır, hiçbir yere gönderilmez.
2. Genel yarısını ekrana yazar. Kullanıcı Binance'te "Kendi ürettiğim"
   (self-generated) anahtar oluşturup bunu yapıştırır ve yalnızca **Okumayı
   etkinleştir** iznini açık bırakır.
3. Binance'in verdiği API Key kimliğini sorar ve Anahtar Zinciri'ne yazar.
4. İki imzalı **okuma** isteği yapar: anahtarın izinleri
   (`/sapi/v1/account/apiRestrictions`) ve komisyon
   (`/api/v3/account/commission`). Para çekme izni açıksa komisyon okunmaz ve
   anahtar kullanılmaz.
5. Oranları `veri/komisyon.json`'a yazar. Kâğıt işlem bundan sonra bu oranları
   kullanır.

İmzalı istek sınıfında emir gönderen, iptal eden ya da hesabı değiştiren hiçbir
yöntem yoktur; izin verilen adresler iki satırlık bir listeyle sınırlıdır.

**Çözülen belirsizlik.** Kendi üretilmiş Ed25519 anahtarının `/sapi/`
adreslerinde kabul edilip edilmeyeceği bu ortamdan doğrulanamamıştı. Kod
güvenli tarafı seçer: izinler okunamazsa komut "Binance'e sorulamadı" der ve
**komisyonu okumadan durur**. 23 Eylül 2026'da Berk'in Mac'inde anahtar kabul
edildi, izin kontrolü ve komisyon okuması çalıştı. Ölçülen oran maker ve taker
için **%0.1**; Faz 1 ve Faz 2'de varsayılan oranla aynı, önceki sonuçlar
geçerli.

---

## 12. Telegram (`notify/`, `cli/telegram.py`)

`bash kurulum.sh telegram`:

1. Kullanıcı @BotFather'da bot açar, jetonu gizli girer (ekranda görünmez).
2. Jeton Telegram'a sorularak doğrulanır, Anahtar Zinciri'ne yazılır.
3. Kullanıcı bota `/start` yazar; sohbet kimliği Telegram'ın kendisinden
   okunur, elle girilmez. `veri/telegram.json` yalnızca sohbet kimliğini ve
   botun kullanıcı adını tutar; ikisi de sır değildir.
4. Deneme mesajı gönderilir.

Kurulumdan sonra arayüz açıksa **kapatılıp yeniden açılmalı**; çalışan uygulama
Telegram ayarını açılışta okur.

Bildirim türleri: sinyal, emir açıldı/doldu/kapandı/iptal, risk limiti aşıldı,
limit değişti, bağlantı koptu/uyku, açılış/kapanış. Mesajlar aynı sohbete en
fazla ~1 saniyede bir gider; 429 gelirse Telegram'ın söylediği kadar beklenir.
Jeton adresin içinde olduğu için hiçbir hata mesajına adres yazılmaz.

Komutlar: `/durum`, `/durdur`, `/durdur kapat`, `/onayla` (Faz 6'da açılacak),
`/yardim`. Komutlara **yalnızca kurulumda kaydedilen sohbetten** yanıt verilir;
başka biri botu bulup yazsa sessizce yok sayılır.

Telegram kurulu değilse bildirimler arayüzdeki "Bildirimler" kutusunda görünür.

---

## 13. Arayüz (`api/paper_api.py`, `api/static/kagit.js`)

Yeni **Kâğıt işlem** sekmesi: bağlantı şeridi, modlar, kâğıt hesap, risk
göstergeleri, açık pozisyonlar ve bekleyen emirler, elle emir formu, sonuçlar
(kural ve elle ayrı), işlem geçmişi ve CSV, limitler, piyasa koşulları,
bildirimler, denetim kaydı, hesap sıfırlama.

Sekme açıkken ve sayfa görünürken 5 saniyede bir yenilenir. Bir bölüm yalnızca
verisi değiştiyse yeniden çizilir; kullanıcının yazdığı formlar (elle emir,
limitler) yenilemede silinmez.

### 13.1 Yazma uçları ve yerel koruma

Faz 3'te arayüzün yalnızca okuma uçları vardı. Faz 4'te mod değiştirme, limit,
elle emir, iptal, kapatma, acil durdurma ve sıfırlama uçları var. Hepsi yalnızca
yerel defteri değiştirir, ama başka bir sitenin tarayıcı üzerinden bunları
çağırabilmesi kabul edilemez. Bu yüzden her istek şu kontrollerden geçer:

- `Host` başlığı `127.0.0.1` ya da `localhost` olmalı (DNS rebinding'e karşı).
- `GET`/`HEAD` dışındaki her istek `X-Albsat-Istek: 1` başlığı ve
  `Content-Type: application/json` taşımalı. Başka bir sitenin formu ya da basit
  isteği bu başlığı ekleyemez; eklemeye çalışırsa tarayıcı önce izin sorar ve
  sunucu izin vermez.
- `Origin` başlığı varsa yerel olmalı; `Origin: null` reddedilir.
- `Sec-Fetch-Site` başlığı varsa `same-origin` ya da `none` olmalı.
- İstek gövdesinde bilinmeyen alan varsa istek 422 ile reddedilir.

### 13.2 Sayı biçimi

Para tutarları sunucudan metin olarak gelir (`Decimal`, yuvarlama hatası yok).
Bakiyeler 2, kâr/zarar ve risk tutarları 4 ondalıkla yazılır. Ondalık ayırıcı
uygulamanın her yerinde noktadır; yüzde işareti Türkçe yazımdaki gibi sayının
önündedir (`%0.76`).

---

## 14. Denetim kaydı (`core/audit.py`)

Mod değişiklikleri, limit değişiklikleri, emirler, iptaller, acil durdurma,
sıfırlama, açılış ve kapanış yalnızca eklenen bir tabloya yazılır. Her satır
kaynağı (arayüz, Telegram, canlı döngü, risk motoru) taşır. Kayıtta sır olmaz:
API anahtarı, Telegram jetonu ya da imza hiçbir zaman yazılmaz; bir test bunu
denetler.

---

## 15. Testler

| Dosya | Ne sınıyor |
|---|---|
| `test_risk_motoru.py` | Her limit, sınırın iki yanında; ileriye bakış; İstanbul günü/haftası/ayı; performans sınaması; varsayılanların yapılandırmayla aynılığı |
| `test_kagit_islem.py` | Dolum kuralları, toz, çevrimiçi/çevrimdışı ayrımı, sınır aşımında modların kapanması, CSV, özetler, dönemler |
| `test_canli_dongu.py` | İstek bütçesi (tavan, 429, 418), akış mesajları, yeniden bağlanma, uyku algılama ve uzlaştırma, sinyalden kâğıt emre, uyku engeli |
| `test_telegram.py` | Jetonun maskelenmesi, yalnızca kayıtlı sohbete yanıt, hız sınırı, komutlar |
| `test_komisyon_olcumu.py` | Ed25519 imzası, izin listesi, para çekme izninde ret, komisyonun kaydı |
| `test_kagit_arayuz.py` | Yerel koruma, bütün uçlar, yuvarlama, post-only kapısı, sıfırlama onayı, api/ altında imzalı istek olmaması |

Arayüz ayrıca gerçek tarayıcıda (Chromium, 1280 px ve 390 px, 1×/2×/3× piksel
yoğunluğu) sınandı: emir açma, iptal, mod değişimi, limit kaydetme ve ret,
acil durdurma, tek zamanlayıcı, başka sekmedeyken istek olmaması, yatay taşma
olmaması, telefonda alt şeridin düğmeleri kapatmaması.

---

## 16. Bu fazın söylemediği şeyler

- **Kâğıt sonuç gerçek sonuç değildir.** Dolum kuralları temkinli olsa da
  kuyruk sırası, kısmi dolum ve piyasanın emre tepkisi modellenmez.
- **Kâğıt işlem bir avantaj üretmez, ölçer.** Faz 2'nin bulgusu hâlâ geçerli:
  bu kapsamda maliyet sonrası yön bilgisi bulunmadı.
- **Komisyon ölçülmediyse %0.1 varsayımdır.** Arayüz hangi oranın kullanıldığını
  ve kaynağını her zaman yazar ("Komisyon ve kayma" satırları).
- **Mac'te henüz sınanmayanlar:** uyku engeli (`caffeinate`), pil uyarısı
  (`pmset`) ve Telegram kurulumu. Kodları testli ama Berk'in Mac'inde
  çalıştırılmadı.

---

## 17. Onay

**Faz 4, 23 Eylül 2026'da onaylandı.** Onaydan önce Berk'in Mac'inde
doğrulananlar: arayüz açıldı, canlı Binance akışı bağlandı, hata yok;
`bash kurulum.sh anahtar` salt okuma anahtarıyla komisyonu ölçtü (%0.1).
