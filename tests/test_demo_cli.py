"""Demo anahtar kurulumu ve uçtan uca Demo sınaması (``albsat.cli.demo``).

Borsa yerine ``fake_binance``; Anahtar Zinciri yerine bellekteki bir sözlük.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from fake_binance import FakeBinance, FakeUserStream
from test_demo_kaos import API_KEY, BTC, KEY, PUBLIC_PEM, Clock

from albsat.cli import demo as cli
from albsat.core.filters import SymbolRules
from albsat.data.commission import DEMO_FILENAME, CommissionStore
from albsat.exchange.keys import SecretText
from albsat.exchange.trading import CLIENT_PREFIX, KEYCHAIN_SERVICE_DEMO, DemoTrader


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


def _exchange() -> tuple[Clock, FakeBinance, DemoTrader]:
    clock = Clock()
    fake = FakeBinance(clock_ms=clock.ms, public_pem=PUBLIC_PEM, api_key=API_KEY)
    return clock, fake, DemoTrader(KEY, opener=fake, time_ms=clock.local_ms)


def _stream_factory(fake: FakeBinance):  # noqa: ANN202
    def factory(**kwargs: Any) -> FakeUserStream:
        return FakeUserStream(fake, kwargs["on_event"], None, kwargs.get("server_time_ms"))

    return factory


def test_anahtar_kurulumu_yalnizca_genel_yariyi_gosterir(chain):
    said: list[str] = []
    key = cli.setup(ask=lambda _: "", secret=lambda _: API_KEY, say=said.append)
    assert key is not None
    text = "\n".join(said)
    assert "BEGIN PUBLIC KEY" in text and cli.API_PAGE in text
    private = chain.items[(KEYCHAIN_SERVICE_DEMO, "ed25519-private")]
    assert private not in text and API_KEY not in text
    assert chain.items[(KEYCHAIN_SERVICE_DEMO, "api-key")] == API_KEY
    # Faz 4'ün salt okuma anahtarına dokunulmadı.
    assert all(service == KEYCHAIN_SERVICE_DEMO for service, _ in chain.items)


def test_anahtar_kurulumu_gecersiz_api_key_kaydetmez(chain):
    said: list[str] = []
    assert cli.setup(ask=lambda _: "", secret=lambda _: "kısa!", say=said.append) is None
    assert (KEYCHAIN_SERVICE_DEMO, "api-key") not in chain.items
    # Yarım kalan deneme: aynı çift yeniden kullanılır.
    kept = chain.items[(KEYCHAIN_SERVICE_DEMO, "ed25519-private")]
    cli.setup(ask=lambda _: "", secret=lambda _: API_KEY, say=said.append)
    assert chain.items[(KEYCHAIN_SERVICE_DEMO, "ed25519-private")] == kept
    assert any("Önceki denemeden" in line for line in said)


def test_dogrulama_izinleri_ve_komisyonu_okur(tmp_path):
    _, fake, trader = _exchange()
    said: list[str] = []
    assert cli.verify(trader, tmp_path, say=said.append) == 0
    assert not fake.posts()  # yalnızca okuma
    measured = CommissionStore(tmp_path, DEMO_FILENAME).read()
    assert measured is not None and set(measured.tablolar) == {"BTCUSDT", "SOLUSDT"}


def test_dogrulama_cekim_izni_acik_anahtari_reddeder(tmp_path):
    _, fake, trader = _exchange()
    fake.can_withdraw = True
    said: list[str] = []
    assert cli.verify(trader, tmp_path, say=said.append) == 1
    assert any("para çekme izni AÇIK" in line for line in said)


def test_sinama_emri_dolmaz_akistan_izlenir_ve_iptal_edilir(tmp_path):
    _, fake, trader = _exchange()
    said: list[str] = []
    result = cli.smoke(trader, fake, _stream_factory(fake), tmp_path, symbol=BTC,
                       ask=lambda _: "e", say=said.append, wait_seconds=2)
    assert result == 0, "\n".join(said)
    sent = fake.posts("/api/v3/orderList/otoco")
    assert len(sent) == 1
    params = sent[0]
    assert params["workingClientOrderId"].startswith(CLIENT_PREFIX)
    assert Decimal(params["workingPrice"]) < fake.book[BTC][0] * Decimal("0.96")
    order = fake.order(params["workingClientOrderId"])
    assert order.status == "CANCELED" and order.executed == 0
    assert not [item for item in fake.open_orders(BTC)]
    assert fake.trades == []


def test_sinama_emri_onay_verilmezse_gonderilmez(tmp_path):
    _, fake, trader = _exchange()
    said: list[str] = []
    result = cli.smoke(trader, fake, _stream_factory(fake), tmp_path, symbol=BTC,
                       ask=lambda _: "h", say=said.append, wait_seconds=1)
    assert result == 1
    assert not fake.posts()


def test_sinama_plani_borsa_kurallarina_uyar():
    from fake_binance import exchange_info

    rules = SymbolRules.from_exchange_info(exchange_info([BTC])["symbols"][0])
    plan = cli.probe_plan(rules, Decimal("60000"), Decimal("0.001"))
    assert plan.gecerli, plan.sorunlar
    notional = Decimal(plan.params["workingPrice"]) * Decimal(plan.params["workingQuantity"])
    assert rules.notional is not None and notional >= rules.notional.min_notional


def test_mac_disinda_calismaz(monkeypatch, capsys):
    monkeypatch.setattr(cli.keychain, "available", lambda: False)
    assert cli.main(["--sina"]) == 1
    assert "yalnızca Mac" in capsys.readouterr().out
