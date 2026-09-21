"""Tarama sonucunu Türkçe rapora çevirir (SPEC.md §4.2, §4.8).

Rapor iki yerde okunur: terminalde tarama biter bitmez ve `docs/` altına
yazılan dosyada. İkisi de aynı metni üretir.

Yazım ilkesi şartnameden geliyor (§11): kâr vaat etme, bulamadığını da söyle.
Bu yüzden rapor "kabul edilen örüntü yok" sonucunu da tam olarak aynı
ciddiyetle yazar ve bunun neden iyi bir sonuç olduğunu açıklar.
"""

from __future__ import annotations

import datetime as dt

from albsat.backtest.benchmarks import BuyAndHold, random_entry_summary
from albsat.backtest.engine import BacktestResult
from albsat.features import FAMILIES_TR, FeatureSet
from albsat.research.eventstudy import EventSummary, OutcomeConfig
from albsat.research.scan import PatternResult, ScanResult

LINE = "=" * 78
THIN = "-" * 78


def _pct(value: float, digits: int = 4) -> str:
    return f"%{value:+.{digits}f}"


def baseline_block(summary: EventSummary, config: OutcomeConfig) -> str:
    """Örüntüsüz taban çizgisi: her mumda girseydik ne olurdu?"""
    if summary.events == 0:
        return "Taban çizgisi hesaplanamadı: uygun mum yok."
    return "\n".join(
        [
            f"Taban çizgisi ({config.label_tr}) — her uygun mumda girilseydi:",
            f"  olay sayısı           {summary.events:,}",
            f"  işlem başına net      {_pct(summary.net_mean_pct)}",
            f"  hedefe ulaşma         %{summary.hit_rate * 100:.1f}",
            f"  stopa çarpma          %{summary.stop_share * 100:.1f}",
            f"  süre dolması          %{summary.timeout_share * 100:.1f}",
            f"  ortalama tutma        {summary.bars_held_mean:.2f} mum",
            "",
            "Bu sayı örüntü aramanın sıfır noktasıdır. Bir örüntü ancak bunu",
            "belirgin biçimde aşarsa bir şey eklemiş olur.",
        ]
    )


def pattern_block(pattern: PatternResult, rank: int) -> str:
    """Tek bir örüntünün kartı."""
    lines = [f"{rank}. {pattern.label_tr}"]
    summary = pattern.full
    lines.append(
        f"   olay {summary.events:,} (üst üste binmeyen {summary.independent_events:,}), "
        f"işlem başına net {_pct(summary.net_mean_pct)}"
    )
    lines.append(
        f"   isabet %{summary.hit_rate * 100:.1f} · "
        f"medyan MFE {_pct(summary.mfe_median_pct, 2)} · "
        f"medyan MAE {_pct(summary.mae_median_pct, 2)} · "
        f"ortalama {summary.bars_held_mean:.1f} mum"
    )
    lines.append(f"   {pattern.bootstrap.summary_tr}, düzeltilmiş q={pattern.q_value:.4f}")
    lines.append(
        f"   bu p-değeri yalnızca ayrılmış dönemden hesaplandı "
        f"({pattern.inference_events:,} üst üste binmeyen olay); keşif eğitim "
        f"döneminde yapıldı"
    )

    split_text = " · ".join(
        f"{item.name} {item.events:,} olay {_pct(item.net_mean_pct)}"
        for item in pattern.splits
        if item.events
    )
    if split_text:
        lines.append(f"   dönemler: {split_text}")
    lines.append(
        f"   walk-forward dışı ortalama {_pct(pattern.walk_forward_mean_pct)}, "
        f"katmanların %{pattern.walk_forward_positive_share * 100:.0f}'inde doğru yönde"
    )
    if pattern.stability:
        lines.append(
            f"   çeyreklik kararlılık: {len(pattern.stability)} çeyrekten "
            f"%{pattern.stable_share * 100:.0f}'i beklenen yönde"
        )
    if pattern.direction == "al":
        lines.append(f"   {pattern.random.summary_tr}")

    if pattern.warnings_tr:
        for note in pattern.warnings_tr:
            lines.append(f"   ! {note}")
    else:
        lines.append("   Uyarı yok.")
    return "\n".join(lines)


#: Düzeltmeden geçemese de rapora not düşülecek ham p-değeri sınırı.
NEAR_MISS_P = 0.05


def _pattern_list(
    patterns: tuple[PatternResult, ...], *, top: int, empty_text: str
) -> list[str]:
    """Kabul edilenleri, yoksa düzeltme öncesi dikkat çekenleri yazar.

    Dikkat çekenler iki listede de gösteriliyor. Yalnızca "alınacaklar"a
    bakmak, aşağı yönlü bir bulguyu sessizce gizlerdi; "bir şey bulunamadı"
    sonucunun ne kadar net olduğunu okuyan kişi iki yönü de görmeli.
    """
    accepted = [item for item in patterns if item.accepted]
    if accepted:
        out: list[str] = []
        for rank, pattern in enumerate(accepted[:top], start=1):
            out.append(pattern_block(pattern, rank))
            out.append("")
        return out

    out = [empty_text]
    near = [item for item in patterns if item.bootstrap.p_value < NEAR_MISS_P][:top]
    if near:
        out.append("")
        out.append("  Düzeltme öncesi dikkat çekenler (kanıt sayılmaz, not düşülüyor):")
        for rank, pattern in enumerate(near, start=1):
            out.append("  " + pattern_block(pattern, rank).replace("\n", "\n  "))
    return out


