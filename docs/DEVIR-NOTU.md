# Devir notu — Faz 3'ten Faz 4'e

22 Eylül 2026. Bu not, koda ve diğer belgelere bakarak öğrenilemeyecek
şeyleri yeni oturuma aktarmak içindir. Şartname `SPEC.md`, mimari kararlar
`docs/FAZ0-MIMARI.md`, fizibilite sonucu `docs/FAZ1-FIZIBILITE.md`,
**Faz 2 sonucu `docs/FAZ2-SONUC.md`**, Faz 2 yöntemi
`docs/FAZ2-ORUNTU-MOTORU.md`, **Faz 3 yöntemi
`docs/FAZ3-ONERI-MOTORU.md`**.

## 1. Berk nasıl çalışıyor

* Mac kullanıyor, kod `~/Desktop/alsat` altında. Terminal, git ve GitHub
  akışları ona tanıdık değil.
* Kendi bilgisayarında atması gereken adımlar tek tek, sade ve elinden
  tutarak anlatılmalı. Çıplak komut listesi verme.
* Güncelleme komutu tek satıra indirgendi:
  `cd ~/Desktop/alsat && git pull && bash kurulum.sh`
  Sonuna eklenebilecek seçenekler:

  | Seçenek | Ne yapar |
  |---|---|
  | (boş) | Veriyi tazeler + fizibilite + örüntü taraması |
  | `tarama` | İnternete çıkmaz; yalnızca örüntü taraması |
  | `teshis` | Tarama + maliyetsiz teşhis turu |
  | `arayuz` | Tarama yapmaz; kurulumu kontrol edip arayüzü açar |

* Uzun süren her adım ekrana ilerleme yazmalı. Sessiz ekrana bakınca
  takıldığını sanıyor.
* Uzun çıktıları sohbete yapıştıramıyor; dosya olarak yolluyor.
* Kod GitHub'da `berkerden/alsat`, `main` dalında duruyor; Berk'in
  bilgisayarında da yedeği var. Doğrudan `main`'e gönderiliyor.

## 2. Çalışma ortamının kısıtları ve doğrulama kuralı

