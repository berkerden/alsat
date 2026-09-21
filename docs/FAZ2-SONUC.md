# Faz 2 — Sonuç: örüntü araması ne buldu

**Tarih:** 22 Eylül 2026
**Kapsam:** BTCUSDT ve SOLUSDT, 15m ve 1h; hedef pencereleri 2, 3 ve 4 mum
(12 kombinasyon). Hedef 1,5×ATR, stop 1×ATR.
**Veri:** Berk'in kendi bilgisayarında, Binance'in kendi arşivinden inen
gerçek piyasa verisi. Ham çıktı: `faz2-oruntu-sonuc.txt`.

---

## 1. Tek cümlelik sonuç

**12 kombinasyonun hiçbirinde, çoklu test düzeltmesinden geçen tek bir
örüntü çıkmadı.** Ne "alınacak" ne de "kaçınılacak" listesinde.

Bu bir arıza değil, bir ölçüm sonucudur. Motorun doğru çalıştığının kanıtı
bulduğu örüntüler değil, bulmadıklarıdır: sentetik veriye bilerek konmuş bir
kuralı buluyor, rastgele yürüyüşte hiçbir şey bulmuyor, kuralı göremeyeceği
bir döneme koyduğumuzda keşfedemiyor. Üçü de `tests/test_scan.py` içinde.

## 2. Sonucun ne kadar net olduğu

Bu, "kıl payı kaçırdı" türü bir sonuç değil.

Rapor, düzeltmeden geçemese bile **ham p-değeri 0,05'in altında** kalan
örüntüleri ayrı bir başlıkta ("Düzeltme öncesi dikkat çekenler") gösterecek
şekilde yazılmıştı. **Bu başlık 12 bölümün hiçbirinde görünmedi.** Yani
hiçbir örüntü, çoklu test düzeltmesi *hiç uygulanmasaydı bile* geçerli
sayılacak bir p-değerine ulaşamadı.

Bu ayrım önemli: "düzeltme yüzünden elendi" demek, gerçek bir bulgunun
istatistiksel titizliğe kurban gittiğini ima ederdi. Olan bu değil. Aranan
şey orada yoktu.

## 3. Sayılar

Taban çizgisi — "her uygun mumda girilseydi" — 12 kombinasyonun hepsinde
neredeyse aynı çıktı:

| Seri | Olay | İşlem başına net | Denenen aday | Kabul |
|---|---:|---:|---:|---:|
| BTCUSDT 15m, 2 mum | 14.372 | %-0,2195 | 553 | 0 |
| BTCUSDT 15m, 3 mum | 14.371 | %-0,2218 | 548 | 0 |
| BTCUSDT 15m, 4 mum | 14.370 | %-0,2235 | 553 | 0 |
| BTCUSDT 1h, 2 mum | 4.604 | %-0,2202 | 357 | 0 |
| BTCUSDT 1h, 3 mum | 4.603 | %-0,2236 | 360 | 0 |
| BTCUSDT 1h, 4 mum | 4.602 | %-0,2265 | 388 | 0 |
| SOLUSDT 15m, 2 mum | 18.589 | %-0,2191 | 719 | 0 |
| SOLUSDT 15m, 3 mum | 18.588 | %-0,2216 | 732 | 0 |
| SOLUSDT 15m, 4 mum | 18.587 | %-0,2226 | 740 | 0 |
| SOLUSDT 1h, 2 mum | 4.665 | %-0,2232 | 455 | 0 |
| SOLUSDT 1h, 3 mum | 4.664 | %-0,2286 | 487 | 0 |
| SOLUSDT 1h, 4 mum | 4.663 | %-0,2367 | 480 | 0 |

Toplam 6.372 aday örüntü denendi. Kabul edilen: sıfır.

Maliyet eşiğini (%0,28) geçemediği için hiç işleme sokulmayan mum sayısı:
BTCUSDT 15m'de 5.050, SOLUSDT 15m'de 833, BTCUSDT 1h'de 61, SOLUSDT 1h'de
hiç yok. Yani sakin dönemlerde 15m grafiğinde hedef, maliyeti karşılayacak
büyüklükte bile değil.

## 4. Neden hiçbir şey çıkmadı — maliyet aritmetiği

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

## 5. Faz 1'in bulgusu nasıl netleşti

Faz 1 şunu söylemişti: kârlılığı hangi grafiğe bakıldığı değil, pozisyonda ne
kadar kalındığı belirliyor. Faz 2 bunu bir adım ileri taşıyor ve bir yanlış
anlamayı kapatıyor:

Faz 1'in √N tablosu, tipik fiyat hareketinin maliyetin iki katına ulaşması
için ne kadar beklemek gerektiğini gösteriyordu. O hareketin **senin lehine**
olacağını hiçbir zaman söylemedi. Yön bilgisi yoksa beklenen brüt kazanç
sıfırdır; maliyet ise sıfır değildir. Uzun tutmak bu yüzden tek başına
kurtarmıyor — yalnızca aynı sabit maliyeti daha seyrek ödetiyor.

Kârlılığın koşulu, tutma süresi değil **yön bilgisi**. Faz 2'nin ölçtüğü şey
tam olarak bu: 6.372 aday arasında, bu iki sembolde, bu iki periyotta, bu
özellik ailelerinde yön bilgisi taşıyan bir kural yok.

## 6. Bu koşuda kullanılan motorda bulunan kusur

Berk'in çalıştırdığı sürümde (commit `54bc71d`) istatistik tarafında iki
sorun vardı. İkisi de bu koşunun sonucunu değiştirmiyor — hiçbir örüntü
kabul eşiğinin yakınına bile gelmediği için — ama düzeltildi:

**1. Çözünürlük sınırı.** Bootstrap p-değerinin ölçebileceği en küçük değer
`1/(yineleme+1)` idi, yani 1.500 yinelemede 0,000666. Düzeltme eşiği ise
553 aday için 0,000181. Yani *gerçekten* güçlü tek bir örüntü, hak ettiği
halde reddedilirdi: p-değeri sıfıra ne kadar yakın olduğunu söyleyemiyordu.
Motor artık tabana dayanan örüntüleri eşiği çözecek yinelemeyle yeniden
ölçüyor.

**2. Keşif ile sınamanın aynı veriyi paylaşması.** Adaylar eğitim dönemine
bakılarak seçiliyordu, ama kabul kararını veren p-değeri **tüm seriden**
hesaplanıyordu — yani seçimde kullanılan veri, sınamada tekrar kullanılıyordu.
Bu, seçimin kendisini kanıt saymak demek. Artık kabul kararı **yalnızca
ayrılmış dönemden** (doğrulama + test, son %50) hesaplanıyor.

İkincisi daha ciddi olanı ve motoru **daha muhafazakâr** hale getiriyor:
karar artık yarım veriye dayanıyor. Bu koşunun sonucu bu yüzden
değişmeyecek, ama kayda geçen sayıların düzeltilmiş motordan gelmesi için
tarama bir kez daha çalıştırılmalı.

## 7. Faz 3'e taşınanlar

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
