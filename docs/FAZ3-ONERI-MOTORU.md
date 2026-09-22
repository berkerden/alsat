# Faz 3 — Periyot sihirbazı, öneri motoru ve arayüz

**Tarih:** 22 Eylül 2026
**Kapsam:** BTCUSDT ve SOLUSDT, 15m ve 1h
**Şartname karşılığı:** SPEC.md §4.3, §4.4, §4.7, §5 ve §10'daki Faz 3 satırı
**Kabul kriteri (SPEC.md §10):** *"Öneri kartları eksiksiz; marjlar komisyon
sonrası doğru."*

Bu belge Faz 3'ün **nasıl kurulduğunu ve neden öyle kurulduğunu** anlatır.

---

## 1. Faz 3 hangi soruyu cevaplıyor

Faz 1 şunu sordu: *bu coin ve periyotta tipik hareket, gidiş-dönüş maliyetini
karşılıyor mu?* Cevap 15m ve 1h için "evet, ama dar" oldu.

Faz 2 şunu sordu: *ölçülebilir bir avantaj var mı?* Cevap **hayır** oldu: 401
aday denendi, hiçbiri çoklu test düzeltmesinden geçmedi.

Faz 3 şunu soruyor: **bir kural çıktığında kullanıcı ne görecek?** Yani kabul
edilmiş bir kural, kullanıcının karar verebileceği bir öneriye nasıl çevrilir
— ve kural yokken ekranda ne yazar.

İkinci yarısı bu fazın asıl işi oldu. Kabul edilen kural olmadığı için
gösterilecek gerçek kart yok; ekranın söylemesi gereken şey "önerilecek
kural yok" ve **nedeni**.

---

## 2. Kabul kriterinin okunuşu

*"Öneri kartları eksiksiz; marjlar komisyon sonrası doğru"* cümlesi iki türlü
okunabilirdi:

1. **Yanlış okuma:** "Bir kart göster." Kabul edilmiş kural yokken kart
   göstermek, uydurma bir öneriyi gerçek gibi sunmak olurdu. Uygulamanın
   temel ilkesine (*kâr vaat etme, bulamadığını söyle*) doğrudan aykırı.
2. **Doğru okuma:** **Kart şablonu eksiksiz olmalı ve marj hesabı doğru
   olmalı.** Yani bir kural çıktığı gün kart eksiksiz dolabilmeli, bugünkü
   ekran ise dürüstçe "önerilecek kural yok" demeli.

İkincisi uygulandı. Kartın eksiksizliği ve marj aritmetiği, `strategy/example.py`
içindeki **açıkça uydurma olduğu yazan** örnek kuralla gösteriliyor; kullanıcı
arayüzde "Kart şablonunu örnek kuralla göster" düğmesine basmadıkça bu kart
çıkmıyor ve çıktığında üstünde "ÖRNEK KART — bu bir öneri değildir" yazıyor.

Örnek kuralın sayıları elle kontrol edilebilsin diye yuvarlak seçildi
(fiyat 100, ATR %1, hedef 1,5×ATR, stop 1×ATR, komisyon %0,1/%0,1):

```
brüt marj   = (101,5 − 100) / 100                 = %1,5000000
net marj    = 101,5 · (1−0,001)(1−0,001) − 100    = %1,2971015
başa-baş    = 100 / ((1−0,001)(1−0,001))          = 100,20030041
stop net    = 99 · (1−0,001)(1−0,001) − 100       = %−1,1979010
Hedef 2     = ölçülen medyan MFE %1,9 → 101,9     (net %1,6963019)
```

`tests/test_card.py` bu beş sayıyı kâğıt hesabına karşı doğruluyor. Bu dosya
bozulursa kart yanlış sayı gösteriyor demektir.

---

## 3. Faz 2 ile Faz 3 arasındaki köprü: `kurallar.json`

Faz 2 yalnızca insan okuyacak bir `.txt` rapor üretiyordu. Öneri motorunun
makine okuyabileceği bir çıktıya ihtiyacı vardı; `strategy/rules.py` bunun
için yazıldı ve `cli/research.py`'ye bağlandı. Tarama artık
`<veri-dizini>/kurallar.json` dosyasını da yazıyor.

