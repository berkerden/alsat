# İkinci kural arama turu — ön kayıt

**Tarih:** 27 Eylül 2026
**Karar:** Berk, 27 Eylül 2026'da "2 yıllık veriyle tek turluk kural araması"
teklifine *"evet"* dedi.
**Çalıştırma:** Berk'in Mac'inde, uygulama kapalıyken
`cd ~/Desktop/alsat && git pull && bash kurulum.sh tur2`
**Sonuç (28 Eylül 2026):** kabul edilen örüntü yok; arama kapandı.
Ayrıntı `TUR2-SONUC.md`.

Bu belge tur çalıştırılmadan **önce** yazıldı ve depoya gönderildi. Tur burada
yazılanla birebir çalıştırılır. Sonuç görüldükten sonra hiçbir ayar
değiştirilmez; ayar değiştirip yeniden çalıştırmak yeni bir tur olur ve öyle
sayılır.

---

## 1. Soru

Faz 2, 204 günlük veriyle 6.372 aday denedi ve çoklu test düzeltmesinden geçen
örüntü bulamadı (`FAZ2-SONUC.md`). Bu tur tek bir soruyu sorar: **aynı arama,
yaklaşık 3,5 kat veriyle bir şey buluyor mu?**

Daha çok veri, gerçek ama küçük bir kenarın p-değerini küçültür; şansla
parlayan bir adayın işini kolaylaştırmaz. Faz 2'nin tavan argümanı (maliyetsiz
ölçülen en iyi aday bile maliyeti karşılamıyordu) bir şey çıkma ihtimalinin
düşük olduğunu söylüyor. Bu tur o ihtimali ölçmek için bir kez yapılır.

## 2. Faz 2 ile aynı kalanlar

| | |
|---|---|
| Semboller | BTCUSDT, SOLUSDT |
| Periyotlar | 15m, 1h |
| Hedef pencereleri | 2, 3, 4 mum |
| Hedef / stop | 1,5×ATR / 1×ATR |
| Aday uzayı | 56 boole özellik, tekler ve ikili kesişimler |
| En az olay | 50 |
| Ayrıntılı incelenen | her yönde en fazla 100 aday |
| Yineleme / tohum | 1.500 / 20260921 |
| Keşif / kabul | keşif ilk %50'de, kabul kararı yalnızca son %50'den |
| Maliyet | ölçülen komisyon (maker/taker %0,1), spread %0,01, kayma %0,02, güvenlik payı %0,05; hedef en az %0,28 |
| Yanlış buluş payı | %10 (Benjamini–Hochberg), bütün bölümler tek aile |

## 3. Değişenler

1. **Veri:** en yeni mumdan geriye **730 gün** (yaklaşık Eylül 2024 → Eylül
   2026), Binance'in herkese açık arşivinden. Veri bu pencereyi kapsamıyorsa
   tarama başlamaz (`albsat-oruntu --gun 730`).

   Keşif yarısı (yaklaşık Eylül 2024 → Eylül 2025) Faz 2'nin hiç görmediği
   dönemdir. Kabul yarısı Faz 2'nin baktığı 204 günü de içerir; aday uzayı Faz
   2 sonuçlarına bakılarak daraltılmadığı için bu seçimi etkilemez.

2. **Önceki tur sayılır:** Faz 2'nin **6.372** adayı çoklu test ailesine
   eklenir (`--onceki-aday 6372`). Tek bir örüntünün kabul eşiği
   `0,10 / (bu turun adayları + 6.372)` olur. Maliyetsiz teşhis turunun 9.215
   adayı sayılmaz: o tur kabul kararı vermez ve kural üretemez.

3. **Ölçüm düzeltmesi:** p-değeri bölümün kendi eşiğine göre ölçülüyordu (≈550
   aday → ≈11.000 yineleme → en küçük p ≈ 0,00009); kabul ise çok daha küçük
   aile eşiğiyle veriliyordu. Tabana oturan bir örüntü, ne kadar güçlü olursa
   olsun kabul edilemezdi. Artık tabana oturanlar aile eşiğini çözecek kadar
   yeniden ölçülüyor (`scan.refine_for_family`). Faz 2'deki en yakın aday
   (BTCUSDT 15m, 3 mum, kaçınma, p=0,00009) bu tabandaydı; ayrıntı
   `FAZ2-SONUC.md` §2'deki nottadır.

## 4. Kabul ölçütü

Motorun var olan ölçütü değişmedi: bir örüntü, aile üzerinden
Benjamini–Hochberg düzeltmesinden %10 yanlış buluş payıyla geçerse kabul
edilir. Kabul edilen "alınacak" örüntüler kural deposuna (`veri/kurallar.json`)
yazılır ve arayüz onları öneri olarak gösterir. Kabul edilen "kaçınılacak" bir
örüntü alış açmaz; yalnızca "yeni alım yapma" uyarısı olarak görünür.

## 5. Sonuca göre ne olacak

* **Kabul yok:** bu kapsamdaki giriş sinyali araması (BTCUSDT ve SOLUSDT, 15m
  ve 1h, tek ve ikili özellik, 2-4 mumluk hedef) kapanır. Aynı aramayı daha
  çok veriyle ya da ayarları değiştirerek yeniden çalıştırmak önerilmez.
  Uygulama izleme, hesap ve elle işlem aracı olarak kalır.
* **Yalnızca "kaçınılacak" kabul:** işlem kuralı çıkmamıştır; sonuç rapora ve
  hafızaya not düşülür, arama yine kapanır.
* **"Alınacak" kabul:** kural hiçbir parayla hemen kullanılmaz. Sıra şudur:
  önce kâğıt işlem; canlıya geçiş kapısı (en az 7 gün ve 30 kural işlemi, sonuç
  beklentiyle uyumlu ve ortalaması pozitif) geçilirse Yarı Otomatik, 10 USDT
  emir tavanıyla. Her adım Berk'in ayrı onayıyla.

## 6. Tekrar yok

* `bash kurulum.sh tur2`, rapor dosyası (`tur2-oruntu-sonuc.txt`) varsa yeniden
  çalışmaz.
* Veri indirilemezse ya da pencere eksikse tarama hiç başlamaz; tur sayılmaz.
  Tarama rapor yazılmadan kesilirse yeniden başlatmak aynı turdur.
* Faz 2'nin kural deposu `veri/kurallar-faz2.json` olarak bir kez kenara alınır.
* Bu turdan sonra kural deposu birikimli aday sayısını (6.372 + bu tur) taşır.
  Sonraki her tarama bu sayıyı kendiliğinden düzeltmeye ekler; teşhis turu
  sayıyı taşır ama artırmaz.
