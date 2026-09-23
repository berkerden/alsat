/* Kâğıt işlem sekmesi (Faz 4).
 *
 * Sekme açıkken beş saniyede bir sunucudan durum okur. Parasal değerler
 * sunucudan METİN olarak gelir ve öyle gösterilir; burada fiyat hesabı
 * yapılmaz. Durum değiştiren her istek app.js'teki gonder() ile gider
 * (özel başlık + JSON gövde; sunucunun yerel koruması bunu şart koşar).
 *
 * Kullanıcının yazdığı alanlar (elle emir, risk limitleri) yenilemede
 * yeniden çizilmez; yalnızca sunucudan gelen bölümler çizilir ve yalnızca
 * veri değiştiyse.
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
  let sonPiyasa = null;
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

  // Uygulamanın geri kalanı gibi ondalık ayırıcı nokta: tutarlar sunucudan
  // "60049.79" biçiminde geliyor, aynı ekranda virgül karışmasın.
  function sayi(deger, basamak) {
    if (deger === null || deger === undefined) return "—";
    return deger.toFixed(basamak);
  }

  // Hücresi DOM düğümü de olabilen tablo (düğmeler için).
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

  function coinPiyasa(sembol) {
    if (!sonPiyasa) return null;
    return sonPiyasa.coinler.find(function (c) { return c.sembol === sembol; }) || null;
  }

  // --- bölümler ----------------------------------------------------------

  function baglantiCiz(v) {
    const d = el("div", "baglanti-serit");
    const b = v.baglanti;
    let durumSinif = "kotu";
    let durumMetin = "Canlı veri yok: arayüz çevrimdışı açıldı, kâğıt işlem çalışmaz.";
    if (v.canli && b) {
      if (b.akis_bagli) {
        durumSinif = "iyi";
        durumMetin = "Canlı veri bağlı (Binance WebSocket)";
        if (b.akis_son_mesaj_sn !== null) durumMetin += " · son mesaj " + sayi(b.akis_son_mesaj_sn, 1) + " sn önce";
      } else if (b.yedek_yoklama) {
        durumSinif = "sari";
        durumMetin = "Canlı akış kopuk; yedek olarak dakikada bir REST ile fiyat alınıyor";
      } else {
        durumSinif = "sari";
        durumMetin = "Canlı akışa bağlanılıyor…";
      }
    }
    const durum = el("span", "baglanti-durum");
    durum.appendChild(el("span", "nokta " + durumSinif));
    durum.appendChild(el("span", null, durumMetin));
    d.appendChild(durum);
    const uyku = b && b.uyku_engeli;
    if (uyku && uyku.etkin) {
      const u = el("span", "kart-etiket" + (uyku.pilde ? " sari" : ""),
        uyku.pilde ? "Mac uyumuyor · pille çalışıyor, kapak kapanırsa uyur" : "Mac uyumuyor");
      u.title = uyku.aciklama;
      d.appendChild(u);
    }
    const tg = v.telegram || {};
    d.appendChild(el("span", "kart-etiket",
      tg.kurulu ? "Telegram: @" + (tg.bot || "bot") + " (" + tg.gonderilen + " gönderildi" +
        (tg.gonderilemeyen ? ", " + tg.gonderilemeyen + " gönderilemedi" : "") + ")"
        : "Telegram kurulu değil"));
    return d;
  }

  function modlarCiz(v) {
    const k = A.kutu("Modlar");
    k.appendChild(el("p", "kart-etiket",
      "Uygulama her açılışta bütün coinleri Sadece Öneri'ye alır. Kâğıt İşlem modunda, " +
      "kabul edilmiş bir kural sinyal verince kâğıt emir kendiliğinden açılır; elle " +
      "kâğıt emir de yalnızca bu modda açılır."));
    const kap = el("div", "mod-satirlar");
    v.modlar.forEach(function (m) {
      const satir = el("label", "mod-satir");
      satir.appendChild(el("span", "mod-sembol", m.sembol));
      const sec = el("select");
      sec.setAttribute("aria-label", m.sembol + " modu");
      v.secilebilir.forEach(function (s) {
        const o = new Option(s.etiket, s.mod);
        if (s.mod === "kagit" && !v.canli) {
          o.disabled = true;
          o.textContent = s.etiket + " (canlı veri yok)";
        }
        sec.appendChild(o);
      });
      v.kilitli.forEach(function (s) {
        const o = new Option(s.etiket + " — kilitli: " + s.ne_zaman, s.mod);
        o.disabled = true;
        sec.appendChild(o);
      });
      sec.value = m.mod;
      sec.addEventListener("change", async function () {
        const yeni = sec.value;
        sec.disabled = true;
        const sonuc = await eylem("/api/kagit/mod", { sembol: m.sembol, mod: yeni },
          m.sembol + ": " + sec.options[sec.selectedIndex].text);
        if (!sonuc) sec.value = m.mod;
        sec.disabled = false;
      });
      satir.appendChild(sec);
      if (m.onceki_oturum === "kagit" && m.mod !== "kagit") {
        satir.appendChild(el("span", "kart-etiket",
          "Önceki oturumda Kâğıt İşlem'deydi; devam etmek için yeniden seçin."));
      }
      kap.appendChild(satir);
    });
    k.appendChild(kap);
    return k;
  }

  function hesapCiz(h) {
    const k = A.kutu("Kâğıt hesap");
    k.appendChild(A.alanlar([
      ["Başlangıç", h.baslangic_usdt + " USDT"],
      ["Nakit", h.nakit_usdt + " USDT"],
      ["Bekleyen emirde", h.kilitli_usdt + " USDT"],
      ["Serbest", h.serbest_usdt + " USDT"],
      ["Özsermaye", h.ozsermaye_usdt === null ? "fiyat yok" : h.ozsermaye_usdt + " USDT"],
      ["Getiri", A.yuzde(h.getiri_yuzde, 2) || "—", A.isaret(h.getiri_yuzde)],
    ]));
    if (h.coinler.length) {
      k.appendChild(tablo(["Coin", "Miktar", "Fiyat (en iyi alış)", "Değer (USDT)"],
        h.coinler.map(function (c) { return [c.sembol, c.miktar, c.fiyat, c.deger_usdt]; })));
      k.appendChild(el("p", "kart-etiket",
        "Açık pozisyon yoksa buradaki küçük miktarlar, borsanın miktar adımı yüzünden " +
        "satılamayan küsurattır (toz); bir sonraki satışa eklenir."));
    }
    k.appendChild(el("p", "kart-etiket", "Dönem " + h.donem_id + ", başlangıç " + h.baslangic +
      " (İstanbul)."));
    return k;
  }

  function riskCiz(v) {
    const r = v.risk;
    const k = A.kutu("Risk sınırları");
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
      r.gunluk_kalan_usdt + " USDT (açık pozisyonların stop zararı düşüldü). Gün " +
      r.gun_sonu + "'da (İstanbul) yenilenir. Bir sınır aşılırsa kâğıt işlem kendiliğinden " +
      "durur ve ancak elle yeniden açılır."));
    const alt = el("div", "dugme-satir");
    const artArda = r.gostergeler.find(function (g) { return g.ad === "art_arda_kayip"; });
    if (artArda && artArda.deger > 0) {
      alt.appendChild(dugme("Art arda kayıp sayacını sıfırla", "dugme-ikincil", function () {
        return eylem("/api/kagit/art-arda-sifirla", {}, "Art arda kayıp sayacı sıfırlandı.");
      }));
    }
    if (alt.childNodes.length) k.appendChild(alt);
    if (v.durdurulan.length) {
      k.appendChild(el("h3", null, "Performansı bozulduğu için durdurulan kurallar"));
      v.durdurulan.forEach(function (kural) {
        const s = el("div", "dugme-satir");
        s.appendChild(el("span", null, kural));
        s.appendChild(dugme("Yeniden aç", "dugme-ikincil", function () {
          return eylem("/api/kagit/kural-ac", { kural: kural },
            kural + " yeniden açıldı; sınaması sıfırdan başlıyor.");
        }));
        k.appendChild(s);
      });
    }
    return k;
  }

  function aktifCiz(liste) {
    const k = A.kutu("Açık pozisyonlar ve bekleyen emirler");
    if (!liste.length) {
      k.appendChild(el("p", "kart-etiket", "Yok."));
      return k;
    }
    k.appendChild(tablo(
      ["Durum", "Coin", "Kaynak", "Giriş", "Hedef", "Stop", "Miktar", "Tutar", "Stop zararı",
        "Anlık", "Kalan", ""],
      liste.map(function (o) {
        const bekliyor = o.durum === "bekliyor";
        const d = bekliyor
          ? dugme("İptal", "dugme-ikincil dugme-kucuk", function () {
            return eylem("/api/kagit/iptal", { id: o.id }, o.sembol + " giriş emri iptal edildi.");
          })
          : dugme("Kapat", "dugme-tehlike dugme-kucuk", function () {
            if (!window.confirm(o.sembol + " pozisyonu piyasa fiyatından kapatılsın mı? " +
              "(Kâğıt işlem; taker komisyonu ve kayma düşülür.)")) return Promise.resolve();
            return eylem("/api/kagit/kapat", { id: o.id }, function (s) {
              return o.sembol + " kapatıldı: net " + s.emir.net_usdt + " USDT.";
            });
          });
        return [
          o.durum_tr, o.sembol + " " + o.periyot,
          o.kaynak_tr + (o.kural_etiketi && o.kaynak === "kural" ? " · " + o.kural_etiketi : ""),
          o.giris, o.hedef, o.stop, bekliyor ? o.miktar : o.satilacak, o.tutar_usdt,
          { metin: o.stop_zarari_usdt, sinif: "kotu" },
          o.anlik_fiyat === null ? "—"
            : { metin: o.anlik_fiyat + " (" + A.yuzde(o.anlik_yuzde, 2) + ")", sinif: A.isaret(o.anlik_yuzde) },
          o.kalan, d,
        ];
      })));
    k.appendChild(el("p", "kart-etiket",
      "Bekleyen giriş, fiyat girişin altına inince dolar; süresi dolunca iptal olur. " +
      "Açık pozisyon hedefte ya da stopta kapanır; süre dolarsa piyasa fiyatından kapatılır. " +
      "Anlık yüzde komisyon öncesidir."));
    return k;
  }

  function ozetAlanlari(o) {
    return A.alanlar([
      ["İşlem", String(o.islem)],
      ["Kazanan", o.islem ? o.kazanan + " (" + A.oran(100 * o.isabet_orani, 0) + ")" : "—"],
      ["Net", o.net_usdt + " USDT", A.isaret(parseFloat(o.net_usdt))],
      ["Ortalama net", A.yuzde(o.ortalama_net_yuzde, 3) || "—", A.isaret(o.ortalama_net_yuzde)],
      ["Kâr faktörü", o.kar_faktoru === null ? "—" : sayi(o.kar_faktoru, 2)],
      ["En büyük düşüş", o.en_buyuk_dusus_usdt + " USDT"],
      ["Ödenen komisyon", o.toplam_komisyon_usdt + " USDT"],
      ["Komisyon / brüt kâr", o.komisyon_brut_kar_orani === null ? "—"
        : A.oran(100 * o.komisyon_brut_kar_orani, 0)],
      ["Ortalama tutma", o.ortalama_tutma_dk === null ? "—" : sayi(o.ortalama_tutma_dk, 0) + " dk"],
      ["Beklenen ort.", A.yuzde(o.beklenen_ortalama_yuzde, 3) || "—"],
      ["Sapma", A.yuzde(o.sapma_yuzde, 3) || "—", A.isaret(o.sapma_yuzde)],
    ]);
  }

  function sonuclarCiz(v) {
    const k = A.kutu("Sonuçlar (bu hesap dönemi)");
    const iki = el("div", "iki-sutun");
    [["Kural işlemleri", v.ozet.kural], ["Elle işlemler", v.ozet.elle]].forEach(function (c) {
      const s = el("div");
      s.appendChild(el("h3", null, c[0]));
      s.appendChild(ozetAlanlari(c[1]));
      iki.appendChild(s);
    });
    k.appendChild(iki);
    k.appendChild(el("p", "kart-etiket",
      "Kural ve elle işlemler ayrı sayılır: hangisinin ne getirdiği karışmasın. Oranlar " +
      "bütçeye göre değil, her işlemin kendi tutarına göredir."));
    if (v.performans.length) {
      k.appendChild(el("h3", null, "Kural performansı (beklentiyle kıyas)"));
      k.appendChild(tablo(["Kural", "İşlem", "Beklenen ort.", "Gerçekleşen ort.", "Fark (std. hata)", "Durum"],
        v.performans.map(function (p) {
          return [p.kural, p.islem, A.yuzde(p.beklenen_yuzde, 3), A.yuzde(p.gerceklesen_yuzde, 3),
            p.z === null ? "—" : sayi(p.z, 2),
            { metin: p.durduruldu ? "durduruldu" : p.bozuk ? "bozuk" : "izleniyor",
              sinif: p.durduruldu || p.bozuk ? "kotu" : null }];
        })));
    }
    if (v.canliya_gecis.length) {
      k.appendChild(el("h3", null, "Canlıya geçiş kapısı (bilgi; Faz 6'da zorunlu olacak)"));
      k.appendChild(tablo(["Kural", "Gün", "İşlem", "Durum"],
        v.canliya_gecis.map(function (g) {
          return [g.etiket, sayi(g.gun, 1), g.islem,
            { metin: g.gecti ? "koşul sağlandı" : "yetersiz", sinif: g.gecti ? "iyi" : "sari" }];
        })));
    }
    k.appendChild(el("h3", null, "Komisyon ve kayma"));
    Object.keys(v.maliyet).forEach(function (s) {
      k.appendChild(el("p", "kart-etiket", s + ": " + v.maliyet[s]));
    });
    return k;
  }

  function gecmisCiz(liste) {
    const k = A.kutu("İşlem geçmişi");
    const a = el("a", "dugme dugme-ikincil", "CSV indir (Excel, TRY sütunlu)");
    a.href = "/api/kagit/islemler.csv";
    a.setAttribute("download", "");
    k.appendChild(a);
    const bitmis = liste.filter(function (o) { return o.durum === "kapandi" || o.durum === "iptal"; });
    if (!bitmis.length) {
      k.appendChild(el("p", "kart-etiket", "Henüz kapanan ya da iptal edilen kâğıt emir yok."));
      return k;
    }
    k.appendChild(tablo(
      ["Açılış", "Coin", "Kaynak", "Giriş", "Çıkış", "Sonuç", "Net USDT", "Net %"],
      bitmis.slice(0, 50).map(function (o) {
        return [
          o.olusturma, o.sembol + " " + o.periyot, o.kaynak_tr, o.giris,
          o.durum === "iptal" ? "—" : o.cikis_fiyati,
          o.durum === "iptal" ? { metin: "iptal: " + tire(o.iptal_sebebi), sinif: "kart-etiket" }
            : o.cikis_sebebi_tr,
          o.durum === "iptal" ? "—" : { metin: o.net_usdt, sinif: A.isaret(parseFloat(o.net_usdt)) },
          o.durum === "iptal" ? "—" : { metin: A.yuzde(o.net_yuzde, 3), sinif: A.isaret(o.net_yuzde) },
        ];
      })));
    return k;
  }

  function piyasaCiz(p) {
    const kap = document.createDocumentFragment();
    kap.appendChild(tablo(
      ["Coin", "Alış", "Satış", "Spread", "Normal spread", "ATR", "ATR medyan", "24s hacim (USDT)", "BTC 60 dk", "Son veri"],
      p.coinler.map(function (c) {
        return [c.sembol, c.alis, c.satis,
          c.spread_yuzde === null ? "—" : "%" + sayi(c.spread_yuzde, 6),
          c.spread_medyan_yuzde === null ? "ölçülüyor (" + c.spread_gozlem + "/300)"
            : "%" + sayi(c.spread_medyan_yuzde, 6),
          c.atr_yuzde === null ? "—" : "%" + sayi(c.atr_yuzde, 3),
          c.atr_medyan_yuzde === null ? "—" : "%" + sayi(c.atr_medyan_yuzde, 3),
          c.hacim_24s_usdt, c.btc_60dk_yuzde === null ? "—" : "%" + sayi(c.btc_60dk_yuzde, 2),
          c.son_veri];
      })));
    p.coinler.forEach(function (c) {
      kap.appendChild(el("h3", null, c.sembol + " " + c.periyot + " — yeni işlem için piyasa kapıları"));
      kap.appendChild(kapiListesi(c.kapilar));
      c.notlar.forEach(function (n) { kap.appendChild(el("p", "kart-etiket", n)); });
    });
    const b = p.baglanti;
    if (b) {
      kap.appendChild(el("h3", null, "Bağlantı ve istek bütçesi"));
      const butce = b.istek_butcesi || {};
      kap.appendChild(A.alanlar([
        ["Akış", b.akis_bagli ? "bağlı" : "kopuk", b.akis_bagli ? "iyi" : "kotu"],
        ["Yeniden bağlanma", String(b.akis_yeniden_baglanma)],
        ["Yedek yoklama", b.yedek_yoklama ? "çalışıyor" : "gerekmiyor"],
        ["Saat farkı", b.saat_farki_ms === null ? "—" : b.saat_farki_ms + " ms"],
        ["İstek (bu dakika)", tire(butce.yerel_1dk) + " / " + tire(butce.yerel_tavan)],
        ["Borsa sayacı", tire(butce.borsa_1dk) + " / " + tire(butce.borsa_siniri)],
        ["Reddedilen istek", tire(butce.reddedilen)],
        ["Uyku / uyanma", String(b.uyku_sayisi)],
      ]));
      kap.appendChild(el("p", "kart-etiket",
        "İstek sayısı bu uygulamanın kendi tavanıyla sınırlı; tavana gelirse istek " +
        "gönderilmeden beklenir. Borsa 429 derse söylediği süre boyunca hiç istek gitmez."));
      if (b.uyku_engeli) {
        kap.appendChild(el("p", b.uyku_engeli.pilde ? "sari" : "kart-etiket",
          "Uyku engeli: " + b.uyku_engeli.aciklama));
      }
      if (b.son_uzlastirma_ozet) {
        kap.appendChild(el("p", "kart-etiket", "Son uzlaştırma: " + b.son_uzlastirma_ozet));
      }
      if (b.son_hata) kap.appendChild(el("p", "sari", "Son hata: " + b.son_hata));
      if (b.olaylar && b.olaylar.length) {
        kap.appendChild(A.liste(b.olaylar.slice(0, 10)));
      }
    }
    if (p.usdttry) kap.appendChild(el("p", "kart-etiket", "USDT/TRY: " + p.usdttry));
    return kap;
  }

  // --- elle emir ---------------------------------------------------------------

  const form = document.getElementById("elle-form");

  function elleBaslik() {
    document.getElementById("elle-baslik").textContent =
      "Elle kâğıt emir · " + A.sembol() + " " + A.periyot();
    const c = coinPiyasa(A.sembol());
    const ipucu = document.getElementById("elle-ipucu");
    if (!c || !c.alis) {
      ipucu.textContent = "Güncel fiyat henüz yok.";
      return;
    }
    let metin = "En iyi alış " + c.alis + ", en iyi satış " + c.satis + ".";
    if (c.atr_yuzde !== null) {
      metin += " Bu periyotta tipik mum hareketi (ATR 14) %" + sayi(c.atr_yuzde, 3) +
        (c.atr_medyan_yuzde !== null ? " (30 gün medyanı %" + sayi(c.atr_medyan_yuzde, 3) + ")" : "") +
        ". Hedef komisyon ve kayma düşüldükten sonra da kârda kalmalı.";
    }
    ipucu.textContent = metin;
  }

  function elleSonucCiz(veri, acildi) {
    const k = el("div", "elle-sonuc " + (veri.karar.izin ? "" : "kutu-uyari"));
    k.appendChild(el("p", veri.karar.izin ? "iyi" : "sari",
      acildi ? "Kâğıt emir açıldı." : veri.karar.ozet));
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
    k.appendChild(el("h3", null, "Risk kapıları"));
    k.appendChild(kapiListesi(veri.karar.kapilar));
    return k;
  }

  form.addEventListener("submit", async function (olay) {
    olay.preventDefault();
    const hangi = olay.submitter && olay.submitter.dataset.eylem === "ac" ? "ac" : "sina";
    const alan = new FormData(form);
    const govde = {
      sembol: A.sembol(), periyot: A.periyot(),
      giris: (alan.get("giris") || "").trim(),
      hedef: (alan.get("hedef") || "").trim(),
      stop: (alan.get("stop") || "").trim(),
    };
    const dugmeler = form.querySelectorAll("button");
    dugmeler.forEach(function (d) { d.disabled = true; });
    try {
      let veri;
      if (hangi === "sina") {
        veri = await A.getir("/api/kagit/on-izleme?" + new URLSearchParams(govde).toString());
        A.yaz("elle-sonuc", elleSonucCiz(veri, false));
      } else {
        govde.gecerlilik_mum = parseInt(alan.get("gecerlilik_mum"), 10) || 1;
        govde.azami_tutma_mum = parseInt(alan.get("azami_tutma_mum"), 10) || 4;
        veri = await A.gonder("/api/kagit/emir", govde);
        A.yaz("elle-sonuc", elleSonucCiz(veri, !!veri.emir));
        if (veri.emir) {
          A.bildir(veri.emir.sembol + " kâğıt giriş emri açıldı: " + veri.emir.miktar + " @ " +
            veri.emir.giris + ".", "iyi");
          await yenile();
        } else {
          A.bildir("Kâğıt emir açılmadı: " + veri.karar.ozet, "kotu");
        }
      }
    } catch (hata) {
      A.yaz("elle-sonuc", A.hataKutusu(hata));
    } finally {
      dugmeler.forEach(function (d) { d.disabled = false; });
    }
  });

  document.getElementById("elle-fiyat").addEventListener("click", function () {
    const c = coinPiyasa(A.sembol());
    if (c && c.alis) form.elements.giris.value = c.alis;
    else A.bildir("Güncel fiyat henüz yok.", "kotu");
  });

  // --- risk limitleri (yalnızca açılınca ve kaydedince çizilir) ----------------

  async function limitleriYukle() {
    let veri;
    try {
      veri = await A.getir("/api/kagit/limitler");
    } catch (hata) {
      A.yaz("kagit-limitler", A.hataKutusu(hata));
      return;
    }
    const f = el("form", "limit-form");
    f.setAttribute("autocomplete", "off");
    const ilk = {};
    veri.limitler.forEach(function (l) {
      ilk[l.ad] = l.deger;
      const satir = el("label", "limit-satir");
      const bas = el("span", "limit-ad", l.etiket + " (" + l.birim + ")");
      satir.appendChild(bas);
      const giris = el("input");
      giris.name = l.ad;
      giris.value = l.deger;
      giris.setAttribute("inputmode", "decimal");
      satir.appendChild(giris);
      satir.appendChild(el("span", "kart-etiket",
        l.aciklama + " Aralık " + l.alt + "–" + l.ust + ", varsayılan " + l.varsayilan + "."));
      f.appendChild(satir);
    });
    const kaydet = el("button", "dugme", "Değişiklikleri kaydet");
    kaydet.type = "submit";
    f.appendChild(kaydet);
    f.appendChild(el("p", "kart-etiket", sonDurum ? sonDurum.butce_notu : ""));
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
      const sonuc = await eylem("/api/kagit/limitler", { degerler: degerler }, function (s) {
        return Object.keys(s.degisen).length + " limit güncellendi.";
      });
      kaydet.disabled = false;
      if (sonuc) limitleriYukle();
    });
    A.yaz("kagit-limitler", f);
  }

  async function bildirimleriYukle() {
    let veri;
    try { veri = await A.getir("/api/bildirimler?adet=50"); } catch (hata) {
      A.yaz("kagit-bildirimler", A.hataKutusu(hata));
      return;
    }
    cizDegistiyse("kagit-bildirimler", veri, function (v) {
      const kap = document.createDocumentFragment();
      const tg = v.telegram;
      kap.appendChild(el("p", tg.kurulu ? "iyi" : "kart-etiket",
        tg.kurulu ? "Telegram bağlı: @" + tg.bot + ". Komutlar: /durum, /durdur, /yardim."
          : tg.aciklama));
      if (tg.son_hata) kap.appendChild(el("p", "sari", "Son Telegram hatası: " + tg.son_hata));
      if (!v.bildirimler.length) {
        kap.appendChild(el("p", "kart-etiket", "Bu oturumda henüz bildirim yok."));
        return kap;
      }
      kap.appendChild(tablo(["Zaman", "Tür", "Mesaj", "Telefona"],
        v.bildirimler.map(function (b) {
          const m = el("div", "bildirim-metin", b.metin);
          return [b.zaman, b.tur, m, tg.kurulu ? (b.gonderildi ? "✓" : "gönderilemedi") : "—"];
        })));
      return kap;
    });
  }

  async function denetimiYukle() {
    let veri;
    try { veri = await A.getir("/api/denetim?adet=100"); } catch (hata) {
      A.yaz("kagit-denetim", A.hataKutusu(hata));
      return;
    }
    cizDegistiyse("kagit-denetim", veri, function (v) {
      const kap = document.createDocumentFragment();
      kap.appendChild(el("p", "kart-etiket",
        "Mod değişikliği, emir, iptal, limit değişikliği, acil durdurma: kim (arayüz, " +
        "Telegram, canlı döngü, risk motoru) ne zaman ne yaptı. Silinmez."));
      kap.appendChild(tablo(["Zaman", "Kaynak", "Olay", "Özet"],
        v.kayitlar.map(function (k) {
          return [k.zaman, k.kaynak_tr, k.tur, { metin: k.ozet, sinif: "sol" }];
        })));
      return kap;
    });
  }

  function detayAcik(id) {
    const d = document.getElementById(id);
    return d && d.open;
  }

  document.getElementById("kagit-limitler-kutu").addEventListener("toggle", function () {
    if (this.open) limitleriYukle();
  });
  document.getElementById("kagit-bildirim-kutu").addEventListener("toggle", function () {
    if (this.open) { yeniden("kagit-bildirimler"); bildirimleriYukle(); }
  });
  document.getElementById("kagit-denetim-kutu").addEventListener("toggle", function () {
    if (this.open) { yeniden("kagit-denetim"); denetimiYukle(); }
  });
  document.getElementById("kagit-piyasa-kutu").addEventListener("toggle", function () {
    if (this.open && sonPiyasa) { yeniden("kagit-piyasa"); cizDegistiyse("kagit-piyasa", sonPiyasa, piyasaCiz); }
  });

  document.getElementById("sifirla-form").addEventListener("submit", async function (olay) {
    olay.preventDefault();
    const onay = (new FormData(this).get("onay") || "").trim();
    const sonuc = await eylem("/api/kagit/hesap-sifirla", { onay: onay }, function (s) {
      return "Kâğıt hesap sıfırlandı; dönem " + s.donem_id + " başladı.";
    });
    if (sonuc) this.reset();
  });

  // --- yenileme ------------------------------------------------------------------

  async function yenile() {
    if (yukleniyor) return;
    yukleniyor = true;
    try {
      const sonuclar = await Promise.all([
        A.getir("/api/kagit/durum"),
        A.getir("/api/kagit/piyasa?periyot=" + encodeURIComponent(A.periyot())),
        A.getir("/api/kagit/islemler?adet=100"),
      ]);
      const d = sonuclar[0];
      const p = sonuclar[1];
      sonDurum = d;
      sonPiyasa = p;
      A.modRozeti(d.modlar);
      cizDegistiyse("kagit-baglanti", { canli: p.canli, baglanti: p.baglanti, telegram: d.telegram },
        baglantiCiz);
      cizDegistiyse("kagit-modlar", {
        modlar: d.modlar, secilebilir: d.secilebilir_modlar, kilitli: d.kilitli_modlar,
        canli: d.canli,
      }, modlarCiz);
      cizDegistiyse("kagit-hesap", d.hesap, hesapCiz);
      cizDegistiyse("kagit-risk", { risk: d.risk, durdurulan: d.durdurulan_kurallar }, riskCiz);
      cizDegistiyse("kagit-aktif", d.aktif, aktifCiz);
      cizDegistiyse("kagit-sonuclar", {
        ozet: d.ozet, performans: d.kural_performansi, canliya_gecis: d.canliya_gecis,
        maliyet: d.maliyet,
      }, sonuclarCiz);
      cizDegistiyse("kagit-gecmis", sonuclar[2].islemler, gecmisCiz);
      if (detayAcik("kagit-piyasa-kutu")) cizDegistiyse("kagit-piyasa", p, piyasaCiz);
      elleBaslik();
    } catch (hata) {
      yeniden("kagit-baglanti");
      A.yaz("kagit-baglanti", A.hataKutusu(hata));
    } finally {
      yukleniyor = false;
    }
    if (detayAcik("kagit-bildirim-kutu")) bildirimleriYukle();
    if (detayAcik("kagit-denetim-kutu")) denetimiYukle();
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

  window.Kagit = { etkin: etkin, yenile: function () { if (acik) return yenile(); } };
})();
