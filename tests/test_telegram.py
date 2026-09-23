"""Telegram, Anahtar Zinciri, komutlar ve kâğıt işlem raporu testleri.

Telegram'a bağlanılmaz: ``urlopen`` yerine sahte bir açıcı verilir.
"""
from __future__ import annotations

import io
import json
import subprocess
import time
import urllib.error
from datetime import timedelta
from decimal import Decimal

import pytest
from test_kagit_islem import NOW, candle, market, place  # noqa: F401
from test_kagit_islem import notifier as notifier  # noqa: F401  (fikstür)
from test_kagit_islem import paper as paper  # noqa: F401  (fikstür)

from albsat.core import keychain
from albsat.core.audit import SOURCE_TELEGRAM, AuditLog
from albsat.exchange.keys import SecretText
from albsat.modes.state import MODE_ADVICE
from albsat.notify.commands import build_handler
from albsat.notify.telegram import (
    TelegramClient,
    TelegramCommands,
    TelegramConfig,
    TelegramError,
    TelegramNotifier,
    looks_like_token,
)
from albsat.paper import report
from albsat.risk.engine import SOURCE_MANUAL

TOKEN = "123456789:AAHfakefakefakefakefakefakefakefake12"
D = Decimal


class FakeTelegram:
    """``urlopen`` yerine geçer; çağrıları kaydeder, sırayla yanıt döner."""

    def __init__(self, responses=None):
        self.calls: list[tuple[str, dict]] = []
        self.responses = list(responses or [])

    def __call__(self, request, timeout):
        method = request.full_url.rsplit("/", 1)[-1]
        self.calls.append((method, json.loads(request.data or b"{}")))
        response = self.responses.pop(0) if self.responses else {"ok": True, "result": []}
        if isinstance(response, Exception):
            raise response
        return io.BytesIO(json.dumps(response).encode())


def test_jeton_bicimi():
    assert looks_like_token(TOKEN)
    assert not looks_like_token("merhaba")
    assert not looks_like_token("123:kisa")


def test_hata_mesajinda_jeton_gorunmez():
    opener = FakeTelegram([urllib.error.URLError(f"bağlantı reddedildi bot{TOKEN}")])
    client = TelegramClient(SecretText(TOKEN), opener=opener)
    with pytest.raises(TelegramError) as error:
        client.get_me()
    assert TOKEN not in str(error.value)
    assert "<jeton>" in str(error.value)
    assert TOKEN not in repr(client)


def test_telegram_hatasi_bekleme_suresini_tasir():
    body = {"ok": False, "error_code": 429, "description": "Too Many Requests",
            "parameters": {"retry_after": 3}}
    error = urllib.error.HTTPError("u", 429, "x", {}, io.BytesIO(json.dumps(body).encode()))
    client = TelegramClient(SecretText(TOKEN), opener=FakeTelegram([error]))
    with pytest.raises(TelegramError) as caught:
        client.send_message(1, "x")
    assert caught.value.code == 429 and caught.value.retry_after == 3


def test_bildirim_gonderilir_ve_listede_gorunur():
    opener = FakeTelegram([{"ok": True, "result": {}}])
    notifier = TelegramNotifier(TelegramClient(SecretText(TOKEN), opener=opener), 42,
                                min_interval=0)
    notifier.start()
    notifier.send("merhaba", kind="sistem")
    deadline = time.monotonic() + 3
    while not notifier.recent() and time.monotonic() < deadline:
        time.sleep(0.01)
    notifier.stop()
    assert opener.calls[0][0] == "sendMessage"
    assert opener.calls[0][1]["chat_id"] == 42
    assert notifier.recent()[0].gonderildi is True


def test_gonderilemeyen_bildirim_kaybolmaz():
    opener = FakeTelegram([urllib.error.URLError("yok")])
    notifier = TelegramNotifier(TelegramClient(SecretText(TOKEN), opener=opener), 42,
                                min_interval=0)
    notifier.start()
    notifier.send("önemli", kind="stop")
    deadline = time.monotonic() + 3
    while not notifier.recent() and time.monotonic() < deadline:
        time.sleep(0.01)
    notifier.stop()
    notice = notifier.recent()[0]
    assert notice.metin == "önemli" and notice.gonderildi is False
    assert notifier.status()["gonderilemeyen"] == 1


