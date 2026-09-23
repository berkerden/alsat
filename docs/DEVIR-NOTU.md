# Devir notu — Faz 5'ten Faz 6'ya

23 Eylül 2026. Bu not, koda ve diğer belgelere bakarak öğrenilemeyecek
şeyleri yeni oturuma aktarmak içindir. Şartname `docs/SPEC.md`, mimari
kararlar `docs/FAZ0-MIMARI.md`, fizibilite sonucu `docs/FAZ1-FIZIBILITE.md`,
Faz 2 sonucu `docs/FAZ2-SONUC.md`, Faz 2 yöntemi `docs/FAZ2-ORUNTU-MOTORU.md`,
Faz 3 yöntemi `docs/FAZ3-ONERI-MOTORU.md`, Faz 4 yöntemi
`docs/FAZ4-RISK-KAGIT-TELEGRAM.md`, **Faz 5 yöntemi
`docs/FAZ5-DEMO-EMIR-YURUTME.md`**.

**Faz 6'nın işi (SPEC §10):** canlı **Yarı Otomatik**, sonra küçük bütçeyle
**Tam Otomatik**. Kabul kriteri: *"Canlıya geçiş kapısı çalışıyor; güvenlik
kontrolleri aktif."* Mod kuralları SPEC §4'te (satır 159-165: "Emri Gönder"
onayı, 7 gün / 30 işlem kapısı, PIN ya da coin adıyla açma, yeniden
başlatmada Tam Otomatik'in kendiliğinden açılmaması). **Bu faz gerçek para
kullanır.** Başlamadan önce §7'nin ilk üç maddesini oku.

Faz 6 bitince bu not aynı sekiz başlıkla baştan yazılır. Belgelerin kendisi
yerinde duruyor; bu not onların yerine geçmez, hangisinin ne zaman
okunacağını söyler.

## 1. Berk nasıl çalışıyor

* Mac kullanıyor (Retina), kod `~/Desktop/alsat` altında. Terminal, git ve
  GitHub akışları ona tanıdık değil.
* Kendi bilgisayarında atması gereken adımlar tek tek, sade ve elinden
  tutarak anlatılmalı: hangi tuşa basacağı, ekranda ne göreceği, neyi
  kopyalayacağı. Çıplak komut listesi verme. Uzun adım listesi dosya olarak
  verilir (`/mnt/project-files/faz5/FAZ5-MAC-ADIMLARI.md` gibi); Berk
  dosyanın satırlarına yorum yazarak cevap veriyor.
* **Komutu her zaman tam satır olarak ver.** Faz 5'te dosyada
  "`demo-anahtar` ve `demo-sina`" diye kısa ad geçti; Berk Terminal'e yalnızca
  `demo-anahtar` yazdı ve "bir şey olmadı" dedi. Doğrusu:
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

* **Terminal'de soru soran bir adım ona donmuş gibi görünür.** Önceden hangi
  soruyu göreceğini ve ne basacağını yaz ("hiçbir şey yazmadan `Enter`").
* Uzun süren her adım ekrana ilerleme yazmalı. Sessiz ekrana bakınca
  takıldığını sanıyor.
* Terminal çıktısını artık sohbete yapıştırabiliyor; arayüz için ekran
  görüntüsü yolluyor (`Cmd + Shift + 3` tarif edildi).
* Kısa ve net soruyu seviyor; seçenekli sorularda bir öneri işaretlenince
  hızlı karar veriyor. Karar kartındaki düğmeye basarak da cevap veriyor.
* Kod GitHub'da `berkerden/alsat`, `main` dalında. Doğrudan `main`'e
  gönderiliyor; Berk'in bilgisayarında da yedeği var.
* Mac'inde Python 3.14.7 var; 583 test orada ~15 sn'de geçiyor (2 uyarı,
  burada 1; zararsız).

## 2. Çalışma ortamının kısıtları ve doğrulama kuralı

* Ağ politikası **Binance adreslerini engelliyor** (proxy 403), Demo Mode
  (`demo-api.binance.com`) dahil. Buradan gerçek piyasa verisine,
  WebSocket'e ya da imzalı uçlara çıkılamaz. Gerçek bağlantı gerektiren her
  şey Berk'in Mac'inde ilk kez çalışır; emir yürütme sahte borsayla
  (`tests/fake_binance.py`) sınanır.
