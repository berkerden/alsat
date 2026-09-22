"""Alan nesnelerinin arayüze gidecek JSON karşılıkları.

Dönüşüm elle yazılmıştır, otomatik değil. Sebebi tek bir satırda: bu
uygulamada bir sayının ekrana **nasıl** yazıldığı, sayının kendisi kadar
önemlidir. ``Decimal`` doğrudan JSON'a verilemez; ``float``'a çevirmek
parasal değerlerde yasaktır (SPEC §3); ``str(Decimal)`` ise küçük sayılarda
``9.9E-7`` gibi bilimsel gösterim üretir ve kullanıcı bunu okuyamaz. Bu
yüzden her parasal alan ``format_for_api`` ile metne çevrilir ve arayüze
metin olarak gider; arayüz de onu aritmetiğe sokmaz, olduğu gibi gösterir.

Yüzdeler ve istatistikler ``float`` olarak gider: onlar ölçüm sonucudur,
emir fiyatı değildir.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from albsat.core.money import format_for_api
from albsat.risk.sizing import BINDING_LABELS_TR, PositionSize
from albsat.strategy.card import SignalCard
from albsat.strategy.journal import JournalEntry
from albsat.strategy.panel import CostRiskPanel
from albsat.strategy.plan import AccumulationPlan
from albsat.strategy.rules import Rule, RuleSet, SectionSummary
from albsat.strategy.signals import Recommendations
from albsat.strategy.watch import WatchBoard
from albsat.strategy.wizard import WizardResult


def money(value: Decimal | None) -> str | None:
    """Parasal değeri arayüze gidecek metne çevirir."""
    if value is None:
        return None
    return format_for_api(value)


def percent(value: Decimal | float | None, digits: int = 4) -> float | None:
    """Yüzdeyi yuvarlanmış ``float`` olarak verir (gösterim içindir)."""
    if value is None:
        return None
    return round(float(value), digits)


def position(item: PositionSize) -> dict[str, Any]:
    return {
        "butce_usdt": money(item.butce_usdt),
        "hedeflenen_risk_usdt": money(item.hedeflenen_risk_usdt),
        "miktar": money(item.miktar),
        "ham_miktar": money(item.ham_miktar),
        "tutar_usdt": money(item.tutar_usdt),
        "stop_zarari_usdt": money(item.stop_zarari_usdt),
        "baglayici": item.baglayici.value,
        "baglayici_tr": BINDING_LABELS_TR[item.baglayici],
        "gerceklesen_risk_yuzde": percent(item.gerceklesen_risk_yuzde, 2),
        "butce_kullanim_yuzde": percent(item.butce_kullanim_yuzde, 2),
        "yuvarlama_kaybi_yuzde": percent(item.yuvarlama_kaybi_yuzde, 4),
        "asgari_gecerli_miktar": money(item.asgari_gecerli_miktar),
        "gecerli": item.gecerli,
        "aciklama": item.aciklama_tr,
        "uyarilar": list(item.uyarilar),
    }


def card(item: SignalCard) -> dict[str, Any]:
    return {
        "sembol": item.sembol,
        "periyot": item.periyot,
        "kural_kimligi": item.kural_kimligi,
        "kural_etiketi": item.kural_etiketi,
        "aksiyon": item.aksiyon,
        "aksiyon_aciklama": item.aksiyon_aciklama,
        "sinyal_mumu_utc": item.sinyal_mumu_kapanis_utc,
        "sinyal_mumu_istanbul": item.sinyal_mumu_istanbul,
        "gecerlilik_mum": item.gecerlilik_mum,
        "gecerlilik_bitis_utc": item.gecerlilik_bitis_utc,
        "gecerlilik_bitis_istanbul": item.gecerlilik_bitis_istanbul,
        "giris": money(item.giris),
        "hedef1": money(item.hedef1),
        "hedef2": money(item.hedef2),
        "hedef2_notu": item.hedef2_notu,
        "stop": money(item.stop),
        "basa_bas": money(item.basa_bas),
        "brut_marj_yuzde": percent(item.brut_marj_yuzde),
        "net_marj_yuzde": percent(item.net_marj_yuzde),
        "hedef2_net_marj_yuzde": percent(item.hedef2_net_marj_yuzde),
        "stop_net_marj_yuzde": percent(item.stop_net_marj_yuzde),
        "risk_odul": percent(item.risk_odul, 2),
        "try_kuru": money(item.try_kuru),
        "giris_try": money(item.try_of(item.giris)),
        "stop_zarari_try": money(item.try_of(item.pozisyon.stop_zarari_usdt)),
        "pozisyon": position(item.pozisyon),
        "maliyet_ozeti": item.maliyet_ozeti,
        "guven": {
            "puan": item.guven.puan,
            "seviye": item.guven.seviye_tr,
            "aciklama": item.guven.aciklama_tr,
            "bilesenler": [
                {
                    "ad": part.ad,
                    "puan": round(part.puan, 1),
                    "azami": part.azami,
                    "aciklama": part.aciklama,
                }
                for part in item.guven.bilesenler
            ],
            "ceza": item.guven.ceza,
        },
        "gerekce": list(item.gerekce),
        "uyarilar": list(item.uyarilar),
        "filtreler_uygulandi": item.filtreler_uygulandi,
        "kanit": {
            "olay": item.kanit.olay,
            "bagimsiz_olay": item.kanit.bagimsiz_olay,
            "isabet_orani": round(item.kanit.isabet_orani, 4),
            "net_medyan_yuzde": round(item.kanit.net_medyan_yuzde, 4),
            "hedefe_cikis_orani": round(item.kanit.hedefe_cikis_orani, 4),
            "stopa_cikis_orani": round(item.kanit.stopa_cikis_orani, 4),
            "ortalama_tutulan_mum": round(item.kanit.ortalama_tutulan_mum, 2),
            "mfe_medyan_yuzde": round(item.kanit.mfe_medyan_yuzde, 4),
            "mae_medyan_yuzde": round(item.kanit.mae_medyan_yuzde, 4),
        },
    }


def rule(item: Rule) -> dict[str, Any]:
    evidence = item.kanit
    return {
        "kimlik": item.kimlik,
        "sembol": item.sembol,
        "periyot": item.periyot,
        "yon": item.yon,
        "etiket": item.etiket,
        "ozellikler": list(item.ozellikler),
        "pencere_mum": item.pencere_mum,
        "hedef_atr": item.hedef_atr,
        "stop_atr": item.stop_atr,
        "kabul": item.kabul,
        "kabul_notu": item.kabul_notu,
        "uyarilar": list(item.uyarilar),
        "kanit": {
            "olay": evidence.olay,
            "bagimsiz_olay": evidence.bagimsiz_olay,
            "kabul_ornegi": evidence.kabul_ornegi,
            "isabet_orani": round(evidence.isabet_orani, 4),
            "net_ortalama_yuzde": round(evidence.net_ortalama_yuzde, 4),
            "net_medyan_yuzde": round(evidence.net_medyan_yuzde, 4),
            "guven_alt_yuzde": round(evidence.guven_alt_yuzde, 4),
            "guven_ust_yuzde": round(evidence.guven_ust_yuzde, 4),
            "p_degeri": evidence.p_degeri,
            "q_degeri": evidence.q_degeri,
            "kabul_esigi_p": evidence.kabul_esigi_p,
            "rastgeleyi_geciyor": evidence.rastgeleyi_geciyor,
            "donem_dogru_yon_orani": round(evidence.donem_dogru_yon_orani, 4),
            "walk_forward_dogru_yon_orani": round(
                evidence.walk_forward_dogru_yon_orani, 4
            ),
            "test_donemi_net_yuzde": round(evidence.test_donemi_net_yuzde, 4),
            "mfe_medyan_yuzde": round(evidence.mfe_medyan_yuzde, 4),
            "mae_medyan_yuzde": round(evidence.mae_medyan_yuzde, 4),
        },
    }


def section(item: SectionSummary) -> dict[str, Any]:
    return {
        "sembol": item.sembol,
        "periyot": item.periyot,
        "pencere_mum": item.pencere_mum,
        "hedef_atr": item.hedef_atr,
        "stop_atr": item.stop_atr,
        "uygun_mum": item.uygun_mum,
        "aday": item.aday,
        "incelenen": item.incelenen,
        "taban_net_ortalama_yuzde": round(item.taban_net_ortalama_yuzde, 4),
        "taban_isabet_orani": round(item.taban_isabet_orani, 4),
        "taban_olay": item.taban_olay,
        "en_iyi_ham_p": item.en_iyi_ham_p,
        "kabul_esigi_p": item.kabul_esigi_p,
        "kabul_edilen": item.kabul_edilen,
        "esikten_uzaklik_kati": round(item.esikten_uzaklik_kati, 1),
    }


def run_summary(ruleset: RuleSet) -> dict[str, Any]:
    run = ruleset.kosu
    return {
        "kosu_zamani_utc": run.kosu_zamani_utc,
        "teshis_turu": run.teshis_turu,
        "semboller": list(run.semboller),
        "periyotlar": list(run.periyotlar),
        "pencereler": list(run.pencereler),
        "toplam_aday": run.toplam_aday,
        "toplam_incelenen": run.toplam_incelenen,
        "alpha": run.alpha,
        "kabul_esigi_p": run.kabul_esigi_p,
        "veri_baslangic_utc": run.veri_baslangic_utc,
        "veri_bitis_utc": run.veri_bitis_utc,
        "kabul_edilen_kural": ruleset.kabul_edilen_sayisi,
        "incelenen_aday": len(ruleset.incelenen_adaylar),
        "maliyet": {
            "maker_yuzde": run.maliyet.maker_yuzde,
            "taker_yuzde": run.maliyet.taker_yuzde,
            "spread_yuzde": run.maliyet.spread_yuzde,
            "kayma_yuzde": run.maliyet.kayma_yuzde,
            "guvenlik_payi_yuzde": run.maliyet.guvenlik_payi_yuzde,
            "bnb_indirimi": run.maliyet.bnb_indirimi,
            "minimum_hedef_yuzde": run.maliyet.minimum_hedef_yuzde,
            "aciklama": run.maliyet.aciklama,
        },
    }


def recommendations(item: Recommendations) -> dict[str, Any]:
    data: dict[str, Any] = {
        "sembol": item.sembol,
        "periyot": item.periyot,
        "durum": item.durum,
        "kabul_edilen_kural": item.kabul_edilen_kural,
        "kartlar": [card(one) for one in item.kartlar],
        "tetiklenmeyen_kurallar": list(item.tetiklenmeyen_kurallar),
        "aciklama": None,
        "veri": None,
    }
    if item.aciklama is not None:
        data["aciklama"] = {
            "baslik": item.aciklama.baslik,
            "satirlar": list(item.aciklama.satirlar),
            "bolumler": [section(one) for one in item.aciklama.bolumler],
        }
    if item.veri is not None:
        data["veri"] = {
            "mum_sayisi": item.veri.mum_sayisi,
            "ilk_mum_utc": item.veri.ilk_mum_utc,
            "son_kapanis_utc": item.veri.son_kapanis_utc,
            "gecikme_mum": round(item.veri.gecikme_mum, 2),
            "bayat": item.veri.bayat,
            "aciklama": item.veri.aciklama_tr,
        }
    return data


def wizard(item: WizardResult) -> dict[str, Any]:
    return {
        "sembol": item.sembol,
        "onerilen": list(item.onerilen),
        "gerekce": list(item.gerekce),
        "karar_notu": item.karar_notu,
        "satirlar": [
            {
                "periyot": row.periyot,
                "mum": row.mum,
                "veri_baslangic_utc": row.veri_baslangic_utc,
                "veri_bitis_utc": row.veri_bitis_utc,
                "atr_yuzde_medyan": percent(row.atr_yuzde_medyan),
                "minimum_hedef_yuzde": percent(row.minimum_hedef_yuzde),
                "oran": percent(row.oran, 2),
                "sonuc": row.sonuc,
                "aciklama": row.aciklama,
                "gunluk_mum": round(row.gunluk_mum, 1),
                "kabul_edilen_kural": row.kabul_edilen_kural,
                "gunluk_sinyal": round(row.gunluk_sinyal, 2),
                "haftalik_sinyal": round(row.haftalik_sinyal, 2),
                "taban_net_ortalama_yuzde": round(row.taban_net_ortalama_yuzde, 4),
                "taban_olay": row.taban_olay,
                "al_tut_net_yuzde": round(row.al_tut_net_yuzde, 2),
                "al_tut_max_dusus_yuzde": round(row.al_tut_max_dusus_yuzde, 2),
                "medyan_gunluk_hacim_usdt": round(row.medyan_gunluk_hacim_usdt, 0),
                "varsayilan_spread_yuzde": percent(row.varsayilan_spread_yuzde),
                "uygun": row.uygun,
            }
            for row in item.satirlar
        ],
    }


def panel(item: CostRiskPanel) -> dict[str, Any]:
    return {
        "sembol": item.sembol,
        "giris": money(item.giris),
        "hedef": money(item.hedef),
        "stop": money(item.stop),
        "basa_bas": money(item.basa_bas),
        "brut_marj_yuzde": percent(item.brut_marj_yuzde),
        "net_marj_yuzde": percent(item.net_marj_yuzde),
        "stop_net_marj_yuzde": percent(item.stop_net_marj_yuzde),
        "komisyon_yuzde": percent(item.komisyon_yuzde),
        "esik": {
            "komisyon_yuzde": percent(item.esik.fee_pct),
            "spread_yuzde": percent(item.esik.spread_pct),
            "kayma_yuzde": percent(item.esik.slippage_pct),
            "guvenlik_payi_yuzde": percent(item.esik.safety_pct),
            "minimum_hedef_yuzde": percent(item.esik.minimum_target_pct),
            "aciklama": item.esik.explain(),
        },
        "hedef_esigi_geciyor": item.hedef_esigi_geciyor,
        "risk_odul": percent(item.risk_odul, 2),
        "pozisyon": position(item.pozisyon),
        "hedef_kazanc_usdt": money(item.hedef_kazanc_usdt),
        "stop_zarari_usdt": money(item.stop_zarari_usdt),
        "odenecek_komisyon_usdt": money(item.odenecek_komisyon_usdt),
        "hedef_kazanc_try": money(item.try_of(item.hedef_kazanc_usdt)),
        "stop_zarari_try": money(item.try_of(item.stop_zarari_usdt)),
        "filtreler_uygulandi": item.filtreler_uygulandi,
        "uyarilar": list(item.uyarilar),
        "sonuc": item.sonuc_tr,
    }


def watch(item: WatchBoard) -> dict[str, Any]:
    return {
        "olusturma_utc": item.olusturma_utc,
        "not": item.not_tr,
        "satirlar": [
            {
                "sembol": row.sembol,
                "periyot": row.periyot,
                "son_kapanis_utc": row.son_kapanis_utc,
                "son_fiyat": money(row.son_fiyat),
                "mum_sayisi": row.mum_sayisi,
                "bayat": row.bayat,
                "gecikme_mum": round(row.gecikme_mum, 2),
                "gun_ici_dusuk": money(row.gun_ici_dusuk),
                "gun_ici_yuksek": money(row.gun_ici_yuksek),
                "degisim_1g_yuzde": round(row.degisim_1g_yuzde, 2),
                "degisim_7g_yuzde": round(row.degisim_7g_yuzde, 2),
                "degisim_30g_yuzde": round(row.degisim_30g_yuzde, 2),
                "atr_yuzde": percent(row.atr_yuzde),
                "atr_yuzde_medyan": percent(row.atr_yuzde_medyan),
                "maliyet_esigi_yuzde": percent(row.maliyet_esigi_yuzde),
                "oynaklik_orani": percent(row.oynaklik_orani, 2),
                "oynaklik_notu": row.oynaklik_notu_tr,
                "oynaklik_karsilastirma": row.gunluk_atr_karsilastirmasi,
                "seviyeler": [
                    {
                        "etiket": level.etiket,
                        "fiyat": money(level.fiyat),
                        "uzaklik_yuzde": percent(level.uzaklik_yuzde, 2),
                        "esik_kati": percent(level.esik_kati, 2),
                    }
                    for level in row.seviyeler
                ],
            }
            for row in item.satirlar
        ],
    }


def plan(item: AccumulationPlan) -> dict[str, Any]:
    data: dict[str, Any] = {
        "sembol": item.sembol,
        "kip": item.kip,
        "butce_usdt": money(item.butce_usdt),
        "dilim_sayisi": item.dilim_sayisi,
        "gecerli_dilim": item.gecerli_adim_sayisi,
        "guncel_fiyat": money(item.guncel_fiyat),
        "toplam_tutar_usdt": money(item.toplam_tutar_usdt),
        "toplam_komisyon_usdt": money(item.toplam_komisyon_usdt),
        "komisyon_orani_yuzde": percent(item.komisyon_orani_yuzde),
        "filtreler_uygulandi": item.filtreler_uygulandi,
        "uyarilar": list(item.uyarilar),
        "not": item.not_tr,
        "adimlar": [
            {
                "sira": step.sira,
                "tarih_utc": step.tarih_utc,
                "fiyat": money(step.fiyat),
                "tutar_usdt": money(step.tutar_usdt),
                "miktar": money(step.miktar),
                "gerceklesen_tutar_usdt": money(step.gerceklesen_tutar_usdt),
                "komisyon_usdt": money(step.komisyon_usdt),
                "gecerli": step.gecerli,
                "not": step.not_tr,
            }
            for step in item.adimlar
        ],
        "gecmis": None,
    }
    if item.gecmis is not None:
        history = item.gecmis
        data["gecmis"] = {
            "baslangic_utc": history.baslangic_utc,
            "bitis_utc": history.bitis_utc,
            "alim_sayisi": history.alim_sayisi,
            "ortalama_maliyet": money(history.ortalama_maliyet),
            "toplam_miktar": money(history.toplam_miktar),
            "yatirilan_usdt": money(history.yatirilan_usdt),
            "son_deger_usdt": money(history.son_deger_usdt),
            "net_yuzde": round(history.net_yuzde, 2),
            "toplam_komisyon_usdt": money(history.toplam_komisyon_usdt),
            "tek_seferde_net_yuzde": round(history.tek_seferde_net_yuzde, 2),
            "en_kotu_ara_deger_yuzde": round(history.en_kotu_ara_deger_yuzde, 2),
        }
    return data


def journal_entry(item: JournalEntry) -> dict[str, Any]:
    return {
        "id": item.id,
        "kayit_zamani_utc": item.kayit_zamani_utc,
        "kural_kimligi": item.kural_kimligi,
        "sembol": item.sembol,
        "periyot": item.periyot,
        "aksiyon": item.aksiyon,
        "sinyal_mumu_utc": item.sinyal_mumu_utc,
        "gecerlilik_bitis_utc": item.gecerlilik_bitis_utc,
        "giris": item.giris,
        "hedef1": item.hedef1,
        "stop": item.stop,
        "onerilen_net_yuzde": round(item.onerilen_net_yuzde, 4),
        "gerceklesen_net_yuzde": (
            None if item.gerceklesen_net_yuzde is None
            else round(item.gerceklesen_net_yuzde, 4)
        ),
        "fark_yuzde": (
            None if item.fark_yuzde is None else round(item.fark_yuzde, 4)
        ),
        "guven_puani": item.guven_puani,
        "durum": item.durum,
        "durum_tr": item.durum_tr,
    }


__all__ = [
    "card",
    "journal_entry",
    "money",
    "panel",
    "percent",
    "plan",
    "position",
    "recommendations",
    "rule",
    "run_summary",
    "section",
    "watch",
    "wizard",
]
