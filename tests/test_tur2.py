"""İkinci kural arama turu (docs/TUR2-ONKAYIT.md) için eklenenler.

* Önceki turların adayları çoklu test düzeltmesine eklenir; aynı soruyu
  yeni veriyle yeniden sormak da bir denemedir.
* Tabana oturan p-değerleri ailenin eşiğini çözecek kadar yeniden ölçülür;
  yoksa gerçekten güçlü tek bir örüntü, ne kadar güçlü olursa olsun reddedilir.
* ``--gun`` penceresi veriyi kapsamıyorsa tarama hiç başlamaz.
"""

from __future__ import annotations

from dataclasses import astuple, replace

import numpy as np
import pytest
from test_scan import FAST, prepare
from veri_uret import planted_signal, random_walk

from albsat.cli import research
from albsat.data.klines import interval_ms
from albsat.data.store import KlineStore
from albsat.research import stats
from albsat.research.scan import apply_global_correction, refine_for_family, scan
from albsat.research.stats import p_value_floor, required_iterations
from albsat.strategy import rules as rulestore
from albsat.strategy.rules import RunSummary

# --- bootstrap parçalama ---------------------------------------------------------


def test_parcali_bootstrap_tek_seferle_birebir_ayni():
    """Parça sınırını aşan yinelemede sonuç, tek seferde çekilmişle aynı olmalı."""
    values = np.random.default_rng(3).normal(0.05, 1.0, 120)
    iterations = stats.BOOTSTRAP_CHUNK * 2 + 17

    generator = np.random.default_rng(9)
    draws = generator.integers(0, values.size, size=(iterations, values.size))
    means = values[draws].mean(axis=1)
    observed = float(values.mean())
    reached = np.count_nonzero((values - observed)[draws].mean(axis=1) >= observed)
    low, high = np.percentile(means, [2.5, 97.5])

    result = stats.bootstrap_mean(values, iterations=iterations, seed=9)
    assert astuple(result) == (
        observed, float(low), float(high), (reached + 1) / (iterations + 1), iterations
    )


# --- önceki turlar ve çözünürlük -------------------------------------------------


@pytest.fixture(scope="module")
def planted():
    frame, _ = planted_signal(9_000, drift_pct=0.9)
    features, outcomes = prepare(frame)
    result = scan(frame, features, outcomes, symbol="BTCUSDT", interval="15m", config=FAST)
    return features, outcomes, result


def _at_floor(pattern) -> bool:
    return pattern.bootstrap.p_value <= p_value_floor(pattern.bootstrap.iterations) * (1 + 1e-9)


def test_onceki_turlar_aileye_ekleniyor(planted):
    _, _, result = planted
    corrected = apply_global_correction([result], prior_tests=6_372)[0]
    assert corrected.family_tests == result.candidates + 6_372
    assert corrected.prior_tests == 6_372
    assert corrected.acceptance_p_threshold == pytest.approx(
        FAST.alpha / (result.candidates + 6_372)
    )


def test_onceki_tur_sayisi_negatif_olamaz(planted):
    _, _, result = planted
    with pytest.raises(ValueError):
        apply_global_correction([result], prior_tests=-1)


def test_tabandaki_tek_guclu_oruntu_aile_esiginde_de_olculuyor(planted):
    """22 Eylül 2026 koşusunun kör noktası.

    Bölüm içi çözünürlük turu yinelemeyi bölümün aday sayısına göre seçer.
    Kabul kararı ise çok daha büyük bir aile üzerinden verilir. Tek başına
    güçlü bir örüntünün p-değeri bölümün tabanında kalırsa aile eşiğine
    inemez ve reddedilir. Aile turu bunu düzeltmeli.
    """
    features, outcomes, result = planted
    strong = next(item for item in result.buy_patterns if _at_floor(item))
    alone = replace(result, buy_patterns=(strong,), avoid_patterns=())
    prior = 6_372
    family = alone.candidates + prior

    blind = apply_global_correction([alone], prior_tests=prior)[0]
    assert not blind.buy_patterns[0].accepted, "test kurulumu bozuk: kör nokta görünmüyor"

    refined = refine_for_family(alone, feature_set=features, outcomes=outcomes,
                                family_tests=family)
    assert refined.buy_patterns[0].bootstrap.iterations == required_iterations(family)
    assert refined.buy_patterns[0].bootstrap.p_value < strong.bootstrap.p_value

    decided = apply_global_correction([refined], prior_tests=prior)[0]
    assert decided.buy_patterns[0].accepted, "güçlü tek örüntü aile eşiğinde kabul edilmedi"


