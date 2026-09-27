# Günlük trend testi — ön kayıt

**Tarih:** 28 Eylül 2026
**Karar:** Berk, "işe yarar bir şeyler bulmak için ne önerirsin" sorusunun
ardından gelen üç seçenekten **Günlük trend testi**ni seçti. Seçeneğin
sözü: *"4-5 klasik kuralı günlük grafikte bir kez sınarım. Kodu ben yazarım,
sen Mac'te bir kez çalıştırırsın."*
**Çalıştırma:** Berk'in Mac'inde
`cd ~/Desktop/alsat && git pull && bash kurulum.sh trend`
**Sonuç:** henüz yok.

Bu belge sınama çalıştırılmadan **önce** yazıldı ve depoya gönderildi.
Sınama burada yazılanla birebir çalıştırılır. Sonuç görüldükten sonra hiçbir
ayar değiştirilmez; ayar değiştirip yeniden çalıştırmak yeni bir sınama olur
ve öyle sayılır.

Kod: `src/albsat/research/trend.py` (kurallar, ölçüm, sınama),
`src/albsat/research/trend_report.py` (rapor), `src/albsat/cli/trend.py`
(indirme ve çalıştırma), testler `tests/test_trend.py`.

---

## 1. Soru

Önceki iki tur 15 dakikalık ve 1 saatlik grafikte, birkaç saat sonrasını
öngören bir giriş sinyali aradı ve bulamadı (`FAZ2-SONUC.md`,
`TUR2-SONUC.md`). Orada sorun, her alım-satımın %0,28'lik maliyetinin
fiyatın o kısa sürede yaptığı tipik harekete çok yakın olmasıydı.

Bu sınama soruyu değiştiriyor: **günlük grafikte, önceden seçilmiş beş klasik
trend takibi kuralı "yalnızca eğilim yukarıyken elde tut, değilse nakitte
bekle" diyerek al-ve-tut'tan daha iyi bir risk/getiri dengesi veriyor mu?**
Günlük grafikte tipik hareket maliyetin on katından büyük ve bir kural yılda
birkaç kez alıp satar; maliyet belirleyici olmaktan çıkar.

Beklenti al-ve-tut'u getiride geçmek değil, **büyük düşüşlerin bir kısmından
kaçmak**. Bu yüzden karar iki ölçüye bakar: risk başına getiri (Sharpe) ve en
büyük düşüş.

## 2. Önceki 12.021 adayla ilişkisi

Önceki iki tur toplam **12.021** giriş sinyali adayı denedi. Bu sınamanın on
alt sınaması o aileye **eklenmez**; ayrı bir aile olarak düzeltilir. Berk'e
"az kural denenince kabul çıtası da düşük kalır" denildi; bu cümlenin
dayanağı bu ayrımdır, gerekçesi ve bedeli şöyle:

* **Soru farklı.** Önceki aile şunu sordu: "15 dakikalık ya da 1 saatlik bir
  mumda X örüntüsü görülünce, sonraki 2-4 mumda (30 dakika ile 4 saat) fiyat
  maliyeti aşacak kadar yükselir mi?" Bu sınama şunu soruyor: "Haftalar ve
  aylar süren bir yükseliş varken elde tutup düşüşe dönünce nakde geçmek,
  dönemin tamamında al-ve-tut'tan daha iyi risk/getiri verir mi?" Veri farklı
  (günlük mum önceki turlarda hiç kullanılmadı), zaman ölçeği yüzlerce kat
  uzun, ölçü farklı (işlem başına net getiri yerine dönemin Sharpe'ı ve en
  büyük düşüşü).
* **Örtüşme var ve saklanmıyor.** Önceki aday uzayında trend durumu bildiren
  özellikler de vardı: EMA 20 > 50 > 200 dizilimi, EMA 20/50 kesişimi, 20
  mumluk kırılım, ADX ile trend rejimi, MACD. Ama 1 saatlik grafikte 200 mum
  yaklaşık 8 gündür; bu sınamadaki 200 günlük ortalamanın 25'te biri. Orada
  sorulan, bu durumların sonraki birkaç saati öngörüp öngörmediğiydi.
