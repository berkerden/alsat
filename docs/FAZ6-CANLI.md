# Faz 6: Canlı Yarı Otomatik ve canlıya geçiş kapısı

**Tarih:** 23 Eylül 2026
**Kapsam:** BTCUSDT ve SOLUSDT, 15m ve 1h, 100 USDT bot bütçesi, Binance **canlı** Spot hesabı (gerçek para)
**Şartname karşılığı:** SPEC.md §4.5, §4.6, §4.7, §5 ve §10'daki Faz 6 satırı
**Kabul kriteri (SPEC.md §10):** *"Canlıya geçiş kapısı çalışıyor; güvenlik kontrolleri aktif."*
**Berk'in kapsam kararı (23 Eylül 2026):** "Kapı + mini emir"

Bu belge Faz 6'nın ne yaptığını ve neden öyle kurulduğunu anlatır. Emir
akışının kendisi (OTOCO, koruma, uzlaştırma, sonucu bilinmeyen istek) Faz 5'te
kuruldu ve değişmedi: [`FAZ5-DEMO-EMIR-YURUTME.md`](FAZ5-DEMO-EMIR-YURUTME.md).

---

## 1. Faz 6 ne yapıyor, ne yapmıyor

Faz 5'in sonunda uygulama emirleri yalnızca Binance Demo Mode'a (sahte para)
gönderiyordu. Faz 6 aynı yürütücüyü **canlı hesaba** bağlar ve gerçek paranın
gerektirdiği kilitleri ekler:

1. **Canlı yürütücü** (`execution/live.py`). Demo yürütücüsünün bütün emir
   akışını kullanır; üstüne anahtar izni denetimi, emir tavanı, Yarı
   Otomatik önerileri ve Tam Otomatik'in kapı denetimi gelir.
2. **Canlı istemci** (`exchange/trading.py` → `LiveTrader`). Demo
   istemcisiyle aynı çekirdeği paylaşır ama ortamı, adresi, emir kimliği
   öneki (`albsat-canli-`) ve izin listesi ayrıdır.
3. **Canlıya geçiş kapısı** (`execution/gate.py`). Tam Otomatik'in kural
   başına ön koşulu.
4. **Canlı işlem sekmesi** (`api/live_api.py`, `api/static/canli.js`),
   Telegram'da `/onayla` ve `/reddet`, `/durum` ile `/durdur`'un canlıyı da
   kapsaması.
5. **Kurulum komutları:** `bash kurulum.sh canli-anahtar` (anahtarı kurar,
   yalnızca okur) ve `bash kurulum.sh canli-sina` (dolmayacak bir sınama
   emri gönderip iptal eder).

Yapmadıkları:

- **Tam Otomatik açılmaz.** Kapıyı geçen kural yok: Faz 2 kabul edilmiş kural
  bulmadı, kâğıtta kural işlemi yok. Kapıyı elle aşan yol yazılmadı (§3).
- Kabul edilmiş kural olmadığı için Yarı Otomatik'te de **öneri gelmez**.
  Canlı yol elle girilen canlı emirle sınanır (§9).
- Kaldıraç, margin, vadeli işlem ve borçlanma kodu yoktur. Emir yürütmede
  Binance MCP sunucusu kullanılmaz.

---

## 2. Kabul kriterinin okunuşu

**"Canlıya geçiş kapısı çalışıyor"**: kapı her kural için altı koşulu ayrı
ayrı hesaplar, sonucunu gerekçesiyle gösterir ve Tam Otomatik'i yalnızca
kapıyı geçen bir kural varsa açtırır. Bu hem birim testleriyle (her koşul tek
tek bozularak) hem de kapıyı geçen yapay bir kuralla uçtan uca sınandı:
Tam Otomatik açıldı ve yalnızca o kuralın sinyali emre döndü.

**"Güvenlik kontrolleri aktif"**: §4'teki kilitlerin her biri testle
sınanıyor. Gerçek hesapta sınama Berk'in Mac'inde, yazılı onayıyla yapılır
(§9).

---

## 3. Canlıya geçiş kapısı (`execution/gate.py`)

SPEC §4.7: *"Bir strateji paper modda en az [7 gün / 30 işlem] çalışıp
beklentiyle uyumlu sonuç vermeden Tam Otomatik açılamaz."*

Kapı **kural başınadır**. Bir kural şu koşulların hepsi sağlanınca geçer:

