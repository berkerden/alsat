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

### Teşhis turunu da çalıştırmak

`tarama` yerine `teshis` yazarsanız, normal taramanın ardından bir de
**maliyetsiz teşhis turu** çalışır. Bu tur komisyon, spread ve kaymayı sıfır
sayar ve tek bir soruyu ayırır: kâr çıkmamasının sebebi maliyetin ağırlığı
mı, yoksa ortada hiç yön bilgisi olmaması mı? Sonucu `faz2-teshis-sonuc.txt`
dosyasına yazar ve **işlem önerisi üretmez**.

```bash
cd ~/Desktop/alsat && git pull && bash kurulum.sh teshis
```

İki tarama art arda çalıştığı için normalin iki katı sürer. Ekrana ilerleme
yazar; sessiz kalmaz.

## 5. Arayüzü açmak

Tarama bittikten sonra sonuçları tarayıcıda görebilirsiniz. Terminal'de:

```bash
cd ~/Desktop/alsat && bash kurulum.sh arayuz
```

Betik kurulumu kontrol eder, testleri çalıştırır ve arayüzü açar. Tarayıcı
birkaç saniye içinde kendiliğinden açılır; açılmazsa terminalde yazan adresi
(`http://127.0.0.1:8756/`) kopyalayıp tarayıcının adres çubuğuna yapıştırın.

**Arayüzü kapatmak için** o terminal penceresinde `Control` tuşuna basılı
tutup `C` tuşuna basın. Pencereyi kapatmak da kapatır.

Arayüzde yedi sekme var:

| Sekme | Ne gösterir |
|---|---|
| Öneriler | Son kapanmış mumda tetiklenen kural varsa öneri kartı; yoksa neden olmadığı |
| Periyot sihirbazı | Hangi periyotta aramanın anlamlı olduğu, tek tabloda |
| Örüntü kütüphanesi | Kabul edilen kurallar ve (ayrı başlıkta) kabul edilmeyen adaylar |
| Ek araçlar | İzleme, maliyet/risk hesabı, disiplinli alım planı |
| Sinyal günlüğü | Önerilen ile gerçekleşen arasındaki fark |
| Kâğıt işlem | Parasız hesap, risk sınırları, elle kâğıt emir, sonuçlar, limitler |
| Demo işlem | Binance Demo Mode hesabı (sahte para): bağlantı, açık pozisyonlar ve borsadaki emirleri, elle Demo emri, sonuçlar |

### Arayüz hakkında bilmeniz gerekenler

- **Yalnızca sizin bilgisayarınızdan erişilebilir.** Adres `127.0.0.1` ile
  başlıyor; bu "bu bilgisayar" demektir. Aynı ağdaki başka bir cihaz açamaz.
- **Canlı hesabınıza emir göndermez.** Canlı hesaba emir gönderen kod yok.
  Kâğıt işlemler yalnızca bu bilgisayardaki deftere yazılır. Demo anahtarını
  kurduysanız Demo işlem sekmesi yalnızca Binance Demo Mode'a (sahte para)
  emir gönderir.
- **Binance'in herkese açık fiyat akışına bağlanır.** Kâğıt işlem canlı fiyatla
  çalışır. Bu akış için hesap ya da anahtar gerekmez.
- **API anahtarı istemez.** Salt okuma anahtarı (`bash kurulum.sh anahtar`)
  yalnızca komisyonu ölçer; Demo anahtarı (`bash kurulum.sh demo-anahtar`)
  yalnızca Demo Mode içindir. İkisi de isteğe bağlıdır.
- **Her açılışta "Sadece Öneri" modunda başlar.** Önceki açılışta kâğıt işlemde
  olan coin kendiliğinden kâğıt işleme dönmez; sekmede tekrar açmanız gerekir.

### "Önerilecek kural yok" yazıyorsa

Bu bir arıza değil. Faz 2 taraması bu kapsamda kabul edilebilecek bir örüntü
bulamadı; ekran bunun **nedenini** sayılarla yazıyor: kaç aday denendi, kabul
için gereken eşik neydi, en yakın aday eşikten kaç kat uzaktaydı. Uygulama
bulamadığı bir şeyi varmış gibi göstermiyor.

Kartın nasıl göründüğünü merak ediyorsanız "Kart şablonunu örnek kuralla
göster" düğmesine basın. O kart **uydurma bir kuralla** doldurulmuştur ve
üstünde öyle yazar; bir öneri değildir.