def test_aile_turu_tabanda_olmayani_degistirmiyor(planted):
    features, outcomes, result = planted
    refined = refine_for_family(result, feature_set=features, outcomes=outcomes,
                                family_tests=result.candidates + 1_000)
    for before, after in zip(result.avoid_patterns, refined.avoid_patterns, strict=True):
        if not _at_floor(before):
            assert after is before


def test_rastgele_yuruyuste_onceki_turlarla_kabul_cikmiyor():
    frame = random_walk(9_000, seed=21)
    features, outcomes = prepare(frame)
    result = scan(frame, features, outcomes, symbol="BTCUSDT", interval="15m", config=FAST)
    refined = refine_for_family(result, feature_set=features, outcomes=outcomes,
                                family_tests=result.candidates + 6_372)
    decided = apply_global_correction([refined], prior_tests=6_372)[0]
    assert not any(item.accepted for item in decided.buy_patterns + decided.avoid_patterns)


# --- kural deposu -----------------------------------------------------------------


def _summary(**changes) -> RunSummary:
    payload = {
        "kosu_zamani_utc": "2026-09-22T00:00:00+00:00",
        "teshis_turu": False,
        "semboller": ["BTCUSDT"],
        "periyotlar": ["15m"],
        "pencereler": [3],
        "toplam_aday": 500,
        "toplam_incelenen": 200,
        "alpha": 0.1,
        "kabul_esigi_p": 0.0002,
        "maliyet": {
            "maker_orani": "0.001", "taker_orani": "0.001", "spread_yuzde": "0.01",
            "kayma_yuzde": "0.02", "guvenlik_payi_yuzde": "0.05", "bnb_indirimi": False,
            "minimum_hedef_yuzde": "0.28", "aciklama": "",
        },
    }
    payload.update(changes)
    return RunSummary.from_dict(payload)


def test_faz2_donemi_deposu_onceki_tur_bilgisi_olmadan_okunuyor():
    run = _summary()
    assert run.onceki_aday == 0 and run.birikimli_aday == 0
    assert run.duzeltme_denemesi == 500


def test_duzeltme_denemesi_onceki_turlari_iceriyor():
    run = _summary(onceki_aday=6_372, birikimli_aday=6_872)
    assert run.duzeltme_denemesi == 6_872


def test_kural_yok_aciklamasi_onceki_turlari_soyluyor():
    from albsat.strategy.signals import _no_rules_explanation

    ruleset = rulestore.RuleSet(kosu=_summary(onceki_aday=6_372, birikimli_aday=6_872))
    text = " ".join(_no_rules_explanation(ruleset, ()).satirlar)
    assert "önceki turların 6,372 adayı dahil 6,872 deneme üzerinden" in text


# --- komut satırı ------------------------------------------------------------------


def _write(root, *, days_15m: float, days_1h: float, end_shift_ms: int = 0):
    store = KlineStore(root)
    for symbol, seed, start in (("BTCUSDT", 1, 43_000.0), ("SOLUSDT", 3, 180.0)):
        for interval, days in (("15m", days_15m), ("1h", days_1h)):
            step = interval_ms(interval)
            count = int(days * 86_400_000 / step)
            frame = random_walk(count, seed=seed + len(interval), start=start, step_ms=step)
            # Hepsi aynı anda bitsin: pencere en yeni mumdan geriye sayılır.
            frame["open_time"] = frame["open_time"] - frame["open_time"].iloc[-1] + (
                1_790_000_000_000 - end_shift_ms
            )
            store.write(frame, symbol=symbol, interval=interval)
    return root


