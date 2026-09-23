/* Canlı işlem sekmesi (Faz 6): Binance canlı hesap, GERÇEK PARA.
 *
 * Sekme açıkken beş saniyede bir /api/canli/durum okunur. Bu okuma
 * Binance'e istek göndermez: sunucu, yürütücünün elindeki son durumu
 * döndürür. Parasal değerler sunucudan METİN olarak gelir ve öyle gösterilir.
 *
 * Gerçek parayla sonuç doğuran her eylem iki kez sorulur: coini Yarı
 * Otomatik'e almak ve elle canlı emir coin adının yazılmasını ister; öneriyi
 * göndermek ve pozisyonu kapatmak onay penceresi açar. Tam Otomatik yalnızca
 * canlıya geçiş kapısını geçen bir kural varsa açılabilir; kapıyı elle aşan
 * bir düğme yoktur. Kullanıcının yazdığı alanlar yenilemede yeniden çizilmez.
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
  // Tam Otomatik özeti hangi coin için açık (yenilemede kapanmasın) ve
  // özetteki onay kutusuna yazılan (özet yeniden çizilirse silinmesin).
  let ozetAcik = null;
  let tamOnayMetni = "";

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

  function sure(saniye) {
    if (saniye === null || saniye === undefined) return "—";
    if (saniye < 60) return saniye.toFixed(1) + " sn";
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

  // Coin adı yazdırarak onay (gerçek para). Boş dönerse vazgeçildi.
  function coinAdiSor(sembol, ne) {
    const yazilan = window.prompt(ne + "\n\nGERÇEK PARA. Onaylamak için coin adını yazın: " +
      sembol);
    if (yazilan === null) return null;
    return yazilan.trim();
  }

  // --- bölümler ----------------------------------------------------------

  function uyariCiz(v) {
    const k = el("div", "canli-serit");
    k.appendChild(el("strong", null, "GERÇEK PARA"));
    k.appendChild(el("span", null, v.uyari));
    return k;
  }

  function baglantiCiz(v) {
    const kap = document.createDocumentFragment();
    const d = el("div", "baglanti-serit");
    const b = v.baglanti;
    let sinif = "sari";
    let metin = b.hazir_degil || "Canlı hesap hazır.";
    if (!b.kurulu) sinif = "kotu";
    else if (b.hazir) {
      sinif = "iyi";
      metin = "Canlı hesap hazır: izinler uygun, hesap akışı bağlı, borsayla uzlaştırıldı";
    }
    const durum = el("span", "baglanti-durum");
    durum.appendChild(el("span", "nokta " + sinif));
    durum.appendChild(el("span", null, metin));
    d.appendChild(durum);
    if (b.kurulu) {
      const ha = b.hesap_akisi;
      d.appendChild(el("span", "kart-etiket",
        "Hesap akışı: " + (ha && ha.abone ? "bağlı" : "kopuk")));
      if (b.son_uzlastirma) {
        d.appendChild(el("span", "kart-etiket", "Son uzlaştırma " + b.son_uzlastirma));
      }
      d.appendChild(dugme("Borsayla uzlaştır", "dugme-ikincil dugme-kucuk", function () {
        return eylem("/api/canli/uzlastir", { onay: true }, function (s) { return s.ozet; });
      }));
    }
    kap.appendChild(d);
    const iz = b.izinler || {};
    if (b.kurulu) {
      const k = A.kutu("Anahtarın izinleri", iz.tamam ? null : "kutu-uyari");
      k.appendChild(A.alanlar([
        ["Para çekme", iz.cekim_izni === null || iz.cekim_izni === undefined ? "okunmadı"
          : (iz.cekim_izni ? "AÇIK" : "kapalı"), iz.cekim_izni ? "kotu" : "iyi"],
        ["Spot işlem", iz.islem_izni ? "açık" : "kapalı", iz.islem_izni ? "iyi" : "kotu"],
        ["IP kısıtlaması", iz.ip_kisitli === null || iz.ip_kisitli === undefined ? "—"
          : (iz.ip_kisitli ? "var" : "yok"), iz.ip_kisitli ? "iyi" : "sari"],
        ["Son okuma", iz.yas_sn === null || iz.yas_sn === undefined ? "—"
          : sure(iz.yas_sn) + " önce"],
      ]));
      if (iz.giris_engeli) k.appendChild(el("p", "sari", "Yeni giriş kapalı: " + iz.giris_engeli));
      (iz.engeller || []).forEach(function (t) { k.appendChild(el("p", "kotu", t)); });
      (iz.uyarilar || []).forEach(function (t) { k.appendChild(el("p", "kart-etiket", t)); });
      k.appendChild(el("p", "kart-etiket",
        "İzinler açılışta ve yarım saatte bir Binance'ten okunur. Para çekme ya da Spot dışı " +
        "bir izin açık okunursa yeni emir gitmez ve canlı moddaki coinler Sadece Öneri'ye " +
        "alınır; açık pozisyonların stop ve hedefi yerinde kalır."));
      kap.appendChild(k);
    }
    if (b.kurulu && (b.uyarilar.length || Object.keys(b.elle_emirler).length)) {
      const k = A.kutu(null, "kutu-uyari");
      const elle = Object.keys(b.elle_emirler);
      if (elle.length) {
        k.appendChild(el("p", "sari",
          "Canlı hesapta bu uygulamanın açmadığı emirler var (" +
          elle.map(function (s) { return s + " " + b.elle_emirler[s]; }).join(", ") +
          "). Uygulama bunlara dokunmaz ama bakiye hesabında onları da görür."));
      }
      if (b.uyarilar.length) k.appendChild(A.liste(b.uyarilar, "uyari-liste"));
      kap.appendChild(k);
    }
    return kap;
  }

  function tamOtomatikOzet(o) {
    const k = el("div", "tam-ozet");
    k.appendChild(el("h3", null, o.sembol + " için Tam Otomatik özeti"));
    k.appendChild(el("p", o.acilabilir ? "iyi" : "sari", o.kapi));
    k.appendChild(A.alanlar([
      ["Bot bütçesi", o.butce_usdt + " USDT"],
      ["Emir başına tavan", o.tavan_usdt + " USDT"],
      ["Periyotlar", o.periyotlar.join(", ")],
    ].concat(o.risk.map(function (r) { return [r.etiket, r.deger]; }))));
    if (o.kurallar.length) {
      k.appendChild(el("p", "kart-etiket", "Emir açabilecek kurallar (kapıyı geçenler): " +
        o.kurallar.map(function (r) { return r.kural_etiketi + " (" + r.periyot + ")"; })
          .join(", ")));
    }
    if (!o.acilabilir) {
      if (o.hazir_degil) k.appendChild(el("p", "sari", o.hazir_degil));
      k.appendChild(el("p", "kart-etiket",
        "Tam Otomatik bu coin için kilitli. Kapıyı elle aşan bir yol yok."));
      return k;
    }
    const f = el("form", "form-satir");
    f.setAttribute("autocomplete", "off");
    const lab = el("label", null, "Onay için coin adını yazın (" + o.sembol + ")");
    const giris = el("input");
    giris.name = "onay";
    giris.maxLength = 20;
    giris.value = tamOnayMetni;
    giris.addEventListener("input", function () { tamOnayMetni = giris.value; });
    lab.appendChild(giris);
    f.appendChild(lab);
    const b = el("button", "dugme dugme-tehlike", "Tam Otomatik'i aç");
    b.type = "submit";
    f.appendChild(b);
    f.addEventListener("submit", async function (olay) {
      olay.preventDefault();
      b.disabled = true;
      const sonuc = await eylem("/api/canli/mod",
        { sembol: o.sembol, mod: "tam_otomatik", onay: giris.value.trim() },
        o.sembol + " Tam Otomatik'te (gerçek para).");
      if (sonuc) { ozetAcik = null; tamOnayMetni = ""; yeniden("canli-modlar"); await yenile(); }
      b.disabled = false;
    });
    k.appendChild(f);
    return k;
  }

  function modlarCiz(v) {
    const k = A.kutu("Hangi coin canlıda?");
    k.appendChild(el("p", "kart-etiket",
      "Yarı Otomatik: kabul edilmiş bir kural sinyal verince emir gitmez, öneri olarak buraya " +
      "ve Telegram'a düşer; 'Emri Gönder' demeden emir gitmez. Elle canlı emir de yalnızca " +
      "bu moddaki coinde açılır. Tam Otomatik: yalnızca canlıya geçiş kapısını geçen " +
      "kuralların sinyali onaysız emre dönüşür. Uygulama her açılışta bütün coinleri Sadece " +
      "Öneri'ye alır; canlı mod kendiliğinden açılmaz."));
    const kap = el("div", "mod-satirlar");
    v.modlar.forEach(function (m) {
      const satir = el("div", "mod-satir");
      satir.appendChild(el("span", "mod-sembol", m.sembol));
      satir.appendChild(el("span", m.canli ? "kotu" : "kart-etiket",
        m.mod_tr + (m.canli ? " (CANLI)" : "")));
      if (m.canli) {
        satir.appendChild(dugme("Sadece Öneri'ye al", "dugme-ikincil dugme-kucuk", function () {
          return eylem("/api/canli/mod", { sembol: m.sembol, mod: "sadece_oneri" },
            m.sembol + " Sadece Öneri'ye alındı. Açık pozisyonun stop ve hedefi yerinde.");
        }));
      }
      if (m.mod !== "yari_otomatik") {
        const d = dugme("Yarı Otomatik'e al", "dugme-kucuk", function () {
          const onay = coinAdiSor(m.sembol, m.sembol + " Yarı Otomatik'e alınacak: her canlı " +
            "emir sizin onayınızla gerçek parayla Binance hesabınıza gider.");
          if (onay === null) return Promise.resolve();
          return eylem("/api/canli/mod", { sembol: m.sembol, mod: "yari_otomatik", onay: onay },
            m.sembol + " Yarı Otomatik'te (gerçek para, her emir onayla).");
        });
        if (!v.hazir) { d.disabled = true; d.title = v.neden || ""; }
        satir.appendChild(d);
      }
      if (m.mod !== "tam_otomatik") {
        satir.appendChild(dugme(ozetAcik === m.sembol ? "Özeti gizle" : "Tam Otomatik…",
          "dugme-ikincil dugme-kucuk", async function () {
            ozetAcik = ozetAcik === m.sembol ? null : m.sembol;
            tamOnayMetni = "";
            yeniden("canli-modlar");
            await yenile();
          }));
      }
      kap.appendChild(satir);
      if (ozetAcik === m.sembol && v.ozet && v.ozet.sembol === m.sembol) {
        kap.appendChild(tamOtomatikOzet(v.ozet));
      }
    });
    k.appendChild(kap);
    if (!v.hazir && v.neden) {
      k.appendChild(el("p", "kart-etiket", "Canlı mod şu an seçilemiyor: " + v.neden));
    }
    return k;
  }

  function onerilerCiz(v) {
    const k = A.kutu("Onay bekleyen canlı öneriler");
    if (!v.bekleyen.length) {
      k.appendChild(el("p", "kart-etiket",
        "Yok. Yarı Otomatik'teki bir coinde kabul edilmiş bir kural sinyal verince öneri " +
        "buraya düşer (Telegram'dan /onayla ile de gönderilebilir). Kabul edilmiş kural " +
        "olmadığı sürece öneri gelmez."));
    }
    v.bekleyen.forEach(function (o) {
      const kart = A.kutu(null, "kutu-uyari");
      const bas = el("div", "kart-baslik");
      const sol = el("div");
      sol.appendChild(el("strong", null, o.sembol + " " + o.periyot));
      sol.appendChild(el("span", "kart-etiket", "  " + o.kural_etiketi + " · " + o.kimlik));
      bas.appendChild(sol);
      bas.appendChild(el("span", "rozet rozet-uyari", o.durum_tr));
      kart.appendChild(bas);
      kart.appendChild(A.alanlar([
        ["Giriş", o.giris], ["Hedef", o.hedef], ["Stop", o.stop],
        ["Tahmini tutar", o.tahmini_tutar_usdt ? o.tahmini_tutar_usdt + " USDT" : null],
        ["Geçerli", o.bitis + "'a kadar"],
      ]));
      if (o.notlar.length) kart.appendChild(A.liste(o.notlar));
      const alt = el("div", "dugme-satir");
      alt.appendChild(dugme("Emri Gönder", "dugme-tehlike dugme-kucuk", function () {
        if (!window.confirm(o.sembol + " için canlı alış emri GERÇEK PARAYLA gönderilsin mi? " +
          "(giriş " + o.giris + ", hedef " + o.hedef + ", stop " + o.stop + "; emir yine risk " +
          "kapılarından ve tavandan geçer)")) return Promise.resolve();
        return eylem("/api/canli/oneri-gonder", { kimlik: o.kimlik }, function (s) {
          return s.pozisyon ? o.sembol + " canlı giriş emri: " + s.pozisyon.durum_tr + "."
            : "Emir açılmadı: " + s.mesaj;
        });
      }));
      alt.appendChild(dugme("Reddet", "dugme-ikincil dugme-kucuk", function () {
        return eylem("/api/canli/oneri-reddet", { kimlik: o.kimlik }, "Öneri reddedildi.");
      }));
      kart.appendChild(alt);
      k.appendChild(kart);
    });
    if (v.son.length) {
      k.appendChild(el("h3", null, "Son öneriler"));
      k.appendChild(tablo(["Zaman", "Coin", "Kural", "Durum", "Sonuç"],
        v.son.map(function (o) {
          return [o.olusturma, o.sembol + " " + o.periyot, o.kural_etiketi, o.durum_tr,
            { metin: o.sonuc, sinif: "kart-etiket sol" }];
        })));
    }
    return k;
  }

  function kapiCiz(g) {
    const k = A.kutu("Canlıya geçiş kapısı");
    k.appendChild(el("p", "kart-etiket", g.aciklama));
    g.coinler.forEach(function (c) {
      const p = el("p", c.tam_otomatik_acilabilir ? "iyi" : "sari");
      p.appendChild(el("strong", null, c.tam_otomatik_acilabilir ? "Açılabilir. " : "Kilitli. "));
      p.appendChild(document.createTextNode(c.ozet));
      k.appendChild(p);
    });
    g.kurallar.forEach(function (r) {
      k.appendChild(el("h3", null, r.kural_etiketi + " · " + r.sembol + " " + r.periyot +
        (r.gecti ? " · geçti" : "")));
      k.appendChild(kapiListesi(r.kosullar));
    });
    return k;
  }

  function hesapCiz(v) {
    const h = v.hesap;
    const k = A.kutu("Canlı hesap (bot bütçesi)");
    k.appendChild(A.alanlar([
      ["Bot bütçesi", h.baslangic_usdt + " USDT"],
      ["Emir başına tavan", v.tavan_usdt + " USDT", "sari"],
      ["Nakit", h.nakit_usdt + " USDT"],
      ["Bekleyen emirde", h.kilitli_usdt + " USDT"],
      ["Serbest", h.serbest_usdt + " USDT"],
      ["Özsermaye", h.ozsermaye_usdt === null ? "fiyat yok" : h.ozsermaye_usdt + " USDT"],
      ["Getiri", A.yuzde(h.getiri_yuzde, 2) || "—", A.isaret(h.getiri_yuzde)],
      ["Borsadaki serbest USDT", h.borsa_serbest_usdt === null ? "—" : h.borsa_serbest_usdt + " USDT"],
    ]));
    if (h.coinler.length) {
      k.appendChild(tablo(["Coin", "Miktar", "Fiyat (alış)", "Değer (USDT)"],
        h.coinler.map(function (c) { return [c.sembol, c.miktar, c.fiyat, c.deger_usdt]; })));
    }
    if (v.defter.length) {
      k.appendChild(tablo(["Coin", "En iyi alış", "En iyi satış", "Fiyat yaşı"],
        v.defter.map(function (f) {
          return [f.sembol, f.alis, f.satis, f.yas_sn === null ? "—" : sure(f.yas_sn)];
        })));
    }
    k.appendChild(el("p", "kart-etiket", v.butce_notu));
    k.appendChild(el("p", "kart-etiket", "Dönem " + h.donem_id + ", başlangıç " + h.baslangic +
      " (İstanbul)."));
    return k;
  }

  function riskCiz(v) {
    const r = v.risk;
    const k = A.kutu("Risk sınırları (canlı)");
    r.gostergeler.forEach(function (g) {
      const satir = el("div", "olcer");
      const ust = el("div", "olcer-ust");
      ust.appendChild(el("span", null, g.etiket));
      const yuzdeMi = g.birim.indexOf("%") === 0;
      ust.appendChild(el("span", "olcer-deger", yuzdeMi
        ? "%" + g.deger.toFixed(2) + " / %" + g.sinir.toFixed(2) + g.birim.slice(1)
        : g.deger.toFixed(0) + " / " + g.sinir.toFixed(0) + " " + g.birim));
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
      r.gunluk_kalan_usdt + " USDT. Limitler kâğıt işlemle ortaktır. Bir sınır aşılırsa " +
      "kâğıt, Demo ve canlı otomatik işlem birlikte durur; stoplar yerinde kalır."));
    const artArda = r.gostergeler.find(function (g) { return g.ad === "art_arda_kayip"; });
    if (artArda && artArda.deger > 0) {
      const alt = el("div", "dugme-satir");
      alt.appendChild(dugme("Art arda kayıp sayacını sıfırla", "dugme-ikincil", function () {
        return eylem("/api/canli/art-arda-sifirla", { onay: true },
          "Canlı art arda kayıp sayacı sıfırlandı.");
      }));
      k.appendChild(alt);
    }
    return k;
  }

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
        return eylem("/api/canli/iptal", { id: o.id },
          o.sembol + " canlı giriş emrine iptal gönderildi.");
      }));
    }
    if (o.kapatilabilir) {
      alt.appendChild(dugme("Pozisyonu kapat", "dugme-tehlike dugme-kucuk", function () {
        if (!window.confirm(o.sembol + " CANLI pozisyonu kapatılsın mı? Borsadaki stop ve hedef " +
          "iptal edilir, elde kalan coin en iyi alıştan en fazla ayarlardaki kayma kadar aşağıda " +
          "gerçek parayla satılır.")) return Promise.resolve();
        return eylem("/api/canli/kapat", { id: o.id }, o.sembol + " kapatılıyor.");
      }));
    }
    if (alt.childNodes.length) k.appendChild(alt);
    return k;
  }

  function aktifCiz(liste) {
    const kap = el("div");
    const k = A.kutu("Açık canlı pozisyonlar ve bekleyen emirler");
    if (!liste.length) {
      k.appendChild(el("p", "kart-etiket", "Yok."));
      kap.appendChild(k);
      return kap;
    }
    k.appendChild(el("p", "kart-etiket",
      "Giriş dolunca stop ve hedef borsaya konur ve orada bekler: bu uygulama kapansa da " +
      "Mac uyusa da stop borsada çalışır."));
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
    const k = A.kutu("Canlı sonuçlar (bu dönem)");
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
      "Elle canlı işlemler canlıya geçiş kapısına sayılmaz."));
    k.appendChild(el("h3", null, "Komisyon"));
    Object.keys(v.maliyet).forEach(function (s) {
      k.appendChild(el("p", "kart-etiket", s + ": " + v.maliyet[s]));
    });
    return k;
  }

  function gecmisCiz(liste) {
    const k = A.kutu("Kapanan canlı işlemler");
    const a = el("a", "dugme dugme-ikincil", "Dolumları CSV indir (Excel)");
    a.href = "/api/canli/islemler.csv";
    a.setAttribute("download", "");
    k.appendChild(a);
    if (!liste.length) {
      k.appendChild(el("p", "kart-etiket", "Henüz kapanan canlı işlem yok."));
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
      kap.appendChild(el("p", "kart-etiket", b.hazir_degil || "Canlı işlem anahtarı kurulu değil."));
      return kap;
    }
    const butce = b.istek_butcesi || {};
    const sayac = b.emir_sayaci || {};
    const ha = b.hesap_akisi || {};
    kap.appendChild(A.alanlar([
      ["Ortam", b.ortam, "kotu"],
      ["Adres", b.adres],
      ["Saat farkı", b.saat_farki_ms === null ? "—" : b.saat_farki_ms + " ms"],
      ["Hesap akışı", ha.abone ? "bağlı" : "kopuk", ha.abone ? "iyi" : "kotu"],
      ["Akış yeniden bağlanma", tire(ha.yeniden_baglanma)],
      ["Akıştan gelen olay", tire(ha.olay)],
      ["İstek (bu dakika)", tire(butce.yerel_1dk) + " / " + tire(butce.yerel_tavan)],
      ["Borsa sayacı", tire(butce.borsa_1dk)],
      ["Emir (10 sn)", tire(sayac.son_10sn) + " / " + tire(sayac.sinir_10sn)],
      ["Emir (gün)", tire(sayac.son_1gun) + " / " + tire(sayac.sinir_gun)],
    ]));
    kap.appendChild(el("p", "kart-etiket",
      "Canlı hesabın istekleri canlı piyasa verisiyle aynı istek bütçesinden düşer (Binance'in " +
      "sınırı IP başına). Tavana gelirse istek gönderilmeden beklenir; borsa 418 derse engel " +
      "bitene kadar canlı işlem durur."));
    const bakiye = Object.keys(b.bakiyeler);
    if (bakiye.length) {
      kap.appendChild(el("h3", null, "Borsadaki canlı bakiye"));
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

  // --- elle canlı emir --------------------------------------------------------------

  const form = document.getElementById("canli-elle-form");

  function elleBaslik() {
    document.getElementById("canli-elle-baslik").textContent =
      "Elle canlı emir · " + A.sembol() + " " + A.periyot();
    const f = defterFiyati(A.sembol());
    const ipucu = document.getElementById("canli-elle-ipucu");
    if (!hazir()) {
      ipucu.textContent = sonDurum ? (sonDurum.baglanti.hazir_degil || "") : "";
      return;
    }
    ipucu.textContent = (f && f.alis
      ? "En iyi alış " + f.alis + ", en iyi satış " + f.satis +
        ". Giriş en iyi satışın altında olmalı; yoksa borsa reddeder. "
      : "Piyasa fiyatı henüz gelmedi. ") +
      "Tutar boş bırakılırsa risk motoru tavana (" + sonDurum.tavan_usdt + " USDT) kadar boyutlar.";
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
    if (gonderildi) baslik = veri.mesaj || "Canlı emir gönderildi.";
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
    k.appendChild(el("h3", null, "Risk ve canlı hesap kapıları"));
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
    const tutar = (alan.get("tutar_usdt") || "").trim();
    if (tutar) govde.tutar_usdt = tutar;
    if (hangi === "gonder") {
      const onay = (alan.get("onay") || "").trim();
      if (onay.toUpperCase() !== govde.sembol) {
        A.bildir("Göndermek için onay kutusuna coin adını yazın: " + govde.sembol, "kotu");
        return;
      }
      if (!window.confirm(govde.sembol + " için Binance CANLI hesabınıza GERÇEK PARAYLA alış " +
        "emri gönderilsin mi? (giriş " + govde.giris + ", hedef " + govde.hedef + ", stop " +
        govde.stop + (tutar ? ", en fazla " + tutar + " USDT" : "") + ")")) return;
      govde.onay = onay;
    }
    const dugmeler = form.querySelectorAll("button");
    dugmeler.forEach(function (d) { d.disabled = true; });
    try {
      let veri;
      if (hangi === "onizle") {
        veri = await A.getir("/api/canli/on-izleme?" + new URLSearchParams(govde).toString());
        A.yaz("canli-elle-sonuc", elleSonucCiz(veri, false));
      } else {
        govde.gecerlilik_mum = parseInt(alan.get("gecerlilik_mum"), 10) || 1;
        govde.azami_tutma_mum = parseInt(alan.get("azami_tutma_mum"), 10) || 4;
        veri = await A.gonder("/api/canli/emir", govde);
        const gitti = !!veri.pozisyon;
        A.yaz("canli-elle-sonuc", elleSonucCiz(veri, gitti));
        if (gitti) {
          form.elements.onay.value = "";
          A.bildir(veri.pozisyon.sembol + " canlı giriş emri: " + veri.pozisyon.durum_tr + ".", "iyi");
          await yenile();
        } else {
          A.bildir("Canlı emir gönderilmedi: " + (veri.mesaj || veri.karar.ozet), "kotu");
        }
      }
    } catch (hata) {
      A.yaz("canli-elle-sonuc", A.hataKutusu(hata));
    } finally {
      dugmeler.forEach(function (d) { d.disabled = false; });
    }
  });

  document.getElementById("canli-elle-fiyat").addEventListener("click", function () {
    const f = defterFiyati(A.sembol());
    if (f && f.alis) form.elements.giris.value = f.alis;
    else A.bildir("Piyasa fiyatı henüz yok.", "kotu");
  });

  // --- ayarlar (yalnızca açılınca ve kaydedince çizilir) ------------------------------

  function tavanCiz(v) {
    const f = el("form", "form-satir");
    f.setAttribute("autocomplete", "off");
    const lab = el("label", null, "Emir başına tavan (USDT)");
    const giris = el("input");
    giris.name = "tutar_usdt";
    giris.setAttribute("inputmode", "decimal");
    giris.value = v.tavan_usdt;
    lab.appendChild(giris);
    f.appendChild(lab);
    const b = el("button", "dugme", "Tavanı kaydet");
    b.type = "submit";
    f.appendChild(b);
    f.appendChild(el("p", "kart-etiket",
      "Her canlı giriş emri bu tutarı aşamaz (varsayılan " +
      v.baglanti.tavan_varsayilan_usdt + " USDT, en fazla bot bütçesi " + v.butce_usdt +
      " USDT). Emri imzalayan parça da tavanı ayrıca sınar."));
    f.addEventListener("submit", async function (olay) {
      olay.preventDefault();
      b.disabled = true;
      const sonuc = await eylem("/api/canli/tavan", { tutar_usdt: giris.value.trim() },
        function (s) { return "Canlı emir tavanı " + s.tavan_usdt + " USDT."; });
      if (sonuc) giris.value = sonuc.tavan_usdt;
      b.disabled = false;
    });
    return f;
  }

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
        const sonuc = await A.gonder("/api/canli/ayarlar", { degerler: degerler });
        A.bildir(Object.keys(degerler).length + " ayar güncellendi.", "iyi");
        A.yaz("canli-ayar-emir", ayarlariCiz(sonuc));
      } catch (hata) {
        A.bildir(hata.message, "kotu");
        kaydet.disabled = false;
      }
    });
    return f;
  }

  function ayarKutusunuCiz(d) {
    const kap = el("div");
    kap.appendChild(tavanCiz(d));
    const emir = el("div");
    emir.id = "canli-ayar-emir";
    emir.appendChild(ayarlariCiz(d.ayarlar));
    kap.appendChild(emir);
    return kap;
  }

  function detayAcik(id) {
    const d = document.getElementById(id);
    return d && d.open;
  }

  document.getElementById("canli-ayar-kutu").addEventListener("toggle", function () {
    if (this.open && sonDurum) A.yaz("canli-ayarlar", ayarKutusunuCiz(sonDurum));
  });
  document.getElementById("canli-ayrinti-kutu").addEventListener("toggle", function () {
    if (this.open && sonDurum) { yeniden("canli-ayrinti"); cizDegistiyse("canli-ayrinti", sonDurum.baglanti, ayrintiCiz); }
  });

  document.getElementById("canli-sifirla-form").addEventListener("submit", async function (olay) {
    olay.preventDefault();
    const onay = (new FormData(this).get("onay") || "").trim();
    const sonuc = await eylem("/api/canli/hesap-sifirla", { onay: onay }, function (s) {
      return "Canlı bot dönemi sıfırlandı; dönem " + s.donem_id + " başladı.";
    });
    if (sonuc) this.reset();
  });

  // --- yenileme ------------------------------------------------------------------

  async function yenile() {
    if (yukleniyor) return;
    yukleniyor = true;
    try {
      const d = await A.getir("/api/canli/durum");
      const ilkKez = sonDurum === null;
      sonDurum = d;
      let ozet = null;
      if (ozetAcik) {
        try {
          ozet = await A.getir("/api/canli/tam-otomatik-ozet?sembol=" +
            encodeURIComponent(ozetAcik));
        } catch (hata) {
          ozet = null;
        }
      }
      A.modRozeti(d.modlar);
      cizDegistiyse("canli-uyari", { uyari: d.uyari }, uyariCiz);
      cizDegistiyse("canli-baglanti", { baglanti: d.baglanti }, baglantiCiz);
      cizDegistiyse("canli-modlar", {
        modlar: d.modlar, hazir: d.baglanti.hazir && d.canli, ozet: ozet, acik: ozetAcik,
        neden: d.canli ? d.baglanti.hazir_degil : "Canlı veri yok: arayüz çevrimdışı açıldı.",
      }, modlarCiz);
      cizDegistiyse("canli-oneriler", { bekleyen: d.bekleyen_oneriler, son: d.son_oneriler },
        onerilerCiz);
      cizDegistiyse("canli-kapi", d.gecis_kapisi, kapiCiz);
      cizDegistiyse("canli-hesap", { hesap: d.hesap, defter: d.defter, butce_notu: d.butce_notu,
        tavan_usdt: d.tavan_usdt }, hesapCiz);
      cizDegistiyse("canli-risk", { risk: d.risk }, riskCiz);
      cizDegistiyse("canli-aktif", d.aktif, aktifCiz);
      cizDegistiyse("canli-sonuclar", { ozet: d.ozet, maliyet: d.maliyet }, sonuclarCiz);
      cizDegistiyse("canli-gecmis", d.son_kapanan, gecmisCiz);
      if (detayAcik("canli-ayrinti-kutu")) cizDegistiyse("canli-ayrinti", d.baglanti, ayrintiCiz);
      if (ilkKez && detayAcik("canli-ayar-kutu")) A.yaz("canli-ayarlar", ayarKutusunuCiz(d));
      elleBaslik();
    } catch (hata) {
      yeniden("canli-baglanti");
      A.yaz("canli-baglanti", A.hataKutusu(hata));
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
        if (!document.hidden) yenile();
      }, YENILEME_MS);
    }
  }

  window.Canli = { etkin: etkin, yenile: function () { if (acik) return yenile(); } };
})();