def _correction_scope(result: ScanResult) -> str:
    """Düzeltmenin kaç deneme üzerinden yapıldığını söyleyen satır."""
    if result.family_tests and result.family_tests != result.candidates:
        return (
            "Çoklu test düzeltmesi bu bölüm için değil, koşunun tamamı için\n"
            f"yapıldı: {result.family_tests:,} deneme. Kaç bölüm çalıştırıldığını\n"
            "hesaba katmayan bir düzeltme, hiçbir şey yokken bile bölüm başına\n"
            "şansa buluş üretir."
        )
    return f"Çoklu test düzeltmesi {result.candidates:,} deneme üzerinden yapıldı."


def scan_block(result: ScanResult, feature_set: FeatureSet, *, top: int = 5) -> str:
    """Bir sembol + periyot + pencere için bölüm."""
    config = result.outcomes.config
    lines = [
        THIN,
        f"{result.symbol} {result.interval} — {config.label_tr}",
        THIN,
        "",
        baseline_block(result.baseline, config),
        "",
        f"Denenen aday örüntü: {result.candidates:,} "
        f"(ayrıntılı incelenen {result.evaluated:,}).",
        _correction_scope(result),
    ]
    if result.outcomes.rejected_by_threshold:
        lines.append(
            f"Maliyet eşiğini geçemediği için elenen mum: "
            f"{result.outcomes.rejected_by_threshold:,} (SPEC.md §4.3)."
        )
    lines.append("")

    lines.append(
        f"En küçük ham p-değeri: {result.best_raw_p_value:.5f} "
        f"(tek bir örüntünün kabul edilmesi için gereken: "
        f"{result.acceptance_p_threshold:.5f})."
    )
    distance = result.best_raw_p_value / result.acceptance_p_threshold
    if result.acceptance_p_threshold > 0 and distance > 1:
        lines.append(
            f"En iyi aday bu eşiğin {distance:,.0f} katı uzağında — "
            "yakın bir kaçırma değil."
        )
    lines.append("")

    lines.append("ALINACAK ÖRÜNTÜLER")
    lines.extend(
        _pattern_list(
            result.buy_patterns,
            top=top,
            empty_text=(
                "  Çoklu test düzeltmesinden geçen örüntü yok. Bu, bir başarısızlık\n"
                "  değil bir ölçüm sonucudur: denenen adaylar arasında, şansla\n"
                "  açıklanamayacak kadar iyi olan çıkmadı."
            ),
        )
    )

    lines.append("")
    lines.append("KAÇINILACAK ÖRÜNTÜLER (alma / elindekini sat)")
    lines.extend(
        _pattern_list(
            result.avoid_patterns,
            top=top,
            empty_text="  Düzeltmeden geçen bir kaçınma örüntüsü de yok.",
        )
    )

    families = sorted({feature_set.family_of(name) for name in feature_set.names})
    lines.append("")
    lines.append(
        "Kullanılan özellik aileleri: "
        + ", ".join(FAMILIES_TR.get(family, family) for family in families)
        + f" ({len(feature_set.names)} özellik)."
    )
    return "\n".join(lines)


def backtest_block(
    label: str,
    result: BacktestResult,
    random_results: list[BacktestResult],
    hold: BuyAndHold,
) -> str:
    """Faz 2 kabul kriteri: rapor rastgele girişle kıyas içerir."""
    lines = [
        f"BACKTEST — {label}",
        f"  işlem sayısı          {result.trade_count:,} "
        f"(sinyal {result.signals_seen:,}, pozisyon doluyken atlanan "
        f"{result.signals_skipped_busy:,})",
        f"  toplam getiri         {_pct(result.total_return_pct, 2)}",
        f"  işlem başına net      {_pct(result.expectancy_pct)}",
        f"  isabet oranı          %{result.hit_rate * 100:.1f}",
        f"  en büyük düşüş        %{result.max_drawdown_pct:.2f}",
        f"  kâr faktörü           {result.profit_factor:.2f}",
        f"  yıllık Sharpe         {result.annual_sharpe:.2f}",
    ]
    distribution = random_entry_summary(random_results)
    if distribution:
        better = sum(
            1 for item in random_results if item.total_return_pct >= result.total_return_pct
        )
        share = better / len(random_results)
        lines.append("")
        lines.append(
            f"  Rastgele giriş ({len(random_results)} deneme, aynı işlem sayısı, "
            f"aynı hedef/stop):"
        )
        lines.append(
            f"    ortalama {_pct(distribution['ortalama'], 2)}, "
            f"%5–%95 aralığı {_pct(distribution['p05'], 2)} … "
            f"{_pct(distribution['p95'], 2)}"
        )
        lines.append(
            f"    rastgele denemelerin %{share * 100:.1f}'i bu sonuca eşit veya daha iyi"
        )
    lines.append("")
    lines.append(f"  {hold.summary_tr}")
    return "\n".join(lines)