* **Kurallar veriden seçilmedi.** Önceki turlarda aday uzayı veride tarandı
  ve en iyisi arandı. Burada beş kural, kaynaklarındaki parametrelerle ve
  günlük veri hiç görülmeden sabitlendi; "en iyisini seçme" adımı yok.
* **Önceki adaylar sayılsaydı:** tek bir alt sınamanın eşiği
  `0,10 / 12.031 ≈ 0,0000083` olurdu. Bu sınamanın ulaşabileceği en küçük p
  `1 / 10.001 ≈ 0,0001`. Yani önceki adaylar sayılsaydı sınama başlamadan
  kaybedilmiş olurdu. Rapor bunu her seferinde tek satırla yazar.
* **Bedeli, proje düzeyinde:** her yeni soru kendi ailesiyle sorulursa "bir
  şey çıkana kadar soru değiştirmek" mümkün olur. Bunu sınırlamak için bu
  soru **bir kez** sorulur, sonucu ne olursa olsun aynı kural ailesi (başka
  parametre, başka coin, başka periyot) yeniden sorulmaz (§7), ve önceki
  turların sonucuna bakılarak hiçbir kural ya da ayar seçilmedi.
* Bu on alt sınama giriş sinyali ailesinin birikimli sayısına (12.021)
  eklenmez; kural deposuna (`veri/kurallar.json`) dokunulmaz. Arayüz
  "önerilecek kural yok" demeye devam eder.

## 3. Kurallar

Beşi de yalnızca kapanış fiyatına bakar, yalnızca "elde tut" ya da "nakitte
bekle" der (açığa satış, kaldıraç yok), pozisyonun tamamıyla girer ve çıkar.

| # | Kural | Tanım | Kaynak |
|---|---|---|---|
| 1 | 200 günlük ortalama | Kapanış 200 günlük basit ortalamanın üstündeyse elde tut, altındaysa sat. | En bilinen trend filtresi; Faber (2007) aylık karşılığını kullanır. |
| 2 | 50/200 kesişimi | 50 günlük ortalama 200 günlüğün üstündeyse elde tut, altına inince sat. | Klasik "altın kesişim". |
| 3 | 55/20 gün kırılımı | Kapanış önceki 55 günün en yüksek kapanışını aşınca al; önceki 20 günün en düşük kapanışının altına inince sat. | Donchian kanalı; "Kaplumbağalar"ın 2. sistemi. |
| 4 | 20/10 gün kırılımı | Kapanış önceki 20 günün en yüksek kapanışını aşınca al; önceki 10 günün en düşük kapanışının altına inince sat. | Donchian kanalı; "Kaplumbağalar"ın 1. sistemi. |
| 5 | 12 aylık momentum | Kapanış 365 gün önceki kapanışın üstündeyse elde tut, altındaysa sat. | Zaman serisi momentumu; Moskowitz, Ooi ve Pedersen (2012). |

Kaynaklardan bilinçli sapmalar: kırılım kuralları gün içi en yüksek/en düşük
yerine kapanışla çalışır ve Kaplumbağaların "kazançlı işlemden sonraki
sinyali atla" filtresi, kademeli alım ve oynaklığa göre pozisyon büyüklüğü
yoktur. Momentum 12 ayı takvim yılı olarak alır (kripto her gün işlem görür).

## 4. Veri ve ölçüm

