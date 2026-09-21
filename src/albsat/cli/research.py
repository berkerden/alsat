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
from decimal import Decimal
from pathlib import Path

import numpy as np

from albsat.backtest import run as run_backtest
from albsat.backtest.benchmarks import buy_and_hold, random_entry_backtest
from albsat.core.costs import minimum_meaningful_target, round_trip_for
from albsat.core.fees import Liquidity, flat_table
from albsat.data.klines import closed_only
from albsat.data.store import KlineStore
from albsat.features import build_features
from albsat.research import report
from albsat.research.eventstudy import OutcomeConfig, build_outcomes
from albsat.research.scan import PatternResult, ScanConfig, ScanResult, scan

#: Faz 1 kapsam kararı (A seçeneği).
DEFAULT_SYMBOLS = ["BTCUSDT", "SOLUSDT"]
DEFAULT_INTERVALS = ["15m", "1h"]
#: Faz 1 bulgusu: hedef tek mumda değil, birkaç mumluk pencerede tanımlanır.
DEFAULT_WINDOWS = [2, 3, 4]

DEFAULT_MAKER = "0.001"
DEFAULT_TAKER = "0.001"


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
    parser.add_argument("--maker", default=DEFAULT_MAKER)
    parser.add_argument("--taker", default=DEFAULT_TAKER)
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
    return parser


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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    maker, taker = args.maker, args.taker
    if args.bnb_indirimi:
        maker = str(Decimal(maker) * Decimal("0.75"))
        taker = str(Decimal(taker) * Decimal("0.75"))

    iterations = 400 if args.hizli else args.yineleme
    detailed = 30 if args.hizli else args.detay

    store = KlineStore(args.veri_dizini)
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
        spread_pct=args.spread,
        slippage_pct=args.kayma,
        safety_pct=args.guvenlik,
    )

    chunks: list[str] = [report.header(args.semboller, args.periyotlar, threshold.explain())]
    print(chunks[0], flush=True)

    scan_config = ScanConfig(
        min_events=args.min_olay,
        detailed_top=detailed,
        bootstrap_iterations=iterations,
        random_repeats=iterations,
        seed=args.tohum,
    )

    # BTC etkisi ailesi için referans seriler; BTCUSDT kendisi için eklenmez.
    context: dict[str, object] = {}
    for interval in args.periyotlar:
        frame = store.read("BTCUSDT", interval)
        if not frame.empty:
            context[interval] = closed_only(frame).reset_index(drop=True)

    missing = 0
    started = time.monotonic()

    for symbol in args.semboller:
        for interval in args.periyotlar:
            frame = store.read(symbol, interval)
            if frame.empty:
                message = _missing_data_help(symbol, interval, args.veri_dizini)
                print(message, file=sys.stderr, flush=True)
                chunks.append(message)
                missing += 1
                continue

            frame = closed_only(frame).reset_index(drop=True)
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
                    exit_slippage_pct=float(args.kayma),
                )
                outcomes = build_outcomes(
                    frame,
                    config=outcome_config,
                    trip_to_target=trip_to_target,
                    trip_to_stop=trip_to_stop,
                    threshold=threshold,
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
                block = report.scan_block(result, feature_set)
                chunks.append(block)
                print("\n" + block, flush=True)

                label = f"{symbol} {interval} · {outcome_config.label_tr}"
                hold = buy_and_hold(frame, trip_to_stop)
                best = _best_pattern(result)

                if best is None:
                    # Kıyas ölçütleri her durumda rapora girer; "bir şey
                    # bulunamadı" sonucunu okuyan kişinin taban çizgisine en
                    # çok o anda ihtiyacı var.
                    print("  kıyas ölçütleri hesaplanıyor...", flush=True)
                    typical = max(10, int(outcomes.eligible_count / max(window, 1) / 20))
                    randoms = random_entry_backtest(
                        frame, outcomes, signal_count=typical,
                        repeats=min(200, iterations), seed=args.tohum,
                    )
                    block = report.benchmark_block(label, randoms, hold)
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

                print(f"  en güvenilir örüntü backtest ediliyor: {best.label_tr}",
                      flush=True)
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
                    f"{label} · {best.label_tr}", backtest, randoms, hold
                )
                chunks.append(block)
                print("\n" + block, flush=True)

    chunks.append(report.closing_note())
    print("\n" + chunks[-1], flush=True)

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
