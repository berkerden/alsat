"""Telegram bildirimleri ve komutları (SPEC.md §8.10).

Bot jetonu (token) **macOS Anahtar Zinciri'nde** durur; bu dosya, depo,
günlük ve arayüz onu hiç görmez. ``veri/telegram.json`` yalnızca sohbet
kimliğini (chat id) ve botun kullanıcı adını tutar; ikisi de sır değildir.

Bot API kuralları (core.telegram.org/bots/api):

* İstek ``https://api.telegram.org/bot<jeton>/<yöntem>`` adresine gider;
  yanıt ``{"ok": bool, "result": ..., "description": ..., "error_code": ...,
  "parameters": {"retry_after": sn}}`` biçimindedir.
* Mesaj metni en fazla 4096 karakterdir.
* ``getUpdates`` uzun yoklama yapar; ``offset`` son işlenen güncellemenin
  bir fazlasıdır, önceki güncellemeleri onaylar.
* Aynı sohbete saniyede birden fazla mesaj gönderilmemesi önerilir; 429
  gelirse ``retry_after`` kadar beklenir.

**Adres jetonu içerdiği için** hiçbir hata mesajına adres yazılmaz; bir
istisnanın metninde jeton geçerse maskelenir.

Komutlara **yalnızca kurulumda kaydedilen sohbetten** yanıt verilir; başka
biri botu bulup yazsa bile sessizce yok sayılır.
"""

from __future__ import annotations

import contextlib
import json
import logging
import queue
import re
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from albsat.core import keychain
from albsat.core.clock import iso, utc_now
from albsat.exchange.keys import SecretText
from albsat.notify.base import KIND_SYSTEM, MemoryNotifier, Notice

logger = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"
KEYCHAIN_SERVICE = "albsat-telegram"
KEYCHAIN_ACCOUNT = "bot-token"
CONFIG_FILENAME = "telegram.json"
MAX_TEXT = 4096
TOKEN_PATTERN = re.compile(r"^\d{5,15}:[A-Za-z0-9_-]{30,64}$")


class TelegramError(RuntimeError):
    def __init__(self, message: str, *, code: int | None = None,
                 retry_after: float | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.retry_after = retry_after


def looks_like_token(value: str) -> bool:
    return bool(TOKEN_PATTERN.match(value.strip()))


class TelegramClient:
    def __init__(
        self,
        token: SecretText,
        *,
        opener: Callable[..., Any] = urllib.request.urlopen,
        base: str = API_BASE,
    ) -> None:
        self._token = token
        self._opener = opener
        self._base = base.rstrip("/")

    def __repr__(self) -> str:
        return "TelegramClient(<jeton gizli>)"

    def _mask(self, text: str) -> str:
        secret = self._token.reveal()
        return text.replace(secret, "<jeton>") if secret else text

    def call(self, method: str, params: dict[str, Any] | None = None, *,
             timeout: float = 30.0) -> Any:
        url = f"{self._base}/bot{self._token.reveal()}/{method}"
        body = json.dumps(params or {}).encode("utf-8")
        request = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with self._opener(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            try:
                payload = json.loads(error.read().decode("utf-8"))
            except (ValueError, OSError):
                raise TelegramError(
                    f"Telegram {method}: HTTP {error.code}", code=error.code
                ) from None
        except (urllib.error.URLError, OSError, ValueError) as error:
            reason = getattr(error, "reason", error)
            raise TelegramError(
                self._mask(f"Telegram'a ulaşılamadı ({method}): {reason}")
            ) from None
        if not isinstance(payload, dict):
            raise TelegramError(f"Telegram {method}: beklenmeyen yanıt")
        if not payload.get("ok"):
            parameters = payload.get("parameters") or {}
            raise TelegramError(
                self._mask(f"Telegram {method}: {payload.get('description', 'hata')}"),
                code=payload.get("error_code"),
                retry_after=parameters.get("retry_after"),
            )
        return payload.get("result")

    def get_me(self) -> dict[str, Any]:
        result = self.call("getMe")
        return result if isinstance(result, dict) else {}

    def send_message(self, chat_id: int, text: str) -> None:
        self.call(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": text[:MAX_TEXT],
                "link_preview_options": {"is_disabled": True},
            },
        )

    def get_updates(self, offset: int | None, *, timeout: int = 25) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"timeout": timeout, "allowed_updates": ["message"]}
        if offset is not None:
            params["offset"] = offset
        result = self.call("getUpdates", params, timeout=timeout + 10)
        return [item for item in result if isinstance(item, dict)] if result else []


# --- ayar dosyası ---------------------------------------------------------------


@dataclass(frozen=True)
class TelegramConfig:
    chat_id: int
    bot_kullanici_adi: str
    kurulum_utc: str
    sohbet_adi: str = ""

    @staticmethod
    def path(root: Path | str) -> Path:
        return Path(root) / CONFIG_FILENAME

    @classmethod
    def load(cls, root: Path | str) -> TelegramConfig | None:
        path = cls.path(root)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(
                chat_id=int(data["chat_id"]),
                bot_kullanici_adi=str(data.get("bot_kullanici_adi", "")),
                kurulum_utc=str(data.get("kurulum_utc", "")),
                sohbet_adi=str(data.get("sohbet_adi", "")),
            )
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def save(self, root: Path | str) -> Path:
        path = self.path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2),
                             encoding="utf-8")
        temporary.replace(path)
        return path