* **Semboller:** BTCUSDT ve SOLUSDT; periyot 1d (günlük).
* **Veri:** Binance'in herkese açık `GET /api/v3/klines` ucundan, her coinin
  Binance'teki ilk günlük mumundan (BTCUSDT Ağustos 2017, SOLUSDT Ağustos
  2020) çalıştırılan gün kapanmış son günlük muma kadar. Coin başına 3-4
  istek; 8'i aşacak bir hesap çıkarsa hiçbir istek gönderilmeden durulur.
  Veri eksiksiz değilse (%99,5 altı, tekrar ya da bozuk mum) ya da son mum
  üç günden eskiyse sınama başlamaz.
* **Dönem:** her coinde ilk mumdan 365 gün sonra başlar (en uzun kural olan
  12 aylık momentumun ısınması), son kapanmış mumda biter. Beş kural ve
  al-ve-tut aynı dönemde ölçülür. Bitiş günü seçilmez; çalıştırılan gündür.
* **Karar ve işlem:** karar günlük mumun kapanışında (00:00 UTC, İstanbul
  saatiyle 03:00) verilir ve ertesi günün getirisini alır; işlem kapanış
  fiyatından yapılmış sayılır.
* **Maliyet:** önceki turlarla aynı tanım. Gidiş-dönüş %0,28 (ölçülen
  komisyon %0,1 + %0,1, spread %0,01, kayma %0,02, güvenlik payı %0,05); her
  alışta ve her satışta yarısı, %0,14. Komisyon `veri/komisyon.json`'daki
  ölçülen orandan okunur, yoksa %0,1 varsayılır.
* **Nakitte** getiri sıfırdır (USDT faiz getirmez). Dönem başında herkes
  nakittedir; dönem sonunda açık pozisyon satılmış sayılır. Al-ve-tut dönem
  başında alır, sonunda satar, aynı maliyeti öder.
* **Ölçüler:** toplam ve yıllık getiri, en büyük düşüş, Sharpe (günlük net
  getirilerden, √365 ile yıllık, risksiz faiz sıfır), piyasada kalma oranı,
  yıllık alış sayısı.
* **Karar dışı, yalnızca bilgi için:** al-ve-tut'un en derin üç düşüşünde
  her kuralın aynı aralıktaki sonucu; her kararın bir gün geç uygulandığı
  durum (elle işlem yapan biri kararı ertesi gün uygulayabilir).

## 5. Karar ölçütü

On alt sınama var: beş kural × iki coin. Her biri için:

* **Geçti:** q ≤ 0,10 **ve** kuralın Sharpe'ı al-ve-tut'unkinden yüksek
  **ve** en büyük düşüşü al-ve-tut'unkinden küçük.
* **Belirsiz:** Sharpe yüksek ve düşüş küçük, ama q > 0,10. Al-ve-tut'tan
  iyi görünüyor, şanstan ayırt edilemiyor.
* **Geçmedi:** Sharpe al-ve-tut'unkinden yüksek değil ya da en büyük düşüşü
  daha derin.

q, on alt sınamanın p-değerlerinin birlikte Benjamini–Hochberg ile
düzeltilmiş halidir; yanlış buluş payı önceki turlarla aynı, %10. Hiçbir
kuralın gerçek bir üstünlüğü yoksa on alt sınamadan en az birinin "Geçti"
çıkma olasılığı en fazla %10 olmalı; yapay veride %1 ile %10 arasında
ölçüldü (§6).

Kural ve coin çifti birlikte değerlendirilir: BTC'de geçen bir kural SOL
için geçmiş sayılmaz.

## 6. Sağlamlık sınaması ve nasıl seçildiği

**Sınama:** her kural için "Sharpe'ı al-ve-tut'unkinden yüksek mi" sorusu,
tek yönlü. Kuralın ve al-ve-tut'un günlük net getirileri **eşli durağan blok
bootstrap** ile yeniden örneklenir: dönem, ortalama **20 günlük** parçalar
halinde rastgele yeniden dizilerek **10.000** alternatif dönem kurulur
(Politis–Romano). Kural ve al-ve-tut her alternatifte aynı günleri alır.
Sınama ortalanmış bootstrap'tır:
`p = (1 + #{fark* − fark ≥ fark}) / 10.001`. Tohum **20260927**; aynı
veride aynı sonuç çıkar. Rapor farkın %5–%95 aralığını da yazar.

