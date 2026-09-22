"""Kart şablonunu gösteren **örnek kural** — bir öneri değildir.

SPEC.md §10'un Faz 3 kabul kriteri: *"Öneri kartları eksiksiz; marjlar
komisyon sonrası doğru."* Faz 2 bu kapsamda kabul edilen bir örüntü
bulamadı, dolayısıyla gösterilecek gerçek kart yok. Kriteri "kart uydur"
diye okumak yanlış olurdu; doğru okunuşu şudur: **kart şablonu eksiksiz
olmalı ve marj hesabı doğru olmalı** — yani bir kural çıktığı gün kart
eksiksiz dolabilmeli.

Bu modül tam olarak onu gösterir: uydurulmuş ama **açıkça uydurulmuş
olduğu yazan** bir kuralla kartı doldurur. Sayılar elle kontrol edilebilsin
diye yuvarlaktır: fiyat 100, ATR %1, hedef 1,5×ATR, stop 1×ATR, komisyon
%0,1/%0,1. Beklenen değerler::

    brüt marj   = (101,5 − 100) / 100                 = %1,5000000
    net marj    = 101,5 · (1−0,001)(1−0,001) − 100    = %1,2971015
    başa-baş    = 100 / ((1−0,001)(1−0,001))          = 100,20030041
    stop net    = 99 · (1−0,001)(1−0,001) − 100       = %−1,1979010
    Hedef 2     = ölçülen medyan MFE %1,9 → 101,9     (net %1,6963019)

``tests/test_card.py`` bu sayıları doğruluyor. Arayüz bu kartı yalnızca
kullanıcı açıkça "kart şablonunu göster" dediğinde çizer ve üstüne örnek
olduğunu yazar.
"""

from __future__ import annotations

from albsat.core.filters import SymbolRules
from albsat.risk.sizing import size_position
from albsat.strategy.card import SignalCard, build_card
from albsat.strategy.rules import CostAssumptions, Rule, RuleEvidence
from albsat.strategy.signals import (
    STATUS_EXAMPLE,
    Explanation,
    Recommendations,
    cost_trips,
)

EXAMPLE_NOTE = (
    "ÖRNEK KART — bu bir öneri değildir. Kabul edilmiş bir kural olmadığı "
    "için gerçek kart yok; burada gösterilen, bir kural çıktığında kartın "
    "hangi alanları eksiksiz dolduracağıdır. Sayılar elle doğrulanabilsin "
    "diye yuvarlak seçilmiştir (fiyat 100, ATR %1, komisyon %0,1)."
)

EXAMPLE_COST = CostAssumptions(
    maker_orani="0.001",
    taker_orani="0.001",
    spread_yuzde="0.01",
    kayma_yuzde="0.02",
    guvenlik_payi_yuzde="0.05",
    bnb_indirimi=False,
    minimum_hedef_yuzde="0.28",
    aciklama=(
        "Komisyon %0.2000 + spread %0.0100 + kayma %0.0200 + "
        "güvenlik payı %0.0500 = en az %0.2800"
    ),
)

EXAMPLE_EVIDENCE = RuleEvidence(
    olay=180,
    bagimsiz_olay=120,
    kabul_ornegi=62,
    isabet_orani=0.61,
    net_ortalama_yuzde=0.42,
    net_medyan_yuzde=0.38,
    guven_alt_yuzde=0.11,
    guven_ust_yuzde=0.73,
    p_degeri=0.000004,
    q_degeri=0.031,
    kabul_esigi_p=0.0000157,
    rastgeleyi_geciyor=True,
    rastgele_yuzdelik=0.97,
    donem_dogru_yon_orani=0.75,
    walk_forward_dogru_yon_orani=0.75,
    walk_forward_ortalama_yuzde=0.35,
    test_donemi_net_yuzde=0.29,
    hedefe_cikis_orani=0.61,
    stopa_cikis_orani=0.28,
    ortalama_tutulan_mum=2.4,
    mfe_medyan_yuzde=1.9,
    mae_medyan_yuzde=-0.8,
)