def load_token() -> SecretText | None:
    return keychain.read(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)


# --- bildirim gönderici ---------------------------------------------------------------


class TelegramNotifier(MemoryNotifier):
    """Bildirimleri sıraya alır, arka planda saniyede en fazla bir mesaj gönderir.

    Gönderilemeyen bildirim kaybolmaz: arayüzün "son bildirimler" listesinde
    "gönderilemedi" olarak görünür.
    """

    def __init__(self, client: TelegramClient, chat_id: int, *, bot_name: str = "",
                 min_interval: float = 1.1, capacity: int = 200) -> None:
        super().__init__(capacity)
        self.client = client
        self.chat_id = chat_id
        self.bot_name = bot_name
        self.min_interval = min_interval
        self._queue: queue.Queue[tuple[str, str] | None] = queue.Queue(maxsize=500)
        self._thread: threading.Thread | None = None
        self._sent = 0
        self._failed = 0
        self._last_error: str | None = None
        self._last_sent_at = 0.0

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="telegram-gonder", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        with contextlib.suppress(queue.Full):
            self._queue.put_nowait(None)
        if self._thread is not None:
            self._thread.join(timeout)

    def send(self, text: str, *, kind: str = KIND_SYSTEM) -> None:
        try:
            self._queue.put_nowait((text, kind))
        except queue.Full:
            self._failed += 1
            self._record(Notice(iso(utc_now()), kind, text, gonderildi=False))

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            text, kind = item
            wait = self.min_interval - (time.monotonic() - self._last_sent_at)
            if wait > 0:
                time.sleep(wait)
            delivered = self._deliver(text)
            self._last_sent_at = time.monotonic()
            self._record(Notice(iso(utc_now()), kind, text, gonderildi=delivered))

    def _deliver(self, text: str) -> bool:
        for attempt in range(2):
            try:
                self.client.send_message(self.chat_id, text)
                self._sent += 1
                return True
            except TelegramError as error:
                self._last_error = str(error)
                if error.retry_after and attempt == 0:
                    time.sleep(min(float(error.retry_after), 60.0))
                    continue
                break
        self._failed += 1
        return False

    def status(self) -> dict[str, Any]:
        return {
            "kurulu": True,
            "bot": self.bot_name,
            "gonderilen": self._sent,
            "gonderilemeyen": self._failed,
            "kuyrukta": self._queue.qsize(),
            "son_hata": self._last_error,
        }


# --- komutlar -----------------------------------------------------------------------


CommandHandler = Callable[[str, str], str]


class TelegramCommands:
    """``getUpdates`` ile komut dinler; yalnızca kayıtlı sohbete yanıt verir."""

    def __init__(self, client: TelegramClient, chat_id: int, handler: CommandHandler,
                 *, on_ignored: Callable[[int], None] | None = None) -> None:
        self.client = client
        self.chat_id = chat_id
        self.handler = handler
        self.on_ignored = on_ignored
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._offset: int | None = None
        self.last_error: str | None = None
        self.handled = 0
        self.ignored = 0

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="telegram-komut", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def skip_backlog(self) -> None:
        """Uygulama kapalıyken yazılmış eski komutlar çalıştırılmaz."""
        updates = self.client.get_updates(-1, timeout=0)
        if updates:
            self._offset = int(updates[-1]["update_id"]) + 1

    def process(self, updates: list[dict[str, Any]]) -> None:
        for update in updates:
            self._offset = int(update.get("update_id", 0)) + 1
            message = update.get("message") or {}
            chat = message.get("chat") or {}
            sender = message.get("from") or {}
            text = str(message.get("text") or "").strip()
            if chat.get("id") != self.chat_id or sender.get("id") != self.chat_id:
                self.ignored += 1
                if self.on_ignored is not None and isinstance(chat.get("id"), int):
                    self.on_ignored(int(chat["id"]))
                continue
            if not text.startswith("/"):
                reply = "Komutlar için /yardim yazın."
            else:
                head, _, rest = text.partition(" ")
                command = head.split("@", 1)[0].lower()
                try:
                    reply = self.handler(command, rest.strip())
                except Exception as error:  # noqa: BLE001 - komut hatası botu durdurmasın
                    logger.exception("Telegram komutu başarısız")
                    reply = f"Komut çalıştırılamadı: {type(error).__name__}"
            self.handled += 1
            try:
                self.client.send_message(self.chat_id, reply)
            except TelegramError as error:
                self.last_error = str(error)

    def _run(self) -> None:
        failures = 0
        try:
            self.skip_backlog()
        except TelegramError as error:
            self.last_error = str(error)
        while not self._stop.is_set():
            try:
                updates = self.client.get_updates(self._offset, timeout=25)
                failures = 0
            except TelegramError as error:
                self.last_error = str(error)
                failures += 1
                self._stop.wait(min(60.0, 5.0 * failures))
                continue
            self.process(updates)


__all__ = [
    "CONFIG_FILENAME",
    "KEYCHAIN_ACCOUNT",
    "KEYCHAIN_SERVICE",
    "TelegramClient",
    "TelegramCommands",
    "TelegramConfig",
    "TelegramError",
    "TelegramNotifier",
    "load_token",
    "looks_like_token",
]
