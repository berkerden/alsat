"""Demo Mode uçları (Faz 5; SPEC.md §4.5, §4.6, §10 Faz 5).

Bu modül yalnızca ``DemoExecutor`` ile konuşur. İmzalı istemciye, anahtara
ve Anahtar Zinciri'ne dokunmaz (``tests/test_kagit_arayuz.py``'deki yalıtım
testi bunu denetler). Emir gönderen, iptal eden ve kapatan uçlar ``POST``'tur
ve ``app.py``'deki yerel koruma katmanından geçer.

Demo Mode'da emirler **Binance Demo Mode hesabına** gider: sahte para,
gerçek emir defteri benzeri. Gerçek para riski yoktur; yine de her emir
aynı risk kapılarından ve Demo'ya özel kapılardan geçer.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response
from pydantic import Field

from albsat.api.paper_api import (
    RESET_WORD,
    OrderBody,
    OrderIdBody,
    ResetBody,
    _Body,
    _intent_prices,
    _manual_intent,
    decision_json,
    meters_json,
    usdt,
)
from albsat.api.runtime import Runtime
from albsat.api.serialize import money, percent
from albsat.core.audit import SOURCE_UI
from albsat.core.clock import istanbul_text, utc_now
from albsat.execution.executor import DemoAccountView, DemoExecutor, OrderExecutor
from albsat.execution.planner import Plan
from albsat.execution.settings import STOP_TYPES_TR, SettingsError
from albsat.paper.engine import OrderMeta
from albsat.risk import engine as risk

NOT_RUNNING = "Demo Mode bu çalıştırmada kapalı. Arayüzü 'bash kurulum.sh arayuz' ile açın."
OFFLINE = (
    "Canlı piyasa verisi bu çalıştırmada kapalı (çevrimdışı açıldı). Demo emri risk "
    "kapıları için canlı veriye ihtiyaç duyar; arayüzü çevrimdışı seçeneği olmadan açın."
)


class SettingsBody(_Body):
    degerler: dict[str, str | int | float]


class ConfirmBody(_Body):
    onay: bool = Field(False)


def _executor(runtime: Runtime) -> DemoExecutor:
    if runtime.demo is None:
        raise HTTPException(503, NOT_RUNNING)
    return runtime.demo


def _need_trader(executor: DemoExecutor) -> None:
    if executor.trader is None:
        raise HTTPException(409, executor.not_ready_reason() or NOT_RUNNING)


def account_json(view: DemoAccountView, marks: dict[str, Decimal]) -> dict[str, Any]:
    return {
        "donem_id": view.donem_id,
        "baslangic_usdt": usdt(view.baslangic_usdt),
        "baslangic": istanbul_text(view.baslangic_utc),
        "nakit_usdt": usdt(view.nakit_usdt),
        "kilitli_usdt": usdt(view.kilitli_usdt),
        "serbest_usdt": usdt(view.serbest_usdt),
        "borsa_serbest_usdt": usdt(view.borsa_serbest_usdt),
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


def plan_json(plan: Plan | None) -> dict[str, Any] | None:
    """Borsaya gidecek emirlerin okunur hâli (önizleme)."""
    if plan is None:
        return None
    return {
        "gecerli": plan.gecerli,
        "sorunlar": list(plan.sorunlar),
        "satis_miktari": format(plan.satis_miktari, "f"),
        "emirler": [
            {
                "rol": {"giris": "Giriş", "hedef": "Hedef", "stop": "Stop",
                        "cikis": "Çıkış"}.get(str(leg["rol"]), str(leg["rol"])),
                "tur": leg["tur"],
                "taraf": "ALIŞ" if leg["taraf"] == "BUY" else "SATIŞ",
                "fiyat": leg["fiyat"],
                "stop_fiyati": leg["stop_fiyati"],
                "miktar": leg["miktar"],
            }
            for leg in plan.legs
        ],
    }


def book_json(executor: DemoExecutor, runtime: Runtime, now: datetime) -> list[dict[str, Any]]:
    """Demo defterinin ve canlı piyasanın en iyi fiyatları, yan yana.

    Yalnızca akıştan gelen son fiyat okunur; arayüzün yenilemesi Binance'e
    istek göndermez."""
    rows = []
    for symbol in runtime.symbols:
        demo = executor.demo_market.quote(symbol)
        live = runtime.market.quote(symbol)
        rows.append({
            "sembol": symbol,
            "alis": None if demo is None else money(demo.alis),
            "satis": None if demo is None else money(demo.satis),
            "yas_sn": None if demo is None else round((now - demo.zaman).total_seconds(), 1),
            "canli_alis": None if live is None else money(live.alis),
            "canli_satis": None if live is None else money(live.satis),
        })
    return rows


def settings_json(executor: OrderExecutor) -> dict[str, Any]:
    current = executor.settings
    return {
        "degerler": current.as_dict(),
        "alanlar": [
            {"ad": "stop_tipi", "etiket": "Stop tipi", "tur": "secim",
             "secenekler": [{"deger": key, "aciklama": text}
                            for key, text in STOP_TYPES_TR.items()]},
            {"ad": "stop_limit_ofset_yuzde", "etiket": "Limitli stopta limit farkı (%)",
             "tur": "sayi", "aralik": "0.05 – 5",
             "aciklama": "Yalnızca limitli stopta: limit fiyatı stopun bu kadar altında."},
            {"ad": "azami_kayma_yuzde", "etiket": "Korumalı çıkışta azami kayma (%)",
             "tur": "sayi", "aralik": "0.05 – 5",
             "aciklama": "Stop kurulamadığında ya da kapatmada satış en iyi alışın en "
                         "fazla bu kadar altından yapılır."},
            {"ad": "korumasiz_azami_saniye", "etiket": "Kısmi dolumda azami bekleme (sn)",
             "tur": "tam", "aralik": "5 – 300",
             "aciklama": "Giriş yarım dolarsa kalan emir bu kadar beklenir; sonra iptal "
                         "edilip dolan kısma stop ve hedef konur."},
            {"ad": "yeniden_fiyatlama_denemesi", "etiket": "Yeniden fiyatlama denemesi",
             "tur": "tam", "aralik": "0 – 5",
             "aciklama": f"Kural emrinin girişi {executor.venue.defterde} hemen eşleşeceği "
                         "için reddedilirse en iyi alışa çekilip en fazla bu kadar yeniden "
                         "gönderilir. Elle emirde yapılmaz."},
        ],
    }


def register(app: FastAPI, get_runtime: Callable[[], Runtime | None]) -> None:
    """Demo Mode uçlarını uygulamaya ekler."""

    def rt() -> Runtime:
        runtime = get_runtime()
        if runtime is None:
            raise HTTPException(503, NOT_RUNNING)
        return runtime

    def meta_for(body: OrderBody, notes: list[str]) -> OrderMeta:
        return OrderMeta(kural_etiketi="Elle emir", gecerlilik_mum=body.gecerlilik_mum,
                         azami_tutma_mum=body.azami_tutma_mum, notlar=tuple(notes))

    # --- okuma ------------------------------------------------------------------

    @app.get("/api/demo/durum")
    def demo_durum() -> dict[str, Any]:
        runtime = rt()
        executor = _executor(runtime)
        now = utc_now()
        marks = runtime.demo_marks()
        limits = runtime.engine.limits
        active = [executor.position_payload(item, marks) for item in executor.ledger.active()]
        recent = [executor.position_payload(item, marks)
                  for item in executor.ledger.recent(20) if item.bitti]
        return {
            "zaman": istanbul_text(now),
            "canli": runtime.runner is not None,
            "baglanti": executor.status(),
            "modlar": [
                {"sembol": item.sembol, "mod": item.mod, "mod_tr": item.mod_tr}
                for item in runtime.engine.modes.all()
            ],
            "hesap": account_json(executor.account(marks), marks),
            "risk": meters_json(risk.metrics(executor.snapshot(now), limits, now), limits),
            "aktif": active,
            "son_kapanan": recent,
            "ozet": executor.summary(),
            "defter": book_json(executor, runtime, now),
            "ayarlar": settings_json(executor),
            "maliyet": {symbol: executor.costs_for(symbol).kaynak_tr
                        for symbol in runtime.symbols},
            "butce_notu": (
                "Demo hesabında Binance'in verdiği sahte bakiye bot bütçesinden büyük "
                "olabilir; bot yalnızca kendi bütçesi kadar kullanır ve sonuçları o "
                "bütçeye göre hesaplar."
            ),
        }

    @app.get("/api/demo/islemler")
    def demo_islemler(adet: int = Query(100, ge=1, le=1000)) -> dict[str, Any]:
        runtime = rt()
        executor = _executor(runtime)
        marks = runtime.demo_marks()
        return {"islemler": [executor.position_payload(item, marks)
                             for item in executor.ledger.recent(adet)]}

    @app.get("/api/demo/islemler.csv")
    def demo_csv() -> Response:
        executor = _executor(rt())
        stamp = utc_now().strftime("%Y%m%d-%H%M")
        return Response(
            content=executor.fills_csv().encode("utf-8"),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="demo-dolumlar-{stamp}.csv"'},
        )

    @app.get("/api/demo/on-izleme")
    def demo_on_izleme(
        sembol: str = Query(..., min_length=3, max_length=20),
        periyot: str = Query(..., min_length=2, max_length=4),
        giris: str = Query(..., max_length=40),
        hedef: str = Query(..., max_length=40),
        stop: str = Query(..., max_length=40),
    ) -> dict[str, Any]:
        """Elle Demo emrinin risk ve Demo kapıları, borsaya gidecek emirler. Kaydetmez."""
        runtime = rt()
        executor = _executor(runtime)
        intent, notes = _manual_intent(runtime, sembol=sembol, periyot=periyot,
                                       giris=giris, hedef=hedef, stop=stop)
        result = executor.preview(
            intent,
            market=runtime.market.market_state(intent.sembol, intent.periyot,
                                               executor.rules_for(intent.sembol)),
            now=utc_now(),
        )
        return {"karar": decision_json(result.karar), "fiyatlar": _intent_prices(intent),
                "yuvarlama": notes, "plan": plan_json(result.plan), "mesaj": result.mesaj}

    # --- durum değiştiren (POST) ------------------------------------------------------

    @app.post("/api/demo/emir")
    def demo_emir(body: OrderBody) -> dict[str, Any]:
        runtime = rt()
        executor = _executor(runtime)
        _need_trader(executor)
        if runtime.runner is None:
            raise HTTPException(409, OFFLINE)
        intent, notes = _manual_intent(runtime, sembol=body.sembol, periyot=body.periyot,
                                       giris=body.giris, hedef=body.hedef, stop=body.stop)
        now: datetime = utc_now()
        result = executor.place(
            intent,
            market=runtime.market.market_state(intent.sembol, intent.periyot,
                                               executor.rules_for(intent.sembol)),
            now=now, meta=meta_for(body, notes), source=SOURCE_UI, reprice=False,
        )
        position = result.pozisyon
        return {
            "karar": decision_json(result.karar),
            "fiyatlar": _intent_prices(intent),
            "yuvarlama": notes,
            "plan": plan_json(result.plan),
            "mesaj": result.mesaj,
            "pozisyon": None if position is None
            else executor.position_payload(position, runtime.demo_marks()),
        }

    @app.post("/api/demo/iptal")
    def demo_iptal(body: OrderIdBody) -> dict[str, Any]:
        runtime = rt()
        executor = _executor(runtime)
        _need_trader(executor)
        try:
            position = executor.request_cancel(body.id, source=SOURCE_UI)
        except ValueError as error:
            raise HTTPException(409, str(error)) from None
        return {"pozisyon": executor.position_payload(position, runtime.demo_marks())}

    @app.post("/api/demo/kapat")
    def demo_kapat(body: OrderIdBody) -> dict[str, Any]:
        runtime = rt()
        executor = _executor(runtime)
        _need_trader(executor)
        try:
            position = executor.request_close(body.id, source=SOURCE_UI)
        except ValueError as error:
            raise HTTPException(409, str(error)) from None
        return {"pozisyon": executor.position_payload(position, runtime.demo_marks())}

    @app.post("/api/demo/uzlastir")
    def demo_uzlastir(body: ConfirmBody) -> dict[str, Any]:
        executor = _executor(rt())
        _need_trader(executor)
        del body
        return {"ozet": executor.reconcile("elle istendi", full=True)}

    @app.post("/api/demo/ayarlar")
    def demo_ayarlar(body: SettingsBody) -> dict[str, Any]:
        executor = _executor(rt())
        try:
            executor.update_settings(dict(body.degerler), source=SOURCE_UI)
        except SettingsError as error:
            raise HTTPException(400, str(error)) from None
        return settings_json(executor)

    @app.post("/api/demo/art-arda-sifirla")
    def demo_art_arda_sifirla(body: ConfirmBody) -> dict[str, Any]:
        executor = _executor(rt())
        del body
        executor.reset_streak(source=SOURCE_UI, now=utc_now())
        return {"tamam": True}

    @app.post("/api/demo/hesap-sifirla")
    def demo_hesap_sifirla(body: ResetBody) -> dict[str, Any]:
        executor = _executor(rt())
        if body.onay.strip().upper() != RESET_WORD:
            raise HTTPException(400, f"Onay için kutuya {RESET_WORD} yazın.")
        try:
            period_id = executor.reset_account(source=SOURCE_UI, now=utc_now())
        except ValueError as error:
            raise HTTPException(409, str(error)) from None
        return {"donem_id": period_id}


__all__ = ["account_json", "book_json", "plan_json", "register", "settings_json"]
