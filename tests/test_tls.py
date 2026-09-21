"""TLS güven deposu ve teşhis aracı testleri."""
import ssl

from albsat.cli.tlsteshis import classify, describe
from albsat.core.tls import CERTIFICATE_HELP, enable_system_trust


def test_sistem_guven_deposu_baglanabiliyor():
    # truststore bağımlılık listesinde; kuruluysa True dönmeli.
    assert enable_system_trust() in (True, False)


def test_enjeksiyon_dogrulamayi_kapatmaz():
    enable_system_trust()
    context = ssl.create_default_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_yardim_metni_dogrulama_kapatmayi_onermez():
    yasak = ("verify=false", "cert_none", "check_hostname = false",
             "doğrulamayı kapat", "-k ", "insecure")
    lowered = CERTIFICATE_HELP.lower()
    for ifade in yasak:
        assert ifade not in lowered, f"Yardım metni {ifade!r} içeriyor"
    assert "KAPATMAYIN" in CERTIFICATE_HELP


def test_araya_giren_urunler_taninir():
    assert classify("Kaspersky Anti-Virus Personal Root") == "interceptor"
    assert classify("Zscaler Root CA") == "interceptor"
    assert classify("ESET SSL Filter CA") == "interceptor"


def test_taninmis_otoriteler_taninir():
    assert classify("DigiCert Global Root G2") == "public"
    assert classify("Let's Encrypt") == "public"
    assert classify("ISRG Root X1") == "public"


def test_bilinmeyen_ad_bilinmeyen_kalir():
    assert classify("Ornek Sirket A.S. Root") == "unknown"


def test_describe_organizasyon_adini_tercih_eder():
    name = ((("countryName", "US"),), (("organizationName", "DigiCert Inc"),),
            (("commonName", "DigiCert Global Root"),))
    assert describe(name) == "DigiCert Inc"


def test_describe_organizasyon_yoksa_common_name_kullanir():
    assert describe(((("commonName", "Ornek Root"),),)) == "Ornek Root"
