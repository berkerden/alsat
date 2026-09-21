# Devir notu — Faz 1'den Faz 2'ye

21 Eylül 2026. Bu not, koda ve diğer belgelere bakarak öğrenilemeyecek
şeyleri yeni oturuma aktarmak içindir. Şartname `SPEC.md`, mimari kararlar
`docs/FAZ0-MIMARI.md`, fizibilite sonucu `docs/FAZ1-FIZIBILITE.md`.

## Berk nasıl çalışıyor

* Mac kullanıyor, kod `~/Desktop/alsat` altında. Terminal, git ve GitHub
  akışları ona tanıdık değil.
* Kendi bilgisayarında atması gereken adımlar tek tek, sade ve elinden
  tutarak anlatılmalı. Çıplak komut listesi verme.
* Güncelleme komutu tek satıra indirgendi:
  `cd ~/Desktop/alsat && git pull && bash kurulum.sh`
* Kod GitHub'da `berkerden/alsat`, `main` dalında duruyor; Berk'in
  bilgisayarında da yedeği var.

## Bu çalışma ortamının kısıtları

* Ağ politikası **api.binance.com ve data.binance.vision adreslerini
  engelliyor** (proxy CONNECT'e 403 dönüyor). Buradan gerçek piyasa verisi
  indirilemez, tarama gerçek sayılarla çalıştırılamaz. Gerçek veri gerektiren
  her adımı Berk kendi Mac'inde çalıştırıyor, çıktıyı sohbete yapıştırıyor.
* PyPI erişilebilir, kurulum ve testler burada çalışır.
* Kapsayıcının varsayılan `python3`'ü 3.11 olabilir; proje 3.12+ istiyor.
  Sanal ortamı `python3.12 -m venv` ile kur.
* Bu oturumların yetkisi yeni GitHub deposu açmayı kapsamıyor; depoyu Berk
  açtı, `add_repo` ile bağlandı.

## Doğrulama kuralı

Berk'e "çalıştır" demeden önce depoyu **geçici bir dizine temiz klonlayıp**
kurulumu ve testleri orada çalıştır. Buradaki çalışma dizininde testlerin
geçmesi kanıt değil: 21 Eylül'de `.gitignore` içindeki sabitlenmemiş `data/`
kuralı `src/albsat/data/` kaynak paketini yuttu, burada testler geçti,
Berk'in bilgisayarında `ModuleNotFoundError` ile patladı.

## Kolay tekrar düşülecek tuzaklar

* **Arşiv zaman damgaları mikrosaniye.** Binance 1 Ocak 2025'ten itibaren
  `data.binance.vision` SPOT arşivlerinde mikrosaniye, `GET /api/v3/klines`
  ise milisaniye veriyor. `albsat.data.klines.normalize_epoch_ms` bunu
  çözüyor; zaman damgası okuyan yeni kod da oradan geçmeli. Aksi halde
  ardışık her mum çifti "boşluk" sanılır (bir kez oldu: 293.759 sahte
  boşluk).
* **Berk'in ağında HTTPS trafiğini yeniden imzalayan bir katman var.**
  Uygulama bu yüzden `truststore` ile macOS güven deposunu kullanıyor.
  Sertifika doğrulaması **hiçbir koşulda, geçici olarak bile kapatılmayacak**;
  gerçek emir gönderecek bir uygulamada araya girme riski doğurur. Teşhis
  aracı: `albsat-tls-teshis`.
* **Arşivler gün/ay bittikten sonra yayımlanır.** Bugünün dosyası kesin 404
  döner; `plan_archives` bugünü plana almıyor, kalanı REST tamamlıyor.
* **Kapanmamış mum tahmine giremez** (SPEC §11). Tek kapı
  `albsat.data.klines.closed_only`.
* **Fiyat, miktar ve bakiyede `float` yok, `Decimal` var** (SPEC §3).
  `albsat.core.money` `float` alırsa reddediyor.

## Güvenlik

* Sırlar Berk'in bilgisayarından çıkmıyor: anahtar macOS Keychain'de, `.env`
  yalnızca anahtarın yerini söylüyor, ortam değişkenine sır konmuyor.
* Berk 21 Eylül 2026'da sohbete bir Binance API anahtarı yapıştırdı. İptal
  etmesi söylendi; anahtar kullanılmadı, hiçbir dosyaya, commit'e veya
  hafızaya yazılmadı. Anahtar isteme, yazdırma, dosyaya koyma.
* Gerçek para ile emir gönderecek her adım öncesinde Berk'e sorulacak.
  Uygulama her zaman "Sadece Öneri" modunda açılıyor.

## Yarım kalanlar ve Faz 2'ye taşınan uyarılar

1. **Komisyon oranı hâlâ varsayım** (%0,1 / %0,1). Gerçek oran imzalı
   `GET /api/v3/account/commission` ile Faz 4'te ölçülecek; fizibilite
   tablosu o zaman yenilenmeli.
2. **BNB ile komisyon ödeme kararı verilmedi.** Varsayılan temkinli
   (indirimsiz). BNB bakiyesi tutmayı ve kendi fiyat riskini gerektirir.
3. **ATR medyanı sakin ve hareketli dönemleri birlikte ölçüyor.** Faz 2'de
   rejim ayrımı yapılmalı; sakin dönemlerde 15m'in eşiğin altına düşmesi
   bekleniyor. Tek bir ortalama sayıya güvenme.
4. **Kapsam A ile daraltıldı**: BTCUSDT ve SOLUSDT, yalnızca 15m ve 1h.
   Hedefler tek mum değil 2–4 mumluk pencerelerde. 1m ve 5m kapsam dışı;
   sessizce geri getirme, gerekirse Berk'e sor.
5. **OTOCO kısmi dolumda koruma sağlamıyor** (FAZ0 Risk #1). "Korumasız süre
   nöbetçisi" (`config/default.yaml` → `emir.korumasiz_azami_saniye`, 20 sn)
   onaylandı ama henüz yazılmadı; Faz 5'in işi.
6. **Faz 5 bağımlılığı** `binance-sdk-spot` `pyproject.toml` içinde yorum
   satırında bekliyor. `binance-connector` PyPI'da "deprecated"; internetteki
   örneklerin çoğu hâlâ onu kullanıyor, kullanma.
7. **Örüntü aramasında ileri bakış (look-ahead) tuzağına dikkat.** Veri
   Berk'in Mac'inde `~/Desktop/alsat/veri` altında indirilmiş ve doğrulanmış
   durumda; yeniden indirmeye gerek yok.
