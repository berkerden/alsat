# Binance Al-Sat Uygulaması

Binance Global **Spot** piyasasında örüntü analizi yapan, al-sat önerisi üreten ve
istendiğinde kullanıcı kontrolünde otomatik işlem açabilen Türkçe uygulama.

> **Bu bir yatırım tavsiyesi değildir.** Öneriler geçmiş verilere dayalı istatistiksel
> çıkarımlardır; geçmiş performans geleceği garanti etmez. Uygulama kâr vaat etmez ve
> bir örüntünün komisyon sonrası gerçekten avantaj sağlayıp sağlamadığını dürüstçe
> gösterir. "İşlem yapmaya değer avantaj bulunamadı" sonucu da geçerli bir çıktıdır.

Kapsam ve gereksinimler: [`docs/SPEC.md`](docs/SPEC.md)
Mimari, API bulguları ve risk listesi: [`docs/FAZ0-MIMARI.md`](docs/FAZ0-MIMARI.md)
Fizibilite sonucu: [`docs/FAZ1-FIZIBILITE.md`](docs/FAZ1-FIZIBILITE.md)
Örüntü motorunun yöntemi: [`docs/FAZ2-ORUNTU-MOTORU.md`](docs/FAZ2-ORUNTU-MOTORU.md)
Örüntü aramasının sonucu: [`docs/FAZ2-SONUC.md`](docs/FAZ2-SONUC.md)
Öneri motoru ve arayüzün yöntemi: [`docs/FAZ3-ONERI-MOTORU.md`](docs/FAZ3-ONERI-MOTORU.md)
Fazlar arası devir notu: [`docs/DEVIR-NOTU.md`](docs/DEVIR-NOTU.md)

---

## Durum

| Faz | Kapsam | Durum |
|---|---|---|
| 0 | Doküman incelemesi, mimari, kütüphane seçimi | ✅ Onaylandı |
| 1 | Veri katmanı + **fizibilite taraması** | ✅ Onaylandı |
| 2 | Örüntü keşif + backtest motoru | ✅ Onaylandı |
| 3 | Periyot sihirbazı + öneri motoru + arayüz | 🔍 Onay bekliyor |
| 4 | Risk motoru + kâğıt işlem + Telegram | ⏳ |
| 5 | Emir yürütme (**Demo Mode**) | ⏳ |
| 6 | Canlı yarı otomatik → tam otomatik | ⏳ |
| 7 | VPS dağıtımı | ⏳ |

Her fazın sonunda çalışma durur ve onay beklenir (SPEC.md §10).

Şu an hazır olanlar:

- `Decimal` tabanlı fiyat/miktar yuvarlama ve `exchangeInfo` filtre doğrulaması
- Dört bileşenli komisyon modeli (standart + vergi + özel + BNB indirimi)
- Başa-baş fiyatı ve gidiş-dönüş maliyet hesabı (bacak tipine duyarlı)
- Arşiv (`data.binance.vision`) indirme + SHA256 doğrulama + REST ile boşluk doldurma
- Mum kalite raporu (eksik mum, tekrar, boşluk, sıfır hacim, geçersiz OHLC)
- **Fizibilite taraması** ve komut satırı aracı
- **Özellik katmanı:** mum formasyonları, indikatörler, hacim, rejim, seviye,
  zaman etkileri ve BTC etkisi (56 boole özellik, 7 aile)
- **Olay çalışması:** hedef/stop simülasyonu, MFE/MAE, komisyon sonrası beklenen değer
- **İstatistik:** bootstrap güven aralığı, Benjamini–Hochberg düzeltmesi,
  deflated Sharpe, walk-forward, çeyreklik kararlılık
- **Backtest motoru** ve kıyas ölçütleri (rastgele giriş, al-ve-tut)
- **Look-ahead ve repaint testleri** (`tests/test_lookahead.py`)
- **Örüntü tarama aracı** (`albsat-oruntu`): keşif eğitim döneminde, kabul kararı
  yalnızca ayrılmış dönemden, çoklu test düzeltmesi koşunun tamamı üzerinden
- **Maliyetsiz teşhis turu** (`albsat-oruntu --maliyetsiz`): maliyet sıfır
  sayılarak "ortada yön bilgisi var mı" sorusunu ölçer
- **Kural deposu** (`veri/kurallar.json`): taramanın kabul ettiği kurallar,
  kabul etmediği adaylar ve bölüm özetleri; Faz 2 ile Faz 3 arasındaki köprü
- **Öneri motoru ve kartı:** giriş/hedef/stop, başa-baş, komisyon sonrası
  marj, pozisyon büyüklüğü, güven skoru dökümü, geçerlilik süresi
- **Pozisyon büyüklüğü:** bütçe mi risk kuralı mı bağlayıcı, stepSize
  yuvarlamasının kaybı, komisyon dahil stop zararı