| Koşul | Ne sınanıyor |
|---|---|
| Kabul edilmiş kural | Kural şu anki kural deposunda kabul edilmiş (teşhis turu değil) |
| Süre | Kâğıttaki ilk kural işleminden bu yana en az 7 gün |
| İşlem sayısı | Kâğıtta en az 30 kapanmış **kural** işlemi (elle işlemler sayılmaz) |
| Beklentiyle uyum | Faz 4'ün performans sınaması yapılabilmiş ve gerçekleşen ortalama beklenenin anlamlı ölçüde altında değil |
| Pozitif ortalama | Kâğıttaki ortalama net sıfırın üstünde |
| Durdurulmamış | Kural performans koruması tarafından kapatılmamış |

"Pozitif ortalama" SPEC'te açıkça yazmıyor. Beklentiyle "uyumlu" ama zarar
eden bir kuralı gerçek paraya taşımamak için eklendi.

Bir coinde Tam Otomatik, o coinde kapıyı geçen en az bir kural varsa
açılabilir. Tam Otomatik'te **yalnızca kapıyı geçen kuralların** sinyali
onaysız emre dönüşür; aynı coindeki başka bir kuralın sinyali emir açmaz.

**Şartnameden iki sapma:**

- SPEC kapıyı *"açık bir uyarı ekranı ve ek onay"* ile aşmaya izin veriyor.
  Bu yol yazılmadı: Berk'in kararıyla Tam Otomatik kapıda kilitli kalıyor.
- SPEC, beklenmedik yeniden başlatmadan sonra Tam Otomatik'in kendiliğinden
  açılmamasını "ayarlanabilir, varsayılan kapalı" diyor. Burada ayar yok:
  uygulama her açılışta bütün coinleri Sadece Öneri'ye alır (Faz 3'ten beri
  sabit karar). Borsadaki stop ve hedef yerinde kalır.

---

## 4. Güvenlik kilitleri

### 4.1 Anahtar ayrı, sır Mac'te

Canlı işlem anahtarı Anahtar Zinciri'nde `albsat-binance-canli` kaydındadır.
Faz 4'ün salt okuma anahtarından (`albsat-binance`) ve Demo anahtarından
(`albsat-binance-demo`) ayrıdır. Anahtar çifti Mac'te üretilir; Binance'e
yalnızca genel yarı verilir. API Key Terminal'e gizli girişle yazılır; depoda,
dosyalarda, günlüklerde, denetim kaydında ve arayüzde görünmez. Ortam
değişkeni kullanılmaz.

### 4.2 Anahtarın izinleri

İzinler `GET /sapi/v1/account/apiRestrictions` ile okunur. **Hesabın
`canWithdraw` bayrağı anahtarın izni değildir** (Faz 5'te bu karışıklık bir
kez yaşandı); ona bakılmaz.

- Engeller (biri varsa anahtarla emir gitmez): para çekme, margin, vadeli
  işlem, opsiyon, portföy margin, hesaplar arası transfer ya da evrensel
  transfer izni açık; okuma ya da Spot işlem izni kapalı; yanıt eksik.
- Uyarı: IP kısıtlaması yok (§8).

İzinler açılışta, sonra **30 dakikada bir** yeniden okunur. Sonuç bozuksa ya
da okunamazsa:

- canlı moddaki coinler Sadece Öneri'ye alınır,
- borsada bekleyen canlı girişler iptal edilir,
- Telegram'a ve arayüze nedeni yazılır.

İmzalayan sınıf ayrıca yeni bir giriş emrini ancak izinler **son bir saat
içinde** okunmuş ve uygun bulunmuşsa gönderir. Bu, aynı kuralın borsaya en
yakın yerdeki ikinci kopyasıdır.

**Koruma emirleri bu kilide takılmaz.** Elde coin varken stop koymak, OCO'yu
yeniden kurmak, korumalı çıkış ve iptal riski azaltır; izin bozuk diye
engellenmez (testle sınanıyor).

### 4.3 Emir tavanı

Her canlı giriş emrinin tutarı (fiyat × miktar) **emir tavanını** aşamaz.
Varsayılan 10 USDT (`config/default.yaml` → `canli.emir_tavani_usdt`,
`execution/live.py` → `DEFAULT_CAP_USDT`; test ikisinin aynı olduğunu
denetler). Tavan arayüzden değiştirilir. 0'dan büyük olmalı ve bot bütçesini
(100 USDT) aşamaz. Her değişiklik denetim kaydına yazılır.