Sade anlatımıyla: bazı alternatif dönemler büyük bir düşüşü iki kez, bazıları
hiç içermez. Kuralın üstünlüğü yalnızca birkaç şanslı dönemden geliyorsa bu
alternatiflerde kolayca kaybolur.

**Nasıl seçildi.** Bu belge yazılırken iki başka sınama denendi ve
**yalnızca yapay veride** elendi; gerçek günlük veriye hiç bakılmadı.

1. *Rastgele zamanlama* (kuralla aynı gün sayısı piyasada kalan, aynı sayıda
   alıp satan rastgele takvimler). Eğilim yokken bile trend kurallarını
   sistematik olarak cezalandırıyordu: kural düşüşü tetikleyen günleri
   yaşayıp yükselişi tetikleyen günleri kaçırır, rastgele takvim bu bedeli
   ödemez. Sınama gereğinden sert ve kör çıkıyordu.
2. *Getirilerin sırasını karıştırmak* (kural karıştırılmış geçmişte yeniden
   uygulanır). Düşüşten sonra oynaklığın arttığı, ama yön bilgisi olmayan
   yapay veride iki coinden en az birinde "Geçti" çıkma oranı **%28**'e
   çıktı; izin verilen en fazla %10. Kural gerçekten "eğilim" değil
   "oynaklık" zamanlaması yapıyordu, ama al-ve-tut'u yine de güvenilir
   biçimde geçmiyordu.

Seçilen sınamanın yön bilgisi olmayan yapay verilerde "en az bir Geçti"
oranı (her satır 200 deneme; her denemede iki yapay coin, ikisi de BTC ya da
SOL uzunluğunda; bootstrap 1.000 alternatif dönem):

| Yapay veri (yön bilgisi yok) | BTC uzunluğu | BTC uzunluğu, 60 günlük parça | SOL uzunluğu |
|---|---:|---:|---:|
| Oynaklık sabit, sürüklenme yok (sınır durum) | %10 | %9 | %4 |
| Düşüşten sonra oynaklık artıyor, sürüklenme yok | %3 | %5 | — |
| Düşüşten sonra oynaklık artıyor, yukarı sürüklenme (günde %0,1) | %1 | %1 | — |

Ortalama parça uzunluğu 20 gün seçildi: oynaklığın kümelenmesini parça
içinde tutacak kadar uzun, sekiz yıllık dönemde yüzlerce parça verecek kadar
kısa. 60 günle sonuçlar aynı düzeyde çıktı (tablo ve §10); seçim sonucu
değiştirmiyor.

## 7. Tekrar yok

* `bash kurulum.sh trend`, rapor dosyası (`trend-sonuc.txt`) varsa hiçbir
  şey indirmeden durur.
* Veri indirilemezse, eksik ya da bozuksa sınama hiç başlamaz; o çalıştırma
  sayılmaz ve yeniden denenir.
* Sınama rapor yazılmadan kesilirse (sonuç görülmeden) yeniden başlatmak
  aynı sınamadır.
* Kurallar, parametreler, maliyet, dönem, 10.000 alternatif, 20 günlük
  parça, tohum ve karar ölçütü kodda sabittir; değiştirmek yeni bir
  sınamadır ve bu belgeye göre önerilmez.

## 8. Sonuca göre ne olacak

* **En az bir "Geçti":** kural hiçbir parayla hemen kullanılmaz. Sonuç
  Berk'e anlatılır. İsterse, arayüzde yalnızca geçen kural ve coin için
  günlük bir "trend durumu" göstergesi olarak gösterilir (bilgi; emir
  değil). Kâğıt işlemde ya da canlıda kullanmak ayrı kararlardır ve her biri
  Berk'in ayrı onayını ister. Canlıya geçiş kapısı en az 30 kural işlemi
  istiyor; yılda birkaç kez alıp satan bir kural için bu yıllar sürer, kapının
  böyle bir kurala nasıl uygulanacağı o zaman ayrıca konuşulur.
