"""Günlük trend testi komut satırı aracı (docs/TREND-ONKAYIT.md).

Kullanım (Mac'te ``bash kurulum.sh trend`` bunu çağırır)::

    python -m albsat.cli.trend

Ne yapar:

1. BTCUSDT ve SOLUSDT'nin bütün günlük mumlarını Binance'in herkese açık
   ``GET /api/v3/klines`` ucundan indirir. Günlük veri küçüktür: coin başına
   3-4 istek. Anahtar gerekmez, hesaba hiçbir istek gitmez.
2. Veri kalitesini denetler. Eksik, bozuk ya da güncel olmayan veride sınama
   hiç başlamaz; o çalıştırma sayılmaz.
3. Beş kuralı ve al-ve-tut'u ölçer, sağlamlık sınamasını (blok bootstrap)
   yapar, raporu yazar.

**Bir kez çalışır.** Rapor dosyası varsa hiçbir şey indirmeden durur: aynı
soruyu yeniden sormak sonucu şansa açar (ön kayıt §7).
"""

from __future__ import annotations

import argparse
import datetime as dt
import math
import subprocess
import sys
import urllib.error
from pathlib import Path

import pandas as pd

from albsat.cli.feasibility import resolve_rates
from albsat.core.costs import minimum_meaningful_target, round_trip_for
from albsat.core.fees import Liquidity, flat_table
from albsat.core.tls import enable_system_trust
from albsat.data.backfill import MAX_LIMIT, KlineSource, fetch_range
from albsat.data.klines import QualityReport, check_quality, closed_only, interval_ms
from albsat.data.store import KlineStore
from albsat.exchange.http import HttpError, PublicHttp
from albsat.research import trend
from albsat.research.trend_report import render

INTERVAL = "1d"
DAY_MS = interval_ms(INTERVAL)

#: İstek bu tarihten başlar. Binance Temmuz 2017'de açıldı; borsa bu tarihten
#: sonraki ilk mumdan itibaren döndürür, yani her coin kendi ilk gününden gelir.
FIRST_REQUEST = dt.datetime(2017, 1, 1, tzinfo=dt.UTC)

#: Coin başına izin verilen azami istek. Bugün 4 yeter (1000 gün/istek).
#: Hesap bunu aşarsa hiçbir istek gönderilmeden durulur.
MAX_REQUESTS_PER_SYMBOL = 8

DEFAULT_REPORT = Path("trend-sonuc.txt")