Dosya üç şeyi ayrı ayrı taşıyor:

| Alan | İçerik | Öneri üretir mi |
|---|---|---|
| `kurallar` | Çoklu test düzeltmesinden **geçen** örüntüler | Evet |
| `incelenen_adaylar` | Dikkat çeken ama **geçmeyen** adaylar | **Hayır** |
| `bolumler` | Her tarama bölümünün özeti (taban çizgisi, eşik, en iyi ham p) | Hayır |

`incelenen_adaylar`'ın karta dönüşmemesi bilinçli bir karardır. Bu liste
taramanın neye baktığını göstermek için var; arayüzde ayrı bir başlık altında,
"**Bu aday çoklu test düzeltmesinden GEÇMEDİ**" notuyla listeleniyor. Onları
öneri kartına çevirmek, eşiğin varlık sebebini ortadan kaldırırdı.

`bolumler` olmasaydı "önerilecek kural yok" ekranı sebebini söyleyemezdi.

---

## 4. Öneri motoru (`strategy/signals.py`)

Motorun ürettiği durumlar:

| Durum | Ne zaman | Ekranda |
|---|---|---|
| `veri_yok` | O sembol/periyot için kayıtlı mum yok | "Önce veri tazeleme adımını çalıştırın" |
| `kural_yok` | Kabul edilmiş kural yok | Sebep, sayılarla (aşağıda) |
| `kural_var_tetiklenmedi` | Kural var ama son kapanmış mumda tetiklenmedi | "Şu an açık bir öneri yok" |
| `oneri_var` | Kural tetiklendi | Öneri kartı |
| `teshis_deposu` | Kural deposu maliyetsiz teşhis turundan geliyor | "Bu turdan öneri çıkmaz" |
| `ornek` | Kullanıcı kart şablonunu istedi | Örnek kart + "öneri değildir" |

Sıralama önemli: **veri kontrolü kural kontrolünden önce gelir.** Veri hiç
yokken "önerilecek kural yok" demek, taramanın başka bir kapsamda ölçtüğü
sayıları o sembolün cevabı gibi gösterirdi.

### "Önerilecek kural yok" ekranı

Bu fazın en çok emek verilen ekranı, hiçbir şey göstermeyen ekran oldu.
Kullanıcının sorması muhtemel soru şu: *bozuldu mu, yoksa gerçekten bir şey mi
yok?* Ekran o soruyu cevaplıyor:

- Kaç aday denendi (401)
- Kabul için gereken p-değeri (0,0002494) ve nasıl hesaplandığı
- Maliyet eşiği (%0,28) ve bileşenleri
- En yakın adayın eşikten kaç kat uzakta olduğu (30×) ve ham p'si
- Taban çizgisi: aynı dönemde her muma girilseydi işlem başına net (%−0,2264)
- Ve açıkça: *"Bu bir arıza değil, ölçülmüş bir sonuç."*

### Teşhis deposu reddi

Maliyetsiz teşhis turu (`--maliyetsiz`) komisyon, spread ve kaymayı sıfır
sayar. O turdan çıkan bir kural deposu yüklenirse motor **kart üretmeyi
reddeder**. Kabul edilmiş kural içerse bile. Sıfır maliyetle "kârlı" çıkan bir
örüntü gerçek dünyada zarar ettirir.

---

## 5. Öneri kartı (`strategy/card.py`)

SPEC §4.4'ün istediği her alan kartta var: giriş, hedef 1, hedef 2, stop,
başa-baş, brüt/net marj, risk/ödül, pozisyon büyüklüğü, geçerlilik süresi,
tarihsel isabet oranı (n=...), güven skoru ve skorun dökümü, gerekçe,
uyarılar, TRY karşılığı.

Üç tasarım kararı burada sabitlendi:

### 5.1 Kart, kuralın kabul edildiği maliyet varsayımlarını kullanır

