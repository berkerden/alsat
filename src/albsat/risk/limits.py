"""Risk limitleri: varsayılanlar, geçerli aralıklar ve kalıcı ayar.

SPEC.md §4.6: "tüm limitler arayüzden ayarlanabilir, varsayılanlar
temkinli". Varsayılanlar ``config/default.yaml``'daki değerlerle aynıdır;
aynı kaldıklarını ``test_risk_motoru.py`` dosyayı okuyarak doğrular.

Her limitin bir **geçerli aralığı** vardır. Arayüzden gelen değer aralığın
dışındaysa reddedilir, sessizce kırpılmaz: "günlük zarar sınırı %300"
yazmış biri yazım hatası yapmıştır ve bunu duymalıdır.

Kullanıcının değiştirdiği değerler SQLite'taki ``ayarlar`` tablosunda durur.
Her değişiklik denetim kaydına eski ve yeni değeriyle yazılır (çağıran
tarafından).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields, replace
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from albsat.core import db
from albsat.core.clock import iso, utc_now

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ayarlar (
    anahtar            TEXT PRIMARY KEY,
    deger              TEXT NOT NULL,
    guncelleme_utc     TEXT NOT NULL
);
"""

SETTINGS_KEY = "risk_limitleri"


@dataclass(frozen=True)
class LimitSpec:
    """Bir limitin arayüzde nasıl gösterileceği ve hangi aralıkta geçerli olduğu."""

    ad: str
    etiket: str
    birim: str
    alt: Decimal
    ust: Decimal
    aciklama: str
    tam_sayi: bool = False


@dataclass(frozen=True)
class RiskLimits:
    """Risk motorunun tüm ayarları. Parasal/oransal değerler ``Decimal``."""

    # --- bütçe ve boyut (SPEC §4.6) --------------------------------------
    butce_usdt: Decimal = Decimal("100")
    islem_basi_risk_yuzde: Decimal = Decimal("1.0")
    # --- zarar sınırları --------------------------------------------------
    gunluk_max_zarar_yuzde: Decimal = Decimal("3.0")
    haftalik_max_dusus_yuzde: Decimal = Decimal("6.0")
    aylik_max_dusus_yuzde: Decimal = Decimal("10.0")
    art_arda_kayip_limiti: int = 4
    kayip_sonrasi_soguma_mum: int = 2
    # --- maruziyet --------------------------------------------------------
    max_es_zamanli_pozisyon: int = 1
    coin_basi_max_maruziyet_yuzde: Decimal = Decimal("100")
    gunluk_max_islem: int = 20
    # --- piyasa koşulu filtreleri -----------------------------------------
    #: Son kapanmış 1 dakikalık mum bundan eskiyse veri bayat sayılır.
    bayat_veri_azami_saniye: int = 180
    #: Güncel ATR%, son dönemin medyan ATR%'sinin bu katını aşarsa "aşırı oynak".
    volatilite_kati: Decimal = Decimal("3")
    #: Güncel spread, gözlenen medyan spread'in bu katını aşarsa işlem açılmaz.
    spread_kati: Decimal = Decimal("5")
    #: Medyan ne olursa olsun spread bu yüzdeyi aşarsa işlem açılmaz.
    spread_azami_yuzde: Decimal = Decimal("0.10")
    #: Son 24 saatlik işlem hacmi (USDT) bunun altındaysa "düşük likidite".
    min_24s_hacim_usdt: Decimal = Decimal("5000000")
    #: BTC son 60 dakikada bu yüzdeden fazla hareket ettiyse yeni işlem yok.
    btc_sert_hareket_yuzde: Decimal = Decimal("3.0")
    # --- performans bozulma koruması --------------------------------------
    #: Bir kural en az bu kadar kâğıt işlem yaptıktan sonra sınanır.
    performans_min_islem: int = 10
    #: Gerçekleşen ortalama, beklenenin bu kadar standart hata altındaysa
    #: kural devre dışı kalır (2,33 ≈ tek yönlü %1).
    performans_z_esigi: Decimal = Decimal("2.33")

    def to_json(self) -> dict[str, Any]:
        return {
            key: (str(value) if isinstance(value, Decimal) else value)
            for key, value in asdict(self).items()
        }

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> RiskLimits:
        """Kayıtlı ayarı okur. Bilinmeyen anahtar yok sayılır, eksik varsayılan kalır."""
        defaults = cls()
        values: dict[str, Any] = {}
        for item in fields(cls):
            if item.name not in payload:
                continue
            raw = payload[item.name]
            current = getattr(defaults, item.name)
            try:
                values[item.name] = (
                    int(raw) if isinstance(current, int) else Decimal(str(raw))
                )
            except (TypeError, ValueError, InvalidOperation):
                continue
        return replace(defaults, **values)


