"""İmzalı okuma istekleri ve ölçülen komisyonun kullanımı.

Binance'e bağlanılmaz; imza, üretilen anahtarın genel yarısıyla doğrulanır.
"""
from __future__ import annotations

import base64
import io
import json
import re
import urllib.parse
from decimal import Decimal
from pathlib import Path

import pytest

from albsat.core.fees import Liquidity, Side
from albsat.data.commission import (
    DEFAULT_SLIPPAGE_PCT,
    CommissionStore,
    paper_costs,
    research_rates,
)
from albsat.exchange.keys import SecretText
from albsat.exchange.signed import (
    SignedReader,
    SignedRequestError,
    StoredKey,
    generate_keypair,
    public_pem_of,
    restriction_problems,
    sign,
)

API_KEY = "A" * 64
COMMISSION = {
    "symbol": "BTCUSDT",
    "standardCommission": {"maker": "0.00075000", "taker": "0.00090000",
                           "buyer": "0.00000000", "seller": "0.00000000"},
    "taxCommission": {"maker": "0.00000000", "taker": "0.00000000",
                      "buyer": "0.00000000", "seller": "0.00000000"},
    "specialCommission": {"maker": "0.00000000", "taker": "0.00000000",
                          "buyer": "0.00000000", "seller": "0.00000000"},
    "discount": {"enabledForAccount": True, "enabledForSymbol": True,
                 "discountAsset": "BNB", "discount": "0.75000000"},
}


class Recorder:
    def __init__(self, payload):
        self.payload = payload
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append(request)
        body = io.BytesIO(json.dumps(self.payload).encode())
        body.headers = {}
        body.__enter__ = lambda: body
        return _Ctx(body)


class _Ctx:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self.body

    def __exit__(self, *args):
        return False


def test_imza_genel_anahtarla_dogrulanir():
    from cryptography.hazmat.primitives.serialization import load_pem_public_key

    private, public_pem = generate_keypair()
    assert public_pem_of(private) == public_pem
    query = "symbol=BTCUSDT&recvWindow=5000&timestamp=1700000000000"
    signature = base64.b64decode(sign(query, private))
    load_pem_public_key(public_pem.encode()).verify(signature, query.encode())


def test_imzali_istek_anahtar_basligi_ve_imza_tasir():
    private, _ = generate_keypair()
    opener = Recorder(COMMISSION)
    reader = SignedReader(StoredKey(SecretText(API_KEY), private), opener=opener,
                          time_ms=lambda: 1_700_000_000_000)
    assert reader.commission("BTCUSDT")["symbol"] == "BTCUSDT"
    request = opener.requests[0]
    assert request.get_method() == "GET"
    assert request.get_header("X-mbx-apikey") == API_KEY
    parts = urllib.parse.urlsplit(request.full_url)
    assert parts.path == "/api/v3/account/commission"
    query = parts.query
    assert query.startswith("symbol=BTCUSDT&recvWindow=5000&timestamp=1700000000000&signature=")


def test_listede_olmayan_adres_gonderilmez():
    private, _ = generate_keypair()
    opener = Recorder({})
    reader = SignedReader(StoredKey(SecretText(API_KEY), private), opener=opener)
    with pytest.raises(SignedRequestError):
        reader.get("/api/v3/order", {"symbol": "BTCUSDT"})
    assert opener.requests == []


def test_imzali_okuyucuda_yazan_yontem_yok():
    names = [name for name in dir(SignedReader) if not name.startswith("_")]
    forbidden = re.compile(r"order|cancel|withdraw|transfer|post|put|delete", re.I)
    assert not [name for name in names if forbidden.search(name)]


def test_anahtar_nesnesi_yazdirilinca_gizli():
    private, _ = generate_keypair()
    key = StoredKey(SecretText(API_KEY), private)
    assert API_KEY not in repr(key) and private.reveal() not in repr(key)
    assert API_KEY not in repr(SignedReader(key))


def test_cekim_izni_acik_anahtar_reddedilir():
    blocking, _ = restriction_problems({"enableReading": True, "enableWithdrawals": True})
    assert any("çekme" in item for item in blocking)
    blocking, _ = restriction_problems({"enableReading": True, "enableFutures": True})
    assert blocking
    blocking, warnings = restriction_problems(
        {"enableReading": True, "enableSpotAndMarginTrading": True, "ipRestrict": False}
    )
    assert blocking == []
    assert len(warnings) == 2


def test_olculen_komisyon_kagit_isleme_gecer(tmp_path):
    before = paper_costs(tmp_path, "BTCUSDT")
    assert "varsayım" in before.kaynak_tr
    assert before.rate(Side.BUY, Liquidity.MAKER) == Decimal("0.001")
    CommissionStore(tmp_path).write({"BTCUSDT": COMMISSION})
    after = paper_costs(tmp_path, "BTCUSDT")
    assert "ölçülen" in after.kaynak_tr
    # BNB indirimi temkinli olmak için uygulanmaz.
    assert after.rate(Side.BUY, Liquidity.MAKER) == Decimal("0.00075")
    assert after.rate(Side.SELL, Liquidity.TAKER) == Decimal("0.0009")
    assert research_rates(tmp_path) == ("0.00075000", "0.00090000")


def test_kayma_varsayimi_yapilandirmayla_ayni():
    text = (Path(__file__).parents[1] / "config" / "default.yaml").read_text()
    match = re.search(r'beklenen_kayma_yuzde:\s*"([0-9.]+)"', text)
    assert match and Decimal(match.group(1)) == DEFAULT_SLIPPAGE_PCT
