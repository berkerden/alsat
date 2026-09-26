import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


@pytest.fixture(autouse=True)
def _sir_dizini_yok(monkeypatch):
    """Testler sunucu kipinde (ALBSAT_SIR_DIZINI tanımlı) çalışsa da Mac'teki gibi davranır.

    Sunucuda sır dizini tanımlıyken IP kısıtı zorunludur (Faz 7); sahte borsa
    kısıtsız anahtar döndürdüğü için canlı testler aksi halde konteynerde farklı
    sonuç verirdi. Sır dizinini isteyen test kendisi tanımlar.
    """
    monkeypatch.delenv("ALBSAT_SIR_DIZINI", raising=False)