Tavan üç yerde uygulanır:

1. Risk motoru emri tavana göre boyutlar. Tavan bağlayıcıysa kartta
   "Bağlayan: tutar sınırı" yazar. İşlem başı risk yine bot bütçesinden
   hesaplanır.
2. Elle yazılan tutar tavanı aşarsa istek reddedilir.
3. İmzalayan sınıf göndermeden önce tutarı yeniden sınar (%1 yuvarlama
   payıyla).

### 4.4 Yalnızca kendi emirleri, yalnızca izinli adresler

Canlı istemci yalnızca `albsat-canli-` önekli emirleri gönderir, sorgular ve
iptal eder. Kullanıcının elle verdiği emirlere dokunamaz. İzin listesi Demo'nun
listesi artı izin okumasıdır. Bütün açık emirleri silen
`DELETE /api/v3/openOrders` listede yok. Canlı istemci yalnızca canlı ortamla,
Demo istemcisi yalnızca Demo ile kurulur. Biri ötekinin yerine kurulamaz.

### 4.5 Ayrı kayıt, ortak istek bütçesi

Canlı pozisyonlar, dolumlar ve olaylar `canli_` önekli tablolardadır. Demo
kayıtlarıyla karışmaz. Canlı yürütücü, canlı piyasa akışıyla **aynı istek
bütçesini** kullanır, çünkü Binance'in ağırlık sınırı IP başınadır. Beklenmedik
büyüklükte bir iş çıkarsa istek gönderilmeden durulur (Faz 4 kuralı).

### 4.6 Açılış ve durdurma

- Uygulama her açılışta Sadece Öneri'dedir. Canlı mod kendiliğinden açılmaz.
- Yarı ve Tam Otomatik **yalnızca Canlı işlem sekmesinden** açılır. Kâğıt
  sekmesinde bilgi olarak görünür ama seçilemez. İkisi de açılırken coin
  adının yazılmasını ister.
- ACİL DURDUR ve Telegram `/durdur` canlı modları da kapatır, borsada bekleyen
  canlı girişleri iptal eder. Açık pozisyonun stop ve hedefi borsada kalır.

---

## 5. Yarı Otomatik

Kabul edilmiş bir kural sinyal verince emir gitmez; bir **öneri** oluşur:

- Canlı işlem sekmesinde "Onay bekleyen canlı öneriler" kutusuna ve
  Telegram'a düşer (`🟡 CANLI ÖNERİ, onay bekliyor`, altı karakterlik bir
  kimlikle).
- "Emri Gönder" (onay penceresiyle) ya da Telegram'da `/onayla <kimlik>` emri
  gönderir. Emir yine bütün risk kapılarından, izin kilidinden ve tavandan
  geçer.
- "Reddet" ya da `/reddet <kimlik>` öneriyi kapatır.
- Öneri, sinyalin geçerlilik süresi dolunca kendiliğinden düşer. Coin Yarı
  Otomatik'ten çıkarsa bekleyen öneriler iptal olur.
- Bir öneri yalnızca bir kez gönderilebilir.

Elle canlı emir de yalnızca Yarı Otomatik'teki coinde açılır ve coin adının
yazılmasını ister. Elle emirler kapıda sayılmaz.

---

## 6. Arayüz: Canlı işlem sekmesi

- Üstte kırmızı **GERÇEK PARA** şeridi. Sekme adı ve üstteki mod rozeti canlı
  modda kırmızıdır ("CANLI Yarı Otomatik: BTCUSDT").
- Bağlantı ve **anahtar izinleri** (para çekme, Spot işlem, IP kısıtı, son
  okuma).
- "Hangi coin canlıda?": Yarı Otomatik'e alma (coin adı sorulur), Sadece
  Öneri'ye alma, Tam Otomatik özeti. Özet bütçeyi, tavanı, risk sınırlarını,
  periyotları ve kapıyı geçen kuralları gösterir; kapı kapalıysa onay kutusu
  hiç çizilmez.
- Onay bekleyen öneriler, canlıya geçiş kapısı (coin coin), canlı hesap (bot
  bütçesi, borsadaki bakiye), risk sınırları, açık pozisyonlar, elle canlı emir
  (önizle / gönder), sonuçlar, kapanan işlemler ve CSV.