EXAMPLE_RULE = Rule(
    sembol="ÖRNEKUSDT",
    periyot="1h",
    yon="al",
    ozellikler=("ornek_kosul_a", "ornek_kosul_b"),
    etiket="ÖRNEK koşul A + ÖRNEK koşul B (uydurma kural)",
    pencere_mum=3,
    hedef_atr=1.5,
    stop_atr=1.0,
    atr_periyodu=14,
    kanit=EXAMPLE_EVIDENCE,
    uyarilar=(),
    kabul=True,
    kabul_notu=EXAMPLE_NOTE,
)

EXAMPLE_DESCRIPTIONS = {
    "ornek_kosul_a": "ÖRNEK koşul A (gerçek bir özellik değil)",
    "ornek_kosul_b": "ÖRNEK koşul B (gerçek bir özellik değil)",
}


def example_card(
    *,
    sembol: str = "ÖRNEKUSDT",
    periyot: str = "1h",
    rules: SymbolRules | None = None,
    butce_usdt: str = "100",
    risk_yuzde: str = "1.0",
) -> SignalCard:
    """Örnek kuraldan kart üretir. Veri veya kural deposu gerekmez."""
    trip_to_target, trip_to_stop = cost_trips(EXAMPLE_COST)
    rule = Rule(
        sembol=sembol.upper(),
        periyot=periyot,
        yon=EXAMPLE_RULE.yon,
        ozellikler=EXAMPLE_RULE.ozellikler,
        etiket=EXAMPLE_RULE.etiket,
        pencere_mum=EXAMPLE_RULE.pencere_mum,
        hedef_atr=EXAMPLE_RULE.hedef_atr,
        stop_atr=EXAMPLE_RULE.stop_atr,
        atr_periyodu=EXAMPLE_RULE.atr_periyodu,
        kanit=EXAMPLE_EVIDENCE,
        uyarilar=EXAMPLE_RULE.uyarilar,
        kabul=True,
        kabul_notu=EXAMPLE_NOTE,
    )

    entry = "100"
    stop = "99"
    position = size_position(
        entry=entry,
        stop=stop,
        budget_usdt=butce_usdt,
        risk_pct=risk_yuzde,
        round_trip=trip_to_stop,
        rules=rules,
    )
    return build_card(
        rule,
        close_price=entry,
        atr_pct="1",
        signal_close_time_utc="2026-01-01T00:00:00+00:00",
        interval_ms=3_600_000,
        trip_to_target=trip_to_target,
        trip_to_stop=trip_to_stop,
        position=position,
        rules=rules,
        feature_descriptions=EXAMPLE_DESCRIPTIONS,
        extra_warnings=(EXAMPLE_NOTE,),
    )


def example_recommendation(
    *, sembol: str = "ÖRNEKUSDT", periyot: str = "1h"
) -> Recommendations:
    """Örnek kartı, öneri motorunun çıktısı biçiminde sarmalar."""
    return Recommendations(
        sembol=sembol.upper(),
        periyot=periyot,
        durum=STATUS_EXAMPLE,
        kartlar=(example_card(sembol=sembol, periyot=periyot),),
        kabul_edilen_kural=0,
        tetiklenmeyen_kurallar=(),
        aciklama=Explanation(
            baslik="Örnek kart (öneri değildir)",
            satirlar=(
                EXAMPLE_NOTE,
                "Gerçek öneri için kabul edilmiş bir kural gerekir; Faz 2 "
                "taraması bu kapsamda kabul edilen kural bulamadı.",
            ),
        ),
        veri=None,
    )


__all__ = [
    "EXAMPLE_COST",
    "EXAMPLE_EVIDENCE",
    "EXAMPLE_NOTE",
    "EXAMPLE_RULE",
    "example_card",
    "example_recommendation",
]
