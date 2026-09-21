# Faz 2 — Örüntü keşif motoru, backtest ve raporlar

**Tarih:** 21 Eylül 2026
**Kapsam:** BTCUSDT ve SOLUSDT, 15m ve 1h (Faz 1'de A seçeneğiyle daraltıldı)
**Şartname karşılığı:** SPEC.md §4.2, §4.8 ve §10'daki Faz 2 satırı
**Kabul kriteri (SPEC.md §10):** *"Look-ahead testleri geçer; rapor rastgele
girişle kıyas içerir."*

Bu belge motorun **nasıl kurulduğunu ve neden öyle kurulduğunu** anlatır.
Gerçek veriyle çalıştırılınca çıkan sayılar ayrı bir rapora
(`faz2-oruntu-sonuc.txt`) yazılır.

---

## 1. Faz 2 hangi soruyu cevaplıyor

Faz 1 şunu sordu: *bu coin ve periyotta tipik hareket, gidiş-dönüş maliyeti
karşılıyor mu?* Cevap 15m ve 1h için "evet, ama dar" oldu.

Faz 2 bir sonraki soruyu soruyor: **ölçülebilir bir avantaj var mı?** Yani
"şu koşullar oluştuğunda sonraki birkaç mumda fiyat, komisyon ödendikten
sonra da kazandıracak kadar tutarlı hareket ediyor mu?"

İki soru arasındaki fark önemli: birincisi mümkün olup olmadığını, ikincisi
gerçekten olup olmadığını ölçer.

---

## 2. Zaman çizelgesi — belgenin en önemli bölümü

Geçmişe dönük testlerde en sık yapılan ve sonucu en çok şişiren hata, sinyalin
hesaplandığı mumun kapanış fiyatından işleme girildiğini varsaymaktır. O fiyat,
mum kapandığı anda artık yoktur.

Motorun kullandığı çizelge:

```
mum i kapanır      ──►  sinyal hesaplanır (yalnızca i ve öncesi kullanılır)
mum i+1 açılır     ──►  POZİSYON BURADA AÇILIR
mum i+1 … i+N      ──►  hedefe mi stopa mı önce değildi, ölçülür
```

Tek bir mumluk bu kayma, gerçekte yakalanamayan bir fiyattan girmeyi ve
kârı olduğundan büyük göstermeyi engeller.

Isınma da aynı disiplinin parçası: EMA200 ve 200 mumluk yüzdelik pencereleri
dolmadan hiçbir mum olay sayılmaz (ilk 250 mum).

---

## 3. Özellik aileleri

Şartnamenin §4.2'de saydığı ailelerin hepsi karşılandı. Her özellik bir
**boole** değerdir: "bu mumda şu koşul sağlandı mı?"

| Aile | Örnekler | Sayı |
|---|---|---|
| Mum formasyonu | yutan mum, çekiç, kayan yıldız, doji, iç/dış mum, iğne | 11 |
| İndikatör durumu | RSI bölgeleri ve kesişimleri, MACD kesişimleri, Bollinger sıkışma/genişleme, EMA dizilimi, VWAP'a göre konum | 17 |
| Hacim | hacim patlaması, hacim kuruması, agresif alıcı/satıcı baskınlığı, hacim-fiyat uyumsuzluğu | 5 |
| Piyasa rejimi | ADX ile trend/yatay, yön, oynaklık rejimi (sakin/oynak) | 6 |
| Destek/direnç ve seviye | 20 mumluk kırılım, aralık ucu, yuvarlak rakam yakınlığı | 5 |
| Zaman etkisi | Asya/Avrupa/ABD seansı, hafta sonu, pazartesi, İstanbul saatiyle mesai ve gece | 7 |
| BTC etkisi | BTC'nin son 3 mumluk yönü, oynaklık rejimi, EMA dizilimi | 4 |

**Toplam 56 özellik** (BTCUSDT kendisi taranırken BTC etkisi ailesi eklenmez,
52 olur).

İki tasarım kararı:

* **Eşikler geçmişe göre.** "ATR yüksek mi?" sorusu tüm serinin medyanıyla
  değil, son 200 mumun dağılımıyla cevaplanıyor. Tüm seriye bakmak, Mart
  ayındaki bir mumun "sakin" sayılıp sayılmamasını Eylül'de olanlara bağlardı;
  bu da geleceğe bakmaktır.
* **Ölçüler orana çevrildi.** Fitil uzunluğu mumun kendi aralığına oranla
  tanımlı. Aksi halde aynı kural 43.000 USDT'lik BTC'de bir, 180 USDT'lik
  SOL'da başka bir anlama gelirdi.

Emir defteri dengesizliği (§4.2'nin opsiyonel maddesi) kapsam dışı: canlı
akış gerektiriyor, Faz 5'in işi. Yerine kline verisindeki **taker alış hacmi**
kullanıldı; agresif alıcı mı satıcı mı baskın olduğunu emir defterine bakmadan
söylüyor.

---

## 4. Hedef, stop ve tutma süresi

Faz 1'in belirleyici bulgusu şuydu: *kârlılığı hangi grafiğe bakıldığı değil,
pozisyonda ne kadar kalındığı belirliyor.* Motor bunu doğrudan uyguluyor:

* Hedef tek mumda değil, **2, 3 ve 4 mumluk pencerelerde** tanımlanıyor; her
  pencere ayrı ayrı taranıyor.
* Hedef ve stop sabit yüzde değil, **o mumdaki ATR'nin katı**. Varsayılan
  hedef 1,5×ATR, stop 1,0×ATR. Sabit yüzde sakin ve oynak dönemleri aynı
  kabul ederdi.
* SPEC.md §4.3'ün otomatik elemesi burada da çalışıyor: hedefi maliyet
  eşiğinin (komisyon + spread + kayma + güvenlik payı) altında kalan mumlar
  olay bile sayılmıyor. Sakin dönemlerde 15m'in eşiğin altına düşmesi
  bekleniyordu; rapor kaç mumun bu yüzden elendiğini yazıyor.

---

## 5. Maliyet modeli

Tek bir "gidiş-dönüş komisyonu" sabiti yanlış olurdu (FAZ0-MIMARI.md Risk #4).
Çıkışın nasıl olduğu komisyonu değiştirir:

| Çıkış | Emir tipi | Likidite | Kayma |
|---|---|---|---|
| Hedefe ulaşma | limit | maker | yok |
| Stopa çarpma | piyasa (STOP_LOSS tetiklenince) | taker | var |
| Süre dolması | piyasa | taker | var |

Giriş tarafında **temkinli varsayım** seçildi: giriş piyasa emri sayılıyor ve
taker komisyonu ödeniyor. Şartname §4.5 girişte `LIMIT_MAKER` tercih ediyor,
ama araştırmada doldurulacağı garanti olmayan bir emri dolmuş saymak sonuçları
sessizce iyimserleştirir — üstelik taraflı biçimde: limit emir en çok, fiyat
size doğru geldiğinde yani işlem aleyhinize dönerken dolar.

> **Not:** VIP0 seviyesinde maker ve taker oranı da %0,1'dir, yani bu seçim
> şu an yalnızca kaymayı etkiliyor. Oranlar farklılaştığında (Faz 4'te gerçek
> oran ölçülünce) fark büyür.

### Decimal / float ayrımı

Fiyat, miktar ve bakiye borsaya giderken **her zaman `Decimal`** (SPEC.md §3).
Araştırma hesabı borsaya gitmez; on binlerce mum üzerinde istatistik üretir ve
hız için `float` kullanır. Komisyon oranları yine `Decimal` maliyet motorundan
alınıyor. `tests/test_eventstudy.py` içindeki bir test, hızlı yolun `Decimal`
motoruyla birebir aynı sayıyı verdiğini dört komisyon senaryosunda doğruluyor;
iki ayrı doğruluk kaynağının sessizce ayrışması böyle engelleniyor.

---

## 6. İstatistiksel sağlamlık

SPEC.md §4.2'nin "zorunlu" dediği maddelerin karşılığı:

| Gereksinim | Nasıl karşılandı |
|---|---|
| Yalnızca kapanmış mum | `closed_only` tek kapı; ayrıca ısınma satırları olay sayılmıyor |
| Look-ahead / repaint testi | `tests/test_lookahead.py` — aşağıda ayrıntısı |
| Eğitim / doğrulama / test | Zamana göre %50 / %25 / %25, karıştırma yok |
| Walk-forward | Genişleyen pencereyle 4 katman; her katmanda eğitim testin öncesinde |
| Minimum örnek (n ≥ 50) | Varsayılan eşik; ayrıca **üst üste binmeyen** olay sayısı da raporlanıyor |
| Bootstrap güven aralığı | Yeniden örneklemeyle ortalama ve tek yönlü p-değeri, **yalnızca ayrılmış dönemden** |
| Çoklu test düzeltmesi | Benjamini–Hochberg, **denenen tüm adaylar** üzerinden |
| Deflated Sharpe | Bailey & López de Prado; `research/stats.py` |
| Rejim ve dönem kararlılığı | Çeyreklik kırılım + oynaklık rejimi özellikleri |
| Rastgele giriş ve al-ve-tut kıyası | Her raporda, bulgu çıkmasa bile |
| Aşırı uyum uyarıları | Her örüntü kartında açık cümlelerle |

Beş karar ayrıca açıklanmayı hak ediyor:

**Neden bootstrap, t-testi değil?** İşlem getirileri normal dağılmaz: stop
kayıpları kırpar, hedefler kârı sınırlar, kuyruklar kalındır. t-testi bu
dağılımda güven aralığını olduğundan dar gösterir.

**Neden "üst üste binmeyen olay"?** Bir olay 3 mum sürüyorsa, ardışık
mumlardaki iki sinyal aynı fiyat hareketini iki kez sayar. Bu, bağımsız kanıt
değildir ve güven aralığını sahte biçimde daraltır. İstatistiksel testler bu
yüzden yalnızca üst üste binmeyen olayları kullanıyor; rapor her iki sayıyı da
gösteriyor.

**Neden düzeltme tüm adaylar üzerinden?** Tarama binlerce adayı ucuz bir
süzgeçten geçirip yalnızca umut verenlerin p-değerini hesaplıyor. Süzgecin
kendisi de bir seçimdir. Düzeltmeyi yalnızca incelenen 100 aday üzerinden
yapmak, elemeyi bedavaya getirir ve sonucu iyimser gösterirdi. Elenenler tek
yönlü testte zaten en büyük p-değerlerine sahip olduğu için bu yaklaşım
matematiksel olarak da güvenli taraftadır.

**Neden düzeltme koşunun tamamı üzerinden?** Bir koşuda 12 bölüm var (2 sembol
× 2 periyot × 3 pencere). Her bölüm kendi içinde %10 yanlış buluş payıyla
düzeltilirse, ortada hiçbir şey yokken bile ortalama `12 × 0,10 ≈ 1,2` bölümde
bir "buluş" çıkar — bölüm içi düzeltme bunu göremez, çünkü kaç bölüm
çalıştırıldığını bilmez. Bu teorik bir kaygı değil: 22 Eylül 2026 koşusunda
tam olarak bir örüntü kabul edildi, yani şansın üreteceği sayının kendisi.
Kabul kararı artık tüm bölümlerin p-değerleri havuzlanarak veriliyor
(`scan.apply_global_correction`).

Bu düzeltmenin güzel yanı, kör bir sertleştirme olmaması: Benjamini–Hochberg
sıralamaya bakar, eşiğe değil. Bölümlerin hepsinde görünen gerçek bir kenar,
havuzda da sıralamanın üstünde kaldığı için hayatta kalır; yalnızca tek bir
bölümde parlayan bir şans ise deneme sayısının büyümesiyle elenir. İkisi de
`tests/test_scan.py` içinde ayrı ayrı test ediliyor.

**Neden keşif ve sınama ayrı dönemlerde?** Adaylar yalnızca **eğitim**
dilimine (ilk %50) bakılarak seçiliyor. Kabul kararını veren p-değeri ise
yalnızca **ayrılmış dönemden** (doğrulama + test, son %50) hesaplanıyor. İkisi
aynı veriyi kullanırsa seçimin kendisi kanıt sayılır: bir örüntü zaten "eğitim
döneminde iyi göründüğü için" seçilmiştir, aynı veride tekrar iyi görünmesi
yeni bir bilgi değildir. Tüm seriden hesaplanan sayılar raporda bağlam olarak
duruyor ama kararı vermiyor. Bunun bedeli test gücünde düşüş — karar yarım
veriye dayanıyor — ama yanlış bir "buldum"un bedeli daha ağır.

**Neden yineleme sayısı adaya göre değişiyor?** Bootstrap p-değerinin
ölçebileceği en küçük değer `1/(yineleme+1)`. Düzeltme eşiği bundan küçükse,
gerçekten güçlü bir örüntü hak ettiği halde reddedilir — çünkü p-değeri
sıfıra ne kadar yakın olduğunu söyleyemez. Motor bu durumu tanıyıp yalnızca
tabana dayanan örüntüleri eşiği çözecek yinelemeyle yeniden ölçüyor
(`stats.required_iterations`). Normalde hiçbir örüntü tabana oturmaz ve bu
tur hiç çalışmaz; maliyeti yalnızca gerçekten güçlü bir bulgu varken ödenir.

---

## 7. Look-ahead testleri nasıl kanıtlıyor

Faz 2'nin kabul kriteri bu. İki bağımsız yoldan gidiliyor:

**1. Geleceği bozma.** Veri 1.200'üncü mumdan sonra tanınmaz hale getiriliyor
(fiyatlar 3–9 katına, hacimler 17 katına çıkarılıyor). Sonra 1.200'den
öncekiyle karşılaştırılıyor: **tek bir özellik bile değişmemeli.** Testin
kendisi de kontrol ediliyor — sınırın hemen ötesindeki değerlerin *gerçekten*
değiştiği doğrulanıyor, yoksa test hiçbir şey kanıtlamazdı.

**2. Kesip yeniden hesaplama (repaint).** Seri kısaltılıp göstergeler baştan
hesaplanıyor. Ortak bölgedeki değerler birebir aynı çıkmalı. Farklı çıkan bir
gösterge, sonradan gelen veriyle geçmişi yeniden boyuyor demektir — grafikte
mükemmel görünür, canlıda öyle davranmaz.

Ayrıca:

* Sonuç tablosunun **en fazla pencere kadar** ileri baktığı, bir mum
  fazlasına bakmadığı ölçülüyor.
* Girişin sinyal mumunun kapanışı değil, sonraki mumun açılışı olduğu
  boşluklu (gap) bir seriyle doğrulanıyor.
* Kapanmamış mumun tabloya hiç girmediği doğrulanıyor.

Motorun **bulmaması gerekeni bulmadığı** da test ediliyor, ki bu belki daha
önemli: rastgele yürüyüş verisinde, maliyet de varken, düzeltmeden geçen
örüntü çıkmamalı. Ve tersi: içine bilerek bir kenar konmuş veride o kural
bulunabilmeli. Üçüncü bir test keşif ile sınamanın gerçekten ayrı dönemlerde
durduğunu ölçüyor: eğitim dilimi bittikten sonraki her şey tanınmaz hale
getirilip tarama yeniden çalıştırılıyor, aday sayısı ve ayrıntılı incelemeye
alınan örüntüler harfi harfine aynı çıkmalı. Testin kendi gücü de kontrol
edildi — eleme tüm seriye bakacak şekilde bozulduğunda test hemen düşüyor.

---

## 8. Backtest ve kıyas ölçütleri

Olay çalışması "her mumda girseydik ne olurdu" sorusunu cevaplar ve aynı
hareketi üst üste binen pencerelerle birden çok kez sayar. Backtest farklı bir
soru sorar: **tek bir hesapla, sırayla işlem yapsaydık ne olurdu?**

Motor aynı anda tek pozisyon tutuyor (`config/default.yaml` ile aynı), pozisyon
açıkken gelen sinyali atlıyor ve kaç sinyalin bu yüzden atlandığını raporluyor.
Çıkış kuralları olay tablosundan geliyor; iki ayrı yerde yazılmıyor.

Her rapor iki kıyas içeriyor:

* **Rastgele giriş** — aynı sayıda işlem, aynı hedef/stop, rastgele mumlarda,
  yüzlerce tekrar. Örüntünün *zamanlaması* bir şey katıyor mu?
* **Al-ve-tut** — dönem başında alıp sonunda satmak, komisyon düşülmüş.
  Örüntü, hiçbir şey yapmamaktan iyi mi?

Kıyaslar **kabul edilen örüntü olmasa da** rapora giriyor. "Hiçbir şey
bulunamadı" sonucunu okuyan kişinin taban çizgisine en çok o anda ihtiyacı var.

---

## 9. Bilerek seçilmiş temkinli varsayımlar

Bir araştırma motorunda her belirsizlik bir tercih noktasıdır. Hepsinde aleyhte
olan seçildi:

1. **Aynı mumda hem hedefe hem stopa değilirse stop kabul edilir.** Mum verisi
   hangisinin önce geldiğini söylemez; gerçek sıralama ancak tik verisiyle
   bilinir.
2. **Giriş piyasa emri sayılır.** Dolmama riski olan bir limit emri dolmuş
   saymak yerine, kesin dolan ama pahalı olanı varsayıldı.
3. **Kayma her piyasa çıkışında aleyhte uygulanır.**
4. **Çoklu test düzeltmesi tüm adaylar üzerinden yapılır.**
5. **İstatistik üst üste binmeyen olaylarla yapılır**, toplam olay sayısıyla
   değil.
6. **Isınma dönemi olay sayılmaz.**

Bu varsayımlar bir örüntüyü "bulunamaz" hale getirebilir. Alternatifi, canlıda
tutmayacak bir örüntüyü gerçek parayla öğrenmektir.

---

## 10. Bilinen sınırlar

* **Komisyon oranı hâlâ varsayım** (%0,1 / %0,1). Hesaba özel gerçek oran
  Faz 4'te imzalı `GET /api/v3/account/commission` ile ölçülecek; bu rapordaki
  her sayı o zaman yenilenmeli.
* **BNB ile komisyon ödeme kararı verilmedi.** Varsayılan indirimsiz.
* **Örüntüler en fazla iki özelliğin kesişimi.** Üçlü kombinasyonlar aday
  sayısını on binlere çıkarır ve her yeni aday çoklu test düzeltmesini
  sertleştirir; kazancı belirsiz, maliyeti kesin.
* **Makine öğrenmesi yok.** SPEC.md §4.2 bunu bilerek sonraki bir faza
  bırakıyor; eklenirse purged/embargoed cross-validation gerekir.
* **Spread sabit varsayıldı.** Gerçek spread emir defterinden ölçülür,
  Faz 5'in işi.
* **Kısmi dolum modellenmedi.** Araştırmada pozisyonun tamamı tek fiyattan
  açılıp kapanıyor. Gerçek kısmi dolum davranışı ve korumasız süre nöbetçisi
  Faz 5'te gelecek (FAZ0-MIMARI.md Risk #1).

---

## 11. Nasıl çalıştırılır

Mac'te, depo klasöründe:

```bash
bash kurulum.sh
```

Beşinci adım örüntü taramasıdır ve **internete çıkmaz**; veriyi diskteki
`./veri` klasöründen okur. Yalnızca taramayı tekrar çalıştırmak için:

```bash
bash kurulum.sh tarama
```

Doğrudan çalıştırmak ve ayar değiştirmek isteyenler için:

```bash
python -m albsat.cli.research \
  --semboller BTCUSDT SOLUSDT \
  --periyotlar 15m 1h \
  --pencereler 2 3 4 \
  --hedef-atr 1.5 --stop-atr 1.0
```

Sonuç `faz2-oruntu-sonuc.txt` dosyasına yazılır.

### Teşhis turu: ortada yön bilgisi var mı?

Normal tarama şunu sorar: *maliyet ödendikten sonra kâr kalıyor mu?* Cevap
hayır çıktığında geriye ayrılması gereken bir alt soru kalır: sebep maliyetin
ağırlığı mı, yoksa ortada hiç yön bilgisi olmaması mı? İkisi aynı şey değil.
Birincisi ise maliyeti düşürmek (BNB indirimi, maker giriş) bir yol açar;
ikincisi ise düşürülecek maliyetin altında bir kenar yok demektir ve hiçbir
indirim bir şey değiştirmez.

```bash
bash kurulum.sh teshis
```

Bu, önce normal taramayı, sonra komisyon / spread / kayma ve maliyet eşiğinin
tamamı sıfır sayılarak aynı taramayı çalıştırır; ikincisi
`faz2-teshis-sonuc.txt` dosyasına yazılır. Doğrudan:

```bash
python -m albsat.cli.research --maliyetsiz --rapor faz2-teshis-sonuc.txt
```

Teşhis turunda kabul edilen bir örüntü **kârlı olduğu anlamına gelmez**;
yalnızca ölçülebilir bir yön bilgisi bulunduğunu söyler. Raporun başlığı bunu
her seferinde yazıyor, çünkü bu ayrımın unutulması bu motorun engellemek için
var olduğu hatanın ta kendisi.

---

## 12. Sorumluluk notu

Bu motor kâr vaat etmez. Yaptığı iş, geçmiş veride bir avantaj olup olmadığını
ölçmek ve **olmadığında bunu açıkça söylemektir**. "Kabul edilen örüntü yok"
çıktısı bir başarısızlık değil, bir ölçüm sonucudur; üstelik yüzlerce aday
denendiğinde en sık beklenen sonuçtur.

Geçmiş veriye dayanan her çıkarım, piyasanın davranışı değişirse geçerliliğini
yitirir.
