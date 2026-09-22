# Devir notu — Faz 2'den Faz 3'e

22 Eylül 2026. Bu not, koda ve diğer belgelere bakarak öğrenilemeyecek
şeyleri yeni oturuma aktarmak içindir. Şartname `SPEC.md`, mimari kararlar
`docs/FAZ0-MIMARI.md`, fizibilite sonucu `docs/FAZ1-FIZIBILITE.md`,
**Faz 2 sonucu `docs/FAZ2-SONUC.md`**, yöntem `docs/FAZ2-ORUNTU-MOTORU.md`.

## Berk nasıl çalışıyor

* Mac kullanıyor, kod `~/Desktop/alsat` altında. Terminal, git ve GitHub
  akışları ona tanıdık değil.
* Kendi bilgisayarında atması gereken adımlar tek tek, sade ve elinden
  tutarak anlatılmalı. Çıplak komut listesi verme.
* Güncelleme komutu tek satıra indirgendi:
  `cd ~/Desktop/alsat && git pull && bash kurulum.sh`
  Sonuna `tarama` eklenirse internete çıkan adım atlanır, `teshis` eklenirse
  ayrıca maliyetsiz teşhis turu da çalışır.
* Uzun süren her adım ekrana ilerleme yazmalı. Sessiz ekrana bakınca
  takıldığını sanıyor.
* Uzun çıktıları sohbete yapıştıramıyor; dosya olarak yolluyor.
* Kod GitHub'da `berkerden/alsat`, `main` dalında duruyor; Berk'in
  bilgisayarında da yedeği var.

## Berk şu ana kadar neyi çalıştırdı

* Faz 1 fizibilite taraması: bir kez, gerçek veriyle. Veri
  `~/Desktop/alsat/veri` altında indirilmiş ve doğrulanmış durumda
  (1 Mart – 21 Eylül 2026, sekiz seri, %100 eksiksiz).
* Faz 2 örüntü taraması: üç kez (motor iki kez düzeltildi arada).
  Sonuncusu kesin kayıt.
* Maliyetsiz teşhis turu: bir kez.

Yeniden indirtme, yeniden çalıştırtma gerekmiyor; sonuçlar belgelerde.

## Eldeki komut satırı araçları

| Araç | Ne yapar | Ağa çıkar mı |
|---|---|---|
| `albsat-fizibilite` | Veri indirir, kalite raporu ve fizibilite taraması | Evet |
| `albsat-oruntu` | Örüntü keşfi, istatistik, backtest, rapor | **Hayır** |
| `albsat-oruntu --maliyetsiz` | Aynısı, maliyet sıfır sayılarak (teşhis) | **Hayır** |
| `albsat-tls-teshis` | Sertifika zinciri teşhisi | Evet |

`bash kurulum.sh` hepsini sırayla sarmalıyor; `tarama` ve `teshis`
seçenekleri yukarıda.

## Bu çalışma ortamının kısıtları

