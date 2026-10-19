# Günlük trend testi — sonuç

**Tarih:** 19 Ekim 2026 (Berk'in Mac'inde, `bash kurulum.sh trend`, kod
`370f157`)
**Ön kayıt:** `TREND-ONKAYIT.md` (sınama çalışmadan önce yazıldı; kontrol
coinleri eki de çalışmadan önce)
**Veri:** on iki serinin hepsi %100 eksiksiz, boşluk ve tekrar yok. BTCUSDT
2017-08-17 → 2026-10-18 (3.350 günlük mum), SOLUSDT 2020-08-11 → 2026-10-18
(2.260 mum). On kontrol coininin onu da ölçüldü; EOSUSDT 2025-05-26'da,
XMRUSDT 2024-02-20'de bitiyor (çiftlerin işlemi o gün durmuş), ön kayıt §5a'ya
göre son günlerine kadar ölçüldüler.
**Ham çıktı:** `trend-sonuc.txt` (Berk'in Mac'inde depo kökünde; kopyası proje
klasöründe `trend/` altında).

---

## 1. Tek cümlelik sonuç

On sınamadan **biri geçti: SOLUSDT'de 20/10 gün kırılımı** (q = 0,094, eşik
0,10; kontrol coinlerinin 6/10'unda da al-ve-tut'tan iyi). Kalan dokuzdan
yedisi "Belirsiz", ikisi "Geçmedi".

## 2. Bütün kararlar

Sharpe ve en büyük düşüş: kural / al-ve-tut. Kontrol: kuralın al-ve-tut'tan
iki ölçüde de iyi olduğu kontrol coini sayısı.

| Coin | Kural | Sharpe | En büyük düşüş | p | q | Kontrol | Karar |
|---|---|---|---|---:|---:|---:|---|
| BTC | 200 günlük ortalama | 0,90 / 0,81 | %-64,3 / %-76,6 | 0,348 | 0,507 | 2/10 | Belirsiz |
| BTC | 50/200 kesişimi | 0,76 / 0,81 | %-66,8 / %-76,6 | 0,570 | 0,570 | 0/10 | Geçmedi |
| BTC | 55/20 gün kırılımı | 1,05 / 0,81 | %-38,6 / %-76,6 | 0,201 | 0,503 | 3/10 | Belirsiz |
| BTC | 20/10 gün kırılımı | 0,97 / 0,81 | %-55,6 / %-76,6 | 0,270 | 0,507 | 6/10 | Belirsiz |
| BTC | 12 aylık momentum | 0,89 / 0,81 | %-63,3 / %-76,6 | 0,355 | 0,507 | 4/10 | Belirsiz |
| SOL | 200 günlük ortalama | 1,01 / 0,66 | %-66,2 / %-96,3 | 0,152 | 0,503 | 2/10 | Belirsiz |
| SOL | 50/200 kesişimi | 0,68 / 0,66 | %-78,5 / %-96,3 | 0,461 | 0,570 | 0/10 | Belirsiz |
| SOL | 55/20 gün kırılımı | 0,97 / 0,66 | %-65,1 / %-96,3 | 0,192 | 0,503 | 3/10 | Belirsiz |
| SOL | **20/10 gün kırılımı** | **1,43 / 0,66** | **%-56,4 / %-96,3** | **0,009** | **0,094** | **6/10** | **Geçti** |
| SOL | 12 aylık momentum | 0,63 / 0,66 | %-89,6 / %-96,3 | 0,534 | 0,570 | 4/10 | Geçmedi |

q-değerleri rapordaki p-değerlerinden ayrıca yeniden hesaplandı
(Benjamini–Hochberg, on sınama); rapordakilerle aynı.

## 3. Geçen kural: SOLUSDT, 20/10 gün kırılımı

*Kural:* kapanış önceki 20 günün en yüksek kapanışını aşınca al, önceki 10
günün en düşük kapanışının altına inince sat.

| SOLUSDT, 2021-08-11 → 2026-10-18 (5,2 yıl) | Toplam getiri | Yıllık | En büyük düşüş | Sharpe | Piyasada | Alış/yıl |
|---|---:|---:|---:|---:|---:|---:|
| Al-ve-tut | %+149 | %+19 | %-96,3 | 0,66 | %100 | — |
| 20/10 gün kırılımı | %+3.003 | %+94 | %-56,4 | 1,43 | %35 | 7,3 |
| Aynı kural, bir gün geç uygulansa (karar dışı) | %+1.562 | %+72 | %-57,4 | 1,22 | — | — |

