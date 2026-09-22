/* Mum grafiği — bağımlılıksız canvas çizimi.
 *
 * Neden hazır bir grafik kütüphanesi değil: bu uygulama tek komutla kurulan
 * bir Python paketi. Bir JavaScript paketi eklemek, kullanıcıdan Node ve npm
 * kurmasını istemek veya sayfayı bir CDN'e bağımlı hale getirmek demekti.
 * İkisi de Faz 3'ün ihtiyacı olan şeyin (mumlar, giriş/hedef/stop çizgileri)
 * çok ötesinde bir bedel. Burası yaklaşık 120 satır ve internetsiz çalışıyor.
 */
(function (global) {
  "use strict";

  const RENK = {
    yukselen: "#3fb950",
    dusen: "#f85149",
    izgara: "#222a35",
    yazi: "#9aa7b4",
    giris: "#4c8dff",
    hedef: "#3fb950",
    stop: "#f85149",
  };

  const VARSAYILAN_YUKSEKLIK = 260;

  function ciz(canvas, mumlar, cizgiler) {
    const ctx = canvas.getContext("2d");
    const oran = global.devicePixelRatio || 1;
    const genislik = canvas.clientWidth || 600;
    // Yükseklik canvas'ın "height" özniteliğinden OKUNMAZ: aşağıda
    // canvas.height'a piksel oranıyla çarpılmış değer yazılıyor ve o
    // öznitelik de onunla değişiyor. Geri okumak, Retina ekranda (oran 2)
    // grafiği her çizimde ikiye katlıyordu: 260 → 520 → 1040 → ...
    // "data-yukseklik" hiç yazılmadığı için her seferinde aynı kalır.
    const yukseklik = Number(canvas.dataset.yukseklik) || VARSAYILAN_YUKSEKLIK;

    canvas.width = genislik * oran;
    canvas.height = yukseklik * oran;
    canvas.style.height = yukseklik + "px";
    ctx.setTransform(oran, 0, 0, oran, 0, 0);
    ctx.clearRect(0, 0, genislik, yukseklik);

    if (!mumlar || mumlar.length === 0) {
      ctx.fillStyle = RENK.yazi;
      ctx.font = "13px -apple-system, sans-serif";
      ctx.fillText("Gösterilecek mum yok.", 12, yukseklik / 2);
      return;
    }

    const solBosluk = 8;
    const sagBosluk = 62;
    const ustBosluk = 8;
    const altBosluk = 18;
    const alan = {
      x: solBosluk,
      y: ustBosluk,
      g: genislik - solBosluk - sagBosluk,
      y2: yukseklik - altBosluk,
    };
    alan.yuk = alan.y2 - alan.y;

    let enDusuk = Infinity;
    let enYuksek = -Infinity;
    for (const mum of mumlar) {
      if (mum.d < enDusuk) enDusuk = mum.d;
      if (mum.y > enYuksek) enYuksek = mum.y;
    }
    (cizgiler || []).forEach(function (cizgi) {
      if (!isFinite(cizgi.deger)) return;
      if (cizgi.deger < enDusuk) enDusuk = cizgi.deger;
      if (cizgi.deger > enYuksek) enYuksek = cizgi.deger;
    });

    const pay = (enYuksek - enDusuk) * 0.06 || 1;
    enDusuk -= pay;
    enYuksek += pay;

    function yEkseni(fiyat) {
      return alan.y + (enYuksek - fiyat) / (enYuksek - enDusuk) * alan.yuk;
    }

    // Yatay ızgara ve fiyat etiketleri
    ctx.font = "11px -apple-system, sans-serif";
    ctx.textBaseline = "middle";
    for (let i = 0; i <= 4; i++) {
      const fiyat = enDusuk + (enYuksek - enDusuk) * (i / 4);
      const y = yEkseni(fiyat);
      ctx.strokeStyle = RENK.izgara;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(alan.x, y + 0.5);
      ctx.lineTo(alan.x + alan.g, y + 0.5);
      ctx.stroke();
      ctx.fillStyle = RENK.yazi;
      ctx.fillText(bicimle(fiyat), alan.x + alan.g + 6, y);
    }

    const adim = alan.g / mumlar.length;
    const govde = Math.max(1, Math.min(9, adim * 0.7));

    mumlar.forEach(function (mum, sira) {
      const merkez = alan.x + adim * (sira + 0.5);
      const renk = mum.k >= mum.a ? RENK.yukselen : RENK.dusen;
      ctx.strokeStyle = renk;
      ctx.fillStyle = renk;

      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(Math.round(merkez) + 0.5, yEkseni(mum.y));
      ctx.lineTo(Math.round(merkez) + 0.5, yEkseni(mum.d));
      ctx.stroke();

      const ust = yEkseni(Math.max(mum.a, mum.k));
      const alt = yEkseni(Math.min(mum.a, mum.k));
      ctx.fillRect(merkez - govde / 2, ust, govde, Math.max(1, alt - ust));
    });

    // Giriş / hedef / stop çizgileri
    (cizgiler || []).forEach(function (cizgi) {
      if (!isFinite(cizgi.deger)) return;
      const y = yEkseni(cizgi.deger);
      ctx.strokeStyle = RENK[cizgi.tur] || RENK.giris;
      ctx.lineWidth = 1;
      ctx.setLineDash([5, 4]);
      ctx.beginPath();
      ctx.moveTo(alan.x, y + 0.5);
      ctx.lineTo(alan.x + alan.g, y + 0.5);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = RENK[cizgi.tur] || RENK.giris;
      ctx.fillText(cizgi.etiket, alan.x + 4, y - 7);
    });
  }

  function bicimle(sayi) {
    const mutlak = Math.abs(sayi);
    if (mutlak >= 1000) return sayi.toFixed(0);
    if (mutlak >= 1) return sayi.toFixed(2);
    return sayi.toPrecision(4);
  }

  global.Grafik = { ciz: ciz };
})(window);