Kartın marjı kuralın kanıtından farklı bir maliyetle hesaplansaydı, kart
kendi kanıtıyla çelişirdi. Maliyet varsayımları `kurallar.json` içinde
saklanıyor ve kart onları kullanıyor. Gerçek komisyon oranı Faz 4'te
ölçülünce hem tarama hem kart o oranla yenilenecek.

Tur maliyetleri araştırmadakiyle birebir aynı eşlenir (FAZ0-MIMARI Risk #4):

| Tur | Giriş | Çıkış |
|---|---|---|
| Hedefe | taker | maker (limit) |
| Stopa | taker | taker (tetiklenince piyasa) |

### 5.2 İki giriş varsayımı farklıdır ve bu kartta yazar

Olay çalışmasında giriş, sinyal mumundan **sonraki** mumun açılışıdır ve
taker komisyonu ödenir. Canlıda o açılış henüz bilinmediği için kart, son
kapanışı `tickSize`'a yuvarlayarak **limit** giriş öneriyor. İki varsayım
aynı değil: limit dolmazsa işlem hiç açılmaz, dolarsa fiyat ölçülenden farklı
olabilir. Bu fark kartın gerekçe bölümünde yazılı; gizlenmiyor.

### 5.3 Hedef 2 uydurulmaz

Kademeli kâr alma seviyesi, örüntünün **ölçülmüş medyan MFE**'sinden
(pozisyon süresince görülen en iyi fiyatın medyanı) türetilir. Ölçülen en iyi
fiyat Hedef 1'in altındaysa **Hedef 2 yoktur** ve kartta bunun sebebi yazar.
"Biraz daha yukarısı" diye bir seviye koymak, ölçülmemiş bir sayıyı ölçülmüş
gibi göstermek olurdu.

### 5.4 Güven skoru

100 puan altı bileşene bölünmüş; her birinin puanı ve gerekçesi kartta ayrı
satır olarak görünüyor:

| Bileşen | Azami | Neye bakar |
|---|---|---|
| Bağımsız örnek | 20 | Kabul kararının dayandığı üst üste binmeyen olay sayısı |
| İstatistiksel pay | 20 | Ham p'nin kabul eşiğinden kaç kat küçük olduğu |
| Dönem kararlılığı | 20 | Çeyreklerin kaçında doğru yönde |
| Walk-forward | 20 | Katmanların kaçında doğru yönde |
| Rastgele kıyası | 10 | Rastgele girişlere göre yüzdelik |
| Test dönemi | 10 | Ayrılmış test döneminde net sonuç |

Her uyarı 5 puan düşürür. Skorun tek bir sayıya indirgenmemesi bilinçli:
"güven 76" tek başına bir şey söylemez, dökümü söyler.

### 5.5 Tarihsel isabet oranı ve örneklem

Kart "%61,0 (n=180, bağımsız 120)" biçiminde yazıyor. İsabet oranı **tüm**
olaylar üzerinde ölçüldü, o yüzden n olarak o sayı veriliyor; üst üste binen
olaylar istatistiği şişirdiği için bağımsız olay sayısı ayrıca gösteriliyor
ve kabul kararı ona dayanıyor. İkisini karıştırmak, 180 olayda ölçülen bir
oranı 120 olaya dayanıyormuş gibi göstermek olurdu.

---

## 6. Pozisyon büyüklüğü (`risk/sizing.py`)

FAZ0-MIMARI Risk #2'nin karşılığı. 100 USDT'lik bir bütçede iki sınır
çarpışır: *işlem başına %1 risk* kuralı ve *bütçenin tamamı*. Hangisinin
bağlayıcı olduğu her kartta yazıyor:

> "Bağlayıcı olan bütçe: 100 USDT'nin tamamı kullanılıyor, gerçekleşen risk
> %1,20 (hedef %1,00)."

İki sayı ayrıca raporlanıyor:

- **stepSize yuvarlama kaybı:** ham miktar borsanın adımına **aşağı**
  yuvarlanır (yukarı yuvarlamak hedeflenenden fazla risk aldırırdı) ve
  kaybedilen oran yazılır.
- **Gerçekleşen risk:** stop zararı yalnızca fiyat farkı değildir; giriş ve
  çıkış komisyonları da ödenir. Hedeflenen riskin üstüne çıkıldığında uyarı
  çıkar.

`NOTIONAL` minimumunun altında kalan pozisyon **geçersiz** işaretlenir ve
en küçük geçerli emrin bütçenin yüzde kaçı olduğu söylenir.

---

## 7. Periyot sihirbazı (`strategy/wizard.py`)

SPEC §4.3'ün karşılığı: *"hangi periyotta çalışmalıyım?"* sorusuna tek tablo.
Her periyot için:

- ATR% medyanı ve maliyet eşiği, ve **oranı** (2,17× gibi) — asıl sayı budur
- Kabul edilmiş kural sayısı ve sinyal sıklığı
- Al-tut net sonucu ve maksimum düşüş (kıyas için)
- Medyan günlük hacim

**Sinyal sıklığı toplanmaz.** Üst üste binen kuralların sinyalleri toplanırsa
günde 90 sinyal gibi anlamsız bir sayı çıkar; doğru sayı en çok sinyal üreten
**tek** kuralın sıklığıdır.

Tablonun altında ne söylediği açıkça yazıyor: tablo **nerede aranacağını**
söyler, nerede kâr olduğunu değil. Oranın yüksek olması "burada iş var"
değil, "burada aramak matematiksel olarak anlamlı" demektir.

---

## 8. B eki: izleme, maliyet/risk, disiplinli alım

Berk'in isteği üzerine ana akışın **yanında** duran, istenince kullanılan bir
bölüm. Üçü de emir göndermez.

- **İzleme:** son fiyat, gün içi aralık, 1g/7g/30g değişim, ATR%'nin maliyet
  eşiğine oranı, isteğe bağlı kullanıcı seviyeleri. Faz 3'te **alarm yok**;
  bildirim Faz 4'ün işi ve olmayan bir şey vaat edilmiyor.
- **Maliyet/risk paneli:** kullanıcının girdiği üç fiyatla kartın gösterdiği
  dökümün aynısı. Kartla **aynı kodu** kullanır; iki ayrı hesap sessizce
  ayrışırdı.
- **Disiplinli alım planı:** dönemsel (her n günde bir) veya kademeli (fiyat
  düştükçe) alım dilimleri; komisyon ve stepSize dahil. Ayrıca aynı plan
  geçmiş veriyle bir kez çalıştırılıp **tek seferde alımla** kıyaslanıyor ve
  en kötü ara değer gösteriliyor — kullanıcının asıl sorusu "bölerek almak işe
  yaradı mı" olduğu için.

---

## 9. Sinyal günlüğü (`strategy/journal.py`)

SPEC §4.8'in çekirdeği: **önerilen ile gerçekleşen** arasındaki fark.

Üretilen her kart SQLite'a yazılır (aynı kural + aynı sinyal mumu ikinci kez
yazılmaz). Geçerlilik penceresi dolunca sonuç aynı mum verisinden hesaplanır
ve yanına eklenir. Sonuçlandırma, araştırmadaki olay çalışmasıyla **aynı
temkinli varsayımları** kullanır:

