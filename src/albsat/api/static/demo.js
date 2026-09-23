/* Demo işlem sekmesi (Faz 5).
 *
 * Sekme açıkken beş saniyede bir /api/demo/durum okunur. Bu okuma
 * Binance'e istek göndermez: sunucu, yürütücünün elindeki son durumu
 * (akıştan gelen dolumlar, son uzlaştırma) döndürür. Parasal değerler
 * sunucudan METİN olarak gelir ve öyle gösterilir.
 *
 * Borsaya giden her eylem (emir, iptal, kapat, uzlaştır) app.js'teki
 * gonder() ile gider ve önce kullanıcıya sorulur. Kullanıcının yazdığı
 * alanlar (elle emir, ayarlar) yenilemede yeniden çizilmez.
 */
(function () {
  "use strict";

  const A = window.Albsat;
  if (!A) return;
  const el = A.el;

  const YENILEME_MS = 5000;
  let zamanlayici = null;
  let acik = false;
  let yukleniyor = false;
  let sonDurum = null;
  const onceki = {};

  // --- yardımcılar -----------------------------------------------------

  function cizDegistiyse(id, veri, ciz) {
    const anahtar = JSON.stringify(veri);
    if (onceki[id] === anahtar) return;
    onceki[id] = anahtar;
    A.yaz(id, ciz(veri));
  }

  function yeniden(id) { delete onceki[id]; }

  function tire(deger) {
    return deger === null || deger === undefined || deger === "" ? "—" : String(deger);
  }

  function sayi(deger, basamak) {
    if (deger === null || deger === undefined) return "—";
    return deger.toFixed(basamak);
  }

  function sure(saniye) {
    if (saniye === null || saniye === undefined) return "—";
    if (saniye < 60) return sayi(saniye, 1) + " sn";
    return Math.floor(saniye / 60) + " dk " + Math.floor(saniye % 60) + " sn";
  }

  function tablo(basliklar, satirlar) {
    const sar = el("div", "tablo-kaydir");
    const t = el("table");
    const thead = el("thead");
    const tr = el("tr");
    basliklar.forEach(function (b) { tr.appendChild(el("th", null, b)); });
    thead.appendChild(tr);
    t.appendChild(thead);
    const tbody = el("tbody");
    satirlar.forEach(function (satir) {
      const str = el("tr");
      satir.forEach(function (hucre) {
        const td = el("td");
        if (hucre instanceof Node) td.appendChild(hucre);
        else if (hucre && typeof hucre === "object" && "metin" in hucre) {
          td.textContent = tire(hucre.metin);
          if (hucre.sinif) td.className = hucre.sinif;
        } else td.textContent = tire(hucre);
        str.appendChild(td);
      });
      tbody.appendChild(str);
    });
    t.appendChild(tbody);
    sar.appendChild(t);
    return sar;
  }

  function dugme(metin, sinif, tikla) {
    const d = el("button", "dugme " + (sinif || ""), metin);
    d.type = "button";
    d.addEventListener("click", async function () {
      d.disabled = true;
      try { await tikla(); } finally { d.disabled = false; }
    });
    return d;
  }

  async function eylem(yol, govde, basari) {
    try {
      const sonuc = await A.gonder(yol, govde);
      if (basari) A.bildir(typeof basari === "function" ? basari(sonuc) : basari, "iyi");
      await yenile();
      return sonuc;
    } catch (hata) {
      A.bildir(hata.message, "kotu");
      return null;
    }
  }

  function kapiListesi(kapilar) {
    const ul = el("ul", "kapi-liste");
    kapilar.forEach(function (k) {
      const li = el("li", k.gecti ? "kapi-acik" : "kapi-kapali");
      li.appendChild(el("span", "kapi-isaret", k.gecti ? "✓" : "✗"));
      const metin = el("span");
      metin.appendChild(el("strong", null, k.etiket + ": "));
      metin.appendChild(document.createTextNode(k.aciklama));
      li.appendChild(metin);
      ul.appendChild(li);
    });
    return ul;
  }

  function defterFiyati(sembol) {
    if (!sonDurum) return null;
    return sonDurum.defter.find(function (d) { return d.sembol === sembol; }) || null;
  }

  function hazir() {
    return !!(sonDurum && sonDurum.baglanti.kurulu);
  }

  // --- bölümler ----------------------------------------------------------

  function baglantiCiz(v) {
    const kap = document.createDocumentFragment();
    const d = el("div", "baglanti-serit");
    const b = v.baglanti;
    let sinif = "sari";
    let metin = b.hazir_degil || "Demo Mode hazır.";
    if (!b.kurulu) sinif = "kotu";
    else if (b.hazir) {
      sinif = "iyi";
      metin = "Demo Mode hazır: hesap akışı bağlı, borsayla uzlaştırıldı";
    }
    const durum = el("span", "baglanti-durum");
    durum.appendChild(el("span", "nokta " + sinif));
    durum.appendChild(el("span", null, metin));
    d.appendChild(durum);
    if (b.kurulu) {
      const ha = b.hesap_akisi;
      const da = b.defter_akisi;
      d.appendChild(el("span", "kart-etiket",
        "Hesap akışı: " + (ha && ha.abone ? "bağlı" : "kopuk")));
      d.appendChild(el("span", "kart-etiket",
        "Demo defteri: " + (da && da.bagli ? "bağlı" : "kopuk")));
      if (b.son_uzlastirma) {
        d.appendChild(el("span", "kart-etiket", "Son uzlaştırma " + b.son_uzlastirma));
      }
      d.appendChild(dugme("Borsayla uzlaştır", "dugme-ikincil dugme-kucuk", function () {
        return eylem("/api/demo/uzlastir", { onay: true }, function (s) { return s.ozet; });
      }));
    }
    kap.appendChild(d);
    if (b.kurulu && (b.uyarilar.length || Object.keys(b.elle_emirler).length)) {
      const k = A.kutu(null, "kutu-uyari");
      const elle = Object.keys(b.elle_emirler);
      if (elle.length) {
        k.appendChild(el("p", "sari",
          "Demo hesabında bu uygulamanın açmadığı emirler var (" +
          elle.map(function (s) { return s + " " + b.elle_emirler[s]; }).join(", ") +
          "). Uygulama bunlara dokunmaz ama bakiye hesabında onları da görür."));
      }
      if (b.uyarilar.length) k.appendChild(A.liste(b.uyarilar, "uyari-liste"));
      kap.appendChild(k);
    }
    return kap;
  }

  function modlarCiz(v) {
    const k = A.kutu("Hangi coin Demo'da?");
    k.appendChild(el("p", "kart-etiket",
      "Demo Mode'daki bir coinde kabul edilmiş bir kural sinyal verince emir Demo hesabına " +
      "kendiliğinden gider; elle Demo emri de yalnızca bu moddaki coinde açılır. Uygulama " +
      "her açılışta bütün coinleri Sadece Öneri'ye alır."));
    const kap = el("div", "mod-satirlar");
    v.modlar.forEach(function (m) {
      const satir = el("div", "mod-satir");
      satir.appendChild(el("span", "mod-sembol", m.sembol));
      satir.appendChild(el("span", m.mod === "demo" ? "iyi" : "kart-etiket", m.mod_tr));
      if (m.mod === "demo") {
        satir.appendChild(dugme("Sadece Öneri'ye al", "dugme-ikincil dugme-kucuk", function () {
          return eylem("/api/kagit/mod", { sembol: m.sembol, mod: "sadece_oneri" },
            m.sembol + " Sadece Öneri'ye alındı. Açık Demo pozisyonunun stop ve hedefi yerinde.");
        }));
      } else {
        const d = dugme("Demo Mode'a al", "dugme-kucuk", function () {
          return eylem("/api/kagit/mod", { sembol: m.sembol, mod: "demo" },
            m.sembol + " Demo Mode'da.");
        });
        if (!v.hazir) {
          d.disabled = true;
          d.title = v.neden || "";
        }
        satir.appendChild(d);
      }
      kap.appendChild(satir);
    });
    k.appendChild(kap);
    if (!v.hazir && v.neden) {
      k.appendChild(el("p", "kart-etiket", "Demo Mode şu an seçilemiyor: " + v.neden));
    }
    return k;
  }

  function hesapCiz(v) {
    const h = v.hesap;
    const k = A.kutu("Demo hesabı (bot bütçesi)");
    k.appendChild(A.alanlar([
      ["Bot bütçesi", h.baslangic_usdt + " USDT"],
      ["Nakit", h.nakit_usdt + " USDT"],
      ["Bekleyen emirde", h.kilitli_usdt + " USDT"],
      ["Serbest", h.serbest_usdt + " USDT"],
      ["Özsermaye", h.ozsermaye_usdt === null ? "fiyat yok" : h.ozsermaye_usdt + " USDT"],
      ["Getiri", A.yuzde(h.getiri_yuzde, 2) || "—", A.isaret(h.getiri_yuzde)],
      ["Borsadaki serbest USDT", h.borsa_serbest_usdt === null ? "—" : h.borsa_serbest_usdt + " USDT"],
    ]));
    if (h.coinler.length) {
      k.appendChild(tablo(["Coin", "Miktar", "Fiyat (Demo alış)", "Değer (USDT)"],
        h.coinler.map(function (c) { return [c.sembol, c.miktar, c.fiyat, c.deger_usdt]; })));
    }
    if (v.defter.length) {
      k.appendChild(el("h3", null, "Demo defteri ve canlı piyasa"));
      k.appendChild(tablo(["Coin", "Demo alış", "Demo satış", "Canlı alış", "Canlı satış", "Demo fiyat yaşı"],
        v.defter.map(function (f) {
          return [f.sembol, f.alis, f.satis, f.canli_alis, f.canli_satis,
            f.yas_sn === null ? "—" : sure(f.yas_sn)];
        })));
      k.appendChild(el("p", "kart-etiket",
        "Demo Mode'un emir defteri canlı piyasadan ayrıdır; fiyatlar birbirine yakın ama aynı " +
        "değildir. Emirler Demo defterinde eşleşir, bu yüzden giriş ve kapanış Demo " +
        "fiyatına göre yapılır."));
    }
    k.appendChild(el("p", "kart-etiket", v.butce_notu));
    k.appendChild(el("p", "kart-etiket", "Dönem " + h.donem_id + ", başlangıç " + h.baslangic +
      " (İstanbul)."));
    return k;
  }

  function riskCiz(v) {
    const r = v.risk;
    const k = A.kutu("Risk sınırları (Demo)");
    r.gostergeler.forEach(function (g) {
      const satir = el("div", "olcer");
      const ust = el("div", "olcer-ust");
      ust.appendChild(el("span", null, g.etiket));
      const yuzdeMi = g.birim.indexOf("%") === 0;
      ust.appendChild(el("span", "olcer-deger", yuzdeMi
        ? "%" + sayi(g.deger, 2) + " / %" + sayi(g.sinir, 2) + g.birim.slice(1)
        : sayi(g.deger, 0) + " / " + sayi(g.sinir, 0) + " " + g.birim));
      satir.appendChild(ust);
      const cubuk = el("div", "olcer-cubuk");
      const dolu = el("div", "olcer-dolu");
      const oran = g.oran === null ? 0 : Math.max(0, Math.min(1, g.oran));
      dolu.style.width = (oran * 100).toFixed(1) + "%";
      dolu.classList.add(oran >= 0.8 ? "olcer-kotu" : oran >= 0.5 ? "olcer-sari" : "olcer-iyi");
      cubuk.appendChild(dolu);
      satir.appendChild(cubuk);
      k.appendChild(satir);
    });
    k.appendChild(el("p", "kart-etiket",
      "Bugün net " + r.gunluk_net_usdt + " USDT. Günlük zarar sınırına kalan " +
      r.gunluk_kalan_usdt + " USDT. Limitler kâğıt işlemle ortaktır (Kâğıt işlem sekmesi → " +
      "Risk limitleri). Kâğıtta ya da Demo'da bir sınır aşılırsa ikisi birden durur; stoplar " +
      "yerinde kalır."));
    const artArda = r.gostergeler.find(function (g) { return g.ad === "art_arda_kayip"; });
    if (artArda && artArda.deger > 0) {
      const alt = el("div", "dugme-satir");
      alt.appendChild(dugme("Art arda kayıp sayacını sıfırla", "dugme-ikincil", function () {
        return eylem("/api/demo/art-arda-sifirla", { onay: true },
          "Demo art arda kayıp sayacı sıfırlandı.");
      }));
      k.appendChild(alt);
    }
    return k;
  }

  // Borsanın emir durumları (ve yürütücünün kendi ara durumları) Türkçe.
  const BACAK_DURUMU = {
    NEW: "borsada bekliyor",
    PARTIALLY_FILLED: "kısmen doldu",
    PENDING_NEW: "girişin dolmasını bekliyor",
    PENDING_CANCEL: "iptal ediliyor",
    FILLED: "doldu",
    CANCELED: "iptal edildi",
    EXPIRED: "sona erdi",
    EXPIRED_IN_MATCH: "eşleşmede iptal",
    REJECTED: "reddedildi",
    GONDERILIYOR: "gönderiliyor",
    BILINMIYOR: "yanıt gelmedi, sorgulanıyor",
    ULASMADI: "borsaya ulaşmadı",
  };

  function bacakDurumu(e) {
    const sinif = e.durum === "FILLED" ? "iyi"
      : (e.durum === "REJECTED" || e.durum === "ULASMADI" || e.durum === "BILINMIYOR") ? "sari"
        : null;
    const metin = (BACAK_DURUMU[e.durum] || e.durum) + (e.sebep ? " · " + e.sebep : "");
    return { metin: metin, sinif: sinif ? sinif + " sol" : "sol" };
  }

  function pozisyonKart(o) {
    const korumasiz = o.durum === "korumasiz";
    const k = A.kutu(null, korumasiz ? "kutu-uyari" : null);
    const bas = el("div", "kart-baslik");
    const sol = el("div");
    sol.appendChild(el("strong", null, o.sembol + " " + o.periyot));
    sol.appendChild(el("span", "kart-etiket", "  " + (o.kaynak === "kural" ? "Kural" : "Elle") +
      (o.kural && o.kaynak === "kural" ? " · " + o.kural : "") + " · #" + o.id));
    bas.appendChild(sol);
    bas.appendChild(el("span", "rozet " + (korumasiz ? "rozet-kotu"
      : o.durum === "korunuyor" ? "rozet-iyi" : "rozet-uyari"), o.durum_tr));
    k.appendChild(bas);
    if (o.aciklama) k.appendChild(el("p", korumasiz ? "sari" : "kart-etiket", o.aciklama));
    k.appendChild(A.alanlar([
      ["Giriş", o.giris],
      ["Hedef", o.hedef],
      ["Stop", o.stop + (o.stop_limit ? " (limit " + o.stop_limit + ")" : "")],
      ["Emir miktarı", o.miktar],
      ["Alınan", o.alinan],
      ["Ortalama giriş", o.ortalama_giris],
      ["Elde", o.elde],
      ["Anlık net", o.anlik_net_usdt === null ? null : o.anlik_net_usdt + " USDT",
        A.isaret(o.anlik_net_usdt === null ? null : parseFloat(o.anlik_net_usdt))],
      ["Stopta zarar", o.stop_zarari_usdt + " USDT", "kotu"],
      ["Giriş geçerli", o.gecerlilik],
      ["Stopsuz süre", o.korumasiz_toplam_sn > 0 ? sure(o.korumasiz_toplam_sn) : null,
        o.korumasiz_simdi_sn > 0 ? "sari" : null],
    ]));
    if (o.emirler.length) {
      k.appendChild(tablo(["Borsadaki emir", "Tür", "Taraf", "Fiyat", "Stop fiyatı", "Miktar", "Dolan", "Durum"],
        o.emirler.map(function (e) {
          return [e.rol, e.tur, e.taraf === "BUY" ? "ALIŞ" : "SATIŞ", e.fiyat, e.stop_fiyati,
            e.miktar, e.dolan, bacakDurumu(e)];
        })));
    }
    const alt = el("div", "dugme-satir");
    if (o.iptal_edilebilir) {
      alt.appendChild(dugme("Girişi iptal et", "dugme-ikincil dugme-kucuk", function () {
        return eylem("/api/demo/iptal", { id: o.id },
          o.sembol + " giriş emrine iptal gönderildi.");
      }));
    }
    if (o.kapatilabilir) {
      alt.appendChild(dugme("Pozisyonu kapat", "dugme-tehlike dugme-kucuk", function () {
        if (!window.confirm(o.sembol + " Demo pozisyonu kapatılsın mı? Borsadaki stop ve hedef " +
          "iptal edilir, elde kalan coin Demo defterinde en iyi alıştan en fazla ayarlardaki " +
          "kayma kadar aşağıda satılır.")) return Promise.resolve();
        return eylem("/api/demo/kapat", { id: o.id }, o.sembol + " kapatılıyor.");
      }));
    }
    if (alt.childNodes.length) k.appendChild(alt);
    return k;
  }

  function aktifCiz(liste) {
    const kap = el("div");
    const k = A.kutu("Açık Demo pozisyonları ve bekleyen emirler");
    if (!liste.length) {
      k.appendChild(el("p", "kart-etiket", "Yok."));
      kap.appendChild(k);
      return kap;
    }
    k.appendChild(el("p", "kart-etiket",
      "Giriş dolunca stop ve hedef borsaya konur ve orada bekler: bu uygulama kapansa da " +
      "Mac uyusa da stop borsada çalışır. Giriş yarım dolarsa kalan kısım ayarlardaki süre " +
      "kadar beklenir, sonra iptal edilip dolan kısım korunur."));
    kap.appendChild(k);
    liste.forEach(function (o) { kap.appendChild(pozisyonKart(o)); });
    return kap;
  }

  function ozetAlanlari(o) {
    const sebepler = Object.keys(o.cikis_sebepleri).map(function (s) {
      return s + " " + o.cikis_sebepleri[s];
    }).join(", ");
    const kap = el("div");
    kap.appendChild(A.alanlar([
      ["İşlem", String(o.islem)],
      ["Kazanan", o.islem ? o.kazanan + " (" + A.oran(100 * o.isabet_orani, 0) + ")" : "—"],
      ["Net", o.net_usdt + " USDT", A.isaret(parseFloat(o.net_usdt))],
      ["Ortalama net", A.yuzde(o.ortalama_net_yuzde, 3) || "—", A.isaret(o.ortalama_net_yuzde)],
      ["Ödenen komisyon", o.komisyon_usdt + " USDT"],
      ["Stopsuz toplam", sure(o.korumasiz_toplam_sn)],
      ["En uzun stopsuz", sure(o.korumasiz_azami_sn)],
    ]));
    if (sebepler) kap.appendChild(el("p", "kart-etiket", "Çıkışlar: " + sebepler));
    return kap;
  }

  function sonuclarCiz(v) {
    const k = A.kutu("Demo sonuçları (bu dönem)");
    const iki = el("div", "iki-sutun");
    [["Kural işlemleri", v.ozet.kural], ["Elle işlemler", v.ozet.elle]].forEach(function (c) {
      const s = el("div");
      s.appendChild(el("h3", null, c[0]));
      s.appendChild(ozetAlanlari(c[1]));
      iki.appendChild(s);
    });
    k.appendChild(iki);
    k.appendChild(el("p", "kart-etiket",
      "Sonuçlar borsanın bildirdiği gerçek dolumlardan ve gerçek komisyondan hesaplanır. " +
      "Stopsuz süre: elde coin varken borsada stop emrinin bulunmadığı toplam süre."));
    k.appendChild(el("h3", null, "Komisyon"));
    Object.keys(v.maliyet).forEach(function (s) {
      k.appendChild(el("p", "kart-etiket", s + ": " + v.maliyet[s]));
    });
    return k;
  }

  function gecmisCiz(liste) {
    const k = A.kutu("Kapanan Demo işlemleri");
    const a = el("a", "dugme dugme-ikincil", "Dolumları CSV indir (Excel)");
    a.href = "/api/demo/islemler.csv";
    a.setAttribute("download", "");
    k.appendChild(a);
    if (!liste.length) {
      k.appendChild(el("p", "kart-etiket", "Henüz kapanan Demo işlemi yok."));
      return k;
    }
    k.appendChild(tablo(
      ["Açılış", "Coin", "Kaynak", "Ort. giriş", "Çıkış", "Sonuç", "Net USDT", "Net %"],
      liste.map(function (o) {
        const kapandi = o.durum === "kapandi";
        return [
          o.olusturma, o.sembol + " " + o.periyot, o.kaynak === "kural" ? "Kural" : "Elle",
          o.ortalama_giris, kapandi ? o.cikis_fiyati : "—",
          kapandi ? o.cikis_sebebi : { metin: o.durum_tr + (o.aciklama ? ": " + o.aciklama : ""),
            sinif: "kart-etiket sol" },
          kapandi ? { metin: o.net_usdt, sinif: A.isaret(parseFloat(o.net_usdt)) } : "—",
          kapandi ? { metin: A.yuzde(o.net_yuzde, 3), sinif: A.isaret(o.net_yuzde) } : "—",
        ];
      })));
    return k;
  }

  function ayrintiCiz(b) {
    const kap = document.createDocumentFragment();
    if (!b.kurulu) {
      kap.appendChild(el("p", "kart-etiket", b.hazir_degil || "Demo anahtarı kurulu değil."));
      return kap;
    }
    const butce = b.istek_butcesi || {};
    const sayac = b.emir_sayaci || {};
    const ha = b.hesap_akisi || {};
    const da = b.defter_akisi || {};
    kap.appendChild(A.alanlar([
      ["Ortam", b.ortam],
      ["Adres", b.adres],
      ["Saat farkı", b.saat_farki_ms === null ? "—" : b.saat_farki_ms + " ms"],
      ["Hesap akışı", ha.abone ? "bağlı" : "kopuk", ha.abone ? "iyi" : "kotu"],
      ["Akış yeniden bağlanma", tire(ha.yeniden_baglanma)],
      ["Akıştan gelen olay", tire(ha.olay)],
      ["Demo defteri akışı", da.bagli ? "bağlı" : "kopuk", da.bagli ? "iyi" : "sari"],
      ["İstek (bu dakika)", tire(butce.yerel_1dk) + " / " + tire(butce.yerel_tavan)],
      ["Borsa sayacı", tire(butce.borsa_1dk)],
      ["Emir (10 sn)", tire(sayac.son_10sn) + " / " + tire(sayac.sinir_10sn)],
      ["Emir (gün)", tire(sayac.son_1gun) + " / " + tire(sayac.sinir_gun)],
    ]));
    kap.appendChild(el("p", "kart-etiket",
      "İstek ve emir sayıları bu uygulamanın kendi tavanıyla sınırlı; tavana gelirse istek " +
      "gönderilmeden beklenir. Borsa 429 derse söylediği süre boyunca hiç istek gitmez, " +
      "418 derse engel bitene kadar Demo işlem durur."));
    const bakiye = Object.keys(b.bakiyeler);
    if (bakiye.length) {
      kap.appendChild(el("h3", null, "Borsadaki Demo bakiyesi"));
      kap.appendChild(tablo(["Varlık", "Serbest", "Emirde kilitli"],
        bakiye.map(function (v) { return [v, b.bakiyeler[v].serbest, b.bakiyeler[v].kilitli]; })));
    }
    if (b.son_uzlastirma_ozet) kap.appendChild(el("p", "kart-etiket", b.son_uzlastirma_ozet));
    if (b.son_hata) kap.appendChild(el("p", "sari", "Son hata: " + b.son_hata));
    if (ha.son_hata) kap.appendChild(el("p", "sari", "Hesap akışı: " + ha.son_hata));
    if (butce.son_hata) kap.appendChild(el("p", "sari", "İstek bütçesi: " + butce.son_hata));
    if (b.olaylar && b.olaylar.length) {
      kap.appendChild(el("h3", null, "Son olaylar"));
      kap.appendChild(A.liste(b.olaylar.slice(0, 15)));
    }
    return kap;
  }

  // --- elle Demo emri --------------------------------------------------------------

  const form = document.getElementById("demo-elle-form");

  function elleBaslik() {
    document.getElementById("demo-elle-baslik").textContent =
      "Elle Demo emri · " + A.sembol() + " " + A.periyot();
    const f = defterFiyati(A.sembol());
    const ipucu = document.getElementById("demo-elle-ipucu");
    if (!hazir()) {
      ipucu.textContent = sonDurum ? (sonDurum.baglanti.hazir_degil || "") : "";
      return;
    }
    ipucu.textContent = f && f.alis
      ? "Demo defterinde en iyi alış " + f.alis + ", en iyi satış " + f.satis +
        ". Giriş en iyi satışın altında olmalı; yoksa borsa reddeder."
      : "Demo defterinin fiyatı henüz gelmedi.";
  }

  function planCiz(plan) {
    const kap = el("div");
    kap.appendChild(el("h3", null, "Borsaya gidecek emirler"));
    if (!plan.gecerli) {
      kap.appendChild(el("p", "sari", "Emir borsanın kurallarına uymuyor:"));
      kap.appendChild(A.liste(plan.sorunlar, "uyari-liste"));
      return kap;
    }
    kap.appendChild(tablo(["Emir", "Tür", "Taraf", "Fiyat", "Stop fiyatı", "Miktar"],
      plan.emirler.map(function (e) {
        return [e.rol, e.tur, e.taraf, e.fiyat, e.stop_fiyati, e.miktar];
      })));
    kap.appendChild(el("p", "kart-etiket",
      "Giriş dolunca satılacak miktar " + plan.satis_miktari + " (alışta komisyon coin " +
      "olarak düşülür; kalan küsurat bir sonraki satışa eklenir)."));
    return kap;
  }

  function elleSonucCiz(veri, gonderildi) {
    const izin = veri.karar.izin && (!veri.plan || veri.plan.gecerli);
    const k = el("div", "elle-sonuc " + (izin ? "" : "kutu-uyari"));
    let baslik = veri.karar.ozet;
    if (gonderildi) baslik = veri.mesaj || "Demo emri gönderildi.";
    else if (veri.mesaj) baslik = veri.mesaj;
    k.appendChild(el("p", izin ? "iyi" : "sari", baslik));
    if (veri.yuvarlama.length) {
      k.appendChild(el("p", "kart-etiket", "Fiyatlar borsanın adımına yuvarlandı:"));
      k.appendChild(A.liste(veri.yuvarlama));
    }
    const poz = veri.karar.pozisyon;
    if (poz) {
      k.appendChild(A.alanlar([
        ["Giriş", veri.fiyatlar.giris],
        ["Hedef", veri.fiyatlar.hedef],
        ["Stop", veri.fiyatlar.stop],
        ["Hedefte net", A.yuzde(veri.karar.hedef_net_yuzde, 3), A.isaret(veri.karar.hedef_net_yuzde)],
        ["Miktar", poz.miktar],
        ["Tutar", poz.tutar_usdt + " USDT"],
        ["Stopta zarar", poz.stop_zarari_usdt + " USDT", "kotu"],
        ["Bağlayan", poz.baglayici_tr],
      ]));
      if (poz.aciklama) k.appendChild(el("p", "kart-etiket", poz.aciklama));
    }
    if (veri.plan) k.appendChild(planCiz(veri.plan));
    k.appendChild(el("h3", null, "Risk ve Demo kapıları"));
    k.appendChild(kapiListesi(veri.karar.kapilar));
    return k;
  }

  form.addEventListener("submit", async function (olay) {
    olay.preventDefault();
    const hangi = olay.submitter && olay.submitter.dataset.eylem === "gonder" ? "gonder" : "onizle";
    const alan = new FormData(form);
    const govde = {
      sembol: A.sembol(), periyot: A.periyot(),
      giris: (alan.get("giris") || "").trim(),
      hedef: (alan.get("hedef") || "").trim(),
      stop: (alan.get("stop") || "").trim(),
    };
    if (hangi === "gonder" && !window.confirm(govde.sembol + " için Binance Demo Mode hesabına " +
      "alış emri gönderilsin mi? (Sahte para; giriş " + govde.giris + ", hedef " + govde.hedef +
      ", stop " + govde.stop + ")")) return;
    const dugmeler = form.querySelectorAll("button");
    dugmeler.forEach(function (d) { d.disabled = true; });
    try {
      let veri;
      if (hangi === "onizle") {
        veri = await A.getir("/api/demo/on-izleme?" + new URLSearchParams(govde).toString());
        A.yaz("demo-elle-sonuc", elleSonucCiz(veri, false));
      } else {
        govde.gecerlilik_mum = parseInt(alan.get("gecerlilik_mum"), 10) || 1;
        govde.azami_tutma_mum = parseInt(alan.get("azami_tutma_mum"), 10) || 4;
        veri = await A.gonder("/api/demo/emir", govde);
        const gitti = !!veri.pozisyon;
        A.yaz("demo-elle-sonuc", elleSonucCiz(veri, gitti));
        if (gitti) {
          A.bildir(veri.pozisyon.sembol + " Demo giriş emri: " + veri.pozisyon.durum_tr + ".", "iyi");
          await yenile();
        } else {
          A.bildir("Demo emri gönderilmedi: " + (veri.mesaj || veri.karar.ozet), "kotu");
        }
      }
    } catch (hata) {
      A.yaz("demo-elle-sonuc", A.hataKutusu(hata));
    } finally {
      dugmeler.forEach(function (d) { d.disabled = false; });
    }
  });

  document.getElementById("demo-elle-fiyat").addEventListener("click", function () {
    const f = defterFiyati(A.sembol());
    if (f && f.alis) form.elements.giris.value = f.alis;
    else A.bildir("Demo defterinin fiyatı henüz yok.", "kotu");
  });

  // --- ayarlar (yalnızca açılınca ve kaydedince çizilir) ------------------------------

  function ayarlariCiz(veri) {
    const f = el("form", "limit-form");
    f.setAttribute("autocomplete", "off");
    const ilk = {};
    veri.alanlar.forEach(function (a) {
      const deger = String(veri.degerler[a.ad]);
      ilk[a.ad] = deger;
      const satir = el("label", "limit-satir");
      satir.appendChild(el("span", "limit-ad", a.etiket));
      let giris;
      if (a.tur === "secim") {
        giris = el("select");
        a.secenekler.forEach(function (s) {
          giris.appendChild(new Option(s.deger === "STOP_LOSS" ? "Piyasa stop" : "Limitli stop", s.deger));
        });
      } else {
        giris = el("input");
        giris.setAttribute("inputmode", a.tur === "tam" ? "numeric" : "decimal");
      }
      giris.name = a.ad;
      giris.value = deger;
      satir.appendChild(giris);
      if (a.tur === "secim") {
        a.secenekler.forEach(function (s) { satir.appendChild(el("span", "kart-etiket", s.aciklama)); });
      } else {
        satir.appendChild(el("span", "kart-etiket", a.aciklama + " Aralık " + a.aralik + "."));
      }
      f.appendChild(satir);
    });
    const kaydet = el("button", "dugme", "Değişiklikleri kaydet");
    kaydet.type = "submit";
    f.appendChild(kaydet);
    f.appendChild(el("p", "kart-etiket",
      "Değişiklik yeni emirlerde geçerli olur; borsada bekleyen emirler değişmez."));
    f.addEventListener("submit", async function (olay) {
      olay.preventDefault();
      const degerler = {};
      Array.prototype.forEach.call(f.elements, function (g) {
        if (g.name && g.value.trim() !== ilk[g.name]) degerler[g.name] = g.value.trim();
      });
      if (!Object.keys(degerler).length) {
        A.bildir("Değişen bir değer yok.", null);
        return;
      }
      kaydet.disabled = true;
      try {
        const sonuc = await A.gonder("/api/demo/ayarlar", { degerler: degerler });
        A.bildir(Object.keys(degerler).length + " ayar güncellendi.", "iyi");
        A.yaz("demo-ayarlar", ayarlariCiz(sonuc));
      } catch (hata) {
        A.bildir(hata.message, "kotu");
        kaydet.disabled = false;
      }
    });
    return f;
  }

  function detayAcik(id) {
    const d = document.getElementById(id);
    return d && d.open;
  }

  document.getElementById("demo-ayar-kutu").addEventListener("toggle", function () {
    if (this.open && sonDurum) A.yaz("demo-ayarlar", ayarlariCiz(sonDurum.ayarlar));
  });
  document.getElementById("demo-ayrinti-kutu").addEventListener("toggle", function () {
    if (this.open && sonDurum) { yeniden("demo-ayrinti"); cizDegistiyse("demo-ayrinti", sonDurum.baglanti, ayrintiCiz); }
  });

  document.getElementById("demo-sifirla-form").addEventListener("submit", async function (olay) {
    olay.preventDefault();
    const onay = (new FormData(this).get("onay") || "").trim();
    const sonuc = await eylem("/api/demo/hesap-sifirla", { onay: onay }, function (s) {
      return "Demo bot dönemi sıfırlandı; dönem " + s.donem_id + " başladı.";
    });
    if (sonuc) this.reset();
  });

  // --- yenileme ------------------------------------------------------------------

  async function yenile() {
    if (yukleniyor) return;
    yukleniyor = true;
    try {
      const d = await A.getir("/api/demo/durum");
      const ilkKez = sonDurum === null;
      sonDurum = d;
      A.modRozeti(d.modlar);
      cizDegistiyse("demo-baglanti", { baglanti: d.baglanti }, baglantiCiz);
      cizDegistiyse("demo-modlar", {
        modlar: d.modlar, hazir: d.baglanti.hazir && d.canli,
        neden: d.canli ? d.baglanti.hazir_degil : "Canlı veri yok: arayüz çevrimdışı açıldı.",
      }, modlarCiz);
      cizDegistiyse("demo-hesap", { hesap: d.hesap, defter: d.defter, butce_notu: d.butce_notu },
        hesapCiz);
      cizDegistiyse("demo-risk", { risk: d.risk }, riskCiz);
      cizDegistiyse("demo-aktif", d.aktif, aktifCiz);
      cizDegistiyse("demo-sonuclar", { ozet: d.ozet, maliyet: d.maliyet }, sonuclarCiz);
      cizDegistiyse("demo-gecmis", d.son_kapanan, gecmisCiz);
      if (detayAcik("demo-ayrinti-kutu")) cizDegistiyse("demo-ayrinti", d.baglanti, ayrintiCiz);
      if (ilkKez && detayAcik("demo-ayar-kutu")) A.yaz("demo-ayarlar", ayarlariCiz(d.ayarlar));
      elleBaslik();
    } catch (hata) {
      yeniden("demo-baglanti");
      A.yaz("demo-baglanti", A.hataKutusu(hata));
    } finally {
      yukleniyor = false;
    }
  }

  function etkin(acilsin) {
    if (acilsin && acik) { yenile(); return; }
    acik = acilsin;
    if (zamanlayici) { clearInterval(zamanlayici); zamanlayici = null; }
    if (acik) {
      yenile();
      zamanlayici = setInterval(function () {
        // Sekme arka plandayken istek atma; öne gelince ilk tur yeniler.
        if (!document.hidden) yenile();
      }, YENILEME_MS);
    }
  }

  window.Demo = { etkin: etkin, yenile: function () { if (acik) return yenile(); } };
})();
