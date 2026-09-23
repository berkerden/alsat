"""Kâğıt emirlerinin dolum kuralları (SPEC.md §4.7 ve §4.8).

Şartname kâğıt işlem için "komisyon, kayma ve limit emir dolum mantığı
dahil gerçekçi simülasyon" istiyor ve limit emirde **"fiyat dokundu" değil
"fiyat içinden geçti"** varsayımını şart koşuyor. Buradaki kurallar bunun
açık hâlidir; her biri temkinli taraftadır, yani belirsizlik varsa sonuç
aleyhimize seçilir:

1. **Giriş (``LIMIT_MAKER`` alış)** yalnızca bir 1 dakikalık mumun en düşüğü
   giriş fiyatının **altına indiyse** dolar (eşit olması yetmez: o fiyatta
   sırada önümüzde başkaları olabilir). Dolum fiyatı giriş fiyatıdır,
   komisyon maker'dır.
2. Emir, verildiği andan **sonra açılan** ilk mumdan itibaren geçerlidir.
   Verildiği dakikanın mumu sayılmaz: o mumun en düşüğü emirden önce
   gelmiş olabilir.
3. **Stop (``STOP_LOSS``)** mumun en düşüğü stop fiyatına değince tetiklenir
   ve piyasa emriyle satar: dolum ``min(stop, mumun açılışı)`` fiyatından
   kayma kadar aşağıdadır. Fiyat stopun altında açıldıysa (boşluk) dolum
   stoptan değil açılıştan olur. Komisyon taker'dır.
4. **Hedef (``LIMIT_MAKER`` satış)** mumun en yükseği hedefin **üstüne
   çıktıysa** dolar, hedef fiyatından, maker komisyonla.
5. Aynı mumda hem stop hem hedef görülürse **stop** kabul edilir: mum
   verisi hangisinin önce geldiğini söylemez.
6. Girişin dolduğu mumda stop da görüldüyse pozisyon aynı mumda stopla
   kapanır; hedef ise o mumda sayılmaz (dolumdan sonra mı geldi bilinmez).
7. Komisyon alınan varlıktan düşülür (BNB ile ödeme kapalı, varsayılan).
   Alışta eldeki coin ``miktar × (1 − komisyon)`` olur ve satış emri
   ``stepSize``'ın katı olmak zorundadır; bu yüzden satılabilen miktar
   **aşağı** yuvarlanır, artan küsurat ("toz") hesapta coin olarak kalır.
8. Gerçek hesapta satış emri serbest bakiyeye göre verilir; önceki
   işlemlerden kalan toz da o bakiyenin içindedir. Kâğıt işlem de böyle
   yapar: satılabilir miktar ``alınan + önceki toz``'un aşağı
   yuvarlanmışıdır. Böylece toz birikmez, en fazla bir ``stepSize`` kadar
   kalır.
9. İşlemin net sonucu, satış geliri ile alış tutarının farkına, işlemin
   toz bakiyesinde yaptığı değişikliğin **çıkış fiyatından** değerini ekler.
   Tozu hiç saymamak her işlemi bir adım (BTC'de ~0,6 USDT) zararlı
   gösterirdi; o coin kaybolmuyor, sonraki satışta satılıyor. Bu kuralla
   işlem sonuçlarının toplamı hesabın değer değişimine eşit kalır.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from albsat.core.fees import CommissionTable, Liquidity, Side
from albsat.core.filters import SymbolRules
from albsat.core.money import ONE_HUNDRED, ZERO, Rounding, clamp_decimals, round_to_increment

EXIT_TARGET = "hedef"
EXIT_STOP = "stop"
EXIT_TIME = "sure"
EXIT_KILL = "acil_durdur"
EXIT_MANUAL = "elle_kapatma"

EXIT_LABELS_TR = {
    EXIT_TARGET: "hedefe ulaştı",
    EXIT_STOP: "stop oldu",
    EXIT_TIME: "süre doldu",
    EXIT_KILL: "acil durdurmada kapatıldı",
    EXIT_MANUAL: "elle kapatıldı",
}

#: Çıkışın emir tipi ve likiditesi.
EXIT_LIQUIDITY = {
    EXIT_TARGET: Liquidity.MAKER,
    EXIT_STOP: Liquidity.TAKER,
    EXIT_TIME: Liquidity.TAKER,
    EXIT_KILL: Liquidity.TAKER,
    EXIT_MANUAL: Liquidity.TAKER,
}


@dataclass(frozen=True)
class Candle:
    """Kapanmış bir mum; fiyatlar borsanın metninden ``Decimal``."""

    open_time_ms: int
    close_time_ms: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal

    @classmethod
    def from_rest(cls, row: list[object], interval_ms: int) -> Candle:
        """``GET /api/v3/klines`` satırından (fiyatlar metin gelir)."""
        open_time = int(str(row[0]))
        return cls(
            open_time_ms=open_time,
            close_time_ms=open_time + interval_ms,
            open=Decimal(str(row[1])),
            high=Decimal(str(row[2])),
            low=Decimal(str(row[3])),
            close=Decimal(str(row[4])),
        )


@dataclass(frozen=True)
class PaperCosts:
    """Kâğıt işlemin maliyet varsayımları ve nereden geldikleri."""

    komisyon: CommissionTable
    kayma_yuzde: Decimal
    kaynak_tr: str

    def rate(self, side: Side, liquidity: Liquidity) -> Decimal:
        return self.komisyon.effective_rate(side, liquidity)


@dataclass(frozen=True)
class EntryFill:
    fiyat: Decimal
    miktar: Decimal
    maliyet_usdt: Decimal
    komisyon_coin: Decimal
    alinan: Decimal
    #: Önceki işlemlerden kalan ve bu satışa eklenen toz.
    onceki_toz: Decimal
    satilacak: Decimal
    #: Bu işlemden sonra hesapta kalacak toz.
    toz: Decimal

    @property
    def toz_degisimi(self) -> Decimal:
        """Bu işlemin toz bakiyesine etkisi (eksi: önceki tozdan kullandı)."""
        return self.toz - self.onceki_toz


@dataclass(frozen=True)
class ExitFill:
    sebep: str
    fiyat: Decimal
    miktar: Decimal
    gelir_usdt: Decimal
    komisyon_usdt: Decimal


def _floor_price(value: Decimal, rules: SymbolRules | None) -> Decimal:
    """Satışta aleyhe yuvarlama: aşağı, borsanın fiyat adımına."""
    if rules is not None and rules.price is not None:
        return round_to_increment(
            value, rules.price.tick_size, Rounding.FLOOR, base=rules.price.min_price
        )
    return clamp_decimals(value, mode=Rounding.FLOOR)


def entry_fills(candle: Candle, *, price: Decimal, active_from_ms: int) -> bool:
    """Kural 1-2: mum emirden sonra açıldı ve fiyat girişin altına indi mi?"""
    return candle.open_time_ms >= active_from_ms and candle.low < price


def fill_entry(
    *,
    price: Decimal,
    quantity: Decimal,
    costs: PaperCosts,
    rules: SymbolRules | None,
    carried_dust: Decimal = ZERO,
) -> EntryFill:
    """Kural 7-8: maker komisyonu coinden düşülür, önceki toz eklenir,
    satılabilir miktar aşağı yuvarlanır."""
    rate = costs.rate(Side.BUY, Liquidity.MAKER)
    fee = quantity * rate
    received = quantity - fee
    carried = max(carried_dust, ZERO)
    available = received + carried
    sellable = rules.round_quantity(available) if rules is not None else available
    if sellable < ZERO:
        sellable = ZERO
    return EntryFill(
        fiyat=price,
        miktar=quantity,
        maliyet_usdt=price * quantity,
        komisyon_coin=fee,
        alinan=received,
        onceki_toz=carried,
        satilacak=sellable,
        toz=available - sellable,
    )


def exit_trigger(
    candle: Candle,
    *,
    stop: Decimal,
    target: Decimal,
    slippage_pct: Decimal,
    rules: SymbolRules | None,
    allow_target: bool = True,
) -> tuple[str, Decimal] | None:
    """Kural 3-6: bu mumda pozisyon kapanıyor mu, hangi fiyattan?"""
    if candle.low <= stop:
        base = min(stop, candle.open)
        fill = _floor_price(base * (ONE_HUNDRED - slippage_pct) / ONE_HUNDRED, rules)
        return EXIT_STOP, fill
    if allow_target and candle.high > target:
        return EXIT_TARGET, target
    return None


def market_exit_price(
    reference: Decimal, *, slippage_pct: Decimal, rules: SymbolRules | None
) -> Decimal:
    """Piyasa emriyle satış: referans fiyattan kayma kadar aşağıda."""
    return _floor_price(reference * (ONE_HUNDRED - slippage_pct) / ONE_HUNDRED, rules)


def fill_exit(
    *, reason: str, price: Decimal, quantity: Decimal, costs: PaperCosts
) -> ExitFill:
    rate = costs.rate(Side.SELL, EXIT_LIQUIDITY[reason])
    gross = price * quantity
    fee = gross * rate
    return ExitFill(
        sebep=reason,
        fiyat=price,
        miktar=quantity,
        gelir_usdt=gross - fee,
        komisyon_usdt=fee,
    )


def net_result(
    *, cost_usdt: Decimal, exit_: ExitFill, dust_change: Decimal
) -> tuple[Decimal, Decimal]:
    """Kural 9: (net USDT, net yüzde). Toz değişimi çıkış fiyatından değerlenir."""
    net = exit_.gelir_usdt + dust_change * exit_.fiyat - cost_usdt
    pct = net / cost_usdt * ONE_HUNDRED if cost_usdt > ZERO else ZERO
    return net, pct


__all__ = [
    "EXIT_KILL",
    "EXIT_LABELS_TR",
    "EXIT_LIQUIDITY",
    "EXIT_MANUAL",
    "EXIT_STOP",
    "EXIT_TARGET",
    "EXIT_TIME",
    "Candle",
    "EntryFill",
    "ExitFill",
    "PaperCosts",
    "entry_fills",
    "exit_trigger",
    "fill_entry",
    "fill_exit",
    "market_exit_price",
    "net_result",
]