* **Geçen yok, "Belirsiz" var:** bu aile kapanır; uygulama bunu öneri olarak
  göstermez. Bu kuralları kendi disiplini olarak kullanmak Berk'in
  tercihidir; rapor ve yanıt, bunun bir kanıt değil bir tercih olacağını
  açıkça yazar.
* **Hepsi "Geçmedi":** bu aile kapanır.

Her durumda aynı soru başka parametre, coin ya da periyotla yeniden
sorulmaz. Uygulama izleme, maliyet/risk hesabı, disiplinli alım planı ve
elle işlem aracı olarak kalır.

## 9. Beklenti ve sınırlar

* **Getiri beklentisi.** Trend kuralları düşüş başladıktan sonra çıkar,
  yükseliş başladıktan sonra girer. En iyi durumda bile düşüşün bir kısmını
  yaşar ve yükselişin bir kısmını kaçırır; yatay piyasada boşa alıp satar.
  Al-ve-tut'tan az kazandırıp daha az düşüş yaşamaları normaldir.
* **Sonucun "Belirsiz" çıkması güçlü bir ihtimal.** Isınmadan sonra BTC'de
  yaklaşık 8, SOL'da yaklaşık 5 yıllık veri kalıyor ve bu sürede yalnızca
  birkaç büyük düşüş dönemi var; sonuç büyük ölçüde onların davranışına
  dayanır. Yapay veride, aylarca süren belirgin eğilimler varken bile bu
  sınama kuralı iki coinden en az birinde yaklaşık yarı yarıya yakalıyor;
  eğilim zayıfsa çok daha az (§10).
* **Geçmiş bilgisi.** Bu kuralların kripto geçmişinde nasıl davrandığı genel
  olarak bilinir; kurallar bu genel bilgiden tamamen bağımsız seçilemez. Bu
  yüzden "Geçti" sonucu da "bu geçmişte işe yaradı ve şansla açıklanmıyor"
  demektir. Geleceğin aynı olacağını söylemez; yatırım tavsiyesi değildir.

## 10. Bu belge yazılırken

* Gerçek günlük veriye bakılmadı. Bu ortamdan Binance'e erişim yok, başka
  bir kaynaktan da günlük veri indirilmedi. Araç yalnızca yapay veriyle
  sınandı.
* Yapay veri ölçümleri: yön bilgisi yokken yanlış alarm oranı §6'daki
  tabloda. Bilerek eğilim konmuş veride sınamanın kuralı yakalama oranı (en
  az bir "Geçti"; bootstrap 1.000, her satır 100 deneme, her denemede iki
  yapay coin):

  | Yapay veri | BTC uzunluğu | BTC uzunluğu, 60 günlük parça | SOL uzunluğu |
  |---|---:|---:|---:|
  | Uzun ve belirgin eğilim (yükseliş ort. 400, düşüş 250 gün) | %48 | %50 | %42 |
  | Uzun ve zayıf eğilim | %16 | %12 | — |

* Tam çalıştırma (10.000 alternatif dönem, iki coin) yapay veriyle bu
  ortamda 7 saniye sürdü; ekrana her 1.000 dönemde bir ilerleme yazar.

* `tests/test_trend.py`: kuralların geleceğe bakmadığı (sonraki fiyatlar
  bozulunca önceki kararlar değişmiyor), kaynaktaki tanımlarla birebir
  çalıştığı, maliyetin her alış ve satışta ödendiği, sınamanın üstünlük
  yokken yanlış alarm vermediği ve bilerek eğilim konmuş veride kuralı
  bulduğu, indirmenin birkaç istekle bittiği ve raporun bir kez yazıldığı.
