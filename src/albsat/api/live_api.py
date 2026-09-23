"""Canlı işlem uçları (Faz 6; SPEC.md §4.6, §4.7, §5, §10 Faz 6).

Bu modül yalnızca ``LiveExecutor`` ve kâğıt motoruyla konuşur. İmzalı
istemciye, anahtara ve Anahtar Zinciri'ne dokunmaz (``tests/test_canli_arayuz.py``
yalıtımı denetler). Durum değiştiren her uç ``POST``'tur ve ``app.py``'deki yerel
koruma katmanından geçer.

Buradaki emirler **gerçek parayla** Binance canlı hesabına gider. Bu yüzden:

* Coin'i Yarı Otomatik'e almak, coin adının yazılmasını ister. Tam Otomatik
  ayrıca canlıya geçiş kapısını ister (``execution/gate.py``); kapıyı elle aşan
  bir uç yoktur.
* Elle canlı emir de coin adının yazılmasını ister; tutar verilebilir ama
  canlı emir tavanını aşamaz.
* Onay bekleyen öneri yalnızca açıkça "Emri Gönder" denince gider.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response
from pydantic import Field

from albsat.api.demo_api import ConfirmBody, SettingsBody, account_json, plan_json, settings_json
from albsat.api.paper_api import (
    RESET_WORD,
    OrderBody,
    OrderIdBody,
    ResetBody,
    _Body,
    _intent_prices,
    _manual_intent,
    _symbol,
    decision_json,
    meters_json,
)
from albsat.api.runtime import Runtime
from albsat.api.serialize import money
from albsat.core.audit import SOURCE_UI
from albsat.core.clock import istanbul_text, utc_now
from albsat.execution.gate import RuleGate, passed_for, symbol_summary
from albsat.execution.live import (
    PROPOSAL_WAITING,
    CapError,
    LiveExecutor,
    Proposal,
    ProposalError,
)
from albsat.execution.settings import SettingsError
from albsat.modes.state import (
    LIVE_MODES,
    MODE_ADVICE,
    MODE_FULL,
    MODE_LABELS_TR,
    MODE_SEMI,
    ModeError,
)
from albsat.paper.engine import OrderMeta
from albsat.risk import engine as risk

NOT_RUNNING = "Canlı işlem bu çalıştırmada kapalı. Arayüzü 'bash kurulum.sh arayuz' ile açın."
OFFLINE = (
    "Canlı piyasa verisi bu çalıştırmada kapalı (çevrimdışı açıldı). Canlı emir risk "
    "kapıları için canlı veriye ihtiyaç duyar; arayüzü çevrimdışı seçeneği olmadan açın."
)
REAL_MONEY = (
    "Bu sekmedeki emirler GERÇEK PARAYLA Binance canlı hesabınıza gider. Bot yalnızca "
    "kendi bütçesi kadar kullanır; her giriş emri canlı emir tavanını aşamaz ve anahtarın "
    "para çekme izni kapalı okunmadan gönderilmez."
)


class LiveOrderBody(OrderBody):
    #: Onay: coin adı elle yazılır (ör. BTCUSDT).
    onay: str = Field(max_length=20)
    #: İstenen tutar (USDT). Boşsa risk motoru tavana kadar boyutlar.
    tutar_usdt: str | None = Field(None, max_length=20)


class LiveModeBody(_Body):
    sembol: str = Field(min_length=3, max_length=20)
    mod: str = Field(min_length=2, max_length=20)
    onay: str = Field("", max_length=20)


class CapBody(_Body):
    tutar_usdt: str = Field(min_length=1, max_length=20)


class ProposalBody(_Body):
    kimlik: str = Field(min_length=4, max_length=12)


def _executor(runtime: Runtime) -> LiveExecutor:
    if runtime.live is None:
        raise HTTPException(503, NOT_RUNNING)
    return runtime.live


def _need_trader(executor: LiveExecutor) -> None:
    if executor.trader is None:
        raise HTTPException(409, executor.not_ready_reason() or NOT_RUNNING)


def _confirm(symbol: str, typed: str) -> None:
    if typed.strip().upper() != symbol:
        raise HTTPException(400, f"Onay için kutuya coin adını yazın: {symbol}")


def _amount(text: str | None, executor: LiveExecutor) -> Decimal | None:
    if text is None or not text.strip():
        return None
    try:
        value = Decimal(text.strip().replace(",", "."))
    except (InvalidOperation, ValueError):
        raise HTTPException(400, "Tutar bir sayı olmalı (ör. 6).") from None
    if not value.is_finite() or value <= 0:
        raise HTTPException(400, "Tutar sıfırdan büyük olmalı.")
    cap = executor.cap_usdt()
    if value > cap:
        raise HTTPException(400, f"Tutar ({value} USDT) canlı emir tavanını ({cap} USDT) "
                                 "aşıyor. Tavanı değiştirmek için Ayarlar bölümü.")
    return value


def gate_json(item: RuleGate) -> dict[str, Any]:
    return {
        "kural_kimligi": item.kural_kimligi,
        "kural_etiketi": item.kural_etiketi,
        "sembol": item.sembol,
        "periyot": item.periyot,
        "gun": round(item.gun, 2),
        "islem": item.islem,
        "ortalama_net_yuzde": item.ortalama_net_yuzde,
        "beklenen_yuzde": item.beklenen_yuzde,
        "gecti": item.gecti,
        "kosullar": [
            {"ad": c.ad, "etiket": c.etiket, "gecti": c.gecti, "aciklama": c.aciklama}
            for c in item.kosullar
        ],
    }


def gates_json(executor: LiveExecutor, symbols: tuple[str, ...]) -> dict[str, Any]:
    gates = executor.gates()
    return {
        "kurallar": [gate_json(item) for item in gates],
        "coinler": [
            {"sembol": symbol, "tam_otomatik_acilabilir": bool(passed_for(symbol, gates)),
             "ozet": symbol_summary(symbol, gates)}
            for symbol in symbols
        ],
        "aciklama": (
            "Bir kural Tam Otomatik'te canlı emir açabilmek için kâğıtta en az 7 gün ve 30 "
            "kapanmış kural işlemi çalışmalı, sonucu beklentisiyle uyumlu ve ortalaması "
            "pozitif olmalı. Elle kâğıt emirler sayılmaz. Kapıyı elle aşan bir yol yok."
        ),
    }


def proposal_json(item: Proposal) -> dict[str, Any]:
    return {
        "kimlik": item.kimlik,
        "durum": item.durum,
        "durum_tr": item.durum_tr,
        "olusturma": istanbul_text(item.olusturma_utc),
        "bitis": istanbul_text(item.bitis_utc),
        "sembol": item.sembol,
        "periyot": item.periyot,
        "kural_etiketi": item.kural_etiketi,
        "giris": item.giris,
        "hedef": item.hedef,
        "stop": item.stop,
        "tahmini_tutar_usdt": item.tahmini_tutar_usdt,
        "notlar": list(item.notlar),
        "sonuc": item.sonuc,
        "pozisyon_id": item.pozisyon_id,
    }


def book_json(runtime: Runtime, now: datetime) -> list[dict[str, Any]]:
    """Canlı piyasanın en iyi fiyatları (akıştan; Binance'e istek göndermez)."""
    rows = []
    for symbol in runtime.symbols:
        quote = runtime.market.quote(symbol)
        rows.append({
            "sembol": symbol,
            "alis": None if quote is None else money(quote.alis),
            "satis": None if quote is None else money(quote.satis),
            "yas_sn": None if quote is None else round((now - quote.zaman).total_seconds(), 1),
        })
    return rows


def register(app: FastAPI, get_runtime: Callable[[], Runtime | None]) -> None:
    """Canlı işlem uçlarını uygulamaya ekler."""

    def rt() -> Runtime:
        runtime = get_runtime()
        if runtime is None:
            raise HTTPException(503, NOT_RUNNING)
        return runtime

    def meta_for(body: OrderBody, notes: list[str]) -> OrderMeta:
        return OrderMeta(kural_etiketi="Elle canlı emir", gecerlilik_mum=body.gecerlilik_mum,
                         azami_tutma_mum=body.azami_tutma_mum, notlar=tuple(notes))

    def modes(runtime: Runtime) -> list[dict[str, Any]]:
        return [{"sembol": item.sembol, "mod": item.mod, "mod_tr": item.mod_tr,
                 "canli": item.mod in LIVE_MODES}
                for item in runtime.engine.modes.all()]

    # --- okuma ------------------------------------------------------------------

    @app.get("/api/canli/durum")
    def canli_durum() -> dict[str, Any]:
        runtime = rt()
        executor = _executor(runtime)
        now = utc_now()
        marks = runtime.marks()
        limits = runtime.engine.limits
        active = [executor.position_payload(item, marks) for item in executor.ledger.active()]
        recent = [executor.position_payload(item, marks)
                  for item in executor.ledger.recent(20) if item.bitti]
        proposals = executor.proposals()
        return {
            "zaman": istanbul_text(now),
            "canli": runtime.runner is not None,
            "uyari": REAL_MONEY,
            "baglanti": executor.status(),
            "modlar": modes(runtime),
            "mod_secenekleri": [
                {"mod": MODE_ADVICE, "etiket": MODE_LABELS_TR[MODE_ADVICE]},
                {"mod": MODE_SEMI, "etiket": MODE_LABELS_TR[MODE_SEMI]},
                {"mod": MODE_FULL, "etiket": MODE_LABELS_TR[MODE_FULL]},
            ],
            "hesap": account_json(executor.account(marks), marks),
            "risk": meters_json(risk.metrics(executor.snapshot(now), limits, now), limits),
            "aktif": active,
            "son_kapanan": recent,
            "ozet": executor.summary(),
            "defter": book_json(runtime, now),
            "ayarlar": settings_json(executor),
            "tavan_usdt": str(executor.cap_usdt()),
            "butce_usdt": str(limits.butce_usdt),
            "maliyet": {symbol: executor.costs_for(symbol).kaynak_tr
                        for symbol in runtime.symbols},
            "bekleyen_oneriler": [proposal_json(item) for item in proposals
                                  if item.durum == PROPOSAL_WAITING],
            "son_oneriler": [proposal_json(item) for item in reversed(proposals)
                             if item.durum != PROPOSAL_WAITING][:10],
            "gecis_kapisi": gates_json(executor, runtime.symbols),
            "butce_notu": (
                "Canlı hesabınızdaki bakiye bot bütçesinden büyük olabilir; bot yalnızca kendi "
                "bütçesi kadar kullanır, sonuçları o bütçeye göre hesaplar ve kendi "
                "açmadığı emirlere ve coinlere dokunmaz."
            ),
        }

    @app.get("/api/canli/islemler")
    def canli_islemler(adet: int = Query(100, ge=1, le=1000)) -> dict[str, Any]:
        runtime = rt()
        executor = _executor(runtime)
        marks = runtime.marks()
        return {"islemler": [executor.position_payload(item, marks)
                             for item in executor.ledger.recent(adet)]}

    @app.get("/api/canli/islemler.csv")
    def canli_csv() -> Response:
        executor = _executor(rt())
        stamp = utc_now().strftime("%Y%m%d-%H%M")
        return Response(
            content=executor.fills_csv().encode("utf-8"),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition":
                     f'attachment; filename="canli-dolumlar-{stamp}.csv"'},
        )

    @app.get("/api/canli/on-izleme")
    def canli_on_izleme(
        sembol: str = Query(..., min_length=3, max_length=20),
        periyot: str = Query(..., min_length=2, max_length=4),
        giris: str = Query(..., max_length=40),
        hedef: str = Query(..., max_length=40),
        stop: str = Query(..., max_length=40),
        tutar_usdt: str | None = Query(None, max_length=20),
    ) -> dict[str, Any]:
        """Elle canlı emrin kapıları ve borsaya gidecek emirler. Kaydetmez, göndermez."""
        runtime = rt()
        executor = _executor(runtime)
        intent, notes = _manual_intent(runtime, sembol=sembol, periyot=periyot,
                                       giris=giris, hedef=hedef, stop=stop)
        result = executor.preview(
            intent,
            market=runtime.market.market_state(intent.sembol, intent.periyot,
                                               executor.rules_for(intent.sembol)),
            now=utc_now(), amount_usdt=_amount(tutar_usdt, executor),
        )
        return {"karar": decision_json(result.karar), "fiyatlar": _intent_prices(intent),
                "yuvarlama": notes, "plan": plan_json(result.plan), "mesaj": result.mesaj}

    @app.get("/api/canli/tam-otomatik-ozet")
    def canli_tam_ozet(sembol: str = Query(..., min_length=3, max_length=20)) -> dict[str, Any]:
        """Tam Otomatik'i açmadan önce gösterilen özet (SPEC §4.7)."""
        runtime = rt()
        executor = _executor(runtime)
        symbol = _symbol(runtime, sembol)
        gates = executor.gates()
        passed = passed_for(symbol, gates)
        limits = runtime.engine.limits
        return {
            "sembol": symbol,
            "acilabilir": bool(passed) and executor.not_ready_reason() is None,
            "kapi": symbol_summary(symbol, gates),
            "hazir_degil": executor.not_ready_reason(),
            "butce_usdt": str(limits.butce_usdt),
            "tavan_usdt": str(executor.cap_usdt()),
            "periyotlar": list(runtime.periods),
            "kurallar": [gate_json(item) for item in passed],
            "risk": [
                {"etiket": "İşlem başı risk", "deger": f"%{limits.islem_basi_risk_yuzde}"},
                {"etiket": "Günlük azami zarar", "deger": f"%{limits.gunluk_max_zarar_yuzde}"},
                {"etiket": "Haftalık azami düşüş",
                 "deger": f"%{limits.haftalik_max_dusus_yuzde}"},
                {"etiket": "Art arda kayıp sınırı", "deger": str(limits.art_arda_kayip_limiti)},
                {"etiket": "Eş zamanlı pozisyon", "deger": str(limits.max_es_zamanli_pozisyon)},
                {"etiket": "Günlük azami işlem", "deger": str(limits.gunluk_max_islem)},
            ],
        }

    # --- durum değiştiren (POST) ------------------------------------------------------

    @app.post("/api/canli/mod")
    def canli_mod(body: LiveModeBody) -> dict[str, Any]:
        runtime = rt()
        executor = _executor(runtime)
        symbol = _symbol(runtime, body.sembol)
        if body.mod not in (MODE_ADVICE, *LIVE_MODES):
            raise HTTPException(400, "Bu sekmede yalnızca Sadece Öneri, Yarı Otomatik ve Tam "
                                     "Otomatik seçilir.")
        if body.mod in LIVE_MODES:
            if runtime.runner is None:
                raise HTTPException(409, OFFLINE)
            _need_trader(executor)
            _confirm(symbol, body.onay)
        try:
            old, new = runtime.engine.set_mode(symbol, body.mod, source=SOURCE_UI,
                                               now=utc_now(), allow_live=True)
        except ModeError as error:
            raise HTTPException(400, str(error)) from None
        return {"eski": old, "yeni": new, "modlar": modes(runtime)}

    @app.post("/api/canli/emir")
    def canli_emir(body: LiveOrderBody) -> dict[str, Any]:
        runtime = rt()
        executor = _executor(runtime)
        _need_trader(executor)
        if runtime.runner is None:
            raise HTTPException(409, OFFLINE)
        intent, notes = _manual_intent(runtime, sembol=body.sembol, periyot=body.periyot,
                                       giris=body.giris, hedef=body.hedef, stop=body.stop)
        _confirm(intent.sembol, body.onay)
        amount = _amount(body.tutar_usdt, executor)
        now: datetime = utc_now()
        result = executor.place(
            intent,
            market=runtime.market.market_state(intent.sembol, intent.periyot,
                                               executor.rules_for(intent.sembol)),
            now=now, meta=meta_for(body, notes), source=SOURCE_UI, reprice=False,
            amount_usdt=amount,
        )
        position = result.pozisyon
        return {
            "karar": decision_json(result.karar),
            "fiyatlar": _intent_prices(intent),
            "yuvarlama": notes,
            "plan": plan_json(result.plan),
            "mesaj": result.mesaj,
            "pozisyon": None if position is None
            else executor.position_payload(position, runtime.marks()),
        }

    @app.post("/api/canli/oneri-gonder")
    def canli_oneri_gonder(body: ProposalBody) -> dict[str, Any]:
        runtime = rt()
        executor = _executor(runtime)
        _need_trader(executor)
        if runtime.runner is None:
            raise HTTPException(409, OFFLINE)
        try:
            result = executor.approve(body.kimlik, source=SOURCE_UI)
        except ProposalError as error:
            raise HTTPException(409, str(error)) from None
        position = result.pozisyon
        return {
            "karar": decision_json(result.karar),
            "mesaj": result.mesaj,
            "plan": plan_json(result.plan),
            "pozisyon": None if position is None
            else executor.position_payload(position, runtime.marks()),
        }

    @app.post("/api/canli/oneri-reddet")
    def canli_oneri_reddet(body: ProposalBody) -> dict[str, Any]:
        executor = _executor(rt())
        try:
            item = executor.reject(body.kimlik, source=SOURCE_UI)
        except ProposalError as error:
            raise HTTPException(409, str(error)) from None
        return {"oneri": proposal_json(item)}

    @app.post("/api/canli/iptal")
    def canli_iptal(body: OrderIdBody) -> dict[str, Any]:
        runtime = rt()
        executor = _executor(runtime)
        _need_trader(executor)
        try:
            position = executor.request_cancel(body.id, source=SOURCE_UI)
        except ValueError as error:
            raise HTTPException(409, str(error)) from None
        return {"pozisyon": executor.position_payload(position, runtime.marks())}

    @app.post("/api/canli/kapat")
    def canli_kapat(body: OrderIdBody) -> dict[str, Any]:
        runtime = rt()
        executor = _executor(runtime)
        _need_trader(executor)
        try:
            position = executor.request_close(body.id, source=SOURCE_UI)
        except ValueError as error:
            raise HTTPException(409, str(error)) from None
        return {"pozisyon": executor.position_payload(position, runtime.marks())}

    @app.post("/api/canli/uzlastir")
    def canli_uzlastir(body: ConfirmBody) -> dict[str, Any]:
        executor = _executor(rt())
        _need_trader(executor)
        del body
        return {"ozet": executor.reconcile("elle istendi", full=True)}

    @app.post("/api/canli/ayarlar")
    def canli_ayarlar(body: SettingsBody) -> dict[str, Any]:
        executor = _executor(rt())
        try:
            executor.update_settings(dict(body.degerler), source=SOURCE_UI)
        except SettingsError as error:
            raise HTTPException(400, str(error)) from None
        return settings_json(executor)

    @app.post("/api/canli/tavan")
    def canli_tavan(body: CapBody) -> dict[str, Any]:
        executor = _executor(rt())
        try:
            value = executor.update_cap(body.tutar_usdt, source=SOURCE_UI)
        except CapError as error:
            raise HTTPException(400, str(error)) from None
        return {"tavan_usdt": str(value)}

    @app.post("/api/canli/art-arda-sifirla")
    def canli_art_arda_sifirla(body: ConfirmBody) -> dict[str, Any]:
        executor = _executor(rt())
        del body
        executor.reset_streak(source=SOURCE_UI, now=utc_now())
        return {"tamam": True}

    @app.post("/api/canli/hesap-sifirla")
    def canli_hesap_sifirla(body: ResetBody) -> dict[str, Any]:
        executor = _executor(rt())
        if body.onay.strip().upper() != RESET_WORD:
            raise HTTPException(400, f"Onay için kutuya {RESET_WORD} yazın.")
        try:
            period_id = executor.reset_account(source=SOURCE_UI, now=utc_now())
        except ValueError as error:
            raise HTTPException(409, str(error)) from None
        return {"donem_id": period_id}


__all__ = ["REAL_MONEY", "gate_json", "gates_json", "proposal_json", "register"]
