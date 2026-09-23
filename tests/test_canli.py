"""Faz 6: canlı hesap (gerçek para) yolu ve güvenlik denetimleri.

Kabul ölçütü (SPEC.md §10 Faz 6): *"Canlıya geçiş kapısı çalışıyor; güvenlik
kontrolleri aktif."* Buradaki testler canlıya özgü kilitleri sınar; emir
akışının kendisi (dolum, koruma, uzlaştırma, belirsiz sonuç) ``test_demo_kaos.py``'de
her test için hem Demo hem canlı yürütücüyle çalışır.

Borsa yerine ``fake_binance``: her isteğin imzası gerçekten doğrulanır.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fake_binance import FakeBinance, FakeUserStream
from fastapi.testclient import TestClient
from test_demo_kaos import API_KEY, BTC, KEY, PUBLIC_PEM, Clock, Rig, build, fill_entry, placed
from test_kagit_islem import market

from albsat.api.app import AppState, create_app
from albsat.api.runtime import Runtime
from albsat.cli import canli as cli
from albsat.core.audit import SOURCE_TELEGRAM, SOURCE_UI
from albsat.core.clock import iso
from albsat.data.commission import CommissionStore
from albsat.data.live import LiveMarket
from albsat.data.store import KlineStore
from albsat.exchange.endpoints import Environment
from albsat.exchange.keys import SecretText
from albsat.exchange.ratelimit import RequestBudget
from albsat.exchange.signed import live_key_problems
from albsat.exchange.trading import (
    ALLOWED_LIVE,
    KEYCHAIN_SERVICE_LIVE,
    LIVE_PREFIX,
    PERMISSIONS_PATH,
    DemoTrader,
    LiveTrader,
    TradingRefused,
)
from albsat.execution import ids
from albsat.execution.gate import passed_for, rule_gates, symbol_summary
from albsat.execution.ledger import POS_CANCELLED, POS_PENDING, POS_PROTECTED
from albsat.execution.live import (
    DEFAULT_CAP_USDT,
    PROPOSAL_CANCELLED,
    PROPOSAL_EXPIRED,
    PROPOSAL_REJECTED,
    PROPOSAL_SENT,
    PROPOSAL_WAITING,
    CapError,
    LiveExecutor,
    ProposalError,
)
from albsat.execution.planner import plan_otoco
from albsat.execution.service import LIVE_OFFLINE, build_live_executor
from albsat.execution.settings import ExecutionSettings
from albsat.modes.state import MODE_ADVICE, MODE_FULL, MODE_PAPER, MODE_SEMI, ModeError
from albsat.notify.base import MemoryNotifier
from albsat.notify.commands import build_handler
from albsat.paper.engine import PaperEngine
from albsat.paper.ledger import STATUS_CLOSED
from albsat.risk.engine import SOURCE_MANUAL, SOURCE_RULE

D = Decimal
ROOT = Path(__file__).resolve().parents[1]
YAZ = {"X-Albsat-Istek": "1"}


def live(tmp_path: Path, **kwargs: Any) -> Rig:
    return build(tmp_path, hesap="canli", **kwargs)


def default_cap(rig: Rig) -> None:
    """Kaos düzeneği tavanı bütçeye çekiyor; burada varsayılana döner."""
    rig.executor.ledger.set_state("emir_tavani_usdt", "")


def texts(rig: Rig) -> str:
    return "\n".join(rig.notifier.texts)


# --- imzalayan sınıf: canlıya özgü kilitler -------------------------------------------------


def _trader(fake: FakeBinance, clock: Clock, **kwargs: Any) -> LiveTrader:
    return LiveTrader(KEY, opener=fake, time_ms=clock.local_ms, monotonic=clock.monotonic,
                      **kwargs)


def _otoco(quantity: str = "0.001", prefix_scheme: ids.IdScheme = ids.LIVE) -> dict[str, str]:
    from test_kagit_islem import RULES

    return plan_otoco(rules=RULES, token="abc", attempt=1, entry=D("60000"),
                      target=D("60900"), stop=D("59400"), quantity=D(quantity),
                      fee_rate=D("0.001"), carried_dust=D("0"),
                      settings=ExecutionSettings(), scheme=prefix_scheme).params


def test_canli_istemci_yalnizca_canli_ortamda_kurulur_ve_kendi_onekini_kullanir():
    for environment in (Environment.DEMO, Environment.TESTNET):
        with pytest.raises(TradingRefused):
            LiveTrader(KEY, environment=environment)
    trader = LiveTrader(KEY, opener=lambda *a, **k: pytest.fail("istek gönderilmemeli"))
    assert trader.base == "https://api.binance.com"
    assert trader.real_money and trader.prefix == LIVE_PREFIX == "albsat-canli-"
    assert API_KEY not in repr(trader)
    # Demo önekli bir emir canlı istemciden gönderilemez (kimlikler karışmaz).
    with pytest.raises(TradingRefused):
        trader.place_otoco(_otoco(prefix_scheme=ids.DEMO))
    with pytest.raises(TradingRefused):
        trader.cancel_order(BTC, "albsat-demo-abc-G1")
    with pytest.raises(TradingRefused):
        trader.cancel_order(BTC, "web_elle_123")


def test_canli_izin_listesinde_yalnizca_izin_okuma_eklendi():
    paths = {path for _, path in ALLOWED_LIVE}
    assert ("DELETE", "/api/v3/openOrders") not in ALLOWED_LIVE
    assert [path for path in paths if path.startswith("/sapi")] == [PERMISSIONS_PATH]
    assert ("GET", PERMISSIONS_PATH) in ALLOWED_LIVE
    assert not any("withdraw" in path or "margin" in path or "futures" in path
                   or "transfer" in path for path in paths)
    trader = LiveTrader(KEY, opener=lambda *a, **k: pytest.fail("istek gönderilmemeli"))
    for method, path in (("DELETE", "/api/v3/openOrders"),
                         ("POST", "/sapi/v1/capital/withdraw/apply"),
                         ("POST", "/sapi/v1/margin/loan")):
        with pytest.raises(TradingRefused):
            trader.request(method, path, {})
    # Demo istemcisi anahtar izinlerini okuyan ucu bile çağıramaz.
    with pytest.raises(TradingRefused):
        DemoTrader(KEY, opener=lambda *a, **k: None).request("GET", PERMISSIONS_PATH)


def test_izinler_okunmadan_giris_emri_gonderilmez():
    clock = Clock()
    fake = FakeBinance(clock_ms=clock.ms, public_pem=PUBLIC_PEM, api_key=API_KEY)
    trader = _trader(fake, clock)
    with pytest.raises(TradingRefused, match="henüz okunmadı"):
        trader.place_otoco(_otoco())
    assert not fake.requests


def test_izinler_bir_saatten_eskiyse_giris_kapanir():
    clock = Clock()
    fake = FakeBinance(clock_ms=clock.ms, public_pem=PUBLIC_PEM, api_key=API_KEY)
    trader = _trader(fake, clock)
    assert trader.verify_permissions().tamam
    assert trader.entry_block_reason() is None
    clock.advance(3601)
    assert "bir saatten" in (trader.entry_block_reason() or "")
    with pytest.raises(TradingRefused):
        trader.place_otoco(_otoco())
    assert not fake.posts()


def test_izin_okuma_hatasi_onceki_durumu_korur_ama_sure_dolunca_kapanir():
    clock = Clock()
    fake = FakeBinance(clock_ms=clock.ms, public_pem=PUBLIC_PEM, api_key=API_KEY)
    trader = _trader(fake, clock)
    trader.verify_permissions()
    fake.fail_next("GET", PERMISSIONS_PATH, "500_once")
    with pytest.raises(Exception):  # noqa: B017 - sınıflandırma ayrı sınanıyor
        trader.verify_permissions()
    state = trader.permissions()
    assert state.tamam and state.hata
    clock.advance(3601)
    assert trader.entry_block_reason() is not None


def test_tavan_imzalayan_sinifta_da_sinanir():
    clock = Clock()
    fake = FakeBinance(clock_ms=clock.ms, public_pem=PUBLIC_PEM, api_key=API_KEY)
    trader = _trader(fake, clock, entry_cap_usdt=lambda: D("10"))
    trader.verify_permissions()
    with pytest.raises(TradingRefused, match="tavanını"):
        trader.place_otoco(_otoco("0.001"))  # 60 USDT
    assert not fake.posts()
    trader.place_otoco(_otoco("0.00016"))  # 9,6 USDT
    assert len(fake.posts("/api/v3/orderList/otoco")) == 1


# --- anahtar izinlerinin yorumu --------------------------------------------------------------


def _restrictions(**changes: Any) -> dict[str, Any]:
    fake = FakeBinance(clock_ms=lambda: 0, public_pem=PUBLIC_PEM, api_key=API_KEY)
    return {**fake.restrictions, **changes}


@pytest.mark.parametrize(("key", "text"), [
    ("enableWithdrawals", "Para çekme izni AÇIK"),
    ("enableFutures", "Spot kullanır"),
    ("enableMargin", "Spot kullanır"),
    ("enableVanillaOptions", "Spot kullanır"),
    ("permitsUniversalTransfer", "Spot kullanır"),
    ("enableInternalTransfer", "Spot kullanır"),
])
def test_riskli_izin_acik_anahtar_engellenir(key, text):
    blocking, _ = live_key_problems(_restrictions(**{key: True}))
    assert blocking and text in " ".join(blocking)


def test_izin_yaniti_eksik_ya_da_islem_kapaliysa_engellenir_ip_kisiti_uyaridir():
    payload = _restrictions()
    del payload["enableWithdrawals"]
    assert live_key_problems(payload)[0]
    assert live_key_problems(_restrictions(enableSpotAndMarginTrading=False))[0]
    assert live_key_problems(_restrictions(enableReading=False))[0]
    blocking, warnings = live_key_problems(_restrictions(ipRestrict=False))
    assert not blocking and "IP kısıtlaması yok" in warnings[0]
    assert live_key_problems(_restrictions(ipRestrict=True)) == ([], [])


# --- canlı yürütücü --------------------------------------------------------------------------


def test_cekim_izni_acik_anahtarla_canli_hazir_olmaz_ve_mod_secilemez(tmp_path):
    rig = live(tmp_path, bootstrap=False)
    rig.fake.restrictions["enableWithdrawals"] = True
    assert not rig.executor.bootstrap()
    assert not rig.executor.bootstrap()  # 30 sn'de bir yeniden denenir
    reason = rig.executor.not_ready_reason() or ""
    assert "Para çekme izni AÇIK" in reason
    with pytest.raises(ModeError, match="Para çekme"):
        rig.engine.set_mode(BTC, MODE_SEMI, source=SOURCE_UI, now=rig.clock.now(),
                            allow_live=True)
    assert not rig.fake.posts()
    # Aynı sorun bir kez bildirilir.
    assert texts(rig).count("kullanılamıyor") == 1
    # Binance'te düzeltildi: bir sonraki denemede hazır.
    rig.fake.restrictions["enableWithdrawals"] = False
    assert rig.executor.bootstrap()
    assert rig.executor.not_ready_reason() is None


def test_hesabin_canwithdraw_bayragi_anahtar_izni_sayilmaz(tmp_path):
    rig = live(tmp_path, bootstrap=False)
    rig.fake.can_withdraw = True  # hesabın bayrağı; anahtarın izni apiRestrictions'ta
    assert rig.executor.bootstrap()
    assert rig.executor.not_ready_reason() is None


def test_islem_kapali_canli_hesap_reddedilir(tmp_path):
    rig = live(tmp_path, bootstrap=False)
    rig.fake.can_trade = False
    assert not rig.executor.bootstrap()
    assert "işlem yapamıyor" in (rig.executor.not_ready_reason() or "")


def test_izin_sonradan_bozulursa_canli_modlar_kapanir_bekleyen_giris_iptal_edilir(tmp_path):
    rig = live(tmp_path)
    position = placed(rig)
    rig.fake.restrictions["enableWithdrawals"] = True
    rig.executor._last_permission_check = -1e18  # yarım saat doldu
    rig.tick()
    assert rig.engine.modes.get(BTC) == MODE_ADVICE
    assert "CANLI İŞLEM DURDU" in texts(rig)
    rig.tick(2)
    assert rig.position(position.id).durum == POS_CANCELLED
    assert rig.fake.order(f"{LIVE_PREFIX}{position.jeton}-G1").status == "CANCELED"
    with pytest.raises(ModeError):
        rig.engine.set_mode(BTC, MODE_SEMI, source=SOURCE_UI, now=rig.clock.now(),
                            allow_live=True)
    # Düzeldi: kilit kalkar, mod elle yeniden seçilir (kendiliğinden açılmaz).
    rig.fake.restrictions["enableWithdrawals"] = False
    rig.executor._last_permission_check = -1e18
    rig.tick()
    assert rig.executor.not_ready_reason() is None
    assert rig.engine.modes.get(BTC) == MODE_ADVICE
    assert "yeniden uygun" in texts(rig)


def test_izin_bozukken_koruma_emirleri_calismaya_devam_eder(tmp_path):
    rig = live(tmp_path)
    position = placed(rig)
    fill_entry(rig, position.id)
    rig.tick()
    assert rig.position(position.id).durum == POS_PROTECTED
    rig.fake.restrictions["enableWithdrawals"] = True
    rig.executor._last_permission_check = -1e18
    rig.tick()
    # Borsa stopu kendiliğinden bitirdi: yeni koruma (OCO) yine kurulur.
    rig.fake.expire(f"{LIVE_PREFIX}{position.jeton}-S1")
    rig.tick(2)
    assert rig.position(position.id).durum == POS_PROTECTED
    assert len(rig.fake.posts("/api/v3/orderList/oco")) == 1
    # Yeni giriş ise gönderilmez.
    rig.engine.modes.set(BTC, MODE_SEMI)  # kilidi atlayan bir hata olsa bile
    before = len(rig.fake.posts())
    result = rig.place()
    assert result.pozisyon is None
    assert len(rig.fake.posts()) == before


def test_varsayilan_tavan_config_ile_ayni_ve_emri_boyutlar(tmp_path):
    text = (ROOT / "config" / "default.yaml").read_text(encoding="utf-8")
    assert f'emir_tavani_usdt: "{DEFAULT_CAP_USDT}"' in text
    rig = live(tmp_path)
    default_cap(rig)
    assert rig.executor.cap_usdt() == DEFAULT_CAP_USDT == D("10")
    position = placed(rig)
    assert D(position.tutar_usdt) <= D("10")
    sent = rig.fake.posts("/api/v3/orderList/otoco")[0]
    assert D(sent["workingPrice"]) * D(sent["workingQuantity"]) <= D("10")
    gates = {gate.ad: gate for gate in rig.executor.preview(
        __import__("test_kagit_islem").intent(), market=market(rig.clock.now()),
        now=rig.clock.now()).karar.kapilar}
    assert gates["canli_tavan"].gecti and gates["canli_izin"].gecti


def test_istenen_tutar_tavanin_altinda_kalir(tmp_path):
    rig = live(tmp_path)
    default_cap(rig)
    now = rig.clock.now()
    from test_kagit_islem import intent

    result = rig.executor.place(intent(kaynak=SOURCE_MANUAL, kural_kimligi=None),
                                market=market(now), now=now, amount_usdt=D("6"))
    assert result.pozisyon is not None, result.mesaj
    assert D(result.pozisyon.tutar_usdt) <= D("6")
    # Tavandan büyük tutar istense de tavan bağlar.
    assert rig.executor.entry_cap(D("50")) == D("10")


@pytest.mark.parametrize("value", ["0", "-1", "abc", "101", "NaN"])
def test_tavan_dogrulanir(tmp_path, value):
    rig = live(tmp_path)
    with pytest.raises(CapError):
        rig.executor.update_cap(value, source=SOURCE_UI)


def test_tavan_degisimi_denetime_yazilir(tmp_path):
    rig = live(tmp_path)
    assert rig.executor.update_cap("25", source=SOURCE_UI) == D("25.00")
    assert rig.executor.cap_usdt() == D("25.00")
    assert rig.engine.audit.recent(1)[0].tur == "canli_tavan"


# --- mod kilidi ve canlıya geçiş kapısı ---------------------------------------------------------


def test_canli_modlar_yalnizca_canli_sekmesinden_ve_hazirken_acilir(tmp_path):
    rig = live(tmp_path)
    rig.engine.set_mode(BTC, MODE_ADVICE, source=SOURCE_UI, now=rig.clock.now())
    with pytest.raises(ModeError, match="Canlı işlem"):
        rig.engine.set_mode(BTC, MODE_SEMI, source=SOURCE_UI, now=rig.clock.now())
    rig.stream.drop()
    with pytest.raises(ModeError, match="akışına bağlı değil"):
        rig.engine.set_mode(BTC, MODE_SEMI, source=SOURCE_UI, now=rig.clock.now(),
                            allow_live=True)
    rig.stream.reconnect()
    rig.engine.set_mode(BTC, MODE_SEMI, source=SOURCE_UI, now=rig.clock.now(),
                        allow_live=True)
    assert rig.engine.modes.get(BTC) == MODE_SEMI
    # Canlı moddan çıkmak her yerden serbest.
    rig.engine.set_mode(BTC, MODE_PAPER, source=SOURCE_UI, now=rig.clock.now())
    assert rig.engine.modes.get(BTC) == MODE_PAPER


def test_tam_otomatik_kabul_edilmis_kural_yokken_acilamaz(tmp_path):
    rig = live(tmp_path)
    with pytest.raises(ModeError, match="Tam Otomatik açılamaz") as caught:
        rig.engine.set_mode(BTC, MODE_FULL, source=SOURCE_UI, now=rig.clock.now(),
                            allow_live=True)
    assert "kabul edilmiş kural yok" in str(caught.value)
    assert rig.engine.modes.get(BTC) == MODE_SEMI


def test_acil_durdur_canli_modlari_indirir_ve_bekleyen_girisi_iptal_eder(tmp_path):
    rig = live(tmp_path)
    position = placed(rig)
    rig.engine.kill_switch(close_positions=False, marks={}, source=SOURCE_UI,
                           now=rig.clock.now())
    rig.executor.kill_switch(close_positions=False, source=SOURCE_UI)
    rig.tick()
    assert rig.engine.modes.get(BTC) == MODE_ADVICE
    assert rig.position(position.id).durum == POS_CANCELLED


def _rule(rule_id: str = "BTCUSDT|15m|r1") -> SimpleNamespace:
    return SimpleNamespace(kimlik=rule_id, etiket="Örnek kural", sembol=BTC, periyot="15m")


def _ruleset(*rules: SimpleNamespace, teshis: bool = False) -> SimpleNamespace:
    return SimpleNamespace(kosu=SimpleNamespace(teshis_turu=teshis), kurallar=list(rules))


def _paper(rule_id: str, count: int, *, first_days_ago: float, now: Any,
           source: str = SOURCE_RULE) -> list[SimpleNamespace]:
    first = now - timedelta(days=first_days_ago)
    return [SimpleNamespace(kaynak=source, kural_kimligi=rule_id, durum=STATUS_CLOSED,
                            olusturma_utc=iso(first + timedelta(hours=i)),
                            kural_etiketi="Örnek kural") for i in range(count)]


def _check(rule_id: str, *, mean: float | None = 0.2, broken: bool = False) -> SimpleNamespace:
    return SimpleNamespace(kural_kimligi=rule_id, gerceklesen_yuzde=mean, beklenen_yuzde=0.25,
                           bozuk=broken, aciklama="sınama")


def test_gecis_kapisi_butun_kosullar_saglaninca_gecer():
    now = Clock().now()
    rid = "BTCUSDT|15m|r1"
    gates = rule_gates(ruleset=_ruleset(_rule(rid)),  # type: ignore[arg-type]
                       paper_orders=_paper(rid, 30, first_days_ago=7.5, now=now),  # type: ignore[arg-type]
                       checks=[_check(rid)], disabled=[], now=now)  # type: ignore[list-item]
    assert len(gates) == 1 and gates[0].gecti
    assert passed_for(BTC, gates) == gates
    assert "kapısını geçti" in symbol_summary(BTC, gates)


@pytest.mark.parametrize(("case", "failing"), [
    ("gun", "gun"), ("islem", "islem"), ("uyum", "uyum"), ("negatif", "pozitif"),
    ("durduruldu", "durdurulmadi"), ("kabul_yok", "kabul"), ("elle", "islem"),
    ("teshis", "kabul"),
])
def test_gecis_kapisi_her_kosulu_ayri_sinar(case, failing):
    now = Clock().now()
    rid = "BTCUSDT|15m|r1"
    ruleset = _ruleset(_rule(rid), teshis=case == "teshis")
    orders = _paper(rid, 30, first_days_ago=7.5, now=now,
                    source=SOURCE_MANUAL if case == "elle" else SOURCE_RULE)
    checks = [_check(rid)]
    disabled: list[str] = []
    if case == "gun":
        orders = _paper(rid, 30, first_days_ago=3, now=now)
    elif case == "islem":
        orders = orders[:29]
    elif case == "uyum":
        checks = [_check(rid, broken=True)]
    elif case == "negatif":
        checks = [_check(rid, mean=-0.1)]
    elif case == "durduruldu":
        disabled = [rid]
    elif case == "kabul_yok":
        ruleset = _ruleset()
    gates = rule_gates(ruleset=ruleset, paper_orders=orders,  # type: ignore[arg-type]
                       checks=checks, disabled=disabled, now=now)  # type: ignore[arg-type]
    gate = next(item for item in gates if item.kural_kimligi == rid) if gates else None
    if gate is None:  # elle işlemler hiç sayılmaz ve kural da yok: kapı listesi boş
        assert case == "teshis" or case == "kabul_yok"
        assert not passed_for(BTC, gates)
        return
    assert not gate.gecti
    assert failing in {item.ad for item in gate.kosullar if not item.gecti}
    assert not passed_for(BTC, gates)


def test_kapi_gecilince_tam_otomatik_acilir_gecen_kural_emir_acar(tmp_path):
    rig = live(tmp_path)
    executor = rig.executor
    assert isinstance(executor, LiveExecutor)
    rid = "BTCUSDT|15m|r1"
    now = rig.clock.now()
    executor.ruleset_loader = lambda: _ruleset(_rule(rid))  # type: ignore[assignment,return-value]
    rig.engine.ledger.all_closed = lambda: _paper(rid, 30, first_days_ago=8, now=now)  # type: ignore[method-assign,assignment,return-value]
    rig.engine.rule_checks = lambda now: [_check(rid)]  # type: ignore[method-assign,assignment,return-value]
    rig.engine.set_mode(BTC, MODE_FULL, source=SOURCE_UI, now=now, allow_live=True)
    assert rig.engine.modes.get(BTC) == MODE_FULL
    executor.live_market.market_state = lambda *a: market(rig.clock.now())  # type: ignore[method-assign]
    # Kapıyı geçmeyen kuralın sinyali emre dönüşmez.
    executor.place_from_card(_card(rig, "BTCUSDT|15m|baska"), _card_rule())
    assert not rig.fake.posts()
    assert "kapısını geçmediği" in texts(rig)
    result = executor.place_from_card(_card(rig, rid), _card_rule())
    assert result is not None and result.pozisyon is not None, result and result.mesaj
    assert len(rig.fake.posts("/api/v3/orderList/otoco")) == 1


# --- Yarı Otomatik önerileri -------------------------------------------------------------------


def _card(rig: Rig, rule_id: str = "BTCUSDT|15m|r1") -> SimpleNamespace:
    return SimpleNamespace(
        sembol=BTC, periyot="15m", giris=D("60000"), hedef1=D("60900"), stop=D("59400"),
        kural_kimligi=rule_id, kural_etiketi="Örnek kural",
        sinyal_mumu_kapanis_utc=iso(rig.clock.now() - timedelta(seconds=5)),
        gecerlilik_mum=1, net_marj_yuzde=D("1.2"))


def _card_rule() -> SimpleNamespace:
    return SimpleNamespace(pencere_mum=4, kanit=SimpleNamespace(test_donemi_net_yuzde=D("0.3")))


def _semi(tmp_path: Path) -> tuple[Rig, LiveExecutor]:
    rig = live(tmp_path)
    executor = rig.executor
    assert isinstance(executor, LiveExecutor)
    executor.live_market.market_state = lambda *a: market(rig.clock.now())  # type: ignore[method-assign]
    return rig, executor


def test_yari_otomatikte_sinyal_oneri_olur_onaysiz_emir_gitmez(tmp_path):
    rig, executor = _semi(tmp_path)
    assert executor.place_from_card(_card(rig), _card_rule()) is None
    assert not rig.fake.posts()
    [proposal] = executor.waiting()
    assert proposal.durum == PROPOSAL_WAITING
    assert "CANLI ÖNERİ" in texts(rig) and f"/onayla {proposal.kimlik}" in texts(rig)
    result = executor.approve(proposal.kimlik, source=SOURCE_UI)
    assert result.pozisyon is not None and result.pozisyon.durum == POS_PENDING, result.mesaj
    assert len(rig.fake.posts("/api/v3/orderList/otoco")) == 1
    assert executor.proposals()[-1].durum == PROPOSAL_SENT
    with pytest.raises(ProposalError, match="artık onay beklemiyor"):
        executor.approve(proposal.kimlik, source=SOURCE_UI)
    assert len(rig.fake.posts("/api/v3/orderList/otoco")) == 1


def test_oneri_reddedilir_suresi_dolar_mod_degisince_iptal_olur(tmp_path):
    rig, executor = _semi(tmp_path)
    executor.place_from_card(_card(rig), _card_rule())
    first = executor.waiting()[0]
    assert executor.reject(first.kimlik, source=SOURCE_UI).durum == PROPOSAL_REJECTED
    executor.place_from_card(_card(rig), _card_rule())
    second = executor.waiting()[0]
    rig.clock.advance(15 * 60)
    rig.tick()
    assert executor._find(second.kimlik).durum == PROPOSAL_EXPIRED
    with pytest.raises(ProposalError):
        executor.approve(second.kimlik, source=SOURCE_UI)
    executor.place_from_card(_card(rig), _card_rule())
    third = executor.waiting()[0]
    rig.engine.set_mode(BTC, MODE_ADVICE, source=SOURCE_UI, now=rig.clock.now())
    rig.tick()
    assert executor._find(third.kimlik).durum == PROPOSAL_CANCELLED
    assert not rig.fake.posts()


def test_telegram_onayla_reddet_durum_ve_durdur_canliyi_kapsar(tmp_path):
    rig, executor = _semi(tmp_path)
    handler = build_handler(rig.engine, marks=lambda: {}, connection=lambda: None,
                            clock=rig.clock.now, live=executor)
    assert "Onay bekleyen canlı öneri yok" in handler("/onayla", "")
    executor.place_from_card(_card(rig), _card_rule())
    proposal = executor.waiting()[0]
    listing = handler("/onayla", "")
    assert proposal.kimlik in listing and "GERÇEK PARA" in listing
    assert "Onay bekleyen canlı öneri" in handler("/durum", "")
    answer = handler("/onayla", proposal.kimlik)
    assert answer.startswith("✅"), answer
    assert executor._find(proposal.kimlik).karar_kaynagi == SOURCE_TELEGRAM
    assert "yok" in handler("/reddet", "zzzzzz").lower() or "Reddedilemedi" in handler(
        "/reddet", "zzzzzz")
    status = handler("/durum", "")
    assert "Canlı hesap (GERÇEK PARA): hazır" in status and "CANLI:" in status
    stopped = handler("/durdur", "")
    assert "Canlı hesaptaki bekleyen girişler" in stopped
    rig.tick()
    assert rig.engine.modes.get(BTC) == MODE_ADVICE
    assert not rig.executor.ledger.active() or all(
        item.durum == POS_CANCELLED for item in rig.executor.ledger.recent(5))


# --- arayüz uçları ---------------------------------------------------------------------------


class _Runner:
    def __init__(self, rig: Rig) -> None:
        self.rig = rig

    def marks(self) -> dict[str, Decimal]:
        return {BTC: self.rig.fake.book[BTC][0]}

    def status(self) -> dict[str, object]:
        return {"akis_bagli": True}


class _Market:
    def __init__(self, rig: Rig) -> None:
        self.rig = rig

    def market_state(self, sembol, periyot, rules):  # noqa: ANN001, ANN201
        return market(self.rig.clock.now())

    def quote(self, sembol):  # noqa: ANN001, ANN201
        return self.rig.executor.venue_market.quote(sembol)

    def bid(self, sembol):  # noqa: ANN001, ANN201
        return self.rig.fake.book[BTC][0]

    def usdttry(self) -> None:
        return None


def _client(rig: Rig) -> TestClient:
    runtime = Runtime(root=rig.root, symbols=(BTC,), periods=("15m", "1h"),
                      notifier=rig.notifier, engine=rig.engine,
                      market=_Market(rig),  # type: ignore[arg-type]
                      runner=_Runner(rig),  # type: ignore[arg-type]
                      live=rig.executor,  # type: ignore[arg-type]
                      telegram_note="yok")
    state = AppState(veri_dizini=rig.root, semboller=(BTC,), periyotlar=("15m", "1h"),
                     runtime=runtime)
    return TestClient(create_app(state), base_url="http://127.0.0.1")


def test_canli_durumu_gercek_para_izinler_tavan_ve_kapiyi_gosterir(tmp_path, monkeypatch):
    rig = live(tmp_path)
    monkeypatch.setattr("albsat.api.live_api.utc_now", rig.clock.now)
    client = _client(rig)
    before = len(rig.fake.requests)
    status = client.get("/api/canli/durum").json()
    assert len(rig.fake.requests) == before  # arayüz yenilemesi borsaya istek atmaz
    assert status["baglanti"]["gercek_para"] is True
    assert "GERÇEK PARA" in status["baglanti"]["ortam"] and "GERÇEK PARA" in status["uyari"]
    assert status["baglanti"]["izinler"]["cekim_izni"] is False
    assert status["baglanti"]["izinler"]["tamam"] is True
    coin = status["gecis_kapisi"]["coinler"][0]
    assert coin["tam_otomatik_acilabilir"] is False
    assert "Tam Otomatik açılamaz" in coin["ozet"]
    health = client.get("/api/saglik").json()
    assert "CANLI" in health["emir_yetkisi"] and health["canli_emir"] is True
    summary = client.get("/api/canli/tam-otomatik-ozet", params={"sembol": BTC}).json()
    assert summary["acilabilir"] is False and summary["butce_usdt"] == "100"


def test_canli_mod_ucu_coin_adini_ister_tam_otomatik_kapida_kalir(tmp_path, monkeypatch):
    rig = live(tmp_path)
    monkeypatch.setattr("albsat.api.live_api.utc_now", rig.clock.now)
    client = _client(rig)
    client.post("/api/canli/mod", json={"sembol": BTC, "mod": "sadece_oneri"}, headers=YAZ)
    assert rig.engine.modes.get(BTC) == MODE_ADVICE
    for typed in ("", "SOLUSDT", "evet"):
        answer = client.post("/api/canli/mod", json={"sembol": BTC, "mod": MODE_SEMI,
                                                     "onay": typed}, headers=YAZ)
        assert answer.status_code == 400 and "coin adını" in answer.json()["detail"]
    assert rig.engine.modes.get(BTC) == MODE_ADVICE
    ok = client.post("/api/canli/mod", json={"sembol": BTC, "mod": MODE_SEMI, "onay": "btcusdt"},
                     headers=YAZ)
    assert ok.status_code == 200 and rig.engine.modes.get(BTC) == MODE_SEMI
    full = client.post("/api/canli/mod", json={"sembol": BTC, "mod": MODE_FULL, "onay": BTC},
                       headers=YAZ)
    assert full.status_code == 400 and "Tam Otomatik açılamaz" in full.json()["detail"]
    other = client.post("/api/canli/mod", json={"sembol": BTC, "mod": "demo", "onay": BTC},
                        headers=YAZ)
    assert other.status_code == 400
    # Kâğıt sekmesinin ucu canlı modu açamaz (ama canlı moddan çıkarabilir).
    down = client.post("/api/kagit/mod", json={"sembol": BTC, "mod": "sadece_oneri"},
                       headers=YAZ)
    assert down.status_code == 200 and rig.engine.modes.get(BTC) == MODE_ADVICE
    paper = client.post("/api/kagit/mod", json={"sembol": BTC, "mod": MODE_SEMI}, headers=YAZ)
    assert paper.status_code == 400 and rig.engine.modes.get(BTC) == MODE_ADVICE


def test_elle_canli_emir_onay_ve_tavan_ister(tmp_path, monkeypatch):
    rig = live(tmp_path)
    default_cap(rig)
    monkeypatch.setattr("albsat.api.live_api.utc_now", rig.clock.now)
    client = _client(rig)
    body = {"sembol": BTC, "periyot": "15m", "giris": "60000", "hedef": "60900",
            "stop": "59400"}
    preview = client.get("/api/canli/on-izleme", params={**body, "tutar_usdt": "6"}).json()
    assert preview["karar"]["izin"] is True, preview["karar"]["ozet"]
    assert D(preview["karar"]["pozisyon"]["tutar_usdt"]) <= D("6")
    # Bağlayan tavan: kullanıcıya "bütçe" değil "tutar sınırı" yazılır, risk yüzdesi
    # 6 USDT'ye göre değil bot bütçesine göre kalır.
    assert preview["karar"]["pozisyon"]["baglayici"] == "tavan"
    assert preview["karar"]["pozisyon"]["baglayici_tr"] == "tutar sınırı"
    size_text = next(gate["aciklama"] for gate in preview["karar"]["kapilar"]
                     if gate["ad"] == "boyut")
    assert "en fazla 6 USDT" in size_text and "1.00 USDT'ye izin" in size_text, size_text
    assert not rig.fake.posts()
    assert client.post("/api/canli/emir", json={**body, "onay": BTC}).status_code == 403
    wrong = client.post("/api/canli/emir", json={**body, "onay": "SOLUSDT"}, headers=YAZ)
    assert wrong.status_code == 400
    over = client.post("/api/canli/emir", json={**body, "onay": BTC, "tutar_usdt": "11"},
                       headers=YAZ)
    assert over.status_code == 400 and "tavanını" in over.json()["detail"]
    assert not rig.fake.posts()
    placed_ = client.post("/api/canli/emir", json={**body, "onay": BTC, "tutar_usdt": "6"},
                          headers=YAZ).json()
    assert placed_["pozisyon"]["durum"] == "bekliyor", placed_
    sent = rig.fake.posts("/api/v3/orderList/otoco")
    assert len(sent) == 1 and sent[0]["workingClientOrderId"].startswith(LIVE_PREFIX)
    assert D(sent[0]["workingPrice"]) * D(sent[0]["workingQuantity"]) <= D("6")
    cancelled = client.post("/api/canli/iptal", json={"id": placed_["pozisyon"]["id"]},
                            headers=YAZ)
    assert cancelled.status_code == 200
    rig.tick()
    assert rig.position(placed_["pozisyon"]["id"]).durum == POS_CANCELLED
    cap = client.post("/api/canli/tavan", json={"tutar_usdt": "500"}, headers=YAZ)
    assert cap.status_code == 400


def test_arayuzden_oneri_gonderme_ve_reddetme(tmp_path, monkeypatch):
    rig, executor = _semi(tmp_path)
    monkeypatch.setattr("albsat.api.live_api.utc_now", rig.clock.now)
    client = _client(rig)
    executor.place_from_card(_card(rig), _card_rule())
    executor.place_from_card(_card(rig), _card_rule())
    waiting = client.get("/api/canli/durum").json()["bekleyen_oneriler"]
    assert len(waiting) == 2
    rejected = client.post("/api/canli/oneri-reddet", json={"kimlik": waiting[0]["kimlik"]},
                           headers=YAZ)
    assert rejected.json()["oneri"]["durum"] == PROPOSAL_REJECTED
    sent = client.post("/api/canli/oneri-gonder", json={"kimlik": waiting[1]["kimlik"]},
                       headers=YAZ).json()
    assert sent["pozisyon"]["durum"] == "bekliyor", sent
    again = client.post("/api/canli/oneri-gonder", json={"kimlik": waiting[1]["kimlik"]},
                        headers=YAZ)
    assert again.status_code == 409


def test_arayuzdeki_acil_durdur_canli_girisi_iptal_eder(tmp_path, monkeypatch):
    rig = live(tmp_path)
    monkeypatch.setattr("albsat.api.live_api.utc_now", rig.clock.now)
    client = _client(rig)
    position = placed(rig)
    result = client.post("/api/kagit/acil-durdur", json={}, headers=YAZ).json()
    assert result["canli_kapatilan"] == 0
    rig.tick()
    assert rig.position(position.id).durum == POS_CANCELLED
    assert rig.engine.modes.get(BTC) == MODE_ADVICE


def test_cevrimdisi_calistirmada_canli_kullanilamaz(tmp_path):
    runtime = Runtime.build(tmp_path, symbols=(BTC,), periods=("15m",), online=False,
                            use_telegram=False)
    assert runtime.live is not None and runtime.live.trader is None
    assert runtime.live.not_ready_reason() == LIVE_OFFLINE
    state = AppState(veri_dizini=tmp_path, semboller=(BTC,), periyotlar=("15m",),
                     runtime=runtime)
    client = TestClient(create_app(state), base_url="http://127.0.0.1")
    status = client.get("/api/canli/durum").json()
    assert status["baglanti"]["kurulu"] is False
    answer = client.post("/api/canli/mod", json={"sembol": BTC, "mod": MODE_SEMI, "onay": BTC},
                         headers=YAZ)
    assert answer.status_code == 409
    assert "yok" in client.get("/api/saglik").json()["emir_yetkisi"]


# --- kurulum -----------------------------------------------------------------------------------


def _engine(root: Path) -> PaperEngine:
    from test_kagit_islem import RULES

    engine = PaperEngine(root, symbols=(BTC,), notifier=MemoryNotifier(),
                         costs_for=lambda s: None, rules_for=lambda s: RULES)  # type: ignore[arg-type,return-value]
    engine.modes.start_session()
    return engine


def test_canli_yurutucu_canli_adreslerle_ve_ortak_istek_butcesiyle_kurulur(tmp_path):
    engine = _engine(tmp_path)
    budget = RequestBudget()
    executor = build_live_executor(tmp_path, symbols=(BTC,), engine=engine,
                                   live_market=LiveMarket(KlineStore(tmp_path)),
                                   notifier=engine.notifier, online=True, budget=budget,
                                   key_loader=lambda: KEY)
    assert isinstance(executor.trader, LiveTrader)
    assert executor.trader.base == "https://api.binance.com"
    assert executor.trader.budget is budget and executor.public.budget is budget
    assert executor.stream.url == "wss://ws-api.binance.com/ws-api/v3"
    assert executor.book_stream is None
    assert executor.venue_market is executor.live_market
    assert "hazırlanıyor" in (executor.not_ready_reason() or "")
    missing = build_live_executor(tmp_path, symbols=(BTC,), engine=_engine(tmp_path),
                                  live_market=LiveMarket(KlineStore(tmp_path)),
                                  notifier=engine.notifier, online=True, budget=budget,
                                  key_loader=lambda: None)
    assert "canli-anahtar" in (missing.not_ready_reason() or "")


def test_canli_kayitlari_demodan_ayri_tablolarda(tmp_path):
    demo = build(tmp_path)
    placed(demo)
    canli = build(tmp_path, hesap="canli", clock=demo.clock)
    assert canli.executor.ledger.active() == ()
    assert len(demo.executor.ledger.active()) == 1


# --- komut satırı (bash kurulum.sh canli-anahtar / canli-sina) --------------------------------


class _Keychain:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str], str] = {}

    def read(self, service: str, account: str) -> SecretText | None:
        value = self.items.get((service, account))
        return None if value is None else SecretText(value)

    def write(self, service: str, account: str, secret: SecretText) -> None:
        self.items[(service, account)] = secret.reveal()

    def delete(self, service: str, account: str) -> bool:
        return self.items.pop((service, account), None) is not None


@pytest.fixture
def chain(monkeypatch) -> _Keychain:
    store = _Keychain()
    for name in ("read", "write", "delete"):
        monkeypatch.setattr(cli.keychain, name, getattr(store, name))
    return store


def _exchange() -> tuple[Clock, FakeBinance, LiveTrader]:
    clock = Clock()
    fake = FakeBinance(clock_ms=clock.ms, public_pem=PUBLIC_PEM, api_key=API_KEY)
    trader = LiveTrader(KEY, opener=fake, time_ms=clock.local_ms,
                        entry_cap_usdt=lambda: DEFAULT_CAP_USDT)
    return clock, fake, trader


def _stream_factory(fake: FakeBinance):  # noqa: ANN202
    def factory(**kwargs: Any) -> FakeUserStream:
        return FakeUserStream(fake, kwargs["on_event"], None, kwargs.get("server_time_ms"))

    return factory


def test_canli_anahtar_kurulumu_ayri_kayda_yazar_ve_izinleri_anlatir(chain):
    said: list[str] = []
    key = cli.setup(ask=lambda _: "", secret=lambda _: API_KEY, say=said.append)
    assert key is not None
    text = "\n".join(said)
    assert "BEGIN PUBLIC KEY" in text and cli.API_PAGE in text
    assert "Para çekme" in text and "KAPALI" in text and "IP kısıtlaması" in text
    private = chain.items[(KEYCHAIN_SERVICE_LIVE, "ed25519-private")]
    assert private not in text and API_KEY not in text
    assert all(service == KEYCHAIN_SERVICE_LIVE for service, _ in chain.items)


def test_canli_dogrulama_yalnizca_okur(tmp_path):
    _, fake, trader = _exchange()
    said: list[str] = []
    assert cli.verify(trader, tmp_path, say=said.append) == 0, "\n".join(said)
    assert not fake.posts()
    text = "\n".join(said)
    assert "Para çekme       : kapalı" in text and "hiçbir emir göndermedi" in text
    measured = CommissionStore(tmp_path).read()
    assert measured is not None and set(measured.tablolar) == {"BTCUSDT", "SOLUSDT"}


def test_canli_dogrulama_cekim_izni_acik_anahtari_reddeder(tmp_path):
    _, fake, trader = _exchange()
    fake.restrictions["enableWithdrawals"] = True
    said: list[str] = []
    assert cli.verify(trader, tmp_path, say=said.append) == 1
    assert any("Para çekme izni AÇIK" in line for line in said)


def test_canli_sinama_coin_adi_yazilmadan_emir_gondermez(tmp_path):
    _, fake, trader = _exchange()
    said: list[str] = []
    for typed in ("e", "evet", "SOLUSDT", ""):
        result = cli.smoke(trader, fake, _stream_factory(fake), tmp_path, symbol=BTC,
                           ask=lambda _, t=typed: t, say=said.append, wait_seconds=1)
        assert result == 1
    assert not fake.posts()


def test_canli_sinama_emri_dolmaz_izlenir_ve_iptal_edilir(tmp_path):
    _, fake, trader = _exchange()
    said: list[str] = []
    result = cli.smoke(trader, fake, _stream_factory(fake), tmp_path, symbol=BTC,
                       ask=lambda _: "btcusdt", say=said.append, wait_seconds=2)
    assert result == 0, "\n".join(said)
    [params] = fake.posts("/api/v3/orderList/otoco")
    assert params["workingClientOrderId"].startswith(LIVE_PREFIX)
    assert D(params["workingPrice"]) * D(params["workingQuantity"]) <= DEFAULT_CAP_USDT
    assert fake.order(params["workingClientOrderId"]).status == "CANCELED"
    assert fake.trades == []


def test_canli_sinama_bakiye_yetmezse_gondermez(tmp_path):
    clock = Clock()
    fake = FakeBinance(clock_ms=clock.ms, public_pem=PUBLIC_PEM, api_key=API_KEY,
                       usdt=D("3"))
    trader = LiveTrader(KEY, opener=fake, time_ms=clock.local_ms)
    said: list[str] = []
    result = cli.smoke(trader, fake, _stream_factory(fake), tmp_path, symbol=BTC,
                       ask=lambda _: BTC, say=said.append, wait_seconds=1)
    assert result == 1 and not fake.posts()
    assert any("serbest USDT" in line for line in said)


def test_canli_komutu_mac_disinda_calismaz(monkeypatch, capsys):
    monkeypatch.setattr(cli.keychain, "available", lambda: False)
    assert cli.main(["--sina"]) == 1
    assert "yalnızca Mac" in capsys.readouterr().out
