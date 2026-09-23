"""Risk motoru testleri (SPEC.md §10 Faz 4 kabul kriteri: "Limitler test edilmiş").

Her limit için iki yön sınanır: sınırın hemen altında kapı açık, sınırda ya
da üstünde kapalı. Zaman parametre olarak verildiği için "günlük" ve
"haftalık" sınırlar gerçek zamanı beklemeden, İstanbul saatine göre
sınanabiliyor.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from test_sizing import PAYLOAD

from albsat.core.costs import round_trip_for
from albsat.core.fees import Liquidity, flat_table
from albsat.core.filters import SymbolRules
from albsat.risk import engine
from albsat.risk.engine import (
    AccountSnapshot,
    ClosedTrade,
    OpenExposure,
    OrderIntent,
    breaches,
    consecutive_losses,
    evaluate,
    max_drawdown_since,
    performance_check,
)
from albsat.risk.limits import LimitError, LimitStore, RiskLimits, validate
from albsat.risk.market import MarketState, spread_pct

# Pazartesi 21 Eylül 2026, 12:00 UTC = 15:00 İstanbul.
NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
D = Decimal


@pytest.fixture
def rules() -> SymbolRules:
    return SymbolRules.from_exchange_info(PAYLOAD)


def trips():
    table = flat_table("*", "0.001", "0.001")
    to_target = round_trip_for(table, entry_liquidity=Liquidity.MAKER,
                               exit_liquidity=Liquidity.MAKER)
    to_stop = round_trip_for(table, entry_liquidity=Liquidity.MAKER,
                             exit_liquidity=Liquidity.TAKER)
    return to_target, to_stop


def market(rules, **changes) -> MarketState:
    values = dict(
        sembol="BTCUSDT",
        son_veri_utc=NOW - timedelta(seconds=30),
        son_fiyat=D("60000"),
        atr_yuzde=D("0.5"),
        atr_medyan_yuzde=D("0.4"),
        spread_yuzde=D("0.0002"),
        spread_medyan_yuzde=D("0.0002"),
        hacim_24s_usdt=D("900000000"),
        btc_60dk_degisim_yuzde=D("0.4"),
        kurallar=rules,
    )
    values.update(changes)
    return MarketState(**values)


def intent(**changes) -> OrderIntent:
    values = dict(
        sembol="BTCUSDT", periyot="15m",
        giris=D("60000"), hedef=D("60900"), stop=D("59400"),
    )
    values.update(changes)
    return OrderIntent(**values)


def snapshot(**changes) -> AccountSnapshot:
    values = dict(baslangic_usdt=D("100"), serbest_usdt=D("100"))
    values.update(changes)
    return AccountSnapshot(**values)


def trade(net: str, *, minutes_ago: int = 60, sembol: str = "BTCUSDT",
          periyot: str = "15m", when: datetime | None = None) -> ClosedTrade:
    return ClosedTrade(
        sembol=sembol,
        periyot=periyot,
        kapanis_utc=when or NOW - timedelta(minutes=minutes_ago),
        net_usdt=D(net),
    )


def decide(rules, *, snap=None, limits=None, mkt=None, order=None, mode_open=True,
           disabled=()):
    to_target, to_stop = trips()
    return evaluate(
        order or intent(),
        snapshot=snap or snapshot(),
        limits=limits or RiskLimits(),
        market=mkt or market(rules),
        now=NOW,
        round_trip_to_target=to_target,
        round_trip_to_stop=to_stop,
        exit_slippage_pct=D("0.02"),
        mode_open=mode_open,
        disabled_rules=disabled,
    )


def gate(decision, name):
    found = [item for item in decision.kapilar if item.ad == name]
    assert found, f"{name} kapısı yok: {[item.ad for item in decision.kapilar]}"
    return found[0]


# --- varsayılanlar ve ayarlar ------------------------------------------------


def test_varsayilanlar_yapilandirma_dosyasiyla_ayni():
    """config/default.yaml ile RiskLimits ayrışırsa hangisi doğru belli olmaz."""
    text = (Path(__file__).parent.parent / "config" / "default.yaml").read_text("utf-8")
    section = text[text.index("\nrisk:") : text.index("\nmaliyet:")]
    found = dict(re.findall(r'^\s+(\w+):\s*"?([\d.]+)"?', section, flags=re.M))
    defaults = RiskLimits().to_json()
    assert found, "risk bölümü okunamadı"
    for key, value in found.items():
        assert key in defaults, f"{key} RiskLimits'te yok"
        assert D(str(defaults[key])) == D(value), key
    # Tersi: RiskLimits'teki her risk ayarı dosyada da yazıyor (bütçe hariç,
    # o "butce" bölümünde).
    assert set(defaults) - set(found) == {"butce_usdt"}


def test_butce_varsayilani_100_usdt():
    assert RiskLimits().butce_usdt == D("100")


def test_aralik_disi_deger_reddedilir_kirpilmaz():
    with pytest.raises(LimitError, match="arasında"):
        validate({"gunluk_max_zarar_yuzde": "300"}, RiskLimits())
    with pytest.raises(LimitError, match="sayı değil"):
        validate({"gunluk_max_zarar_yuzde": "üç"}, RiskLimits())
    with pytest.raises(LimitError, match="tam sayı"):
        validate({"art_arda_kayip_limiti": "2.5"}, RiskLimits())
    with pytest.raises(LimitError, match="Bilinmeyen"):
        validate({"kaldirac": "10"}, RiskLimits())


def test_virgullu_ondalik_kabul_edilir():
    limits = validate({"gunluk_max_zarar_yuzde": "2,5"}, RiskLimits())
    assert limits.gunluk_max_zarar_yuzde == D("2.5")


def test_ayarlar_kalici_ve_farklar_raporlanir(tmp_path):
    store = LimitStore.in_directory(tmp_path)
    assert store.read() == RiskLimits()
    new = validate({"art_arda_kayip_limiti": "3", "islem_basi_risk_yuzde": "0.5"},
                   store.read())
    store.write(new)
    again = LimitStore.in_directory(tmp_path).read()
    assert again.art_arda_kayip_limiti == 3
    assert again.islem_basi_risk_yuzde == D("0.5")
    assert store.differences(RiskLimits(), again) == {
        "islem_basi_risk_yuzde": ("1.0", "0.5"),
        "art_arda_kayip_limiti": ("4", "3"),
    }


# --- her şey yolundaysa ------------------------------------------------------


def test_butun_kapilar_acikken_izin_var(rules):
    decision = decide(rules)
    assert decision.izin, [item for item in decision.kapilar if not item.gecti]
    assert decision.pozisyon is not None and decision.pozisyon.gecerli


def test_mod_kapali_ise_emir_acilmaz(rules):
    decision = decide(rules, mode_open=False)
    assert not decision.izin
    assert not gate(decision, "mod").gecti


def test_maliyeti_karsilamayan_hedef_reddedilir(rules):
    # %0,1 + %0,1 komisyon; %0,1'lik hedef maliyeti karşılamaz.
    decision = decide(rules, order=intent(hedef=D("60060")))
    assert not gate(decision, "fiyatlar").gecti
    assert "maliyeti karşılamıyor" in gate(decision, "fiyatlar").aciklama


def test_tutarsiz_fiyatlar_reddedilir(rules):
    decision = decide(rules, order=intent(stop=D("61000")))
    assert not gate(decision, "fiyatlar").gecti


# --- günlük zarar --------------------------------------------------------------


def test_gunluk_zarar_sinirinin_altinda_acik(rules):
    decision = decide(rules, snap=snapshot(kapanan=(trade("-1.00"),)))
    assert gate(decision, "gunluk_zarar").gecti


def test_gunluk_zarar_sinirinda_kapali_ve_ertesi_gunu_soyler(rules):
    decision = decide(rules, snap=snapshot(kapanan=(trade("-3.00"),)))
    kapi = gate(decision, "gunluk_zarar")
    assert not kapi.gecti
    assert "22.09.2026 00:00" in kapi.aciklama  # İstanbul'da ertesi gün
    assert "elle" in kapi.aciklama


def test_gunluk_zarar_istanbul_gunune_gore_sayilir(rules):
    """20 Eylül 22:30 UTC = 21 Eylül 01:30 İstanbul: bugüne sayılır."""
    snap = snapshot(kapanan=(trade("-3.00", when=datetime(2026, 9, 20, 22, 30, tzinfo=UTC)),))
    assert not gate(decide(rules, snap=snap), "gunluk_zarar").gecti
    # 20 Eylül 20:30 UTC = 23:30 İstanbul: dünkü gün, bugünü etkilemez.
    snap = snapshot(kapanan=(trade("-3.00", when=datetime(2026, 9, 20, 20, 30, tzinfo=UTC)),))
    assert gate(decide(rules, snap=snap), "gunluk_zarar").gecti


def test_gun_icinde_kar_zarari_dengeler(rules):
    snap = snapshot(kapanan=(trade("2.00", minutes_ago=120), trade("-4.00", minutes_ago=60)))
    # Net −2: sınırın (3) altında.
    assert gate(decide(rules, snap=snap), "gunluk_zarar").gecti


def test_ileriye_bakis_stop_zarari_siniri_asacaksa_kapali(rules):
    """Bugünkü zarar 2,5; bu emir stopa giderse ~1 daha: sınır 3 aşılır."""
    snap = snapshot(kapanan=(trade("-2.50"),))
    decision = decide(rules, snap=snap)
    assert gate(decision, "gunluk_zarar").gecti
    kapi = gate(decision, "gunluk_zarar_ileri")
    assert not kapi.gecti
    assert not decision.izin


def test_ileriye_bakis_acik_pozisyonun_stop_zararini_da_sayar(rules):
    acik = (OpenExposure("SOLUSDT", D("50"), D("2.20")),)
    snap = snapshot(kapanan=(trade("-0.50"),), acik=acik,
                    serbest_usdt=D("50"))
    limits = RiskLimits(max_es_zamanli_pozisyon=2)
    decision = decide(rules, snap=snap, limits=limits)
    assert not gate(decision, "gunluk_zarar_ileri").gecti


# --- haftalık / aylık düşüş -----------------------------------------------------


def test_haftalik_dusus_tepeden_olculur():
    # Pazartesi başladı: +4, sonra −5, −5 → tepe 104, dip 94 → düşüş 10.
    snap = snapshot(kapanan=(
        trade("4", when=datetime(2026, 9, 21, 6, tzinfo=UTC)),
        trade("-5", when=datetime(2026, 9, 21, 7, tzinfo=UTC)),
        trade("-5", when=datetime(2026, 9, 21, 8, tzinfo=UTC)),
    ))
    assert max_drawdown_since(snap, datetime(2026, 9, 20, 21, tzinfo=UTC)) == D("10")


def test_haftalik_dusus_sinirda_kapali(rules):
    # Geçen hafta −20 (bu haftayı etkilemez), bu hafta −6.
    snap = snapshot(kapanan=(
        trade("-20", when=datetime(2026, 9, 18, 9, tzinfo=UTC)),
        trade("-6", when=datetime(2026, 9, 20, 22, tzinfo=UTC)),  # pzt 01:00 İst
    ))
    limits = RiskLimits(gunluk_max_zarar_yuzde=D("20"), aylik_max_dusus_yuzde=D("80"))
    decision = decide(rules, snap=snap, limits=limits)
    assert not gate(decision, "haftalik_dusus").gecti


def test_aylik_dusus_sinirda_kapali(rules):
    snap = snapshot(kapanan=(trade("-10", when=datetime(2026, 9, 5, 9, tzinfo=UTC)),))
    decision = decide(rules, snap=snap)
    assert gate(decision, "haftalik_dusus").gecti
    assert not gate(decision, "aylik_dusus").gecti


# --- art arda kayıp ---------------------------------------------------------------


def test_art_arda_kayip_sayilir_ve_kazanc_sifirlar():
    snap = snapshot(kapanan=(
        trade("-1", minutes_ago=300), trade("0.5", minutes_ago=240),
        trade("-1", minutes_ago=180), trade("-1", minutes_ago=120),
    ))
    assert consecutive_losses(snap) == 2


def test_art_arda_kayip_sinirinda_kapali_ve_sifirlaninca_acik(rules):
    kayiplar = tuple(trade("-0.2", minutes_ago=m) for m in (400, 300, 200, 100))
    limits = RiskLimits(kayip_sonrasi_soguma_mum=0)
    decision = decide(rules, snap=snapshot(kapanan=kayiplar), limits=limits)
    assert not gate(decision, "art_arda_kayip").gecti
    sifirlanmis = snapshot(kapanan=kayiplar, art_arda_sifirlama_utc=NOW - timedelta(minutes=50))
    assert gate(decide(rules, snap=sifirlanmis, limits=limits), "art_arda_kayip").gecti


def test_islem_sonrasi_sinir_asimlari_raporlanir():
    kayiplar = tuple(trade("-0.8", minutes_ago=m) for m in (400, 300, 200, 100))
    found = {item.tur for item in breaches(snapshot(kapanan=kayiplar), RiskLimits(), NOW)}
    # 4 × 0,8 = 3,2 USDT → günlük %3,2 ve art arda 4.
    assert found == {engine.BREACH_DAILY, engine.BREACH_STREAK}


def test_sinir_asilmadiysa_rapor_bos():
    assert breaches(snapshot(kapanan=(trade("-1"),)), RiskLimits(), NOW) == ()


# --- soğuma ------------------------------------------------------------------------


def test_kayip_sonrasi_soguma_periyot_mumu_kadar(rules):
    # 15m işlem, 2 mum soğuma = 30 dakika.
    snap = snapshot(kapanan=(trade("-0.5", minutes_ago=20),))
    assert not gate(decide(rules, snap=snap), "soguma").gecti
    snap = snapshot(kapanan=(trade("-0.5", minutes_ago=31),))
    assert gate(decide(rules, snap=snap), "soguma").gecti


def test_soguma_yalnizca_o_coin_icin(rules):
    snap = snapshot(kapanan=(trade("-0.5", minutes_ago=5, sembol="SOLUSDT"),))
    assert gate(decide(rules, snap=snap), "soguma").gecti


def test_kazancla_kapanan_islemden_sonra_soguma_yok(rules):
    snap = snapshot(kapanan=(trade("0.5", minutes_ago=1),))
    assert gate(decide(rules, snap=snap), "soguma").gecti


# --- maruziyet ------------------------------------------------------------------


def test_eszamanli_pozisyon_siniri(rules):
    acik = (OpenExposure("SOLUSDT", D("40"), D("0.9"), bekleyen=True),)
    decision = decide(rules, snap=snapshot(acik=acik, serbest_usdt=D("60")))
    assert not gate(decision, "es_zamanli").gecti


def test_gunluk_islem_sayisi_siniri(rules):
    decision = decide(rules, snap=snapshot(girisler_bugun=20))
    assert not gate(decision, "gunluk_islem").gecti
    assert gate(decide(rules, snap=snapshot(girisler_bugun=19)), "gunluk_islem").gecti


def test_coin_basina_maruziyet_siniri(rules):
    limits = RiskLimits(coin_basi_max_maruziyet_yuzde=D("50"))
    decision = decide(rules, limits=limits)
    # Dar stop: bütçe bağlayıcı, ~100 USDT'lik emir 50 USDT sınırını aşar.
    assert not gate(decision, "maruziyet").gecti


def test_serbest_bakiye_bitmisse_boyut_kapali(rules):
    decision = decide(rules, snap=snapshot(serbest_usdt=D("0")))
    assert not gate(decision, "boyut").gecti


def test_buyukluk_serbest_bakiyeyle_sinirlanir_risk_butceye_gore_kalir(rules):
    decision = decide(rules, snap=snapshot(serbest_usdt=D("50")))
    size = decision.pozisyon
    assert size is not None
    assert size.tutar_usdt <= D("50")
    # Hedeflenen risk hâlâ bot bütçesinin %1'i.
    assert size.hedeflenen_risk_usdt == D("1.0")


def test_kayma_risk_butcesinin_icinde_kalir(rules):
    """Geniş stopta risk bağlar; kayma dahil zarar hedeflenen riski aşmaz (komisyon hariç)."""
    order = intent(giris=D("100"), hedef=D("110"), stop=D("90"))
    rules_low = SymbolRules.from_exchange_info(
        {**PAYLOAD, "symbol": "XUSDT",
         "filters": [
             {"filterType": "PRICE_FILTER", "minPrice": "0.01", "maxPrice": "100000",
              "tickSize": "0.01"},
             {"filterType": "LOT_SIZE", "minQty": "0.001", "maxQty": "100000",
              "stepSize": "0.001"},
             {"filterType": "NOTIONAL", "minNotional": "1", "applyMinToMarket": True,
              "maxNotional": "9000000", "applyMaxToMarket": False, "avgPriceMins": 5},
         ]})
    decision = decide(rules, order=order, mkt=market(rules_low))
    size = decision.pozisyon
    assert size is not None and size.baglayici.value == "risk"
    fill = D("90") * (D("100") - D("0.02")) / D("100")
    assert size.miktar * (D("100") - fill) <= size.hedeflenen_risk_usdt


# --- piyasa filtreleri --------------------------------------------------------


def test_bayat_veri_kapali(rules):
    decision = decide(rules, mkt=market(rules, son_veri_utc=NOW - timedelta(minutes=10)))
    assert not gate(decision, "bayat_veri").gecti


def test_olculemeyen_kosul_gecmis_sayilmaz(rules):
    for alan, kapi in (("son_veri_utc", "bayat_veri"), ("spread_yuzde", "spread"),
                       ("atr_yuzde", "volatilite"), ("hacim_24s_usdt", "likidite"),
                       ("btc_60dk_degisim_yuzde", "btc_hareketi"), ("kurallar", "coin_uygunlugu")):
        decision = decide(rules, mkt=market(rules, **{alan: None}))
        sonuc = gate(decision, kapi)
        assert not sonuc.gecti and sonuc.olculemedi, alan
        assert not decision.izin


def test_asiri_oynaklik_kapali(rules):
    decision = decide(rules, mkt=market(rules, atr_yuzde=D("1.3"), atr_medyan_yuzde=D("0.4")))
    assert not gate(decision, "volatilite").gecti


def test_spread_normalin_k_kati_kapali(rules):
    decision = decide(rules, mkt=market(rules, spread_yuzde=D("0.002"),
                                        spread_medyan_yuzde=D("0.0002")))
    assert not gate(decision, "spread").gecti


def test_spread_mutlak_ust_sinir(rules):
    decision = decide(rules, mkt=market(rules, spread_yuzde=D("0.2"), spread_medyan_yuzde=None))
    assert not gate(decision, "spread").gecti


def test_dusuk_likidite_kapali(rules):
    decision = decide(rules, mkt=market(rules, hacim_24s_usdt=D("1000")))
    assert not gate(decision, "likidite").gecti


def test_btc_sert_hareketi_kapali(rules):
    decision = decide(rules, mkt=market(rules, btc_60dk_degisim_yuzde=D("3.5")))
    assert not gate(decision, "btc_hareketi").gecti


def test_islem_disi_sembol_kapali(rules):
    halted = SymbolRules.from_exchange_info({**PAYLOAD, "status": "BREAK"})
    decision = decide(rules, mkt=market(rules, kurallar=halted))
    assert not gate(decision, "coin_uygunlugu").gecti


def test_spread_hesabi():
    assert spread_pct(D("99.99"), D("100.01")) == D("0.02")
    assert spread_pct(D("0"), D("1")) is None
    assert spread_pct(D("2"), D("1")) is None


# --- performans bozulma koruması ---------------------------------------------


def test_performans_az_islemde_karar_vermez():
    check = performance_check("k", [-1.0] * 9, expected_pct=0.3, limits=RiskLimits())
    assert not check.bozuk and check.z is None


def test_performans_anlamli_kotu_sapma_kurali_durdurur():
    sonuclar = [-0.4, -0.2, -0.5, 0.1, -0.3, -0.6, -0.1, -0.4, 0.2, -0.5, -0.3, -0.2]
    check = performance_check("k", sonuclar, expected_pct=0.3, limits=RiskLimits())
    assert check.bozuk and check.z is not None and check.z < -2.33


def test_performans_iyi_sapma_durdurmaz():
    sonuclar = [0.8, 0.5, 1.1, 0.4, 0.9, 0.7, 0.6, 1.0, 0.5, 0.8]
    assert not performance_check("k", sonuclar, expected_pct=0.3, limits=RiskLimits()).bozuk


def test_gurultu_icindeki_sapma_durdurmaz():
    sonuclar = [0.9, -0.6, 1.1, -0.8, 0.7, -0.5, 1.2, -0.9, 0.3, -0.4]
    assert not performance_check("k", sonuclar, expected_pct=0.3, limits=RiskLimits()).bozuk


def test_durdurulmus_kural_emir_acamaz(rules):
    order = intent(kaynak=engine.SOURCE_RULE, kural_kimligi="kural-1")
    decision = decide(rules, order=order, disabled=("kural-1",))
    assert not gate(decision, "performans").gecti
    # Elle emir kural durdurmasından etkilenmez.
    manual = intent(kaynak=engine.SOURCE_MANUAL, kural_kimligi=None)
    assert decide(rules, order=manual, disabled=("kural-1",)).izin
