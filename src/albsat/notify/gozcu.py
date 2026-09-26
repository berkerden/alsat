"""Dış gözcü: uygulama kapanınca ya da takılınca haber veren alarm (SPEC.md §6, §7).

Uygulama kendi kapandığını haber veremez; bu yüzden iş tersine çevrilir.
Uygulama sağlıklıyken düzenli aralıklarla dışarıdaki bir gözcü hizmetine
"buradayım" der (ping). Gözcü belirlenen süre boyunca ping almazsa telefona ya
da e-postaya alarm gönderir. Uygulama kapanırsa, bilgisayar ya da sunucu
kapanırsa, internet giderse veya uygulama takılırsa ping durur ve alarm
gelir. Faz 4'teki "çevrimdışıydı" bildirimi ancak uygulama yeniden açılınca
gidiyordu; bu modül o boşluğu kapatır.

**Hizmet:** Healthchecks.io (açık kaynak, ücretsiz planı yeter). Alarmı kendi
Telegram botuyla, e-postayla ya da başka kanallarla gönderir; bu uygulamanın
Telegram jetonunu istemez. Ping adresi ``https://hc-ping.com/<kimlik>``
biçimindedir. Hizmetin kuralları (healthchecks.io/docs/http_api):

* Adrese ``GET`` ya da ``POST``: "çalışıyorum". Yanıt ``200 OK``.
* ``<adres>/fail``: "sorun var"; gözcü süre dolmasını beklemeden alarm verir.
  İstek gövdesi (en fazla 100 KB) alarmın ayrıntısında görünür.
* ``<adres>/log``: durumu değiştirmeyen not.

**Ne zaman "sorun var" denir:** canlı döngü ilerlemiyorsa, piyasa verisi
beş dakikadan eskiyse ya da diskte yer kalmadıysa (``Runtime.health_verdict``).
Tek bir kötü okuma alarm olmasın diye aynı sorun iki ardışık bakışta
görülmelidir.

**Adres sır sayılır:** bilen biri sahte "çalışıyorum" diyerek gerçek bir
kesintiyi gizleyebilir. Bu yüzden sır deposunda (Mac'te Anahtar Zinciri)
durur; ekrana, günlüğe ve arayüze yalnızca sunucu adı yazılır.

Ağ: yalnızca gözcü adresine, :data:`INTERVAL_SECONDS` saniyede bir istek.
Binance'e istek göndermez. TLS doğrulaması açıktır.
"""

from __future__ import annotations

import logging
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from albsat.core import keychain
from albsat.core.clock import iso, utc_now
from albsat.exchange.keys import SecretText
from albsat.notify.base import KIND_CONNECTION, Notifier

logger = logging.getLogger(__name__)

KEYCHAIN_SERVICE = "albsat-gozcu"
KEYCHAIN_ACCOUNT = "ping-adresi"
#: Ping sıklığı. Gözcüde "süre" 5 dakika, "tolerans" 10 dakika önerilir.
INTERVAL_SECONDS = 120.0
TIMEOUT_SECONDS = 10.0
#: Aynı sorun bu kadar ardışık bakışta görülürse "sorun var" gönderilir.
FAILS_BEFORE_ALARM = 2
USER_AGENT = "albsat-gozcu"
_URL = re.compile(r"^https://[A-Za-z0-9.-]+(:\d{1,5})?/[A-Za-z0-9._~/-]{8,300}$")


class WatchdogError(RuntimeError):
    """Gözcü adresi geçersiz ya da hizmete ulaşılamadı; mesajda adres yoktur."""


@dataclass(frozen=True)
class HealthVerdict:
    saglikli: bool
    neden: str


def valid_url(text: str) -> bool:
    return bool(_URL.match(text.strip()))


def host_of(url: SecretText) -> str:
    """Ekrana yazılabilecek tek parça: sunucu adı."""
    return urllib.parse.urlsplit(url.reveal()).hostname or "?"


def load_url_strict() -> SecretText | None:
    """Sır deposundaki adres; izinler bozuksa ``keychain.KeychainError``."""
    return keychain.read(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)


def load_url() -> SecretText | None:
    try:
        return load_url_strict()
    except keychain.KeychainError:
        return None


