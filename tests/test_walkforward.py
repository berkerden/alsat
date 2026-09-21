"""Zaman bölmesi ve kararlılık testleri."""

from __future__ import annotations

import numpy as np
import pytest

from albsat.research.walkforward import (
    positive_period_share,
    stability_by_period,
    train_validation_test,
    walk_forward_splits,
)


def test_bolmeler_sirali_ve_ortusmuyor():
    train, validation, test = train_validation_test(1_000)
    assert train.start == 0
    assert train.stop == validation.start
    assert validation.stop == test.start
    assert test.stop == 1_000
    assert len(train) + len(validation) + len(test) == 1_000


def test_bolme_oranlari_toplami_bir_olmali():
    with pytest.raises(ValueError):
        train_validation_test(100, fractions=(0.5, 0.3, 0.3))


def test_bos_veri_bolmesi():
    train, validation, test = train_validation_test(0)
    assert len(train) == len(validation) == len(test) == 0


def test_walk_forward_egitim_her_zaman_testten_once():
    layers = walk_forward_splits(1_000, folds=4)
    assert len(layers) == 4
    for train, test in layers:
        assert train.start == 0
        assert train.stop == test.start, "Eğitim penceresi testle örtüşüyor"
        assert test.stop > test.start
    # Katmanlar ilerledikçe eğitim büyümeli.
    assert [len(train) for train, _ in layers] == sorted(
        len(train) for train, _ in layers
    )
    # Test dilimleri tüm sonu kapsamalı.
    assert layers[-1][1].stop == 1_000


def test_walk_forward_az_veride_bos_doner():
    assert walk_forward_splits(0) == []
    assert walk_forward_splits(5, folds=100) == []


def test_bolme_maskesi():
    _, validation, _ = train_validation_test(100)
    mask = validation.mask(100)
    assert mask.sum() == len(validation)
    assert not mask[: validation.start].any()


def test_kararlilik_donemlere_boluyor():
    count = 400
    step = 24 * 3_600_000  # günlük
    open_time = np.arange(count) * step + 1_740_000_000_000
    net = np.where(np.arange(count) < 200, 1.0, -1.0)
    hit = net > 0
    rows = stability_by_period(open_time, net, hit, np.ones(count, dtype=bool))
    assert len(rows) >= 4
    assert sum(row.events for row in rows) == count
    # İlk yarı artıda, ikinci yarı ekside: oran yarıya yakın olmalı.
    assert 0.3 <= positive_period_share(rows) <= 0.7


def test_kararlilik_bos_maskede_bos_doner():
    open_time = np.arange(10) * 900_000 + 1_740_000_000_000
    rows = stability_by_period(
        open_time, np.zeros(10), np.zeros(10, dtype=bool), np.zeros(10, dtype=bool)
    )
    assert rows == []
    assert positive_period_share(rows) == 0.0