def benchmark_block(
    label: str, random_results: list[BacktestResult], hold: BuyAndHold
) -> str:
    """Kıyas ölçütleri, kabul edilen örüntü olmasa da rapora girer.

    Faz 2'nin kabul kriteri raporun rastgele girişle kıyas içermesidir. Kıyas
    yalnızca bir bulgu çıktığında yazılsaydı, "hiçbir şey bulunamadı" sonucu
    okuyucuya taban çizgisini göstermeden bırakırdı — oysa en çok o durumda
    "rastgele girseydim ne olurdu" sorusunun cevabı gerekir.
    """
    lines = [f"KIYAS ÖLÇÜTLERİ — {label}"]
    distribution = random_entry_summary(random_results)
    if distribution:
        positive = sum(1 for item in random_results if item.total_return_pct > 0)
        lines.append(
            f"  Rastgele giriş ({len(random_results)} deneme, "
            f"{random_results[0].trade_count} işlem, aynı hedef/stop):"
        )
        lines.append(
            f"    ortalama {_pct(distribution['ortalama'], 2)}, "
            f"medyan {_pct(distribution['medyan'], 2)}, "
            f"%5–%95 aralığı {_pct(distribution['p05'], 2)} … "
            f"{_pct(distribution['p95'], 2)}"
        )
        lines.append(
            f"    denemelerin %{positive / len(random_results) * 100:.1f}'i artıda bitti"
        )
    else:
        lines.append("  Rastgele giriş kıyası için yeterli uygun mum yok.")
    lines.append(f"  {hold.summary_tr}")
    return "\n".join(lines)


def header(
    symbols: list[str],
    intervals: list[str],
    threshold_text: str,
    *,
    diagnostic: bool = False,
) -> str:
    today = dt.date.today().isoformat()
    title = (
        "FAZ 2 — TEŞHİS TURU: YÖN BİLGİSİ VAR MI?"
        if diagnostic
        else "FAZ 2 — ÖRÜNTÜ KEŞFİ VE BACKTEST"
    )
    lines = [
        LINE,
        title,
        LINE,
        f"Tarih: {today}",
        f"Kapsam: {', '.join(symbols)} — {', '.join(intervals)}",
    ]
    if diagnostic:
        lines += [
            "Maliyet: SIFIR sayıldı (komisyon, spread, kayma ve eşik dahil).",
            "",
            "BU BİR TEŞHİS TURUDUR, İŞLEM ÖNERİSİ DEĞİL.",
            "",
            "Normal tarama şunu sorar: maliyet ödendikten sonra kâr kalıyor mu?",
            "Cevabı hayırdı. Bu tur tek bir alt soruyu ayırıyor: kâr kalmamasının",
            "sebebi maliyetin ağırlığı mı, yoksa ortada hiç yön bilgisi olmaması mı?",
            "",
            "Burada kabul edilen bir örüntü, gerçek maliyetle kârlı olduğu anlamına",
            "GELMEZ. Yalnızca 'ölçülebilir bir yön bilgisi var' demektir. Hiçbir şey",
            "çıkmazsa, maliyeti düşürmek de bir şey değiştirmez — çünkü düşürülecek",
            "maliyetin altında bir kenar yok.",
        ]
    else:
        lines += [
            f"Maliyet eşiği: {threshold_text}",
            "",
            "Faz 1'in bulgusu gereği hedefler tek mum değil, birkaç mumluk",
            "pencerelerde tanımlandı: kârlılığı hangi grafiğe bakıldığı değil,",
            "pozisyonda ne kadar kalındığı belirliyor.",
        ]
    return "\n".join(lines)


def closing_note() -> str:
    return "\n".join(
        [
            LINE,
            "NASIL OKUNMALI",
            LINE,
            "",
            "* Giriş her zaman sinyalin oluştuğu mumun DEĞİL, bir sonraki mumun",
            "  açılışıdır. Sinyal mumunun kapanışından işlem yapılamaz.",
            "* Aynı mumda hem hedefe hem stopa değilirse stop kabul edildi. Mum",
            "  verisi hangisinin önce geldiğini söylemez; temkinli varsayım bu.",
            "* Komisyon oranı varsayımdır (%0,1 / %0,1). Hesaba özel gerçek oran",
            "  Faz 4'te imzalı GET /api/v3/account/commission ile ölçülecek ve",
            "  bu rapordaki her sayı o zaman yenilenmeli.",
            "* 'Kabul edilen örüntü' demek, o örüntünün çoklu test düzeltmesinden",
            "  geçtiği anlamına gelir; kâr garantisi anlamına gelmez.",
            "* Geçmiş veriye dayanan her çıkarım, piyasanın davranışı değişirse",
            "  geçerliliğini yitirir.",
        ]
    )