class PingClient:
    def __init__(
        self,
        url: SecretText,
        *,
        opener: Callable[..., Any] = urllib.request.urlopen,
        timeout: float = TIMEOUT_SECONDS,
    ) -> None:
        if not valid_url(url.reveal()):
            raise WatchdogError("Gözcü adresi geçersiz: https:// ile başlayan tam adres olmalı.")
        self._url = url
        self._opener = opener
        self._timeout = timeout

    def __repr__(self) -> str:
        return f"PingClient({self.host})"

    @property
    def host(self) -> str:
        return host_of(self._url)

    def _send(self, suffix: str, body: str | None) -> None:
        url = self._url.reveal().rstrip("/") + suffix
        data = None if body is None else body.encode("utf-8")[:10_000]
        request = urllib.request.Request(
            url, data=data, method="GET" if data is None else "POST",
            headers={"User-Agent": USER_AGENT, "Content-Type": "text/plain; charset=utf-8"},
        )
        try:
            with self._opener(request, timeout=self._timeout) as response:
                status = getattr(response, "status", 200)
                response.read(200)
        except urllib.error.HTTPError as error:
            raise WatchdogError(f"Gözcü ({self.host}) HTTP {error.code} döndü") from None
        except (urllib.error.URLError, OSError, ValueError) as error:
            reason = getattr(error, "reason", error)
            text = str(reason).replace(self._url.reveal(), "<adres>")
            raise WatchdogError(f"Gözcüye ({self.host}) ulaşılamadı: {text}") from None
        if status >= 300:
            raise WatchdogError(f"Gözcü ({self.host}) HTTP {status} döndü")

    def ok(self, body: str | None = None) -> None:
        self._send("", body)

    def fail(self, reason: str) -> None:
        self._send("/fail", reason)

    def log(self, text: str) -> None:
        self._send("/log", text)


class Watchdog:
    """Arka planda sağlık bakar ve gözcüye bildirir."""

    def __init__(
        self,
        client: PingClient,
        *,
        check: Callable[[], HealthVerdict],
        notifier: Notifier | None = None,
        interval: float = INTERVAL_SECONDS,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.client = client
        self.check = check
        self.notifier = notifier
        self.interval = interval
        self.clock = clock
        self.son_ping_utc: str | None = None
        self.son_durum: str | None = None
        self.son_neden: str | None = None
        self.son_hata: str | None = None
        self.ping_sayisi = 0
        self._bad_streak = 0
        self._alarm = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="gozcu", daemon=True)
        self._thread.start()

    def stop(self, *, note: str | None = None) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(TIMEOUT_SECONDS + 5)
        if note:
            try:
                self.client.log(note)
            except WatchdogError as error:
                logger.warning("%s", error)

    def _run(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            self.beat()
            spent = time.monotonic() - started
            if self._stop.wait(max(1.0, self.interval - spent)):
                return

    def beat(self) -> str:
        """Bir bakış: ``"ok"``, ``"fail"`` ya da ``"bekliyor"`` (ilk kötü okuma) döner."""
        try:
            verdict = self.check()
        except Exception as error:  # noqa: BLE001 - denetimin kendisi bozulduysa da söyle
            verdict = HealthVerdict(False, f"sağlık denetimi hata verdi: {type(error).__name__}")
        if verdict.saglikli:
            self._bad_streak = 0
            outcome = "ok"
        else:
            self._bad_streak += 1
            outcome = "fail" if self._bad_streak >= FAILS_BEFORE_ALARM else "bekliyor"
        self.son_neden = verdict.neden
        try:
            if outcome == "ok":
                self.client.ok()
            elif outcome == "fail":
                self.client.fail(verdict.neden)
            else:
                # İlk kötü okumada da "çalışıyorum" denir; alarm ikinci okumada.
                self.client.ok(f"uyarı: {verdict.neden}")
        except WatchdogError as error:
            self.son_hata = str(error)
            logger.warning("%s", error)
        else:
            self.son_hata = None
            self.son_ping_utc = iso(self.clock())
            self.ping_sayisi += 1
        self.son_durum = outcome
        self._announce(outcome, verdict)
        return outcome

    def _announce(self, outcome: str, verdict: HealthVerdict) -> None:
        if self.notifier is None:
            return
        if outcome == "fail" and not self._alarm:
            self._alarm = True
            self.notifier.send(f"🩺 Gözcüye 'sorun var' bildirildi: {verdict.neden}",
                               kind=KIND_CONNECTION)
        elif outcome == "ok" and self._alarm:
            self._alarm = False
            self.notifier.send("🩺 Sorun giderildi; gözcüye yeniden 'çalışıyor' deniyor.",
                               kind=KIND_CONNECTION)

    def status(self) -> dict[str, Any]:
        return {
            "kurulu": True,
            "sunucu": self.client.host,
            "aralik_sn": int(self.interval),
            "son_ping_utc": self.son_ping_utc,
            "son_durum": self.son_durum,
            "son_neden": self.son_neden,
            "son_hata": self.son_hata,
            "ping_sayisi": self.ping_sayisi,
        }


__all__ = [
    "INTERVAL_SECONDS",
    "KEYCHAIN_ACCOUNT",
    "KEYCHAIN_SERVICE",
    "HealthVerdict",
    "PingClient",
    "Watchdog",
    "WatchdogError",
    "host_of",
    "load_url",
    "load_url_strict",
    "valid_url",
]