def _d(text: str) -> Decimal:
    return Decimal(text)


#: Arayüzde gösterilen sıra ve geçerli aralıklar.
SPECS: tuple[LimitSpec, ...] = (
    LimitSpec("butce_usdt", "Bot bütçesi", "USDT", _d("10"), _d("100000"),
              "Bot yalnızca bu tutarı kullanır; hesabın kalanına dokunmaz."),
    LimitSpec("islem_basi_risk_yuzde", "İşlem başına risk", "% bütçe", _d("0.1"), _d("2"),
              "Stop'a gidilirse kaybedilecek tutar, bütçenin bu yüzdesini aşmaz. "
              "Şartname %0.5–1 öneriyor."),
    LimitSpec("gunluk_max_zarar_yuzde", "Günlük azami zarar", "% bütçe", _d("0.5"), _d("20"),
              "Aşılınca kâğıt işlem kapanır; ertesi gün ancak elle açılırsa devam eder."),
    LimitSpec("haftalik_max_dusus_yuzde", "Haftalık azami düşüş", "% bütçe", _d("1"), _d("50"),
              "Bu hafta ulaşılan en yüksek bakiyeden düşüş."),
    LimitSpec("aylik_max_dusus_yuzde", "Aylık azami düşüş", "% bütçe", _d("1"), _d("80"),
              "Bu ay ulaşılan en yüksek bakiyeden düşüş."),
    LimitSpec("art_arda_kayip_limiti", "Art arda kayıp sınırı", "işlem", _d("1"), _d("20"),
              "Bu kadar işlem üst üste zararla kapanırsa kâğıt işlem durur.", tam_sayi=True),
    LimitSpec("kayip_sonrasi_soguma_mum", "Kayıp sonrası soğuma", "mum", _d("0"), _d("50"),
              "Zararla kapanan işlemden sonra o coinde bu kadar mum yeni işlem yok.",
              tam_sayi=True),
    LimitSpec("max_es_zamanli_pozisyon", "Aynı anda en fazla pozisyon", "adet", _d("1"),
              _d("10"), "Açık pozisyon ve bekleyen giriş emirleri birlikte sayılır.",
              tam_sayi=True),
    LimitSpec("coin_basi_max_maruziyet_yuzde", "Coin başına azami maruziyet", "% bütçe",
              _d("5"), _d("100"), "Tek bir coine bağlanabilecek en yüksek tutar."),
    LimitSpec("gunluk_max_islem", "Günlük azami işlem", "adet", _d("1"), _d("500"),
              "Bir günde açılabilecek en fazla giriş emri.", tam_sayi=True),
    LimitSpec("bayat_veri_azami_saniye", "Bayat veri eşiği", "saniye", _d("60"), _d("3600"),
              "Son fiyat bundan eskiyse yeni işlem açılmaz.", tam_sayi=True),
    LimitSpec("volatilite_kati", "Aşırı oynaklık eşiği", "kat", _d("1.5"), _d("20"),
              "Güncel ATR, son dönemin medyanının bu katını aşarsa yeni işlem yok."),
    LimitSpec("spread_kati", "Spread eşiği", "kat", _d("1.5"), _d("50"),
              "Spread, gözlenen normal değerin bu katını aşarsa yeni işlem yok."),
    LimitSpec("spread_azami_yuzde", "Spread üst sınırı", "%", _d("0.01"), _d("2"),
              "Spread bu yüzdeyi aşarsa, normali ne olursa olsun yeni işlem yok."),
    LimitSpec("min_24s_hacim_usdt", "Asgari 24 saatlik hacim", "USDT", _d("0"),
              _d("100000000000"), "Bunun altındaki hacimde likidite düşük sayılır."),
    LimitSpec("btc_sert_hareket_yuzde", "BTC sert hareket eşiği", "% / 60 dk", _d("0.5"),
              _d("20"), "BTC son bir saatte bundan fazla oynadıysa yeni işlem yok."),
    LimitSpec("performans_min_islem", "Performans sınaması için asgari işlem", "işlem",
              _d("5"), _d("200"), "Bir kural bu kadar işlemden sonra beklentiyle kıyaslanır.",
              tam_sayi=True),
    LimitSpec("performans_z_esigi", "Performans bozulma eşiği", "standart hata", _d("1.64"),
              _d("5"), "Gerçekleşen, beklenenin bu kadar altındaysa kural durdurulur. "
              "2.33 yaklaşık %1 yanılma payı demek."),
)

