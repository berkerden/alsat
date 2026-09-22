"""Arayüzün arkasındaki yerel sunucu (SPEC.md §8, Faz 3).

Uygulama **her zaman "Sadece Öneri" modunda açılır** (SPEC §2) ve bu fazda
başka bir mod yoktur: emir gönderen tek bir satır kod bu katmanda
bulunmuyor, API anahtarına erişimi de yok. Sunucu yalnızca ``127.0.0.1``e
bağlanır (SPEC §5).

Veri kaynağı disktir: Faz 1'in indirdiği Parquet mumlar, Faz 2'nin yazdığı
``kurallar.json`` ve veri tazeleme adımının bıraktığı ``exchangeinfo.json``.
Sunucu açılırken internete çıkmaz; tazeleme ayrı bir adımdır ve kullanıcı
onu bilerek çalıştırır.

Hesaplayıcı uçlar (``/api/maliyet-risk``, ``/api/plan``) ``GET``tir: yan
etkileri yoktur, saf fonksiyondur ve bağlantı paylaşılabilir. Durum
değiştiren tek uç ``/api/gunluk/kaydet``, o da yalnızca yerel günlüğe yazar.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from albsat.api import serialize
from albsat.core.filters import SymbolRules
from albsat.data.exchangeinfo import ExchangeInfoStore
from albsat.data.klines import closed_only, interval_ms, to_utc
from albsat.data.store import KlineStore
from albsat.strategy import panel as panel_module
from albsat.strategy import plan as plan_module
from albsat.strategy import rules as rulestore
from albsat.strategy import watch as watch_module
from albsat.strategy import wizard as wizard_module
from albsat.strategy.card import DISCLAIMER
from albsat.strategy.example import EXAMPLE_NOTE, example_recommendation
from albsat.strategy.journal import SignalJournal
from albsat.strategy.rules import RuleSet, RuleStoreError
from albsat.strategy.signals import EngineConfig, cost_threshold_text, recommend

STATIC_DIR = Path(__file__).parent / "static"

#: Uygulama bu fazda yalnızca bu modda çalışır.
MODE = "sadece_oneri"
MODE_TR = "Sadece Öneri"


@dataclass
class AppState:
    """Sunucunun okuduğu yerler ve kullanıcı ayarları."""

    veri_dizini: Path
    butce_usdt: str = "100"
    islem_basi_risk_yuzde: str = "1.0"
    semboller: tuple[str, ...] = ("BTCUSDT", "SOLUSDT")
    periyotlar: tuple[str, ...] = ("15m", "1h")

    @property
    def kural_dosyasi(self) -> Path:
        return rulestore.path_for(self.veri_dizini)

    def ruleset(self) -> RuleSet | None:
        """Kural deposunu okur; yoksa ``None``, bozuksa istisna."""
        if not self.kural_dosyasi.exists():
            return None
        return rulestore.load(self.kural_dosyasi)

    def symbol_rules(self, sembol: str) -> SymbolRules | None:
        snapshot = ExchangeInfoStore(self.veri_dizini).read()
        return snapshot.rules_for(sembol) if snapshot else None

    def engine(self) -> EngineConfig:
        return EngineConfig(
            butce_usdt=self.butce_usdt,
            islem_basi_risk_yuzde=self.islem_basi_risk_yuzde,
        )

    def journal(self) -> SignalJournal:
        return SignalJournal.in_directory(self.veri_dizini)


NO_RULESET_MESSAGE = (
    "Kural deposu bulunamadı. Faz 2 taramasını çalıştırın; tarama bittiğinde "
    "kabul edilen kuralları bu dosyaya yazar."
)


def _require_interval(periyot: str) -> str:
    """Tanınmayan periyodu 400 ile reddeder.

    Boş mum listesi döndürmek "bu periyotta veri yok" demektir; oysa doğru
    cevap "böyle bir periyot yok". İkisini aynı ekrana çıkarmak kullanıcıyı
    veri tazelemeye gönderirdi.
    """
    try:
        interval_ms(periyot)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return periyot


def _require_ruleset(state: AppState) -> RuleSet:
    try:
        ruleset = state.ruleset()
    except RuleStoreError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if ruleset is None:
        raise HTTPException(status_code=404, detail=NO_RULESET_MESSAGE)
    return ruleset


def create_app(state: AppState) -> FastAPI:
    """Uygulamayı kurar. Test de aynı fonksiyonu çağırır."""
    app = FastAPI(
        title="Binance Al-Sat — Sadece Öneri",
        description=(
            "Faz 3 arayüzü. Emir göndermez, API anahtarı kullanmaz, "
            "internete çıkmaz."
        ),
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.albsat = state

    # --- durum ---------------------------------------------------------

    @app.get("/api/durum")
    def durum() -> dict[str, Any]:
        """Açılışta arayüzün ihtiyacı olan her şey."""
        store = KlineStore(state.veri_dizini)
        info = ExchangeInfoStore(state.veri_dizini).read()

        try:
            ruleset = state.ruleset()
            rule_error = None
        except RuleStoreError as error:
            ruleset, rule_error = None, str(error)

        veri = []
        for sembol in state.semboller:
            for periyot in state.periyotlar:
                frame = store.read(sembol, periyot)
                if frame.empty:
                    veri.append(
                        {
                            "sembol": sembol,
                            "periyot": periyot,
                            "mum": 0,
                            "son_kapanis_utc": None,
                            "gecikme_mum": None,
                        }
                    )
                    continue
                frame = closed_only(frame)
                step = interval_ms(periyot)
                last_close = to_utc(int(frame["open_time"].max()) + step)
                veri.append(
                    {
                        "sembol": sembol,
                        "periyot": periyot,
                        "mum": int(len(frame)),
                        "son_kapanis_utc": last_close.isoformat(),
                        "gecikme_mum": round(
                            (datetime.now(UTC) - last_close).total_seconds()
                            / (step / 1000.0),
                            1,
                        ),
                    }
                )

        return {
            "mod": MODE,
            "mod_tr": MODE_TR,
            "uyari": DISCLAIMER,
            "veri_dizini": str(state.veri_dizini.resolve()),
            "semboller": list(state.semboller),
            "periyotlar": list(state.periyotlar),
            "butce_usdt": state.butce_usdt,
            "islem_basi_risk_yuzde": state.islem_basi_risk_yuzde,
            "veri": veri,
            "filtreler": None
            if info is None
            else {
                "indirilme_zamani_utc": info.indirilme_zamani_utc,
                "yas_gun": (
                    None if info.age_days() is None else round(info.age_days() or 0, 1)
                ),
                "semboller": list(info.semboller),
            },
            "kural_deposu": None
            if ruleset is None
            else serialize.run_summary(ruleset),
            "kural_deposu_hatasi": rule_error,
            "kural_deposu_yolu": str(state.kural_dosyasi),
        }

    # --- öneri motoru --------------------------------------------------

    @app.get("/api/oneriler")
    def oneriler(
        sembol: str = Query(..., min_length=3, max_length=20),
        periyot: str = Query(..., min_length=2, max_length=4),
        ornek: bool = Query(
            False,
            description="Kart şablonunu örnek bir kuralla doldurup gösterir. "
            "Bu bir öneri DEĞİLDİR; kartın eksiksizliğini ve marj hesabının "
            "doğruluğunu göstermek içindir.",
        ),
    ) -> dict[str, Any]:
        if ornek:
            data = serialize.recommendations(
                example_recommendation(sembol=sembol, periyot=periyot)
            )
            data["ornek"] = True
            data["ornek_notu"] = EXAMPLE_NOTE
            return data

        _require_interval(periyot)
        ruleset = _require_ruleset(state)
        result = recommend(
            ruleset,
            sembol=sembol.upper(),
            periyot=periyot,
            veri_dizini=state.veri_dizini,
            config=state.engine(),
            symbol_rules=state.symbol_rules(sembol.upper()),
        )
        data = serialize.recommendations(result)
        data["ornek"] = False
        return data

    @app.get("/api/kurallar")
    def kurallar() -> dict[str, Any]:
        """Örüntü kütüphanesi: kabul edilenler ve incelenen adaylar ayrı ayrı."""
        ruleset = _require_ruleset(state)
        return {
            "kosu": serialize.run_summary(ruleset),
            "maliyet_esigi": cost_threshold_text(ruleset.kosu.maliyet),
            "kabul_edilenler": [serialize.rule(item) for item in ruleset.kurallar],
            "incelenen_adaylar": [
                serialize.rule(item) for item in ruleset.incelenen_adaylar
            ],
            "incelenen_aday_notu": rulestore.NOT_ACCEPTED_NOTE,
            "bolumler": [serialize.section(item) for item in ruleset.bolumler],
        }

    @app.get("/api/sihirbaz")
    def sihirbaz(sembol: str = Query(..., min_length=3, max_length=20)) -> dict[str, Any]:
        ruleset = _require_ruleset(state)
        result = wizard_module.build_wizard(
            ruleset,
            sembol=sembol.upper(),
            periyotlar=state.periyotlar,
            veri_dizini=state.veri_dizini,
        )
        return serialize.wizard(result)

    @app.get("/api/mumlar")
    def mumlar(
        sembol: str = Query(..., min_length=3, max_length=20),
        periyot: str = Query(..., min_length=2, max_length=4),
        adet: int = Query(300, ge=10, le=2000),
    ) -> dict[str, Any]:
        """Grafik için son ``adet`` kapanmış mum."""
        _require_interval(periyot)
        frame = KlineStore(state.veri_dizini).read(sembol.upper(), periyot)
        if frame.empty:
            return {"sembol": sembol.upper(), "periyot": periyot, "mumlar": []}
        frame = closed_only(frame).tail(adet)
        return {
            "sembol": sembol.upper(),
            "periyot": periyot,
            "mumlar": [
                {
                    "t": int(row.open_time),
                    "a": float(row.open),
                    "y": float(row.high),
                    "d": float(row.low),
                    "k": float(row.close),
                    "h": float(row.volume),
                }
                for row in frame.itertuples()
            ],
        }

    # --- B eki ---------------------------------------------------------

    @app.get("/api/izleme")
    def izleme() -> dict[str, Any]:
        ruleset = _require_ruleset(state)
        board = watch_module.build_board(
            semboller=[
                (sembol, periyot)
                for sembol in state.semboller
                for periyot in state.periyotlar
            ],
            veri_dizini=state.veri_dizini,
            maliyet=ruleset.kosu.maliyet,
        )
        return serialize.watch(board)

    @app.get("/api/maliyet-risk")
    def maliyet_risk(
        sembol: str = Query(..., min_length=3, max_length=20),
        giris: str = Query(...),
        hedef: str = Query(...),
        stop: str = Query(...),
        butce: str | None = Query(None),
        risk: str | None = Query(None),
        try_kuru: str | None = Query(None),
    ) -> dict[str, Any]:
        ruleset = _require_ruleset(state)
        try:
            result = panel_module.evaluate(
                sembol=sembol.upper(),
                giris=giris,
                hedef=hedef,
                stop=stop,
                butce_usdt=butce or state.butce_usdt,
                risk_yuzde=risk or state.islem_basi_risk_yuzde,
                maliyet=ruleset.kosu.maliyet,
                rules=state.symbol_rules(sembol.upper()),
                try_kuru=try_kuru,
            )
        except (ValueError, ArithmeticError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return serialize.panel(result)

    @app.get("/api/plan")
    def plan(
        sembol: str = Query(..., min_length=3, max_length=20),
        butce: str | None = Query(None),
        dilim: int = Query(4, ge=1, le=52),
        kip: str = Query(plan_module.MODE_PERIODIC),
        aralik_gun: int = Query(7, ge=1, le=365),
        basamak_yuzde: str = Query("2"),
        periyot: str = Query("1h"),
    ) -> dict[str, Any]:
        _require_interval(periyot)
        ruleset = _require_ruleset(state)
        try:
            result = plan_module.build_plan(
                sembol=sembol.upper(),
                butce_usdt=butce or state.butce_usdt,
                dilim_sayisi=dilim,
                maliyet=ruleset.kosu.maliyet,
                veri_dizini=state.veri_dizini,
                periyot=periyot,
                kip=kip,
                aralik_gun=aralik_gun,
                basamak_yuzde=basamak_yuzde,
                rules=state.symbol_rules(sembol.upper()),
            )
        except (ValueError, ArithmeticError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return serialize.plan(result)

    # --- sinyal günlüğü ------------------------------------------------

    @app.get("/api/gunluk")
    def gunluk(adet: int = Query(50, ge=1, le=500)) -> dict[str, Any]:
        journal = state.journal()
        return {
            "kayitlar": [
                serialize.journal_entry(item) for item in journal.recent(adet)
            ],
            "performans": journal.performance(),
        }

    @app.get("/api/saglik")
    def saglik() -> dict[str, Any]:
        """Sistem sağlığı paneli (SPEC §8.9'un Faz 3'te ölçülebilen kısmı)."""
        return {
            "mod": MODE_TR,
            "internet_kullanimi": "yok — sunucu diskten okur",
            "emir_yetkisi": "yok — bu fazda emir gönderen kod bulunmuyor",
            "api_anahtari": "kullanılmıyor",
            "veri_dizini": str(state.veri_dizini.resolve()),
            "kural_deposu_var": state.kural_dosyasi.exists(),
            "filtre_onbellegi_var": ExchangeInfoStore(state.veri_dizini).exists(),
            "zaman_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
        }

    # --- statik dosyalar -----------------------------------------------

    if STATIC_DIR.exists():
        app.mount(
            "/statik", StaticFiles(directory=STATIC_DIR), name="statik"
        )

        @app.get("/")
        def anasayfa() -> FileResponse:
            return FileResponse(STATIC_DIR / "index.html")
    else:  # pragma: no cover - paket eksik kurulmuşsa

        @app.get("/")
        def anasayfa_yok() -> JSONResponse:
            return JSONResponse(
                {"hata": f"Arayüz dosyaları bulunamadı: {STATIC_DIR}"},
                status_code=500,
            )

    return app


__all__ = ["MODE", "MODE_TR", "STATIC_DIR", "AppState", "create_app"]