def _run(root, tmp_path, *extra):
    return research.main([
        "--veri-dizini", str(root), "--rapor", str(tmp_path / "rapor.txt"),
        "--hizli", "--pencereler", "3", *extra,
    ])


def test_pencere_veriyi_kapsamiyorsa_tarama_baslamiyor(tmp_path, capsys):
    root = _write(tmp_path / "veri", days_15m=20, days_1h=60)
    assert _run(root, tmp_path, "--gun", "30") == 4
    err = capsys.readouterr().err
    assert "BTCUSDT 15m" in err and "kapsamıyor" in err
    assert not (root / "kurallar.json").exists()
    assert not (tmp_path / "rapor.txt").exists()


def test_eski_kalan_seri_tarama_baslatmiyor(tmp_path, capsys):
    root = _write(tmp_path / "veri", days_15m=40, days_1h=40)
    KlineStore(root).write(
        random_walk(900, seed=5, step_ms=interval_ms("1h")), symbol="SOLUSDT", interval="1h"
    )
    assert _run(root, tmp_path, "--gun", "30") == 4
    assert "son mum" in capsys.readouterr().err


def test_arada_eksik_ay_tarama_baslatmiyor(tmp_path, capsys):
    root = _write(tmp_path / "veri", days_15m=40, days_1h=40)
    store = KlineStore(root)
    frame = store.read("SOLUSDT", "15m")
    middle = frame["open_time"].iloc[len(frame) // 2]
    hole = (frame["open_time"] > middle) & (frame["open_time"] < middle + 5 * 86_400_000)
    store.write(frame[~hole].reset_index(drop=True), symbol="SOLUSDT", interval="15m")
    assert _run(root, tmp_path, "--gun", "30") == 4
    assert "eksik dönem" in capsys.readouterr().err


def test_pencere_ve_onceki_tur_depoya_yaziliyor(tmp_path):
    root = _write(tmp_path / "veri", days_15m=40, days_1h=80)
    assert _run(root, tmp_path, "--gun", "30", "--onceki-aday", "6372") == 0

    run = rulestore.load(root / "kurallar.json").kosu
    assert run.onceki_aday == 6_372
    assert run.birikimli_aday == 6_372 + run.toplam_aday
    assert run.kabul_esigi_p == pytest.approx(0.10 / run.duzeltme_denemesi)
    span = np.datetime64(run.veri_bitis_utc[:19]) - np.datetime64(run.veri_baslangic_utc[:19])
    assert span <= np.timedelta64(30, "D")

    text = (tmp_path / "rapor.txt").read_text(encoding="utf-8")
    assert "YENİ TUR" in text and "geriye 30 gün" in text
    assert "6,372" in text


def test_sonraki_tur_birikimli_sayiyi_kendiliginden_okuyor(tmp_path):
    root = _write(tmp_path / "veri", days_15m=40, days_1h=80)
    assert _run(root, tmp_path, "--onceki-aday", "1000") == 0
    first = rulestore.load(root / "kurallar.json").kosu

    assert _run(root, tmp_path) == 0
    second = rulestore.load(root / "kurallar.json").kosu
    assert second.onceki_aday == first.birikimli_aday
    assert second.birikimli_aday == first.birikimli_aday + second.toplam_aday


def test_teshis_turu_birikimli_sayiyi_artirmadan_tasiyor(tmp_path):
    root = _write(tmp_path / "veri", days_15m=40, days_1h=80)
    assert _run(root, tmp_path, "--onceki-aday", "1000") == 0
    first = rulestore.load(root / "kurallar.json").kosu

    assert _run(root, tmp_path, "--maliyetsiz") == 0
    teshis = rulestore.load(root / "kurallar.json").kosu
    assert teshis.teshis_turu
    assert teshis.birikimli_aday == first.birikimli_aday


def test_bozuk_depo_onceki_turu_sifir_saymiyor(tmp_path, capsys):
    root = _write(tmp_path / "veri", days_15m=40, days_1h=80)
    (root / "kurallar.json").write_text("{bozuk", encoding="utf-8")
    assert _run(root, tmp_path) == 2
    assert "--onceki-aday" in capsys.readouterr().err
