/* Sadece Öneri arayüzü (Faz 3).
 *
 * Çerçeve yok, derleme adımı yok: sayfa doğrudan Python paketinden
 * sunuluyor. Sunucudan gelen parasal değerler METİNDİR ve öyle gösterilir;
 * burada hiçbir fiyat sayıya çevrilip yeniden hesaplanmaz. Hesap sunucuda,
 * Decimal ile yapılır (SPEC §3).
 */
(function () {
  "use strict";

  const durumEl = document.getElementById("veri-ozeti");
  const sembolEl = document.getElementById("sembol");
  const periyotEl = document.getElementById("periyot");
  let durum = null;
  let ornekGoster = false;

  // --- yardımcılar ---------------------------------------------------

  function el(etiket, sinif, metin) {
    const dugum = document.createElement(etiket);
    if (sinif) dugum.className = sinif;
    if (metin !== undefined && metin !== null) dugum.textContent = metin;
    return dugum;
  }

  function kutu(baslik, sinif) {
    const d = el("div", "kutu" + (sinif ? " " + sinif : ""));
    if (baslik) d.appendChild(el("h2", null, baslik));
    return d;
  }

  function alanlar(ciftler) {
    const kap = el("div", "alanlar");
    ciftler.forEach(function (cift) {
      if (cift[1] === null || cift[1] === undefined || cift[1] === "") return;
      const a = el("div", "alan");
      a.appendChild(el("div", "alan-ad", cift[0]));
      a.appendChild(el("div", "alan-deger" + (cift[2] ? " " + cift[2] : ""), cift[1]));
      kap.appendChild(a);
    });
    return kap;
  }

  function liste(maddeler, sinif) {
    const ul = el("ul", "liste" + (sinif ? " " + sinif : ""));
    maddeler.forEach(function (madde) { ul.appendChild(el("li", null, madde)); });
    return ul;
  }

  function yuzde(deger, basamak) {
    if (deger === null || deger === undefined) return null;
    const b = basamak === undefined ? 4 : basamak;
    return "%" + (deger >= 0 ? "+" : "") + deger.toFixed(b);
  }

  // Oranlar (isabet, hedefe çıkış) işaretsiz yazılır: bunlar bir marj değil,
  // bir yüzdedir; başına "+" koymak kâr gibi okunmasına yol açıyor.
  function oran(deger, basamak) {
    if (deger === null || deger === undefined) return null;
    const b = basamak === undefined ? 1 : basamak;
    return "%" + deger.toFixed(b);
  }

  function isaret(deger) {
    if (deger === null || deger === undefined) return null;
    return deger > 0 ? "iyi" : (deger < 0 ? "kotu" : null);
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
        if (hucre && typeof hucre === "object" && "metin" in hucre) {
          str.appendChild(el("td", hucre.sinif, hucre.metin));
        } else {
          str.appendChild(el("td", null, hucre === null || hucre === undefined ? "—" : String(hucre)));
        }
      });
      tbody.appendChild(str);
    });
    t.appendChild(tbody);
    sar.appendChild(t);
    return sar;
  }

  function saat(iso) {
    if (!iso) return "—";
    const t = new Date(iso);
    if (isNaN(t.getTime())) return iso;
    return t.toLocaleString("tr-TR", { timeZone: "Europe/Istanbul" });
  }

  async function getir(yol) {
    const yanit = await fetch(yol);
    let govde = null;
    try { govde = await yanit.json(); } catch (hata) { govde = null; }
    if (!yanit.ok) {
      const mesaj = govde && govde.detail ? govde.detail : "Sunucu hatası (" + yanit.status + ")";
      throw new Error(mesaj);
    }
    return govde;
  }

  function hataKutusu(hata) {
    const k = kutu("Bir şey eksik", "kutu-uyari");
    k.appendChild(el("p", null, hata.message));
    return k;
  }

  function yaz(hedefId, dugum) {
    const hedef = document.getElementById(hedefId);
    hedef.innerHTML = "";
    hedef.appendChild(dugum);
  }

  // --- açılış --------------------------------------------------------

  async function baslat() {
    try {
      durum = await getir("/api/durum");
    } catch (hata) {
      durumEl.textContent = "Sunucuya ulaşılamadı.";
      return;
    }

    document.getElementById("mod-rozeti").textContent = durum.mod_tr;
    document.getElementById("uyari-metni").textContent = durum.uyari;

    durum.semboller.forEach(function (s) {
      sembolEl.appendChild(new Option(s, s));
    });
    durum.periyotlar.forEach(function (p) {
      periyotEl.appendChild(new Option(p, p));
    });

    ustOzet();
    saglik();
    sekmeleriBagla();
    formlariBagla();

    sembolEl.addEventListener("change", tazele);
    periyotEl.addEventListener("change", tazele);
    document.getElementById("yenile").addEventListener("click", function () {
      ornekGoster = false;
      tazele();
    });
    document.getElementById("ornek-dugme").addEventListener("click", function () {
      ornekGoster = !ornekGoster;
      this.textContent = ornekGoster
        ? "Örnek kartı gizle"
        : "Kart şablonunu örnek kuralla göster";
      onerileriYukle();
    });

    tazele();
  }

  function ustOzet() {
    const parcalar = [];
    if (durum.kural_deposu) {
      const k = durum.kural_deposu;
      parcalar.push(
        k.kabul_edilen_kural + " kabul edilen kural · " +
        k.toplam_aday.toLocaleString("tr-TR") + " aday denendi"
      );
    } else if (durum.kural_deposu_hatasi) {
      parcalar.push("Kural deposu okunamadı");
    } else {
      parcalar.push("Kural deposu yok");
    }
    const bayat = durum.veri.filter(function (v) {
      return v.gecikme_mum !== null && v.gecikme_mum > 2;
    });
    if (bayat.length) parcalar.push(bayat.length + " seri güncel değil");
    if (!durum.filtreler) parcalar.push("Borsa filtreleri indirilmemiş");
    durumEl.textContent = parcalar.join(" · ");
  }

  async function saglik() {
    try {
      const s = await getir("/api/saglik");
      document.getElementById("saglik-satiri").textContent =
        "Mod: " + s.mod + " · Emir yetkisi: " + s.emir_yetkisi +
        " · API anahtarı: " + s.api_anahtari + " · Veri: " + s.veri_dizini;
    } catch (hata) { /* sağlık satırı olmadan da çalışır */ }
  }

  function sekmeleriBagla() {
    document.querySelectorAll(".sekme").forEach(function (dugme) {
      dugme.addEventListener("click", function () {
        document.querySelectorAll(".sekme").forEach(function (d) { d.classList.remove("etkin"); });
        dugme.classList.add("etkin");
        document.querySelectorAll(".panel").forEach(function (p) { p.classList.add("gizli"); });
        document.getElementById("panel-" + dugme.dataset.sekme).classList.remove("gizli");
        tazele(dugme.dataset.sekme);
      });
    });
  }

  function etkinSekme() {
    const d = document.querySelector(".sekme.etkin");
    return d ? d.dataset.sekme : "oneriler";
  }

  function tazele(sekme) {
    const hangi = sekme || etkinSekme();
    if (hangi === "oneriler") { onerileriYukle(); grafikYukle(); }
    if (hangi === "sihirbaz") sihirbazYukle();
    if (hangi === "kutuphane") kutuphaneYukle();
    if (hangi === "ek") izlemeYukle();
    if (hangi === "gunluk") gunlukYukle();
  }

  // --- öneriler ------------------------------------------------------

  async function onerileriYukle() {
    const hedef = document.getElementById("oneri-icerik");
    hedef.innerHTML = "";
    hedef.appendChild(el("p", "bekle", "Hesaplanıyor…"));

    const sorgu = "?sembol=" + encodeURIComponent(sembolEl.value) +
      "&periyot=" + encodeURIComponent(periyotEl.value) +
      (ornekGoster ? "&ornek=true" : "");
    let veri;
    try {
      veri = await getir("/api/oneriler" + sorgu);
    } catch (hata) {
      yaz("oneri-icerik", hataKutusu(hata));
      return;
    }

    const kap = document.createDocumentFragment();

    if (veri.ornek) {
      const u = kutu("Örnek kart", "kutu-uyari");
      u.appendChild(el("p", null, veri.ornek_notu));
      kap.appendChild(u);
    }

    if (veri.veri && veri.veri.bayat) {
      const u = kutu("Veri güncel değil", "kutu-uyari");
      u.appendChild(el("p", null, veri.veri.aciklama));
      kap.appendChild(u);
    }

    if (veri.aciklama && veri.kartlar.length === 0) {
      const k = kutu(veri.aciklama.baslik, "kutu-bilgi");
      k.appendChild(liste(veri.aciklama.satirlar));
      if (veri.aciklama.bolumler && veri.aciklama.bolumler.length) {
        k.appendChild(el("h3", null, "Taranan bölümler"));
        k.appendChild(tablo(
          ["Pencere", "Uygun mum", "Aday", "Taban net%", "En iyi ham p", "Eşiğin katı"],
          veri.aciklama.bolumler.map(function (b) {
            return [
              b.pencere_mum + " mum",
              b.uygun_mum.toLocaleString("tr-TR"),
              b.aday.toLocaleString("tr-TR"),
              { metin: yuzde(b.taban_net_ortalama_yuzde), sinif: isaret(b.taban_net_ortalama_yuzde) },
              b.en_iyi_ham_p.toExponential(2),
              b.esikten_uzaklik_kati.toLocaleString("tr-TR") + "×",
            ];
          })
        ));
      }
      kap.appendChild(k);
    }

    veri.kartlar.forEach(function (kart) { kap.appendChild(kartCiz(kart)); });

    hedef.innerHTML = "";
    hedef.appendChild(kap);
  }

  function kartCiz(kart) {
    const k = kutu(null, "kutu");
    const bas = el("div", "kart-baslik");
    const sol = el("div");
    sol.appendChild(el("h2", null, kart.aksiyon + " · " + kart.sembol + " " + kart.periyot));
    sol.appendChild(el("div", "kart-etiket", kart.kural_etiketi));
    bas.appendChild(sol);
    const guven = el("span", "rozet " + (kart.guven.puan >= 75 ? "rozet-iyi" : kart.guven.puan >= 50 ? "rozet-uyari" : "rozet-kotu"),
      "Güven " + kart.guven.puan + "/100 (" + kart.guven.seviye + ")");
    bas.appendChild(guven);
    k.appendChild(bas);

    k.appendChild(el("p", null, kart.aksiyon_aciklama));

    k.appendChild(alanlar([
      ["Giriş (limit)", kart.giris],
      ["Hedef 1", kart.hedef1],
      ["Hedef 2", kart.hedef2 || "yok"],
      ["Stop", kart.stop],
      ["Başa-baş", kart.basa_bas],
      ["Brüt marj", yuzde(kart.brut_marj_yuzde), isaret(kart.brut_marj_yuzde)],
      ["Net marj", yuzde(kart.net_marj_yuzde), isaret(kart.net_marj_yuzde)],
      ["Stopta net", yuzde(kart.stop_net_marj_yuzde), isaret(kart.stop_net_marj_yuzde)],
      ["Risk / ödül", kart.risk_odul === null ? "—" : kart.risk_odul.toFixed(2)],
      ["Miktar", kart.pozisyon.miktar],
      ["Tutar (USDT)", kart.pozisyon.tutar_usdt],
      ["Stop zararı (USDT)", kart.pozisyon.stop_zarari_usdt, "kotu"],
      ["Geçerlilik", kart.gecerlilik_mum + " mum → " + kart.gecerlilik_bitis_istanbul],
      /* İsabet oranı TÜM olaylar üzerinde ölçüldü (n). Üst üste binen
         olaylar istatistiği şişirdiği için bağımsız olay sayısı da
         yazılıyor; kabul kararı ona dayanıyor. */
      ["Tarihsel isabet", oran(100 * kart.kanit.isabet_orani) +
        " (n=" + kart.kanit.olay + ", bağımsız " + kart.kanit.bagimsiz_olay + ")"],
      ["Hedefe çıkış", oran(100 * kart.kanit.hedefe_cikis_orani)],
      ["Stopa çıkış", oran(100 * kart.kanit.stopa_cikis_orani)],
      ["Ortalama tutuş", kart.kanit.ortalama_tutulan_mum.toFixed(1) + " mum"],
    ]));

    if (kart.hedef2_notu) k.appendChild(el("p", "kart-etiket", kart.hedef2_notu));

    k.appendChild(el("h3", null, "Pozisyon büyüklüğü"));
    k.appendChild(el("p", null, kart.pozisyon.aciklama));
    k.appendChild(el("p", "kart-etiket",
      "stepSize yuvarlaması ham miktarın %" + kart.pozisyon.yuvarlama_kaybi_yuzde.toFixed(4) +
      "'ini götürdü. Bütçe kullanımı %" + kart.pozisyon.butce_kullanim_yuzde.toFixed(1) + "."));

    k.appendChild(el("h3", null, "Güven skoru nasıl hesaplandı"));
    k.appendChild(tablo(["Bileşen", "Puan", "Açıklama"],
      kart.guven.bilesenler.map(function (b) {
        return [b.ad, b.puan.toFixed(0) + " / " + b.azami.toFixed(0), { metin: b.aciklama, sinif: "kart-etiket" }];
      })));
    if (kart.guven.ceza) {
      k.appendChild(el("p", "kart-etiket", "Uyarı cezası: −" + kart.guven.ceza));
    }

    k.appendChild(el("h3", null, "Gerekçe"));
    k.appendChild(liste(kart.gerekce));

    k.appendChild(el("h3", null, "Maliyet"));
    k.appendChild(el("p", "kart-etiket", kart.maliyet_ozeti));

    if (kart.uyarilar.length) {
      k.appendChild(el("h3", null, "Uyarılar"));
      k.appendChild(liste(kart.uyarilar, "uyari-liste"));
    }
    return k;
  }

  async function grafikYukle() {
    const tuval = document.getElementById("grafik");
    let veri;
    try {
      veri = await getir("/api/mumlar?sembol=" + encodeURIComponent(sembolEl.value) +
        "&periyot=" + encodeURIComponent(periyotEl.value) + "&adet=180");
    } catch (hata) {
      document.getElementById("grafik-alt").textContent = hata.message;
      return;
    }
    window.Grafik.ciz(tuval, veri.mumlar, []);
    const alt = document.getElementById("grafik-alt");
    if (veri.mumlar.length === 0) {
      alt.textContent = "Bu sembol ve periyot için kayıtlı mum yok.";
    } else {
      const son = veri.mumlar[veri.mumlar.length - 1];
      alt.textContent = veri.mumlar.length + " kapanmış mum · son kapanış " +
        saat(new Date(son.t).toISOString());
    }
  }

  // --- periyot sihirbazı ---------------------------------------------

  async function sihirbazYukle() {
    let veri;
    try {
      veri = await getir("/api/sihirbaz?sembol=" + encodeURIComponent(sembolEl.value));
    } catch (hata) {
      yaz("sihirbaz-icerik", hataKutusu(hata));
      return;
    }
    const k = kutu(veri.sembol + " — periyot karşılaştırması");
    k.appendChild(tablo(
      ["Periyot", "Mum", "ATR% medyan", "Min hedef%", "Oran", "Sinyal/gün", "Taban net%", "Al-tut net%", "Maks düşüş%", "Günlük hacim", "Sonuç"],
      veri.satirlar.map(function (s) {
        return [
          s.periyot,
          s.mum.toLocaleString("tr-TR"),
          s.atr_yuzde_medyan.toFixed(4),
          s.minimum_hedef_yuzde.toFixed(4),
          { metin: s.oran.toFixed(2), sinif: s.uygun ? "iyi" : (s.oran < 1 ? "kotu" : "sari") },
          s.gunluk_sinyal.toFixed(2),
          { metin: yuzde(s.taban_net_ortalama_yuzde), sinif: isaret(s.taban_net_ortalama_yuzde) },
          { metin: s.al_tut_net_yuzde.toFixed(2), sinif: isaret(s.al_tut_net_yuzde) },
          s.al_tut_max_dusus_yuzde.toFixed(2),
          Math.round(s.medyan_gunluk_hacim_usdt).toLocaleString("tr-TR"),
          { metin: s.sonuc, sinif: s.sonuc === "UYGUN" ? "iyi" : (s.sonuc === "SINIRDA" ? "sari" : "kotu") },
        ];
      })
    ));
    k.appendChild(el("h3", null, "Önerilen"));
    k.appendChild(el("p", null, veri.onerilen.length ? veri.onerilen.join(", ") : "Yok — hiçbir periyot maliyet eşiğini geçmiyor."));
    k.appendChild(el("h3", null, "Gerekçe"));
    k.appendChild(liste(veri.gerekce));
    k.appendChild(el("p", "kart-etiket", veri.karar_notu));
    yaz("sihirbaz-icerik", k);
  }

  // --- örüntü kütüphanesi --------------------------------------------

  async function kutuphaneYukle() {
    let veri;
    try {
      veri = await getir("/api/kurallar");
    } catch (hata) {
      yaz("kutuphane-icerik", hataKutusu(hata));
      return;
    }
    const kap = document.createDocumentFragment();

    const ozet = kutu("Tarama özeti", "kutu-bilgi");
    ozet.appendChild(alanlar([
      ["Kabul edilen kural", String(veri.kosu.kabul_edilen_kural), veri.kosu.kabul_edilen_kural ? "iyi" : null],
      ["Denenen aday", veri.kosu.toplam_aday.toLocaleString("tr-TR")],
      ["Kabul eşiği (p)", veri.kosu.kabul_esigi_p.toExponential(2)],
      ["Yanlış buluş payı", "%" + (veri.kosu.alpha * 100).toFixed(0)],
      ["Koşu zamanı", saat(veri.kosu.kosu_zamani_utc)],
      ["Veri aralığı", veri.kosu.veri_baslangic_utc ? (veri.kosu.veri_baslangic_utc.slice(0, 10) + " → " + veri.kosu.veri_bitis_utc.slice(0, 10)) : null],
    ]));
    ozet.appendChild(el("p", "kart-etiket", veri.maliyet_esigi));
    if (veri.kosu.teshis_turu) {
      ozet.appendChild(el("p", "sari", "Bu depo TEŞHİS turundan geliyor; maliyet sıfır sayıldı ve buradan öneri üretilmez."));
    }
    kap.appendChild(ozet);

    const kabul = kutu("Kabul edilen kurallar");
    if (veri.kabul_edilenler.length === 0) {
      kabul.appendChild(el("p", null,
        "Kabul edilen kural yok. Tarama " + veri.kosu.toplam_aday.toLocaleString("tr-TR") +
        " aday denedi ve hiçbiri çoklu test düzeltmesinden geçmedi. " +
        "Bu bir arıza değil, ölçülmüş bir sonuçtur."));
    } else {
      veri.kabul_edilenler.forEach(function (k) { kabul.appendChild(kuralCiz(k)); });
    }
    kap.appendChild(kabul);

    const adaylar = kutu("İncelenen adaylar — kabul EDİLMEDİ", "aday-kutu");
    adaylar.appendChild(el("p", "sari", veri.incelenen_aday_notu));
    if (veri.incelenen_adaylar.length === 0) {
      adaylar.appendChild(el("p", null, "Not düşülecek aday da çıkmadı."));
    } else {
      adaylar.appendChild(tablo(
        ["Örüntü", "Sembol", "Periyot", "Yön", "Olay", "Net ort.%", "Ham p", "q", "Durum"],
        veri.incelenen_adaylar.map(function (k) {
          return [
            k.etiket, k.sembol, k.periyot, k.yon,
            k.kanit.olay,
            { metin: yuzde(k.kanit.net_ortalama_yuzde), sinif: isaret(k.kanit.net_ortalama_yuzde) },
            k.kanit.p_degeri.toExponential(2),
            k.kanit.q_degeri.toFixed(3),
            { metin: "KABUL EDİLMEDİ", sinif: "sari" },
          ];
        })
      ));
    }
    kap.appendChild(adaylar);

    yaz("kutuphane-icerik", kap);
  }

  function kuralCiz(k) {
    const d = el("div", "kutu");
    d.appendChild(el("h2", null, k.etiket));
    d.appendChild(el("div", "kart-etiket", k.sembol + " " + k.periyot + " · " + k.pencere_mum + " mum · yön: " + k.yon));
    d.appendChild(alanlar([
      ["Olay", String(k.kanit.olay)],
      ["Bağımsız olay", String(k.kanit.bagimsiz_olay)],
      ["Kabul örneği", String(k.kanit.kabul_ornegi)],
      ["İsabet", oran(100 * k.kanit.isabet_orani)],
      ["Net ortalama", yuzde(k.kanit.net_ortalama_yuzde), isaret(k.kanit.net_ortalama_yuzde)],
      ["Güven aralığı", yuzde(k.kanit.guven_alt_yuzde) + " … " + yuzde(k.kanit.guven_ust_yuzde)],
      ["q-değeri", k.kanit.q_degeri.toFixed(4)],
      ["Test dönemi", yuzde(k.kanit.test_donemi_net_yuzde), isaret(k.kanit.test_donemi_net_yuzde)],
    ]));
    if (k.uyarilar.length) d.appendChild(liste(k.uyarilar, "uyari-liste"));
    return d;
  }

  // --- B eki: izleme --------------------------------------------------

  async function izlemeYukle() {
    let veri;
    try {
      veri = await getir("/api/izleme");
    } catch (hata) {
      yaz("izleme-icerik", hataKutusu(hata));
      return;
    }
    const kap = document.createDocumentFragment();
    kap.appendChild(tablo(
      ["Sembol", "Periyot", "Son fiyat", "1g%", "7g%", "30g%", "ATR%", "Maliyetin katı", "Veri"],
      veri.satirlar.map(function (s) {
        return [
          s.sembol, s.periyot, s.son_fiyat,
          { metin: s.degisim_1g_yuzde.toFixed(2), sinif: isaret(s.degisim_1g_yuzde) },
          { metin: s.degisim_7g_yuzde.toFixed(2), sinif: isaret(s.degisim_7g_yuzde) },
          { metin: s.degisim_30g_yuzde.toFixed(2), sinif: isaret(s.degisim_30g_yuzde) },
          s.atr_yuzde.toFixed(4),
          { metin: s.oynaklik_orani.toFixed(2) + "×", sinif: s.oynaklik_orani >= 2 ? "iyi" : (s.oynaklik_orani < 1 ? "kotu" : "sari") },
          { metin: s.bayat ? "güncel değil" : "güncel", sinif: s.bayat ? "kotu" : "iyi" },
        ];
      })
    ));
    veri.satirlar.forEach(function (s) {
      kap.appendChild(el("p", "kart-etiket", s.sembol + " " + s.periyot + ": " + s.oynaklik_notu + " " + s.oynaklik_karsilastirma));
    });
    kap.appendChild(el("p", "kart-etiket", veri["not"]));
    yaz("izleme-icerik", kap);
  }

  // --- B eki: formlar -------------------------------------------------

  function formlariBagla() {
    document.getElementById("mr-form").addEventListener("submit", async function (olay) {
      olay.preventDefault();
      const alan = new FormData(this);
      const sorgu = new URLSearchParams({ sembol: sembolEl.value });
      ["giris", "hedef", "stop", "butce", "risk", "try_kuru"].forEach(function (ad) {
        const deger = (alan.get(ad) || "").trim();
        if (deger) sorgu.set(ad, deger);
      });
      try {
        yaz("mr-icerik", maliyetRiskCiz(await getir("/api/maliyet-risk?" + sorgu.toString())));
      } catch (hata) {
        yaz("mr-icerik", hataKutusu(hata));
      }
    });

    document.getElementById("plan-form").addEventListener("submit", async function (olay) {
      olay.preventDefault();
      const alan = new FormData(this);
      const sorgu = new URLSearchParams({ sembol: sembolEl.value, periyot: periyotEl.value });
      ["butce", "dilim", "kip", "aralik_gun", "basamak_yuzde"].forEach(function (ad) {
        const deger = (alan.get(ad) || "").trim();
        if (deger) sorgu.set(ad, deger);
      });
      try {
        yaz("plan-icerik", planCiz(await getir("/api/plan?" + sorgu.toString())));
      } catch (hata) {
        yaz("plan-icerik", hataKutusu(hata));
      }
    });
  }

  function maliyetRiskCiz(p) {
    const k = kutu(null, p.hedef_esigi_geciyor ? "kutu" : "kutu kutu-uyari");
    k.appendChild(el("p", p.hedef_esigi_geciyor ? null : "sari", p.sonuc));
    k.appendChild(alanlar([
      ["Giriş", p.giris],
      ["Hedef", p.hedef],
      ["Stop", p.stop],
      ["Başa-baş", p.basa_bas],
      ["Brüt marj", yuzde(p.brut_marj_yuzde), isaret(p.brut_marj_yuzde)],
      ["Net marj", yuzde(p.net_marj_yuzde), isaret(p.net_marj_yuzde)],
      ["Stopta net", yuzde(p.stop_net_marj_yuzde), isaret(p.stop_net_marj_yuzde)],
      ["Risk / ödül", p.risk_odul === null ? "—" : p.risk_odul.toFixed(2)],
      ["Miktar", p.pozisyon.miktar],
      ["Tutar (USDT)", p.pozisyon.tutar_usdt],
      ["Hedefte kazanç", p.hedef_kazanc_usdt, "iyi"],
      ["Stopta zarar", p.stop_zarari_usdt, "kotu"],
      ["Ödenecek komisyon", p.odenecek_komisyon_usdt],
      ["Hedefte kazanç (TRY)", p.hedef_kazanc_try],
      ["Stopta zarar (TRY)", p.stop_zarari_try],
    ]));
    k.appendChild(el("h3", null, "Maliyet eşiği"));
    k.appendChild(el("p", "kart-etiket", p.esik.aciklama));
    k.appendChild(el("h3", null, "Pozisyon büyüklüğü"));
    k.appendChild(el("p", null, p.pozisyon.aciklama));
    if (p.uyarilar.length) {
      k.appendChild(el("h3", null, "Uyarılar"));
      k.appendChild(liste(p.uyarilar, "uyari-liste"));
    }
    return k;
  }

  function planCiz(p) {
    const k = kutu(p.sembol + " — " + (p.kip === "donemsel" ? "dönemsel alım" : "kademeli alım"));
    k.appendChild(alanlar([
      ["Bütçe", p.butce_usdt + " USDT"],
      ["Dilim", p.gecerli_dilim + " / " + p.dilim_sayisi],
      ["Güncel fiyat", p.guncel_fiyat],
      ["Toplam tutar", p.toplam_tutar_usdt + " USDT"],
      ["Toplam komisyon", p.toplam_komisyon_usdt + " USDT"],
      ["Komisyon oranı", oran(p.komisyon_orani_yuzde, 4)],
    ]));
    k.appendChild(tablo(
      ["#", p.kip === "donemsel" ? "Tarih" : "Limit fiyatı", "Tutar", "Miktar", "Komisyon", "Not"],
      p.adimlar.map(function (a) {
        return [
          a.sira,
          p.kip === "donemsel" ? saat(a.tarih_utc) : a.fiyat,
          a.tutar_usdt,
          a.miktar,
          a.komisyon_usdt,
          { metin: a["not"], sinif: a.gecerli ? "kart-etiket" : "sari" },
        ];
      })
    ));
    if (p.gecmis) {
      k.appendChild(el("h3", null, "Aynı plan geçmiş veride ne yapardı"));
      k.appendChild(alanlar([
        ["Dönem", p.gecmis.baslangic_utc.slice(0, 10) + " → " + p.gecmis.bitis_utc.slice(0, 10)],
        ["Alım", String(p.gecmis.alim_sayisi)],
        ["Ortalama maliyet", p.gecmis.ortalama_maliyet],
        ["Yatırılan", p.gecmis.yatirilan_usdt + " USDT"],
        ["Son değer", p.gecmis.son_deger_usdt + " USDT"],
        ["Net", yuzde(p.gecmis.net_yuzde, 2), isaret(p.gecmis.net_yuzde)],
        ["Tek seferde alsaydı", yuzde(p.gecmis.tek_seferde_net_yuzde, 2), isaret(p.gecmis.tek_seferde_net_yuzde)],
        ["En kötü ara değer", yuzde(p.gecmis.en_kotu_ara_deger_yuzde, 2), "kotu"],
      ]));
      k.appendChild(el("p", "kart-etiket",
        "Bu bir vaat değil, elimizdeki veride tek bir planın ölçümüdür."));
    }
    if (p.uyarilar.length) {
      k.appendChild(el("h3", null, "Uyarılar"));
      k.appendChild(liste(p.uyarilar, "uyari-liste"));
    }
    k.appendChild(el("p", "kart-etiket", p["not"]));
    return k;
  }

  // --- sinyal günlüğü -------------------------------------------------

  async function gunlukYukle() {
    let veri;
    try {
      veri = await getir("/api/gunluk");
    } catch (hata) {
      yaz("gunluk-icerik", hataKutusu(hata));
      return;
    }
    const k = kutu("Sinyal günlüğü");
    if (veri.kayitlar.length === 0) {
      k.appendChild(el("p", null,
        "Henüz kayıt yok. Kabul edilmiş bir kural tetiklendiğinde öneri " +
        "buraya yazılır, geçerlilik süresi dolunca da gerçekte ne olduğu " +
        "aynı mum verisinden hesaplanıp yanına eklenir."));
    } else {
      k.appendChild(tablo(
        ["Sinyal", "Sembol", "Periyot", "Giriş", "Hedef", "Stop", "Önerilen net%", "Gerçekleşen net%", "Fark", "Durum"],
        veri.kayitlar.map(function (g) {
          return [
            saat(g.sinyal_mumu_utc), g.sembol, g.periyot, g.giris, g.hedef1, g.stop,
            { metin: yuzde(g.onerilen_net_yuzde), sinif: isaret(g.onerilen_net_yuzde) },
            { metin: g.gerceklesen_net_yuzde === null ? "—" : yuzde(g.gerceklesen_net_yuzde), sinif: isaret(g.gerceklesen_net_yuzde) },
            { metin: g.fark_yuzde === null ? "—" : yuzde(g.fark_yuzde), sinif: isaret(g.fark_yuzde) },
            g.durum_tr,
          ];
        })
      ));
      if (veri.performans && veri.performans.sonuclanan) {
        k.appendChild(el("h3", null, "Önerilen vs gerçekleşen"));
        k.appendChild(alanlar([
          ["Sonuçlanan", String(veri.performans.sonuclanan)],
          ["Önerilen ort.", yuzde(veri.performans.onerilen_ortalama_yuzde)],
          ["Gerçekleşen ort.", yuzde(veri.performans.gerceklesen_ortalama_yuzde)],
          ["Sapma", yuzde(veri.performans.sapma_yuzde), isaret(veri.performans.sapma_yuzde)],
          ["İsabet", oran(100 * veri.performans.isabet_orani)],
        ]));
      }
    }
    yaz("gunluk-icerik", k);
  }

  baslat();
})();
