"""Emir gönderilen hesabın kimliği: Binance Demo Mode (sahte para) ya da canlı
hesap (gerçek para).

Faz 5'in yürütücüsü yalnızca Demo içindi. Faz 6'da aynı yürütücü iki hesap
için de çalışır; hesaba özgü her şey (emir kimliği öneki, kayıt tabloları,
Anahtar Zinciri kaydı, dosya adları, seçilebilen modlar ve kullanıcıya
gösterilen Türkçe adlar) burada tek yerde durur. İki hesabın kayıtları,
kimlikleri ve uzlaştırmaları birbirine hiç karışmaz.
"""

from __future__ import annotations

from dataclasses import dataclass

from albsat.data.commission import DEMO_FILENAME
from albsat.exchange.trading import KEYCHAIN_SERVICE_DEMO, KEYCHAIN_SERVICE_LIVE
from albsat.execution import ids
from albsat.execution.ledger import DEMO_TABLES, LIVE_TABLES
from albsat.modes.state import MODE_DEMO, MODE_FULL, MODE_LABELS_TR, MODE_SEMI

DEMO_INFO_FILENAME = "exchangeinfo-demo.json"


@dataclass(frozen=True)
class Venue:
    #: Kısa kod: denetim kaydı olaylarının ve kapı adlarının öneki.
    kod: str
    #: "Demo" / "Canlı"
    ad: str
    #: Bildirim başlıklarındaki parantez içi etiket: "(demo)" / "(CANLI)".
    etiket: str
    #: Bildirim başlığındaki büyük harfli ad: "DEMO EMİR" / "CANLI EMİR".
    buyuk: str
    #: Arayüzdeki bölüm adı: "Demo Mode" / "Canlı işlem".
    baslik: str
    ortam_tr: str
    gercek_para: bool
    ids: ids.IdScheme
    tablo_oneki: str
    #: Borsa filtreleri dosyası; ``None``: canlı piyasanın dosyası.
    bilgi_dosyasi: str | None
    #: Ölçülen komisyon dosyası; ``None``: canlı hesabın dosyası (Faz 4 ile ortak).
    komisyon_dosyasi: str | None
    anahtar_kaydi: str
    kurulum_komutu: str
    #: Bu hesaba emir gönderilen modlar.
    modlar: tuple[str, ...]
    #: Emir defteri canlı piyasadan ayrı mı (Demo'da evet).
    ayri_defter: bool
    # --- Türkçe ekler (cümle içinde doğru okunsun diye) ---
    hesap: str
    hesabin: str
    hesapta: str
    hesaptaki: str
    hesaptan: str
    pozisyon: str
    emri: str
    fiyat: str
    defter: str
    defterin: str
    defterde: str
    defterdeki: str
    deftere: str
    anahtar_yok: str
    bakiye_notu: str

    #: Risk kapısındaki "Mod" satırı: {sembol} ve {mod} yerine konur.
    mod_acik_tr: str
    mod_kapali_tr: str

    def mod_text(self, symbol: str, mode: str) -> str:
        template = self.mod_acik_tr if mode in self.modlar else self.mod_kapali_tr
        return template.format(sembol=symbol, mod=MODE_LABELS_TR.get(mode, mode))


DEMO = Venue(
    kod="demo",
    ad="Demo",
    etiket="demo",
    buyuk="DEMO",
    baslik="Demo Mode",
    ortam_tr="Binance Demo Mode (sahte para)",
    gercek_para=False,
    ids=ids.DEMO,
    tablo_oneki=DEMO_TABLES,
    bilgi_dosyasi=DEMO_INFO_FILENAME,
    komisyon_dosyasi=DEMO_FILENAME,
    anahtar_kaydi=KEYCHAIN_SERVICE_DEMO,
    kurulum_komutu="bash kurulum.sh demo-anahtar",
    modlar=(MODE_DEMO,),
    ayri_defter=True,
    hesap="Demo hesabı",
    hesabin="Demo hesabının",
    hesapta="Demo hesabında",
    hesaptaki="Demo hesabındaki",
    hesaptan="Demo hesabından",
    pozisyon="Demo pozisyonu",
    emri="Demo emri",
    fiyat="Demo fiyatı",
    defter="Demo defteri",
    defterin="Demo defterinin",
    defterde="Demo defterinde",
    defterdeki="Demo defterindeki",
    deftere="Demo defterine",
    anahtar_yok="Demo Mode anahtarı kurulu değil. Kurmak için: bash kurulum.sh demo-anahtar",
    bakiye_notu="Coin elle satılmış ya da Demo bakiyesi sıfırlanmış olabilir",
    mod_acik_tr="{sembol} Demo Mode'da.",
    mod_kapali_tr="{sembol} şu an '{mod}' modunda; Demo emri için Demo Mode'a alın.",
)

LIVE = Venue(
    kod="canli",
    ad="Canlı",
    etiket="CANLI",
    buyuk="CANLI",
    baslik="Canlı işlem",
    ortam_tr="Binance canlı hesap (GERÇEK PARA)",
    gercek_para=True,
    ids=ids.LIVE,
    tablo_oneki=LIVE_TABLES,
    bilgi_dosyasi=None,
    komisyon_dosyasi=None,
    anahtar_kaydi=KEYCHAIN_SERVICE_LIVE,
    kurulum_komutu="bash kurulum.sh canli-anahtar",
    modlar=(MODE_SEMI, MODE_FULL),
    ayri_defter=False,
    hesap="Canlı hesap",
    hesabin="Canlı hesabın",
    hesapta="Canlı hesapta",
    hesaptaki="Canlı hesaptaki",
    hesaptan="Canlı hesaptan",
    pozisyon="canlı pozisyon",
    emri="Canlı emir",
    fiyat="Piyasa fiyatı",
    defter="canlı defter",
    defterin="Canlı defterin",
    defterde="canlı defterde",
    defterdeki="canlı defterdeki",
    deftere="canlı deftere",
    anahtar_yok=("Canlı işlem anahtarı kurulu değil. Kurmak için: "
                 "bash kurulum.sh canli-anahtar"),
    bakiye_notu="Coin borsada elle satılmış olabilir",
    mod_acik_tr="{sembol} {mod} modunda (canlı hesap, gerçek para).",
    mod_kapali_tr=("{sembol} şu an '{mod}' modunda; canlı emir için Canlı işlem sekmesinden "
                   "Yarı Otomatik'e alın."),
)


__all__ = ["DEMO", "DEMO_INFO_FILENAME", "LIVE", "Venue"]
