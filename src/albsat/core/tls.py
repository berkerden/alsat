"""TLS güven deposu.

Sorun: python.org'dan kurulan Python, macOS'un Anahtar Zinciri'ni değil
kendi kök sertifika listesini kullanır. Bilgisayarda trafiği açıp yeniden
imzalayan bir katman varsa (kurumsal ağ, VPN, bazı antivirüs programları),
Safari ve Chrome çalışır ama Python
``CERTIFICATE_VERIFY_FAILED: self-signed certificate in certificate chain``
hatası verir.

Çözüm ``truststore``: Python'ı işletim sisteminin kendi güven deposunu
kullanacak şekilde ayarlar. Böylece macOS'un güvendiği her kök sertifikaya
Python da güvenir.

**Sertifika doğrulaması hiçbir koşulda kapatılmaz.** Doğrulamayı kapatmak,
araya girip trafiği değiştiren birine kapıyı açar; parayla iş yapan bir
uygulamada bu kabul edilemez. Geçici çözüm olarak bile yapılmaz.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_injected = False


def enable_system_trust() -> bool:
    """Python'ın işletim sistemi güven deposunu kullanmasını sağlar.

    ``True`` döndürürse artık macOS Anahtar Zinciri (veya Windows/Linux
    karşılığı) geçerlidir. ``truststore`` kurulu değilse ``False`` döner ve
    Python varsayılan listesiyle devam eder — doğrulama yine açıktır.
    """
    global _injected
    if _injected:
        return True
    try:
        import truststore
    except ImportError:
        logger.debug("truststore kurulu değil; varsayılan kök sertifikalar kullanılıyor.")
        return False
    truststore.inject_into_ssl()
    _injected = True
    logger.debug("TLS doğrulaması işletim sisteminin güven deposuna bağlandı.")
    return True


#: Sertifika doğrulama hatasında kullanıcıya gösterilecek açıklama.
CERTIFICATE_HELP = """\
Sunucunun sertifikası doğrulanamadı. Bu, bağlantının güvenli olmadığı
anlamına gelir; uygulama bu durumda veri indirmeyi reddeder.

En sık iki sebebi var:

  1. Bilgisayarınızda trafiği açıp yeniden imzalayan bir katman var:
     kurumsal ağ, VPN veya HTTPS taraması yapan bir antivirüs.
     Tarayıcınız çalışır çünkü o, işletim sisteminin sertifika listesini
     kullanır; Python ise kendi listesini kullanır.

  2. Python'ın kök sertifika listesi hiç kurulmamış. python.org'dan
     kurduysanız, Uygulamalar klasöründeki "Python 3.x" klasöründe bulunan
     "Install Certificates.command" dosyasına bir kez çift tıklamanız
     gerekir.

Hangisi olduğunu öğrenmek için şunu çalıştırın:

    python -m albsat.cli.tlsteshis

Sertifika doğrulamasını KAPATMAYIN. Bu uygulama ileride emir gönderecek;
doğrulamasız bir bağlantıda araya giren biri fiyatları ve emirleri
değiştirebilir."""
