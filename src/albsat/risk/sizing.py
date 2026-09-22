"""Pozisyon büyüklüğü: bütçe mi risk kuralı mı bağlıyor, ve yuvarlama ne kadar kaydırıyor.

SPEC.md §4.6: "Bot sadece ayırdığım tutarı kullanır (varsayılan 100 USDT)" ve
"İşlem başı risk: bot bütçesinin %0,5–1'i. Pozisyon büyüklüğü stop mesafesine
göre hesaplanır."

FAZ0-MIMARI.md Risk #2 bu iki kuralın küçük bütçede çatıştığını gösteriyordu:
100 USDT bütçeyle ve dar bir stop mesafesiyle, "bütçenin %1'i kadar risk al"
kuralı 100 USDT'den büyük bir pozisyon ister. O zaman bağlayıcı olan risk
kuralı değil bütçedir ve **gerçekte alınan risk hedeflenenin altında kalır**.
Tersi de olur: geniş stopta risk kuralı bağlar ve bütçenin bir kısmı boşta
kalır. Hangisinin bağladığı her öneride yazılır; kullanıcı bunu tahmin etmek
zorunda bırakılmaz.

İkinci gözden kaçan nokta ``stepSize``. Miktar her zaman **aşağı** yuvarlanır
(yukarı yuvarlamak hedeflenen riski aşmak olurdu). SOLUSDT'de adım 0,001 iken
kayıp ihmal edilebilir; BTCUSDT'de 0,00001 adım ve 100 USDT bütçe ile hâlâ
küçüktür, ama ``minNotional`` (genelde 5 USDT) bütçenin küçük bir yüzdesiyle
işlem açmayı tamamen imkânsız kılabilir. Bu hesap ikisini de açıkça söyler.

Buradaki tek iş **büyüklük hesabı**. Günlük zarar limiti, art arda kayıp,
soğuma süresi gibi kapılar risk motorunun geri kalanıdır ve Faz 4'ün işidir.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from albsat.core.costs import RoundTrip
from albsat.core.fees import Side
from albsat.core.filters import SymbolRules
from albsat.core.money import (
    ONE_HUNDRED,
    ZERO,
    Number,
    Rounding,
    clamp_decimals,
    to_decimal,
)


class Binding(str, Enum):
    """Pozisyon büyüklüğünü hangi kuralın sınırladığı."""

    RISK = "risk"
    BUDGET = "butce"
    NONE = "yok"


BINDING_LABELS_TR = {
    Binding.RISK: "risk kuralı",
    Binding.BUDGET: "bütçe",
    Binding.NONE: "sınırlanmadı",
}


@dataclass(frozen=True)
class PositionSize:
    """Bir öneri için hesaplanmış pozisyon büyüklüğü ve gerçek riski."""

    #: Bütçenin tamamı (USDT).
    butce_usdt: Decimal
    #: Kural gereği işlem başına göze alınan tutar (USDT).
    hedeflenen_risk_usdt: Decimal
    giris: Decimal
    stop: Decimal
    #: Risk kuralının izin verdiği ham miktar (yuvarlanmamış).
    risk_miktari: Decimal
    #: Bütçenin izin verdiği ham miktar (yuvarlanmamış).
    butce_miktari: Decimal
    #: İkisinin küçüğü, yuvarlanmamış.
    ham_miktar: Decimal
    #: ``stepSize``'a aşağı yuvarlanmış, borsaya gidebilecek miktar.
    miktar: Decimal
    baglayici: Binding
    #: ``miktar × giris`` (USDT).
    tutar_usdt: Decimal
    #: Stop'a düşülürse komisyon dahil gerçekleşecek zarar (pozitif sayı, USDT).
    stop_zarari_usdt: Decimal
    #: Emir gönderilebilir mi? ``minNotional``/``minQty`` sağlanmıyorsa hayır.
    gecerli: bool
    uyarilar: tuple[str, ...] = ()
    #: ``minNotional``'ı sağlayan en küçük geçerli miktar (bilgi amaçlı).
    asgari_gecerli_miktar: Decimal = ZERO

    @property
    def yuvarlama_kaybi_yuzde(self) -> Decimal:
        """``stepSize`` yuvarlaması ham miktarın yüzde kaçını götürdü?"""
        if self.ham_miktar <= ZERO:
            return ZERO
        return (self.ham_miktar - self.miktar) / self.ham_miktar * ONE_HUNDRED

    @property
    def gerceklesen_risk_yuzde(self) -> Decimal:
        """Stop zararının bütçeye oranı — kuralın hedefiyle karşılaştırmak için."""
        if self.butce_usdt <= ZERO:
            return ZERO
        return self.stop_zarari_usdt / self.butce_usdt * ONE_HUNDRED

    @property
    def butce_kullanim_yuzde(self) -> Decimal:
        if self.butce_usdt <= ZERO:
            return ZERO
        return self.tutar_usdt / self.butce_usdt * ONE_HUNDRED

    @property
    def aciklama_tr(self) -> str:
        """Arayüzde tek satırda gösterilecek "neden bu büyüklük" cümlesi."""
        if not self.gecerli:
            return "Bu bütçeyle geçerli bir emir büyüklüğü çıkmıyor."
        if self.baglayici is Binding.BUDGET:
            return (
                f"Bağlayıcı olan bütçe: {self.butce_usdt} USDT'nin tamamı "
                f"kullanılıyor, gerçekleşen risk %{self.gerceklesen_risk_yuzde:.2f} "
                f"(hedef %{self._target_pct():.2f})."
            )
        return (
            f"Bağlayıcı olan risk kuralı: stop mesafesi bütçenin "
            f"%{self.butce_kullanim_yuzde:.1f}'ini kullandırıyor, gerçekleşen "
            f"risk %{self.gerceklesen_risk_yuzde:.2f}."
        )

    def _target_pct(self) -> Decimal:
        if self.butce_usdt <= ZERO:
            return ZERO
        return self.hedeflenen_risk_usdt / self.butce_usdt * ONE_HUNDRED


def size_position(
    *,
    entry: Number,
    stop: Number,
    budget_usdt: Number,
    risk_pct: Number,
    round_trip: RoundTrip,
    rules: SymbolRules | None = None,
) -> PositionSize:
    """Stop mesafesine ve bütçeye göre pozisyon büyüklüğünü hesaplar.

    ``round_trip`` stop'a giden turun maliyetidir (çıkış piyasa emri); stop
    zararı komisyon dahil hesaplanır, çünkü kullanıcının cebinden çıkan tutar
    fiyat farkı değil, komisyonla birlikte olandır.
    """
    entry = to_decimal(entry)
    stop = to_decimal(stop)
    budget = to_decimal(budget_usdt)
    risk_pct = to_decimal(risk_pct)

    warnings: list[str] = []
    target_risk = budget * risk_pct / ONE_HUNDRED
    distance = entry - stop

    if entry <= ZERO:
        raise ValueError("Giriş fiyatı pozitif olmalı")
    if distance <= ZERO:
        raise ValueError("Stop, giriş fiyatının altında olmalı")

    risk_qty = target_risk / distance
    budget_qty = budget / entry
    raw_qty = min(risk_qty, budget_qty)
    binding = Binding.BUDGET if budget_qty <= risk_qty else Binding.RISK

    quantity = (
        rules.round_quantity(raw_qty)
        if rules is not None
        # Filtre yoksa adım uydurulmaz; yalnızca Binance'in kabul ettiği en
        # yüksek hassasiyete inilir ve aşağı yuvarlanır (hedeflenen
        # büyüklük asla aşılmaz).
        else clamp_decimals(raw_qty, mode=Rounding.FLOOR)
    )
    minimum_valid = (
        rules.min_quantity_for_notional(entry) if rules is not None else ZERO
    )

    valid = quantity > ZERO
    if not valid:
        warnings.append(
            "Yuvarlamadan sonra miktar sıfır kaldı; bu bütçe bu sembol için "
            "çok küçük."
        )

    if rules is not None:
        violations = rules.validate_limit_order(
            side=Side.BUY, price=entry, quantity=quantity
        )
        for violation in violations:
            warnings.append(violation.message)
        if violations:
            valid = False
        if minimum_valid > ZERO and quantity < minimum_valid:
            needed = (minimum_valid * entry).quantize(Decimal("0.01"))
            warnings.append(
                f"En küçük geçerli emir {minimum_valid} adet, yani yaklaşık "
                f"{needed} USDT. Bu, bütçenin "
                f"%{(needed / budget * ONE_HUNDRED) if budget > ZERO else 0:.1f}'i."
            )

    loss = -round_trip.net_pnl_quote(entry, stop, quantity)
    if loss < ZERO:
        loss = ZERO

    if valid and target_risk > ZERO and loss > target_risk:
        warnings.append(
            f"Komisyon dahil gerçekleşecek zarar ({loss.quantize(Decimal('0.01'))} "
            f"USDT), hedeflenen riskin ({target_risk.quantize(Decimal('0.01'))} "
            "USDT) üstünde. Fark komisyondan geliyor."
        )

    return PositionSize(
        butce_usdt=budget,
        hedeflenen_risk_usdt=target_risk,
        giris=entry,
        stop=stop,
        risk_miktari=risk_qty,
        butce_miktari=budget_qty,
        ham_miktar=raw_qty,
        miktar=quantity,
        baglayici=binding if valid else Binding.BUDGET,
        tutar_usdt=quantity * entry,
        stop_zarari_usdt=loss,
        gecerli=valid,
        uyarilar=tuple(warnings),
        asgari_gecerli_miktar=minimum_valid,
    )


__all__ = ["BINDING_LABELS_TR", "Binding", "PositionSize", "size_position"]
