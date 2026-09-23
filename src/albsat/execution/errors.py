"""Borsa hatalarının sınıflandırılması ve Türkçe açıklaması (``errors.md``).

Yürütücü bir hatayla karşılaşınca üç soru sorar: emir borsada var mı
(belirsiz mi)? Fiyatı değiştirip yeniden denemek anlamlı mı? Kullanıcıya ne
söylenmeli? Bu modül her hatayı bir kategoriye indirger ve bu soruların
cevabını verir.
"""

from __future__ import annotations

from dataclasses import dataclass

from albsat.exchange.ratelimit import BudgetExceeded
from albsat.exchange.trading import ExchangeError, OutcomeUnknown, TradingRefused

ERR_WOULD_MATCH = "hemen_eslesir"
ERR_WOULD_TRIGGER = "hemen_tetiklenir"
ERR_BALANCE = "bakiye"
ERR_FILTER = "filtre"
ERR_OCO_PRICES = "fiyat_sirasi"
ERR_DUPLICATE = "kimlik_tekrari"
ERR_UNKNOWN_ORDER = "emir_yok"
ERR_TIMESTAMP = "saat"
ERR_KEY = "anahtar"
ERR_RATE = "hiz_siniri"
ERR_ORDER_RATE = "emir_siniri"
ERR_BAN = "ip_engeli"
ERR_UNKNOWN = "belirsiz"
ERR_NETWORK = "ag"
ERR_MARKET = "piyasa_kapali"
ERR_BUDGET = "yerel_tavan"
ERR_REFUSED = "kural_disi"
ERR_OTHER = "diger"


@dataclass(frozen=True)
class ErrorInfo:
    kategori: str
    mesaj_tr: str
    #: Emir borsada olabilir; kimliğiyle sorgulanmadan hiçbir şey yapılmaz.
    belirsiz: bool = False
    #: Giriş fiyatını en iyi alışa çekip yeniden denemek anlamlı (limit-maker).
    yeniden_fiyatla: bool = False
    #: Kısa bir bekleyişten sonra aynen yeniden denenebilir.
    bekle_dene: bool = False
    kod: int | None = None


_MESSAGES = {
    "Order would immediately match and take.": ERR_WOULD_MATCH,
    "Order would trigger immediately.": ERR_WOULD_TRIGGER,
    "Account has insufficient balance for requested action.": ERR_BALANCE,
    "The relationship of the prices for the orders is not correct.": ERR_OCO_PRICES,
    "Duplicate order sent.": ERR_DUPLICATE,
    "Market is closed.": ERR_MARKET,
}


