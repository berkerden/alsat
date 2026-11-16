# Fable 5.2 çalışma alanı — devir notu

16 Kasım 2026. Bu not, **yeni açılan projede sıfırdan başlayan Claude** için
yazıldı. Yeni projenin hafızası boş, eski projenin konuşmaları ve dosyaları
orada yok. Bilmen gereken her şey bu notta ve bu notun gösterdiği belgelerde.

Okuma sırası: önce bu not, sonra `docs/DEVIR-NOTU.md` (sekiz başlıklı ayrıntılı
devir notu), sonra üzerinde çalışacağın konunun faz belgesi.

**İlk işin:** bu nottaki kalıcı bilgileri (§2 ile §9 arası) yeni
projenin hafızasına kaydet. Sonra Berk'e durumu kısaca özetle ve ne yapmak
istediğini sor. Berk söylemeden kod değiştirme.

## 0. Bu dal nedir, kuralları ne

* Berk 16 Kasım 2026'da şunu istedi: *"Bu projenin tamamını Fable 5.2 ile
  yapılacak şekilde bir yedek alıp oradan devam eder misin, bu kalsın orası
  fable 5.2 ile denenecek durumlar için ayrı bir kanal olacak"*.
* `fable-5-2` dalı, `main` dalının o günkü hâlinden (`0d34c95`) ayrıldı. İlk
  anda tek farkı bu dosyadır.
* **Asıl proje ve `main` olduğu gibi duruyor.** Berk'in Mac'indeki kurulum
  (`~/Desktop/alsat`) `main`'i izliyor ve gerçek hesabına bağlı.
* **Bu projede çalışma `fable-5-2` dalında yürür.** Her işe bu daldan başla,
  değişiklikleri doğrudan bu dala gönder (Berk'in düzeni baştan beri doğrudan
  gönderimdir; "PR" ona yabancı bir terim).
* **`main`'e hiçbir şey gönderme, `main`'e birleştirme ya da pull request
  açma.** Buradaki bir denemenin asıl projeye alınması ayrı bir karardır ve
  Berk açıkça isteyince yapılır.
* Berk bu kurallardan farklı bir şey söylerse onun dediği geçerlidir.
* `main`'de sonradan olan değişiklikler bu dala kendiliğinden gelmez. Berk
  isterse `main` bu dala birleştirilir; tersi yapılmaz.

**Yeni projede olmayanlar:**

* **Proje hafızası.** Eski projenin hafıza notlarının özeti bu dosyadır.
* **Eski projenin dosyaları.** Berk'e verilmiş Mac adım dosyaları
  (`FAZ5-`, `FAZ6-`, `FAZ7-`, `TUR2-`, `TREND-` ve
  `TELEGRAM-UYKU-MAC-ADIMLARI.md`) ile ham çıktılar (`faz2-teshis-sonuc.txt`,
  `tur2-oruntu-sonuc.txt`, `tur2-veri-sonuc.txt`, `trend-sonuc.txt`) eski
  projenin Library bölümünde duruyor. İşlenmiş sonuçların hepsi depoda
  (`docs/*-SONUC.md`). Bir adım dosyası gerekirse Berk'ten yeni projeye
  eklemesini iste ya da koddaki metinlerle karşılaştırarak yeniden yaz.
* **Eski konuşmalar.** Berk'in önemli cümleleri `docs/DEVIR-NOTU.md` §3'te
  ve aşağıda §6'da.

## 1. Proje bir bakışta

Binance Global **Spot** piyasasında örüntü analizi yapan, al-sat önerisi
üreten ve kullanıcı kontrolünde emir gönderebilen Türkçe bir uygulama. Python
3.12+, yerel arayüz (yalnızca 127.0.0.1), derleme adımı olmayan düz
HTML/CSS/JS. Paket adı `albsat`. Şartname `docs/SPEC.md`.

Şartnamedeki bütün fazlar (0-7) yazıldı, sınandı ve Berk tarafından
onaylandı. Ana dalda 799 test geçiyor.

**En önemli gerçek:** ölçümler, bu kapsamda parayla kullanılabilecek bir giriş
kuralı olmadığını gösterdi. Uygulama bugün izler, hesaplar ve dürüstçe
"önerilecek kural yok" der. Bu bir arıza değil, ölçülmüş sonuçtur. Kâr vaat
etme, kural uydurma.

