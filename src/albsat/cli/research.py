"""Örüntü keşfi ve backtest komut satırı aracı (Faz 2).

Kullanım (Mac'te, sanal ortam etkinken)::

    python -m albsat.cli.research

**İnternete çıkmaz.** Veriyi Faz 1'de indirilmiş yerel arşivden okur
(``./veri`` altındaki Parquet dosyaları). Eksik bir sembol/periyot varsa
indirmeye kalkmaz, ne yapılması gerektiğini söyler. Böylece tarama
tekrar tekrar çalıştırılabilir ve Binance'e tek bir istek bile gitmez.

Kapsam varsayılanı Faz 1 bulgusundan geliyor: BTCUSDT ve SOLUSDT, yalnızca
15m ve 1h. 1m ve 5m ölçüm sonucu elendi.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd

from albsat.backtest import run as run_backtest
from albsat.backtest.benchmarks import buy_and_hold, random_entry_backtest
from albsat.core.costs import minimum_meaningful_target, round_trip_for
from albsat.core.fees import Liquidity, flat_table
from albsat.data.commission import research_rates
from albsat.data.klines import closed_only, interval_ms, to_utc
from albsat.data.store import KlineStore
from albsat.features import FeatureSet, build_features
from albsat.research import report
from albsat.research.eventstudy import OutcomeConfig, OutcomeTable, build_outcomes
from albsat.research.scan import (
    PatternResult,
    ScanConfig,
    ScanResult,
    apply_global_correction,
    refine_for_family,
    scan,
)
from albsat.strategy import rules as rulestore

#: Faz 1 kapsam kararı (A seçeneği).
DEFAULT_SYMBOLS = ["BTCUSDT", "SOLUSDT"]
DEFAULT_INTERVALS = ["15m", "1h"]
#: Faz 1 bulgusu: hedef tek mumda değil, birkaç mumluk pencerede tanımlanır.
DEFAULT_WINDOWS = [2, 3, 4]

DEFAULT_MAKER = "0.001"
DEFAULT_TAKER = "0.001"


def resolve_rates(maker: str | None, taker: str | None, root: Path) -> tuple[str, str]:
    """Açıkça verilen oran > ölçülen hesap oranı > genel standart oran."""
    measured = research_rates(root)
    if maker is None and taker is None and measured is not None:
        print(f"Komisyon: hesabınızdan ölçülen oran (maker {measured[0]}, "
              f"taker {measured[1]}; veri/komisyon.json)", flush=True)
        return measured
    return maker or DEFAULT_MAKER, taker or DEFAULT_TAKER


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="albsat-oruntu",
        description="Örüntü keşfi, istatistiksel doğrulama ve backtest (Faz 2)",
    )
    parser.add_argument("--semboller", nargs="+", default=DEFAULT_SYMBOLS)
    parser.add_argument("--periyotlar", nargs="+", default=DEFAULT_INTERVALS)
    parser.add_argument("--pencereler", nargs="+", type=int, default=DEFAULT_WINDOWS,
                        help="Hedefin tanımlandığı mum sayısı (varsayılan 2 3 4)")
    parser.add_argument("--hedef-atr", type=float, default=1.5,
                        help="Hedef = bu katsayı × ATR (varsayılan 1.5)")
    parser.add_argument("--stop-atr", type=float, default=1.0,
                        help="Stop = bu katsayı × ATR (varsayılan 1.0)")
    parser.add_argument("--veri-dizini", default="./veri", type=Path)
    parser.add_argument("--rapor", default="faz2-oruntu-sonuc.txt", type=Path)
    parser.add_argument(
        "--kural-dosyasi",
        type=Path,
        default=None,
        help="Kabul edilen kuralların yazılacağı JSON dosyası "
        "(varsayılan: <veri-dizini>/kurallar.json). Faz 3 öneri motoru "
        "bu dosyayı okur.",
    )
    # Verilmezse <veri-dizini>/komisyon.json'daki ölçülen oran (Faz 4), o da
    # yoksa Binance'in genel standart oranı kullanılır.
    parser.add_argument("--maker", default=None)
    parser.add_argument("--taker", default=None)
    parser.add_argument("--spread", default="0.01", help="Beklenen spread %%")
    parser.add_argument("--kayma", default="0.02", help="Beklenen kayma %%")
    parser.add_argument("--guvenlik", default="0.05", help="Güvenlik payı %%")
    parser.add_argument("--bnb-indirimi", action="store_true",
                        help="Komisyonun BNB ile ödendiğini varsay (%%25 indirim)")
    parser.add_argument("--min-olay", type=int, default=50,
                        help="Bir örüntünün sayılması için en az kaç olay (varsayılan 50)")
    parser.add_argument("--detay", type=int, default=100,
                        help="Her yönde ayrıntılı incelenecek aday sayısı")
    parser.add_argument("--yineleme", type=int, default=1500,
                        help="Bootstrap ve rastgele kıyas yineleme sayısı")
    parser.add_argument("--tohum", type=int, default=20260921,
                        help="Rastgelelik tohumu; aynı tohum aynı sonucu verir")
    parser.add_argument("--hizli", action="store_true",
                        help="Daha az yineleme ve aday ile hızlı bakış")
    parser.add_argument("--maliyetsiz", action="store_true",
                        help="Teşhis turu: komisyon, spread, kayma ve maliyet "
                             "eşiği sıfır sayılır. Yalnızca 'yön bilgisi var mı' "
                             "sorusunu ölçer; işlem önerisi üretmez.")
    parser.add_argument("--gun", type=int, default=None,
                        help="Yalnızca en yeni mumdan geriye bu kadar günü tara. "
                             "Veri bu dönemi kapsamıyorsa tarama başlamaz.")
    parser.add_argument("--onceki-aday", type=int, default=None,
                        help="Önceki turlarda denenmiş aday sayısı; çoklu test "
                             "düzeltmesine eklenir. Verilmezse mevcut kural "
                             "deposundaki birikimli sayı okunur.")
    return parser


#: Veri kapsamı denetiminde hoş görülen boşluk: arşiv ay başından başlar,
#: son mum en fazla birkaç saat gecikebilir.
COVERAGE_TOLERANCE_MS = 2 * 86_400_000
#: Pencerede bulunması gereken en az mum oranı. Borsanın bakım kesintileri
#: birkaç saattir; inmeyen bir aylık arşiv bu oranın çok altına düşürür.
MIN_COVERAGE_SHARE = 0.98


def _prior_from_store(rule_path: Path) -> int:
    """Mevcut kural deposundan önceki turların birikimli aday sayısı.

    Depo yoksa 0. Faz 2 döneminin depolarında bu alan yoktur ve 0 okunur;
    o turların sayısı gerekiyorsa ``--onceki-aday`` ile açıkça verilir.
    Bozuk bir depo sessizce 0 sayılmaz: düzeltmeyi gevşetirdi.
    """
    if not rule_path.exists():
        return 0
    return rulestore.load(rule_path).kosu.birikimli_aday


def _coverage_problems(
    frames: dict[tuple[str, str], pd.DataFrame], days: int
) -> tuple[int, list[str]]:
    """``days`` günlük pencerenin başlangıcı (ms) ve kapsam sorunları.

    Pencere en yeni mumdan geriye sayılır; saat kullanılmaz, böylece aynı veri
    her zaman aynı pencereyi verir.
    """
    newest = max(int(frame["open_time"].max()) for frame in frames.values())
    cutoff = newest - days * 86_400_000
    problems: list[str] = []
    for (symbol, interval), frame in frames.items():
        first = int(frame["open_time"].min())
        last = int(frame["open_time"].max())
        if first > cutoff + COVERAGE_TOLERANCE_MS:
            problems.append(
                f"{symbol} {interval}: veri {to_utc(first).date()} tarihinden başlıyor, "
                f"{days} günlük pencere {to_utc(cutoff).date()} tarihinden başlamalı."
            )
        if last < newest - COVERAGE_TOLERANCE_MS:
            problems.append(
                f"{symbol} {interval}: son mum {to_utc(last).date()}, diğer seriler "
                f"{to_utc(newest).date()} tarihine kadar gidiyor."
            )
        expected = days * 86_400_000 // interval_ms(interval)
        present = int((frame["open_time"] >= cutoff).sum())
        if present < MIN_COVERAGE_SHARE * expected:
            problems.append(
                f"{symbol} {interval}: pencerede {present:,} mum var, olması gereken "
                f"yaklaşık {expected:,}; arada eksik dönem var."
            )
    return cutoff, problems


@dataclass(frozen=True)
class _Section:
    """Tek bir sembol + periyot + pencere için taranmış bölüm.

    Rapor bloğu hemen yazılmıyor: kabul kararı koşunun tamamı görüldükten
    sonra verildiği için bölümler önce toplanıyor.
    """

    frame: pd.DataFrame
    feature_set: FeatureSet
    outcomes: OutcomeTable
    outcome_config: OutcomeConfig
    result: ScanResult
    label: str


def _missing_data_help(symbol: str, interval: str, directory: Path) -> str:
    return (
        f"\n  ! {symbol} {interval} için kayıtlı veri bulunamadı.\n"
        f"    Aranan dosya: {KlineStore(directory).path_for(symbol, interval)}\n"
        "    Bu araç internete çıkmaz; veriyi Faz 1 indirir.\n"
        "    Önce şunu çalıştırın:\n"
        f"      python -m albsat.cli.feasibility --semboller {symbol} "
        f"--periyotlar {interval} --gun 204\n"
    )


def _best_pattern(result: ScanResult) -> PatternResult | None:
    """Backtest edilecek örüntü: kabul edilmiş, uyarısız ve en güvenilir olanı."""
    trustworthy = [p for p in result.buy_patterns if p.trustworthy]
    if trustworthy:
        return trustworthy[0]
    accepted = [p for p in result.buy_patterns if p.accepted]
    return accepted[0] if accepted else None


def _data_span(sections: list[_Section]) -> tuple[str, str]:
    """Taranan verinin ilk ve son mumunun UTC zamanı."""
    starts = [int(item.frame["open_time"].iloc[0]) for item in sections]
    ends = [int(item.frame["open_time"].iloc[-1]) for item in sections]
    if not starts:
        return "", ""
    return to_utc(min(starts)).isoformat(), to_utc(max(ends)).isoformat()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    maker, taker = resolve_rates(args.maker, args.taker, args.veri_dizini)
    spread, slippage, safety = args.spread, args.kayma, args.guvenlik
    if args.bnb_indirimi:
        maker = str(Decimal(maker) * Decimal("0.75"))
        taker = str(Decimal(taker) * Decimal("0.75"))
    if args.maliyetsiz:
        # Teşhis turu: tek bir soruyu ayırmak için maliyetin tamamı kaldırılır.
        # Buradan çıkan hiçbir sayı işlem önerisi değildir.
        maker = taker = "0"
        spread = slippage = safety = "0"

    iterations = 400 if args.hizli else args.yineleme
    detailed = 30 if args.hizli else args.detay

    commissions = flat_table(args.semboller[0], maker, taker)
    # FAZ0-MIMARI.md Risk #4: hedefe limit emirle, stopa piyasa emriyle
    # çıkılır; iki bacağın maliyeti aynı değildir.
    trip_to_target = round_trip_for(
        commissions, entry_liquidity=Liquidity.TAKER, exit_liquidity=Liquidity.MAKER
    )
    trip_to_stop = round_trip_for(
        commissions, entry_liquidity=Liquidity.TAKER, exit_liquidity=Liquidity.TAKER
    )
    threshold = minimum_meaningful_target(
        trip_to_stop,
        spread_pct=spread,
        slippage_pct=slippage,
        safety_pct=safety,
    )
    # Maliyet eşiği maliyetten türer; maliyet yoksa eleme de yoktur.
    eligibility_threshold = None if args.maliyetsiz else threshold

    rule_path = args.kural_dosyasi or rulestore.path_for(args.veri_dizini)
    if args.onceki_aday is not None:
        if args.onceki_aday < 0:
            print("--onceki-aday negatif olamaz.", file=sys.stderr)
            return 2
        prior = args.onceki_aday
    else:
        try:
            prior = _prior_from_store(rule_path)
        except rulestore.RuleStoreError as error:
            print(
                f"\nMevcut kural deposu okunamadı: {error}\n"
                "Önceki turların aday sayısı bilinmeden düzeltme yapılamaz. "
                "Sayıyı --onceki-aday ile açıkça verin.",
                file=sys.stderr,
            )
            return 2

    store = KlineStore(args.veri_dizini)

    cutoff_ms: int | None = None
    if args.gun is not None:
        # BTCUSDT, BTC etkisi ailesinin referansı olduğu için her zaman denetlenir.
        wanted = {
            (symbol, interval): closed_only(store.read(symbol, interval))
            for symbol in dict.fromkeys([*args.semboller, "BTCUSDT"])
            for interval in args.periyotlar
        }
        empty = [f"{s} {i}" for (s, i), frame in wanted.items() if frame.empty]
        if empty:
            print(
                f"\n{args.gun} günlük tarama için veri eksik: {', '.join(empty)}.\n"
                "Tarama başlamadı. Önce veriyi indirin.",
                file=sys.stderr,
            )
            return 4
        cutoff_ms, problems = _coverage_problems(wanted, args.gun)
        if problems:
            print(
                f"\nVeri {args.gun} günlük pencereyi kapsamıyor; tarama başlamadı.\n  "
                + "\n  ".join(problems)
                + "\nÖnce veriyi indirin. Eksik veriyle yapılan bir tur, "
                "önceden belirlenen turun yerine sayılamaz.",
                file=sys.stderr,
            )
            return 4

    chunks: list[str] = [
        report.header(
            args.semboller, args.periyotlar, threshold.explain(),
            diagnostic=args.maliyetsiz,
            prior_tests=prior,
            days=args.gun,
        )
    ]
    print(chunks[0], flush=True)

    scan_config = ScanConfig(
        min_events=args.min_olay,
        detailed_top=detailed,
        bootstrap_iterations=iterations,
        random_repeats=iterations,
        seed=args.tohum,
    )

    # BTC etkisi ailesi için referans seriler; BTCUSDT kendisi için eklenmez.
    def in_window(frame: pd.DataFrame) -> pd.DataFrame:
        frame = closed_only(frame)
        if cutoff_ms is not None:
            frame = frame[frame["open_time"] >= cutoff_ms]
        return frame.reset_index(drop=True)

    context: dict[str, object] = {}
    for interval in args.periyotlar:
        frame = store.read("BTCUSDT", interval)
        if not frame.empty:
            context[interval] = in_window(frame)

    missing = 0
    started = time.monotonic()

    # BİRİNCİ GEÇİŞ — bütün bölümler taranır, ama kabul kararı burada
    # verilmez. Düzeltme, kaç bölüm çalıştırıldığını görebilmek için
    # koşunun tamamı elde olduktan sonra uygulanır.
    sections: list[_Section] = []

    for symbol in args.semboller:
        for interval in args.periyotlar:
            frame = store.read(symbol, interval)
            if frame.empty:
                message = _missing_data_help(symbol, interval, args.veri_dizini)
                print(message, file=sys.stderr, flush=True)
                chunks.append(message)
                missing += 1
                continue

            frame = in_window(frame)
            reference = None if symbol == "BTCUSDT" else context.get(interval)
            print(f"\n{symbol} {interval}: {len(frame):,} kapanmış mum, "
                  "özellikler hesaplanıyor...", flush=True)
            feature_set = build_features(frame, interval=interval, context=reference)
            print(f"  {len(feature_set.names)} özellik hazır "
                  f"(ilk {feature_set.warmup} mum ısınma, sayılmıyor).", flush=True)

            for window in args.pencereler:
                outcome_config = OutcomeConfig(
                    horizon=window,
                    target_atr=args.hedef_atr,
                    stop_atr=args.stop_atr,
                    exit_slippage_pct=float(slippage),
                )
                outcomes = build_outcomes(
                    frame,
                    config=outcome_config,
                    trip_to_target=trip_to_target,
                    trip_to_stop=trip_to_stop,
                    threshold=eligibility_threshold,
                    ready=feature_set.ready.to_numpy(),
                )
                print(f"  {window} mumluk pencere: {outcomes.eligible_count:,} uygun mum, "
                      "örüntüler taranıyor...", flush=True)

                state = {"last": 0.0}

                def progress(
                    direction: str, done: int, total: int, state: dict[str, float] = state
                ) -> None:
                    now = time.monotonic()
                    if done == total or now - state["last"] > 3.0:
                        state["last"] = now
                        print(f"    {direction}: {done}/{total}", flush=True)

                result = scan(
                    frame, feature_set, outcomes,
                    symbol=symbol, interval=interval,
                    config=scan_config, on_progress=progress,
                )
                sections.append(
                    _Section(
                        frame=frame,
                        feature_set=feature_set,
                        outcomes=outcomes,
                        outcome_config=outcome_config,
                        result=result,
                        label=f"{symbol} {interval} · {outcome_config.label_tr}",
                    )
                )

    # Koşu genelinde düzeltme. 12 bölümü ayrı ayrı %10 payla düzeltmek,
    # ortada hiçbir şey yokken bile ortalama 1,2 "buluş" üretir.
    if sections:
        family = sum(item.result.candidates for item in sections) + prior
        note = f" ve önceki turların {prior:,} adayı" if prior else ""
        print(f"\n{len(sections)} bölüm tarandı; çoklu test düzeltmesi "
              f"koşunun tamamı{note} üzerinden yapılıyor ({family:,} deneme)...",
              flush=True)
        # Bölüm içi çözünürlük turu bölümün eşiğine göre ölçer; kabul kararı
        # ailenin çok daha küçük eşiğiyle verildiği için tabana oturan
        # p-değerleri burada ailenin eşiğini çözecek kadar yeniden ölçülür.
        sections = [
            replace(
                section,
                result=refine_for_family(
                    section.result,
                    feature_set=section.feature_set,
                    outcomes=section.outcomes,
                    family_tests=family,
                ),
            )
            for section in sections
        ]
        corrected = apply_global_correction(
            [item.result for item in sections], prior_tests=prior
        )
        sections = [
            replace(section, result=result)
            for section, result in zip(sections, corrected, strict=True)
        ]

    # İKİNCİ GEÇİŞ — rapor blokları ve backtest, kabul kararı kesinleştikten
    # sonra yazılır.
    for section in sections:
        result = section.result
        outcomes = section.outcomes
        frame = section.frame
        feature_set = section.feature_set
        window = section.outcome_config.horizon

        block = report.scan_block(result, feature_set)
        chunks.append(block)
        print("\n" + block, flush=True)

        hold = buy_and_hold(frame, trip_to_stop)
        best = _best_pattern(result)

        if best is None:
            # Kıyas ölçütleri her durumda rapora girer; "bir şey bulunamadı"
            # sonucunu okuyan kişinin taban çizgisine en çok o anda ihtiyacı var.
            print("  kıyas ölçütleri hesaplanıyor...", flush=True)
            typical = max(10, int(outcomes.eligible_count / max(window, 1) / 20))
            randoms = random_entry_backtest(
                frame, outcomes, signal_count=typical,
                repeats=min(200, iterations), seed=args.tohum,
            )
            block = report.benchmark_block(section.label, randoms, hold)
            note = (
                "\nBacktest yapılmadı: kabul edilen örüntü yok.\n"
                "Backtest edilecek bir kural olmadan sermaye eğrisi "
                "çizmek yanıltıcı olur.\n"
                "Aşağıdaki kıyas, aynı piyasada rastgele girilseydi ne "
                "olacağını gösteriyor.\n"
            )
            chunks.append(note + "\n\n" + block)
            print(note + "\n\n" + block, flush=True)
            continue

        print(f"  en güvenilir örüntü backtest ediliyor: {best.label_tr}", flush=True)
        signals = np.ones(len(frame), dtype=bool)
        for name in best.features:
            signals &= feature_set.frame[name].to_numpy(dtype=bool)
        backtest = run_backtest(frame, outcomes, signals)
        randoms = random_entry_backtest(
            frame, outcomes,
            signal_count=backtest.trade_count,
            repeats=min(200, iterations),
            seed=args.tohum,
        )
        block = report.backtest_block(
            f"{section.label} · {best.label_tr}", backtest, randoms, hold
        )
        chunks.append(block)
        print("\n" + block, flush=True)

    chunks.append(report.closing_note())
    print("\n" + chunks[-1], flush=True)

    # Faz 3 köprüsü: kabul kararı kesinleştikten sonra makine okunur kural
    # deposu yazılır. Rapor insan içindir; öneri motoru bu dosyayı okur.
    if sections:
        first, last = _data_span(sections)
        ruleset = rulestore.build_ruleset(
            sections,
            cost=rulestore.CostAssumptions(
                maker_orani=str(maker),
                taker_orani=str(taker),
                spread_yuzde=str(spread),
                kayma_yuzde=str(slippage),
                guvenlik_payi_yuzde=str(safety),
                bnb_indirimi=bool(args.bnb_indirimi),
                minimum_hedef_yuzde=str(threshold.minimum_target_pct),
                aciklama=threshold.explain(),
            ),
            teshis_turu=bool(args.maliyetsiz),
            pencereler=args.pencereler,
            veri_baslangic_utc=first,
            veri_bitis_utc=last,
            onceki_aday=prior,
        )
        rulestore.save(ruleset, rule_path)
        print(
            f"\nKural deposu yazıldı: {rule_path.resolve()} "
            f"({ruleset.kabul_edilen_sayisi} kabul edilen kural, "
            f"{len(ruleset.incelenen_adaylar)} incelenen aday).",
            flush=True,
        )

    elapsed = time.monotonic() - started
    args.rapor.parent.mkdir(parents=True, exist_ok=True)
    args.rapor.write_text("\n\n".join(chunks) + "\n", encoding="utf-8")
    print(f"\nTarama {elapsed:.0f} saniyede bitti.", flush=True)
    print(f"Rapor şu dosyaya yazıldı: {args.rapor.resolve()}", flush=True)

    if missing == len(args.semboller) * len(args.periyotlar):
        print("\nHiçbir sembol için veri bulunamadı.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