def test_bildirimler_arasinda_asgari_bekleme_var():
    times: list[float] = []
    opener = FakeTelegram()

    def timed(request, timeout):
        times.append(time.monotonic())
        return opener(request, timeout)

    notifier = TelegramNotifier(TelegramClient(SecretText(TOKEN), opener=timed), 42,
                                min_interval=0.2)
    notifier.start()
    for index in range(3):
        notifier.send(f"mesaj {index}", kind="sistem")
    deadline = time.monotonic() + 5
    while len(notifier.recent()) < 3 and time.monotonic() < deadline:
        time.sleep(0.01)
    notifier.stop()
    assert len(times) == 3
    # Telegram aynı sohbete saniyede birden fazla mesaj istemiyor; aralık korunur.
    assert all(later - earlier >= 0.19 for earlier, later in zip(times, times[1:]))


def test_denetim_kaydi_sir_tasimaz(tmp_path):
    log = AuditLog.in_directory(tmp_path)
    log.write("deneme", "Telegram kuruldu", ayrinti={
        "jeton": TOKEN, "bot_token": TOKEN, "imza": "abc", "private_key": "def",
        "api_secret": "ghi", "sembol": "BTCUSDT", "adet": 2,
    })
    entry = log.recent(1)[0]
    assert entry.ayrinti == {"sembol": "BTCUSDT", "adet": 2}
    raw = (tmp_path / "albsat.sqlite3").read_bytes()
    assert TOKEN.encode() not in raw


def _update(update_id, chat_id, text, sender_id=None):
    return {"update_id": update_id, "message": {
        "chat": {"id": chat_id, "type": "private"},
        "from": {"id": sender_id if sender_id is not None else chat_id},
        "text": text}}


def test_komutlara_yalnizca_kayitli_sohbet_yanit_alir():
    opener = FakeTelegram()
    seen: list[tuple[str, str]] = []
    ignored: list[int] = []
    commands = TelegramCommands(
        TelegramClient(SecretText(TOKEN), opener=opener), 42,
        lambda command, args: seen.append((command, args)) or "tamam",
        on_ignored=ignored.append,
    )
    commands.process([
        _update(1, 999, "/durdur"),
        _update(2, 42, "/durum@albsat_bot"),
        _update(3, 42, "/durdur kapat"),
    ])
    assert seen == [("/durum", ""), ("/durdur", "kapat")]
    assert ignored == [999]
    sent = [body for method, body in opener.calls if method == "sendMessage"]
    assert all(body["chat_id"] == 42 for body in sent)
    assert len(sent) == 2


def test_acilista_eski_komutlar_atlanir():
    opener = FakeTelegram([{"ok": True, "result": [_update(17, 42, "/durdur")]}])
    commands = TelegramCommands(TelegramClient(SecretText(TOKEN), opener=opener), 42,
                                lambda command, args: "x")
    commands.skip_backlog()
    assert commands._offset == 18
    assert opener.calls[0] == ("getUpdates",
                               {"timeout": 0, "allowed_updates": ["message"], "offset": -1})


def test_ayar_dosyasinda_jeton_yok(tmp_path):
    TelegramConfig(chat_id=42, bot_kullanici_adi="albsat_bot", kurulum_utc="x").save(tmp_path)
    text = (tmp_path / "telegram.json").read_text()
    assert "42" in text and ":" not in text.replace('": ', "")
    assert TelegramConfig.load(tmp_path).chat_id == 42


# --- Anahtar Zinciri ------------------------------------------------------------------


