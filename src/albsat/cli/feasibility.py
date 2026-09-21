"""Fizibilite taraması komut satırı aracı.

Kullanım (Mac'te, sanal ortam etkinken)::

    python -m albsat.cli.feasibility --semboller BTCUSDT SOLUSDT \
        --periyotlar 1m 5m 15m 1h --gun 180

Ne yapar:

1. ``exchangeInfo``'dan sembol filtrelerini çeker.
2. ``data.binance.vision`` arşivlerinden geçmiş mumları indirir, sağlamasını
   doğrular, REST ile boşlukları kapatır.
3. Veri kalite raporunu basar.
4. Her periyot için ATR% / maliyet oranını hesaplayıp fizibilite tablosunu basar.

Komisyon oranları: ``--maker`` ve ``--taker`` verilmezse Binance'in genel
listelenen oranları **tahmini olarak** kullanılır ve çıktıda bu açıkça
belirtilir. Gerçek oranlar imzalı ``GET /api/v3/account/commission``
çağrısını gerektirir; o Faz 4'te API anahtarıyla birlikte gelir.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import ssl
import urllib.error
from pathlib import Path

from albsat.core.costs import minimum_meaningful_target, round_trip_for
from albsat.core.fees import Liquidity, flat_table
from albsat.core.filters import parse_exchange_info
from albsat.core.tls import CERTIFICATE_HELP, enable_system_trust
from albsat.data import vision
from albsat.data.backfill import extend_to_now, merge_frames, repair
from albsat.data.klines import check_quality
from albsat.data.store import KlineStore
from albsat.exchange.http import HttpError, PublicHttp
from albsat.research.feasibility import render_table, scan_interval

#: Binance'in genel listelenen spot oranları — yalnızca varsayılan tahmin.
DEFAULT_MAKER = "0.001"
DEFAULT_TAKER = "0.001"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="albsat-fizibilite",
        description="Coin + periyot için 'matematiksel olarak anlamlı mı' taraması",
    )
    parser.add_argument("--semboller", nargs="+", default=["BTCUSDT", "SOLUSDT"])
    parser.add_argument("--periyotlar", nargs="+", default=["1m", "5m", "15m", "1h"])
    parser.add_argument("--gun", type=int, default=180, help="Kaç günlük geçmiş")
    parser.add_argument("--veri-dizini", default="./veri", type=Path)
    parser.add_argument("--maker", default=DEFAULT_MAKER)
    parser.add_argument("--taker", default=DEFAULT_TAKER)
    parser.add_argument("--spread", default="0.01", help="Beklenen spread %%")
    parser.add_argument("--kayma", default="0.02", help="Beklenen kayma %%")
    parser.add_argument("--guvenlik", default="0.05", help="Güvenlik payı %%")
    parser.add_argument(
        "--bnb-indirimi", action="store_true",
        help="Komisyonun BNB ile ödendiğini varsay (%%25 indirim)",
    )
    parser.add_argument("--dogrulama-yok", action="store_true",
                        help="Arşiv SHA256 doğrulamasını atla (önerilmez)")
    parser.add_argument("--es-zamanli", dest="esZamanli", type=int, default=4,
                        help="Aynı anda indirilecek arşiv sayısı (varsayılan 4)")
    return parser


def _is_certificate_error(error: BaseException) -> bool:
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, ssl.SSLCertVerificationError):
            return True
        error = getattr(error, "reason", None) or error.__cause__
    return False


def _network_failure(error: Exception) -> int:
    """Ağ hatasını yığın izi yerine anlaşılır bir mesajla bildirir."""
    if _is_certificate_error(error):
        print(f"\nGüvenli bağlantı kurulamadı.\nHata: {error}\n\n"
              f"{CERTIFICATE_HELP}", file=sys.stderr)
        return 3
    print(
        "\nBinance'e bağlanılamadı.\n"
        f"Hata: {error}\n\n"
        "Kontrol edilecekler:\n"
        "  1. İnternet bağlantınız çalışıyor mu?\n"
        "  2. Kurumsal bir ağ, VPN veya güvenlik duvarı arkasında mısınız?\n"
        "     Bazı ağlar api.binance.com adresini engelliyor.\n"
        "  3. Binance'in bulunduğunuz ülkede erişilebilir olduğundan emin olun.\n\n"
        "Bu adım yalnızca herkese açık piyasa verisini okur; API anahtarı "
        "gerektirmez.",
        file=sys.stderr,
    )
    return 2


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    maker, taker = args.maker, args.taker
    if args.bnb_indirimi:
        from decimal import Decimal

        maker = str(Decimal(maker) * Decimal("0.75"))
        taker = str(Decimal(taker) * Decimal("0.75"))

    # Python'ı işletim sisteminin güven deposuna bağla; araya giren kurumsal
    # ağ/antivirüs sertifikaları macOS Anahtar Zinciri'nde güvenilirse
    # doğrulama kapatılmadan çalışır.
    enable_system_trust()

    http = PublicHttp()
    store = KlineStore(args.veri_dizini)

    end = dt.date.today()
    start = end - dt.timedelta(days=args.gun)

    # Sunucu saati: kapanmamış mumu doğru işaretlemek için borsadan alınır,
    # yerel saatten değil (SPEC.md §4.1).
    try:
        now_ms = http.server_time()
    except (urllib.error.URLError, HttpError, OSError):
        now_ms = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)

    print("Sembol filtreleri çekiliyor...")
    try:
        rules = parse_exchange_info(http.exchange_info(args.semboller))
    except (urllib.error.URLError, HttpError, OSError) as error:
        return _network_failure(error)
    for symbol in args.semboller:
        rule = rules.get(symbol)
        if rule is None:
            print(f"  ! {symbol} bulunamadı", file=sys.stderr)
            continue
        print(
            f"  {symbol}: durum {rule.status}, OTOCO {'var' if rule.otoco_allowed else 'YOK'}, "
            f"trailing {'var' if rule.allow_trailing_stop else 'YOK'}, "
            f"pegged {'var' if rule.peg_instructions_allowed else 'YOK'}, "
            f"minNotional {rule.notional.min_notional if rule.notional else '?'}"
        )

    rows = []
    print(f"\nVeri indiriliyor ({start} → {end})...")
    print("İlk çalıştırmada bu adım uzun sürer; indirilen dosyalar "
          f"{args.veri_dizini / 'arsiv'} altında saklanır ve sonraki "
          "çalıştırmalarda tekrar indirilmez.")
    for symbol in args.semboller:
        for interval in args.periyotlar:
            refs = vision.plan_archives(symbol, interval, start, end)
            cache_dir = args.veri_dizini / "arsiv"
            cached = sum(1 for ref in refs if vision.is_cached(ref, cache_dir))
            print(f"\n  {symbol} {interval}: {len(refs)} arşiv dosyası "
                  f"({cached} tanesi önbellekte)", flush=True)

            not_published = 0

            def progress(index, ref, frame, error, total=len(refs)):
                nonlocal not_published
                if error is None:
                    rows = 0 if frame is None else len(frame)
                    print(f"    [{index}/{total}] {ref.filename} — {rows:,} mum",
                          flush=True)
                elif isinstance(error, HttpError) and error.status == 404:
                    # Son günlerin arşivi henüz yayımlanmamış olabilir;
                    # bu normaldir, eksik mumlar REST ile tamamlanır.
                    not_published += 1
                    print(f"    [{index}/{total}] {ref.filename} — henüz yayımlanmamış",
                          flush=True)
                else:
                    print(f"    [{index}/{total}] {ref.filename} — HATA: {error}",
                          file=sys.stderr, flush=True)

            frames = vision.fetch_many(
                refs, http,
                cache_dir=cache_dir,
                verify=not args.dogrulama_yok,
                workers=args.esZamanli,
                on_result=progress,
            )

            if not_published:
                print(f"    {not_published} arşiv henüz yayımlanmamış, "
                      "o aralık REST ile tamamlanacak", flush=True)

            frame = merge_frames(frames)
            if not frame.empty:
                print(f"    {len(frame):,} mum birleştirildi, boşluklar aranıyor...",
                      flush=True)
                try:
                    reported = {"total": None}

                    def gap_progress(done, total, state=reported):
                        if state["total"] is None:
                            state["total"] = total
                            if total:
                                print(f"    {total} boşluk bulundu, REST ile "
                                      "dolduruluyor...", flush=True)
                            else:
                                print("    boşluk yok", flush=True)
                        # Uzun listelerde her adımı basmak gürültü olur.
                        if total and (done % 25 == 0 or done == total):
                            print(f"      {done}/{total}", flush=True)

                    frame, filled = repair(frame, http, symbol=symbol,
                                           interval=interval, now_ms=now_ms,
                                           on_progress=gap_progress)
                    print("    son mumlar borsadan alınıyor...", flush=True)
                    frame, extended = extend_to_now(
                        frame, http, symbol=symbol, interval=interval, now_ms=now_ms
                    )
                    if filled or extended:
                        print(f"    {filled + extended:,} mum REST ile tamamlandı",
                              flush=True)
                except (urllib.error.URLError, HttpError, OSError) as error:
                    print(f"  ! {symbol} {interval}: boşluklar doldurulamadı ({error})",
                          file=sys.stderr)
                store.upsert(frame, symbol=symbol, interval=interval)

            report = check_quality(frame, symbol=symbol, interval=interval)
            print("  " + report.summary_tr(), flush=True)

            trip = round_trip_for(
                flat_table(symbol, maker, taker),
                # Giriş LIMIT_MAKER, çıkış stopta TAKER: en kötü durum bacağı.
                entry_liquidity=Liquidity.MAKER,
                exit_liquidity=Liquidity.TAKER,
            )
            threshold = minimum_meaningful_target(
                trip, spread_pct=args.spread, slippage_pct=args.kayma,
                safety_pct=args.guvenlik,
            )
            rows.append(
                scan_interval(frame, symbol=symbol, interval=interval,
                              threshold=threshold)
            )

    print("\n" + "=" * 78)
    print("FİZİBİLİTE TARAMASI")
    print("=" * 78)
    if rows:
        print(rows[0].threshold.explain())
        print()
    print(render_table(rows))
    print()
    for row in rows:
        print(f"  {row.symbol} {row.interval}: {row.explanation_tr}")

    print(
        "\nNot: Komisyon oranları burada varsayılan olarak alınmıştır. Gerçek "
        "oranlar hesaba özeldir ve imzalı GET /api/v3/account/commission ile "
        "çekilir; bu Faz 4'te API anahtarıyla gelir."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
