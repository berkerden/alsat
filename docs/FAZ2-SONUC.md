# Faz 2 — Sonuç: örüntü araması ne buldu

**Tarih:** 22 Eylül 2026
**Kapsam:** BTCUSDT ve SOLUSDT, 15m ve 1h; hedef pencereleri 2, 3 ve 4 mum
(12 kombinasyon). Hedef 1,5×ATR, stop 1×ATR.
**Veri:** Berk'in kendi bilgisayarında, Binance'in kendi arşivinden inen
gerçek piyasa verisi. Ham çıktı: `faz2-oruntu-sonuc.txt`.

---

## 1. Tek cümlelik sonuç

**Alınacak örüntü yok.** 12 kombinasyonun hiçbirinde, çoklu test
düzeltmesinden geçen bir "al" kuralı çıkmadı. Toplam 6.372 aday denendi.

Tek bir istisna kaçınma listesinde çıktı ve onun da kalmaması gerekiyor;
3. bölüm bunu anlatıyor.

Bu bir arıza değil, bir ölçüm sonucudur. Motorun doğru çalıştığının kanıtı
bulduğu örüntüler değil, bulmadıklarıdır: sentetik veriye bilerek konmuş bir
kuralı buluyor, rastgele yürüyüşte hiçbir şey bulmuyor, keşfi eğitim dönemine
kapatınca sonraki veriden etkilenmiyor. Üçü de `tests/test_scan.py` içinde.

## 2. Sonucun ne kadar net olduğu

Bu, "kıl payı kaçırdı" türü bir sonuç değil. Her bölüm, incelediği adaylar
içindeki **en küçük ham p-değerini** ve o bölümde tek bir örüntünün kabul
edilmesi için gereken eşiği yazıyor:

| Seri | En küçük ham p | Gereken eşik | Uzaklık |
|---|---:|---:|---:|
| BTCUSDT 15m, 2 mum | 0,00266 | 0,00018 | 15 kat |
| BTCUSDT 15m, 3 mum | 0,00009 | 0,00018 | **eşiğin altında** |
| BTCUSDT 15m, 4 mum | 0,00133 | 0,00018 | 7 kat |
| BTCUSDT 1h, 2 mum | 0,04330 | 0,00028 | 155 kat |
| BTCUSDT 1h, 3 mum | 0,07595 | 0,00028 | 273 kat |
| BTCUSDT 1h, 4 mum | 0,07262 | 0,00026 | 282 kat |
| SOLUSDT 15m, 2 mum | 0,01066 | 0,00014 | 77 kat |
| SOLUSDT 15m, 3 mum | 0,03664 | 0,00014 | 268 kat |
| SOLUSDT 15m, 4 mum | 0,04530 | 0,00014 | 335 kat |
| SOLUSDT 1h, 2 mum | 0,01199 | 0,00022 | 55 kat |
| SOLUSDT 1h, 3 mum | 0,01865 | 0,00021 | 91 kat |
| SOLUSDT 1h, 4 mum | 0,05596 | 0,00021 | 269 kat |

On iki bölümün on birinde en iyi aday, eşiğin 7 ila 335 katı uzağında. Bu
aralıkta "düzeltme yüzünden elendi" demek yanlış olur: düzeltme hiç
uygulanmasaydı bile çoğu geçemezdi.

## 3. Eşiğin altında kalan tek bölüm

BTCUSDT 15m, 3 mumluk pencere, **kaçınılacaklar** listesinde bir örüntü kabul
edildi: *kapanış üst Bollinger bandının üstünde + fiyat yükselmiş ama hacim
ortalamanın altında*. Ayrılmış dönemde 56 bağımsız olay, p=0,0001, bölüm içi
düzeltmeyle q=0,05.

Buna dayanarak bir şey yapılmamalı, üç sebeple:

**1. Bu, şansın üreteceği sayının kendisi.** Koşuda 12 bölüm var ve her biri
kendi içinde %10 yanlış buluş payıyla düzeltiliyor. Ortada hiçbir şey yokken
bile beklenen buluş sayısı `12 × 0,10 ≈ 1,2`. Bulunan: 1. Bölüm içi düzeltme
bunu göremez çünkü kaç bölüm çalıştırıldığını bilmez.