def test_anahtar_zincirine_sir_komut_satirindan_gecmez(monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs.get("input")))
        if args[:2] == ["security", "find-generic-password"]:
            return subprocess.CompletedProcess(args, 0, stdout=TOKEN + "\n", stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(keychain, "available", lambda: True)
    monkeypatch.setattr(keychain.subprocess, "run", fake_run)
    keychain.write("albsat-telegram", "bot-token", SecretText(TOKEN))
    write_args, write_input = calls[0]
    assert write_args == ["security", "-i"]
    assert TOKEN not in " ".join(write_args)
    assert TOKEN in write_input
    assert all(TOKEN not in " ".join(args) for args, _ in calls)


def test_anahtar_zinciri_tuhaf_karakteri_reddeder(monkeypatch):
    monkeypatch.setattr(keychain, "available", lambda: True)
    with pytest.raises(keychain.KeychainError):
        keychain.write("albsat-telegram", "bot-token", SecretText('abc" ; rm -rf ~ "defgh'))


# --- komutlar ----------------------------------------------------------------------------


def test_durdur_komutu_acil_durdurmayi_calistirir(paper):  # noqa: F811
    order = place(paper)
    handler = build_handler(paper, marks=lambda: {}, connection=lambda: None,
                            clock=lambda: NOW)
    reply = handler("/durdur", "")
    assert "Acil durdurma çalıştı" in reply
    assert paper.modes.get("BTCUSDT") == MODE_ADVICE
    assert paper.ledger.get(order.id).durum == "iptal"
    assert paper.audit.recent(1)[0].kaynak == SOURCE_TELEGRAM


def test_durum_komutu_hesabi_ozetler(paper):  # noqa: F811
    place(paper)
    handler = build_handler(paper, marks=lambda: {"BTCUSDT": D("60000")},
                            connection=lambda: {"akis_bagli": True}, clock=lambda: NOW)
    reply = handler("/durum", "")
    assert "Kâğıt hesap" in reply
    assert "Bekleyen giriş: BTCUSDT" in reply
    assert "canlı akış bağlı" in reply


def test_bilinmeyen_komut_yardim_doner(paper):  # noqa: F811
    handler = build_handler(paper, marks=lambda: {}, connection=lambda: None)
    assert "/durum" in handler("/satinal", "")


# --- rapor ---------------------------------------------------------------------------------


def _two_trades(paper):  # noqa: F811
    from test_kagit_islem import relax

    relax(paper, kayip_sonrasi_soguma_mum=0)
    paper.usdttry = D("41.25")
    place(paper)
    paper.process_candle("BTCUSDT", candle(1, "60050", "60050", "59990", "60000"))
    paper.process_candle("BTCUSDT", candle(2, "60500", "60950", "60400", "60900"))
    later = NOW + timedelta(minutes=3)
    place(paper, now=later, kaynak=SOURCE_MANUAL, kural_kimligi=None)
    paper.process_candle("BTCUSDT", candle(4, "60050", "60050", "59990", "60000"))
    paper.process_candle("BTCUSDT", candle(5, "59800", "59800", "59350", "59380"))


def test_ozet_kural_ve_elle_ayri(paper):  # noqa: F811
    _two_trades(paper)
    orders = paper.ledger.recent()
    rule = report.summarize(orders, "kural")
    manual = report.summarize(orders, SOURCE_MANUAL)
    assert rule.islem == 1 and rule.kazanan == 1 and rule.net_usdt > 0
    assert manual.islem == 1 and manual.kazanan == 0 and manual.net_usdt < 0
    assert rule.toplam_komisyon_usdt > 0
    assert rule.komisyon_brut_kar_orani is not None
    assert rule.cikis_sebepleri == {"hedefe ulaştı": 1}


def test_csv_her_islem_icin_alis_ve_satis_satiri(paper):  # noqa: F811
    _two_trades(paper)
    rows = report.csv_rows(paper.ledger.recent())
    assert [row["yon"] for row in rows] == ["ALIŞ", "SATIŞ", "ALIŞ", "SATIŞ"]
    buy = rows[0]
    assert buy["cift"] == "BTCUSDT" and buy["komisyon_varligi"] == "BTC"
    assert buy["usdttry_kuru"] == "41.25"
    assert D(buy["try_karsiligi"]) == (D(buy["usdt_karsiligi"]) * D("41.25")).quantize(
        D("0.01"))
    assert rows[1]["islem_net_usdt"]
    text = report.to_csv(paper.ledger.recent())
    assert text.startswith("﻿tarih_istanbul;tarih_utc;cift;yon;fiyat;miktar;komisyon")


def test_kur_yoksa_try_uydurulmaz(paper):  # noqa: F811
    place(paper)
    paper.process_candle("BTCUSDT", candle(1, "60050", "60050", "59990", "60000"))
    rows = report.csv_rows(paper.ledger.recent())
    assert rows[0]["try_karsiligi"] == "" and rows[0]["usdttry_kuru"] == ""