- **Periyot sihirbazı:** ATR%/maliyet oranı, sinyal sıklığı, al-tut kıyası
- **Sinyal günlüğü:** önerilen ile gerçekleşen arasındaki fark
- **Ek araçlar:** izleme, maliyet/risk hesabı, disiplinli alım planı
- **Yerel arayüz** (`albsat-arayuz`): yalnızca 127.0.0.1, Sadece Öneri modu,
  emir göndermez, API anahtarı kullanmaz

Faz 2'nin ölçüm sonucu: bu kapsamda (BTCUSDT + SOLUSDT, 15m + 1h, 2-4 mumluk
pencereler) çoklu test düzeltmesinden geçen örüntü yok; maliyet tamamen
kaldırıldığında da yok. Ayrıntı [`docs/FAZ2-SONUC.md`](docs/FAZ2-SONUC.md).

---

## Kurulum (macOS)

Python 3.12 veya üstü gerekir.

```bash
git clone <depo-adresi> binance-alsat
cd binance-alsat

python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Testleri çalıştırın:

```bash
pytest -q
```

---

## Fizibilite taraması

Örüntü motorunu kurmadan önce cevaplanması gereken soru şudur:
**seçtiğim coin ve periyotta tipik fiyat hareketi, gidiş-dönüş maliyeti karşılıyor mu?**

```bash
python -m albsat.cli.feasibility \
  --semboller BTCUSDT SOLUSDT \
  --periyotlar 1m 5m 15m 1h \
  --gun 180