* PyPI erişilebilir; kurulum ve testler burada çalışır. Proje 3.12+ istiyor.
* Bu oturumların yetkisi yeni GitHub deposu açmayı kapsamıyor.
* macOS'a özgü kod (`security`, `caffeinate`, `pmset`) burada
  çalıştırılamaz; testler bunları sahte süreçlerle sınar.

**Doğrulama kuralı:** Berk'e "çalıştır" demeden önce depoyu **GitHub'dan
geçici bir dizine temiz klonlayıp** `bash kurulum.sh demo-anahtar` (ya da
başka bir tek adım) ile Python bulma, sanal ortam, bağımlılık ve testleri
baştan çalıştır; macOS dışında tek adım "yalnızca Mac'te çalışır" deyip temiz
çıkar. Buradaki çalışma dizininde testlerin geçmesi kanıt değil (21 Eylül:
`.gitignore` kaynak paketini yuttu, burada geçti, Berk'te patladı).

**Arayüzü doğrulamanın yolu:** Chromium kurulu (`/opt/pw-browsers/chromium`),
Node Playwright genel kurulu; betik
`NODE_PATH=/opt/node22/lib/node_modules node betik.js` ile çalışır. Karalama
sunucusu (dizinde kalmaz, yeniden kur): gerçek `LiveMarket` (spread ısınması
için geçmişe kaydırılmış saatle 300 örnek), gerçek `PaperEngine`, Demo için
`FakeBinance` + `FakeUserStream`'e bağlı gerçek yürütücü; `Runtime(...)` ve
`AppState(runtime=...)` ile `create_app`'e verilir. Her koşudan önce temiz
veri diziniyle yeniden başlat. 1280 px'te `deviceScaleFactor` 1 ve 2, 390
px'te 2 ve 3; her sekmede yatay taşma, tek zamanlayıcı (istek sayısıyla),
konsol hatası. Gizli `<option>` beklerken `state: "attached"` kullan.

## 3. Berk'in Faz 5'te verdiği kararlar — kendi cümleleriyle

* **Faz 5'i başlattı** (23 Eylül): *"faz 5 e başla"*. Gerisi Claude'un
  seçtiği varsayılanlar (§6); Berk itiraz etmedi.
* **Demo anahtarını onayladı** (23 Eylül): karar kartında *"Binance Demo'da
  uygulama için yeni bir API anahtarı oluşturalım mı?"* sorusuna **"Evet"**.
* **Demo izinleri hakkında** (23 Eylül): *"ayarlar özelleştirilemiyor
  demoda, ayrıca para çekme açık değil öyle bir tik yok zaten."* Bu cümle
  yanlış bir güvenlik denetimini ortaya çıkardı (§5, madde 1; `c8d12ea`).
* **Faz 5, 23 Eylül 2026'da onaylandı.** Berk'in cümlesi: *"evet
  onaylıyorum"*.

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

## 4. Faz 5 ne yaptı (tek paragraf)

Risk kapılarından geçen emri Binance **Demo Mode**'a bir OTOCO listesi olarak
gönderen bir emir yürütücüsü yazıldı: giriş `LIMIT_MAKER` alış, giriş dolunca
borsanın koyduğu hedef ve stop. Kısmi dolum (en fazla 20 sn korumasız, sonra
OCO), stopun altında korumalı `LIMIT IOC` çıkış, sonucu bilinmeyen istek
(aynı kimlikle sorgu, asla yeniden gönderim), kopan akış, uyku, çökme ve
yeniden açılış, borsayla uzlaştırma ve acil durdurma yönetiliyor. İzin
listeli, yalnızca Demo'ya bağlanan imzalı istemci, WebSocket API hesap akışı,
Demo işlem sekmesi ve iki kurulum komutu eklendi. Kabul kriteri "kaos
testleri geçer": sahte borsayla 36 kaos testi, toplam **583 test**. Berk'in
Mac'inde gerçek Demo hesabında: `demo-anahtar` geçti (saat farkı 433 ms,
Demo komisyonu maker/taker %0.1), `demo-sina` geçti (OTOCO kabul edildi,
akıştan olay geldi, bekleyen hedef/stop kimlikle sorgulandı, iptal görüldü),
arayüzden elle verilen BTCUSDT emri 84597.19'dan doldu ve hedef 87000 / stop
83000 borsaya kondu. Ayrıntı ve gerekçeler `docs/FAZ5-DEMO-EMIR-YURUTME.md`.

