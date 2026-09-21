# Binance Al-Sat Uygulaması

Binance Global **Spot** piyasasında örüntü analizi yapan, al-sat önerisi üreten ve
istendiğinde kullanıcı kontrolünde otomatik işlem açabilen Türkçe uygulama.

> **Bu bir yatırım tavsiyesi değildir.** Öneriler geçmiş verilere dayalı istatistiksel
> çıkarımlardır; geçmiş performans geleceği garanti etmez. Uygulama kâr vaat etmez ve
> bir örüntünün komisyon sonrası gerçekten avantaj sağlayıp sağlamadığını dürüstçe
> gösterir. "İşlem yapmaya değer avantaj bulunamadı" sonucu da geçerli bir çıktıdır.

Kapsam ve gereksinimler: [`docs/SPEC.md`](docs/SPEC.md)
Mimari, API bulguları ve risk listesi: [`docs/FAZ0-MIMARI.md`](docs/FAZ0-MIMARI.md)

---

## Durum

| Faz | Kapsam | Durum |
|---|---|---|
| 0 | Doküman incelemesi, mimari, kütüphane seçimi | ✅ Onaylandı |
| 1 | Veri katmanı + **fizibilite taraması** | 🔨 Devam ediyor |
| 2 | Örüntü keşif + backtest motoru | ⏳ |
| 3 | Periyot sihirbazı + öneri motoru + arayüz | ⏳ |
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
  research/    fizibilite taraması, (Faz 2) olay çalışması ve istatistik
  cli/         komut satırı araçları
tests/         birim testleri
docs/          SPEC.md ve faz dokümanları
config/        varsayılan yapılandırma
```
