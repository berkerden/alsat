# Devir notu — Faz 6'dan Faz 7'ye

23 Eylül 2026. Bu not, koda ve diğer belgelere bakarak öğrenilemeyecek
şeyleri yeni oturuma aktarmak içindir. Şartname `docs/SPEC.md`, mimari
kararlar `docs/FAZ0-MIMARI.md`, fizibilite sonucu `docs/FAZ1-FIZIBILITE.md`,
Faz 2 sonucu `docs/FAZ2-SONUC.md`, Faz 2 yöntemi `docs/FAZ2-ORUNTU-MOTORU.md`,
Faz 3 yöntemi `docs/FAZ3-ONERI-MOTORU.md`, Faz 4 yöntemi
`docs/FAZ4-RISK-KAGIT-TELEGRAM.md`, Faz 5 yöntemi
`docs/FAZ5-DEMO-EMIR-YURUTME.md`, **Faz 6 yöntemi `docs/FAZ6-CANLI.md`**.

**Faz 7'nin işi (SPEC §10):** *"VPS dağıtımı (Docker), izleme, yedekleme"*.
Kabul kriteri: *"7/24 çalışma ve alarm testi."* Ayrıntılar SPEC §5 (güvenlik),
§6 (Mac'e özel) ve §7'de (VPS aşaması). **Başlamadan önce §7'nin ilk üç
maddesini oku:** kabul edilmiş kural yokken 7/24 çalışmanın ne anlama
geldiğini, VPS'te sırların nerede duracağını ve para gerektiren adımları
Berk'e sormak gerekiyor.

Faz 7 bitince bu not aynı sekiz başlıkla baştan yazılır. Belgelerin kendisi
yerinde duruyor; bu not onların yerine geçmez, hangisinin ne zaman
okunacağını söyler.

## 1. Berk nasıl çalışıyor

* Mac kullanıyor (Retina), kod `~/Desktop/alsat` altında. Terminal, git ve
  GitHub akışları ona tanıdık değil.
* Kendi bilgisayarında atması gereken adımlar tek tek, sade ve elinden
  tutarak anlatılmalı: hangi tuşa basacağı, ekranda ne göreceği, neyi
  kopyalayacağı. Çıplak komut listesi verme. Uzun adım listesi dosya olarak
  verilir (Faz 6'da `/mnt/project-files/faz6/FAZ6-MAC-ADIMLARI.md`).
* **Komutu her zaman tam satır olarak ver** (Faz 5'te kısa ad yazıldı, Berk
  yalnızca `demo-anahtar` yazıp "bir şey olmadı" dedi):
  `cd ~/Desktop/alsat && git pull && bash kurulum.sh <seçenek>`

  | Seçenek | Ne yapar |
  |---|---|
  | (boş) | Veriyi tazeler + fizibilite + örüntü taraması |
  | `tarama` | İnternete çıkmaz; yalnızca örüntü taraması |
  | `teshis` | Tarama + maliyetsiz teşhis turu |
  | `arayuz` | Kurulumu ve testleri kontrol edip arayüzü açar (canlı fiyat akışıyla) |
  | `telegram` | Telegram botunu kurar (jeton Anahtar Zinciri'ne) |
  | `anahtar` | Salt okuma Binance anahtarı kurar, komisyonu ölçer |
  | `komisyon` | Kayıtlı anahtarla komisyonu yeniden ölçer |
  | `demo-anahtar` | Demo Mode anahtarı kurar, hesabı ve Demo komisyonunu okur |
  | `demo-sina` | Demo'ya dolmayacak bir sınama emri gönderip iptal eder (sorarak) |
  | `canli-anahtar` | **Canlı** işlem anahtarını kurar; yalnızca okur (izinler, hesap, komisyon) |
  | `canli-sina` | **Canlı** hesaba dolmayacak bir sınama emri gönderip iptal eder (coin adını yazdırarak) |

* **Terminal'de soru soran bir adım ona donmuş gibi görünür.** Önceden hangi
  soruyu göreceğini ve ne basacağını yaz ("hiçbir şey yazmadan `Enter`").
* Uzun süren her adım ekrana ilerleme yazmalı. Sessiz ekrana bakınca
  takıldığını sanıyor.
* Terminal ve arayüz için ekran görüntüsü yolluyor. İstenen görüntülerin
  hepsini göndermeyebilir: Faz 6'da Binance'in IP kısıtı sayfasının ve emir
  gönderildikten sonraki ekranın görüntüsü gelmedi. Kanıt gerektiren şeyi
  ayrıca ve tek başına iste.
* **İstenen yazılı onayı beklemeden adımları kendisi çalıştırabiliyor.** Faz
  6'da adım dosyası "her bölüm için önce yazılı onayınız" diyordu; Berk onay
  yazmadan anahtarı kurdu, sınama emrini ve gerçek alışı yaptı, sonra ekran
  görüntüleriyle döndü. Bu yüzden onay, adım dosyasını vermeden **önce**
  alınmalı; gerçek para harcayan her adımda uygulamanın kendi sorusu (coin
  adını yazdırma, onay penceresi) son güvencedir ve gevşetilmemeli.
* Kısa ve net soruyu seviyor; seçenekli sorularda bir öneri işaretlenince
  hızlı karar veriyor. Karar kartındaki düğmeye basarak da cevap veriyor.
* Kod GitHub'da `berkerden/alsat`, `main` dalında. Doğrudan `main`'e
  gönderiliyor; Berk'in bilgisayarında da yedeği var.
* Mac'inde Python 3.14.7 var; 678 test orada ~22 sn'de geçiyor.

## 2. Çalışma ortamının kısıtları ve doğrulama kuralı

* Ağ politikası **Binance adreslerini engelliyor** (proxy 403), Demo Mode ve
  canlı hesap dahil. Gerçek bağlantı gerektiren her şey Berk'in Mac'inde ilk
  kez çalışır; emir yürütme sahte borsayla (`tests/fake_binance.py`) sınanır.
* PyPI erişilebilir; kurulum ve testler burada çalışır. Proje 3.12+ istiyor.
* Bu oturumların yetkisi yeni GitHub deposu açmayı kapsamıyor. `git push`
  Faz 6'da bir kez 403 verdi; depo zaten bağlı olduğu hâlde `add_repo`
  çağrılıp yeniden denenince geçti.
* macOS'a özgü kod (`security`, `caffeinate`, `pmset`) burada
  çalıştırılamaz; testler bunları sahte süreçlerle sınar.
* **Faz 7 için:** burada `docker` ve `docker compose` istemcisi kurulu ama
  Docker servisi çalışmıyor (`/var/run/docker.sock` yok); servisin
  başlatılıp başlatılamayacağı denenmedi. `ssh` kurulu değil. Bir VPS'e
  buradan bağlanmak denenmedi; VPS adımlarını büyük olasılıkla Berk yapacak
  (çıkarım).

**Doğrulama kuralı:** Berk'e "çalıştır" demeden önce depoyu **GitHub'dan
geçici bir dizine temiz klonlayıp** `bash kurulum.sh canli-anahtar` (ya da
başka bir tek adım) ile Python bulma, sanal ortam, bağımlılık ve testleri
baştan çalıştır; macOS dışında tek adım "yalnızca Mac'te çalışır" deyip temiz
çıkar. Buradaki çalışma dizininde testlerin geçmesi kanıt değil (21 Eylül:
`.gitignore` kaynak paketini yuttu, burada geçti, Berk'te patladı).

**Arayüzü doğrulamanın yolu:** Chromium kurulu (`/opt/pw-browsers/chromium`),
Node Playwright genel kurulu; betik
`NODE_PATH=/opt/node22/lib/node_modules node betik.js` ile çalışır. Faz 6'nın
düzenek sunucusu dizinde kalmadı; yeniden kurmak için testlerin yardımcıları
yeterli: `tests/test_demo_kaos.build(hesap="canli")` sahte borsaya bağlı
gerçek canlı yürütücüyü kurar, `tests/test_canli._client` onu `create_app`'e
verir. Faz 6'da üç senaryo sınandı: dolu (Yarı Otomatik, bekleyen öneri,
korunan pozisyon, kapanan işlem), izin bozuk (para çekme açık), çevrimdışı.
Düzenekte kural deposu ve Demo yürütücüsü olmadığı için `/api/oneriler`
404 ve `/api/demo/` 503 döner; bunlar düzenekten, arıza değil. 1280 px'te
`deviceScaleFactor` 1 ve 2, 390 px'te 2 ve 3; her sekmede yatay taşma,
konsol hatası, beş saniyelik yenilemede yazılan alanların durması. Gizli
`<option>` beklerken `state: "attached"` kullan.

## 3. Berk'in Faz 6'da verdiği kararlar — kendi cümleleriyle

* **Faz 6'yı başlattı** (23 Eylül): *"faz 6 ile devam et"*.
* **Kapsam** (23 Eylül, karar kartı): **"Kapı + mini emir"**. Canlı yol ve
  güvenlik denetimleri yazılır, kapı çalışır, Tam Otomatik kapıda kilitli
  kalır, mekanizma en küçük tutarda tek gerçek emirle sınanır. Reddettiği
  seçenekler: "Yalnızca kapı" ve "Beklet".
* **Gerçek hesap sınaması** (23 Eylül): ayrıca yazılı onay yazmadan
  adımları çalıştırdı, üç ekran görüntüsüyle döndü: *"emir gönderdikten
  sonraki ekranı çekemedim ama işlem gerçekleşmişti. Şimdi soğuma süresi
  olduğu için hemen yaptırmıyor beklemem lazım. yeter mi senin için"*.
* **Faz 6, 23 Eylül 2026'da onaylandı.** Berk'in cümlesi: *"onaylıyorum"*.

Önceki fazlardan hâlâ geçerli olan kararları: Testnet yerine Demo Mode
(21 Eylül); kapsam A: BTCUSDT ve SOLUSDT, yalnızca 15m ve 1h; 100 USDT
doğrulama bütçesi; elle emir kararını Claude'a bırakması (Faz 4, *"sen
profesyonel bir yaklaşımla getiri sağlama amacına uygun şekilde
belirleyebilir misin"*).

Berk'in çalışma yöntemi hakkındaki kuralı (21 Eylül): *"Yeni bir oturum ile
yeni fazlara geçiş yap. Bağlam şişip kalite düşmesin bu sayede. Her oturumda
öğrendiğimiz, dikkat ettiğimiz, sonraki faza aktarılması gerekecek bilgileri
toparla ve sonraki faz için sonraki oturuma taşı, eksik bilgi olmadan devam
etmiş olsun."*

## 4. Faz 6 ne yaptı (tek paragraf)

Faz 5'in emir yürütücüsü **canlı hesaba** bağlandı ve gerçek paranın
gerektirdiği kilitler eklendi: ayrı canlı istemci (`LiveTrader`, önek
`albsat-canli-`, kendi izin listesi), ayrı Anahtar Zinciri kaydı
(`albsat-binance-canli`) ve `canli_` tabloları; anahtar izinlerinin
`apiRestrictions` ile açılışta ve 30 dakikada bir okunması (bozulursa canlı
modlar kapanır, bekleyen girişler iptal olur; yeni giriş için izin son bir
saatte okunmuş olmalı); emir tavanı (varsayılan 10 USDT, üç yerde
uygulanıyor); Yarı Otomatik önerileri (arayüzden ve Telegram `/onayla`,
`/reddet`); kural başına canlıya geçiş kapısı (`execution/gate.py`, altı
koşul); Canlı işlem sekmesi ve iki kurulum komutu. Tam Otomatik kapıda
kilitli, çünkü kabul edilmiş kural yok. Toplam **678 test** (36 kaos testi
hem Demo hem canlı yürütücüyle). Berk'in Mac'inde gerçek hesapta:
`canli-anahtar` geçti (saat farkı 97 ms; çekim kapalı, Spot açık, IP
kısıtsız; komisyon maker/taker %0.1), `canli-sina` 6/6 geçti (≈8 USDT OTOCO
kabul edildi, akıştan geldi, iptal edildi), arayüzden elle canlı alış
84218.9'dan doldu, borsa hedef ve stopu koydu (korumasız 0,0 sn), "Pozisyonu
kapat" ile 84236.91'den satıldı: net −0,01 USDT, yalnızca komisyon. Ayrıntı
ve gerekçeler `docs/FAZ6-CANLI.md`.

**Eldeki komut satırı araçları**

| Araç | Ne yapar | Ağa çıkar mı |
|---|---|---|
| `albsat-fizibilite` | Veri indirir, filtreleri önbelleğe yazar, fizibilite taraması | Evet |
| `albsat-oruntu` | Örüntü keşfi, istatistik, backtest, rapor, kural deposu (`--maliyetsiz` teşhis) | Hayır |
| `albsat-arayuz` | Arayüz + canlı döngü + Demo ve canlı yürütücü, yalnızca 127.0.0.1 (`--cevrimdisi` ile ağsız) | Evet |
| `albsat-telegram` | Telegram kurulumu, deneme mesajı, kaldırma | Evet (Telegram) |
| `albsat-anahtar` | Salt okuma anahtar kurulumu, komisyon ölçümü (`--olc`, `--sil`) | Evet (2 imzalı okuma) |
| `albsat-demo` | Demo anahtar kurulumu ve doğrulama; `--sina` uçtan uca sınama; `--sil` | Evet (Demo) |
| `albsat-canli` | Canlı anahtar kurulumu ve okuma; `--sina` dolmayan emirle sınama | Evet (**canlı**) |
| `albsat-tls-teshis` | Sertifika zinciri teşhisi | Evet |

## 5. Faz 6'da öğrenilen, koda bakarak görülmeyecek tuzaklar

1. **Gerçek bir sınama emri 7 USDT'den küçük olamaz.** Alışta komisyon
   coinden kesildiği için satılacak miktar `stepSize`'a aşağı yuvarlanıyor;
   6 USDT'de satış borsanın en küçük tutarının (BTCUSDT'de 5 USDT) altına
   düşebiliyor ve hedef/stop reddedilir. `canli-sina` en küçük tutarın 1,5
   katını kullanıyor (Berk'te ≈8 USDT).
2. **Zararla kapanan her işlem, komisyon kadar küçük olsa da, aynı coinde
   kayıp sonrası soğumayı başlatır** (`kayip_sonrasi_soguma_mum: 2`). Berk
   mini emirden sonra yeni emri bekleyen kapıyı gördü. Aynı coinde art arda
   gerçek sınama planlama; ya diğer coini kullan ya da süreyi söyle.
3. **Tavan bağlayıcıyken kart eskiden "bütçe" yazıyordu** ve hedefi
   yanıltıcı gösteriyordu. `Binding.CAP` ("tutar sınırı") bunun için eklendi;
   risk yine bot bütçesinden hesaplanır. Yeni bir tutar sınırı eklenirse
   bağlayanı doğru adlandır.
4. **İzin okuması bir saatten eskiyse yeni giriş gitmez.** Mac bir saatten
   uzun uyursa uyandıktan sonraki ilk giriş, izinler yeniden okunana kadar
   bekler. Koruma emirleri ve iptaller bu kilide takılmaz; takılırsa elde
   coin korumasız kalır.
5. **Canlı yürütücü, canlı piyasa akışıyla aynı istek bütçesini kullanır**
   (Binance'in ağırlık sınırı IP başına). Aynı IP'den ikinci bir süreç
   (örneğin arayüz açıkken `canli-sina`) bu bütçeyi görmez; üstelik
   uzlaştırma kaydında olmayan `albsat-canli-` alışını yetim sayıp iptal
   eder. `canli-sina` arayüz açıkken çalıştırılmamalı.
6. **Binance'in IP kısıtı kuralı Berk'in hesabında doğrulanmadı.** 2021'deki
   "kısıtsız anahtarda işlem izni 90 günde kapanır" kuralını Binance 24 Ekim
   2023'te geçersiz ilan etti; bugünkü kural görülmedi (ekran görüntüsü
   gelmedi). Uygulama izinleri 30 dakikada bir okuduğu için Binance işlem
   iznini kapatırsa canlı girişleri durdurur ve haber verir.
7. **Beş saniyelik yenileme** Canlı sekmesini yeniden çizer. Kullanıcının
   yazdığı her alan (Tam Otomatik özetindeki onay kutusu dahil,
   `canli.js` → `tamOnayMetni`) yenilemede korunmalı; yeni form eklersen
   aynısını yap.
8. **Süreç öldürme:** `pkill -f desen` kendi kabuğunu öldürür (çıkış 144).
   Deseni `[c]anli_sunucu` biçiminde yaz ya da sunucuyu `setsid` ile başlatıp
   PID dosyasıyla durdur.
9. **Lint ve tip tabanı:** `ruff check src tests` 30, `mypy src` 35 hata (21
   dosya). Hepsi Faz 1-3 dosyalarında; istisna, `exchange/endpoints.py` ve
   `risk/sizing.py`'deki iki `UP042` (`str` + `Enum`) uyarısı. Faz 4-6 kodu
   bunların dışında temiz; yeni kodu temiz tut.

**Faz 5'ten taşınan tuzaklar (hâlâ geçerli):** `canWithdraw` hesabın
bayrağıdır, anahtarın izni değil (`keys.assert_no_withdrawal_permission`
Faz 6'da `apiRestrictions`'a dayanacak biçimde yeniden yazıldı); iptal
olayında emrin kimliği `C`'dedir, `c` değil; OTOCO'nun bekleyen bacakları
kimlikle sorgulanabilir, sahte borsada `GET /api/v3/orderList` yok; satış
miktarı alış miktarı değildir (`planner.sell_quantity`); sonucu bilinmeyen
istekte yeni kimlikle yeniden gönderim yok, kimlik gönderimden önce diske
yazılır; Demo defteri canlı defterden ayrıdır; `/api/demo/durum` ve
`/api/canli/durum` Binance'e istek göndermez.

**Faz 2-4'ten taşınan tuzaklar:** yazma uçları yerel korumadan geçer
(`X-Albsat-Istek: 1`, JSON, Host 127.0.0.1; `TestClient`'a
`base_url="http://127.0.0.1"`); `LiveRunner.tick()` her akış olayında
çalışır, eklenen iş tekdüze saatle seyreltilmeli; spread ısınması 5 dakika;
sayılar nokta ondalıkla; sırlar hata metninde ve denetim kaydında maskelenir;
`CostAssumptions`'ta oran ile yüzde karışık (bilerek); `Decimal` için
`format_for_api`; Retina'da canvas boyutu geri okunmaz; "hâlâ bozuk" derse
önce eski sekmeyi düşün; kapanmamış mum tahmine giremez; fiyat, miktar ve
bakiyede `float` yok; yeni tarama turu çoklu test sorunudur.

## 6. Danışılmadan değiştirilmemesi gereken seçilmiş varsayılanlar

**Faz 6'da seçilenler** (gerekçeler `FAZ6-CANLI.md` §3, §4, §8):

1. **Emir tavanı varsayılan 10 USDT**, `config/default.yaml` →
   `canli.emir_tavani_usdt` ile `execution/live.py` → `DEFAULT_CAP_USDT`
   aynı olmalı (test denetler). Tavan 0'dan büyük, bot bütçesinden küçük ya
   da eşit; her değişiklik denetim kaydına yazılır.
2. **Kapıyı elle aşan yol yok.** SPEC "açık bir uyarı ekranı ve ek onay" ile
   aşmaya izin veriyor; Berk'in kararıyla yazılmadı.
3. **Kapıda "ortalama net > 0" koşulu** var; SPEC'te yok. Beklentiyle uyumlu
   ama zarar eden kural gerçek paraya taşınmasın diye.
4. **Yeniden başlatmada ayar yok:** her açılış Sadece Öneri. SPEC bunu
   "ayarlanabilir, varsayılan kapalı" diyor.
5. **Mac'te IP kısıtı yok;** özel yarı yalnızca Mac'te, API Key tek başına
   imza atamaz, çekim kapalı ve Berk'in ağındaki ara katman çıkış IP'sini
   değiştirebilir. **VPS'te kısıt zorunlu.**
6. **İzin denetimi 30 dakikada bir; yeni giriş için izin ≤ 1 saat.** Koruma
   emirleri ve iptaller kilide takılmaz.
7. **Yarı ve Tam Otomatik yalnızca Canlı işlem sekmesinden** ve coin adı
   yazılarak açılır; elle canlı emir yalnızca Yarı Otomatik'teki coinde.
8. **Ayrı adlar:** Anahtar Zinciri `albsat-binance-canli`, emir öneki
   `albsat-canli-`, tablolar `canli_`. Demo ile canlı kayıtları ve
   uzlaştırmaları hiç karışmaz.

**Önceki fazlardan:**

9. Giriş OTOCO, `LIMIT_MAKER`; varsayılan stop piyasa stop (`STOP_LOSS`);
   **piyasa emri kullanılmaz.** Elde coin varsa borsada canlı stop; kısmi
   dolumda en fazla 20 sn korumasız, sonra OCO; stopun altındaysa korumalı
   `LIMIT IOC`, kayma en fazla %0.5.
10. Belirsiz sonuçta yeniden gönderim yok.
11. Bir yerde risk sınırı aşılınca bütün coinler Sadece Öneri'ye iner
    (kâğıt, Demo ve canlı birlikte), bekleyen girişler iptal edilir, açık
    pozisyonun stop ve hedefi yerinde kalır; yeniden açmak yalnızca elle.
12. **Bot bütçesi 100 USDT;** borsadaki bakiye büyük olsa da yalnızca bütçe
    kadar kullanılır. Demo ve canlı bütçeleri ayrı defterlerde tutulur.
13. **Borsada elle verilen emirlere dokunulmaz;** yetim önekli alış iptal
    edilir, yetim önekli satışa dokunulmaz. `DELETE /api/v3/openOrders`
    bilerek izin listesinde yok.
14. **ACİL DURDUR pozisyon kapatmaz;** modları indirir, bekleyen girişleri
    iptal eder. Kapatmak ayrı seçim.
15. **Elle emirler ayrı sayılır;** kural performans sınamasına ve kapıya
    girmez. Kabul edilmemiş adaylar otomatik işlenmez.
16. Kâğıt dolum kuralları temkinli; BNB indirimi kullanılmaz. Ölçülemeyen
    koşul geçmiş sayılmaz; aralık dışı limit reddedilir, kırpılmaz.
17. Sunucu yalnızca 127.0.0.1; arayüz derleme adımsız düz HTML/CSS/JS
    (Node/npm kurdurma); kapsam A.

## 7. Yarım kalanlar ve Faz 7'ye taşınan uyarılar

1. **Faz 7'nin ne anlama geldiğini Berk'e sor.** Kabul edilmiş kural yok
   (Faz 2); 7/24 çalışan bot bugün yalnızca Sadece Öneri'de izler, öneri de
   üretmez. Faz 6'daki gibi seçenekli bir kart uygun: örneğin tam VPS
   (Docker, izleme, yedek, alarm), yalnızca Mac'te 7/24 (launchd, uyku
   engeli, dış izleme, alarm) ya da bekleme. Kural uydurma, elle emri kapıya
   sayma.
2. **VPS'te sırların yeri Berk'e sorulmalı.** SPEC §5 VPS'te "şifreli gizli
   dosya veya ortam değişkeni" diyor; bu projenin kuralı "ortam değişkenine
   sır konmaz". Anahtar Zinciri yalnızca macOS'ta var; VPS için yeni bir sır
   deposu gerekir. VPS'te **yeni bir canlı anahtar** VPS'in kendisinde
   üretilmeli, IP kısıtı VPS'in sabit IP'sine bağlanmalı.
3. **Para ve dış hizmet gerektiren adımlar:** VPS kiralamak, harici izleme
   hizmeti, VPS için canlı anahtar oluşturmak. Her biri öncesinde Berk'in
   **yazılı** onayı gerekir (kart düğmesi yetmez); onayı adım dosyasını
   vermeden önce al (§1). Sağlayıcı seçimi Berk'in kararı.
4. **Mac ve VPS aynı canlı hesapta aynı anda çalışmamalı.** Her yürütücü
   kendi kaydında olmayan `albsat-canli-` alışını yetim sayıp iptal eder;
   iki kopya birbirinin girişini siler. Geçiş rehberi Mac'teki kopyayı
   durdurmayı ve açık pozisyonun nasıl devredileceğini anlatmalı.
5. **SPEC §5-7'de açık kalanlar:** `.env.mac` / `.env.vps` ortam ayarı;
   Docker Compose, yeniden başlatma politikası, sağlık kontrolleri; log
   rotasyonu; veritabanının günlük yedeği; harici uptime izleme ve alarm;
   Mac'ten VPS'e geçiş rehberi (veri, ayar, IP kısıtı); Mac'te `launchd`
   (isteğe bağlı, çökmede yeniden başlatma); VPS'te arayüz internete açık
   olmamalı (Tailscale/WireGuard ya da HTTPS + parola + TOTP); bağımlılık
   sürümlerinin sabitlenmesi ve güvenlik açığı taraması (şu an
   `pyproject.toml`'da `>=`, kilit dosyası yok); pre-commit gizli bilgi
   taraması (yok); bot için Binance alt hesabı önerisi (README'de yok);
   Binance'in yerel `trailingDelta` desteği (kullanılmıyor).
6. **"Bot çevrimdışı" bildirimi bugün yarım.** Uygulama 30 saniyede bir
   veritabanına nabız yazıyor (`paper/runner.py`, `son_nabiz_utc`) ve yeniden
   açılınca Telegram'a "Uygulama … çevrimdışıydı" diyor. Uygulama kapalıyken
   haber veren dış bir gözcü yok; Faz 7'nin alarm testi bunu istiyor.
7. **Berk'in Mac'inde hâlâ sınanmayanlar:** `caffeinate` uyku engeli ve
   `pmset` pil uyarısı; **Telegram kurulmadı**, bu yüzden Yarı Otomatik'in
   `/onayla` yolu gerçekte denenmedi; izin bozulunca canlı modların
   kapanması; Yarı Otomatik önerisi (kural yok); Tam Otomatik (kapıda
   kilitli). Bunlar yalnızca testle sınandı.
8. **Yalnızca kaos testiyle sınanan yollar:** kısmi dolum → OCO, stopun
   altında korumalı çıkış, yeniden koruma, süresi dolan stop, uykudan sonra
   uzlaştırma, 429/418. Gerçekte görülen: OTOCO kabulü, akış olayları,
   bekleyen bacak sorgusu, iptal, dolumdan sonra hedef/stopun borsaya
   konması, "Pozisyonu kapat".
9. **Berk'in hesaplarında kalanlar:** canlı hesapta alış komisyonundan küçük
   bir BTC artığı (toz) ve Berk'in kendi SOL emri (uygulama dokunmadı). Demo
   hesabında Faz 5'ten elle #2 (BTCUSDT 15m, 0.00056 BTC, hedef 87000, stop
   83000) kendiliğinden kapanmış olabilir. BTCUSDT arayüzde Yarı Otomatik'te
   bırakıldıysa uygulama yeniden açılınca Sadece Öneri'ye döner. Yeni oturum
   bunları arıza sanmasın.
10. **Resmi SDK kullanılmadı** (gerekçe `FAZ5-DEMO-EMIR-YURUTME.md` §9);
    `binance-connector` "deprecated", kullanma.

## 8. Güvenlik (bunlar hiçbir fazda düşmez)

* **TLS sertifika doğrulaması hiçbir koşulda, geçici olarak bile
  kapatılmayacak.** Berk'in ağında HTTPS'i yeniden imzalayan bir katman var;
  uygulama `truststore` ile macOS güven deposunu kullanıyor. WebSocket
  istemcileri de aynı güven deposunu kullanıyor; kendi SSL bağlamını veren
  kod bunu atlar. VPS'te sistemin güven deposu kullanılır, doğrulama yine
  kapatılmaz.
* Sırlar Berk'in bilgisayarından çıkmıyor. Anahtarlar (`albsat-binance`,
  `albsat-binance-demo`, `albsat-binance-canli`) ve Telegram jetonu
  (`albsat-telegram`) macOS Anahtar Zinciri'nde; depoda, dosyada, günlükte,
  denetim kaydında, arayüzde yoklar. Ortam değişkenine sır konmuyor. Özel
  yarı Mac'te üretilir; Binance'e yalnızca genel yarı verilir. VPS'e geçişte
  bu kural Berk'le birlikte yeniden yazılır (§7, madde 2); Mac'teki özel
  yarı VPS'e kopyalanmaz.
* Berk 21 Eylül 2026'da sohbete bir Binance API anahtarı yapıştırmıştı;
  kullanılmadı, hiçbir yere yazılmadı, silmesi söylendi. Anahtar isteme,
  yazdırma, dosyaya koyma. API Key yalnızca Terminal'e gizli girişle yazılır.
* **Gerçek para ile emir gönderecek, para harcatacak ya da API anahtarı
  gerektirecek her adım öncesinde Berk'in yazılı onayı alınır.**
* **Emir gönderen anahtarda çekim izni kapalı olmalı** ve bu
  `apiRestrictions` → `enableWithdrawals` ile denetlenmeli, `canWithdraw`
  ile değil. Margin, vadeli, opsiyon ve transfer izinleri de kapalı olmalı.
* İmzalı istemciler izin listesiyle çalışır; yalnızca kendi önekli
  (`albsat-demo-`, `albsat-canli-`) emirlerini gönderir ve iptal eder.
  `DELETE /api/v3/openOrders` eklenmez. Canlı giriş emri tavanı imzalayan
  sınıfta da sınanır.
* Binance'e giden istek hacmi sınırlı (yerel tavan 600 ağırlık/dk, 429'da
  bekle, 418'de dur, emir sayısı sınırının %50/%80'inde kapı kapanır).
  Beklenmedik büyüklükte bir iş çıkarsa program istek göndermeden durur.
* Komisyon oranı, sembol filtresi veya limit **koda sabit yazılmaz**.
* Arayüz yalnızca `127.0.0.1` dinler; yazma uçları yerel korumanın
  arkasında. VPS'te de internete doğrudan açılmaz.
* Kaldıraç, margin, futures ve borçlanma kodu yok ve olmayacak. Emir
  yürütme için Binance MCP sunucusu kullanılmaz (SPEC §11).
