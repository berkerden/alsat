"""Kâğıt işlem uçları (Faz 4; SPEC.md §4.6, §8.9, §8.10).

Kâğıt işlemin durum değiştiren uçları burada ve ``/api/kagit/`` altında.
Hepsi **kâğıt hesap** üzerinde çalışır: Binance'e emir gönderen ya da API
anahtarına erişen tek satır yok. Tek istisna ACİL DURDUR: kâğıt işlemle
birlikte Demo yürütücüsünü de durdurur (yürütücü üzerinden; anahtara bu
modül dokunmaz). Demo Mode uçları ``demo_api.py``'de.

Durum değiştiren her uç ``POST``'tur ve ``app.py``'deki yerel koruma
katmanından geçer (Host başlığı, özel istek başlığı, JSON gövde, Origin).
Okuyan uçlar ``GET``'tir ve yan etkisizdir.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from albsat.api.runtime import Runtime
from albsat.api.serialize import money, percent, position
from albsat.core.audit import SOURCE_UI, AuditEntry
from albsat.core.clock import from_ms, istanbul_text, utc_now
from albsat.core.fees import Side
from albsat.modes.state import LOCKED, MODE_LABELS_TR, MODE_PAPER, SELECTABLE, ModeError
from albsat.notify.base import KIND_LIMIT, Notice
from albsat.paper import report
from albsat.paper.engine import AccountView, OrderMeta, time_left_text
from albsat.paper.fills import EXIT_LABELS_TR
from albsat.paper.ledger import STATUS_OPEN, STATUS_PENDING, PaperOrder
from albsat.risk import engine as risk
from albsat.risk.limits import SPECS, LimitError, RiskLimits, validate
from albsat.risk.market import Gate, market_gates

#: Hesap sıfırlamada kullanıcının yazması gereken sözcük.
RESET_WORD = "SIFIRLA"

NOT_RUNNING = (
    "Kâğıt işlem bu çalıştırmada kapalı. Arayüzü 'bash kurulum.sh arayuz' ile açın."
)
OFFLINE_PAPER = (
    "Canlı piyasa verisi bu çalıştırmada kapalı (çevrimdışı açıldı). Kâğıt işlem canlı "
    "fiyat olmadan çalışamaz; arayüzü çevrimdışı seçeneği olmadan yeniden açın."
)


# --- istek gövdeleri -----------------------------------------------------------


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModeBody(_Body):
    sembol: str = Field(min_length=3, max_length=20)
    mod: str = Field(min_length=2, max_length=20)


class LimitsBody(_Body):
    degerler: dict[str, str | int | float]


class OrderBody(_Body):
    sembol: str = Field(min_length=3, max_length=20)
    periyot: str = Field(min_length=2, max_length=4)
    giris: str = Field(min_length=1, max_length=40)
    hedef: str = Field(min_length=1, max_length=40)
    stop: str = Field(min_length=1, max_length=40)
    #: Giriş emri kaç mum (emrin periyodunda) bekler.
    gecerlilik_mum: int = Field(1, ge=1, le=8)
    #: Pozisyon en fazla kaç mum tutulur; sonra piyasa fiyatından kapanır.
    azami_tutma_mum: int = Field(4, ge=1, le=24)


class OrderIdBody(_Body):
    id: int = Field(ge=1)


class KillBody(_Body):
    pozisyonlari_kapat: bool = False


class RuleBody(_Body):
    kural: str = Field(min_length=1, max_length=200)


class ResetBody(_Body):
    onay: str = Field(max_length=20)


# --- JSON karşılıkları -----------------------------------------------------------

CENT = Decimal("0.01")
#: Kâr/zarar ve risk tutarları: 100 USDT bütçede kuruşun altı da önemli.
PNL = Decimal("0.0001")


def usdt(value: Decimal | str | None, places: Decimal = CENT) -> str | None:
    """USDT tutarı, sabit basamakla ("0.10", "-0.7547"); fiyatlar bununla yazılmaz."""
    if value is None or value == "":
        return None
    return format(Decimal(value).quantize(places), "f")


def _source_tr(kaynak: str) -> str:
    return "Kural" if kaynak == risk.SOURCE_RULE else "Elle"


def order_json(order: PaperOrder, *, mark: Decimal | None, now: datetime) -> dict[str, Any]:
    """Kâğıt emrin arayüz karşılığı. Parasal değerler metin olarak kalır."""
    data: dict[str, Any] = {
        "id": order.id,
        "sembol": order.sembol,
        "periyot": order.periyot,
        "kaynak": order.kaynak,
        "kaynak_tr": _source_tr(order.kaynak),
        "kural_kimligi": order.kural_kimligi,
        "kural_etiketi": order.kural_etiketi,
        "durum": order.durum,
        "durum_tr": order.durum_tr,
        "olusturma": istanbul_text(order.olusturma_utc),
        "giris": order.giris,
        "hedef": order.hedef,
        "stop": order.stop,
        "miktar": order.miktar,
        "tutar_usdt": usdt(order.tutar_usdt),
        "stop_zarari_usdt": usdt(order.stop_zarari_usdt, PNL),
        "gecerlilik_bitis": istanbul_text(from_ms(order.gecerlilik_bitis_ms)),
        "dolum": istanbul_text(order.dolum_utc) if order.dolum_utc else None,
        "tutma_bitis": (
            istanbul_text(from_ms(order.dolum_ms + order.azami_tutma_ms))
            if order.dolum_ms else None
        ),
        "satilacak": order.satilacak,
        "cikis": istanbul_text(order.cikis_utc) if order.cikis_utc else None,
        "cikis_fiyati": order.cikis_fiyati,
        "cikis_sebebi_tr": EXIT_LABELS_TR.get(order.cikis_sebebi or "", order.cikis_sebebi),
        "net_usdt": usdt(order.net_usdt, PNL),
        "net_yuzde": percent(order.net_yuzde, 3),
        "beklenen_hedef_net_yuzde": percent(order.beklenen_hedef_net_yuzde, 3),
        "iptal_sebebi": order.iptal_sebebi,
        "notlar": order.not_listesi,
        "anlik_fiyat": None,
        "anlik_yuzde": None,
        "kalan": None,
    }
    entry = order.dec("giris")
    if mark is not None and entry > 0 and order.durum in (STATUS_OPEN, STATUS_PENDING):
        data["anlik_fiyat"] = money(mark)
        data["anlik_yuzde"] = percent((mark / entry - 1) * 100, 3)
    if order.durum == STATUS_PENDING:
        data["kalan"] = time_left_text(order.gecerlilik_bitis_ms, now)
    elif order.durum == STATUS_OPEN and order.dolum_ms:
        data["kalan"] = time_left_text(order.dolum_ms + order.azami_tutma_ms, now)
    return data


def summary_json(item: report.Summary) -> dict[str, Any]:
    return {
        "kaynak": item.kaynak,
        "kaynak_tr": _source_tr(item.kaynak),
        "islem": item.islem,
        "kazanan": item.kazanan,
        "isabet_orani": percent(item.isabet_orani, 4),
        "net_usdt": usdt(item.net_usdt, PNL),
        "ortalama_net_yuzde": percent(item.ortalama_net_yuzde, 4),
        "kar_faktoru": percent(item.kar_faktoru, 2),
        "en_buyuk_dusus_usdt": usdt(item.en_buyuk_dusus_usdt, PNL),
        "toplam_komisyon_usdt": usdt(item.toplam_komisyon_usdt, PNL),
        "komisyon_brut_kar_orani": percent(item.komisyon_brut_kar_orani, 4),
        "ortalama_tutma_dk": percent(item.ortalama_tutma_dk, 1),
        "beklenen_ortalama_yuzde": percent(item.beklenen_ortalama_yuzde, 4),
        "sapma_yuzde": percent(item.sapma_yuzde, 4),
        "cikis_sebepleri": item.cikis_sebepleri,
    }


def account_json(view: AccountView, marks: dict[str, Decimal]) -> dict[str, Any]:
    return {
        "donem_id": view.donem_id,
        "baslangic_usdt": usdt(view.baslangic_usdt),
        "baslangic": istanbul_text(view.baslangic_utc),
        "nakit_usdt": usdt(view.nakit_usdt),
        "kilitli_usdt": usdt(view.kilitli_usdt),
        "serbest_usdt": usdt(view.serbest_usdt),
        "coinler": [
            {
                "sembol": sembol,
                "miktar": money(amount),
                "fiyat": money(marks.get(sembol)),
                "deger_usdt": None if sembol not in marks else usdt(amount * marks[sembol]),
            }
            for sembol, amount in sorted(view.coinler.items())
        ],
        "ozsermaye_usdt": usdt(view.ozsermaye_usdt),
        "getiri_yuzde": percent(view.getiri_yuzde, 3),
        "eksik_fiyat": list(view.eksik_fiyat),
    }


def gate_json(item: Gate) -> dict[str, Any]:
    return {
        "ad": item.ad,
        "etiket": item.etiket,
        "gecti": item.gecti,
        "olculemedi": item.olculemedi,
        "aciklama": item.aciklama,
    }


def decision_json(item: risk.RiskDecision) -> dict[str, Any]:
    return {
        "izin": item.izin,
        "ozet": item.ozet_tr,
        "kapilar": [gate_json(gate) for gate in item.kapilar],
        "pozisyon": None if item.pozisyon is None else position(item.pozisyon),
        "hedef_net_yuzde": percent(item.hedef_net_yuzde, 4),
    }


def meters_json(metrics: risk.RiskMetrics, limits: RiskLimits) -> dict[str, Any]:
    def meter(ad: str, etiket: str, deger: Decimal | int, sinir: Decimal | int,
              birim: str) -> dict[str, Any]:
        value, bound = float(deger), float(sinir)
        return {
            "ad": ad, "etiket": etiket, "deger": round(value, 3), "sinir": round(bound, 3),
            "birim": birim, "oran": round(value / bound, 4) if bound > 0 else None,
        }

    return {
        "gunluk_net_usdt": usdt(metrics.gunluk_net_usdt, PNL),
        "gunluk_kalan_usdt": usdt(metrics.gunluk_kalan_usdt),
        "gun_sonu": istanbul_text(metrics.gun_sonu_utc),
        "gostergeler": [
            meter("gunluk_zarar", "Bugünkü zarar", metrics.gunluk_zarar_yuzde,
                  limits.gunluk_max_zarar_yuzde, "% bütçe"),
            meter("haftalik_dusus", "Bu haftaki düşüş", metrics.haftalik_dusus_yuzde,
                  limits.haftalik_max_dusus_yuzde, "% bütçe"),
            meter("aylik_dusus", "Bu ayki düşüş", metrics.aylik_dusus_yuzde,
                  limits.aylik_max_dusus_yuzde, "% bütçe"),
            meter("art_arda_kayip", "Art arda kayıp", metrics.art_arda_kayip,
                  limits.art_arda_kayip_limiti, "işlem"),
            meter("islem_bugun", "Bugünkü giriş emri", metrics.girisler_bugun,
                  limits.gunluk_max_islem, "adet"),
            meter("acik_pozisyon", "Açık pozisyon + bekleyen", metrics.acik_pozisyon,
                  limits.max_es_zamanli_pozisyon, "adet"),
        ],
    }


def limits_json(limits: RiskLimits) -> list[dict[str, Any]]:
    defaults = RiskLimits()
    return [
        {
            "ad": spec.ad,
            "etiket": spec.etiket,
            "birim": spec.birim,
            "alt": money(spec.alt),
            "ust": money(spec.ust),
            "aciklama": spec.aciklama,
            "tam_sayi": spec.tam_sayi,
            "deger": money(Decimal(getattr(limits, spec.ad))),
            "varsayilan": money(Decimal(getattr(defaults, spec.ad))),
        }
        for spec in SPECS
    ]


def notice_json(item: Notice) -> dict[str, Any]:
    return {
        "zaman": istanbul_text(item.zaman_utc),
        "tur": item.tur,
        "metin": item.metin,
        "gonderildi": item.gonderildi,
    }


def audit_json(item: AuditEntry) -> dict[str, Any]:
    return {
        "id": item.id,
        "zaman": item.zaman_istanbul,
        "tur": item.tur,
        "kaynak": item.kaynak,
        "kaynak_tr": item.kaynak_tr,
        "ozet": item.ozet,
        "ayrinti": item.ayrinti,
    }


# --- yardımcılar -----------------------------------------------------------------


def _decimal(text: str, label: str) -> Decimal:
    cleaned = text.strip().replace(" ", "").replace(",", ".")
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        raise HTTPException(400, f"{label}: '{text}' bir sayı değil.") from None
    if not value.is_finite() or value <= 0:
        raise HTTPException(400, f"{label} sıfırdan büyük bir sayı olmalı.")
    return value


def _require(runtime: Runtime | None) -> Runtime:
    if runtime is None:
        raise HTTPException(503, NOT_RUNNING)
    return runtime


def _symbol(runtime: Runtime, sembol: str) -> str:
    symbol = sembol.strip().upper()
    if symbol not in runtime.symbols:
        raise HTTPException(
            400, f"{symbol} izlenen coinler arasında değil ({', '.join(runtime.symbols)})."
        )
    return symbol


def _period(runtime: Runtime, periyot: str) -> str:
    if periyot not in runtime.periods:
        raise HTTPException(
            400, f"Periyot {', '.join(runtime.periods)} olmalı; gelen: {periyot}."
        )
    return periyot


def _manual_intent(
    runtime: Runtime, *, sembol: str, periyot: str, giris: str, hedef: str, stop: str
) -> tuple[risk.OrderIntent, list[str]]:
    """Elle girilen fiyatları borsanın adımına yuvarlar; notları döner."""
    symbol = _symbol(runtime, sembol)
    period = _period(runtime, periyot)
    prices = {
        "giris": _decimal(giris, "Giriş"),
        "hedef": _decimal(hedef, "Hedef"),
        "stop": _decimal(stop, "Stop"),
    }
    notes: list[str] = []
    rules = runtime.engine.rules_for(symbol)
    if rules is not None:
        # Alış limiti aşağı (fazla ödememek için), satış tarafı yukarı yuvarlanır.
        sides = {"giris": Side.BUY, "hedef": Side.SELL, "stop": Side.SELL}
        labels = {"giris": "Giriş", "hedef": "Hedef", "stop": "Stop"}
        for key, value in list(prices.items()):
            rounded = rules.round_price(value, sides[key])
            if rounded != value:
                notes.append(
                    f"{labels[key]} {money(value)} → {money(rounded)} (borsanın fiyat adımı "
                    f"{money(rules.price.tick_size) if rules.price else '?'})."
                )
                prices[key] = rounded
    intent = risk.OrderIntent(
        sembol=symbol,
        periyot=period,
        giris=prices["giris"],
        hedef=prices["hedef"],
        stop=prices["stop"],
        kaynak=risk.SOURCE_MANUAL,
    )
    return intent, notes


def _intent_prices(intent: risk.OrderIntent) -> dict[str, str | None]:
    return {"giris": money(intent.giris), "hedef": money(intent.hedef),
            "stop": money(intent.stop)}


# --- uçlar ----------------------------------------------------------------------------


def register(app: FastAPI, get_runtime: Callable[[], Runtime | None]) -> None:
    """Kâğıt işlem uçlarını uygulamaya ekler."""

    def rt() -> Runtime:
        return _require(get_runtime())

    def modes_json(runtime: Runtime) -> list[dict[str, Any]]:
        return [
            {
                "sembol": item.sembol,
                "mod": item.mod,
                "mod_tr": item.mod_tr,
                "onceki_oturum": item.onceki_oturum,
                "onceki_oturum_tr": MODE_LABELS_TR.get(item.onceki_oturum or "", None),
            }
            for item in runtime.engine.modes.all()
        ]

    # --- okuma ------------------------------------------------------------------

    @app.get("/api/kagit/durum")
    def kagit_durum() -> dict[str, Any]:
        runtime = rt()
        engine = runtime.engine
        now = utc_now()
        marks = runtime.marks()
        limits = engine.limits
        period_id, _, _ = engine.period()
        orders = engine.ledger.in_period(period_id)
        active = [
            order_json(item, mark=marks.get(item.sembol), now=now)
            for item in engine.ledger.active()
        ]
        disabled = set(engine.disabled_rules())
        checks = [
            {
                "kural": item.kural_kimligi,
                "islem": item.islem,
                "beklenen_yuzde": percent(item.beklenen_yuzde, 4),
                "gerceklesen_yuzde": percent(item.gerceklesen_yuzde, 4),
                "z": None if item.z is None or item.z != item.z or abs(item.z) == float("inf")
                else round(item.z, 2),
                "bozuk": item.bozuk,
                "durduruldu": item.kural_kimligi in disabled,
                "aciklama": item.aciklama,
            }
            for item in engine.rule_checks(now)
        ]
        return {
            "canli": runtime.runner is not None,
            "zaman": istanbul_text(now),
            "modlar": modes_json(runtime),
            "secilebilir_modlar": [
                {"mod": mode, "etiket": MODE_LABELS_TR[mode]} for mode in SELECTABLE
            ],
            "kilitli_modlar": [
                {"mod": mode, "etiket": MODE_LABELS_TR[mode], "ne_zaman": when}
                for mode, when in LOCKED.items()
            ],
            "demo_hazir_degil": (
                "Demo bağlantısı bu çalıştırmada kurulmadı." if runtime.demo is None
                else runtime.demo.not_ready_reason()
            ),
            "hesap": account_json(engine.account(marks), marks),
            "risk": meters_json(risk.metrics(engine.snapshot(now), limits, now), limits),
            "aktif": active,
            "ozet": {
                "kural": summary_json(report.summarize(orders, risk.SOURCE_RULE)),
                "elle": summary_json(report.summarize(orders, risk.SOURCE_MANUAL)),
            },
            "durdurulan_kurallar": sorted(disabled),
            "kural_performansi": checks,
            "canliya_gecis": [
                {
                    "kural": item.kural_kimligi,
                    "etiket": item.kural_etiketi,
                    "gun": round(item.gun, 1),
                    "islem": item.islem,
                    "gecti": item.gecti,
                    "aciklama": item.aciklama,
                }
                for item in report.live_gates(engine.ledger.all_closed(), now)
            ],
            "maliyet": {
                symbol: engine.costs_for(symbol).kaynak_tr for symbol in runtime.symbols
            },
            "telegram": runtime.telegram_status(),
            "butce_notu": (
                "Bot bütçesini değiştirmek kâğıt hesabın başlangıç tutarını hemen "
                "değiştirmez; yeni tutar hesap sıfırlanınca geçerli olur."
            ),
        }

    @app.get("/api/kagit/islemler")
    def kagit_islemler(adet: int = Query(100, ge=1, le=1000)) -> dict[str, Any]:
        runtime = rt()
        now = utc_now()
        marks = runtime.marks()
        return {
            "islemler": [
                order_json(item, mark=marks.get(item.sembol), now=now)
                for item in runtime.engine.ledger.recent(adet)
            ]
        }

    @app.get("/api/kagit/islemler.csv")
    def kagit_csv() -> Response:
        runtime = rt()
        text = report.to_csv(runtime.engine.ledger.all_closed())
        name = f"albsat-kagit-islemler-{utc_now():%Y%m%d}.csv"
        return Response(
            content=text.encode("utf-8"),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    @app.get("/api/kagit/limitler")
    def kagit_limitler() -> dict[str, Any]:
        runtime = rt()
        return {"limitler": limits_json(runtime.engine.limits)}

    @app.get("/api/kagit/piyasa")
    def kagit_piyasa(periyot: str | None = Query(None, max_length=4)) -> dict[str, Any]:
        runtime = rt()
        period = _period(runtime, periyot) if periyot else runtime.periods[0]
        now = utc_now()
        limits = runtime.engine.limits
        coins = []
        for symbol in runtime.symbols:
            state = runtime.market.market_state(symbol, period,
                                                runtime.engine.rules_for(symbol))
            quote = runtime.market.quote(symbol)
            coins.append({
                "sembol": symbol,
                "periyot": period,
                "son_fiyat": money(state.son_fiyat),
                "alis": None if quote is None else money(quote.alis),
                "satis": None if quote is None else money(quote.satis),
                "spread_yuzde": percent(state.spread_yuzde, 6),
                "spread_medyan_yuzde": percent(state.spread_medyan_yuzde, 6),
                "spread_gozlem": runtime.market.spread_samples(symbol),
                "atr_yuzde": percent(state.atr_yuzde, 3),
                "atr_medyan_yuzde": percent(state.atr_medyan_yuzde, 3),
                "hacim_24s_usdt": None if state.hacim_24s_usdt is None
                else money(state.hacim_24s_usdt.quantize(Decimal(1))),
                "btc_60dk_yuzde": percent(state.btc_60dk_degisim_yuzde, 2),
                "son_veri": istanbul_text(state.son_veri_utc) if state.son_veri_utc else None,
                "kapilar": [gate_json(item) for item in market_gates(state, limits, now)],
                "notlar": list(state.notlar),
            })
        return {
            "canli": runtime.runner is not None,
            "baglanti": None if runtime.runner is None else runtime.runner.status(),
            "coinler": coins,
            "usdttry": money(runtime.market.usdttry()),
        }

    @app.get("/api/kagit/on-izleme")
    def kagit_on_izleme(
        sembol: str = Query(..., min_length=3, max_length=20),
        periyot: str = Query(..., min_length=2, max_length=4),
        giris: str = Query(..., max_length=40),
        hedef: str = Query(..., max_length=40),
        stop: str = Query(..., max_length=40),
    ) -> dict[str, Any]:
        """Elle emrin risk kapılarından geçip geçmeyeceği. Hiçbir şey kaydetmez."""
        runtime = rt()
        intent, notes = _manual_intent(runtime, sembol=sembol, periyot=periyot,
                                       giris=giris, hedef=hedef, stop=stop)
        quote = runtime.market.quote(intent.sembol)
        decision = runtime.engine.evaluate(
            intent,
            market=runtime.market.market_state(intent.sembol, intent.periyot,
                                               runtime.engine.rules_for(intent.sembol)),
            now=utc_now(),
            best_ask=None if quote is None else quote.satis,
        )
        return {"karar": decision_json(decision), "fiyatlar": _intent_prices(intent),
                "yuvarlama": notes}

    @app.get("/api/bildirimler")
    def bildirimler(adet: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
        runtime = rt()
        return {
            "telegram": runtime.telegram_status(),
            "bildirimler": [notice_json(item) for item in runtime.notifier.recent(adet)],
        }

    @app.get("/api/denetim")
    def denetim(adet: int = Query(100, ge=1, le=1000)) -> dict[str, Any]:
        runtime = rt()
        return {"kayitlar": [audit_json(item) for item in runtime.engine.audit.recent(adet)]}

    # --- durum değiştiren (POST) --------------------------------------------------

    @app.post("/api/kagit/mod")
    def kagit_mod(body: ModeBody) -> dict[str, Any]:
        runtime = rt()
        symbol = _symbol(runtime, body.sembol)
        if body.mod == MODE_PAPER and runtime.runner is None:
            raise HTTPException(409, OFFLINE_PAPER)
        try:
            old, new = runtime.engine.set_mode(symbol, body.mod, source=SOURCE_UI,
                                               now=utc_now())
        except ModeError as error:
            raise HTTPException(400, str(error)) from None
        return {"eski": old, "yeni": new, "modlar": modes_json(runtime)}

    @app.post("/api/kagit/limitler")
    def kagit_limitler_yaz(body: LimitsBody) -> dict[str, Any]:
        runtime = rt()
        engine = runtime.engine
        with engine.lock:
            current = engine.limits
            try:
                updated = validate(dict(body.degerler), current)
            except LimitError as error:
                raise HTTPException(400, str(error)) from None
            changes = engine.limit_store.differences(current, updated)
            if changes:
                engine.limit_store.write(updated)
                labels = {spec.ad: spec.etiket for spec in SPECS}
                text = "; ".join(
                    f"{labels.get(key, key)}: {before} → {after}"
                    for key, (before, after) in changes.items()
                )
                engine.audit.write(
                    "limit", f"Risk limitleri değişti: {text}", kaynak=SOURCE_UI,
                    ayrinti={key: f"{before} → {after}" for key, (before, after)
                             in changes.items()},
                )
                engine.notifier.send(f"⚙️ RİSK LİMİTİ DEĞİŞTİ\n{text}", kind=KIND_LIMIT)
        return {"degisen": {key: list(value) for key, value in changes.items()},
                "limitler": limits_json(updated)}

    @app.post("/api/kagit/emir")
    def kagit_emir(body: OrderBody) -> dict[str, Any]:
        runtime = rt()
        if runtime.runner is None:
            raise HTTPException(409, OFFLINE_PAPER)
        intent, notes = _manual_intent(runtime, sembol=body.sembol, periyot=body.periyot,
                                       giris=body.giris, hedef=body.hedef, stop=body.stop)
        quote = runtime.market.quote(intent.sembol)
        decision, order = runtime.engine.place(
            intent,
            market=runtime.market.market_state(intent.sembol, intent.periyot,
                                               runtime.engine.rules_for(intent.sembol)),
            now=utc_now(),
            meta=OrderMeta(
                kural_etiketi="Elle emir",
                gecerlilik_mum=body.gecerlilik_mum,
                azami_tutma_mum=body.azami_tutma_mum,
                notlar=tuple(notes),
            ),
            source=SOURCE_UI,
            best_ask=None if quote is None else quote.satis,
        )
        return {
            "karar": decision_json(decision),
            "fiyatlar": _intent_prices(intent),
            "yuvarlama": notes,
            "emir": None if order is None
            else order_json(order, mark=runtime.marks().get(order.sembol), now=utc_now()),
        }

    @app.post("/api/kagit/iptal")
    def kagit_iptal(body: OrderIdBody) -> dict[str, Any]:
        runtime = rt()
        try:
            order = runtime.engine.cancel(body.id, source=SOURCE_UI)
        except ValueError as error:
            raise HTTPException(409, str(error)) from None
        return {"emir": order_json(order, mark=None, now=utc_now())}

    @app.post("/api/kagit/kapat")
    def kagit_kapat(body: OrderIdBody) -> dict[str, Any]:
        runtime = rt()
        order = runtime.engine.ledger.get(body.id)
        mark = None if order is None else runtime.marks().get(order.sembol)
        try:
            closed = runtime.engine.close(body.id, mark=mark, source=SOURCE_UI, now=utc_now())
        except ValueError as error:
            raise HTTPException(409, str(error)) from None
        return {"emir": order_json(closed, mark=None, now=utc_now())}

    @app.post("/api/kagit/acil-durdur")
    def kagit_acil_durdur(body: KillBody) -> dict[str, Any]:
        runtime = rt()
        result = runtime.kill_switch(close_positions=body.pozisyonlari_kapat, source=SOURCE_UI)
        return {**result, "modlar": modes_json(runtime)}

    @app.post("/api/kagit/art-arda-sifirla")
    def kagit_art_arda_sifirla() -> dict[str, Any]:
        runtime = rt()
        runtime.engine.reset_streak(source=SOURCE_UI, now=utc_now())
        return {"tamam": True}

    @app.post("/api/kagit/kural-ac")
    def kagit_kural_ac(body: RuleBody) -> dict[str, Any]:
        runtime = rt()
        if body.kural not in runtime.engine.disabled_rules():
            raise HTTPException(409, "Bu kural durdurulmuş değil.")
        runtime.engine.enable_rule(body.kural, source=SOURCE_UI, now=utc_now())
        return {"durdurulan_kurallar": runtime.engine.disabled_rules()}

    @app.post("/api/kagit/hesap-sifirla")
    def kagit_hesap_sifirla(body: ResetBody) -> dict[str, Any]:
        runtime = rt()
        if body.onay.strip().upper() != RESET_WORD:
            raise HTTPException(400, f"Onay için kutuya {RESET_WORD} yazın.")
        try:
            period_id = runtime.engine.reset_account(source=SOURCE_UI, now=utc_now())
        except ValueError as error:
            raise HTTPException(409, str(error)) from None
        return {"donem_id": period_id}


__all__ = [
    "RESET_WORD",
    "account_json",
    "decision_json",
    "order_json",
    "register",
    "summary_json",
]
