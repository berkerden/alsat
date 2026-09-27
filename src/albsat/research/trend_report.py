"""Günlük trend testinin Türkçe raporu (docs/TREND-ONKAYIT.md §4-§5).

Rapor Berk'in okuyacağı ve Claude'la paylaşacağı tek dosyadır: veri
özeti, karar, her coinin tablosu ve karar dışı dayanıklılık satırları. Karar
bölümü en üstte ve sade dille yazılır; sayılar altta.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence

from albsat.data.klines import QualityReport
from albsat.research.trend import (
    DAYS_PER_YEAR,
    EVALUATION_START,
    FDR_ALPHA,
    RULES,
    Performance,
    SymbolResult,
)

LINE = "=" * 78
THIN = "-" * 78

VERDICT_EXPLANATION = {
    "Geçti": "al-ve-tut'tan hem risk başına daha çok kazandırdı hem daha küçük düşüş "
    "yaşadı, ve bu üstünlük şansla açıklanamıyor.",
    "Belirsiz": "al-ve-tut'tan iyi göründü, ama üstünlüğü alternatif dönemlerde "
    "yeterince sağlam değil; şanstan ayırt edilemedi.",
    "Geçmedi": "al-ve-tut'tan iyi değil: risk başına getirisi daha düşük ya da en büyük "
    "düşüşü daha derin.",
}


def _date(open_time_ms: int) -> str:
    return dt.datetime.fromtimestamp(open_time_ms / 1000, tz=dt.UTC).date().isoformat()


def _pct(value: float, digits: int = 1) -> str:
    return f"%{value:+,.{digits}f}"


def _performance_row(label: str, perf: Performance, *, benchmark: bool = False) -> str:
    entries = "—" if benchmark else f"{perf.entries_per_year:.1f}"
    return (
        f"  {label:<22}{_pct(perf.total_return_pct):>11}{_pct(perf.annual_return_pct):>9}"
        f"{_pct(-perf.max_drawdown_pct):>10}{perf.sharpe:>8.2f}"
        f"{f'%{perf.exposure_pct:.0f}':>10}{entries:>7}"
    )


def cost_line(cost: float, detail: str) -> str:
    return (
        f"Maliyet: her alışta ve her satışta %{cost * 100:.3f} "
        f"(gidiş-dönüş %{cost * 200:.2f}: {detail})"
    )


def summary_lines(results: Sequence[SymbolResult]) -> list[str]:
    """En üstteki karar bölümü."""
    verdicts = [
        (result, item, item.verdict(result.benchmark))
        for result in results
        for item in result.rules
    ]
    passed = [(r, i) for r, i, v in verdicts if v == "Geçti"]
    unclear = [(r, i) for r, i, v in verdicts if v == "Belirsiz"]
    total = len(verdicts)

    lines = ["KARAR", ""]
    if passed:
        lines.append(f"{len(passed)} sınama geçti ({total} sınamadan):")
        for result, item in passed:
            state = "elde tut" if item.latest_position else "nakitte bekle"
            lines.append(
                f"  • {result.symbol} — {item.rule.name_tr} "
                f"(son kapanıştaki durumu: {state}; bilgi, öneri değil)"
            )
        lines += [
            "",
            "Ön kayda göre geçen kural hiçbir parayla hemen kullanılmaz. Nasıl",
            "kullanılacağı (ya da kullanılmayacağı) ayrı bir karardır; önce Claude'la",
            "konuşulur.",
        ]
    elif unclear:
        lines += [
            "Geçen kural yok. Sonuç BELİRSİZ:",
            f"{len(unclear)} sınamada kural al-ve-tut'tan iyi göründü ama şanstan",
            "ayırt edilemedi. Ön kayda göre bu aile kapanır; uygulama bunu öneri",
            "olarak göstermez.",
        ]
    else:
        lines += [
            "Geçen kural yok. Hiçbir kural al-ve-tut'tan hem risk başına daha çok",
            "kazandırmadı hem daha küçük düşüş yaşamadı. Ön kayda göre bu aile kapanır.",
        ]
    lines += ["", "Her sınamanın kararı:"]
    for result, item, verdict in verdicts:
        lines.append(f"  {result.symbol:<9}{item.rule.name_tr:<24}{verdict}")
    lines += ["", "Kararların anlamı:"]
    for verdict, text in VERDICT_EXPLANATION.items():
        lines.append(f"  {verdict}: {text}")
    return lines


def symbol_block(result: SymbolResult, block: float) -> list[str]:
    years = result.benchmark.days / DAYS_PER_YEAR
    lines = [
        THIN,
        f"{result.symbol} — değerlendirme {_date(result.start_open_time)} → "
        f"{_date(result.end_open_time)} ({result.benchmark.days:,} gün, {years:.1f} yıl)",
        f"İlk günlük mum {_date(result.first_open_time)}; ilk {EVALUATION_START} gün "
        "kuralların ısınması için kullanıldı.",
        THIN,
        "",
        f"  {'':<22}{'Toplam':>11}{'Yıllık':>9}{'En büyük':>10}{'Sharpe':>8}"
        f"{'Piyasada':>10}{'Alış':>7}",
        f"  {'':<22}{'getiri':>11}{'getiri':>9}{'düşüş':>10}{'':>8}{'kalma':>10}{'/yıl':>7}",
        _performance_row("Al-ve-tut", result.benchmark, benchmark=True),
    ]
    for item in result.rules:
        lines.append(_performance_row(item.rule.name_tr, item.performance))

    lines += [
        "",
        f"  Sağlamlık sınaması: geçmiş, ortalama {block:g} günlük parçalar halinde rastgele",
        "  yeniden örneklenerek alternatif dönemler kuruldu (bazısı bir düşüşü iki kez,",
        "  bazısı hiç içermez). Fark: kuralın Sharpe'ı eksi al-ve-tut'unki. p: gerçekte",
        "  üstünlük yokken bu kadar büyük bir farkın görünme olasılığı. q: on sınama",
        "  birlikte düzeltildikten sonraki değer.",
        "",
        f"  {'':<22}{'Sharpe':>8}{'%90 aralık':>17}{'p':>9}{'q':>8}",
        f"  {'':<22}{'farkı':>8}",
    ]
    for item in result.rules:
        test = item.test
        band = f"{test.low:+.2f} … {test.high:+.2f}"
        lines.append(
            f"  {item.rule.name_tr:<22}{test.difference:>+8.2f}{band:>17}"
            f"{test.p_value:>9.4f}{item.q_value:>8.3f}"
        )
    lines.append("")
    for item in result.rules:
        lines.append(f"  {item.rule.name_tr}: {item.verdict(result.benchmark)}")

    if result.drawdowns:
        lines += [
            "",
            "  Al-ve-tut'un en derin düşüşleri ve kuralların aynı aralıktaki sonucu:",
        ]
        for rank, drawdown in enumerate(result.drawdowns):
            lines.append(
                f"  {rank + 1}. {_date(drawdown.peak_time)} → {_date(drawdown.trough_time)}: "
                f"al-ve-tut "
                f"{_pct(drawdown.depth_pct)}"
            )
            for item in result.rules:
                lines.append(
                    f"       {item.rule.name_tr:<22}{_pct(item.during_drawdowns[rank]):>10}"
                )

    lines += [
        "",
        "  Bir gün geç uygulama (karar dışı): her karar ertesi gün uygulansaydı.",
        f"  {'':<22}{'Toplam':>11}{'Yıllık':>9}{'En büyük':>10}{'Sharpe':>8}",
    ]
    for item in result.rules:
        perf = item.delayed
        lines.append(
            f"  {item.rule.name_tr:<22}{_pct(perf.total_return_pct):>11}"
            f"{_pct(perf.annual_return_pct):>9}{_pct(-perf.max_drawdown_pct):>10}"
            f"{perf.sharpe:>8.2f}"
        )
    return lines


def render(
    results: Sequence[SymbolResult],
    quality: Sequence[QualityReport],
    *,
    ran_at: dt.datetime,
    commit: str,
    cost_detail: str,
    resamples: int,
    block: float,
    seed: int,
) -> str:
    cost = results[0].cost if results else 0.0
    tests = sum(len(result.rules) for result in results)
    lines = [
        LINE,
        "GÜNLÜK TREND TESTİ — SONUÇ",
        LINE,
        "Ön kayıt: docs/TREND-ONKAYIT.md (sınama çalışmadan önce yazıldı)",
        f"Çalıştırma: {ran_at:%Y-%m-%d %H:%M} UTC · kod {commit}",
        cost_line(cost, cost_detail),
        f"Sağlamlık sınaması: eşli durağan blok bootstrap, {resamples:,} alternatif dönem, "
        f"ortalama parça {block:g} gün, tohum {seed}",
        f"Aile: {tests} sınama ({len(RULES)} kural × {len(results)} coin), "
        f"Benjamini–Hochberg, yanlış buluş payı %{FDR_ALPHA * 100:.0f}",
        "Önceki turların 12,021 giriş sinyali adayı bu aileye sayılmadı; gerekçesi ön",
        "kayıt §2'de. Sayılsaydı hiçbir sınama geçemezdi (eşik 0.10/12,031 ≈ 0.0000083,",
        f"bu sınamanın en küçük p'si 1/{resamples + 1:,}).",
        "",
    ]
    lines += summary_lines(results)
    lines += ["", "VERİ", ""]
    lines += ["  " + report.summary_tr() for report in quality]
    lines.append("")
    for result in results:
        lines += symbol_block(result, block)
        lines.append("")
    lines += [
        LINE,
        "Kurallar (ön kayıttaki haliyle):",
    ]
    for rule in RULES:
        lines.append(f"  {rule.name_tr}: {rule.rule_tr}")
    lines += [
        "",
        "Nakitte beklenen günlerde getiri sıfır sayıldı (USDT faiz getirmiyor).",
        "Karar günlük mumun kapanışında verilir, ertesi günün getirisini alır.",
        "Bu sonuç geçmiş veride bir ölçümdür, yatırım tavsiyesi değildir. Ön kayda",
        "göre bu sınama tekrar edilmez; başka parametre, coin ya da periyotla yeni",
        "tur açılmaz.",
        LINE,
    ]
    return "\n".join(lines) + "\n"
