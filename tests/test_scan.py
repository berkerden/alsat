"""Örüntü taraması testleri.

İki yönlü doğrulama:

* **Gömülü sinyal bulunmalı.** İçine bilerek bir kenar konmuş seride tarama
  o kuralı bulmalı. Bulamıyorsa arama tarafında bir şey bozuktur.
* **Olmayan sinyal bulunmamalı.** Rastgele yürüyüşte, maliyet de varken,
  çoklu test düzeltmesinden geçen örüntü çıkmamalı. Çıkıyorsa motor aşırı
  uyum üretiyordur ve raporuna güvenilemez.

İkincisi birincisinden daha önemlidir: yanlış bir "buldum", hiçbir şey
bulmamaktan pahalıdır.
"""

from __future__ import annotations

import numpy as np
import pytest
from veri_uret import planted_signal, poison_future, random_walk

from albsat.core.costs import minimum_meaningful_target, round_trip_for
from albsat.core.fees import Liquidity, flat_table
from albsat.features import build_features
from albsat.research.eventstudy import OutcomeConfig, build_outcomes
from albsat.research.scan import ScanConfig, scan

FAST = ScanConfig(detailed_top=60, bootstrap_iterations=600, random_repeats=600)


def prepare(frame, *, horizon=3, target_atr=1.0, stop_atr=1.0, threshold=True):
    table = flat_table("BTCUSDT", "0.001", "0.001")
    to_target = round_trip_for(
        table, entry_liquidity=Liquidity.TAKER, exit_liquidity=Liquidity.MAKER
    )
    to_stop = round_trip_for(
        table, entry_liquidity=Liquidity.TAKER, exit_liquidity=Liquidity.TAKER
    )
    limit = (
        minimum_meaningful_target(
            to_stop, spread_pct="0.01", slippage_pct="0.02", safety_pct="0.05"
        )
        if threshold
        else None
    )
    features = build_features(frame, interval="15m")
    outcomes = build_outcomes(
        frame,
        config=OutcomeConfig(horizon=horizon, target_atr=target_atr, stop_atr=stop_atr),
        trip_to_target=to_target,
        trip_to_stop=to_stop,
        threshold=limit,
        ready=features.ready.to_numpy(),
    )
    return features, outcomes


@pytest.fixture(scope="module")
def planted():
    frame, trigger = planted_signal(12_000, drift_pct=0.9)
    features, outcomes = prepare(frame)
    result = scan(
        frame, features, outcomes, symbol="BTCUSDT", interval="15m", config=FAST
    )
    return frame, trigger, result


def test_gomulu_sinyal_bulunuyor(planted):
    """Veriye konan kural taramanın en iyi bulgularında görünmeli."""
    _, _, result = planted
    accepted = [pattern for pattern in result.buy_patterns if pattern.accepted]
    assert accepted, "Gömülü kenar bulunamadı"
    top = accepted[:5]
    assert any(
        "hacim_patlamasi" in pattern.features for pattern in top
    ), "En iyi bulgular gömülen hacim kuralını içermiyor"


def test_gomulu_sinyal_rastgeleyi_geciyor(planted):
    _, _, result = planted
    best = next(pattern for pattern in result.buy_patterns if pattern.accepted)
    assert best.random.beats_random
    assert best.bootstrap.significant
    assert best.full.net_mean_pct > 0


def test_gomulu_sinyal_test_doneminde_de_calisiyor(planted):
    """Keşif eğitim döneminde yapıldı; test dönemi dokunulmamıştı."""
    _, _, result = planted
    best = next(pattern for pattern in result.buy_patterns if pattern.accepted)
    test_split = best.split("test")
    assert test_split is not None
    assert test_split.events > 0
    assert test_split.net_mean_pct > 0


def test_rastgele_yuruyuste_oruntu_bulunmuyor():
    """Maliyet varken rastgele veride hiçbir örüntü düzeltmeden geçmemeli."""
    frame = random_walk(12_000, seed=31)
    features, outcomes = prepare(frame, target_atr=1.5)
    result = scan(
        frame, features, outcomes, symbol="BTCUSDT", interval="15m", config=FAST
    )
    accepted = [pattern for pattern in result.buy_patterns if pattern.accepted]
    assert accepted == [], f"Gürültüde {len(accepted)} örüntü 'bulundu'"


def test_duzeltme_denenen_tum_adaylar_uzerinden():
    """q değerleri, yalnızca incelenenler değil tüm adaylar sayılarak hesaplanır."""
    frame, _ = planted_signal(9_000, drift_pct=0.9)
    features, outcomes = prepare(frame)
    result = scan(
        frame, features, outcomes, symbol="BTCUSDT", interval="15m", config=FAST
    )
    assert result.candidates >= result.evaluated
    for pattern in result.buy_patterns:
        assert pattern.q_value >= pattern.bootstrap.p_value


def test_kesif_yalnizca_egitim_doneminde_yapiliyor():
    """Aday üretimi yalnızca eğitim dilimine bakar; sonrası onu etkilemez.

    Eğitim dilimi bittikten sonraki her şeyi tanınmaz hale getirip taramayı
    yeniden çalıştırıyoruz. Keşif o bölgeye bakmıyorsa aday sayısı ve
    ayrıntılı incelemeye alınan örüntüler harfi harfine aynı çıkmalı. Tek bir
    farklılık, seçim aşamasının göremeyeceği veriye baktığı anlamına gelir.

    Kabul kararı ise tam tersi: o **yalnızca** bozulan bölgeden hesaplanır.
    Bu yüzden burada kabul listesi değil, keşfin kendisi karşılaştırılıyor.
    """
    frame, _ = planted_signal(12_000, drift_pct=0.0)
    horizon = 3
    # Eğitim dilimi %50'de bitiyor; kesim noktası hedef penceresi kadar sonra,
    # yoksa son eğitim mumlarının sonucu bozulan bölgeden okunurdu.
    cut = int(len(frame) * 0.50) + horizon
    poisoned = poison_future(frame, cut)

    clean = scan(
        frame, *prepare(frame, horizon=horizon),
        symbol="BTCUSDT", interval="15m", config=FAST,
    )
    dirty = scan(
        poisoned, *prepare(poisoned, horizon=horizon),
        symbol="BTCUSDT", interval="15m", config=FAST,
    )

    assert dirty.candidates == clean.candidates, "Aday sayısı geleceğe bağlı"
    for name in ("buy_patterns", "avoid_patterns"):
        found = {pattern.features for pattern in getattr(dirty, name)}
        expected = {pattern.features for pattern in getattr(clean, name)}
        assert found == expected, f"{name}: keşif bozulan bölgeden etkilendi"


def test_az_veriyle_cokmuyor():
    frame = random_walk(120, seed=4)
    features, outcomes = prepare(frame)
    result = scan(frame, features, outcomes, symbol="BTCUSDT", interval="15m", config=FAST)
    assert result.candidates == 0
    assert result.buy_patterns == ()


def test_kacinilacak_liste_dusen_oruntuleri_yakaliyor():
    """Sonrasında fiyatın düştüğü bir kural 'kaçınılacaklar' listesine girmeli."""
    frame, trigger = planted_signal(12_000, drift_pct=-0.9)
    features, outcomes = prepare(frame)
    result = scan(frame, features, outcomes, symbol="BTCUSDT", interval="15m", config=FAST)
    accepted = [pattern for pattern in result.avoid_patterns if pattern.accepted]
    assert accepted, "Aşağı yönlü gömülü kural bulunamadı"
    assert any("hacim_patlamasi" in pattern.features for pattern in accepted[:5])
    assert np.count_nonzero(trigger) > 100
