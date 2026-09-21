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

Betik dört adımı ekrana yazar. Her adımın başında yeşil bir `✓` görmelisiniz:

```
1/4  Python sürümü aranıyor
✓ Python 3.13.x bulundu

2/4  Sanal ortam hazırlanıyor
✓ Sanal ortam hazır (.venv)

3/4  Bağımlılıklar kuruluyor
✓ Bağımlılıklar kuruldu
✓ Testler geçti

4/4  Fizibilite taraması
```

Dördüncü adımda 180 günlük mum verisi indirilir; ilk seferde birkaç dakika sürebilir.
Sonraki çalıştırmalarda veri önbellekten okunur ve çok daha hızlıdır.

## 4. Sonucu paylaşın

Betik sonucu ekrana basar ve ayrıca `fizibilite-sonuc.txt` dosyasına yazar.
Dosyayı açmak için:

```bash
open fizibilite-sonuc.txt
```

İçeriği kopyalayıp Claude ile paylaşın.

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
