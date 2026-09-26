"""Arayüzün arkasındaki yerel sunucu (SPEC.md §8).

Sunucu yalnızca ``127.0.0.1``e bağlanır (SPEC §5) ve uygulama **her açılışta
bütün coinler "Sadece Öneri" modunda** başlar (SPEC §2).

Faz 3 uçları (öneriler, kütüphane, sihirbaz, ek araçlar) diskten okur ve
yan etkisizdir. Faz 4 ile gelen kâğıt işlem uçları ``paper_api`` dosyasında
ve ``/api/kagit/`` altındadır; yalnızca **kâğıt hesabı** değiştirirler.
Faz 5'in Demo Mode uçları ``demo_api`` dosyasında ve ``/api/demo/``
altındadır; emirleri Demo yürütücüsü (``execution.executor``) gönderir.
Faz 6'nın canlı işlem uçları ``live_api`` dosyasında ve ``/api/canli/``
altındadır; emirleri canlı yürütücü (``execution.live``) gönderir. Bu
katmanda imzalı istemciye ya da API anahtarına erişen kod yoktur.

**Yerel koruma.** Sunucu yalnızca bu Mac'ten erişilebilir olsa da tarayıcıda
açık başka bir site, kullanıcının tarayıcısı üzerinden ``127.0.0.1``e istek
atmayı deneyebilir. Bu yüzden:

* ``Host`` başlığı ``127.0.0.1`` ya da ``localhost`` değilse istek reddedilir
  (başka bir alan adının bu adrese yönlendirilmesiyle yapılan saldırı,
  "DNS rebinding", böyle engellenir).
* ``GET``/``HEAD`` dışındaki her istek ``X-Albsat-Istek: 1`` başlığı ve JSON
  gövde ister; ``Origin`` ya da ``Sec-Fetch-Site`` başlığı varsa isteğin bu
  sayfadan geldiğini göstermelidir. Başka bir sitedeki sayfa bu başlığı
  ekleyemez: tarayıcı önce izin sorar, bu sunucu izin vermez.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from albsat.api import demo_api, live_api, paper_api, serialize
from albsat.core import keychain
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

if TYPE_CHECKING:
    from albsat.api.runtime import Runtime

STATIC_DIR = Path(__file__).parent / "static"

#: Arayüze yalnızca bu adlarla ulaşılır (``Host`` başlığı).
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost"})
#: Durum değiştiren isteklerin taşıması gereken başlık.
REQUEST_HEADER = "X-Albsat-Istek"

#: API yanıtlarında arayüz dosyalarının sürümünü taşıyan başlık. Açık bir
#: sekme bunu kendi yüklendiği sürümle karşılaştırır.
VERSION_HEADER = "X-Arayuz-Surumu"

_version_cache: tuple[tuple[tuple[str, int, int], ...], str] | None = None


def asset_version(directory: Path = STATIC_DIR) -> str:
    """Arayüz dosyalarının içeriğinden türetilen kısa sürüm kimliği.

    Neden var: 22 Eylül 2026'da grafik hatası düzeltilip gönderildiği halde
    kullanıcının ekranında sürdü. Önceden açılmış sekme eski ``grafik.js``'i
    çalıştırmaya devam ediyordu; uygulamanın "Yenile" düğmesi veriyi yeniler,
    kodu yenilemez. Bu kimlik üç yerde kullanılır: dosya adreslerine eklenir
    (eski kopya yeni adresle karışmaz), API yanıtlarına başlık olarak konur
    (açık sekme sürümün değiştiğini anlar), sayfanın altında yazar (kullanıcı
    hangi sürümü gördüğünü okuyabilir).

    İçerikten türetilir, saatten değil: aynı dosyalar her makinede aynı
    kimliği verir. Değişim zamanı ve boyut yalnızca önbellek anahtarıdır;
    sunucu çalışırken ``git pull`` yapılırsa yeni kimlik hemen görünür.
    """
    global _version_cache
    files = sorted(item for item in directory.iterdir() if item.is_file())
    signature = tuple(
        (item.name, item.stat().st_mtime_ns, item.stat().st_size) for item in files
    )
    if _version_cache is not None and _version_cache[0] == signature:
        return _version_cache[1]
    digest = hashlib.sha256()
    for item in files:
        digest.update(item.name.encode("utf-8") + b"\0")
        digest.update(item.read_bytes())
    version = digest.hexdigest()[:8]
    _version_cache = (signature, version)
    return version

#: Uygulama her açılışta bu modda başlar (SPEC §2). Coin başına mod
#: ``/api/kagit/durum`` → ``modlar``.
MODE = "sadece_oneri"
MODE_TR = "Sadece Öneri"


def _host_name(value: str) -> str:
    value = value.strip().lower()
    if value.startswith("["):
        return value.split("]", 1)[0] + "]"
    return value.split(":", 1)[0]


def write_problem(headers: Any) -> str | None:
    """Durum değiştiren istek bu sayfadan gelmiyorsa sebebini döner."""
    if headers.get(REQUEST_HEADER) != "1":
        return "İstek arayüzün kendisinden gelmedi (eksik başlık); reddedildi."
    content_type = (headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        return "İstek gövdesi JSON olmalı."
    origin = headers.get("origin")
    if origin is not None and origin != "null":
        parts = urlsplit(origin)
        if parts.scheme != "http" or (parts.hostname or "") not in LOCAL_HOSTS:
            return "İstek başka bir siteden geldi; reddedildi."
    elif origin == "null":
        return "İstek kaynağı belirsiz; reddedildi."
    site = headers.get("sec-fetch-site")
    if site is not None and site not in ("same-origin", "none"):
        return "İstek başka bir siteden geldi; reddedildi."
    return None


@dataclass
class AppState:
    """Sunucunun okuduğu yerler ve kullanıcı ayarları."""

    veri_dizini: Path
    butce_usdt: str = "100"
    islem_basi_risk_yuzde: str = "1.0"
    semboller: tuple[str, ...] = ("BTCUSDT", "SOLUSDT")
    periyotlar: tuple[str, ...] = ("15m", "1h")
    #: Faz 4: kâğıt motoru, canlı döngü, bildirimler. Yoksa kâğıt uçları 503.
    runtime: Runtime | None = None

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

    def budget(self) -> tuple[str, str]:
        """(bütçe USDT, işlem başına risk %). Kâğıt motoru varsa onun limitleri.

        Öneri kartı ile kâğıt işlem aynı büyüklüğü hesaplasın diye tek kaynak:
        kullanıcı risk limitlerinde bütçeyi değiştirirse kartlar da değişir.
        """
        if self.runtime is not None:
            limits = self.runtime.engine.limits
            return str(limits.butce_usdt), str(limits.islem_basi_risk_yuzde)
        return self.butce_usdt, self.islem_basi_risk_yuzde

    def engine(self) -> EngineConfig:
        budget, risk = self.budget()
        return EngineConfig(butce_usdt=budget, islem_basi_risk_yuzde=risk)

    def journal(self) -> SignalJournal:
        return SignalJournal.in_directory(self.veri_dizini)


NO_RULESET_MESSAGE = (
    "Kural deposu bulunamadı. Faz 2 taramasını çalıştırın; tarama bittiğinde "
    "kabul edilen kuralları bu dosyaya yazar."
)


def _order_authority(runtime: Runtime | None) -> str:
    """Sağlık panelindeki "emir yetkisi" satırı: hangi hesaba emir gidebilir?"""
    demo = runtime is not None and runtime.demo is not None and runtime.demo.trader is not None
    live = runtime is not None and runtime.live is not None and runtime.live.trader is not None
    if live:
        return ("CANLI hesap (gerçek para; yalnızca Yarı/Tam Otomatik coinler, emir başına "
                "tavanla)" + (" ve Binance Demo Mode (sahte para)" if demo else ""))
    if demo:
        return "yalnızca Binance Demo Mode (sahte para); canlı işlem anahtarı kurulu değil"
    return "yok — Demo ve canlı anahtar kurulu değil; kâğıt emirler yalnızca yerel defterde"


def _keys_in_use(runtime: Runtime | None) -> str:
    parts = []
    if runtime is not None and runtime.live is not None and runtime.live.trader is not None:
        parts.append(f"canlı işlem anahtarı ({keychain.where('de')}; para çekme izni "
                     "kapalı okunmadan emir gitmez)")
    if runtime is not None and runtime.demo is not None and runtime.demo.trader is not None:
        parts.append(f"Demo Mode anahtarı ({keychain.where('de')}; Demo'da para çekme yok)")
    return "; ".join(parts) or "kullanılmıyor"


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
        title="Binance Al-Sat",
        description=(
            "Yerel arayüz. Binance'e emir göndermez, API anahtarı kullanmaz; "
            "kâğıt işlem yalnızca yerel defterde."
        ),
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.albsat = state

    @app.middleware("http")
    async def onbellek_ve_surum(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        # Tarayıcı bu uygulamanın hiçbir yanıtını saklamasın. Dosyalar
        # yerelde ve küçük, saklamanın kazancı yok; saklanan eski bir kopya
        # ise düzeltilmiş bir hatayı kullanıcının ekranında yaşatır.
        response.headers["Cache-Control"] = "no-store"
        if request.url.path.startswith("/api/") and STATIC_DIR.exists():
            response.headers[VERSION_HEADER] = asset_version()
        return response

    # Sonra eklenen katman dışta çalışır: bu kontrol her şeyden önce yapılır.
    @app.middleware("http")
    async def yerel_koruma(request: Request, call_next: Any) -> Any:
        if _host_name(request.headers.get("host", "")) not in LOCAL_HOSTS:
            return JSONResponse(
                {"detail": "Bu arayüz yalnızca http://127.0.0.1 adresinden kullanılır."},
                status_code=403,
            )
        if request.method not in ("GET", "HEAD"):
            problem = write_problem(request.headers)
            if problem is not None:
                return JSONResponse({"detail": problem}, status_code=403)
        return await call_next(request)

    paper_api.register(app, lambda: state.runtime)
    demo_api.register(app, lambda: state.runtime)
    live_api.register(app, lambda: state.runtime)

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

        runtime = state.runtime
        return {
            "mod": MODE,
            "mod_tr": MODE_TR,
            "modlar": None if runtime is None else [
                {"sembol": item.sembol, "mod": item.mod, "mod_tr": item.mod_tr}
                for item in runtime.engine.modes.all()
            ],
            "kagit_etkin": runtime is not None,
            "canli": runtime is not None and runtime.runner is not None,
            "uyari": DISCLAIMER,
            "veri_dizini": str(state.veri_dizini.resolve()),
            "semboller": list(state.semboller),
            "periyotlar": list(state.periyotlar),
            "butce_usdt": state.budget()[0],
            "islem_basi_risk_yuzde": state.budget()[1],
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
                butce_usdt=butce or state.budget()[0],
                risk_yuzde=risk or state.budget()[1],
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
                butce_usdt=butce or state.budget()[0],
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
        """Sistem sağlığı paneli (SPEC §8.9). Canlı bağlantı: ``/api/kagit/piyasa``."""
        runtime = state.runtime
        live = runtime is not None and runtime.runner is not None
        live_orders = (runtime is not None and runtime.live is not None
                       and runtime.live.trader is not None)
        return {
            "mod": MODE_TR,
            "internet_kullanimi": (
                "Binance genel piyasa verisi (WebSocket, kopunca REST) — hesap bilgisi yok"
                if live else "yok — sunucu diskten okur"
            ),
            "emir_yetkisi": _order_authority(runtime),
            "canli_emir": live_orders,
            "api_anahtari": _keys_in_use(runtime),
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

        @app.get("/", response_class=HTMLResponse)
        def anasayfa() -> HTMLResponse:
            page = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
            return HTMLResponse(page.replace("{{SURUM}}", asset_version()))
    else:  # pragma: no cover - paket eksik kurulmuşsa

        @app.get("/")
        def anasayfa_yok() -> JSONResponse:
            return JSONResponse(
                {"hata": f"Arayüz dosyaları bulunamadı: {STATIC_DIR}"},
                status_code=500,
            )

    return app


__all__ = [
    "LOCAL_HOSTS",
    "MODE",
    "MODE_TR",
    "REQUEST_HEADER",
    "STATIC_DIR",
    "VERSION_HEADER",
    "AppState",
    "asset_version",
    "create_app",
    "write_problem",
]
