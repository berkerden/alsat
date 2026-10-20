"""Uyku engeli sınama komutu (``albsat.cli.uyku``) ve ``pmset`` okuyucuları.

macOS'a dokunulmaz: ``caffeinate`` ve ``pmset`` yerine sahte bir Mac verilir.
Gerçek sınama Berk'in Mac'inde ``bash kurulum.sh uyku-sina`` ile yapılır.
"""
from __future__ import annotations

from albsat.cli import uyku
from albsat.core.awake import SleepGuard, caffeinate_holds, sleep_minutes

ASSERTIONS_HEAD = """2026-10-20 10:00:00 +0300
Assertion status system-wide:
   BackgroundTask                 0
   UserIsActive                   1
   PreventUserIdleDisplaySleep    0
   PreventSystemSleep             0
   PreventUserIdleSystemSleep     {count}
   NetworkClientActive            0
Listed by owning process:
   pid 130(WindowServer): [0x0000c5b100099a12] 00:01:12 UserIsActive named: "tickle"
\tTimeout will fire in 179 secs Action=TimeoutActionRelease
"""
CAFFEINATE_LINE = (
    '   pid {pid}(caffeinate): [0x0000c5d9000192ab] 00:00:03 PreventUserIdleSystemSleep '
    'named: "caffeinate command-line tool"\n'
    "\tDetails: caffeinate asserting on behalf of Process ID {waits}\n"
    "\tLocalized=THE CAFFEINATE TOOL IS PREVENTING SLEEP.\n"
)
PMSET_G = """System-wide power settings:
Currently in use:
 standby              1
 Sleep On Power Button 1
 disksleep            10
 sleep                {sleep}
 displaysleep         2
"""


class FakeProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.alive = True

    def poll(self):
        return None if self.alive else 0

    def terminate(self):
        self.alive = False

    def kill(self):
        self.alive = False

    def wait(self, timeout=None):
        return 0


class FakeMac:
    """caffeinate süreçlerini ve macOS'un uyku kaydını taklit eder."""

    def __init__(self, *, registers=True, honors_wait=True, battery=False, sleep="10"):
        self.registers = registers
        self.honors_wait = honors_wait
        self.battery = battery
        self.sleep = sleep
        self.next_pid = 800
        self.caffeinates: list[tuple[FakeProcess, int]] = []
        self.stand_ins: list[FakeProcess] = []

    def _pid(self) -> int:
        self.next_pid += 1
        return self.next_pid

    def spawn(self, args):
        assert args[:3] == ["caffeinate", "-i", "-w"]
        process = FakeProcess(self._pid())
        self.caffeinates.append((process, int(args[3])))
        return process

    def start_stand_in(self):
        stand_in = FakeProcess(self._pid())
        original_kill = stand_in.kill

        def kill():
            original_kill()
            if self.honors_wait:  # caffeinate -w: beklediği süreç ölünce kendisi de kapanır
                for process, waits in self.caffeinates:
                    if waits == stand_in.pid:
                        process.alive = False

        stand_in.kill = kill
        self.stand_ins.append(stand_in)
        return stand_in

    def pmset(self, *args):
        if args == ("-g", "batt"):
            source = "'Battery Power'" if self.battery else "'AC Power'"
            return f"Now drawing from {source}\n -InternalBattery-0 (id=1)\t80%\n"
        if args == ("-g",):
            return PMSET_G.format(sleep=self.sleep)
        assert args == ("-g", "assertions")
        alive = [(p, w) for p, w in self.caffeinates if p.alive] if self.registers else []
        return ASSERTIONS_HEAD.format(count=1 if alive else 0) + "".join(
            CAFFEINATE_LINE.format(pid=p.pid, waits=w) for p, w in alive)

    def make_guard(self, pid=None):
        return SleepGuard(platform="darwin", which=lambda name: "/usr/bin/" + name,
                          spawn=self.spawn, power=lambda: self.pmset("-g", "batt"),
                          pid=4321 if pid is None else pid)

    def run(self, **kwargs):
        return uyku.run(platform="darwin", pmset=self.pmset, make_guard=self.make_guard,
                        start_stand_in=self.start_stand_in, wait_seconds=0.3, **kwargs)


def test_pmset_okuyuculari():
    text = ASSERTIONS_HEAD.format(count=1) + CAFFEINATE_LINE.format(pid=812, waits=4321)
    assert caffeinate_holds(text, 812)
    assert not caffeinate_holds(text, 81)
    assert not caffeinate_holds(text, 130)
    assert not caffeinate_holds(ASSERTIONS_HEAD.format(count=0), 812)
    assert sleep_minutes(PMSET_G.format(sleep="10 (sleep prevented by caffeinate, powerd)")) == 10
    assert sleep_minutes(PMSET_G.format(sleep="0")) == 0
    assert sleep_minutes("") is None


def test_uyku_sinamasi_gecer_ve_geride_surec_birakmaz(capsys):
    mac = FakeMac()
    assert mac.run() == 0
    out = capsys.readouterr().out
    assert "Mac prize takılı" in out and "10 dakika" in out
    assert "macOS engeli kaydetti" in out
    assert "SONUÇ: ✓" in out
    # Uygulamanın kendi kimliğiyle bir, geçici süreç için bir caffeinate; ikisi de kapalı.
    assert [waits for _, waits in mac.caffeinates] == [4321, mac.stand_ins[0].pid]
    assert not any(process.alive for process, _ in mac.caffeinates)
    assert not mac.stand_ins[0].alive


def test_uyku_sinamasi_pilde_kapak_uyarisi_verir(capsys):
    assert FakeMac(battery=True, sleep="0").run() == 0
    out = capsys.readouterr().out
    assert "pille çalışıyor" in out and "kapak" in out
    assert "hiç uyumuyor" in out


def test_uyku_sinamasi_macos_kaydi_yoksa_gecmez(capsys):
    mac = FakeMac(registers=False)
    assert mac.run() == 1
    out = capsys.readouterr().out
    assert "görünmedi" in out and "SONUÇ" not in out
    assert not any(process.alive for process, _ in mac.caffeinates)


def test_uyku_sinamasi_caffeinate_geride_kalirsa_gecmez(capsys):
    mac = FakeMac(honors_wait=False)
    assert mac.run() == 1
    assert "caffeinate kapanmadı" in capsys.readouterr().out
    assert not any(process.alive for process, _ in mac.caffeinates)


def test_uyku_sinamasi_macos_disinda_calismaz(capsys):
    assert uyku.run(platform="linux") == 1
    assert "yalnızca Mac'te" in capsys.readouterr().out


def test_uyku_engeli_durumu_borsadaki_pozisyonu_da_soyler():
    mac = FakeMac()
    guard = mac.make_guard()
    assert "borsada açık pozisyon yok" in guard.status().aciklama
    assert guard.process_id is None
    guard.want(True)
    assert guard.process_id == mac.caffeinates[0][0].pid
    assert "borsada (Demo, canlı) açık pozisyon" in guard.status().aciklama
    guard.stop()
    assert guard.process_id is None