def classify(error: BaseException) -> ErrorInfo:
    if isinstance(error, OutcomeUnknown):
        return ErrorInfo(
            ERR_UNKNOWN,
            "Borsadan yanıt alınamadı; emir iletilmiş de olabilir, iletilmemiş de. "
            "Emir kimliğiyle sorgulanıyor, yeniden gönderilmeyecek.",
            belirsiz=True,
        )
    if isinstance(error, TradingRefused):
        return ErrorInfo(ERR_REFUSED, f"İstek gönderilmedi: {error}")
    if isinstance(error, BudgetExceeded):
        return ErrorInfo(ERR_BUDGET, f"İstek gönderilmedi: {error}", bekle_dene=True)
    if not isinstance(error, ExchangeError):
        return ErrorInfo(ERR_OTHER, f"{type(error).__name__}: {error}"[:300])
    code = error.code
    msg = error.msg or ""
    if error.status == 0:
        return ErrorInfo(ERR_NETWORK, f"Borsaya ulaşılamadı: {msg}"[:300], bekle_dene=True)
    if error.status == 418:
        return ErrorInfo(ERR_BAN, "Binance bu IP'yi geçici olarak engelledi (418). Engel "
                         "bitene kadar hiç istek gönderilmeyecek.", kod=code)
    if error.status == 429 or code == -1003:
        return ErrorInfo(ERR_RATE, "Binance istek hızı sınırı (429); bekleniyor.",
                         bekle_dene=True, kod=code)
    if code == -1015:
        return ErrorInfo(ERR_ORDER_RATE, "Binance emir sayısı sınırı doldu (-1015). Yeni "
                         "giriş emri gönderilmiyor; koruma emirleri sınır açılınca "
                         "yeniden denenecek.", bekle_dene=True, kod=code)
    if code == -1021:
        return ErrorInfo(ERR_TIMESTAMP, "Bilgisayarın saati Binance'ten çok farklı (-1021). "
                         "macOS'ta 'Saati otomatik ayarla' açık olmalı.", bekle_dene=True,
                         kod=code)
    if code in (-1022, -2014, -2015):
        return ErrorInfo(ERR_KEY, (f"Demo anahtarı reddedildi ({code}): {msg}. Anahtarı "
                                   "yeniden kurun: bash kurulum.sh demo-anahtar")[:300],
                         kod=code)
    if code == -1013:
        return ErrorInfo(ERR_FILTER, f"Borsa filtresi emri reddetti: {msg}"[:300], kod=code)
    if code in (-2011, -2013):
        return ErrorInfo(ERR_UNKNOWN_ORDER, "Borsa bu emri tanımıyor (dolmuş, iptal edilmiş ya "
                         "da hiç ulaşmamış olabilir).", kod=code)
    if code in (-1006, -1007):
        return ErrorInfo(ERR_UNKNOWN, "Borsa isteğin sonucunu bildiremedi; emir kimliğiyle "
                         "sorgulanıyor.", belirsiz=True, kod=code)
    if code == -2010 or code is None or -1199 <= code <= -1100:
        category = _MESSAGES.get(msg.strip())
        if category == ERR_WOULD_MATCH:
            return ErrorInfo(category, "Giriş fiyatı Demo defterindeki en iyi satışa eşit ya da "
                             "üstünde; limit-maker emir hemen eşleşeceği için reddedildi.",
                             yeniden_fiyatla=True, kod=code)
        if category == ERR_WOULD_TRIGGER:
            return ErrorInfo(category, "Stop fiyatı güncel fiyatın üstünde kaldı; stop hemen "
                             "tetikleneceği için reddedildi.", kod=code)
        if category == ERR_BALANCE:
            return ErrorInfo(category, "Demo hesabında yeterli bakiye yok.", kod=code)
        if category == ERR_OCO_PRICES:
            return ErrorInfo(category, "Hedef/stop fiyat sırası borsanın kuralına uymuyor "
                             "(satışta hedef > güncel fiyat > stop olmalı).", kod=code)
        if category == ERR_DUPLICATE:
            return ErrorInfo(category, "Bu emir kimliği borsada zaten var; aynı emir ikinci "
                             "kez gönderilmedi. Durumu sorgulanıyor.", belirsiz=True, kod=code)
        if category == ERR_MARKET:
            return ErrorInfo(category, "Bu sembolde işlem şu an kapalı.", kod=code)
    return ErrorInfo(ERR_OTHER, f"Borsa reddetti ({code}): {msg}"[:300], kod=code)


__all__ = [
    "ERR_BALANCE",
    "ERR_BAN",
    "ERR_BUDGET",
    "ERR_DUPLICATE",
    "ERR_FILTER",
    "ERR_KEY",
    "ERR_MARKET",
    "ERR_NETWORK",
    "ERR_OCO_PRICES",
    "ERR_ORDER_RATE",
    "ERR_OTHER",
    "ERR_RATE",
    "ERR_REFUSED",
    "ERR_TIMESTAMP",
    "ERR_UNKNOWN",
    "ERR_UNKNOWN_ORDER",
    "ERR_WOULD_MATCH",
    "ERR_WOULD_TRIGGER",
    "ErrorInfo",
    "classify",
]