- Pozisyon sinyal mumundan **sonraki** mumla başlar
- Aynı mumda hem hedef hem stop görülürse **stop** kabul edilir
- Stop ve süre dolumu piyasa emridir; kayma aleyhe işler
- Pencere henüz dolmamışsa sonuç **uydurulmaz**, kayıt açık kalır

---

## 10. Arayüz (`api/`, `cli/serve.py`)

### 10.1 Şartnameden sapma: derleme adımı yok

SPEC §3 önerilen yığını "React + Vite" olarak veriyor ve *"gerekçeyle
değiştirebilirsin"* diyor. Değiştirildi: arayüz, Python paketinin içinden
servis edilen düz HTML/CSS/JS. Gerekçe:

1. React + Vite, kullanıcıdan **Node ve npm kurmasını** ister. Berk terminale
   alışık değil; ikinci bir kurulum zinciri eklemek kurulumun en kırılgan
   yerini iki katına çıkarırdı.
2. Faz 3'ün arayüz ihtiyacı (tablolar, kartlar, bir mum grafiği) bir
   çatı gerektirmiyor.
3. Mum grafiği de bağımlılıksız: `grafik.js` yaklaşık 120 satırlık bir canvas
   çizimi. Hazır kütüphane ya npm ya da CDN demekti; CDN internetsiz
   çalışmaz ve sayfayı dışarıya bağlar.

