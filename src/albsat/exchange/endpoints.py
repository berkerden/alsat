"""Ortam bazlı uç nokta adresleri: canlı / demo / testnet.

Kodun geri kalanı hangi ortamda çalıştığını bilmez; yalnızca buradan adres
alır. Böylece Faz 5'te Demo Mode'a, Faz 6'da canlıya geçiş tek satırlık bir
yapılandırma değişikliğidir.

Adresler ``demo-mode/general-info.md`` ve ``rest-api.md``'den alınmıştır.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Environment(str, Enum):
    LIVE = "live"
    DEMO = "demo"
    TESTNET = "testnet"


@dataclass(frozen=True)
class Endpoints:
    rest: str
    ws_api: str
    stream: str
    #: Bu ortamda gerçek para riski var mı? Arayüz bunu kırmızı/yeşil gösterir.
    real_money: bool


ENDPOINTS: dict[Environment, Endpoints] = {
    Environment.LIVE: Endpoints(
        rest="https://api.binance.com/api",
        ws_api="wss://ws-api.binance.com/ws-api/v3",
        stream="wss://stream.binance.com/stream",
        real_money=True,
    ),
    # Demo Mode: canlıyla birebir aynı filtreler ve limitler, gerçeğe yakın
    # fiyat ve emir defteri. Faz 5'in varsayılanı (bkz. FAZ0-MIMARI.md §6).
    Environment.DEMO: Endpoints(
        rest="https://demo-api.binance.com/api",
        ws_api="wss://demo-ws-api.binance.com/ws-api/v3",
        stream="wss://demo-stream.binance.com/stream",
        real_money=False,
    ),
    Environment.TESTNET: Endpoints(
        rest="https://testnet.binance.vision/api",
        ws_api="wss://ws-api.testnet.binance.vision/ws-api/v3",
        stream="wss://stream.testnet.binance.vision/stream",
        real_money=False,
    ),
}


def endpoints_for(environment: Environment | str) -> Endpoints:
    if isinstance(environment, str):
        environment = Environment(environment)
    return ENDPOINTS[environment]
