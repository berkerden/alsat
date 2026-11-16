# Gösteri botu

16 Kasım 2026, yalnızca `fable-5-2` dalında. Bu belge botun ilk işleminden
**önce** yazıldı; aşağıdaki "sonuç ne anlama gelmez" bölümü sonuç görülmeden
kayda geçsin diye.

## 1. Neden var

Berk şunu sordu: *"günde en az birkaç işlem yapacak şekilde bir bot yapamaz
mıyız"*. Cevap: teknik olarak evet, kazandıran bir tane hayır; ölçümler
(`FAZ2-SONUC.md`, `TUR2-SONUC.md`) bu sıklıkta kabul edilen bir kural olmadığını
gösterdi. Berk sonra, botun günde birkaç işlem yaptığını **parasız** görmek
istedi (*"1-2 yap"*; 2 numaralı seçenek: "yalnızca görmek isterseniz parasız
çalıştırırım, ama zarar yazmasını beklerim").

Gösteri botu bu isteğin karşılığıdır. **Bir kural değildir, bir arama değildir,
bir öneri değildir.** Mekanizmanın (limit giriş, hedef, stop, komisyon, risk
sınırları) sık işlemde nasıl davrandığını sahte parayla gösterir.

## 2. Ne yapar

* Yalnızca **Kâğıt İşlem** modundaki coinde çalışır.
* Her kapanan 1 saatlik mumda bakar: mum yükselişle kapandıysa (kapanış >
  açılış) kapanış fiyatına limit alış emri dener.
* Hedef ve stop girişten eşit uzaklıktadır: 1 saatlik ATR yüzdesi, en az %0,40.
* Giriş bir mum geçerlidir; pozisyon en fazla 4 mum tutulur, sonra piyasa
  fiyatından kapanır.
* Aynı anda en fazla bir gösteri emri olur.
* Emir, kural ve elle emirlerle **aynı risk kapılarından** geçer. Art arda kayıp
  ya da günlük zarar sınırı aşılınca bütün coinler Sadece Öneri'ye iner ve bot
  da durur; devam etmesi için coini yeniden Kâğıt İşlem'e almak (gerekirse art
  arda kayıp sayacını sıfırlamak) gerekir. Varsayılan sınırlarla bunun birkaç
  günde bir olması beklenir.

Yöntem bilerek tek ve sabittir. **Ayarı yoktur ve ayar eklenmeyecek.**
"Uzaklığı şöyle yapalım, mumu böyle seçelim" demek, kapanmış kural aramasını
başka kapıdan yeniden açmak olur (`FABLE-DEVIR.md` §4).

## 3. Kodla korunan sınırlar

| Sınır | Nerede | Test |
|---|---|---|
| Varsayılan kapalı; arayüzden açılır | `paper_api.py` `/api/kagit/gosteri`, durum `gosteri_botu_acik` | `test_varsayilan_olarak_kapalidir` |
| Yalnızca Kâğıt İşlem modunda | `LiveRunner.gosteri_step` | `test_sadece_oneri_modunda_emir_acmaz` |
| Demo ve canlı yürütücülere yol yok | `gosteri_step` yalnızca `PaperEngine.place` çağırır | `test_demo_ve_canli_yurutuculere_hic_ulasmaz` |
| Ayrı kaynak (`risk.SOURCE_SHOW = "gosteri"`), ayrı sayım | `report.summarize`, arayüzde üçüncü sütun | `test_gosteri_islemleri_kapiya_ve_kural_performansina_girmez` |
| Canlıya geçiş kapısına ve kural performansına girmez | `execution/gate.py` ve `PaperEngine.rule_check` yalnızca `SOURCE_RULE` sayar | aynı test |
| Kural deposuna ve birikimli aday sayısına (12.021) dokunmaz | bot `veri/kurallar.json` okumaz, yazmaz | — |

Bot yalnızca yerel kâğıt defterine yazar; Binance'e emir, anahtar ya da hesap
gerektiren hiçbir istek göndermez. Kullandığı tek veri, uygulamanın zaten
indirdiği herkese açık mumlardır.

## 4. Beklenti (sonuçtan önce yazıldı)

* Günde birkaç işlem (yükselen saatlik mum sayısına, dolumlara ve kayıp sonrası
  soğumaya göre değişir; hiç işlem olmayan gün de olur).
* İşlem başına ortalama net sonucun **eksi** olması, kabaca gidiş-dönüş maliyeti
  (%0,2-0,3) kadar. Yön bilgisi olmayan bir giriş yazı-tura gibidir; maliyet her
  işlemde ödenir.
* Tek tek kazanan günler ve haftalar olacaktır. Bunlar şanstır.

## 5. Sonuç ne anlama gelmez

* Bot bir süre artıda görünürse bu **bir kuralın kanıtı değildir.** Bu yöntem
  ön kayıtlı bir sınamadan geçmedi; geçmiş veride de sınanmadı (sınamak yeni bir
  aday demek olurdu ve 12.021'lik birikimli sayıya eklenirdi).
* Gösteri botunun sonucu canlıya geçiş kapısına sayılmaz ve sayılacak biçimde
  değiştirilmez. Bot Demo'ya ya da canlıya taşınmaz.
* "İşe yaradı, gerçek parayla deneyelim" demek için gereken yol aynıdır: sonuç
  görülmeden yazılmış bir ön kayıt, birikimli aday sayısıyla düzeltme ve Berk'in
  ayrı yazılı onayı. Bu belgedeki gözlem o yolun yerini tutmaz.

## 6. Berk'in Mac'inde

Mac'teki kurulum `main` dalını çalıştırıyor; gösteri botu orada yok. Mac'te
görmek için bu dalın Mac'e nasıl alınacağı önce Berk'le kararlaştırılır
(`FABLE-DEVIR.md` §8: veri klasörü, tek kopya kilidi ve ortak Anahtar Zinciri
tuzakları).

Açmak için: **Kâğıt işlem** sekmesi → **Modlar** kutusunun altındaki "Gösteri
botunu aç" düğmesi, sonra coini Kâğıt İşlem'e almak. Sonuçlar aynı sekmede
"Sonuçlar" kutusunun üçüncü sütununda, tek tek işlemler "Geçmiş"te "Gösteri"
kaynağıyla görünür.