**Berk bu sapmayı 22 Eylül 2026'da Faz 3 onayıyla birlikte kabul etti.**

### 10.2 Güvenlik sınırları

- Sunucu **yalnızca 127.0.0.1**'e bağlanır (SPEC §5). Dinlenecek adres bilerek
  seçenek olarak sunulmuyor: bir bayrakla 0.0.0.0'a açılabilen yerel arayüz,
  er ya da geç açılır.
- Uygulama **Sadece Öneri** modunda; emir gönderen bir uç **yok** ve bunun
  testi var (`test_emir_gonderen_uc_yok`).
- Bütün uçlar `GET`; arayüz hiçbir şeyi değiştirmiyor.
- Sunucu internete çıkmaz, API anahtarı okumaz. `/api/saglik` bunu ekranda da
  yazıyor.

### 10.3 Sayı biçimi

Parasal her değer arayüze **metin** olarak gider. `float` parasal değerlerde
yasak (SPEC §3), `Decimal` doğrudan JSON'a verilemez ve `str(Decimal)` küçük
sayılarda `9.9E-7` gibi okunamaz bir çıktı üretir. Arayüz gelen metni
aritmetiğe sokmaz, olduğu gibi gösterir. Yüzdeler ölçüm sonucu olduğu için
sayı olarak gider.

---

## 11. Borsa filtreleri önbelleği (`data/exchangeinfo.py`)

SPEC §11: *"komisyon oranlarını, sembol filtrelerini veya limitleri koda sabit
yazma; borsadan çek."* Ama arayüz internete çıkmadan da açılabilmeli. Çözüm:
filtreler veri tazeleme adımında bir kez indirilip `veri/exchangeinfo.json`
dosyasına yazılıyor, arayüz diskten okuyor.

Önbellek yoksa **uydurulmuş bir varsayılan kullanılmaz.** Kart "filtreler
elimde yok" der ve fiyatların yuvarlanmadığını ekranda yazar. Sessizce 0,01'lik
bir `tickSize` varsaymak, borsanın reddedeceği bir emri geçerli göstermek
olurdu.

---

## 12. Testler

| Dosya | Ne koruyor |
|---|---|
| `test_kural_deposu.py` | Deponun gidiş-dönüşü; taramanın yazdığı her alan arayüzde aynı çıkıyor |
| `test_card.py` | Kart aritmetiği, kâğıt hesabına karşı |
| `test_sizing.py` | Bağlayıcı kural, stepSize kaybı, komisyon dahil stop zararı |
| `test_oneri_motoru.py` | "Kural yok" ekranının içeriği, teşhis deposu reddi, tetiklenen kural |
| `test_ek_araclar.py` | Panel, plan, izleme, sihirbaz, sinyal günlüğü (stop öncelikli sonuçlandırma dahil) |
| `test_arayuz.py` | Her uç ayakta, parasal değerler metin, emir gönderen uç yok |

Faz 2'nin garanti testleri (`test_scan.py`, `test_lookahead.py`) olduğu gibi
duruyor ve geçiyor.

---

## 13. Bu fazın söylemediği şeyler

- **Bir kural bulunmadı.** Bu faz kural bulmaya çalışmadı; Faz 2'nin sonucunu
  sunuyor.
- **Örnek kart bir öneri değildir.** Kart şablonunun eksiksizliğini gösterir.
- **Komisyon oranı hâlâ varsayım.** %0,1 kullanılıyor; hesaba özel gerçek oran
  Faz 4'te imzalı `GET /api/v3/account/commission` ile ölçülecek ve hem tarama
  hem kart o oranla yenilenecek.
- **Bildirim yok.** İzleme paneli okunur; alarm göndermez.