Motor bu yüzden değiştirildi: kabul kararı artık koşunun tamamı üzerinden
veriliyor (`scan.apply_global_correction`). Raporun kendi sayılarıyla
hesaplandığında bu örüntü o düzeltmeden geçmiyor — koşu genelinde deneme
sayısı 6.372, en küçük p-değerinin geçmesi için gereken eşik
`0,10 / 6.372 = 0,0000157`, örüntünün p-değeri ise 0,00009. Yaklaşık altı kat
uzakta.

**2. Komşu pencerelerde izi yok.** Aynı sembolde aynı periyotta 2 ve 4 mumluk
pencerelerde görünmüyor. Gerçek bir piyasa davranışı olsaydı yan pencerelerde
de iz bırakması beklenirdi; yalnızca 3'te çıkması tesadüfün imzasıdır.

**3. İşe yararlığı sınırlı.** Ölçülen etki brüt %-0,14; maliyet %0,28. Ters
yönde işlem yapmaya yetmez (zaten kapsam dışı), yalnızca "bu durumda alma"
filtresi olabilirdi — ve 14.371 mumun 129'unu kapsıyor. Taban çizgisini
değiştirmez.

## 4. Sayılar

Taban çizgisi — "her uygun mumda girilseydi" — 12 kombinasyonun hepsinde
neredeyse aynı çıktı:

| Seri | Olay | İşlem başına net | Denenen aday |
|---|---:|---:|---:|
| BTCUSDT 15m, 2 mum | 14.372 | %-0,2195 | 553 |
| BTCUSDT 15m, 3 mum | 14.371 | %-0,2218 | 548 |
| BTCUSDT 15m, 4 mum | 14.370 | %-0,2235 | 553 |
| BTCUSDT 1h, 2 mum | 4.604 | %-0,2202 | 357 |
| BTCUSDT 1h, 3 mum | 4.603 | %-0,2236 | 360 |
| BTCUSDT 1h, 4 mum | 4.602 | %-0,2265 | 388 |
| SOLUSDT 15m, 2 mum | 18.589 | %-0,2191 | 719 |
| SOLUSDT 15m, 3 mum | 18.588 | %-0,2216 | 732 |
| SOLUSDT 15m, 4 mum | 18.587 | %-0,2226 | 740 |
| SOLUSDT 1h, 2 mum | 4.665 | %-0,2232 | 455 |
| SOLUSDT 1h, 3 mum | 4.664 | %-0,2286 | 487 |
| SOLUSDT 1h, 4 mum | 4.663 | %-0,2367 | 480 |

Maliyet eşiğini (%0,28) geçemediği için hiç işleme sokulmayan mum sayısı:
BTCUSDT 15m'de 5.050, SOLUSDT 15m'de 833, BTCUSDT 1h'de 61, SOLUSDT 1h'de
hiç yok. Yani sakin dönemlerde 15m grafiğinde hedef, maliyeti karşılayacak
büyüklükte bile değil.

## 5. Neden hiçbir şey çıkmadı — maliyet aritmetiği

Taban çizgisinin her yerde **yaklaşık %-0,22** çıkması tesadüf değil.
Maliyet işlem **başına** sabit: komisyon %0,2 + spread %0,01 + kayma %0,02.
Hedefe ulaşma ve stopa çarpma oranları birbirini dengeliyor, geriye sabit
maliyet kalıyor.

Rastgele giriş kıyası bunu doğrudan gösteriyor. İşlem sayısı azaldıkça
toplam kayıp azalıyor — ama işlem başına kayıp aynı kalıyor:

| Seri | İşlem | Rastgele girişin toplamı | Artıda biten deneme |
|---|---:|---:|---:|
| BTCUSDT 15m, 2 mum | 353 | %-54,06 | %0,0 |
| BTCUSDT 15m, 4 mum | 172 | %-32,40 | %0,0 |
| BTCUSDT 1h, 4 mum | 55 | %-11,85 | %0,5 |
| SOLUSDT 15m, 2 mum | 455 | %-62,99 | %0,0 |
| SOLUSDT 1h, 4 mum | 56 | %-12,74 | %1,5 |

Her satır, işlem başına %-0,22'nin o kadar kez bileşiklenmesiyle
0,1–1,1 puan içinde örtüşüyor. Yani daha uzun tutmak maliyeti
**seyreltmiyor**; sadece daha az işlem yapılmasını sağlıyor.

Körlemesine işlem yapmanın yıllık bedeli, aynı aritmetikle:

| Yılda işlem | Yıl sonu |
|---:|---:|
| 100 | %-19,8 |
| 300 | %-48,4 |
| 600 | %-73,3 |
| 1.000 | %-88,9 |