- Ayarlar: emir tavanı ve emir ayarları. Ayrıntılar: bağlantı, izinler, istek
  bütçesi ve olaylar.

Sekme beş saniyede bir sunucudan durum okur. **Bu okuma Binance'e istek
göndermez**; sunucu yürütücünün elindeki son durumu döndürür. Kullanıcının
yazdığı alanlar yenilemede silinmez.

---

## 7. Komutlar (`cli/canli.py`)

`bash kurulum.sh canli-anahtar`:

1. Mac'te yeni bir Ed25519 anahtar çifti üretir ve özel yarıyı Anahtar
   Zinciri'ne yazar.
2. Genel yarıyı ve Binance'te yapılacakları ekrana yazar: izinler, IP kısıtı.
3. API Key'i gizli girişle alır.
4. Yalnızca **okur**: saat, izinler, hesap (Spot, işlem açık) ve komisyon.
   Emir göndermez.

`bash kurulum.sh canli-sina`:

1. İzinleri ve hesabı okur.
2. Borsa kurallarını ve fiyatı alır.
3. Hesap akışına bağlanır.
4. Gerçek hesaba **dolmaması beklenen** bir alış gönderir (en iyi alışın
   yaklaşık %5 altında, en küçük tutarın 1,5 katı, tavanı ve serbest USDT'yi
   aşmaz; en küçük tutar 5 USDT iken yaklaşık 7,5 USDT). Göndermeden önce coin adını
   yazdırır.
5. Emrin akıştan geldiğini ve borsada göründüğünü doğrular.
6. Emri iptal eder ve iptali doğrular.

Dolmadığı sürece para harcanmaz. Arayüz açıkken çalıştırılmamalıdır.

---

## 8. IP kısıtlaması

SPEC §5: *"VPS'te statik IP ile zorunlu. Mac'te ev IP'si değişebileceği için
riskleri ve Binance'in IP kısıtlamasız anahtarlarla ilgili güncel kurallarını
README'de açıkla."*

- **Kısıtsız anahtar:** her IP'den kullanılabilir. Özel yarı Mac'in Anahtar
  Zinciri'nde durduğu için anahtarı kullanmak Mac'e erişmeyi gerektirir, ama
  koruma katmanı bir tane azalır.
- **Kısıtlı anahtar:** ev IP'si değişirse anahtar çalışmaz. Yeni emir
  gönderilemez, uzlaştırma yapılamaz; borsadaki stop ve hedef yerinde kalır.
  Binance'te IP listesini güncellemek gerekir.

Binance'in kuralı zamanla değişti. 26 Temmuz 2021 duyurusu, IP kısıtsız
anahtarlarda Spot işlem izninin 90 günde bir kendiliğinden kapandığını
söylüyordu. Binance duyuruya 24 Ekim 2023'te bu kuralın artık geçerli
olmadığı notunu ekledi. Üçüncü taraf kaynaklar bugün kısıtsız anahtarların bir
süre kullanılmayınca silindiğini yazıyor; bunu Binance'in kendi belgesinde
bulamadım. **Güncel kural, anahtarı oluştururken Binance'in sayfasında
yazandır.**

