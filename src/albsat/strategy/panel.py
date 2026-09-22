"""Maliyet ve risk paneli — B ekinin hesap makinesi.

Ana akış "önerilecek kural yok" diyor. Bu panel ise kullanıcının **kendi**
aklındaki işlemi, emir göndermeden önce sayılarla görmesi içindir: şu
fiyattan alıp şu fiyattan satarsan komisyon sonrası eline ne geçer, başa-baş
nerede, ters giderse cebinden ne çıkar, bu bütçeyle hangi büyüklük
gönderilebilir.

Panelin öneri motorundan tek farkı **girdiyi kullanıcının vermesi**. Hesap
aynı motordur: ``albsat.core.costs`` ve ``albsat.risk.sizing``. İki ayrı
hesap yazmak, birinin diğerinden sessizce ayrışması demek olurdu.

Panel hiçbir koşulda "bu iyi bir işlem" demez. Söylediği tek yargı,
hedefin maliyet eşiğini geçip geçmediğidir (SPEC §4.3): geçmiyorsa o işlem
matematiksel olarak zararınadır ve panel bunu açıkça yazar.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from albsat.core.costs import TargetThreshold, minimum_meaningful_target
from albsat.core.filters import SymbolRules
from albsat.core.money import (
    ONE_HUNDRED,
    ZERO,
    Number,
    Rounding,
    clamp_decimals,
    to_decimal,
)
from albsat.risk.sizing import PositionSize, size_position
from albsat.strategy.rules import CostAssumptions
from albsat.strategy.signals import cost_trips


@dataclass(frozen=True)
class CostRiskPanel:
    """Tek bir varsayımsal işlemin tam maliyet ve risk dökümü."""

    sembol: str
    giris: Decimal
    hedef: Decimal
    stop: Decimal
    basa_bas: Decimal

    brut_marj_yuzde: Decimal
    net_marj_yuzde: Decimal
    stop_net_marj_yuzde: Decimal
    komisyon_yuzde: Decimal
    esik: TargetThreshold
    hedef_esigi_geciyor: bool
    risk_odul: Decimal | None

    pozisyon: PositionSize
    hedef_kazanc_usdt: Decimal
    stop_zarari_usdt: Decimal
    odenecek_komisyon_usdt: Decimal

    filtreler_uygulandi: bool
    uyarilar: tuple[str, ...]
    try_kuru: Decimal | None = None

    @property
    def sonuc_tr(self) -> str:
        if not self.hedef_esigi_geciyor:
            return (
                f"Hedef, maliyet eşiğinin altında kalıyor "
                f"(%{self.brut_marj_yuzde:.4f} < %{self.esik.minimum_target_pct:.4f}). "
                "Bu işlem komisyonu bile çıkarmaz."
            )
        if self.net_marj_yuzde <= ZERO:
            return "Hedefe ulaşsa bile komisyon sonrası net kâr çıkmıyor."
        return (
            f"Hedefe ulaşırsa komisyon sonrası net %{self.net_marj_yuzde:.4f}; "
            f"başa-baş fiyatı {self.basa_bas}."
        )

    def try_of(self, value: Decimal | None) -> Decimal | None:
        if value is None or self.try_kuru is None:
            return None
        return (value * self.try_kuru).quantize(Decimal("0.01"))


def evaluate(
    *,
    sembol: str,
    giris: Number,
    hedef: Number,
    stop: Number,
    butce_usdt: Number,
    risk_yuzde: Number,
    maliyet: CostAssumptions,
    rules: SymbolRules | None = None,
    try_kuru: Number | None = None,
) -> CostRiskPanel:
    """Kullanıcının verdiği fiyatlarla maliyet ve risk dökümünü üretir."""
    trip_to_target, trip_to_stop = cost_trips(maliyet)

    entry = to_decimal(giris)
    target = to_decimal(hedef)
    stop_price = to_decimal(stop)

    warnings: list[str] = []
    if entry <= ZERO:
        raise ValueError("Giriş fiyatı pozitif olmalı")
    if target <= entry:
        raise ValueError("Hedef, giriş fiyatının üstünde olmalı")
    if stop_price >= entry:
        raise ValueError("Stop, giriş fiyatının altında olmalı")

    if rules is not None:
        from albsat.core.fees import Side

        rounded_entry = rules.round_price(entry, Side.BUY)
        rounded_target = rules.round_price(target, Side.SELL)
        rounded_stop = rules.round_price(stop_price, Side.BUY)
        if (rounded_entry, rounded_target, rounded_stop) != (entry, target, stop_price):
            warnings.append(
                f"Fiyatlar borsanın adımına yuvarlandı: giriş {entry} → "
                f"{rounded_entry}, hedef {target} → {rounded_target}, stop "
                f"{stop_price} → {rounded_stop}."
            )
        entry, target, stop_price = rounded_entry, rounded_target, rounded_stop
    else:
        warnings.append(
            "Sembol filtreleri elde yok; fiyatlar borsaya gönderilebilir "
            "biçime yuvarlanmadı. Veri tazeleme adımını çalıştırın."
        )

    threshold = minimum_meaningful_target(
        trip_to_stop,
        spread_pct=maliyet.spread_yuzde,
        slippage_pct=maliyet.kayma_yuzde,
        safety_pct=maliyet.guvenlik_payi_yuzde,
    )

    gross = (target - entry) / entry * ONE_HUNDRED
    net = trip_to_target.net_margin_pct(entry, target)
    stop_net = trip_to_stop.net_margin_pct(entry, stop_price)
    break_even = trip_to_target.break_even_price(entry)
    if rules is not None:
        from albsat.core.fees import Side

        break_even = rules.round_price(break_even, Side.SELL)
    else:
        break_even = clamp_decimals(break_even, mode=Rounding.CEILING)

    position = size_position(
        entry=entry,
        stop=stop_price,
        budget_usdt=butce_usdt,
        risk_pct=risk_yuzde,
        round_trip=trip_to_stop,
        rules=rules,
    )
    warnings.extend(position.uyarilar)

    gain = trip_to_target.net_pnl_quote(entry, target, position.miktar)
    loss = -trip_to_stop.net_pnl_quote(entry, stop_price, position.miktar)
    fees = (
        trip_to_target.entry.rate * entry * position.miktar
        + trip_to_target.exit.rate * target * position.miktar
    )

    risk_reward: Decimal | None = None
    if stop_net < ZERO and net > ZERO:
        risk_reward = (net / -stop_net).quantize(Decimal("0.01"))

    passes = threshold.passes(gross)
    if not passes:
        warnings.append(
            "SPEC §4.3'e göre bu sinyal otomatik elenirdi: hedef, maliyeti "
            "karşılayan en küçük mesafenin altında."
        )

    return CostRiskPanel(
        sembol=sembol,
        giris=entry,
        hedef=target,
        stop=stop_price,
        basa_bas=break_even,
        brut_marj_yuzde=gross,
        net_marj_yuzde=net,
        stop_net_marj_yuzde=stop_net,
        komisyon_yuzde=trip_to_target.total_fee_pct,
        esik=threshold,
        hedef_esigi_geciyor=passes,
        risk_odul=risk_reward,
        pozisyon=position,
        hedef_kazanc_usdt=gain,
        stop_zarari_usdt=loss if loss > ZERO else ZERO,
        odenecek_komisyon_usdt=fees,
        filtreler_uygulandi=rules is not None,
        uyarilar=tuple(dict.fromkeys(warnings)),
        try_kuru=to_decimal(try_kuru) if try_kuru is not None else None,
    )


__all__ = ["CostRiskPanel", "evaluate"]
