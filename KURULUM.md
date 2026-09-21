# Mac'te kurulum ve ilk çalıştırma

Terminal deneyimi gerektirmez. Aşağıdaki adımları sırayla izleyin.

## 1. Terminal'i açın

`Cmd + Boşluk` tuşlarına basın, açılan arama kutusuna **Terminal** yazın ve `Enter`.
Siyah (ya da beyaz) bir pencere açılır. Komutlar buraya yazılır.

## 2. Tek komutu yapıştırın

Aşağıdaki satırı kopyalayıp Terminal penceresine yapıştırın ve `Enter`'a basın:

```bash
cd ~/Desktop && git clone https://github.com/berkerden/alsat && cd alsat && bash kurulum.sh
```

Bu komut sırasıyla şunları yapar:

1. Masaüstüne geçer
2. Kodu GitHub'dan `alsat` adlı bir klasöre indirir
3. O klasöre girer
4. Kurulum betiğini başlatır

### İlk çalıştırmada çıkabilecek iki durum

**"command not found: git" veya bir pencere açılıp geliştirici araçlarını sorarsa:**
Açılan pencerede **Yükle** (Install) düğmesine basın, bitmesini bekleyin, sonra aynı
komutu tekrar yapıştırın. Bu, Apple'ın kendi geliştirici araçlarıdır.

**Betik "Uygun Python bulunamadı" derse:**
https://www.python.org/downloads/macos/ adresinden en güncel sürümün `.pkg`
dosyasını indirip çift tıklayın. Kurulum bitince Terminal'i **kapatıp yeniden açın**
ve şunu yazın:

```bash
cd ~/Desktop/alsat && bash kurulum.sh
```

## 3. Betiğin bitmesini bekleyin

Betik beş adımı ekrana yazar. Her adımın başında yeşil bir `✓` görmelisiniz:

```
1/5  Python sürümü aranıyor
✓ Python 3.13.x bulundu

2/5  Sanal ortam hazırlanıyor
✓ Sanal ortam hazır (.venv)

3/5  Bağımlılıklar kuruluyor
✓ Bağımlılıklar kuruldu
✓ Testler geçti

4/5  Veri tazeleme ve fizibilite taraması

5/5  Örüntü keşfi ve backtest
```

Dördüncü adımda mum verisi kontrol edilir. Daha önce indirilmiş dosyalar **tekrar
indirilmez**; yalnızca son mumlar borsadan tamamlanır. İlk çalıştırmada bu adım
birkaç dakika sürer.

Beşinci adım internete hiç çıkmaz; diskteki veriyi okuyup örüntü taramasını
yapar. Ekrana hangi sembolde, hangi periyotta olduğunu yazar.

## 4. Sonuçları paylaşın

Betik iki dosya üretir:

| Dosya | Ne var içinde |
|---|---|
| `fizibilite-sonuc.txt` | Hangi coin ve periyot matematiksel olarak anlamlı (Faz 1) |
| `faz2-oruntu-sonuc.txt` | Örüntü keşfi, istatistik ve backtest raporu (Faz 2) |

Açmak için:

```bash
open faz2-oruntu-sonuc.txt
```

İçeriği kopyalayıp Claude ile paylaşın.

### Yalnızca örüntü taramasını çalıştırmak

Veri zaten indirilmişse ve yalnızca taramayı tekrar çalıştırmak istiyorsanız,
komutun sonuna `tarama` ekleyin. Bu, internete çıkan adımı tamamen atlar:

```bash
cd ~/Desktop/alsat && bash kurulum.sh tarama
```

## "Güvenli bağlantı kurulamadı" / sertifika hatası alırsanız

Hata metninde `CERTIFICATE_VERIFY_FAILED` geçiyorsa, bilgisayarınızdaki Python
sunucunun sertifikasını doğrulayamıyor demektir. Sebebini bulmak için:

```bash
cd ~/Desktop/alsat && source .venv/bin/activate
python -m albsat.cli.tlsteshis
```

Araç iki olasılığı ayırt eder ve hangisi olduğunu size söyler:

- **Python'ın kök sertifika listesi kurulmamış.** Uygulamalar klasöründeki
  "Python 3.x" klasörünü açıp `Install Certificates.command` dosyasına bir kez
  çift tıklayın.
- **Trafiğinizi açıp yeniden imzalayan bir katman var** (kurumsal ağ, VPN veya
  HTTPS taraması yapan antivirüs). Uygulama artık macOS'un kendi sertifika
  deposunu kullandığı için çoğu durumda bu kendiliğinden çözülür. Çözülmezse o
  ürünün HTTPS tarama özelliğini kapatmayı ya da başka bir ağa geçmeyi deneyin.

> Sertifika doğrulamasını kapatan hiçbir çözümü uygulamayın. İnternette sık
> önerilir ama bu uygulama ileride emir gönderecek; doğrulamasız bir bağlantıda
> araya giren biri fiyatları ve emirleri değiştirebilir.

## Sonradan tekrar çalıştırmak

```bash
cd ~/Desktop/alsat && bash kurulum.sh
```

## Kodu güncellemek

Claude yeni kod yazdığında, en son sürümü almak için:

```bash
cd ~/Desktop/alsat && git pull && bash kurulum.sh
```

`git pull` GitHub'daki güncellemeleri bilgisayarınıza indirir. Bu adım otomatik
değildir; kod kendiliğinden inmez.
