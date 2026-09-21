"""TLS teşhis aracı: sertifikayı kimin imzaladığını gösterir.

``CERTIFICATE_VERIFY_FAILED`` hatasının iki sebebi vardır ve ayırt etmek
için sunucunun sunduğu sertifika zincirine bakmak yeterlidir:

* Zincirin tepesinde tanınmış bir sertifika otoritesi varsa (DigiCert,
  Let's Encrypt, Sectigo gibi) sorun sizin Python kurulumunuzdadır.
* Tepede bir şirket, antivirüs veya ağ ürünü adı varsa, trafiğiniz
  araya giren bir katman tarafından açılıp yeniden imzalanıyordur.

Bu araç **hiçbir veri alışverişi yapmaz**; yalnızca el sıkışma sırasında
sunulan sertifikayı okur ve bağlantıyı kapatır.
"""

from __future__ import annotations

import argparse
import socket
import ssl
import subprocess
import urllib.error
import urllib.request

from albsat.core.tls import enable_system_trust

#: Araya girme yapan ürünlerde sık görülen adlar (küçük harfe çevrilip aranır).
INTERCEPTOR_HINTS = (
    "kaspersky", "eset", "avast", "avg", "bitdefender", "norton", "mcafee",
    "sophos", "fortinet", "fortigate", "zscaler", "netskope", "bluecoat",
    "forcepoint", "paloalto", "checkpoint", "websense", "mitmproxy",
    "charles", "fiddler", "proxy", "firewall", "gateway", "adguard",
)

#: Tanınmış genel sertifika otoriteleri.
PUBLIC_CA_HINTS = (
    "digicert", "let's encrypt", "lets encrypt", "isrg", "sectigo", "comodo",
    "globalsign", "godaddy", "amazon", "google trust", "baltimore",
    "entrust", "identrust", "verisign", "thawte", "geotrust", "buypass",
    "certum", "actalis", "ssl.com",
)


def describe(name: tuple) -> str:
    """OpenSSL'in verdiği iç içe demeti okunabilir metne çevirir."""
    parts = {}
    for group in name:
        for key, value in group:
            parts[key] = value
    for key in ("organizationName", "commonName", "organizationalUnitName"):
        if key in parts:
            return parts[key]
    return str(parts)


def classify(issuer_text: str) -> str:
    lowered = issuer_text.lower()
    if any(hint in lowered for hint in INTERCEPTOR_HINTS):
        return "interceptor"
    if any(hint in lowered for hint in PUBLIC_CA_HINTS):
        return "public"
    return "unknown"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="albsat-tls-teshis",
        description="Sertifika doğrulama sorununun kaynağını bulur",
    )
    parser.add_argument("--host", default="api.binance.com")
    parser.add_argument("--port", type=int, default=443)
    args = parser.parse_args(argv)

    url = f"https://{args.host}/api/v3/time" if args.host == "api.binance.com" \
        else f"https://{args.host}/"
    print(f"Hedef: {args.host}:{args.port}\n")

    # Vekil sunucu ayarı, araya girmenin en sık sebebi. Uygulamanın HTTP
    # istemcisi bunu kullanır; düz soket kullanmaz. Bu yüzden önce gösterilir.
    proxies = {
        scheme: address
        for scheme, address in urllib.request.getproxies().items()
        if scheme in ("http", "https", "all")
    }
    if proxies:
        print("Sistemde tanımlı vekil sunucu (proxy) ayarları bulundu:")
        for scheme, address in sorted(proxies.items()):
            print(f"    {scheme}: {address}")
        print("  Trafiğiniz bu sunucudan geçiyor; sertifikayı büyük ihtimalle o")
        print("  yeniden imzalıyor.\n")
    else:
        print("Sistemde vekil sunucu ayarı yok.\n")

    # 1) Uygulamanın gerçekte kullandığı yol: urllib, varsayılan güven listesi.
    ok_default = _try_http(url, "Python'ın kendi kök sertifika listesi")

    # 2) Aynı yol, ama işletim sisteminin güven deposuyla.
    if enable_system_trust():
        ok_system = _try_http(
            url, "İşletim sisteminin güven deposu (truststore)"
        )
    else:
        ok_system = False
        print("  ! truststore kurulu değil; sistem deposu denenemedi.")
        print("    Kurmak için:  pip install truststore\n")

    if ok_system and not ok_default:
        print(
            "\nSonuç: sorun çözüldü. Python kendi listesiyle doğrulayamıyordu ama\n"
            "işletim sisteminin güven deposuyla doğruluyor. Fizibilite taraması\n"
            "bu depoyu kendiliğinden kullanıyor; taramayı tekrar çalıştırın:\n\n"
            "    bash kurulum.sh"
        )
        return 0
    if ok_default or ok_system:
        print("\nSonuç: bağlantı doğrulanabiliyor. Taramayı tekrar çalıştırabilirsiniz.")
        return 0

    # 3) Doğrulama başarısız: sunulan sertifikayı OKU (veri alışverişi yok).
    print("\nSunulan sertifika zinciri inceleniyor...")
    if proxies:
        print("(Not: bu okuma vekil sunucuyu atlayıp doğrudan bağlanır, bu yüzden\n gerçek zincirden farklı çıkabilir.)")
    print()
    issuer = _read_presented_issuer(args.host, args.port)
    if issuer is None:
        print(
            "Sertifika okunamadı. Bu genellikle bağlantının hiç kurulamadığı\n"
            "anlamına gelir: ağ engeli, güvenlik duvarı veya erişim kısıtlaması."
        )
        return 2

    kind = classify(issuer)
    print(f"Sertifikayı imzalayan: {issuer}\n")

    if kind == "interceptor":
        print(
            "Bu bir ara katman. Trafiğiniz açılıp yeniden imzalanıyor —\n"
            "kurumsal ağ, VPN veya HTTPS taraması yapan bir antivirüs.\n\n"
            "Yapılabilecekler:\n"
            "  • O ürünün HTTPS/SSL tarama özelliğini kapatın, veya\n"
            "  • Bu ağdan çıkıp normal bir internet bağlantısı kullanın, veya\n"
            "  • Kök sertifikası macOS Anahtar Zinciri'nde güvenilir olarak\n"
            "    işaretliyse, taramayı truststore ile çalıştırın (kurulu olmalı)."
        )
    elif kind == "public":
        print(
            "Bu tanınmış bir sertifika otoritesi, yani sunucu tarafı normal.\n"
            "Sorun sizin Python kurulumunuzun kök sertifika listesinde.\n\n"
            "Yapılacak: Uygulamalar klasöründeki \"Python 3.x\" klasörünü açın ve\n"
            "\"Install Certificates.command\" dosyasına çift tıklayın. Sonra\n"
            "Terminal'i kapatıp açın ve taramayı tekrar çalıştırın."
        )
    else:
        print(
            "Bu ad tanınmış bir sertifika otoritesine benzemiyor; büyük\n"
            "ihtimalle araya giren bir katman. Yukarıdaki adı Claude ile\n"
            "paylaşın."
        )

    print(
        "\nSertifika doğrulamasını kapatmayın. Bu uygulama ileride emir\n"
        "gönderecek; doğrulamasız bağlantıda araya giren biri fiyatları ve\n"
        "emirleri değiştirebilir."
    )
    return 1