* Ağ politikası **api.binance.com ve data.binance.vision adreslerini
  engelliyor** (proxy CONNECT'e 403 dönüyor). Buradan gerçek piyasa verisi
  indirilemez. Gerçek veri gerektiren her adımı Berk kendi Mac'inde
  çalıştırıyor, çıktıyı sohbete yolluyor.
* PyPI erişilebilir; kurulum ve testler burada çalışır.
* Kapsayıcının varsayılan `python3`'ü 3.11 olabilir; proje 3.12+ istiyor.
* Bu oturumların yetkisi yeni GitHub deposu açmayı kapsamıyor.

**Doğrulama kuralı:** Berk'e "çalıştır" demeden önce depoyu **geçici bir
dizine temiz klonlayıp** kurulumu ve testleri orada baştan çalıştır.
Buradaki çalışma dizininde testlerin geçmesi kanıt değil: 21 Eylül'de
`.gitignore` içindeki sabitlenmemiş `data/` kuralı `src/albsat/data/`
kaynak paketini yuttu, burada testler geçti, Berk'in bilgisayarında
`ModuleNotFoundError` ile patladı.

Faz 3 böyle doğrulandı: temiz klon → `bash kurulum.sh arayuz` → Python
bulundu, sanal ortam kuruldu, bağımlılıklar indi, 340 test geçti, arayüz
açıldı; sonra sentetik veriyle tarama çalıştırılıp arayüzün beş sekmesi de
tarayıcıda hatasız gezildi.

**Arayüzü doğrulamanın yolu:** Chromium bu ortamda kurulu ve Playwright
onu buluyor (`executable_path="/opt/pw-browsers/chromium"`). Sayfayı gerçek
tarayıcıda açıp konsol hatalarını, sekmeleri ve mobil genişlikte yatay
taşmayı ölçmek mümkün; "endpoint 200 döndü" tek başına sayfanın çalıştığını
göstermiyor (bir JavaScript hatası bunu geçer). **Berk'in Mac'i Retina**:
sayfayı `device_scale_factor=2` ile de aç ve aynı etkileşimi birkaç kez
tekrarlayıp boyutları her seferinde ölç (§6, madde 10).

## 3. Berk'in Faz 3'te verdiği karar — kendi cümlesiyle

*"a ile devam edip b fonksiyonunu da ekleyerek öneri veya lazım olduğunda
kullanılabilecek gibi ek şekilde yapabilir miyiz"*

**A — şartnamedeki Faz 3:** periyot sihirbazı, öneri motoru, Sadece Öneri
arayüzü. **B eki — ana akışın yanında, lazım olunca açılan bölüm:**
izleme, maliyet/risk paneli, disiplinli alım. İkisi de yapıldı.

Berk'in çalışma yöntemi hakkındaki kuralı (21 Eylül): *"Yeni bir oturum ile
yeni fazlara geçiş yap. Bağlam şişip kalite düşmesin bu sayede. Her oturumda
öğrendiğimiz, dikkat ettiğimiz, sonraki faza aktarılması gerekecek bilgileri
toparla ve sonraki faz için sonraki oturuma taşı, eksik bilgi olmadan devam
etmiş olsun."*

**Faz 3, 22 Eylül 2026'da onaylandı.** Berk'in cümlesi: *"onaylıyorum"*.
Onaydan hemen önce Berk'in Mac'inde bir grafik hatası çıktı; iki düzeltmeyle
kapandı ve Berk kendi ekranında doğruladı (*"tamam oldu"*). Ayrıntısı §6,
madde 10 ve 11.

## 4. Faz 3 ne yaptı (tek paragraf)

Faz 2 ile Faz 3 arasına makine okunur bir kural deposu
(`veri/kurallar.json`) kondu; tarama artık kabul ettiği kuralları, kabul
etmediği adayları ve bölüm özetlerini ayrı ayrı yazıyor. Öneri motoru
yalnızca **kabul edilmiş** kuralları okuyor ve kural olmadığı için ekranda
"önerilecek kural yok" yazıp nedenini sayılarla gösteriyor. Kart şablonunun
eksiksizliği ve marj aritmetiği, elle doğrulanabilir bir örnek kuralla
kanıtlandı; o kartın üstünde "öneri değildir" yazıyor. Yanına periyot
sihirbazı, sinyal günlüğü, pozisyon büyüklüğü hesabı ve B eki (izleme,
maliyet/risk, disiplinli alım) geldi. Hepsi yalnızca 127.0.0.1'e bağlanan
yerel bir arayüzden sunuluyor; emir gönderen kod yok. Ayrıntı ve gerekçeler
`docs/FAZ3-ONERI-MOTORU.md`.

## 5. Eldeki komut satırı araçları

| Araç | Ne yapar | Ağa çıkar mı |
|---|---|---|
| `albsat-fizibilite` | Veri indirir, filtreleri önbelleğe yazar, fizibilite taraması | Evet |
| `albsat-oruntu` | Örüntü keşfi, istatistik, backtest, rapor, **kural deposu** | **Hayır** |
| `albsat-oruntu --maliyetsiz` | Aynısı, maliyet sıfır sayılarak (teşhis) | **Hayır** |
| `albsat-arayuz` | Sadece Öneri arayüzü, yalnızca 127.0.0.1 | **Hayır** |
| `albsat-tls-teshis` | Sertifika zinciri teşhisi | Evet |

## 6. Faz 3'te öğrenilen, koda bakarak görülmeyecek tuzaklar

1. **`CostAssumptions` içinde birimler karışık, bilerek.** `maker_orani` ve
   `taker_orani` **orandır** ("0.001" = %0,1), çünkü `fees.flat_table` oran
   bekliyor. `spread_yuzde`, `kayma_yuzde`, `guvenlik_payi_yuzde`,
   `minimum_hedef_yuzde` ise **yüzdedir**. `maker_yuzde`/`taker_yuzde`
   hesaplanmış özellik olarak duruyor. Bunları karıştırmak 100 katlık sessiz
   bir hata üretir; bir kez üretti. `test_kural_deposu.py` içinde testi var.
2. **İsabet oranı ile örneklem sayısı eşleşmeli.** `isabet_orani` **tüm**
   olaylar (`olay`) üzerinde ölçülüyor; `bagimsiz_olay` üst üste binmeyen
   olay sayısı, `kabul_ornegi` ise kabul kararının dayandığı ayrılmış
   örneklem. Kartta "n" olarak `olay` yazılıyor ve bağımsız sayı ayrıca
   gösteriliyor. Güven skorundaki "Bağımsız örnek" bileşeni `kabul_ornegi`'ne
   bakıyor. Üçünü karıştırmak kolay.
3. **Veri kontrolü kural kontrolünden önce gelir.** Veri hiç yokken
   "önerilecek kural yok" demek, taramanın başka bir kapsamda ölçtüğü
   sayıları o sembolün cevabı gibi gösteriyordu. `signals.recommend` içindeki
   dal sırası bilerek böyle.
4. **Sinyal sıklığı toplanmaz.** Üst üste binen kuralların sinyalleri
   toplanırsa periyot sihirbazında günde 90 sinyal gibi anlamsız bir sayı
   çıkar. Doğru sayı en çok sinyal üreten **tek** kuralın sıklığı.
5. **Bağlayıcı kural eşitlikte "bütçe" sayılır.** Risk kuralı ve bütçe aynı
   miktarı veriyorsa "bütçenin tamamı kullanılıyor" demek doğru olandır;
   kullanıcının elinde başka para kalmıyor.
6. **`Decimal` bölmesi 28 basamak üretir.** `core.money.clamp_decimals`
   yalnızca **kırpar**, `normalize()` çağırmaz: `normalize()` `1E+2` ve
   `9.9E-7` gibi bilimsel gösterim üretiyordu. Okunur biçime çevirme işi
   serileştirme katmanında (`api.serialize.format_for_api`).
7. **Parasal her değer arayüze metin olarak gider.** `float` yasak (SPEC §3),
   `Decimal` JSON'a verilemez. Arayüz gelen metni aritmetiğe sokmuyor.
   `test_arayuz.py` bunu ve bilimsel gösterim olmamasını kontrol ediyor.
8. **Tur maliyetleri araştırmayla birebir aynı eşlenmeli.** Giriş her iki
   turda taker, hedefe çıkış maker, stopa çıkış taker (FAZ0 Risk #4).
   `signals.cost_trips` ile `cli/research.py` aynı eşlemeyi kullanıyor;
   ikisi ayrışırsa kart kendi kanıtıyla çelişir.
9. **Örnek kart kendi durum koduyla gelir** (`ornek`). Gerçek durumlardan
   ayrı bir değer taşıyor ki arayüz onu yanlışlıkla öneri diye çizmesin.
10. **Retina ekranda canvas boyutu geri okunmaz.** Grafik, Berk'in Mac'inde
    her Yenile'de ikiye katlanıyordu (260 → 520 → 1040 px). Sebep:
    yükseklik canvas'ın `height` özniteliğinden okunuyor, sonra piksel
    oranıyla (2) çarpılıp geri yazılıyordu. Piksel oranı 1 olan ekranda hiç
    görünmediği için ilk tarayıcı doğrulamasından kaçtı; Berk buldu.
    Düzeltme `bb6c581`: CSS yüksekliği `data-yukseklik` özniteliğinden
    okunuyor, arka tampon her çizimde `CSS boyutu × devicePixelRatio`
    olarak yeniden kuruluyor. `test_arayuz.py` içinde testi var. Canvas'a
    dokunan her değişiklik `device_scale_factor=2` ile, tekrarlı
    etkileşimle doğrulanmalı.
11. **Güncellemeden sonra "hâlâ bozuk" derse önce eski sekmeyi düşün.**
    İlk düzeltmeden sonra Berk aynı hatayı yine gördü: güncellemeden önce
    açılmış sekme eski JS'i çalıştırıyordu. Uygulamadaki Yenile düğmesi
    kodu değil yalnızca veriyi yeniler; önbellek başlığı da yoktu.
    Düzeltme `559b9eb`: bütün yanıtlar `Cache-Control: no-store`, statik
    dosya adresleri içerik özetiyle sürümlü (`?v=`), her `/api/` yanıtı
    `X-Arayuz-Surumu` başlığını taşıyor ve açık sekme sürüm değişince
    üstte sarı bir "sayfayı yenileyin" şeridi gösteriyor. Sürüm sayfanın en
    altındaki gri satırda ve `bash kurulum.sh arayuz` çıktısında yazıyor.
    Berk bir hatanın sürdüğünü söylerse **ilk soru**: iki yerdeki "Arayüz
    sürümü" aynı mı? Onayda geçerli sürüm `c9c97214`; statik dosyalar
    değişince bu değer de değişir.

## 7. Danışılmadan değiştirilmemesi gereken seçilmiş varsayılanlar

1. **"İncelenen adaylar" öneri kartına dönüşmez.** Eşiği geçmemiş adaylar
   ayrı bir bölümde, "kabul EDİLMEDİ" notuyla listeleniyor. Kart haline
   getirmek, Faz 2'nin engellemek için var olduğu hatayı arayüzden geri
   sokmak olur.
2. **Teşhis turundan çıkan kural deposu öneri üretmez.** Motor kabul edilmiş
   kural içerse bile reddediyor; sıfır maliyetle "kârlı" çıkan bir örüntü
   gerçekte zarar ettirir.
3. **Hedef 2 uydurulmaz.** Ölçülmüş medyan MFE Hedef 1'in altındaysa ikinci
   hedef **yoktur** ve sebebi kartta yazar.
4. **Borsa filtreleri yoksa varsayılan uydurulmaz.** Kart "filtreler elimde
   yok" der ve fiyatların yuvarlanmadığını yazar.
5. **Sunucu yalnızca 127.0.0.1.** Dinlenecek adres bilerek bayrak olarak
   sunulmuyor; bir bayrakla 0.0.0.0'a açılabilen yerel arayüz er ya da geç
   açılır. `test_arayuz.py` bunu test ediyor.
6. **Kapsam A ile daraltıldı:** BTCUSDT ve SOLUSDT, yalnızca 15m ve 1h.
   1m ve 5m kapsam dışı; sessizce geri getirme.

## 8. Şartnameden sapma — Berk'in onayına sunuldu

SPEC §3 arayüz için "React + Vite" öneriyor ve *"gerekçeyle
değiştirebilirsin"* diyor. Değiştirildi: arayüz, Python paketinin içinden
servis edilen **derleme adımı olmayan** düz HTML/CSS/JS. Gerekçe: React +
Vite kullanıcıdan Node ve npm kurmasını ister; Berk terminale alışık değil ve
ikinci bir kurulum zinciri kurulumun en kırılgan yerini iki katına
çıkarırdı. Mum grafiği de bağımlılıksız (`grafik.js`, ~120 satır canvas);
hazır kütüphane ya npm ya CDN demekti, CDN internetsiz çalışmaz.

**Berk Faz 3'ü bu sapmayla birlikte onayladı**, itiraz etmedi. İleride
isterse React'e geçmek mümkün; API tarafı değişmez.

## 9. Tarama motorunun güvencesi — bozmadan önce oku

Motorun doğruluğunun tek dayanağı üç testtir (`tests/test_scan.py`):

1. Rastgele yürüyüş verisinde, maliyet varken, düzeltmeden geçen örüntü
   **çıkmamalı**.
2. İçine bilerek kenar konmuş veride o kural **bulunabilmeli**.
3. Eğitim dilimi bittikten sonraki her şey bozulduğunda aday kümesi
   **harfi harfine aynı** kalmalı (keşif geleceğe bakmıyor).

Buna `tests/test_lookahead.py` içindeki 17 test ekleniyor. Faz 3'te bunların
üstüne şunlar geldi:

| Dosya | Ne koruyor |
|---|---|
| `test_kural_deposu.py` | Deponun gidiş-dönüşü; her kanıt alanı yolda kaybolmuyor |
| `test_card.py` | Kart aritmetiği, kâğıt hesabına karşı |
| `test_sizing.py` | Bağlayıcı kural, stepSize kaybı, komisyon dahil stop zararı |
| `test_oneri_motoru.py` | "Kural yok" ekranının içeriği, teşhis deposu reddi |
| `test_ek_araclar.py` | Panel, plan, izleme, sihirbaz, günlük (stop öncelikli sonuçlandırma) |
| `test_arayuz.py` | Her uç ayakta, parasal değerler metin, emir gönderen uç yok |

Bu testlerden biri kırmızıya dönerse çıktı güvenilirliğini kaybeder. Testi
değiştirerek geçirmek çözüm değil.

## 10. Faz 2'den taşınan tuzaklar (hâlâ geçerli)

1. **Çoklu test düzeltmesi bölüm içinde kalmamalı.** Koşunun tamamı
   üzerinden veriliyor (`scan.apply_global_correction`). Yeni bir tarama kipi
   eklenirse bu geçiş atlanmamalı.
2. **Keşif ile sınama aynı veriyi paylaşmamalı.** Adaylar eğitim
   diliminden, kabul p-değeri ayrılmış dönemden.
3. **Bootstrap p-değerinin tabanı `1/(yineleme+1)`**
   (`stats.required_iterations`).
4. **Yön körü kontrol yazma.** Kaçınma örüntüsünde ekside olmak doğru yön.
5. **Al ve kaçın testlerinin çıtası eşit değil.** "Al" maliyeti aşmak
   zorunda, "kaçın" yalnızca sıfırın altında olmak zorunda.
6. **Arşiv zaman damgaları mikrosaniye** (1 Ocak 2025'ten beri),
   REST milisaniye. Tek kapı `klines.normalize_epoch_ms`.
7. **Arşivler gün/ay bittikten sonra yayımlanır.** Bugünün dosyası 404 döner.
8. **Kapanmamış mum tahmine giremez.** Tek kapı `klines.closed_only`.
9. **Fiyat, miktar ve bakiyede `float` yok, `Decimal` var** (SPEC §3).

## 11. Yarım kalanlar ve Faz 4'e taşınan uyarılar

1. **Komisyon oranı hâlâ varsayım** (%0,1 / %0,1). Gerçek oran imzalı
   `GET /api/v3/account/commission` ile **Faz 4'te** ölçülecek. Ölçülünce
   Faz 1 tablosu, Faz 2 taraması **ve** kural deposu yeniden üretilmeli;
   öneri kartı maliyeti kural deposundan okuduğu için kart kendiliğinden
   düzelir. Teşhis turu gösterdi ki gerçek oran daha düşük olsa bile işaret
   değişmiyor.
2. **BNB ile komisyon ödeme kararı verilmedi.** Varsayılan temkinli
   (indirimsiz).
3. **Kâğıt işlem modu Faz 4'ün işi.** Sinyal günlüğü (`strategy/journal.py`)
   altyapısı hazır: kart yazılıyor, pencere dolunca sonuç aynı mum
   verisinden hesaplanıyor, "önerilen vs gerçekleşen" sapması çıkıyor.
   Performans ölçümü **oransal** yapılacak (Berk'in kararı).
4. **Telegram Faz 4'te.** İzleme paneli şu an okunur; alarm göndermiyor ve
   ekranda "bildirim yok" yazıyor. Olmayan bir şey vaat edilmedi.
5. **Risk motoru Faz 4'ün işi**, ama çekirdeği `risk/sizing.py` içinde hazır:
   bütçe mi risk kuralı mı bağlayıcı, stepSize yuvarlama kaybı, komisyon
   dahil stop zararı, NOTIONAL kontrolü. Faz 4 bunun üstüne günlük kayıp
   sınırı ve arka arkaya zarar freni koyacak.
6. **100 USDT bütçe kararı** yürürlükte.
7. **Testnet yerine Binance Demo Mode** kullanılacak (Berk'in kararı).
8. **OTOCO kısmi dolumda koruma sağlamıyor** (FAZ0 Risk #1). "Korumasız süre
   nöbetçisi" (`config/default.yaml` → `emir.korumasiz_azami_saniye`, 20 sn)
   onaylandı ama yazılmadı; Faz 5'in işi.
9. **Faz 5 bağımlılığı** `binance-sdk-spot` `pyproject.toml` içinde yorum
   satırında bekliyor. `binance-connector` PyPI'da "deprecated"; internetteki
   örneklerin çoğu hâlâ onu kullanıyor, kullanma.
10. **Piyasa verisi Berk'in Mac'inde hazır** (`~/Desktop/alsat/veri`).
    Yeniden indirtme. Ama **borsa filtreleri önbelleği
    (`veri/exchangeinfo.json`) onda henüz yok**: o dosya veri tazeleme
    adımında yazılıyor ve bu özellik Faz 3'te eklendi. Bir kez
    `bash kurulum.sh` (seçeneksiz) çalıştırması gerekir; o zamana kadar
    arayüz "borsa filtreleri indirilmemiş" diyecek ve fiyatları
    yuvarlamayacak. Bu beklenen durum.
11. **Yeni tarama turu istenirse bu bir çoklu test sorunudur.** Her yeni
    parametre denemesi kabul eşiğini sertleştirir. İstenirse yapılır ama
    bedeli söylenir.

## 12. Güvenlik (bunlar hiçbir fazda düşmez)

* **TLS sertifika doğrulaması hiçbir koşulda, geçici olarak bile
  kapatılmayacak.** Berk'in ağında HTTPS trafiğini yeniden imzalayan bir
  katman var; uygulama bu yüzden `truststore` ile macOS güven deposunu
  kullanıyor. Teşhis aracı: `albsat-tls-teshis`.
* Sırlar Berk'in bilgisayarından çıkmıyor: anahtar macOS Keychain'de, `.env`
  yalnızca anahtarın yerini söylüyor, ortam değişkenine sır konmuyor.
* Berk 21 Eylül 2026'da sohbete bir Binance API anahtarı yapıştırdı. İptal
  etmesi söylendi; anahtar kullanılmadı, hiçbir dosyaya, commit'e veya
  hafızaya yazılmadı. Anahtar isteme, yazdırma, dosyaya koyma.
* Gerçek para ile emir gönderecek ve API anahtarı gerektirecek her adım
  öncesinde Berk'e sorulacak. Uygulama her zaman "Sadece Öneri" modunda
  açılıyor.
* Binance'e giden istek hacmi sınırlanmalı. Beklenmedik büyüklükte bir iş
  çıkarsa program istek göndermeden durmalı.
* Komisyon oranı, sembol filtresi veya limit **koda sabit yazılmaz**;
  borsadan çekilir (SPEC §11).
* Arayüz yalnızca `127.0.0.1` dinler; bütün uçlar `GET`.

## 13. Bu notu sonraki faza devrederken

Berk'in kuralı: her faz yeni bir oturumda yürür ve devralan oturum **eksik
bilgi olmadan** devam edebilmelidir. Faz 4 bitince bu not baştan yazılır ve
şunları taşır:

1. Berk nasıl çalışıyor (Mac, terminal tanıdık değil, ilerleme yazdır,
   uzun çıktıyı dosya olarak yollar).
2. Çalışma ortamının kısıtları ve temiz-klon doğrulama kuralı.
3. Berk'in o fazda verdiği kararlar, **kendi cümleleriyle**.
4. O fazın sonucu bir paragrafta, ayrıntı için belge adresiyle.
5. O fazda öğrenilen, koda bakarak görülmeyecek tuzaklar.
6. Danışılmadan değiştirilmemesi gereken seçilmiş varsayılanlar.
7. Yarım kalanlar ve sonraki faza taşınan uyarılar.
8. Güvenlik kuralları (bunlar hiçbir fazda düşmez).

Belgelerin kendisi (`SPEC.md`, `FAZ0-MIMARI.md`, `FAZ1-FIZIBILITE.md`,
`FAZ2-SONUC.md`, `FAZ2-ORUNTU-MOTORU.md`, `FAZ3-ONERI-MOTORU.md`) yerinde
duruyor; bu not onların yerine geçmez, hangisinin ne zaman okunacağını
söyler.
