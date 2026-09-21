# Faz 1 çıktısı — Fizibilite taraması

**Tarih:** 21 Eylül 2026
**Veri:** 1 Mart 2026 → 21 Eylül 2026 (204 gün), BTCUSDT ve SOLUSDT, 1m/5m/15m/1h
**Kaynak:** data.binance.vision aylık/günlük arşivleri (SHA256 doğrulanmış) +
`GET /api/v3/klines` ile son güne kadar tamamlama

---

## 1. Veri kalitesi

Sekiz serinin sekizi de eksiksiz:

| Sembol | Periyot | Mum | Eksiksizlik | Eksik | Tekrar | Boşluk | Sonuç |
|---|---|---|---|---|---|---|---|
| BTCUSDT | 1m | 294.735 | %100,00 | 0 | 0 | 0 | kullanılabilir |
| BTCUSDT | 5m | 58.947 | %100,00 | 0 | 0 | 0 | kullanılabilir |
| BTCUSDT | 15m | 19.649 | %100,00 | 0 | 0 | 0 | kullanılabilir |
| BTCUSDT | 1h | 4.913 | %100,00 | 0 | 0 | 0 | kullanılabilir |
| SOLUSDT | 1m | 294.735 | %100,00 | 0 | 0 | 0 | kullanılabilir |
| SOLUSDT | 5m | 58.947 | %100,00 | 0 | 0 | 0 | kullanılabilir |
| SOLUSDT | 15m | 19.649 | %100,00 | 0 | 0 | 0 | kullanılabilir |
| SOLUSDT | 1h | 4.913 | %100,00 | 0 | 0 | 0 | kullanılabilir |

SPEC.md §4.1'in istediği kalite eşiği (%99,5 eksiksizlik, geçersiz OHLC yok,
tekrar kayıt yok) karşılandı.

---

## 2. Maliyet eşiği

Bir gidiş-dönüş işlemin başabaş noktası:

```
komisyon (giriş maker + çıkış taker)   %0,2000
spread beklentisi                       %0,0100
kayma beklentisi                        %0,0200
güvenlik payı                           %0,0500
-------------------------------------------------
en az anlamlı hedef                     %0,2800
```

Bu eşiğin altında kalan her hareket, doğru tahmin edilse bile zarar eder.

**Komisyon oranı burada varsayımdır.** %0,1 / %0,1, Binance'in herkese açık
VIP0 spot oranı. Hesaba özel gerçek oran imzalı `GET /api/v3/account/commission`
ile çekilir; bu Faz 4'te API anahtarıyla gelecek. Oran değişirse tablo da
değişir.

---

## 3. Fizibilite tablosu

Oran = tipik mum hareketi (ATR medyanı) / en az anlamlı hedef.
Oran 1'in altı: o periyotta tek mumluk işlem matematiksel olarak zararına.

### 3a. BNB indirimi olmadan (eşik %0,28) — çalıştırılan tarama

| Sembol | Periyot | ATR% medyan | Oran | Sonuç |
|---|---|---|---|---|
| BTCUSDT | 1m | 0,0447 | 0,16 | UYGUN DEĞİL |
| BTCUSDT | 5m | 0,1294 | 0,46 | UYGUN DEĞİL |
| BTCUSDT | 15m | 0,2532 | 0,90 | UYGUN DEĞİL |
| BTCUSDT | 1h | 0,5545 | 1,98 | SINIRDA |
| SOLUSDT | 1m | 0,0807 | 0,29 | UYGUN DEĞİL |
| SOLUSDT | 5m | 0,2095 | 0,75 | UYGUN DEĞİL |
| SOLUSDT | 15m | 0,3918 | 1,40 | SINIRDA |
| SOLUSDT | 1h | 0,8322 | 2,97 | UYGUN |

### 3b. BNB indirimi ile (eşik %0,23) — aynı ATR değerleriyle hesaplandı

Bu tablo yeniden tarama değil; yukarıdaki ATR ölçümleri projenin kendi maliyet
motoruna %0,075 / %0,075 komisyonla verilerek üretildi.

| Sembol | Periyot | Oran | Sonuç |
|---|---|---|---|
| BTCUSDT | 1m | 0,19 | UYGUN DEĞİL |
| BTCUSDT | 5m | 0,56 | UYGUN DEĞİL |
| BTCUSDT | 15m | 1,10 | SINIRDA |
| BTCUSDT | 1h | 2,41 | UYGUN |
| SOLUSDT | 1m | 0,35 | UYGUN DEĞİL |
| SOLUSDT | 5m | 0,91 | UYGUN DEĞİL |
| SOLUSDT | 15m | 1,70 | SINIRDA |
| SOLUSDT | 1h | 3,62 | UYGUN |

