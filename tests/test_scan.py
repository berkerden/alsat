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

from dataclasses import replace

import numpy as np
import pytest
from veri_uret import planted_signal, poison_future, random_walk

from albsat.core.costs import minimum_meaningful_target, round_trip_for
from albsat.core.fees import Liquidity, flat_table
from albsat.features import build_features
from albsat.research.eventstudy import OutcomeConfig, build_outcomes
from albsat.research.scan import ScanConfig, apply_global_correction, scan

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


def _sections(frame, horizons=(2, 3, 4)):
    """Aynı seriyi birkaç pencereyle tarar — gerçek bir koşunun küçüğü."""
    return [
        scan(
            frame, *prepare(frame, horizon=horizon),
            symbol="BTCUSDT", interval="15m", config=FAST,
        )
        for horizon in horizons
    ]


def test_kosu_geneli_duzeltme_tek_bolumluk_sansi_eliyor():
    """Yalnızca bir bölümde parlayan örüntü, koşu geneli sayıldığında elenmeli.

    Gerçek koşuda tam olarak bu oldu: 12 bölüm ayrı ayrı %10 payla
    düzeltildi ve ortada hiçbir şey yokken bir örüntü kabul edildi —
    12 × 0,10 ≈ 1,2, yani şansın üreteceği sayının kendisi.
    """
    frame = random_walk(9_000, seed=7)
    sections = _sections(frame)
    first = sections[0]
    # Maliyet varken rastgele yürüyüşte "alınacak" adayı hiç çıkmaz; şansı
    # kaçınma listesine yerleştiriyoruz. Düzeltme iki listeyi ayırmaz.
    assert first.avoid_patterns, "aday çıkmadı; test kurulamıyor"

    # Yalnızca kendi bölümü sayılsaydı rahatça kabul edilecek bir p-değeri.
    lucky = first.avoid_patterns[0]
    p_value = FAST.alpha / first.candidates / 2
    lucky = replace(lucky, bootstrap=replace(lucky.bootstrap, p_value=p_value))
    first = replace(first, avoid_patterns=(lucky, *first.avoid_patterns[1:]))

    alone = apply_global_correction([first])[0]
    assert any(
        item.accepted and item.features == lucky.features
        for item in alone.avoid_patterns
    ), "tek bölüm sayıldığında kabul edilmeliydi; test kurulumu bozuk"

    whole = apply_global_correction([first, *sections[1:]])[0]
    assert not any(
        item.accepted and item.features == lucky.features
        for item in whole.avoid_patterns
    ), "tek bölümde parlayan örüntü koşu genelinde de kabul edildi"


def test_kosu_geneli_duzeltme_gercek_kenari_koruyor():
    """Bölümlerin hepsinde görünen gerçek bir kenar elenmemeli.

    Düzeltmeyi sertleştirmek kolaydır; marifet, bulunması gerekeni bulmaya
    devam etmesidir.
    """
    frame, _ = planted_signal(9_000, drift_pct=0.9)
    sections = _sections(frame)
    corrected = apply_global_correction(sections)

    total = sum(item.candidates for item in sections)
    assert all(item.family_tests == total for item in corrected)
    assert any(
        pattern.accepted for item in corrected for pattern in item.buy_patterns
    ), "koşu geneli düzeltme gömülü kenarı da eledi"


def test_tek_bolumluk_kosuda_duzeltme_degismiyor():
    frame, _ = planted_signal(9_000, drift_pct=0.9)
    section = _sections(frame, horizons=(3,))[0]
    corrected = apply_global_correction([section])[0]

    assert corrected.family_tests == section.candidates
    before = {item.features for item in section.buy_patterns if item.accepted}
    after = {item.features for item in corrected.buy_patterns if item.accepted}
    assert before == after