## 6. Kâğıt işlem

**Kâğıt işlem** sekmesinde, "Modlar" kutusunda coinin yanındaki listeden
**Kâğıt İşlem**'i seçin. O coinde parasız bir hesapla işlem açılabilir hâle
gelir. Para kullanılmaz; Binance'e hiçbir emir gitmez.

- **Elle emir:** "Elle kâğıt emir" kutusuna giriş, hedef ve stop fiyatını
  yazın. Önce **Kapıları sına**'ya basın: emir açılmadan, hangi risk kapısının
  neden açık ya da kapalı olduğu görünür. Uygunsa **Kâğıt emri aç**.
- **Uygulama açıldıktan sonraki ilk 5 dakika** yeni emir açılmaz; uygulama
  spread'in normal değerini ölçüyor. Bu bir arıza değil, ekranda yazar.
- **ACİL DURDUR** (sağ üstte, kırmızı): bütün coinleri Sadece Öneri'ye alır,
  bekleyen emirleri iptal eder.
- **Mac uykusu:** kâğıt işlemde coin varken uygulama Mac'in kendiliğinden
  uyumasını engeller. Kapak kapanırsa Mac yine uyur; uyandığında kaçırılan
  süre işlenir.
- **Sonuçlar:** "İşlem geçmişi" kutusundaki **CSV indir** düğmesi bütün
  kâğıt işlemleri Excel'de açılabilen bir dosyaya yazar.

## 7. Telegram bildirimleri (isteğe bağlı)

Telefonunuza bildirim gelmesi ve `/durum`, `/durdur` komutlarını
kullanabilmek için bir kez yapılır. Telegram uygulaması telefonunuzda kurulu
olmalı.

```bash
cd ~/Desktop/alsat && bash kurulum.sh telegram
```

Betik adımları ekrana tek tek yazar:

1. Telegram'da **@BotFather** hesabını açın (mavi tikli olan) ve `/newbot`
   yazın. Botunuza bir ad, sonra sonu `bot` ile biten bir kullanıcı adı verin.
2. BotFather size bir **jeton** verir (`1234567890:AAH...` gibi). Onu kopyalayıp
   Terminal'e yapıştırın. **Yapıştırdığınızda ekranda hiçbir şey görünmez**;
   bu normaldir, gizlilik içindir. `Enter`'a basın.
3. Betik botunuzun adresini yazar. Telegram'da botu açıp **Başlat**'a basın.
4. Betik "Bu siz misiniz?" diye sorar; `e` yazıp `Enter`.
5. Telefonunuza bir deneme mesajı gelir.

Jeton bir şifredir: kimseyle paylaşmayın, Claude dahil hiçbir sohbete
yapıştırmayın. Mac'in Anahtar Zinciri'nde saklanır.

Arayüz açıksa kapatıp (`Control-C`) yeniden açın:
`bash kurulum.sh arayuz`.

## 8. Komisyonu ölçmek (isteğe bağlı, API anahtarı gerekir)

Uygulama hesabınıza özel komisyon oranını bilmiyorsa Binance'in genel oranını
(%0.1) varsayar ve ekranda "varsayım" diye yazar. Gerçek oranı ölçmek için:

```bash
cd ~/Desktop/alsat && bash kurulum.sh anahtar
```

Betik Mac'inizde bir anahtar çifti üretir ve ekrana bir **genel anahtar** metni
yazar (`BEGIN` ile başlayıp `END` ile biten). Sonra:

1. Tarayıcıda binance.com → profil simgesi → **Hesap → API Yönetimi → API
   Oluştur**.
2. **Kendi ürettiğim** (Self-generated) seçeneğini seçin, ekrandaki metni
   `BEGIN` ve `END` satırları dahil yapıştırın.
3. Anahtar oluşunca **yalnızca "Okumayı etkinleştir"** açık kalsın. Para
   çekme, Spot işlem, Margin ve Vadeli işlem kapalı olsun.
4. Binance'in gösterdiği **API Key**'i kopyalayıp Terminal'e yapıştırın.

Betik anahtarın izinlerini okur; para çekme izni açıksa anahtarı kullanmayı
reddeder. Sonra komisyon oranlarınızı okuyup kaydeder. Özel anahtar Mac'inizden
hiç çıkmaz; Binance'e yalnızca genel yarısı verilir.

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
