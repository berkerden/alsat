# İkinci kural arama turu — sonuç

**Tarih:** 28 Eylül 2026 (Berk'in Mac'inde, `bash kurulum.sh tur2`)
**Ön kayıt:** `TUR2-ONKAYIT.md` (tur çalışmadan önce yazıldı)
**Veri:** BTCUSDT ve SOLUSDT, 15m ve 1h, 2024-09-01 → 2026-09-27 indirildi;
dört seri %100 eksiksiz, boşluk ve tekrar yok. Tarama en yeni mumdan geriye
730 günü kullandı.
**Ham çıktılar:** `tur2-oruntu-sonuc.txt`, `tur2-veri-sonuc.txt` (Berk'in
Mac'inde depo kökünde; kopyaları proje klasöründe `tur2/` altında).

---

## 1. Tek cümlelik sonuç

**Kabul edilen örüntü yok** — 12 bölümün hiçbirinde, ne alınacaklar ne
kaçınılacaklar listesinde. Bu tur 5.649 aday denedi; Faz 2'nin 6.372 adayıyla
birlikte kabul kararı **12.021 deneme** üzerinden verildi. Tek bir örüntünün
kabul eşiği `0,10 / 12.021 = 0,0000083`.

Ön kaydın §5'ine göre bu kapsamdaki giriş sinyali araması (BTCUSDT ve SOLUSDT,
15m ve 1h, tek ve ikili özellik, 2-4 mumluk hedef) **kapandı**.

## 2. Sonucun ne kadar net olduğu

| Seri | En küçük ham p | Eşiğin kaç katı |
|---|---:|---:|
| BTCUSDT 15m, 2 mum | 0,03065 | 3.684 |
| BTCUSDT 15m, 3 mum | 0,00799 | 961 |
| BTCUSDT 15m, 4 mum | 0,00733 | 881 |
| BTCUSDT 1h, 2 mum | 0,03398 | 4.084 |
| BTCUSDT 1h, 3 mum | 0,03464 | 4.165 |
| BTCUSDT 1h, 4 mum | 0,05396 | 6.487 |
| SOLUSDT 15m, 2 mum | 0,00333 | 400 |
| SOLUSDT 15m, 3 mum | 0,00266 | 320 |
| SOLUSDT 15m, 4 mum | 0,00999 | 1.201 |
| SOLUSDT 1h, 2 mum | 0,01066 | 1.281 |
| SOLUSDT 1h, 3 mum | 0,00533 | 641 |
| SOLUSDT 1h, 4 mum | 0,00799 | 961 |

En yakın aday eşiğin 320 katı uzağında (SOLUSDT 15m, 3 mum, bir kaçınma
örüntüsü, ayrılmış dönemde yalnızca 18 bağımsız olay). Düzeltilmiş q-değerleri
her yerde 1,00.

**Çözünürlük sınırı bu turda devreye girmedi.** En küçük ham p (0,00266),
1.500 yinelemenin tabanının (0,00067) dört katı; hiçbir aday tabana oturmadı,
yani aile turu (`scan.refine_for_family`) yeniden ölçecek bir şey bulmadı.
Sayılar ölçümün sınırı değil, adayların kendisi.

**Alınacaklar listesinde dikkat çeken aday yok**, 12 bölümün hiçbirinde;
"düzeltme öncesi dikkat çekenler" yalnızca kaçınma listesinde. Bu, Faz 2'deki
gibi iki testin çıtasının farklı olmasından (`FAZ2-SONUC.md` §2): "al" maliyeti
aşmak zorunda, "kaçın" yalnızca sıfırın altında olmak zorunda.

## 3. Faz 2'nin en yakın adayına ne oldu

Faz 2'de BTCUSDT 15m 3 mumda en yakın aday bir kaçınma örüntüsüydü (*kapanış
üst Bollinger bandının üstünde + fiyat yükselmiş ama hacim ortalamanın
altında*, p=0,00009, çözünürlük tabanında). Bu turda aynı bölümün dikkat
çekenleri arasında **yok**; bölümün en iyi adayı p=0,00799. Faz 2'nin hiç
görmediği keşif döneminde (Eylül 2024 → Eylül 2025) tutmadı. Tek dönemde
parlayan bir şansın beklenen davranışı budur.

## 4. Taban çizgisi ve kıyaslar

Her uygun mumda girilseydi işlem başına net **%-0,217 ile %-0,231** arası,
12 bölümün hepsinde; Faz 2'deki %-0,22 ile aynı. Maliyet işlem başına sabit
ve altında bir yön bilgisi yok.

İki yıl boyunca rastgele giriş (200 deneme, aynı hedef/stop):

| | 2 mum | 3 mum | 4 mum |
|---|---:|---:|---:|
| BTCUSDT 15m | %-94,4 | %-85,3 | %-76,4 |
| BTCUSDT 1h | %-59,3 | %-45,3 | %-36,8 |
| SOLUSDT 15m | %-97,6 | %-91,9 | %-84,7 |
| SOLUSDT 1h | %-62,0 | %-47,1 | %-39,2 |

Denemelerin hiçbiri artıda bitmedi. Daha çok işlem, daha çok kayıp.

Al-ve-tut (aynı iki yıl, komisyon sonrası): **BTCUSDT %+27,9, SOLUSDT
%-23,1**. Faz 2'nin 204 günlük döneminde ikisi de artıdaydı (%+28,8 ve
%+41,1). Yani al-ve-tut da dönemine bağlı; bu bir öneri değil, ölçüm.

## 5. Fizibilite, iki yıllık veriyle

| | Oran (ATR medyanı / %0,28) | Sonuç |
|---|---:|---|
| BTCUSDT 15m | 0,97 | uygun değil |
| BTCUSDT 1h | 2,11 | uygun |
| SOLUSDT 15m | 1,80 | sınırda |
| SOLUSDT 1h | 3,81 | uygun |

Faz 1'in 204 günlük tablosuyla aynı resim (`FAZ1-FIZIBILITE.md`).

## 6. Bundan sonrası

* Kural deposu (`veri/kurallar.json`) artık bu turu taşıyor: 0 kabul edilen
  kural, birikimli 12.021 aday. Arayüz "önerilecek kural yok" demeye devam
  eder ve sayıları bu turdan yazar. Faz 2'nin deposu
  `veri/kurallar-faz2.json`'da.
* Ön kayda göre aynı aramayı daha çok veriyle ya da ayar değiştirerek yeniden
  çalıştırmak önerilmez. Yeni bir soru (başka sembol, başka hedef tanımı,
  giriş sinyali dışında bir amaç) ayrı bir karar ve ayrı bir ön kayıttır;
  birikimli sayı düzeltmeye kendiliğinden girer.
* Uygulama izleme, maliyet/risk hesabı, disiplinli alım planı ve elle
  (kâğıt, Demo, canlı) işlem aracı olarak kalır. Tam Otomatik kapıda kilitli.