Aynı dönemde al-ve-tut: BTCUSDT %+28,8, SOLUSDT %+41,1 (komisyon sonrası).

## 6. Faz 1'in bulgusu nasıl netleşti

Faz 1 şunu söylemişti: kârlılığı hangi grafiğe bakıldığı değil, pozisyonda ne
kadar kalındığı belirliyor. Faz 2 bunu bir adım ileri taşıyor ve bir yanlış
anlamayı kapatıyor:

Faz 1'in √N tablosu, tipik fiyat hareketinin maliyetin iki katına ulaşması
için ne kadar beklemek gerektiğini gösteriyordu. O hareketin **senin lehine**
olacağını hiçbir zaman söylemedi. Yön bilgisi yoksa beklenen brüt kazanç
sıfırdır; maliyet ise sıfır değildir. Uzun tutmak bu yüzden tek başına
kurtarmıyor — yalnızca aynı sabit maliyeti daha seyrek ödetiyor.

Kârlılığın koşulu, tutma süresi değil **yön bilgisi**. Bir sonraki adım
(maliyetsiz teşhis turu) tam olarak bunu ölçüyor: maliyet hiç yokmuş gibi
sayıldığında ortada ölçülebilir bir yön bilgisi kalıyor mu?

## 7. Koşular sırasında motorda bulunan kusurlar

Üçü de gerçek veriyle çalıştıktan sonra bulundu ve düzeltildi.

**1. Çözünürlük sınırı.** Bootstrap p-değerinin ölçebileceği en küçük değer
`1/(yineleme+1)` idi, yani 1.500 yinelemede 0,000666. Düzeltme eşiği ise
553 aday için 0,000181. *Gerçekten* güçlü tek bir örüntü, hak ettiği halde
reddedilirdi. Motor artık tabana dayanan örüntüleri eşiği çözecek yinelemeyle
yeniden ölçüyor. (3. bölümdeki örüntü bu düzeltmeden sonra görünür oldu.)

**2. Keşif ile sınamanın aynı veriyi paylaşması.** Adaylar eğitim dönemine
bakılarak seçiliyordu, ama kabul kararını veren p-değeri **tüm seriden**
hesaplanıyordu. Bu, seçimin kendisini kanıt saymak demek. Artık kabul kararı
**yalnızca ayrılmış dönemden** (doğrulama + test, son %50) hesaplanıyor.

**3. Düzeltmenin bölüm içinde kalması.** 12 bölüm ayrı ayrı düzeltiliyordu;
bu, hiçbir şey yokken bile ortalama 1,2 buluş üretir. Kabul kararı artık
koşunun tamamı üzerinden veriliyor. Düzeltme kör bir sertleştirme değil:
bölümlerin hepsinde görünen gerçek bir kenar sıralamada üstte kaldığı için
hayatta kalır, yalnızca tek bölümde parlayan bir şans elenir.

Ayrıca raporlamada bir kusur: kararlılık kontrolü yönü hesaba katmıyordu, bu
yüzden bir kaçınma örüntüsünün her dönemde ekside olması — yani tam olarak
işe yaradığı durum — uyarı olarak yazılıyordu. Düzeltildi. Aynı şekilde
"düzeltme öncesi dikkat çekenler" notu yalnızca "alınacaklar" listesinde
gösteriliyordu; artık iki listede de gösteriliyor.

## 8. Faz 3'e taşınanlar

1. Komisyon %0,1/%0,1 hâlâ varsayım. Hesaba özel gerçek oran Faz 4'te
   ölçülecek ve bu rapordaki her sayı yenilenecek. Gerçek oran daha düşükse
   taban çizgisi yukarı kayar, ama işaret değişmez.
2. Örüntüler en fazla iki özelliğin kesişimi. Üçlü kombinasyon aday sayısını
   on binlere çıkarır ve düzeltme eşiğini aynı oranda sertleştirir.
3. Aranan şey "giriş sinyali"ydi. Bu Faz 2'nin kapsamıydı ve kapsam
   tüketildi; kârlılığın başka bir yerden gelmesi gerekiyorsa o başka bir
   sorudur.
4. Giriş araştırmada piyasa emri sayıldı, `LIMIT_MAKER` değil (SPEC §4.5).
   Dolmama riski olan bir emri dolmuş saymak sonucu iyimser gösterirdi.

---

**Yöntem ayrıntısı:** `docs/FAZ2-ORUNTU-MOTORU.md`.
**Önceki faz:** `docs/FAZ1-FIZIBILITE.md`.