BNB indirimi hiçbir senaryoda 1m'i kurtarmıyor, 5m'i de kurtarmıyor. 15m'i
sınıra taşıyor, 1h'i rahatlatıyor.

---

## 4. Bulgunun şartnameyle çelişen tarafı

SPEC.md'nin hedefi "küçük ve sık hareketleri yakalamak" olarak yazılmıştı.
Ölçüm bunun tersini söylüyor: **hareket küçüldükçe maliyet oransal olarak
büyüyor ve sıklık kazandırmıyor, kaybettiriyor.** BTCUSDT 1m'de tipik mum
hareketi maliyetin altıda biri kadar; o periyotta mükemmel bir tahmin motoru
bile zarar eder.

Bu, şartnamenin "kâr vaat etme, bulamadığını da söyle" ilkesinin tam olarak
beklediği türden bir çıktı ve Faz 1'in en değerli sonucu: örüntü motorunu
kurup aylar sonra öğrenmek yerine, veriyi indirdikten hemen sonra öğrendik.

---

## 5. Asıl belirleyici periyot değil, tutma süresi

ATR **tek bir mumun** tipik hareketini ölçer. Bir pozisyon birden çok mum
boyunca tutulursa hedefin daha büyük olması beklenir. Rastgele yürüyüş
varsayımıyla hareket yaklaşık √N ile büyür (N = tutulan mum sayısı).

Maliyetin iki katına (%0,56) ulaşmak için gereken tahmini tutma süresi:

| Sembol | 1m'den | 5m'den | 15m'den | 1h'ten |
|---|---|---|---|---|
| BTCUSDT | 157 dk | 94 dk | 73 dk | 61 dk |
| SOLUSDT | 48 dk | 36 dk | 31 dk | 27 dk |

Satırların kendi içinde birbirine yakın olması tesadüf değil: **hangi grafiğe
baktığımız ekonomiyi değiştirmiyor, ne kadar tuttuğumuz değiştiriyor.**
1m sütununun diğerlerinden yüksek çıkması, 1 dakikalık ATR'nin alış-satış
sıçramasıyla şişmesinden kaynaklanıyor; en güvenilir tahmin 1h sütunu.

Kısaca: **BTCUSDT'de yaklaşık bir saat, SOLUSDT'de yaklaşık yarım saat**
tutulan bir pozisyon, tipik piyasada maliyetin iki katı bir hareket görür.
√N yaklaşımı bir tahmindir; gerçek dağılım Faz 2'de ölçülecek.

---

## 6. Faz 2 için öneri

Örüntü aramasını **15m ve 1h** üzerinde yapmak, hedefleri tek mum değil
2–4 mumluk pencerelerde tanımlamak.

Gerekçe:

* 1m ve 5m tek mumda da, gerçekçi tutma sürelerinde de maliyetin altında
  kalıyor; oraya emek harcamanın beklenen getirisi negatif.
* 15m, girişi ve stopu yeterince hassas yerleştirmeye izin verecek kadar
  ince, aynı zamanda 2–4 mumluk bir hedefle maliyeti rahat aşıyor.
* 1h zaten tek mumda bile eşiği geçiyor; SOLUSDT 1h en geniş marja sahip.
* SOLUSDT her periyotta BTCUSDT'den daha oynak, yani maliyet açısından daha
  elverişli. Bu onu daha kârlı yapmaz, yalnızca matematiksel olarak mümkün
  kılar; isabet oranını Faz 2 ölçecek.

Kapsam dışı bırakılması önerilenler: BTCUSDT 1m, BTCUSDT 5m, SOLUSDT 1m,
SOLUSDT 5m.

---

## 7. Faz 2'ye taşınan açık maddeler

1. Gerçek komisyon oranı Faz 4'te ölçülecek; tablo o zaman yenilenecek.
2. BNB ile komisyon ödemesi bir karar: BNB bakiyesi tutmayı gerektirir ve
   kendi fiyat riskini taşır. Varsayılan olarak **indirimsiz** (temkinli)
   hesaplamaya devam ediliyor.
3. ATR medyanı piyasanın sakin ve hareketli dönemlerini birlikte ölçüyor.
   Faz 2'de rejim ayrımı (düşük/yüksek oynaklık) yapılacak; sakin dönemlerde
   15m'in eşiğin altına düşmesi bekleniyor.
