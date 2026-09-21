"""Rapor ve komut satırı testleri.

Komut satırı aracının **internete çıkmaması** burada da doğrulanıyor: veri
yoksa indirmeye kalkmaz, ne yapılacağını söyler ve çıkar.
"""

from __future__ import annotations

import numpy as np
import pytest
from veri_uret import planted_signal, random_walk

from albsat.cli import research
from albsat.core.costs import minimum_meaningful_target, round_trip_for
from albsat.core.fees import Liquidity, flat_table
from albsat.data.klines import interval_ms
from albsat.data.store import KlineStore
from albsat.features import build_features
from albsat.research import report
from albsat.research.eventstudy import OutcomeConfig, build_outcomes
from albsat.research.scan import ScanConfig, scan


@pytest.fixture
def veri_dizini(tmp_path):
    store = KlineStore(tmp_path / "veri")
    for symbol, seed, start in (("BTCUSDT", 1, 43_000.0), ("SOLUSDT", 3, 180.0)):
        for interval in ("15m", "1h"):
            frame = random_walk(
                1_200, seed=seed + len(interval), start=start,
                step_ms=interval_ms(interval),
            )
            store.write(frame, symbol=symbol, interval=interval)
    return tmp_path / "veri"


def test_cli_rapor_uretiyor(veri_dizini, tmp_path, capsys):
    rapor = tmp_path / "sonuc.txt"
    code = research.main([
        "--veri-dizini", str(veri_dizini),
        "--rapor", str(rapor),
        "--hizli",
        "--pencereler", "3",
    ])
    assert code == 0
    text = rapor.read_text(encoding="utf-8")
    assert "FAZ 2 — ÖRÜNTÜ KEŞFİ VE BACKTEST" in text
    assert "Taban çizgisi" in text
    assert "NASIL OKUNMALI" in text
    # Faz 2 kabul kriteri: rapor rastgele giriş kıyasını içerir.
    assert "rastgele" in text.lower()
    assert "BTCUSDT" in text and "SOLUSDT" in text


def test_cli_eksik_veride_indirmeye_kalkmiyor(tmp_path, capsys):
    rapor = tmp_path / "sonuc.txt"
    code = research.main([
        "--veri-dizini", str(tmp_path / "yok"),
        "--rapor", str(rapor),
        "--hizli",
    ])
    assert code == 2
    captured = capsys.readouterr()
    assert "kayıtlı veri bulunamadı" in captured.err
    assert "albsat.cli.feasibility" in captured.err


def test_cli_kapsam_varsayilanlari_faz1_kararina_uyuyor():
    """1m ve 5m ölçüm sonucu elendi; varsayılan kapsam onları içermemeli."""
    args = research.build_parser().parse_args([])
    assert args.semboller == ["BTCUSDT", "SOLUSDT"]
    assert args.periyotlar == ["15m", "1h"]
    assert "1m" not in args.periyotlar and "5m" not in args.periyotlar
    # Faz 1 bulgusu: hedef tek mumda değil, birkaç mumluk pencerede.
    assert args.pencereler == [2, 3, 4]
    assert min(args.pencereler) >= 2


def _scan_result(frame):
    table = flat_table("BTCUSDT", "0.001", "0.001")
    to_target = round_trip_for(
        table, entry_liquidity=Liquidity.TAKER, exit_liquidity=Liquidity.MAKER
    )
    to_stop = round_trip_for(
        table, entry_liquidity=Liquidity.TAKER, exit_liquidity=Liquidity.TAKER
    )
    threshold = minimum_meaningful_target(
        to_stop, spread_pct="0.01", slippage_pct="0.02", safety_pct="0.05"
    )
    features = build_features(frame, interval="15m")
    outcomes = build_outcomes(
        frame,
        config=OutcomeConfig(horizon=3, target_atr=1.0, stop_atr=1.0),
        trip_to_target=to_target,
        trip_to_stop=to_stop,
        threshold=threshold,
        ready=features.ready.to_numpy(),
    )
    result = scan(
        frame, features, outcomes, symbol="BTCUSDT", interval="15m",
        config=ScanConfig(detailed_top=40, bootstrap_iterations=400, random_repeats=400),
    )
    return features, outcomes, result


def test_rapor_bulunamadi_durumunu_yaziyor():
    """SPEC.md §11: bulamadığını da söyle."""
    frame = random_walk(6_000, seed=41)
    features, _, result = _scan_result(frame)
    text = report.scan_block(result, features)
    assert "Çoklu test düzeltmesinden geçen örüntü yok" in text
    assert "bir ölçüm sonucudur" in text


def test_rapor_bulunan_oruntuyu_yaziyor():
    frame, _ = planted_signal(9_000, drift_pct=1.0)
    features, _, result = _scan_result(frame)
    text = report.scan_block(result, features)
    assert "ALINACAK ÖRÜNTÜLER" in text
    assert "Hacim" in text
    assert "güven aralığı" in text
    assert "walk-forward" in text
    assert "Çoklu test düzeltmesi" in text


def test_rapor_kar_vaadi_icermiyor():
    """SPEC.md §11: kâr garantisi veya 'kesin' ifadesi kullanma."""
    frame, _ = planted_signal(9_000, drift_pct=1.0)
    features, _, result = _scan_result(frame)
    text = "\n".join([
        report.header(["BTCUSDT"], ["15m"], "eşik"),
        report.scan_block(result, features),
        report.closing_note(),
    ]).lower()
    for yasak in ("kâr garantisi ver", "kesin kazanç", "garanti kazanç", "risksiz"):
        assert yasak not in text
    assert "kâr garantisi anlamına gelmez" in text


def test_taban_cizgisi_bos_veriyle_cokmuyor():
    frame = random_walk(60, seed=42)
    features, outcomes, _ = _scan_result(frame)
    from albsat.research.eventstudy import summarize

    empty = summarize(outcomes, np.zeros(len(frame), dtype=bool))
    text = report.baseline_block(empty, outcomes.config)
    assert "hesaplanamadı" in text


def test_cli_maliyetsiz_teshis_turu(veri_dizini, tmp_path):
    """Teşhis turu maliyeti sıfırlar ve ne olmadığını açıkça söyler."""
    rapor = tmp_path / "teshis.txt"
    code = research.main([
        "--veri-dizini", str(veri_dizini),
        "--rapor", str(rapor),
        "--hizli",
        "--pencereler", "3",
        "--maliyetsiz",
    ])
    assert code == 0
    text = rapor.read_text(encoding="utf-8")
    assert "TEŞHİS TURU" in text
    assert "İŞLEM ÖNERİSİ DEĞİL" in text
    # Maliyet eşiği maliyetten türer; maliyet yoksa eleme de olmamalı.
    assert "Maliyet eşiğini geçemediği için elenen mum" not in text
    assert "Maliyet: SIFIR sayıldı" in text


def test_cli_duzeltme_kosunun_tamamina_uygulaniyor(veri_dizini, tmp_path):
    """Birden çok bölüm çalıştıysa düzeltme bölüm içinde kalmamalı.

    12 bölümü ayrı ayrı %10 payla düzeltmek, ortada hiçbir şey yokken bile
    ortalama 1,2 "buluş" üretir.
    """
    rapor = tmp_path / "sonuc.txt"
    code = research.main([
        "--veri-dizini", str(veri_dizini),
        "--rapor", str(rapor),
        "--hizli",
        "--pencereler", "2", "3",
    ])
    assert code == 0
    text = rapor.read_text(encoding="utf-8")
    assert "koşunun tamamı için" in text