```

Araç sembol filtrelerini çeker, geçmiş mumları indirip doğrular, kalite raporunu
basar ve şu tabloyu üretir:

```
Sembol       Periyot       Mum  ATR% ort  Min hedef%   Oran   Gün/mum  Sonuç
----------------------------------------------------------------------------
BTCUSDT           1m   259,200    0.0639      0.2300   0.28     1,440  UYGUN DEĞİL
BTCUSDT           5m    51,840    0.1416      0.2300   0.62       288  UYGUN DEĞİL
BTCUSDT          15m    17,280    0.2447      0.2300   1.06        96  SINIRDA
BTCUSDT           1h     4,320    0.4862      0.2300   2.11        24  UYGUN
```

**Oran**, tipik mum hareketinin maliyeti karşılayan en küçük hedefe bölümüdür:

- `< 1.0` → tipik hareket maliyeti bile karşılamıyor; o periyotta işlem zararına.
- `1.0 – 2.0` → sınırda; ancak çok isabetli bir sinyalle anlamlı olur.
- `≥ 2.0` → örüntü aramaya elverişli.

Yukarıdaki tablo **örnek çıktıdır**; gerçek sayılar veriyi indirdiğinizde oluşur.

Komisyon oranı verilmezse genel listelenen oranlar varsayılır. Hesabınızın gerçek
oranları imzalı `GET /api/v3/account/commission` çağrısını gerektirir ve Faz 4'te
API anahtarıyla birlikte devreye girer.

---

## Örüntü keşfi ve backtest (Faz 2)

Fizibilite taraması "bu periyotta işlem matematiksel olarak mümkün mü" sorusunu
cevaplar. Örüntü taraması bir sonraki soruyu sorar: **ölçülebilir bir avantaj
var mı?**

```bash
python -m albsat.cli.research
```

Bu komut **internete çıkmaz**; veriyi diskteki `./veri` klasöründen okur.
Varsayılan kapsam Faz 1 bulgusundan gelir: BTCUSDT ve SOLUSDT, yalnızca 15m ve
1h, hedefler 2–4 mumluk pencerelerde.

Tarama sırasıyla şunları yapar:

1. 56 boole özellik hesaplar (yalnızca kapanmış mumlardan).
2. Her mum için "burada girseydik ne olurdu" tablosunu çıkarır: giriş **bir
   sonraki mumun açılışı**, hedef ve stop ATR'nin katı, çıkış maliyet sonrası.
3. Tek özellikleri ve ikili kesişimlerini aday olarak tarar (~500–1.600 aday).
4. Umut verenlere bootstrap güven aralığı, rastgele giriş kıyası, çeyreklik
   kararlılık ve walk-forward uygular.
5. Benjamini–Hochberg düzeltmesini **denenen tüm adaylar** üzerinden yapar.
6. Kabul edilen en güvenilir örüntüyü backtest eder ve rastgele girişle
   kıyaslar.

Sonuç `faz2-oruntu-sonuc.txt` dosyasına yazılır.

**"Kabul edilen örüntü yok" geçerli ve beklenen bir sonuçtur.** Yüzlerce aday
denendiğinde bazılarının şans eseri iyi görünmesi kaçınılmazdır; düzeltmenin
işi tam olarak bunları elemektir.

Yöntemin ayrıntısı ve neden böyle kurulduğu:
[`docs/FAZ2-ORUNTU-MOTORU.md`](docs/FAZ2-ORUNTU-MOTORU.md)

---

## Arayüz (Faz 3)

```bash
python -m albsat.cli.serve
```

Sunucu **yalnızca 127.0.0.1**'e bağlanır (SPEC §5) ve tarayıcıyı kendiliğinden
açar. İnternete çıkmaz, API anahtarı kullanmaz, **emir göndermez**: uygulama
"Sadece Öneri" modundadır ve emir gönderen bir uç bulunmamaktadır.

Beş sekme: Öneriler, Periyot sihirbazı, Örüntü kütüphanesi, Ek araçlar,
Sinyal günlüğü.

Kabul edilmiş kural olmadığı için Öneriler sekmesi **"önerilecek kural yok"**
diyor ve nedenini sayılarla yazıyor: kaç aday denendi, kabul eşiği neydi, en
yakın aday eşikten kaç kat uzaktaydı, aynı dönemde her muma girilseydi ne
olurdu. Bu bir arıza değil, Faz 2'nin ölçtüğü sonuçtur.

Kartın hangi alanları dolduracağını görmek için "Kart şablonunu örnek kuralla
göster" düğmesi var. O kart **açıkça uydurma** bir kuralla doldurulur ve
üstünde öyle yazar; bir öneri değildir.

Terminale alışık olmayan kullanım için:

```bash
bash kurulum.sh arayuz
```

Yöntemin ayrıntısı: [`docs/FAZ3-ONERI-MOTORU.md`](docs/FAZ3-ONERI-MOTORU.md)

---

## API anahtarı oluşturma

Uygulama Faz 4'e kadar **hiçbir API anahtarına ihtiyaç duymaz**; şu ana kadar yalnızca
herkese açık veriyi okur. Anahtar gerektiğinde şu adımlar izlenir:

1. Binance → **Hesap → API Yönetimi → API Oluştur**.
2. Anahtar tipi olarak **Ed25519** seçin.
   Bu zorunludur: WebSocket API oturumu (`session.logon`) ve User Data Stream aboneliği
   (`userDataStream.subscribe`) yalnızca Ed25519 anahtarlarını destekler.
3. İzinlerde **yalnızca** şunlar açık olsun:
   - ✅ Okuma yetkisini etkinleştir
   - ✅ Spot ve Marjin İşlem yetkisini etkinleştir *(marjin kullanılmaz; Binance bu izni
     tek kalem olarak verir)*
   - ❌ **Para çekme yetkisi kapalı olmalı.** Uygulama açılışta bunu kontrol eder ve
     açıksa çalışmayı reddeder.
   - ❌ Vadeli işlemler (futures) kapalı
4. Mümkünse bot için **ayrı bir alt hesap (sub-account)** kullanın.
5. **IP kısıtlaması:** VPS'te statik IP ile zorunludur. Mac'te ev IP'si değişebileceği
   için kısıtlama kullanmak zordur; bu durumda bütçe sınırını yazılım uygular ve riski
   siz kabul etmiş olursunuz.
6. Özel anahtar macOS'ta **Keychain**'de saklanır. `.env` dosyasına, koda, loglara veya
   arayüze asla yazılmaz.

Anahtarı hiç kimseyle, bu proje üzerinde çalışan hiçbir araçla paylaşmayın.

---

## Güvenlik

- `.env*`, `*.pem`, `*.key` ve anahtar içerebilecek dosya adları `.gitignore`'dadır.
- Arayüz yalnızca `127.0.0.1` üzerinden dinler.
- Margin, futures ve borçlanma uç noktaları hiçbir koşulda çağrılmaz.
- Emir gönderimi için Binance MCP sunucusu kullanılmaz; doğrudan REST + WebSocket API.

---

## Proje yapısı

```
src/albsat/
  core/        Decimal aritmetiği, filtreler, komisyon, maliyet, denetim
  exchange/    uç noktalar, HTTP, (Faz 5) imzalı REST + WebSocket API
  data/        arşiv indirme, REST boşluk doldurma, Parquet saklama, kalite
  features/    mum formasyonları, indikatörler, hacim, rejim, zaman, BTC etkisi
  research/    fizibilite, olay çalışması, istatistik, walk-forward, tarama, rapor
  backtest/    olay tabanlı motor ve kıyas ölçütleri
  risk/        pozisyon büyüklüğü (bütçe/risk sınırı, stepSize kaybı)
  strategy/    kural deposu, öneri kartı, öneri motoru, sihirbaz, günlük,
               izleme, maliyet/risk paneli, disiplinli alım planı
  api/         yerel arayüzün uçları ve statik sayfası (HTML/CSS/JS)
  cli/         komut satırı araçları
tests/         birim testleri
docs/          SPEC.md ve faz dokümanları
config/        varsayılan yapılandırma
```