* Ağ politikası **api.binance.com ve data.binance.vision adreslerini
  engelliyor** (proxy CONNECT'e 403 dönüyor). Buradan gerçek piyasa verisi
  indirilemez, tarama gerçek sayılarla çalıştırılamaz. Gerçek veri gerektiren
  her adımı Berk kendi Mac'inde çalıştırıyor, çıktıyı sohbete yapıştırıyor.
* PyPI erişilebilir, kurulum ve testler burada çalışır.
* Kapsayıcının varsayılan `python3`'ü 3.11 olabilir; proje 3.12+ istiyor.
  Sanal ortamı `python3.12 -m venv` ile kur.
* Bu oturumların yetkisi yeni GitHub deposu açmayı kapsamıyor; depoyu Berk
  açtı, `add_repo` ile bağlandı.

## Doğrulama kuralı

Berk'e "çalıştır" demeden önce depoyu **geçici bir dizine temiz klonlayıp**
kurulumu ve testleri orada çalıştır. Buradaki çalışma dizininde testlerin
geçmesi kanıt değil: 21 Eylül'de `.gitignore` içindeki sabitlenmemiş `data/`
kuralı `src/albsat/data/` kaynak paketini yuttu, burada testler geçti,
Berk'in bilgisayarında `ModuleNotFoundError` ile patladı.

## Kolay tekrar düşülecek tuzaklar

* **Arşiv zaman damgaları mikrosaniye.** Binance 1 Ocak 2025'ten itibaren
  `data.binance.vision` SPOT arşivlerinde mikrosaniye, `GET /api/v3/klines`
  ise milisaniye veriyor. `albsat.data.klines.normalize_epoch_ms` bunu
  çözüyor; zaman damgası okuyan yeni kod da oradan geçmeli. Aksi halde
  ardışık her mum çifti "boşluk" sanılır (bir kez oldu: 293.759 sahte
  boşluk).
* **Berk'in ağında HTTPS trafiğini yeniden imzalayan bir katman var.**
  Uygulama bu yüzden `truststore` ile macOS güven deposunu kullanıyor.
  Sertifika doğrulaması **hiçbir koşulda, geçici olarak bile kapatılmayacak**;
  gerçek emir gönderecek bir uygulamada araya girme riski doğurur. Teşhis
  aracı: `albsat-tls-teshis`.
* **Arşivler gün/ay bittikten sonra yayımlanır.** Bugünün dosyası kesin 404
  döner; `plan_archives` bugünü plana almıyor, kalanı REST tamamlıyor.
* **Kapanmamış mum tahmine giremez** (SPEC §11). Tek kapı
  `albsat.data.klines.closed_only`.
* **Fiyat, miktar ve bakiyede `float` yok, `Decimal` var** (SPEC §3).
  `albsat.core.money` `float` alırsa reddediyor.

## Güvenlik

* Sırlar Berk'in bilgisayarından çıkmıyor: anahtar macOS Keychain'de, `.env`
  yalnızca anahtarın yerini söylüyor, ortam değişkenine sır konmuyor.
* Berk 21 Eylül 2026'da sohbete bir Binance API anahtarı yapıştırdı. İptal
  etmesi söylendi; anahtar kullanılmadı, hiçbir dosyaya, commit'e veya
  hafızaya yazılmadı. Anahtar isteme, yazdırma, dosyaya koyma.
* Gerçek para ile emir gönderecek her adım öncesinde Berk'e sorulacak.
  Uygulama her zaman "Sadece Öneri" modunda açılıyor.

## Faz 2 ne buldu (tek paragraf)

Hiçbir şey, ve bu ölçülmüş bir sonuç. BTCUSDT + SOLUSDT, 15m + 1h, 2/3/4
mumluk pencerelerde 6.372 aday denendi; çoklu test düzeltmesinden geçen
örüntü çıkmadı. Ardından maliyet tamamen sıfır sayılıp arama tekrarlandı
(9.215 aday) — yine sıfır. Maliyet kaldırıldığında rastgele işlem yapmak
yazı turaya dönüşüyor (denemelerin %39,5–%52'si artıda, ortalama sıfır),
yani maliyetli koşudaki %-0,22'nin tamamı maliyetti ve altında kenar yok.
İstatistiksel disiplin tamamen bırakılıp 9.215 aday içinden en iyi görünen
seçilse bile en yüksek değer işlem başına %+0,2304; maliyet %0,28, BNB
indirimiyle %0,23. **Maliyeti düşürmek bir yol açmıyor.** Ayrıntı ve
sayılar `docs/FAZ2-SONUC.md`.

Ölçülen her bölümde en iyi sonucu al-ve-tut verdi (BTCUSDT %+28,8,
SOLUSDT %+41,1, komisyon sonrası). Bu bir yatırım tavsiyesi değil, bu veri
kümesindeki bir ölçüm sonucudur; ama Faz 3 kapsamını Berk bu bulguyu
bilerek seçti.

## Faz 3 kapsamı — Berk'in kararı (22 Eylül 2026)

Berk'in kendi cümlesi: *"a ile devam edip b fonksiyonunu da ekleyerek öneri
veya lazım olduğunda kullanılabilecek gibi ek şekilde yapabilir miyiz"*.

**A — şartnamedeki Faz 3.** Periyot sihirbazı, öneri motoru, Sadece Öneri
arayüzü (SPEC §10). Öneri motoru Faz 2'nin **kabul ettiği** kuralları okur.
Şu an kabul edilmiş kural yok, bu yüzden dürüst çıktısı "önerilecek kural
yok" olacak ve nedenini gösterecek: taban çizgisi, maliyet eşiği, en iyi
adayın eşikten uzaklığı.

**B eki — aynı uygulamanın içinde, lazım olunca açılan ek bölüm.** Berk'e
sunulan B seçeneğinin tanımı şuydu: sık al-sat yerine **izleme, maliyet/risk
paneli ve disiplinli alım**. Ana akış değil, ek yetenek.

Bunun dışına çıkma. Kapsamı genişleten her fikir önce Berk'e sorulur.

### Kabul kriteriyle ilgili bir gerilim, baştan bil

SPEC §10 Faz 3'ün kabul kriteri: *"Öneri kartları eksiksiz; marjlar komisyon
sonrası doğru."* Kabul edilmiş kural olmadığı için ortada gösterilecek öneri
kartı yok. Bu kriteri "kart uydur" diye okuma. Doğru okuma: **kart şablonu
eksiksiz ve marj hesabı doğru olmalı**, yani bir kural çıktığında kart
eksiksiz doldurulabilmeli ve komisyon sonrası marj doğru çıkmalı. Kartın
doğruluğu sentetik/örnek bir kuralla gösterilebilir; kullanıcıya sunulan
ekranda ise dürüst "önerilecek kural yok" durumu görünür. Bunu Berk'e Faz 3
sonunda açıkça anlat.

### Faz 3 için seçilmiş varsayılan (Berk'e danışmadan değiştirme)

Öneri motoru **"düzeltme öncesi dikkat çekenler" listesini öneri kartı
olarak göstermez.** Onlar rapora bilerek "kanıt sayılmaz" notuyla konuyor;
arayüzde kart haline getirmek, Faz 2'nin engellemek için var olduğu hatayı
kullanıcı arayüzünden geri sokmak olur. Gösterilecekse ayrı bir "incelenen
adaylar" bölümünde, kabul edilmediği açıkça yazılarak gösterilir. Berk
aksini isterse o zaman konuşulur.

## Tarama motorunun güvencesi — bozmadan önce oku

Motorun doğruluğunun tek dayanağı üç testtir (`tests/test_scan.py`):

1. Rastgele yürüyüş verisinde, maliyet varken, düzeltmeden geçen örüntü
   **çıkmamalı**.
2. İçine bilerek kenar konmuş veride o kural **bulunabilmeli**.
3. Eğitim dilimi bittikten sonraki her şey bozulduğunda aday kümesi
   **harfi harfine aynı** kalmalı (keşif geleceğe bakmıyor).

Buna `tests/test_lookahead.py` içindeki 17 test ekleniyor: geleceği bozma,
kesip yeniden hesaplama, girişin bir sonraki mumun açılışı olması, kapanmamış
mumun tabloya hiç girmemesi.

Bu testlerden biri kırmızıya dönerse rapor güvenilirliğini kaybeder. Motora
dokunan her değişiklikte hepsi yeşil kalmalı; testi değiştirerek geçirmek
çözüm değil.

## Faz 2'den öğrenilen, koda bakarak görülmeyecek tuzaklar

1. **Çoklu test düzeltmesi bölüm içinde kalmamalı.** Bir koşuda 12 bölüm var
   (2 sembol × 2 periyot × 3 pencere); her biri kendi içinde %10 payla
   düzeltilirse, ortada hiçbir şey yokken bile ortalama 1,2 "buluş" çıkar.
   Bu teorik değil, oldu: tam olarak bir örüntü kabul edildi. Kabul kararı
   artık `scan.apply_global_correction` ile koşunun tamamı üzerinden
   veriliyor. Yeni bir tarama kipi eklenirse bu geçiş atlanmamalı.
2. **Keşif ile sınama aynı veriyi paylaşmamalı.** Adaylar yalnızca eğitim
   diliminden seçilir, kabul p-değeri yalnızca ayrılmış dönemden hesaplanır.
   Aksi hâlde seçimin kendisi kanıt sayılır.
3. **Bootstrap p-değerinin tabanı `1/(yineleme+1)`.** Düzeltme eşiği bunun
   altındaysa gerçekten güçlü bir örüntü hak ettiği halde reddedilir.
   `stats.required_iterations` bunu çözüyor.
4. **Yön körü kontrol yazma.** Kaçınma örüntüsünde ekside olmak doğru
   yöndür; kararlılık kontrolü bir süre bunu uyarı olarak yazdı.
5. **Al ve kaçın testlerinin çıtası eşit değil.** "Al" örüntüsü maliyeti
   aşmak zorunda, "kaçın" örüntüsü yalnızca sıfırın altında olmak zorunda.
   Raporda kaçınma tarafında daha çok aday görünmesi bir piyasa bulgusu
   değil, bu asimetridir.

## Yarım kalanlar ve Faz 3'e taşınan uyarılar

1. **Komisyon oranı hâlâ varsayım** (%0,1 / %0,1). Gerçek oran imzalı
   `GET /api/v3/account/commission` ile Faz 4'te ölçülecek; Faz 1 ve Faz 2
   tablolarındaki her sayı o zaman yenilenmeli. Teşhis turu gösterdi ki
   gerçek oran daha düşük olsa bile işaret değişmiyor.
2. **BNB ile komisyon ödeme kararı verilmedi.** Varsayılan temkinli
   (indirimsiz). BNB bakiyesi tutmayı ve kendi fiyat riskini gerektirir.
3. **Kapsam A ile daraltıldı**: BTCUSDT ve SOLUSDT, yalnızca 15m ve 1h.
   1m ve 5m kapsam dışı; sessizce geri getirme, gerekirse Berk'e sor.
4. **Yeni tarama turu istenirse bu bir çoklu test sorunudur.** Her yeni
   parametre denemesi kabul eşiğini sertleştirir; motor tam olarak bunu
   engellemek için var. İstenirse yapılır ama bedeli söylenir.
5. **OTOCO kısmi dolumda koruma sağlamıyor** (FAZ0 Risk #1). "Korumasız süre
   nöbetçisi" (`config/default.yaml` → `emir.korumasiz_azami_saniye`, 20 sn)
   onaylandı ama henüz yazılmadı; Faz 5'in işi.
6. **Faz 5 bağımlılığı** `binance-sdk-spot` `pyproject.toml` içinde yorum
   satırında bekliyor. `binance-connector` PyPI'da "deprecated"; internetteki
   örneklerin çoğu hâlâ onu kullanıyor, kullanma.
7. **100 USDT bütçe kararı.** Risk motoru her öneride bütçenin mi risk
   kuralının mı bağlayıcı olduğunu gösterecek ve stepSize yuvarlamasından
   doğan hatayı hesaba katacak (Faz 4, ama B ekindeki maliyet/risk paneli
   aynı hesabı kullanacak).
8. **Piyasa verisi Berk'in Mac'inde hazır** (`~/Desktop/alsat/veri`).
   Yeniden indirtme.

## Bu notu sonraki faza devrederken

Berk'in kuralı: her faz yeni bir oturumda yürür ve devralan oturum **eksik
bilgi olmadan** devam edebilmelidir. Faz 3 bitince bu not baştan yazılır ve
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
`FAZ2-SONUC.md`, `FAZ2-ORUNTU-MOTORU.md`) yerinde duruyor; bu not onların
yerine geçmez, hangisinin ne zaman okunacağını söyler.
