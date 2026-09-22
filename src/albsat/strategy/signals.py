"""Öneri motoru: kabul edilmiş kuralları son kapanmış muma uygular.

SPEC.md §10, Faz 3. Motorun akışı kısa:

1. Kural deposunu oku (``albsat.strategy.rules``). Teşhis turundan gelen bir
   depo reddedilir; oradaki hiçbir sayı işlem önerisi değildir.
2. Kabul edilmiş kurallardan bu sembol ve periyoda ait olanları seç.
3. Diskteki mum verisinden özellik tablosunu kur ve **yalnızca son kapanmış
   mumda** kuralların tetiklenip tetiklenmediğine bak (SPEC §11: kapanmamış
   mum tahmine giremez).
4. Tetiklenen her kural için öneri kartı üret.

**Kabul edilmiş kural yoksa motor bir arıza vermez.** Beklenen ve dürüst
çıktı "önerilecek kural yok"tur ve nedeni gösterilir: taban çizgisi, kaç aday
denendi, kabul eşiği neydi, en iyi aday eşikten ne kadar uzaktaydı. Faz 2'nin
ölçtüğü sonuç tam olarak budur; arayüzün işi onu gizlemek değil, okunur
kılmak.

Düzeltme öncesi "dikkat çeken" adaylar öneri üretmez ve bu motora hiç
girmez. Arayüz onları ayrı bir "incelenen adaylar" bölümünde, kabul
edilmedikleri açıkça yazılı olarak gösterir.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd

from albsat.core.costs import RoundTrip, minimum_meaningful_target, round_trip_for
from albsat.core.fees import Liquidity, flat_table
from albsat.core.filters import SymbolRules
from albsat.core.money import to_decimal
from albsat.data.klines import closed_only, interval_ms, to_utc
from albsat.data.store import KlineStore
from albsat.features import build_features
from albsat.risk.sizing import size_position
from albsat.strategy.card import SignalCard, build_card
from albsat.strategy.rules import CostAssumptions, Rule, RuleSet, SectionSummary

#: Veri bu kadar mumdan fazla geride kalmışsa "bayat" sayılır.
#: SPEC §4.1 canlı akış için saniye cinsinden bir eşik istiyor; burada veri
#: dosyadan okunduğu için ölçü mum sayısıdır.
STALE_AFTER_BARS = 2

#: Sonuç durumları.
STATUS_NO_RULES = "kural_yok"
STATUS_NOT_TRIGGERED = "kural_var_tetiklenmedi"
STATUS_HAS_SIGNAL = "oneri_var"
STATUS_NO_DATA = "veri_yok"
STATUS_DIAGNOSTIC = "teshis_deposu"
#: Kart şablonunu gösteren örnek çıktı. Gerçek durumlardan ayrı bir
#: değer taşır ki arayüz onu yanlışlıkla öneri diye çizmesin.
STATUS_EXAMPLE = "ornek"


@dataclass(frozen=True)
class DataStatus:
    """Öneri üretilen verinin tazeliği (SPEC §4.1 "bayat veri koruması")."""

    sembol: str
    periyot: str
    mum_sayisi: int
    ilk_mum_utc: str
    son_kapanis_utc: str
    gecikme_saniye: float
    bayat: bool

    @property
    def gecikme_mum(self) -> float:
        step = interval_ms(self.periyot) / 1000.0
        return self.gecikme_saniye / step if step else 0.0

    @property
    def aciklama_tr(self) -> str:
        if self.mum_sayisi == 0:
            return "Bu sembol ve periyot için kayıtlı mum yok."
        if self.bayat:
            return (
                f"Veri {self.gecikme_mum:.1f} mum geride "
                f"(son kapanış {self.son_kapanis_utc}). Önce veriyi tazeleyin; "
                "eski veriyle üretilen öneri yanıltıcıdır."
            )
        return f"Veri güncel; son kapanmış mum {self.son_kapanis_utc}."


@dataclass(frozen=True)
class Explanation:
    """"Önerilecek kural yok" sonucunun gerekçesi."""

    baslik: str
    satirlar: tuple[str, ...]
    bolumler: tuple[SectionSummary, ...] = ()


@dataclass(frozen=True)
class Recommendations:
    """Bir sembol + periyot için öneri motorunun tam çıktısı."""

    sembol: str
    periyot: str
    durum: str
    kartlar: tuple[SignalCard, ...]
    kabul_edilen_kural: int
    tetiklenmeyen_kurallar: tuple[str, ...]
    aciklama: Explanation | None
    veri: DataStatus | None

    @property
    def oneri_var(self) -> bool:
        return bool(self.kartlar)


@dataclass(frozen=True)
class EngineConfig:
    """Öneri motorunun ayarları (hepsi arayüzden değiştirilebilir)."""

    butce_usdt: str = "100"
    islem_basi_risk_yuzde: str = "1.0"
    min_olay: int = 50
    #: Özellik tablosu kurmak için okunacak azami mum sayısı. Tüm seriyi
    #: kurmak gereksiz; ısınma + birkaç yüz mum yeter ve arayüz hızlı açılır.
    #: 0 verilirse tüm seri kullanılır.
    azami_mum: int = 1200


def cost_trips(cost: CostAssumptions) -> tuple[RoundTrip, RoundTrip]:
    """Kuralın kabul edildiği maliyet varsayımlarından tur maliyetlerini kurar.

    ``cli/research.py`` ile birebir aynı eşleme: giriş her iki turda da taker
    (araştırmadaki temkinli varsayım), hedefe çıkış maker (limit), stopa
    çıkış taker (tetiklenince piyasa emri — FAZ0-MIMARI.md Risk #4).
    """
    table = flat_table("*", cost.maker_orani, cost.taker_orani)
    to_target = round_trip_for(
        table, entry_liquidity=Liquidity.TAKER, exit_liquidity=Liquidity.MAKER
    )
    to_stop = round_trip_for(
        table, entry_liquidity=Liquidity.TAKER, exit_liquidity=Liquidity.TAKER
    )
    return to_target, to_stop


def cost_threshold_text(cost: CostAssumptions) -> str:
    """Maliyet eşiğinin bileşenlerini tek satırda açıklar."""
    if cost.aciklama:
        return cost.aciklama
    _, to_stop = cost_trips(cost)
    return minimum_meaningful_target(
        to_stop,
        spread_pct=cost.spread_yuzde,
        slippage_pct=cost.kayma_yuzde,
        safety_pct=cost.guvenlik_payi_yuzde,
    ).explain()


def _data_status(
    frame: pd.DataFrame, *, sembol: str, periyot: str, now: datetime | None = None
) -> DataStatus:
    if frame.empty:
        return DataStatus(sembol, periyot, 0, "", "", 0.0, True)
    now = now or datetime.now(UTC)
    step_ms = interval_ms(periyot)
    last_open = int(frame["open_time"].max())
    last_close_ms = last_open + step_ms
    delay = (now - to_utc(last_close_ms)).total_seconds()
    return DataStatus(
        sembol=sembol,
        periyot=periyot,
        mum_sayisi=int(len(frame)),
        ilk_mum_utc=to_utc(int(frame["open_time"].min())).isoformat(),
        son_kapanis_utc=to_utc(last_close_ms).isoformat(),
        gecikme_saniye=delay,
        bayat=delay > STALE_AFTER_BARS * step_ms / 1000.0,
    )


def _no_rules_explanation(ruleset: RuleSet, sections: tuple[SectionSummary, ...]) -> Explanation:
    """Faz 2'nin ölçtüğü sonucu kullanıcının diliyle anlatır."""
    run = ruleset.kosu
    lines = [
        f"Tarama {run.toplam_aday:,} aday denedi; çoklu test düzeltmesinden "
        "geçen örüntü çıkmadı.",
        f"Bir örüntünün kabul edilmesi için gereken p-değeri: "
        f"{run.kabul_esigi_p:.7f} (yanlış buluş payı %{run.alpha * 100:.0f}, "
        f"{run.toplam_aday:,} deneme üzerinden).",
        cost_threshold_text(run.maliyet),
    ]
    if sections:
        best = min(sections, key=lambda item: item.esikten_uzaklik_kati)
        lines.append(
            f"En yakın aday ({best.sembol} {best.periyot}, {best.pencere_mum} mum) "
            f"eşiğin {best.esikten_uzaklik_kati:,.0f} katı uzağındaydı "
            f"(ham p={best.en_iyi_ham_p:.6f})."
        )
        lines.append(
            "Taban çizgisi — aynı dönemde her muma girilseydi işlem başına net: "
            + ", ".join(
                f"{item.periyot}/{item.pencere_mum} mum: "
                f"%{item.taban_net_ortalama_yuzde:+.4f}"
                for item in sections
            )
            + "."
        )
    lines.append(
        "Bu bir arıza değil, ölçülmüş bir sonuç: bu kapsamda alınacak örüntü "
        "bulunamadı. Uygulamanın 'kâr vaat etme, bulamadığını söyle' ilkesi "
        "gereği uydurma kart gösterilmiyor."
    )
    return Explanation(
        baslik="Önerilecek kural yok",
        satirlar=tuple(lines),
        bolumler=sections,
    )


def _load_frames(
    store: KlineStore, *, sembol: str, periyot: str, limit: int
) -> tuple[pd.DataFrame, pd.DataFrame | None]:
    """Sembolün ve (gerekiyorsa) BTC referansının kapanmış mumları.

    BTC etkisi özellik ailesi referans seriye ihtiyaç duyar; araştırmada da
    böyle kuruldu. Referans olmadan özellik adları eksik kalır ve kural
    uygulanamaz.
    """
    frame = store.read(sembol, periyot)
    if frame.empty:
        return frame, None
    frame = closed_only(frame).reset_index(drop=True)
    if limit > 0 and len(frame) > limit:
        frame = frame.tail(limit).reset_index(drop=True)

    if sembol == "BTCUSDT":
        return frame, None

    reference = store.read("BTCUSDT", periyot)
    if reference.empty:
        return frame, None
    reference = closed_only(reference).reset_index(drop=True)
    if limit > 0 and len(reference) > limit:
        reference = reference.tail(limit).reset_index(drop=True)
    return frame, reference


def recommend(
    ruleset: RuleSet,
    *,
    sembol: str,
    periyot: str,
    veri_dizini: Path | str,
    config: EngineConfig | None = None,
    symbol_rules: SymbolRules | None = None,
    try_rate: str | Decimal | None = None,
    now: datetime | None = None,
) -> Recommendations:
    """Bir sembol + periyot için önerileri üretir."""
    config = config or EngineConfig()

    if ruleset.kosu.teshis_turu:
        return Recommendations(
            sembol=sembol,
            periyot=periyot,
            durum=STATUS_DIAGNOSTIC,
            kartlar=(),
            kabul_edilen_kural=0,
            tetiklenmeyen_kurallar=(),
            aciklama=Explanation(
                baslik="Bu kural deposu teşhis turundan geliyor",
                satirlar=(
                    "Teşhis turu komisyon, spread ve kaymayı sıfır sayar; "
                    "oradan çıkan hiçbir sonuç işlem önerisi değildir.",
                    "Öneri üretmek için normal taramayı çalıştırın.",
                ),
            ),
            veri=None,
        )

    store = KlineStore(veri_dizini)
    frame, reference = _load_frames(
        store, sembol=sembol, periyot=periyot, limit=config.azami_mum
    )
    status = _data_status(frame, sembol=sembol, periyot=periyot, now=now)

    rules = ruleset.for_symbol(sembol, periyot)
    sections = ruleset.sections_for(sembol, periyot)

    # Sıra önemli: veri hiç yokken "önerilecek kural yok" demek,
    # taramanın başka bir kapsamda ölçtüğü sayıları bu sembolün cevabı
    # gibi gösterirdi. Veri yoksa kullanıcının yapacağı şey bellidir.
    if frame.empty:
        return Recommendations(
            sembol=sembol,
            periyot=periyot,
            durum=STATUS_NO_DATA,
            kartlar=(),
            kabul_edilen_kural=len(rules),
            tetiklenmeyen_kurallar=(),
            aciklama=Explanation(
                baslik="Veri yok",
                satirlar=(
                    f"{sembol} {periyot} için kayıtlı mum bulunamadı.",
                    "Önce veri tazeleme adımını çalıştırın.",
                ),
            ),
            veri=status,
        )

    if not rules:
        return Recommendations(
            sembol=sembol,
            periyot=periyot,
            durum=STATUS_NO_RULES,
            kartlar=(),
            kabul_edilen_kural=0,
            tetiklenmeyen_kurallar=(),
            aciklama=_no_rules_explanation(ruleset, sections),
            veri=status if status.mum_sayisi else None,
        )

    cards, not_triggered = _apply_rules(
        rules,
        frame=frame,
        reference=reference,
        ruleset=ruleset,
        config=config,
        symbol_rules=symbol_rules,
        try_rate=try_rate,
        stale=status.bayat,
    )

    if cards:
        durum = STATUS_HAS_SIGNAL
        aciklama = None
    else:
        durum = STATUS_NOT_TRIGGERED
        aciklama = Explanation(
            baslik="Şu an açık bir öneri yok",
            satirlar=(
                f"{len(rules)} kabul edilmiş kural var ama son kapanmış mumda "
                "hiçbiri tetiklenmedi.",
                "Kurallar her mum kapanışında yeniden değerlendirilir.",
            ),
            bolumler=sections,
        )

    return Recommendations(
        sembol=sembol,
        periyot=periyot,
        durum=durum,
        kartlar=cards,
        kabul_edilen_kural=len(rules),
        tetiklenmeyen_kurallar=not_triggered,
        aciklama=aciklama,
        veri=status,
    )


def _apply_rules(
    rules: tuple[Rule, ...],
    *,
    frame: pd.DataFrame,
    reference: pd.DataFrame | None,
    ruleset: RuleSet,
    config: EngineConfig,
    symbol_rules: SymbolRules | None,
    try_rate: str | Decimal | None,
    stale: bool,
) -> tuple[tuple[SignalCard, ...], tuple[str, ...]]:
    periyot = rules[0].periyot
    feature_set = build_features(frame, interval=periyot, context=reference)
    trip_to_target, trip_to_stop = cost_trips(ruleset.kosu.maliyet)

    last = len(feature_set.frame) - 1
    if last < 0:
        return (), tuple(rule.etiket for rule in rules)

    # Isınma bitmeden özellikler güvenilmez; araştırma da o satırları olay
    # saymıyordu.
    if not bool(feature_set.ready.to_numpy()[last]):
        return (), tuple(rule.etiket for rule in rules)

    close_time_ms = int(frame["open_time"].iloc[last]) + interval_ms(periyot)
    close_price = to_decimal(f"{float(frame['close'].iloc[last]):.10f}")

    cards: list[SignalCard] = []
    not_triggered: list[str] = []
    descriptions = dict(feature_set.descriptions)

    for rule in rules:
        missing = [name for name in rule.ozellikler if name not in feature_set.frame]
        if missing:
            not_triggered.append(
                f"{rule.etiket} — özellik tablosunda bulunamadı: {', '.join(missing)}"
            )
            continue

        fired = all(
            bool(feature_set.frame[name].to_numpy(dtype=bool)[last])
            for name in rule.ozellikler
        )
        if not fired:
            not_triggered.append(rule.etiket)
            continue

        atr_pct = _atr_pct_at(frame, rule.atr_periyodu, last)
        if atr_pct is None:
            not_triggered.append(f"{rule.etiket} — ATR hesaplanamadı")
            continue

        target_pct = atr_pct * to_decimal(f"{rule.hedef_atr:g}")
        stop_pct = atr_pct * to_decimal(f"{rule.stop_atr:g}")
        entry = close_price
        stop = entry * (to_decimal("100") - stop_pct) / to_decimal("100")
        if symbol_rules is not None:
            from albsat.core.fees import Side

            entry = symbol_rules.round_price(entry, Side.BUY)
            stop = symbol_rules.round_price(stop, Side.BUY)

        position = size_position(
            entry=entry,
            stop=stop,
            budget_usdt=config.butce_usdt,
            risk_pct=config.islem_basi_risk_yuzde,
            round_trip=trip_to_stop,
            rules=symbol_rules,
        )

        extra: list[str] = []
        if stale:
            extra.append(
                "Veri güncel değil; bu kart eski bir mumdan üretildi ve "
                "işleme esas alınmamalıdır."
            )
        if target_pct <= 0:
            extra.append("Hedef mesafesi sıfır veya negatif çıktı.")

        cards.append(
            build_card(
                rule,
                close_price=close_price,
                atr_pct=atr_pct,
                signal_close_time_utc=to_utc(close_time_ms).isoformat(),
                interval_ms=interval_ms(periyot),
                trip_to_target=trip_to_target,
                trip_to_stop=trip_to_stop,
                position=position,
                rules=symbol_rules,
                feature_descriptions=descriptions,
                min_events=config.min_olay,
                try_rate=try_rate,
                extra_warnings=tuple(extra),
            )
        )

    return tuple(cards), tuple(not_triggered)


def _atr_pct_at(frame: pd.DataFrame, period: int, index: int) -> Decimal | None:
    from albsat.features.indicators import atr_percent

    series = atr_percent(frame, period).to_numpy(dtype=float)
    if index >= len(series):
        return None
    value = float(series[index])
    if not np.isfinite(value) or value <= 0.0:
        return None
    return to_decimal(f"{value:.8f}")


__all__ = [
    "STALE_AFTER_BARS",
    "STATUS_DIAGNOSTIC",
    "STATUS_EXAMPLE",
    "STATUS_HAS_SIGNAL",
    "STATUS_NOT_TRIGGERED",
    "STATUS_NO_DATA",
    "STATUS_NO_RULES",
    "DataStatus",
    "EngineConfig",
    "Explanation",
    "Recommendations",
    "cost_threshold_text",
    "cost_trips",
    "recommend",
]
