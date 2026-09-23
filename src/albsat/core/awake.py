"""Kâğıt işlem açıkken Mac'in kendiliğinden uyumasını engeller (SPEC.md §6).

Şartname: "Otomatik mod açıkken uyku engellensin (``caffeinate`` veya
eşdeğeri). Pilde çalışıyorsa, kapak kapanırsa veya uyku riski varsa uyarı
versin." Kâğıt İşlem modu da kural sinyal verince kendiliğinden emir açar;
Mac uyursa o sürede gelen sinyaller kaçar.

``caffeinate -i -w <pid>`` macOS'un kendi aracıdır: boşta kalma uykusunu
engeller ve ``-w`` sayesinde bu uygulama kapanınca kendisi de kapanır;
geride açık kalan bir süreç olmaz. Kapak kapanınca Mac yine uyur, bunu
hiçbir kullanıcı aracı engelleyemez; bu yüzden pilde çalışırken uyarı
gösterilir. Uyanınca kaçırılan mumlar ``paper.runner`` tarafından işlenir.

macOS dışında (testler, ileride VPS) hiçbir şey yapmaz ve bunu söyler.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger(__name__)

#: Güç kaynağı en fazla bu sıklıkla sorulur.
POWER_CHECK_SECONDS = 60.0


class _Process(Protocol):
    def poll(self) -> int | None: ...
    def terminate(self) -> None: ...
    def wait(self, timeout: float | None = None) -> int: ...


@dataclass(frozen=True)
class GuardStatus:
    destekleniyor: bool
    etkin: bool
    pilde: bool | None
    aciklama: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "destekleniyor": self.destekleniyor,
            "etkin": self.etkin,
            "pilde": self.pilde,
            "aciklama": self.aciklama,
        }


def _spawn(args: Sequence[str]) -> _Process:
    return subprocess.Popen(  # noqa: S603 - sabit komut, kullanıcı girdisi yok
        list(args), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _power_source() -> str:
    result = subprocess.run(  # noqa: S603, S607 - sabit komut
        ["pmset", "-g", "batt"], capture_output=True, text=True, timeout=5, check=False,
    )
    return result.stdout


def on_battery(text: str) -> bool | None:
    """``pmset -g batt`` çıktısından güç kaynağı; anlaşılmazsa None."""
    if "'Battery Power'" in text:
        return True
    if "'AC Power'" in text:
        return False
    return None


class SleepGuard:
    def __init__(
        self,
        *,
        platform: str = sys.platform,
        which: Callable[[str], str | None] = shutil.which,
        spawn: Callable[[Sequence[str]], _Process] = _spawn,
        power: Callable[[], str] = _power_source,
        pid: int | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._supported = platform == "darwin" and which("caffeinate") is not None
        self._spawn = spawn
        self._power = power
        self._pid = os.getpid() if pid is None else pid
        self._monotonic = monotonic
        self._process: _Process | None = None
        self._battery: bool | None = None
        self._last_power = -POWER_CHECK_SECONDS

    @property
    def active(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def want(self, on: bool) -> None:
        """Kâğıt işlemdeki coin varsa açık, yoksa kapalı tutar; tekrar çağrılabilir."""
        if not self._supported:
            return
        if on and not self.active:
            try:
                self._process = self._spawn(["caffeinate", "-i", "-w", str(self._pid)])
            except OSError:
                logger.warning("caffeinate başlatılamadı", exc_info=True)
                self._process = None
        elif not on and self._process is not None:
            self.stop()
        if on:
            self._check_power()

    def stop(self) -> None:
        process, self._process = self._process, None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            logger.warning("caffeinate kapanmadı")

    def _check_power(self) -> None:
        now = self._monotonic()
        if now - self._last_power < POWER_CHECK_SECONDS:
            return
        self._last_power = now
        try:
            self._battery = on_battery(self._power())
        except (OSError, subprocess.SubprocessError):
            self._battery = None

    def status(self) -> GuardStatus:
        if not self._supported:
            return GuardStatus(False, False, None,
                               "Uyku engeli yalnızca macOS'ta çalışır (caffeinate).")
        if not self.active:
            return GuardStatus(True, False, None,
                               "Kâğıt işlemde coin yok; Mac'in uykusuna karışılmıyor.")
        text = "Kâğıt işlem açık olduğu için Mac'in kendiliğinden uyuması engelleniyor."
        if self._battery:
            text += (" Mac pille çalışıyor: kapak kapanırsa yine uyur; uyanınca kaçırılan "
                     "mumlar işlenir ama o sürede yeni sinyal alınmaz.")
        else:
            text += " Kapak kapanırsa Mac yine uyur."
        return GuardStatus(True, True, self._battery, text)


__all__ = ["GuardStatus", "SleepGuard", "on_battery"]