Kaynaklar (23 Eylül 2026'da okundu):
[Binance duyurusu, 26 Temmuz 2021](https://www.binance.com/en/support/announcement/updates-to-api-key-permission-rules-2021-07-26-11e4c2f44e7a47b9b5fc0e479c0b256f),
[Binance API belgesi: Get API Key Permission](https://developers.binance.com/docs/wallet/account/api-key-permission),
[üçüncü taraf özet (QuotaGuard)](https://www.quotaguard.com/blog/binance-api-ip-whitelist-cloud-static-ip). Uygulama izinleri 30 dakikada bir okuduğu için Binance işlem
iznini kapatırsa bunu görür, canlı girişleri durdurur ve haber verir.

VPS'e geçişte (Faz 7) IP kısıtı zorunludur.

---

## 9. Mini emir: gerçek hesapta tek sınama

Berk'in kararı: mekanizma, **yazılı onayıyla**, en küçük tutarda tek bir
gerçek emirle sınanır. Sıra:

1. `canli-anahtar`: anahtarın oluşturulması için ayrıca yazılı onay alınır.
2. `canli-sina`: dolmayan emir, gönder, izle, iptal.
3. Arayüzden BTCUSDT'yi Yarı Otomatik'e alıp **yaklaşık 7 USDT'lik** elle
   canlı emir: giriş dolar, borsa hedefi ve stopu koyar. Tutar, alışta
   komisyon düşüldükten sonra satılacak miktarın da borsanın en küçük
   tutarını (BTCUSDT'de 5 USDT) geçmesi için 5 değil 7 USDT seçildi.
4. "Pozisyonu kapat" ile çıkış.

Beklenen maliyet: iki komisyon (her biri yaklaşık %0.1) ve alış-satış farkı;
7 USDT'de birkaç sent. Adım adım anlatım proje klasöründe
`faz6/FAZ6-MAC-ADIMLARI.md`.

---

## 10. Testler

| Dosya | Test | Ne sınıyor |
|---|---|---|
| `test_demo_kaos.py` | 72 | Faz 5'in 36 kaos testi her biri hem Demo hem **canlı** yürütücüyle: hedef, stop, kısmi dolum, kaybolan yanıt, kopuk akış, çökme ve yeniden açılış, uyku, 429/418, acil durdur, elle emirlere dokunmama, günlük zarar sınırı. Sahte borsa her isteğin Ed25519 imzasını doğrular. |
| `test_canli.py` | 57 | Canlı istemcinin ortamı ve öneki; izin okunmadan, bir saatten eski izinle ve okuma hatasında girişin kapanması; tavanın imzalayan sınıfta da sınanması; her riskli iznin ayrı ayrı engellemesi; `canWithdraw`'ın izin sayılmaması; sonradan bozulan iznin canlı modları kapatıp girişi iptal etmesi; koruma emirlerinin buna takılmaması; tavanın doğrulanması, denetime yazılması, emri boyutlaması ve "tutar sınırı" olarak gösterilmesi; canlı modların yalnızca canlı sekmesinden ve hazırken açılması; kapının her koşulu; kapıyı geçen kuralla Tam Otomatik; öneri oluşma, onay, ret, süre dolumu, mod değişince iptal; Telegram; arayüz uçları; çevrimdışı çalıştırma; kurulumun canlı adreslerle ve ortak bütçeyle yapılması; ayrı tablolar; komutlar. |
| `test_arayuz.py`, `test_kagit_arayuz.py` | | Canlı POST uçlarının tam listesi; kâğıt sekmesinin canlı modları seçtirmemesi; arayüz uçlarının anahtara ve imzalı istemciye dokunmaması. |

Bütün paket: **678 test** geçiyor.

Arayüz gerçek tarayıcıda (Chromium; 1280 px 1×/2×, 390 px 2×/3×) sahte
borsaya bağlı üç sunucuyla sınandı:

- **dolu:** Yarı Otomatik, bekleyen öneri, korunan pozisyon, kapanan işlem.
- **izin bozuk:** para çekme izni açık.
- **çevrimdışı.**

Sonuç:

- Yatay taşma yok, konsolda hata yok.
- Beş saniyelik yenilemede yazılan alanlar silinmedi, yükseklik oynamadı.
- Sekme gidip gelince kutular çoğalmadı.
- Coin adı sorusu: yanlış yazınca mod açılmadı, vazgeçince istek gitmedi,
  küçük harfle yazınca açıldı.
- Elle emirde onay kutusu boşken ve tavan üstü tutarda emir gitmedi.
- Öneri reddi ve ACİL DURDUR sonrası rozet doğru.

---

## 11. Bu fazın söylemediği şeyler

- **Canlı yol bir avantaj üretmez.** Faz 2'nin bulgusu geçerli: bu kapsamda
  maliyet sonrası yön bilgisi bulunmadı. Canlı mekanizma çalışsa da işlem
  yapmaya değer bir kural yok.
- **Gerçek hesapta görülenler** Berk'in Mac'indeki sınamadan sonra bu bölüme
  yazılacak. Şimdiye kadar yalnızca sahte borsayla sınandı.
- Mac uyurken borsadaki stop ve hedef çalışır, ama yeniden koruma ve izin
  denetimi uygulama uyanınca yapılır.

---

## 12. Onay

Faz 6 henüz onaylanmadı. Onay, Berk'in Mac'indeki sınamadan sonra istenecek.
