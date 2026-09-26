"""Anahtar yükleme ve izin doğrulama testleri."""
import pytest

from albsat.exchange import keys
from albsat.exchange.keys import (
    ApiCredentials,
    KeyError_,
    SecretText,
    WithdrawalPermissionError,
    assert_no_withdrawal_permission,
    load_credentials,
    looks_like_ed25519,
)

PEM = (
    "-----BEGIN PRIVATE KEY-----"  # gizli-tarama: sahte
    "\nMC4CAQAwBQYDK2VwBCIEIA==\n-----END PRIVATE KEY-----"
)


def test_gizli_metin_reprde_gorunmez():
    # Yanlışlıkla print/logging'e düşerse anahtar sızmamalı.
    secret = SecretText("cok-gizli-anahtar")
    assert "cok-gizli-anahtar" not in repr(secret)
    assert "cok-gizli-anahtar" not in str(secret)
    assert secret.reveal() == "cok-gizli-anahtar"


def test_kimlik_bilgileri_reprde_maskelenir():
    creds = ApiCredentials(api_key="ABCD1234567890WXYZ", private_key=SecretText(PEM))
    text = repr(creds)
    assert "ABCD1234567890WXYZ" not in text
    assert PEM not in text
    assert "ABCD" in text and "WXYZ" in text


def test_dosyadan_yukleme(tmp_path):
    path = tmp_path / "ed25519.pem"
    path.write_text(PEM)
    path.chmod(0o600)
    creds = load_credentials({
        "ALBSAT_API_KEY": "kimlik",
        "ALBSAT_PRIVATE_KEY_PATH": str(path),
    })
    assert creds.private_key.reveal() == PEM


def test_genis_izinli_dosya_reddedilir(tmp_path):
    path = tmp_path / "ed25519.pem"
    path.write_text(PEM)
    path.chmod(0o644)  # başkaları okuyabilir
    with pytest.raises(KeyError_, match="izinleri çok geniş"):
        load_credentials({
            "ALBSAT_API_KEY": "kimlik",
            "ALBSAT_PRIVATE_KEY_PATH": str(path),
        })


def test_konum_belirtilmezse_hata():
    with pytest.raises(KeyError_, match="konumu belirtilmemiş"):
        load_credentials({"ALBSAT_API_KEY": "kimlik"})


def test_api_kimligi_yoksa_hata():
    with pytest.raises(KeyError_, match="ALBSAT_API_KEY"):
        load_credentials({})


def test_keychain_okumasi_anahtari_argumana_koymaz(monkeypatch, tmp_path):
    seen = {}

    class Result:
        stdout = PEM + "\n"

    def fake_run(command, **kwargs):
        seen["command"] = command
        return Result()

    monkeypatch.setattr(keys.subprocess, "run", fake_run)
    creds = load_credentials({
        "ALBSAT_API_KEY": "kimlik",
        "ALBSAT_KEYCHAIN_SERVICE": "albsat-binance",
    })
    assert creds.private_key.reveal() == PEM
    # Sır komut satırında geçmemeli; süreç listesinde görünür olurdu.
    assert not any(PEM in str(part) for part in seen["command"])


def test_hmac_anahtari_ed25519_degildir():
    # Binance HMAC anahtarı düz bir karakter dizisidir; session.logon ve
    # userDataStream.subscribe bunu kabul etmez.
    assert not looks_like_ed25519("a" * 64)
    assert looks_like_ed25519(PEM)


def test_cekim_izni_acikken_calisma_reddedilir():
    # Karar anahtarın kendi izinleriyle (apiRestrictions) verilir.
    with pytest.raises(WithdrawalPermissionError, match="para çekme izni AÇIK"):
        assert_no_withdrawal_permission(
            {"enableReading": True, "enableSpotAndMarginTrading": True,
             "enableWithdrawals": True}
        )


def test_islem_izni_kapaliysa_reddedilir():
    with pytest.raises(KeyError_, match="Spot işlem izni kapalı"):
        assert_no_withdrawal_permission(
            {"enableReading": True, "enableSpotAndMarginTrading": False,
             "enableWithdrawals": False})


def test_hesap_yaniti_anahtar_izni_sayilmaz():
    # /api/v3/account'taki canWithdraw hesabın bayrağıdır (Demo hesabı bile true
    # döndürüyor); bu yanıtla karar verilmez, durdurulur.
    with pytest.raises(KeyError_, match="canWithdraw"):
        assert_no_withdrawal_permission({"canTrade": True, "canWithdraw": False})


def test_dogru_izinler_gecer():
    assert assert_no_withdrawal_permission(
        {"enableReading": True, "enableSpotAndMarginTrading": True,
         "enableWithdrawals": False, "ipRestrict": False}
    ) is None