**Eldeki komut satırı araçları**

| Araç | Ne yapar | Ağa çıkar mı |
|---|---|---|
| `albsat-fizibilite` | Veri indirir, filtreleri önbelleğe yazar, fizibilite taraması | Evet |
| `albsat-oruntu` | Örüntü keşfi, istatistik, backtest, rapor, kural deposu (`--maliyetsiz` teşhis) | Hayır |
| `albsat-arayuz` | Arayüz + canlı döngü + Demo yürütücü, yalnızca 127.0.0.1 (`--cevrimdisi` ile ağsız) | Evet |
| `albsat-telegram` | Telegram kurulumu, deneme mesajı, kaldırma | Evet (Telegram) |
| `albsat-anahtar` | Salt okuma anahtar kurulumu, komisyon ölçümü (`--olc`, `--sil`) | Evet (2 imzalı okuma) |
| `albsat-demo` | Demo anahtar kurulumu ve doğrulama; `--sina` uçtan uca sınama; `--sil` | Evet (Demo) |
| `albsat-tls-teshis` | Sertifika zinciri teşhisi | Evet |

## 5. Faz 5'te öğrenilen, koda bakarak görülmeyecek tuzaklar

1. **`canWithdraw` anahtarın izni değil, hesabın bayrağıdır.**
   `GET /api/v3/account` onu döndürür; Demo hesabı `true` veriyor, oysa
   Demo'da izinler değiştirilemiyor ve para çekme yok. İlk sürüm bunu
   "anahtarda çekim izni açık" sayıp Berk'in doğru anahtarını reddetti.
   Anahtarın çekim izni `/sapi/v1/account/apiRestrictions` →
   `enableWithdrawals` ile okunur (Faz 4'ün `signed.restriction_problems`).
   `keys.assert_no_withdrawal_permission` hâlâ `canWithdraw`'a bakıyor;
   yalnızca kendi testlerinde kullanılıyor ve yanıltıcı (§7, madde 4).
2. **İptal olayında emrin kimliği `C`'dedir.** `executionReport`'ta `c`
   iptal isteğinin kendi kimliği, `C` iptal edilen emrinki. Olay eşleştirme
   ikisine de bakmalı (`cli/demo.py` `_Events._matches`); yalnızca `c`'ye
   bakan kod iptali hiç görmez.
3. **OTOCO'nun bekleyen hedef/stopu kimlikle sorgulanabiliyor**
   (`PENDING_NEW`; 23 Eylül'de gerçek Demo'da görüldü). Sahte borsada
   `GET /api/v3/orderList` yok; liste sorgusu gerektiren kod yazarsan önce
   sahte borsaya ekle.
4. **Satış miktarı alış miktarı değildir.** Komisyon alınan coinden
   kesiliyor: 0.00056 BTC alışta hesaba 0.00055944 girdi. Satış
   `(alış × (1 − büyük oran)) + önceki toz`'un `stepSize`'a aşağı
   yuvarlanmışı (`planner.sell_quantity`). Berk'in Demo emrinde önceki toz
   (0.00000891) açığı kapattı ve satış 0.00056 oldu; toz olmasaydı 0.00055
   olurdu. `pendingQuantity = quantity` diye "sadeleştirme": borsa hedef ve
   stopu "yetersiz bakiye" ile reddeder, pozisyon korumasız kalır.
5. **Sonucu bilinmeyen istekte yeni kimlikle yeniden gönderim yok.** Zaman
   aşımı, 5xx, `-1006`/`-1007`'de istek işlenmiş olabilir; aynı kimlikle
   sorgulanır, `recvWindow + 5 sn` sonra `-2013` "ulaşmadı" demektir.
   Kimlik gönderimden **önce** diske yazılır.
6. **`demo-sina` arayüz açıkken çalıştırılmamalı.** Arayüzün uzlaştırması
   kaydında olmayan `albsat-` alışını yetim sayıp iptal eder.
7. **Demo defteri canlı defterden ayrıdır.** Limit-maker kapısı ve Demo
   pozisyonunun değeri Demo defterinin `@bookTicker` akışından okunur.
   Canlıda iki defter aynıdır; Demo'ya özgü bu ayrımı canlıya taşıma.
8. **Demo hesabı:** izin ayarları değiştirilemiyor; Binance ~5000 sahte USDT
   ve küçük bir BTC tozu veriyor. Bot yine yalnızca 100 USDT bütçe kullanır.
9. **`/api/demo/durum` Binance'e istek göndermez**; yürütücünün son durumunu
   döner (testi var). Arayüze eklenen her periyodik okuma da böyle olmalı.
10. **Süreç öldürme:** `pkill -f desen` kendi kabuğunu öldürür (çıkış 144).
    Sunucuları `setsid` ile başlat, PID dosyasıyla ya da
    `pkill -f '^/home/claude/alsat/.venv/bin/python /tmp'` gibi başa
    bağlanmış desenle durdur.
11. **Lint ve tip tabanı:** `ruff` 33, `mypy` 35 hata; hepsi Faz 1-3
    dosyalarında. Faz 4-5 dosyaları temiz; yeni kodu temiz tut.

**Faz 4'ten taşınan tuzaklar (hâlâ geçerli):**
yazma uçları yerel korumadan geçer (`X-Albsat-Istek: 1`, JSON, Host
127.0.0.1; `TestClient`'a `base_url="http://127.0.0.1"`); `LiveRunner.tick()`
her akış olayında çalışır, eklenen iş tekdüze saatle seyreltilmeli;
çevrimdışı mumda yalnızca borsa tarafı işler; toz sonucun içinde sayılır;
spread ısınması 5 dakika (ilk denemede Berk'e hatırlat); sayılar nokta
ondalıkla, `toLocaleString("tr-TR")` yok; alt şerit yüksekliği
`--alt-yukseklik`; `change` dinleyicisine isteğe bağlı parametreli fonksiyon
verme; sekme yenilemesi tek zamanlayıcı, kullanıcının yazdığı form yeniden
çizilmez; sırlar hata metninde, imzalı istek hatasında ve denetim kaydında
maskelenir; Anahtar Zinciri'ne sır standart girdiden yazılır; kendi üretilmiş
Ed25519 anahtar `/sapi/`, `/api/v3/` ve Demo'da çalışıyor.

**Faz 2-3'ten taşınan tuzaklar:** `CostAssumptions`'ta oran ile yüzde
karışık (bilerek); veri kontrolü kural kontrolünden önce; `Decimal` için
`format_for_api`; tur maliyeti giriş taker, hedef maker, stop taker;
Retina'da canvas boyutu geri okunmaz (`bb6c581`); "hâlâ bozuk" derse önce
eski sekmeyi düşün (`559b9eb`); kapanmamış mum tahmine giremez; fiyat, miktar
ve bakiyede `float` yok; yeni tarama turu çoklu test sorunudur.

## 6. Danışılmadan değiştirilmemesi gereken seçilmiş varsayılanlar

**Faz 5'te seçilenler** (gerekçeler `FAZ5-DEMO-EMIR-YURUTME.md` §4, §8):

1. **Giriş OTOCO, `LIMIT_MAKER`.** Hedef ve stopu borsa koyar; uygulama
   kapalıyken de korur. Elle emir limit-maker reddinde yeniden
   fiyatlanmaz; kural emri en fazla 3 kez en iyi alışa çekilir.
2. **Varsayılan stop piyasa stop (`STOP_LOSS`).** Limitli stop dolmayabilir;
   arayüzden değiştirilebilir.
3. **Tek değişmez:** elde coin varsa borsada canlı stop. Kısmi dolumda en
   fazla 20 sn korumasız, sonra OCO; stopun altındaysa `LIMIT IOC`, kayma en
   fazla %0.5. **Piyasa emri kullanılmaz.**
4. **Belirsiz sonuçta yeniden gönderim yok** (§5, madde 5).
5. **Kâğıt ve Demo ortak sınır aşımı:** biri aşınca ikisi durur.
6. **Bot bütçesi 100 USDT;** borsadaki bakiye büyük olsa da yalnızca bütçe
   kadar kullanılır, sonuçlar bütçeye göre.
7. **Borsada elle verilen emirlere dokunulmaz;** yetim `albsat-` alışı iptal
   edilir, yetim `albsat-` satışına dokunulmaz. Bütün emirleri silen
   `DELETE /api/v3/openOrders` bilerek izin listesinde yok.
8. **Her anahtar ayrı Anahtar Zinciri kaydında:** `albsat-binance` (salt
   okuma), `albsat-binance-demo` (Demo). Birini kurmak diğerine dokunmaz.

**Önceki fazlardan:**

9. Uygulama her açılışta **Sadece Öneri** modunda başlar; kâğıt ya da Demo'da
   olan coin kendiliğinden dönmez. Tam Otomatik için SPEC aynısını istiyor.
10. Ölçülemeyen koşul geçmiş sayılmaz; aralık dışı limit reddedilir,
    kırpılmaz.
11. Risk sınırı aşılınca bütün coinler Sadece Öneri'ye iner, bekleyen
    girişler iptal edilir, açık pozisyonun stop ve hedefi yerinde kalır;
    yeniden açmak yalnızca elle.
12. **ACİL DURDUR pozisyon kapatmaz;** modları indirir, borsadaki bekleyen
    girişleri iptal eder. Kapatmak ayrı seçim.
13. **Elle emirler ayrı sayılır;** kural performans sınamasına ve canlıya
    geçiş kapısına girmez. Kabul edilmemiş adaylar otomatik işlenmez.
14. Kâğıt dolum kuralları temkinli; BNB indirimi kullanılmaz.
15. Sunucu yalnızca 127.0.0.1; arayüz derleme adımsız düz HTML/CSS/JS
    (Node/npm kurdurma); kapsam A.

## 7. Yarım kalanlar ve Faz 6'ya taşınan uyarılar

1. **Faz 6 gerçek para kullanır.** Canlı anahtar oluşturma, canlı hesaba ilk
   emir, Yarı ya da Tam Otomatik'i açma: her biri öncesinde Berk'in **yazılı**
   onayı gerekir (kart düğmesi yetmez). İlk canlı emir en küçük emir
   sınırına yakın tutulmalı.
2. **Kabul edilmiş kural yok** (Faz 2). Canlıya geçiş kapısı "bir strateji
   kâğıtta 7 gün / 30 işlem" ister; şu an hiçbir strateji bu kapıyı geçemez,
   Yarı Otomatik'in önereceği bir kural sinyali de yok. Kapı ve güvenlik
   kontrolleri yazılabilir, ama Faz 6'nın bu durumda ne anlama geldiğini
   (örneğin yalnızca kapı + güvenlik + çok küçük elle canlı emir, ya da
   bekleme) **Berk'e sor**; kural uydurma, elle emri kapıya sayma.
3. **Canlı işlem anahtarı ayrı ve yeni olmalı:** canlı hesapta Spot işlem
   izni olan, çekim izni kapalı bir anahtar, kendi Anahtar Zinciri kaydında.
   Çekim izni `apiRestrictions` → `enableWithdrawals` ile denetlenmeli ve
   açıksa emir gönderilmemeli (§5, madde 1). Binance'in işlem izinli
   anahtarlarda IP kısıtıyla ilgili güncel kuralını belgeden oku (Berk'in ev
   IP'si değişebilir); varsayma.
4. **`keys.assert_no_withdrawal_permission`** `canWithdraw`'a bakıyor; ya
   `restriction_problems`'a dayanacak şekilde yeniden yaz ya da sil
   (`tests/test_keys.py` onu sınıyor). Canlı yolda kullanılmamalı.
5. **`DemoTrader` Demo dışındaki her ortamı bilerek reddeder**
   (`exchange/trading.py`). Canlı için açıkça adlandırılmış ayrı bir sınıf,
   ayrı izin listesi, **ayrı emir kimliği öneki** (`albsat-demo-` değil) ve
   ayrı kayıt tabloları (Demo'nunkiler `albsat.sqlite3` içinde
   `demo_pozisyonlar`, `demo_emirler`, `demo_dolumlar`) olmalı; Demo ve canlı
   kayıtları ve uzlaştırmaları hiç karışmamalı. `api/` katmanının imzalı koda
   erişmediğini denetleyen test bilinçli güncellenmeli, silinmemeli.
6. **Yalnızca kaos testiyle sınanan, gerçek borsada görülmemiş yollar:**
   kısmi dolum → OCO, stopun altında korumalı çıkış, yeniden koruma, süresi
   dolan stop, uykudan sonra uzlaştırma, 429/418. Gerçek Demo'da görülen:
   OTOCO kabulü, akış olayları, bekleyen bacak sorgusu, iptal, dolum sonrası
   hedef/stopun borsaya konması.
7. **Mac uyurken yeniden koruma yapılmaz** (stop ve hedef borsada çalışır).
   Canlıda bu daha önemli: `caffeinate` uyku engeli ve `pmset` pil uyarısı
   Berk'in Mac'inde hâlâ sınanmadı. **Telegram da Berk'in Mac'inde
   kurulmadı;** Yarı Otomatik'in Telegram onayı (`/onayla`) yer tutucu.
8. **Berk'in Demo hesabında açık bir pozisyon var:** BTCUSDT 15m, elle #2,
   0.00056 BTC, giriş 84597.19, hedef 87000, stop 83000. Demo'da kendiliğinden
   kapanır; yeni oturum bunu arıza sanmasın.
9. **Resmi SDK kullanılmadı** (gerekçe `FAZ5-DEMO-EMIR-YURUTME.md` §9);
   `binance-sdk-spot` `pyproject.toml`'da yorum satırında. `binance-connector`
   "deprecated", kullanma.
10. **Kilitli modlar:** Yarı Otomatik ve Tam Otomatik `modes/state.py`'de
    kilitli listede; Faz 5 yalnızca Demo'yu açtı.

## 8. Güvenlik (bunlar hiçbir fazda düşmez)

* **TLS sertifika doğrulaması hiçbir koşulda, geçici olarak bile
  kapatılmayacak.** Berk'in ağında HTTPS'i yeniden imzalayan bir katman var;
  uygulama `truststore` ile macOS güven deposunu kullanıyor. WebSocket
  istemcileri de aynı enjekte edilmiş güven deposunu kullanıyor; kendi SSL
  bağlamını veren kod bunu atlar.
* Sırlar Berk'in bilgisayarından çıkmıyor. Anahtarlar (`albsat-binance`,
  `albsat-binance-demo`) ve Telegram jetonu (`albsat-telegram`) macOS Anahtar
  Zinciri'nde; depoda, dosyada, günlükte, denetim kaydında, arayüzde yoklar.
  Ortam değişkenine sır konmuyor. Özel yarı Mac'te üretilir; Binance'e
  yalnızca genel yarı verilir.
* Berk 21 Eylül 2026'da sohbete bir Binance API anahtarı yapıştırmıştı;
  kullanılmadı, hiçbir yere yazılmadı, silmesi söylendi. Anahtar isteme,
  yazdırma, dosyaya koyma. API Key yalnızca Terminal'e gizli girişle yazılır.
* **Gerçek para ile emir gönderecek ve API anahtarı gerektirecek her adım
  öncesinde Berk'e sorulacak** (§7, madde 1).
* **Emir gönderen anahtarda çekim izni kapalı olmalı** ve bu
  `apiRestrictions` ile denetlenmeli, `canWithdraw` ile değil.
* İmzalı istemciler izin listesiyle çalışır; yalnızca `albsat-` önekli kendi
  emirlerini gönderir ve iptal eder.
* Binance'e giden istek hacmi sınırlı (yerel tavan 600 ağırlık/dk, 429'da
  bekle, 418'de dur, emir sayısı sınırının %50/%80'inde kapı kapanır).
  Beklenmedik büyüklükte bir iş çıkarsa program istek göndermeden durur.
* Komisyon oranı, sembol filtresi veya limit **koda sabit yazılmaz**.
* Arayüz yalnızca `127.0.0.1` dinler; yazma uçları yerel korumanın
  arkasında.
* Kaldıraç, margin, futures ve borçlanma kodu yok ve olmayacak. Emir
  yürütme için Binance MCP sunucusu kullanılmaz (SPEC §11).