## 2. Berk nasıl çalışıyor

* Türkçe yazıyor; yanıtlar Türkçe.
* Mac kullanıyor (Retina ekran). Terminal, git ve GitHub akışları ona tanıdık
  değil. Kod Mac'inde `~/Desktop/alsat` altında.
* Bilgisayarında yapması gereken adımlar **tek tek, sade, elinden tutarak**
  anlatılır: hangi tuşa basacağı, ekranda ne göreceği, neyi kopyalayacağı.
  Çıplak komut listesi verilmez. Komut her zaman tam satır olarak verilir.
  Uzun adım listesi ayrı bir dosya olarak verilir.
* **Terminalde soru soran ya da sessiz kalan adım ona donmuş gibi görünür.**
  Uzun süren her adım ekrana ilerleme yazmalı; göreceği soruyu ve gizli
  girişte ekranda hiçbir şey görünmeyeceğini önceden söyle.
* Bilmediği terimi ve izin isteklerini kısa soruyla sorar ("PR ne", "bu ne
  için izin"). Terim kullanma, kullanırsan açıkla. İzin isteyen bir aracı
  kullanmadan önce ne için olduğunu bir cümleyle söyle.
* Bir adımın gerekçesini sorar. Onay istemeden önce neden gerektiğini ve
  atlanırsa ne olacağını yaz.
* Kısa ve net soruyu sever. Seçenekli soruda bir öneri işaretlenince hızlı
  karar verir. Bazı kartları yazarak cevaplar, bazılarını hiç cevaplamaz;
  cevapsız kalan soruyu gerektiği anda yeniden sor.
* Sonucu kısa bildirir. Kanıt gereken bir şeyi ayrıca ve tek başına iste.
* **İstenen yazılı onayı beklemeden adımları kendisi çalıştırabiliyor.**
  Bu yüzden onay, adım dosyası verilmeden **önce** alınır.
* **Her büyük iş ayrı bir konuda yürür.** Berk'in kuralı (21 Eylül 2026):
  *"Yeni bir oturum ile yeni fazlara geçiş yap. Bağlam şişip kalite düşmesin
  bu sayede."* Ve: *"Her oturumda öğrendiğimiz, dikkat ettiğimiz, sonraki
  faza aktarılması gerekecek bilgileri toparla ve sonraki faz için sonraki
  oturuma taşı, eksik bilgi olmadan devam etmiş olsun."* İş bitince
  `docs/DEVIR-NOTU.md` aynı sekiz başlıkla baştan yazılır ve README'nin durum
  tablosu güncellenir.

## 3. Fazların durumu

| Faz | Kapsam | Durum | Belge |
|---|---|---|---|
| 0 | Doküman incelemesi, mimari | Onaylandı (21 Eylül 2026) | `FAZ0-MIMARI.md` |
| 1 | Veri katmanı, fizibilite taraması | Onaylandı (21 Eylül 2026) | `FAZ1-FIZIBILITE.md` |
| 2 | Örüntü keşif ve backtest motoru | Onaylandı (22 Eylül 2026) | `FAZ2-ORUNTU-MOTORU.md`, `FAZ2-SONUC.md` |
| 3 | Öneri motoru, periyot sihirbazı, arayüz, ek araçlar | Onaylandı (22 Eylül 2026) | `FAZ3-ONERI-MOTORU.md` |
| 4 | Risk motoru, kâğıt işlem, Telegram | Onaylandı (23 Eylül 2026) | `FAZ4-RISK-KAGIT-TELEGRAM.md` |
| 5 | Demo Mode'da emir yürütme | Onaylandı (23 Eylül 2026) | `FAZ5-DEMO-EMIR-YURUTME.md` |
| 6 | Canlı Yarı Otomatik, canlıya geçiş kapısı | Onaylandı (23 Eylül 2026); Tam Otomatik kapıda kilitli | `FAZ6-CANLI.md` |
| 7 | Sunucu hazırlığı, gözcü, yedek, alarm | Onaylandı (27 Eylül 2026); sunucu kiralanmadı | `FAZ7-SUNUCU.md` |

Şartnamede Faz 8 yok. Sonraki işi Berk söyler.

**Fazlardan sonra yapılanlar:**

* **İkinci kural arama turu** (28 Eylül 2026, Berk'in Mac'i): 730 gün, Faz
  2'nin 6.372 adayı da sayılarak toplam 12.021 deneme. Kabul edilen örüntü
  yok; en yakın aday eşiğin 320 katı uzağında. Ön kayda göre bu arama
  **kapandı**. `TUR2-ONKAYIT.md`, `TUR2-SONUC.md`.
* **Günlük trend testi** (19 Ekim 2026, Berk'in Mac'i): beş klasik trend
  kuralı, günlük grafik, BTC ve SOL, al-ve-tut kıyası, 2018'in on büyük coini
  kontrol. On sınamadan biri geçti: SOLUSDT 20/10 gün kırılımı (q 0,094,
  kontrol 6/10), kıl payı. Ön kayıt §8: **parayla kullanılmaz.**
  `TREND-ONKAYIT.md`, `TREND-SONUC.md`.
* **SOL trend göstergesi** (19 Ekim 2026): Berk isteyince Öneriler sekmesinin
  üstüne yalnızca bilgi veren bir kutu eklendi. Emir göndermez. Kâğıt işlem
  ve canlı kullanım ayrı onay ister.
* **Telegram ve uyku engeli** (20 Ekim 2026): sınamadan önce iki kusur
  düzeltildi (uykudan uyanınca bildirim kayboluyordu; açılışta ağ yoksa
  kapalıyken yazılmış eski bir komut çalışabiliyordu), `uyku-sina` ve
  `telegram-dene` komutları eklendi. **Berk'in Mac'indeki sınama sonucu
  gelmedi.** `pmset` çıktısının satır biçimi gerçek Mac'te hiç görülmedi;
  `uyku-sina` ✗ verirse önce `caffeinate_holds` içindeki düzenli ifadeyi
  gerçek çıktıyla karşılaştır.

* **Kod gözden geçirmesi** (16 Kasım 2026, yalnızca bu dalda): Berk "sıfırdan
  yapsan neyi farklı yapardın" diye sordu. Araştırma, canlı döngü, veri, para
  ve maliyet hesabı, arayüz sunucusu baştan sona; emir yürütücünün ilk üçte
  biri okundu. Para ve istatistik yolunda hata bulunmadı. Davranışı
  değiştirmeyen üç düzeltme yapıldı (`tests/test_iyilestirmeler.py`): mum
  deposu son okunan dosyaları bellekte tutuyor (anahtar inode + değişim
  zamanı + boyut; arayüzün 5 saniyelik yoklaması 26 ms'den 6,5 ms'ye indi),
  sinyal günlüğü `core.db.session` kullanıyor (bağlantılar kapanıyor),
  bütçesiz komut satırı istekleri 429'da `Retry-After` kadar bekliyor (120
  sn'den uzunsa beklemeden duruyor). 808 test.
* **Sabit sürümlü kurulum ve otomatik test** (16 Kasım 2026, yalnızca bu
  dalda; Berk: *"bunları yapmak mantıklı ve efektif ise yap"*):
  `kurulum.sh` Mac'te bağımlılıkları önce `requirements-dev.lock`'tan
  özetleriyle kurar (`--require-hashes --only-binary :all:`), sonra paketi
  `--no-deps -e .` ile ekler. Liste bilgisayara uymazsa (hazır paketi olmayan
  işlemci ya da Python) uyarıp eski yola (`-e ".[dev]"`) döner; **özet
  tutmazsa durur**, eski yola dönmez. Liste Apple işlemcili Mac'te Python
  3.12-3.14 için çözülüyor; Intel Mac'te `cryptography`'nin hazır paketi yok,
  orada eski yol çalışır (Berk'in Mac'inin işlemcisi bilinmiyor).
  `.github/workflows/test.yml` her gönderimde (`main`, `fable-5-2`)
  `kurulum.sh`'yi Linux/3.12 ve macOS/3.14'te baştan çalıştırır; sır
  kullanmaz. Kilit dosyaları `uv` ile üretilmiş; bağımlılık değişirse ikisi
  de yeniden üretilmeli, yoksa otomatik test "sabit sürümler" satırını
  bulamayıp kırmızı olur.
* **Mac'te `main` çalıştığı için bu dalın hiçbir değişikliği Berk'in
  kurulumunda yok.** `main`'e alınması ayrı karar.

## 4. Ölçülmüş sonuçlar ve yeniden açılmayacak sorular

* **Küçük ve sık hareket hedefi çürüdü** (Faz 1). Maliyet eşiği %0,28
  (komisyon %0,20 + spread + kayma + güvenlik payı). 1m ve 5m hiçbir
  senaryoda kârlı değil; 15m sınırda, 1h uygun.
* **Kârlılığın koşulu tutma süresi değil yön bilgisi** (Faz 2). Maliyet işlem
  başına sabittir; uzun tutmak onu seyreltmez.
* **Maliyet sıfır sayılınca da kabul edilen örüntü yok** (maliyetsiz teşhis
  turu). Maliyeti düşürmek (BNB indirimi, maker giriş) bir yol açmıyor;
  ölçüldü.
* **Ölçülen her bölümde en iyi sonucu al-ve-tut verdi.** Yatırım tavsiyesi
  değil, o veri kümesindeki ölçüm.
* **Aynı arama yeniden önerilmez.** Başka parametre, başka coin, başka
  periyot ya da daha çok veriyle "bir daha deneyelim" demek, motorun
  engellemek için yazıldığı çoklu test hatasının kendisidir. Yeni bir tarama
  önerilirse birikimli aday sayısı (12.021) düzeltmeye girer. Trend testi
  için de aynı kural geçerli.
* Asimetriye dikkat: "kaçınılacaklar" listesinde dikkat çeken adayların
  olması piyasa bulgusu değildir; iki testin çıtası farklıdır
  (`FAZ2-SONUC.md`).
* Komisyon varsayımı (%0,1 maker ve taker) 23 Eylül 2026'da Berk'in
  hesabında ölçüldü ve aynı çıktı; Faz 1-2 sonuçları geçerli.
* Berk'e anlatılan kullanım biçimi: uygulama kendi kendine öğrenmez; kural
  yokken sürekli açık durması gerekmez, aralıklı kullanım yeter. Kapatınca
  gözcüde (Healthchecks.io) Pause'a basar. Canlıda açık pozisyon varken açık
  tutar.

## 5. Berk'in kararları

* Testnet yerine Binance **Demo Mode** kullanılır.
* Kapsam: **BTCUSDT ve SOLUSDT, yalnızca 15m ve 1h**; hedefler 2-4 mumluk
  pencerede.
* **100 USDT doğrulama bütçesi** korunur. Risk motoru her öneride bütçenin
  mi risk kuralının mı bağlayıcı olduğunu gösterir ve adım büyüklüğü
  yuvarlamasından doğan hatayı hesaba katar. Performans oransal ölçülür.
* Kod GitHub'da durur, Berk'in bilgisayarında da yedeği olur.
* Faz 3: şartnamedeki öneri motoru ve arayüz, yanında lazım olunca açılan ek
  bölüm (izleme, maliyet/risk, disiplinli alım). Eşiği geçemeyen adaylar
  öneri kartı olarak **gösterilmez** ve kâğıt işleme otomatik sokulmaz.
* Faz 4: elle kâğıt emri kararını Claude'a bıraktı (*"sen profesyonel bir
  yaklaşımla getiri sağlama amacına uygun şekilde belirleyebilir misin"*).
  Elle emir aynı risk kapılarından geçer, sonuçları kural sonuçlarından ayrı
  sayılır, canlıya geçiş kapısına sayılmaz.
* Faz 6: **"Kapı + mini emir"**. Canlı yol ve denetimler yazıldı, tek küçük
  gerçek emirle sınandı. Tam Otomatik, kapıyı geçen bir kural olmadan açılmaz.
* Faz 7: **"Hazırla, kiralama"**. Sunucu kurulumu hazır, sunucu kiralanmadı,
  Mac'te devam ediliyor. **Sunucuda sırların nerede duracağı sorusu
  cevapsız**; sunucuya geçilecekse önce o yeniden sorulur.
* Şartnameden bilinen sapma: arayüz React+Vite yerine düz HTML/CSS/JS, çünkü
  Berk'e Node/npm kurdurmak istenmedi. Berk bu sapmayla onayladı.

Danışılmadan değiştirilmeyecek seçilmiş varsayılanların tam listesi
`docs/DEVIR-NOTU.md` §6'da (emir tavanı 10 USDT, giriş OTOCO ve
`LIMIT_MAKER`, piyasa emri yok, kısmi dolumda en fazla 20 sn korumasız
bekleme, belirsiz sonuçta yeniden gönderim yok, yeniden başlatmada her coin
Sadece Öneri'de açılır ve diğerleri).

## 6. Güvenlik kuralları (hiçbir işte düşmez)

* **TLS sertifika doğrulaması hiçbir koşulda, geçici olarak bile
  kapatılmaz.** Berk'in ağında HTTPS trafiğini yeniden imzalayan bir ara
  katman var; uygulama macOS güven deposunu kullanıyor.
* **Sırlar Berk'in bilgisayarından çıkmaz.** Binance anahtarları, Telegram
  jetonu ve gözcü adresi Mac'te Anahtar Zinciri'nde durur. Depoda, ortam
  değişkeninde, günlükte, arayüzde, yedekte ve sohbette yer almazlar. Anahtar
  ya da jeton isteme, yazdırma, dosyaya koyma; yalnızca Terminal'e gizli
  girişle yazılır.
* Berk bir kez sohbete bir API anahtarı yapıştırdı; kullanılmadı, hiçbir yere
  yazılmadı, iptal etmesi söylendi. Yine olursa aynısını yap (Telegram
  jetonunda BotFather `/revoke`).
* **Gerçek para ile emir gönderecek, para harcatacak, dış hesap açtıracak ya
  da API anahtarı gerektirecek her adımdan önce Berk'in yazılı onayı
  alınır**, adım dosyası verilmeden önce. Kart düğmesi yetmez. Uygulamanın
  kendi soruları (coin adını yazdırma, onay penceresi) son güvencedir,
  gevşetilmez.
* Emir gönderen anahtarda para çekme izni kapalı olmalı ve bu
  `apiRestrictions` içindeki `enableWithdrawals` ile denetlenmeli
  (`canWithdraw` hesabın bayrağıdır, anahtarın izni değil). Margin, vadeli,
  opsiyon ve transfer izinleri de kapalı. Sunucuda IP kısıtı zorunlu.
* **Aynı canlı hesapta iki kopya aynı anda çalışmaz** (§8'deki tuzağa bak).
* Binance'e giden istek hacmi sınırlıdır. Beklenmedik büyüklükte bir iş
  çıkarsa program istek göndermeden durur.
* Arayüz yalnızca `127.0.0.1` dinler.
* Depoya sır girmesin: gizli bilgi taraması hem kayıt öncesi kanca hem test
  olarak çalışır, gevşetilmez. Klonladıktan sonra
  `git config core.hooksPath .githooks` çalıştır.
* **Bu depo herkese açık.** Buraya yazdığın her şeyi herkes okuyabilir;
  kişisel bilgi, hesap ayrıntısı ve sır yazma.
* Kaldıraç, margin, futures ve borçlanma kodu yok ve olmayacak.

Ayrıntılı liste `docs/DEVIR-NOTU.md` §8'de.

## 7. Çalışma ortamının kısıtları ve doğrulama kuralı

* Uzak çalışma ortamının ağ politikası **Binance adreslerini engelliyor**.
  Gerçek veri ya da gerçek bağlantı gerektiren her adımı Berk kendi Mac'inde
  çalıştırır. Burada emir yürütme sahte borsayla (`tests/fake_binance.py`)
  sınanır. Yeni projenin ortamı farklı ayarlanmış olabilir; varsayma, dene.
* Oturumlar yeni GitHub deposu açamaz.
* macOS'a özgü kod (`security`, `caffeinate`, `pmset`) uzak ortamda çalışmaz.
* **Temiz klon kuralı:** Berk'e "çalıştır" demeden önce depoyu GitHub'dan
  geçici bir dizine sıfırdan indir ve kurulumu baştan çalıştır. Çalışma
  dizininde testlerin geçmesi kanıt değildir; bir kez bir kaynak klasörü
  `.gitignore` yüzünden depoya hiç gitmedi ve Berk'in Mac'inde patladı.
  `.gitignore` klasör kuralları kök dizine sabitlenir (`/veri/`, `/data/`).
* **Arayüz gerçek tarayıcıda doğrulanır** (Playwright; uçların 200 dönmesi
  yetmez): Retina oranında (piksel oranı 2), 390 px genişlikte, aynı
  etkileşim birkaç kez tekrarlanarak, konsol hataları toplanarak; hem kuralı
  olan hem hiç kuralı olmayan veriyle. Berk güncellemeden sonra "hâlâ bozuk"
  derse önce eski sekmeyi düşün; sayfanın altındaki "Arayüz sürümü" ile
  terminaldeki aynı olmalı.
* Binance toplu arşivleri 2025'ten beri mikrosaniye, REST ise milisaniye
  zaman damgası verir. Birimi varsayma, büyüklüğünden ölç
  (`albsat.data.klines.normalize_epoch_ms`). Binlerce "boşluk" görülürse veri
  bozuktur; program istek göndermeden durur.
* Diğer ortam ayrıntıları `docs/DEVIR-NOTU.md` §2'de.

## 8. Bu dalla çalışırken dikkat edilecek tuzaklar

Bunlar yalnızca bu ayrı çalışma alanı yüzünden var; başka hiçbir belgede
yazmıyor.

1. **Berk'in Mac'i `main`'i çalıştırıyor.** README, `KURULUM.md` ve eski adım
   dosyalarındaki komut (`cd ~/Desktop/alsat && git pull && bash kurulum.sh
   ...`) `main`'i indirir, bu dalı değil. Bu daldaki bir değişikliği Berk'in
   Mac'inde denemek gerekiyorsa nasıl yapılacağını önce onunla kararlaştır ve
   adımları tek tek yaz.
2. **Veri klasörü depoda değil.** `~/Desktop/alsat/veri` içinde indirilmiş
   mumlar, veritabanı, ayarlar ve **kural deposu** var. Kural deposu birikimli
   aday sayısını (12.021) taşıyor. Ayrı bir klasöre yapılan temiz kurulum bu
   sayıyı sıfırdan başlatır ve çoklu test düzeltmesini bozar. Yeni bir klasör
   kurduracaksan bunu hesaba kat.
3. **Tek kopya kilidi veri klasörüne bağlıdır** (`<veri>/.albsat-kilit`).
   Aynı Mac'te iki ayrı klasördeki iki kopyayı birbirinden korumaz. Anahtar
   Zinciri ise ortaktır: ikinci klasördeki kopya da canlı anahtarı görür. İki
   kopya aynı canlı hesapta aynı anda çalışırsa birbirinin emirlerini iptal
   edebilir. Mac'te aynı anda yalnızca bir kopya çalışmalı.
4. **Bir kez çalışan komutlar** (`tur2`, `trend`) rapor dosyası varsa yeniden
   çalışmaz. Bu bilerek böyle; aşmanın yolunu arama.
5. **Deneme ile ölçümü karıştırma.** Bu dal denemeler içindir, ama §4'teki
   kapanmış sorular burada da kapalıdır. Yeni bir fikir sınanacaksa ön kayıt
   sonuç görülmeden önce depoya gönderilir.

## 9. Açık işler ve olası yönler

Hiçbiri başlatılmadı; hangisinin yapılacağını Berk söyler.

* **Telegram ve uyku engeli sınamasının sonucu** Berk'te (§3).
* **Sunucuya geçiş:** kod ve rehber hazır (`FAZ7-SUNUCU.md` §12). Önce sır
  deposu sorusu, sonra kiralama, gözcü ve sunucuda canlı anahtar için ayrı
  ayrı yazılı onay.
* **Mac'te hiç sınanmayanlar:** izin bozulunca canlı modların kapanması, Yarı
  Otomatik önerisi, yalnızca kaos testiyle sınanan emir yolları (kısmi dolum,
  stopun altında korumalı çıkış, uykudan sonra uzlaştırma).
* **SOL trend kuralı:** yalnızca gösterge olarak duruyor. Kâğıt işlemde
  izlemek ya da daha ötesi Berk'in ayrı onayını ister.
* Küçük açık işler `docs/DEVIR-NOTU.md` §7'de.

## 10. Nereye bakmalı

| Konu | Belge |
|---|---|
| Şartname | `docs/SPEC.md` |
| Ayrıntılı devir notu, komut tablosu, tuzaklar, varsayılanlar | `docs/DEVIR-NOTU.md` |
| Mimari ve Binance API bulguları | `docs/FAZ0-MIMARI.md` |
| Kurulum (Berk için yazılmış) | `KURULUM.md` |
| Durum tablosu ve hazır olanlar | `README.md` |
| Her fazın yöntemi ve gerekçeleri | `docs/FAZ*.md` |
| Ön kayıtlar ve sonuçlar | `docs/TUR2-*.md`, `docs/TREND-*.md` |