* **Sağlamlık sınaması:** Sharpe farkı +0,76; %90 aralığı +0,16 … +1,30
  (sıfırı içermiyor). p = 0,0094, on sınama birlikte düzeltilince q = 0,094.
* **Al-ve-tut'un iki büyük düşüşünde:** 2021-11-06 → 2022-12-29 al-ve-tut
  %-96,3, kural %-53,6; 2025-01-18 → 2026-06-06 al-ve-tut %-76,3, kural
  %-9,8.
* **Kontrol coinleri:** ETH, XRP, EOS, XLM, ADA ve IOTA'da iyi; BCH, LTC, XMR
  ve TRX'te değil. Sharpe farkının ortancası +0,12. Beş kural içinde
  kontrolü tutan tek kural bu.
* **BTC'de aynı kural:** Belirsiz. Sharpe 0,97 / 0,81, en büyük düşüş
  %-55,6 / %-76,6, ama q = 0,507.
* **Son kapanıştaki durumu (2026-10-18):** nakitte bekle. Bilgi, öneri değil.

## 4. Sonuca ne kadar güvenilir

* **Kıl payı geçti.** q 0,094, eşik 0,10. Ön kaydın yapay veri ölçümlerine
  göre hiçbir kuralın gerçek bir üstünlüğü yokken de on sınamadan birinin
  "Geçti" çıkma olasılığı %1 ile %10 arasındaydı (ön kayıt §6). Sonuç şansı
  dışlamaz; şansla açıklanmasını zorlaştırır.
* **Dönem kısa, büyük olay az.** SOL'da 5,2 yıl ve iki büyük düşüş var.
  Kuralın üstünlüğünün büyük kısmı bu iki düşüşte çok daha az kaybetmesinden
  geliyor gibi görünüyor (§3'teki düşüş satırlarından çıkarım; yıl yıl
  ayrıştırma raporda yok).
* **Lehine olanlar:** bir gün geç uygulansa da al-ve-tut'tan açıkça iyi
  (Sharpe 1,22); kontrol coinlerinin çoğunda tuttu; BTC'de de iki ölçüde
  al-ve-tut'tan iyi göründü (ama şanstan ayırt edilemedi).
* **Aleyhine olanlar:** kontrol 6/10 ile sınırın hemen üstünde; BTC'de
  belirsiz; yüzlerce kural yerine beş kural denendiği için çıta düşüktü
  (önceki 12.021 aday sayılsaydı geçemezdi, ön kayıt §2).
* Bu sonuç **"bu geçmişte işe yaradı ve şansla kolay açıklanmıyor"**
  demektir. Geleceğin aynı olacağını söylemez; yatırım tavsiyesi değildir.

## 5. Diğer kurallar

* Ön kaydın beklentisi, yani büyük düşüşlerin bir kısmından kaçmak, BTC'de
  görüldü: 2021-11 → 2022-11 düşüşünde (al-ve-tut %-76,6) beş kuralın hepsi
  %-36 ile %-46 arasında kaldı. Ama bu üstünlük şanstan ayırt edilecek kadar
  sağlam değildi (q ≥ 0,50) ve dört kural kontrol coinlerinin yarısından
  azında tuttu (2/10, 0/10, 3/10, 4/10).
* Ön kayıt §8'e göre "Belirsiz" ve "Geçmedi" kurallar için bu aile kapandı;
  uygulama bunları öneri olarak göstermez.

## 6. Ön kayda göre ne olacak (§8)

* Geçen kural **hiçbir parayla hemen kullanılmaz.**
* Berk isterse, arayüzde **yalnızca SOLUSDT için** günlük bir "trend durumu"
  göstergesi olarak gösterilir: bugün "elde tut" mu "nakitte bekle" mi.
  Bilgidir, emir değildir.
* Kâğıt işlemde ya da canlıda kullanmak ayrı kararlardır; her biri Berk'in
  ayrı onayını ister. Canlıya geçiş kapısı en az 30 kural işlemi istiyor;
  yılda yaklaşık 7 alışla bu 4 yıl kadar sürer. Kapının böyle bir kurala nasıl
  uygulanacağı o zaman ayrıca konuşulur.
* Aynı soru başka parametre, coin ya da periyotla yeniden sorulmaz. BTC için
  20/10'un ayarlarını değiştirip yeniden denemek de buna dahildir.
* Bu on sınama giriş sinyali ailesinin birikimli sayısına (12.021) eklenmez;
  kural deposuna (`veri/kurallar.json`) dokunulmadı.

## 7. Bekleyen karar

Berk'in kararı: SOLUSDT için 20/10 trend durumu göstergesi arayüze eklensin
mi. Karar verilene kadar arayüzde bir şey değişmez.