class DataProblem(RuntimeError):
    """Sınamayı başlatmaya engel veri sorunu; sınama sayılmaz."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="albsat-trend",
        description="Günlük trend testi: beş klasik kural ve al-ve-tut",
    )
    parser.add_argument("--semboller", nargs="+", default=["BTCUSDT", "SOLUSDT"])
    parser.add_argument("--veri-dizini", default="./veri", type=Path)
    parser.add_argument("--rapor", default=DEFAULT_REPORT, type=Path)
    parser.add_argument("--indirme-yok", action="store_true",
                        help="İnternete çıkma; diskteki günlük veriyi kullan")
    # Aşağıdakiler ön kayıtla sabittir; yalnızca testler için açıktır.
    parser.add_argument("--orneklem", type=int, default=trend.RESAMPLES,
                        help=argparse.SUPPRESS)
    parser.add_argument("--tohum", type=int, default=trend.SEED, help=argparse.SUPPRESS)
    parser.add_argument("--maker", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--taker", default=None, help=argparse.SUPPRESS)
    return parser


def planned_requests(now_ms: int) -> int:
    """İlk istekten bugüne kadar olan günlük mumlar için gereken istek sayısı."""
    days = max(0, (now_ms - int(FIRST_REQUEST.timestamp() * 1000)) // DAY_MS + 1)
    return max(1, math.ceil(days / MAX_LIMIT))


def download(source: KlineSource, symbol: str, now_ms: int) -> pd.DataFrame:
    """Bir coinin bütün günlük mumları; yalnızca kapanmış olanlar."""
    pages = planned_requests(now_ms)
    if pages > MAX_REQUESTS_PER_SYMBOL:
        raise DataProblem(
            f"{symbol}: {pages} istek gerekecekti, sınır {MAX_REQUESTS_PER_SYMBOL}. "
            "Beklenmedik büyüklükte bir indirme; hiçbir istek gönderilmedi."
        )
    frame = fetch_range(
        source,
        symbol=symbol,
        interval=INTERVAL,
        start_time=int(FIRST_REQUEST.timestamp() * 1000),
        end_time=now_ms,
        now_ms=now_ms,
        max_pages=MAX_REQUESTS_PER_SYMBOL,
    )
    return closed_only(frame)


def check_data(frame: pd.DataFrame, symbol: str, now_ms: int) -> QualityReport:
    """Sınamaya girecek verinin eksiksiz, sağlam ve güncel olduğunu doğrular."""
    report = check_quality(frame, symbol=symbol, interval=INTERVAL)
    if report.rows == 0 or report.last_open_time is None:
        raise DataProblem(f"{symbol}: günlük veri yok.")
    if not report.usable:
        raise DataProblem(f"{symbol}: veri sorunlu, sınama başlamadı. {report.summary_tr()}")
    # Son kapanmış günlük mum dünün mumudur; ondan eskiyse veri eksik inmiştir.
    if now_ms - report.last_open_time > 3 * DAY_MS:
        raise DataProblem(
            f"{symbol}: veri güncel değil (son mum {report.summary_tr()}). "
            "İndirme yarıda kalmış olabilir."
        )
    return report


def cost_per_side(root: Path, maker: str | None, taker: str | None) -> tuple[float, str]:
    """Tek yön maliyeti (oran) ve açıklaması.

    Faz 2 ve ikinci turla aynı tanım: iki bacak da taker (elle ya da piyasa
    emriyle alıp satmak), spread %0,01, kayma %0,02, güvenlik payı %0,05.
    Gidiş-dönüş toplamının yarısı her alışta ve her satışta ödenir.
    """
    maker_rate, taker_rate = resolve_rates(maker, taker, root)
    trip = round_trip_for(
        flat_table("BTCUSDT", maker_rate, taker_rate),
        entry_liquidity=Liquidity.TAKER,
        exit_liquidity=Liquidity.TAKER,
    )
    threshold = minimum_meaningful_target(
        trip, spread_pct="0.01", slippage_pct="0.02", safety_pct="0.05"
    )
    detail = (
        f"komisyon %{trip.entry.rate_pct:.3f} + %{trip.exit.rate_pct:.3f}, spread "
        f"%{threshold.spread_pct:.2f}, kayma %{threshold.slippage_pct:.2f}, güvenlik payı "
        f"%{threshold.safety_pct:.2f}"
    )
    return float(threshold.minimum_target_pct) / 200.0, detail


def git_commit() -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return "bilinmiyor"
    return completed.stdout.strip() or "bilinmiyor"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report_path: Path = args.rapor

    if report_path.exists() and report_path.stat().st_size > 0:
        print(
            f"Günlük trend testi daha önce tamamlanmış: {report_path.resolve()}\n\n"
            "Bu sınama bir kez çalıştırılır. Yeniden çalıştırmak aynı soruyu bir kez\n"
            "daha sormak olur ve sonucu şansa açar. Raporu açmak için:\n"
            f'   open "{report_path}"',
            file=sys.stderr,
        )
        return 1

    store = KlineStore(args.veri_dizini)
    frames: dict[str, pd.DataFrame] = {}
    quality: list[QualityReport] = []
    try:
        if args.indirme_yok:
            now_ms = int(dt.datetime.now(dt.UTC).timestamp() * 1000)
            for symbol in args.semboller:
                frame = closed_only(store.read(symbol, INTERVAL))
                quality.append(check_data(frame, symbol, now_ms))
                frames[symbol] = frame
        else:
            enable_system_trust()
            http = PublicHttp()
            try:
                now_ms = http.server_time()
            except (urllib.error.URLError, HttpError, OSError):
                now_ms = int(dt.datetime.now(dt.UTC).timestamp() * 1000)
            for symbol in args.semboller:
                print(f"  {symbol}: günlük mumlar indiriliyor "
                      f"(en fazla {planned_requests(now_ms)} istek)...", flush=True)
                frame = download(http, symbol, now_ms)
                report = check_data(frame, symbol, now_ms)
                store.write(frame, symbol=symbol, interval=INTERVAL)
                print("    " + report.summary_tr(), flush=True)
                quality.append(report)
                frames[symbol] = frame
    except DataProblem as problem:
        print(f"\n{problem}\nSınama başlamadı; bu çalıştırma sayılmaz.", file=sys.stderr)
        return 2
    except (urllib.error.URLError, HttpError, OSError) as error:
        print(
            f"\nBinance'ten günlük veri alınamadı: {error}\n"
            "İnternet bağlantınızı kontrol edip aynı komutu yeniden çalıştırın.\n"
            "Sınama başlamadı; bu çalıştırma sayılmaz.",
            file=sys.stderr,
        )
        return 2

    cost, cost_detail = cost_per_side(args.veri_dizini, args.maker, args.taker)
    print(f"\n  Maliyet: her alışta ve her satışta %{cost * 100:.3f}", flush=True)

    results: list[trend.SymbolResult] = []
    for symbol in args.semboller:
        print(f"\n  {symbol}: beş kural ölçülüyor, {args.orneklem:,} alternatif dönemle "
              "sınanıyor", flush=True)

        def progress(done: int, total: int, symbol: str = symbol) -> None:
            print(f"    {symbol}: {done:,}/{total:,} dönem", flush=True)

        try:
            results.append(
                trend.evaluate_symbol(frames[symbol], symbol=symbol, cost=cost,
                                      resamples=args.orneklem, seed=args.tohum,
                                      on_progress=progress)
            )
        except ValueError as problem:
            print(f"\n{problem}\nSınama başlamadı; bu çalıştırma sayılmaz.", file=sys.stderr)
            return 2

    results = trend.apply_correction(results)
    text = render(
        results, quality,
        ran_at=dt.datetime.now(dt.UTC),
        commit=git_commit(),
        cost_detail=cost_detail,
        resamples=args.orneklem,
        block=trend.BLOCK_DAYS,
        seed=args.tohum,
    )
    # Önce geçici dosyaya: yarıda kesilen yazım "tamamlandı" sayılmasın.
    temporary = report_path.with_name(report_path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(report_path)
    print("\n" + text, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
