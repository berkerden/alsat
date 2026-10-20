"""Mac'te uyku engelini (caffeinate) ve güç kaynağı denetimini (pmset) sınayan komut.

Kullanım (Mac'te)::

    bash kurulum.sh uyku-sina

Uygulama, kâğıt işlemde coin ya da borsada açık pozisyon varken Mac'in
kendiliğinden uyumasını ``caffeinate -i -w <pid>`` ile engeller
(``core.awake``). Bu komut aynı kodu çalıştırır ve sonucu macOS'un kendisine
sorar (``pmset -g assertions``):

1. Güç kaynağı (pil ya da priz) ve Mac'in uyku ayarı okunur.
2. Engel açılır; macOS'un kaydında görünmeli.
3. Engel kapatılır; kayıttan silinmeli.
4. Uygulama çöker gibi aniden kapanırsa engel de kalkmalı: geride Mac'i
   sürekli uyanık tutan bir süreç kalmamalı.

Mac'i uyutmaz, ayar değiştirmez, ağa çıkmaz. Yaklaşık 15 saniye sürer.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from collections.abc import Callable
from typing import Any

from albsat.core.awake import SleepGuard, caffeinate_holds, on_battery, sleep_minutes

#: macOS'un kaydı en fazla bu kadar saniyede görünmeli ya da kalkmalı.
WAIT_SECONDS = 5.0


def _say(text: str = "") -> None:
    print(text, flush=True)


def _pmset(*args: str) -> str:
    result = subprocess.run(  # noqa: S603, S607 - sabit komut
        ["pmset", *args], capture_output=True, text=True, timeout=10, check=False,
    )
    return result.stdout


def _start_stand_in() -> Any:
    """Uygulamanın yerine geçen, sonra zorla kapatılacak geçici süreç."""
    return subprocess.Popen(  # noqa: S603 - sabit komut
        [sys.executable, "-c", "import time; time.sleep(120)"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _wait_until(check: Callable[[], bool], seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while True:
        if check():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.25)


def _holds(pmset: Callable[..., str], process_id: int | None) -> bool:
    return process_id is not None and caffeinate_holds(pmset("-g", "assertions"), process_id)


def _show_assertions(pmset: Callable[..., str]) -> None:
    _say("   macOS'un kaydındaki ilgili satırlar:")
    lines = [line for line in pmset("-g", "assertions").splitlines()
             if "caffeinate" in line or "PreventUserIdleSystemSleep" in line]
    for line in lines or ["(hiç satır yok)"]:
        _say(f"      {line.strip()}")


def run(
    *,
    platform: str = sys.platform,
    pmset: Callable[..., str] = _pmset,
    make_guard: Callable[..., SleepGuard] = SleepGuard,
    start_stand_in: Callable[[], Any] = _start_stand_in,
    wait_seconds: float = WAIT_SECONDS,
) -> int:
    if platform != "darwin":
        _say("Bu sınama yalnızca Mac'te çalışır (caffeinate ve pmset macOS'un araçlarıdır).")
        return 1
    guard = make_guard()
    if not guard.status().destekleniyor:
        _say("! caffeinate bulunamadı. macOS'ta her zaman bulunur; bu Mac'te bir sorun var.")
        return 1
    _say("Uyku engeli sınaması. Mac'i uyutmaz, ayarlarınızı değiştirmez, ağa çıkmaz.\n")

    _say("1/4  Güç kaynağı ve uyku ayarı")
    battery = on_battery(pmset("-g", "batt"))
    if battery is True:
        _say("   ✓ Mac pille çalışıyor. Uygulama bunu görüp 'kapak kapanırsa uyur' "
             "uyarısını gösterecek.")
    elif battery is False:
        _say("   ✓ Mac prize takılı.")
    else:
        _say("   ! Güç kaynağı okunamadı (pmset -g batt). Uygulama pil uyarısını "
             "gösteremeyecek; sınamaya devam ediliyor.")
    minutes = sleep_minutes(pmset("-g"))
    if minutes is None:
        _say("   ! Mac'in uyku ayarı okunamadı; sınamaya devam ediliyor.")
    elif minutes == 0:
        _say("   ✓ Mac'in ayarı: boşta kalınca hiç uyumuyor. Engel yine de açılacak; zararı yok.")
    else:
        _say(f"   ✓ Mac'in ayarı: {minutes} dakika boşta kalınca uyuyor. Uygulamanın "
             "engellediği bu uyku.")

    stand_in = None
    try:
        _say("\n2/4  Uyku engeli açılıyor (uygulamanın kullandığı kodla)")
        guard.want(True)
        if not _wait_until(lambda: _holds(pmset, guard.process_id), wait_seconds):
            _say("   ✗ macOS'un kaydında uyku engeli görünmedi.")
            _show_assertions(pmset)
            return 1
        _say(f"   ✓ macOS engeli kaydetti: caffeinate (süreç {guard.process_id}) Mac'in "
             "boşta kalınca uyumasını engelliyor.")

        _say("\n3/4  Uyku engeli kapatılıyor")
        process_id = guard.process_id
        guard.want(False)
        if not _wait_until(lambda: not _holds(pmset, process_id), wait_seconds):
            _say("   ✗ Engel kapatıldı ama macOS'un kaydında hâlâ duruyor.")
            _show_assertions(pmset)
            return 1
        _say("   ✓ Kayıt silindi; Mac yine kendi ayarıyla uyuyabilir.")

        _say("\n4/4  Uygulama aniden kapanırsa engel kalkıyor mu?")
        _say("   Uygulamanın yerine geçen geçici bir süreç açılıp zorla kapatılıyor...")
        stand_in = start_stand_in()
        child_guard = make_guard(pid=stand_in.pid)
        child_guard.want(True)
        if not _wait_until(lambda: _holds(pmset, child_guard.process_id), wait_seconds):
            _say("   ✗ Geçici süreç için uyku engeli açılamadı.")
            _show_assertions(pmset)
            return 1
        process_id = child_guard.process_id
        stand_in.kill()
        stand_in.wait(timeout=5)
        released = _wait_until(
            lambda: not child_guard.active and not _holds(pmset, process_id), wait_seconds
        )
        child_guard.stop()
        if not released:
            _say("   ✗ Geçici süreç kapandı ama caffeinate kapanmadı. Uygulama çökerse Mac "
                 "uyanık kalabilir.")
            _show_assertions(pmset)
            return 1
        _say("   ✓ Geçici süreç kapanınca caffeinate de kapandı; geride Mac'i uyanık tutan "
             "bir şey kalmadı.")
    finally:
        guard.stop()
        if stand_in is not None and stand_in.poll() is None:
            stand_in.kill()

    _say("\nSONUÇ: ✓ Uyku engeli bu Mac'te çalışıyor.")
    if battery:
        _say("Not: Pille çalışırken kapak kapanırsa Mac yine uyur; bunu hiçbir uygulama "
             "engelleyemez.")
    return 0


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(
        prog="albsat-uyku", description="Mac'te uyku engelini (caffeinate) sınar"
    ).parse_args(argv)
    return run()


if __name__ == "__main__":
    sys.exit(main())
