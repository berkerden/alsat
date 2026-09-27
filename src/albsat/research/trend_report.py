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
    CONTROL_MIN_MEASURED,
    DAYS_PER_YEAR,
    EVALUATION_START,
    FDR_ALPHA,
    RULES,
    ControlCoin,
    ControlSummary,
    Performance,
    RuleResult,
    SymbolResult,
    control_summaries,
    final_verdict,
)

LINE = "=" * 78
THIN = "-" * 78

VERDICT_EXPLANATION = {
    "Geçti": "al-ve-tut'tan hem risk başına daha çok kazandırdı hem daha küçük düşüş "
    "yaşadı, ve bu üstünlük şansla açıklanamıyor.",
    "Belirsiz": "al-ve-tut'tan iyi göründü, ama üstünlüğü alternatif dönemlerde "
    "yeterince sağlam değil ya da kontrol coinlerinin en az yarısında tutmadı; "
    "şanstan ayırt edilemedi.",
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


def verdict_text(item: RuleResult, benchmark: Performance, control: ControlSummary) -> str:
    """Son karar; kontrol yüzünden düştüyse nedeniyle."""
    verdict = final_verdict(item, benchmark, control)
    if verdict != item.verdict(benchmark):
        return f"{verdict} (kontrolde tutmadı: {control.better}/{control.measured} coin)"
    return verdict


def summary_lines(
    results: Sequence[SymbolResult], controls: dict[str, ControlSummary]
) -> list[str]:
    """En üstteki karar bölümü."""
    verdicts = [
        (result, item, final_verdict(item, result.benchmark, controls[item.rule.key]))
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
    for result, item, _ in verdicts:
        text = verdict_text(item, result.benchmark, controls[item.rule.key])
        lines.append(f"  {result.symbol:<9}{item.rule.name_tr:<24}{text}")
    lines += ["", "Kararların anlamı:"]
    for verdict, text in VERDICT_EXPLANATION.items():
        lines.append(f"  {verdict}: {text}")
    return lines


def symbol_block(
    result: SymbolResult, block: float, controls: dict[str, ControlSummary]
) -> list[str]:
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
        text = verdict_text(item, result.benchmark, controls[item.rule.key])
        lines.append(f"  {item.rule.name_tr}: {text}")

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


#: Kontrol tablosunda kuralların kısa adları.
SHORT_NAMES = {
    "sma200": "200g",
    "kesisim_50_200": "50/200",
    "kirilim_55_20": "55/20",
    "kirilim_20_10": "20/10",
    "momentum_365": "12ay",
}


def control_block(
    coins: Sequence[ControlCoin], controls: dict[str, ControlSummary]
) -> list[str]:
    """Kontrol coinleri: her coinde her kural iyi mi, ve kural başına özet."""
    lines = [
        THIN,
        "KONTROL COİNLERİ — aynı beş kural, hiç değiştirilmeden",
        THIN,
        "",
        "  Liste: CoinMarketCap'in 12 Ağustos 2018 sırasıyla ilk on coin (BTC ve",
        "  USDT hariç), sonuçlara bakılmadan seçildi. Her coin kendi ilk günlük",
        f"  mumundan {EVALUATION_START} gün sonra başlar; maliyet aynı. p-değeri yok: kontrol",
        "  yalnızca BTC ya da SOL'daki bir \"Geçti\"yi doğrular ya da düşürür.",
        "  +: kural o coinde al-ve-tut'tan hem Sharpe'ta hem en büyük düşüşte iyi.",
        "",
        f"  {'':<10}{'Dönem':<25}{'Al-ve-tut':>15}"
        + "".join(f"{SHORT_NAMES.get(rule.key, rule.key):>7}" for rule in RULES),
        f"  {'':<10}{'':<25}{'Sharpe':>7}{'düşüş':>8}",
    ]
    for coin in coins:
        if coin.benchmark is None:
            lines.append(f"  {coin.symbol:<10}ölçülemedi: {coin.problem}")
            continue
        period = f"{_date(coin.start_open_time)} → {_date(coin.end_open_time)}"
        marks = "".join(
            f"{'+' if coin.better(index) else '-':>7}" for index in range(len(RULES))
        )
        lines.append(
            f"  {coin.symbol:<10}{period:<25}{coin.benchmark.sharpe:>7.2f}"
            f"{_pct(-coin.benchmark.max_drawdown_pct, 0):>8}{marks}"
        )

    lines += [
        "",
        f"  {'':<22}{'İyi olduğu':>12}{'Sharpe farkı':>15}{'Kontrol':>10}",
        f"  {'':<22}{'coin':>12}{'(ortanca)':>15}",
    ]
    for rule in RULES:
        summary = controls[rule.key]
        median = (
            "—" if summary.measured == 0 else f"{summary.median_sharpe_difference:+.2f}"
        )
        state = "tuttu" if summary.holds else "tutmadı"
        lines.append(
            f"  {rule.name_tr:<22}{f'{summary.better}/{summary.measured}':>12}"
            f"{median:>15}{state:>10}"
        )
    lines += [
        "",
        f"  Kontrol, en az {CONTROL_MIN_MEASURED} coin ölçülebildiyse ve kural ölçülen coinlerin",
        "  en az yarısında iyiyse tutar. Coinler BTC ile birlikte hareket ettiği için",
        "  bağımsız kanıt sayılmaz.",
    ]
    return lines


def render(
    results: Sequence[SymbolResult],
    quality: Sequence[QualityReport],
    *,
    controls: Sequence[ControlCoin],
    ran_at: dt.datetime,
    commit: str,
    cost_detail: str,
    resamples: int,
    block: float,
    seed: int,
) -> str:
    cost = results[0].cost if results else 0.0
    tests = sum(len(result.rules) for result in results)
    summaries = control_summaries(controls)
    measured = sum(coin.measured for coin in controls)
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
        f"Kontrol: aynı kurallar {len(controls)} coinde daha ({measured} ölçülebildi); "
        "yalnızca \"Geçti\"yi doğrular",
        "Önceki turların 12,021 giriş sinyali adayı bu aileye sayılmadı; gerekçesi ön",
        "kayıt §2'de. Sayılsaydı hiçbir sınama geçemezdi (eşik 0.10/12,031 ≈ 0.0000083,",
        f"bu sınamanın en küçük p'si 1/{resamples + 1:,}).",
        "",
    ]
    lines += summary_lines(results, summaries)
    lines += ["", "VERİ", ""]
    lines += ["  " + report.summary_tr() for report in quality]
    lines.append("")
    for result in results:
        lines += symbol_block(result, block, summaries)
        lines.append("")
    lines += control_block(controls, summaries)
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
