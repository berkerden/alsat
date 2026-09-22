"""Disiplinli alım planı — B ekinin üçüncü parçası.

Faz 2 bu kapsamda alınacak bir giriş sinyali bulamadı; ölçülen her bölümde en
iyi sonucu al-ve-tut verdi. Bu, "al ve tut" tavsiyesi değildir — bu veri
kümesinde ölçülen bir sonuçtur. Ama şunu söyler: eğer kullanıcı yine de alım
yapacaksa, kararı **önceden yazılmış bir plana** bağlamak, her gün fiyata
bakıp içinden geldiği gibi almaktan disiplinlidir.

Bu modül o planı üretir ve **hiçbir emir göndermez**. Çıktısı, kullanıcının
kendi eliyle uygulayacağı bir listedir: hangi tarihte ya da hangi fiyattan,
ne kadarlık, kaç adet, ne kadar komisyonla.

İki kip:

* **Dönemsel** — bütçe eşit dilimlere bölünür, her ``n`` günde bir alınır.
  Fiyat tahmini içermez; tek varsayımı "düzenli aralıklarla al".
* **Kademeli** — bütçe, güncel fiyatın altındaki basamaklara yayılır. Fiyat
  düşerse daha fazla alınır. Fiyat hiç düşmezse alt basamaklar **hiç
  dolmaz**; plan bunu baştan yazar, çünkü bu kipin asıl riski budur.

Her iki kipte de ``stepSize`` yuvarlaması ve ``minNotional`` kontrol edilir:
küçük bütçeyi çok dilime bölmek, dilimlerin borsanın kabul etmeyeceği kadar
küçük kalmasına yol açar ve bu sessizce olmaz.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import numpy as np

from albsat.core.fees import Liquidity, Side, flat_table
from albsat.core.filters import SymbolRules
from albsat.core.money import (
    ONE_HUNDRED,
    ZERO,
    Number,
    Rounding,
    clamp_decimals,
    to_decimal,
)
from albsat.data.klines import closed_only, to_utc
from albsat.data.store import KlineStore
from albsat.strategy.rules import CostAssumptions

MODE_PERIODIC = "donemsel"
MODE_LADDER = "kademeli"


@dataclass(frozen=True)
class PlanStep:
    """Planın tek bir dilimi."""

    sira: int
    #: Dönemsel kipte tarih (ISO), kademeli kipte boş.
    tarih_utc: str
    #: Kademeli kipte limit fiyatı, dönemsel kipte güncel fiyat (tahmini).
    fiyat: Decimal
    tutar_usdt: Decimal
    miktar: Decimal
    gerceklesen_tutar_usdt: Decimal
    komisyon_usdt: Decimal
    gecerli: bool
    not_tr: str = ""


@dataclass(frozen=True)
class HistoricalIllustration:
    """Aynı planın geçmiş veride ne yapmış olacağı.

    Bir vaat değil, bir ölçüm: "bu plan, elimizdeki veride şöyle işlerdi".
    Parametre arayışı yok, tek bir plan tek bir veri üzerinde çalıştırılıyor;
    bu yüzden Faz 2'nin uğraştığı çoklu test sorunu burada doğmaz.
    """

    baslangic_utc: str
    bitis_utc: str
    alim_sayisi: int
    ortalama_maliyet: Decimal
    toplam_miktar: Decimal
    yatirilan_usdt: Decimal
    son_deger_usdt: Decimal
    net_yuzde: float
    toplam_komisyon_usdt: Decimal
    #: Aynı parayı en başta tek seferde harcamış olsaydık.
    tek_seferde_net_yuzde: float
    #: Plan süresince görülen en düşük ara değer (yüzde, negatif).
    en_kotu_ara_deger_yuzde: float


@dataclass(frozen=True)
class AccumulationPlan:
    """Disiplinli alım planının tamamı."""

    sembol: str
    kip: str
    butce_usdt: Decimal
    dilim_sayisi: int
    guncel_fiyat: Decimal
    adimlar: tuple[PlanStep, ...]
    toplam_tutar_usdt: Decimal
    toplam_komisyon_usdt: Decimal
    komisyon_orani_yuzde: Decimal
    uyarilar: tuple[str, ...]
    filtreler_uygulandi: bool
    gecmis: HistoricalIllustration | None = None
    not_tr: str = (
        "Bu bir plandır, emir değildir. Uygulama hiçbir emir göndermez; "
        "listedeki alımları kendiniz yaparsınız. Geçmiş performans geleceği "
        "garanti etmez."
    )

    @property
    def gecerli_adim_sayisi(self) -> int:
        return sum(1 for step in self.adimlar if step.gecerli)


def build_plan(
    *,
    sembol: str,
    butce_usdt: Number,
    dilim_sayisi: int,
    maliyet: CostAssumptions,
    veri_dizini: Path | str,
    periyot: str = "1h",
    kip: str = MODE_PERIODIC,
    aralik_gun: int = 7,
    basamak_yuzde: Number = "2",
    rules: SymbolRules | None = None,
    gecmis_gun: int = 180,
) -> AccumulationPlan:
    """Disiplinli alım planını üretir."""
    if dilim_sayisi < 1:
        raise ValueError("Dilim sayısı en az 1 olmalı")
    if kip not in (MODE_PERIODIC, MODE_LADDER):
        raise ValueError(f"Bilinmeyen kip: {kip}")

    budget = to_decimal(butce_usdt)
    if budget <= ZERO:
        raise ValueError("Bütçe pozitif olmalı")

    store = KlineStore(veri_dizini)
    frame = store.read(sembol, periyot)
    if frame.empty:
        raise ValueError(f"{sembol} {periyot} için kayıtlı mum yok")
    frame = closed_only(frame).reset_index(drop=True)

    price = to_decimal(f"{float(frame['close'].iloc[-1]):.10f}")
    last_time = to_utc(int(frame["open_time"].iloc[-1]))

    # Alım piyasa emriyse taker, limitle beklenirse maker. Plan limit emir
    # varsayar (acele yok, disiplinli alımın tanımı bu), ama dolmama riski
    # uyarı olarak yazılır.
    table = flat_table(sembol, maliyet.maker_orani, maliyet.taker_orani)
    fee_rate = table.effective_rate(Side.BUY, Liquidity.MAKER)

    warnings: list[str] = []
    if rules is None:
        warnings.append(
            "Sembol filtreleri elde yok; miktarlar borsanın adımına "
            "yuvarlanmadı ve en düşük emir tutarı kontrol edilmedi."
        )

    slice_amount = (budget / dilim_sayisi).quantize(Decimal("0.01"))
    steps: list[PlanStep] = []

    for index in range(dilim_sayisi):
        if kip == MODE_PERIODIC:
            when = (last_time + timedelta(days=aralik_gun * index)).replace(
                microsecond=0
            )
            step_price = price
            date_text = when.isoformat()
            note = "Fiyat bugünkü fiyattır; o gün ne olacağı bilinmiyor."
        else:
            discount = to_decimal(basamak_yuzde) * index
            step_price = price * (ONE_HUNDRED - discount) / ONE_HUNDRED
            step_price = (
                rules.round_price(step_price, Side.BUY)
                if rules is not None
                else clamp_decimals(step_price, mode=Rounding.FLOOR)
            )
            date_text = ""
            note = (
                "Güncel fiyat"
                if index == 0
                else f"Güncel fiyatın %{discount:.2f} altında; fiyat buraya "
                "inmezse bu dilim hiç alınmaz."
            )

        quantity = slice_amount / step_price if step_price > ZERO else ZERO
        quantity = (
            rules.round_quantity(quantity)
            if rules is not None
            else clamp_decimals(quantity, mode=Rounding.FLOOR)
        )

        actual = quantity * step_price
        valid = quantity > ZERO
        if rules is not None and valid:
            violations = rules.validate_limit_order(
                side=Side.BUY, price=step_price, quantity=quantity
            )
            if violations:
                valid = False
                note = violations[0].message

        steps.append(
            PlanStep(
                sira=index + 1,
                tarih_utc=date_text,
                fiyat=step_price,
                tutar_usdt=slice_amount,
                miktar=quantity,
                gerceklesen_tutar_usdt=actual,
                komisyon_usdt=(actual * fee_rate).quantize(Decimal("0.00000001")),
                gecerli=valid,
                not_tr=note,
            )
        )

    invalid = [step for step in steps if not step.gecerli]
    if invalid:
        warnings.append(
            f"{len(invalid)} dilim borsanın en düşük emir kurallarını "
            "sağlamıyor. Dilim sayısını azaltın veya bütçeyi artırın."
        )
    if kip == MODE_LADDER:
        warnings.append(
            "Kademeli alımda fiyat hiç düşmezse alt basamaklar dolmaz ve "
            "bütçenin bir kısmı kullanılmadan kalır."
        )
    warnings.append(
        f"Plan limit (maker) emir varsayar, komisyon %{maliyet.maker_yuzde}. "
        "Beklemeyip piyasa emriyle alırsanız komisyon taker oranına çıkar "
        f"(%{maliyet.taker_yuzde})."
    )

    total = sum((step.gerceklesen_tutar_usdt for step in steps if step.gecerli), ZERO)
    total_fee = sum((step.komisyon_usdt for step in steps if step.gecerli), ZERO)

    historical = None
    if kip == MODE_PERIODIC:
        historical = _illustrate(
            frame,
            periyot=periyot,
            slice_amount=slice_amount,
            count=dilim_sayisi,
            interval_days=aralik_gun,
            fee_rate=fee_rate,
            lookback_days=gecmis_gun,
            rules=rules,
        )

    return AccumulationPlan(
        sembol=sembol,
        kip=kip,
        butce_usdt=budget,
        dilim_sayisi=dilim_sayisi,
        guncel_fiyat=price,
        adimlar=tuple(steps),
        toplam_tutar_usdt=total,
        toplam_komisyon_usdt=total_fee,
        komisyon_orani_yuzde=(total_fee / total * ONE_HUNDRED) if total > ZERO else ZERO,
        uyarilar=tuple(dict.fromkeys(warnings)),
        filtreler_uygulandi=rules is not None,
        gecmis=historical,
    )


def _illustrate(
    frame,
    *,
    periyot: str,
    slice_amount: Decimal,
    count: int,
    interval_days: int,
    fee_rate: Decimal,
    lookback_days: int,
    rules: SymbolRules | None,
) -> HistoricalIllustration | None:
    """Aynı dönemsel planı geçmiş veride bir kez çalıştırır."""
    from albsat.data.klines import candles_per_day

    per_day = candles_per_day(periyot)
    if per_day <= 0 or len(frame) < 2:
        return None

    step_bars = max(1, int(round(per_day * interval_days)))
    needed = step_bars * (count - 1) + 1
    window_bars = min(len(frame), max(needed, int(round(per_day * lookback_days))))
    window = frame.tail(window_bars).reset_index(drop=True)
    if len(window) < needed:
        return None

    close = window["close"].to_numpy(dtype=float)
    indices = [min(len(close) - 1, step_bars * i) for i in range(count)]

    quantity = ZERO
    invested = ZERO
    fees = ZERO
    equity: list[float] = []
    for position, index in enumerate(indices):
        price = to_decimal(f"{float(close[index]):.10f}")
        amount = slice_amount
        bought = amount / price if price > ZERO else ZERO
        bought = (
            rules.round_quantity(bought)
            if rules is not None
            else clamp_decimals(bought, mode=Rounding.FLOOR)
        )
        spent = bought * price
        fee = spent * fee_rate
        quantity += bought
        invested += spent
        fees += fee
        # Bu alımdan sonraki ara değerler
        stop = indices[position + 1] if position + 1 < len(indices) else len(close)
        for point in range(index, stop):
            value = quantity * to_decimal(f"{float(close[point]):.10f}")
            if invested > ZERO:
                equity.append(float((value - invested - fees) / invested * ONE_HUNDRED))

    if invested <= ZERO or quantity <= ZERO:
        return None

    # Değerleme penceredeki son kapanışla yapılır: son alımdan sonraki
    # fiyat hareketi de plana dahildir.
    final_price = to_decimal(f"{float(close[-1]):.10f}")
    final_value = quantity * final_price
    net_pct = float((final_value - invested - fees) / invested * ONE_HUNDRED)

    first_price = to_decimal(f"{float(close[indices[0]]):.10f}")
    lump_qty = invested / first_price if first_price > ZERO else ZERO
    lump_fee = invested * fee_rate
    lump_net = (
        float((lump_qty * final_price - invested - lump_fee) / invested * ONE_HUNDRED)
        if invested > ZERO
        else 0.0
    )

    return HistoricalIllustration(
        baslangic_utc=to_utc(int(window["open_time"].iloc[indices[0]])).isoformat(),
        bitis_utc=to_utc(int(window["open_time"].iloc[-1])).isoformat(),
        alim_sayisi=len(indices),
        ortalama_maliyet=(
            clamp_decimals(invested / quantity) if quantity > ZERO else ZERO
        ),
        toplam_miktar=quantity,
        yatirilan_usdt=invested,
        son_deger_usdt=final_value,
        net_yuzde=net_pct,
        toplam_komisyon_usdt=fees,
        tek_seferde_net_yuzde=lump_net,
        en_kotu_ara_deger_yuzde=float(np.min(equity)) if equity else 0.0,
    )


__all__ = [
    "MODE_LADDER",
    "MODE_PERIODIC",
    "AccumulationPlan",
    "HistoricalIllustration",
    "PlanStep",
    "build_plan",
]