SPEC_BY_NAME = {item.ad: item for item in SPECS}


class LimitError(ValueError):
    """Geçersiz limit değeri; mesaj kullanıcıya gösterilir."""


def validate(changes: dict[str, Any], current: RiskLimits) -> RiskLimits:
    """Değişiklikleri doğrular ve yeni limitleri döndürür.

    Aralık dışındaki değer **reddedilir**, kırpılmaz.
    """
    values: dict[str, Any] = {}
    for name, raw in changes.items():
        spec = SPEC_BY_NAME.get(name)
        if spec is None:
            raise LimitError(f"Bilinmeyen ayar: {name}")
        text = str(raw).strip().replace(",", ".")
        try:
            number = Decimal(text)
        except InvalidOperation:
            raise LimitError(f"{spec.etiket}: '{raw}' bir sayı değil.") from None
        if not number.is_finite():
            raise LimitError(f"{spec.etiket}: geçersiz değer.")
        if spec.tam_sayi and number != number.to_integral_value():
            raise LimitError(f"{spec.etiket} tam sayı olmalı.")
        if number < spec.alt or number > spec.ust:
            raise LimitError(
                f"{spec.etiket} {spec.alt} ile {spec.ust} {spec.birim} arasında olmalı; "
                f"girilen: {text}."
            )
        values[name] = int(number) if spec.tam_sayi else number
    return replace(current, **values)


class LimitStore:
    """Kullanıcının değiştirdiği limitleri SQLite'ta tutar."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        db.ensure_schema(self.path, _SCHEMA)

    @classmethod
    def in_directory(cls, root: Path | str) -> LimitStore:
        return cls(db.path_in(root))

    def read(self) -> RiskLimits:
        with db.session(self.path) as connection:
            row = connection.execute(
                "SELECT deger FROM ayarlar WHERE anahtar = ?", (SETTINGS_KEY,)
            ).fetchone()
        if row is None:
            return RiskLimits()
        try:
            payload = json.loads(row["deger"])
        except json.JSONDecodeError:
            return RiskLimits()
        return RiskLimits.from_json(payload if isinstance(payload, dict) else {})

    def write(self, limits: RiskLimits) -> None:
        with db.session(self.path) as connection:
            connection.execute(
                "INSERT INTO ayarlar (anahtar, deger, guncelleme_utc) VALUES (?,?,?) "
                "ON CONFLICT(anahtar) DO UPDATE SET deger = excluded.deger, "
                "guncelleme_utc = excluded.guncelleme_utc",
                (SETTINGS_KEY, json.dumps(limits.to_json()), iso(utc_now())),
            )

    def differences(self, old: RiskLimits, new: RiskLimits) -> dict[str, tuple[str, str]]:
        """Denetim kaydı için: hangi ayar neyden neye değişti."""
        before, after = old.to_json(), new.to_json()
        return {
            key: (str(before[key]), str(after[key]))
            for key in after
            if before.get(key) != after[key]
        }


__all__ = [
    "SPECS",
    "SPEC_BY_NAME",
    "LimitError",
    "LimitSpec",
    "LimitStore",
    "RiskLimits",
    "validate",
]