def _try_http(url: str, label: str) -> bool:
    """Uygulamanın kullandığı yolun aynısını dener (vekil sunucu dahil)."""
    try:
        with urllib.request.urlopen(url, timeout=20) as response:
            response.read(64)
        print(f"  ✓ {label}: doğrulandı")
        return True
    except urllib.error.URLError as error:
        reason = getattr(error, "reason", error)
        if isinstance(reason, ssl.SSLCertVerificationError):
            print(f"  ✗ {label}: {reason.verify_message or reason}")
        else:
            print(f"  ✗ {label}: bağlanılamadı ({reason})")
    except (OSError, ssl.SSLError) as error:
        print(f"  ✗ {label}: {error}")
    return False


def _try_verified(host: str, port: int, label: str) -> bool:
    context = ssl.create_default_context()
    try:
        with socket.create_connection((host, port), timeout=15) as raw:
            with context.wrap_socket(raw, server_hostname=host):
                print(f"  ✓ {label}: doğrulandı")
                return True
    except ssl.SSLCertVerificationError as error:
        print(f"  ✗ {label}: {error.verify_message or error}")
    except (OSError, socket.timeout) as error:
        print(f"  ✗ {label}: bağlanılamadı ({error})")
    return False


def _read_presented_issuer(host: str, port: int) -> str | None:
    """Sunulan sertifika zincirinin en üstündeki imzalayanı okur.

    ``openssl s_client`` kullanılır (macOS'ta hazır gelir). Python'ın kendi
    soketi doğrulama kapalıyken sertifikayı ayrıştırmadığı için bu yol
    tercih edilir.

    Bu çağrı **hiçbir uygulama verisi göndermez**; el sıkışmanın ardından
    bağlantı hemen kapatılır. Uygulamanın gerçek HTTP istemcisi bu kodu
    asla kullanmaz ve her zaman doğrulama yapar.
    """
    try:
        result = subprocess.run(
            [
                "openssl", "s_client", "-connect", f"{host}:{port}",
                "-servername", host, "-showcerts",
            ],
            input="", capture_output=True, text=True, timeout=25,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None

    issuers = [
        line.split("i:", 1)[1].strip()
        for line in result.stdout.splitlines()
        if line.strip().startswith("i:")
    ]
    if not issuers:
        for line in result.stdout.splitlines():
            if line.startswith("issuer="):
                issuers.append(line.split("=", 1)[1].strip())
    if not issuers:
        return None

    top = issuers[-1]
    # "CN = X, O = Y" biçimindeki alanlardan okunabilir bir ad çıkar.
    fields = {}
    for part in top.replace("/", ", ").split(","):
        if "=" in part:
            key, value = part.split("=", 1)
            fields[key.strip().upper()] = value.strip()
    return fields.get("O") or fields.get("CN") or top


if __name__ == "__main__":
    raise SystemExit(main())
