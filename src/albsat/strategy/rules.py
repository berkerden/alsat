"""Kabul edilmiş kuralların deposu — Faz 2'nin çıktısı, Faz 3'ün girdisi.

Faz 2 taraması ekrana ve bir metin raporuna yazıyordu; ikisi de insan
okuması içindir. Öneri motorunun ise makinenin okuyabileceği bir kaynağa
ihtiyacı var: **hangi kurallar kabul edildi, hangi varsayımlarla, hangi
kanıtla.** Bu modül o köprüdür.

Üç kural burada bilinçli olarak sabitlenmiştir:

1. **Yalnızca kabul edilen kurallar öneri üretebilir.** Düzeltme öncesi
   dikkat çeken adaylar dosyaya ayrı bir listede, ``kabul: false`` ile ve
   "kanıt sayılmaz" notuyla yazılır. Öneri motoru o listeyi hiç okumaz;
   arayüz onları yalnızca "incelenen adaylar" bölümünde, kabul edilmedikleri
   yazılı olarak gösterir. Bunları öneri kartına çevirmek, Faz 2'nin
   engellemek için var olduğu çoklu test hatasını arayüzden geri sokardı.
2. **Kuralın maliyet varsayımları kuralla birlikte saklanır.** Bir kural
   "komisyon %0,1" varsayımıyla kabul edildiyse, o kuralın kartı da aynı
   varsayımla hesaplanmalı; aksi hâlde kartın marjı kuralın kanıtıyla
   uyuşmaz. Gerçek oran Faz 4'te ölçülünce tarama yenilenir.
3. **Teşhis turunun çıktısı kural üretmez.** ``--maliyetsiz`` koşusu
   maliyeti sıfır sayar; oradan çıkan hiçbir şey işlem önerisi değildir.
   Dosyada ``teshis_turu: true`` işaretlenir ve öneri motoru böyle bir
   dosyayı reddeder.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

#: Dosya biçimi sürümü. Alan eklenip çıkarıldıkça artar; öneri motoru
#: tanımadığı bir sürümü sessizce okumak yerine açıkça reddeder.
FORMAT_VERSION = 1

#: Kural deposunun varsayılan adı (veri dizininin altında).
DEFAULT_FILENAME = "kurallar.json"


class RuleStoreError(RuntimeError):
    """Kural deposu okunamadı veya güvenilmez."""


@dataclass(frozen=True)
class CostAssumptions:
    """Kuralın kabul edildiği maliyet varsayımları.

    **Birimlere dikkat.** Komisyon alanları *oran*dır (``"0.001"`` = %0,1),
    çünkü ``albsat.core.fees.flat_table`` oran bekler. Spread, kayma ve
    güvenlik payı ise *yüzde*dir, çünkü ``minimum_meaningful_target`` yüzde
    bekler. İkisi tek bir dosyada yan yana durduğu için adlar bunu söylüyor;
    "maliyet" deyip geçmek, 100 kat hatanın en kolay yoludur.
    """

    maker_orani: str
    taker_orani: str
    spread_yuzde: str
    kayma_yuzde: str
    guvenlik_payi_yuzde: str
    bnb_indirimi: bool
    #: Komisyon + spread + kayma + güvenlik payı toplamı (yüzde).
    minimum_hedef_yuzde: str
    aciklama: str

    @property
    def maker_yuzde(self) -> str:
        return str(Decimal(self.maker_orani) * Decimal("100"))

    @property
    def taker_yuzde(self) -> str:
        return str(Decimal(self.taker_orani) * Decimal("100"))

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> CostAssumptions:
        return cls(
            maker_orani=str(payload["maker_orani"]),
            taker_orani=str(payload["taker_orani"]),
            spread_yuzde=str(payload["spread_yuzde"]),
            kayma_yuzde=str(payload["kayma_yuzde"]),
            guvenlik_payi_yuzde=str(payload["guvenlik_payi_yuzde"]),
            bnb_indirimi=bool(payload["bnb_indirimi"]),
            minimum_hedef_yuzde=str(payload["minimum_hedef_yuzde"]),
            aciklama=str(payload.get("aciklama", "")),
        )


@dataclass(frozen=True)
class RuleEvidence:
    """Bir kuralın arkasındaki sayılar.

    Hepsi rapora da giren, kabul kararını açıklayan büyüklüklerdir. Öneri
    kartı "güven skoru ve bu skorun nasıl hesaplandığı" alanını (SPEC §4.4)
    buradan doldurur.
    """

    olay: int
    bagimsiz_olay: int
    kabul_ornegi: int
    isabet_orani: float
    net_ortalama_yuzde: float
    net_medyan_yuzde: float
    guven_alt_yuzde: float
    guven_ust_yuzde: float
    p_degeri: float
    q_degeri: float
    kabul_esigi_p: float
    rastgeleyi_geciyor: bool
    rastgele_yuzdelik: float
    donem_dogru_yon_orani: float
    walk_forward_dogru_yon_orani: float
    walk_forward_ortalama_yuzde: float
    test_donemi_net_yuzde: float
    hedefe_cikis_orani: float
    stopa_cikis_orani: float
    ortalama_tutulan_mum: float
    #: Pozisyon süresince görülen en iyi/en kötü fiyatın medyanı (MFE/MAE).
    #: Kademeli kâr alma seviyesi bundan türetilir; uydurulmuş bir ikinci
    #: hedef yerine ölçülmüş bir sayı kullanılsın diye saklanır.
    mfe_medyan_yuzde: float = 0.0
    mae_medyan_yuzde: float = 0.0

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> RuleEvidence:
        return cls(
            olay=int(payload["olay"]),
            bagimsiz_olay=int(payload["bagimsiz_olay"]),
            kabul_ornegi=int(payload["kabul_ornegi"]),
            isabet_orani=float(payload["isabet_orani"]),
            net_ortalama_yuzde=float(payload["net_ortalama_yuzde"]),
            net_medyan_yuzde=float(payload["net_medyan_yuzde"]),
            guven_alt_yuzde=float(payload["guven_alt_yuzde"]),
            guven_ust_yuzde=float(payload["guven_ust_yuzde"]),
            p_degeri=float(payload["p_degeri"]),
            q_degeri=float(payload["q_degeri"]),
            kabul_esigi_p=float(payload["kabul_esigi_p"]),
            rastgeleyi_geciyor=bool(payload["rastgeleyi_geciyor"]),
            rastgele_yuzdelik=float(payload["rastgele_yuzdelik"]),
            donem_dogru_yon_orani=float(payload["donem_dogru_yon_orani"]),
            walk_forward_dogru_yon_orani=float(payload["walk_forward_dogru_yon_orani"]),
            walk_forward_ortalama_yuzde=float(payload["walk_forward_ortalama_yuzde"]),
            test_donemi_net_yuzde=float(payload["test_donemi_net_yuzde"]),
            hedefe_cikis_orani=float(payload["hedefe_cikis_orani"]),
            stopa_cikis_orani=float(payload["stopa_cikis_orani"]),
            ortalama_tutulan_mum=float(payload["ortalama_tutulan_mum"]),
            mfe_medyan_yuzde=float(payload.get("mfe_medyan_yuzde", 0.0)),
            mae_medyan_yuzde=float(payload.get("mae_medyan_yuzde", 0.0)),
        )


@dataclass(frozen=True)
class Rule:
    """Öneri motorunun uygulayabileceği tek bir kural.

    ``ozellikler`` özellik tablosundaki sütun adlarıdır; kuralın tetiklenmesi
    "son kapanmış mumda bu sütunların hepsi ``True``" demektir.
    """

    sembol: str
    periyot: str
    yon: str
    ozellikler: tuple[str, ...]
    etiket: str
    #: Pozisyonun en fazla kaç mum tutulacağı; geçerlilik süresi de budur.
    pencere_mum: int
    hedef_atr: float
    stop_atr: float
    atr_periyodu: int
    kanit: RuleEvidence
    uyarilar: tuple[str, ...] = ()
    kabul: bool = False
    #: Kabul edilmemiş adaylar için, neden gösterildiğini açıklayan not.
    kabul_notu: str = ""

    @property
    def kimlik(self) -> str:
        """Sinyal günlüğünde ve arayüzde kuralı tekilleştiren kimlik."""
        return (
            f"{self.sembol}|{self.periyot}|{self.pencere_mum}|"
            f"{self.yon}|{'+'.join(self.ozellikler)}"
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["ozellikler"] = list(self.ozellikler)
        payload["uyarilar"] = list(self.uyarilar)
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Rule:
        return cls(
            sembol=str(payload["sembol"]),
            periyot=str(payload["periyot"]),
            yon=str(payload["yon"]),
            ozellikler=tuple(str(name) for name in payload["ozellikler"]),
            etiket=str(payload["etiket"]),
            pencere_mum=int(payload["pencere_mum"]),
            hedef_atr=float(payload["hedef_atr"]),
            stop_atr=float(payload["stop_atr"]),
            atr_periyodu=int(payload["atr_periyodu"]),
            kanit=RuleEvidence.from_dict(payload["kanit"]),
            uyarilar=tuple(str(note) for note in payload.get("uyarilar", ())),
            kabul=bool(payload.get("kabul", False)),
            kabul_notu=str(payload.get("kabul_notu", "")),
        )


@dataclass(frozen=True)
class SectionSummary:
    """Taranan tek bir bölümün özeti (sembol + periyot + pencere).

    "Önerilecek kural yok" sonucunu okuyan kişinin ihtiyacı olan sayılar
    burada: taban çizgisi neydi, kaç aday denendi, en iyi aday kabul
    eşiğinden ne kadar uzaktı.
    """

    sembol: str
    periyot: str
    pencere_mum: int
    hedef_atr: float
    stop_atr: float
    uygun_mum: int
    aday: int
    incelenen: int
    taban_net_ortalama_yuzde: float
    taban_isabet_orani: float
    taban_olay: int
    en_iyi_ham_p: float
    kabul_esigi_p: float
    kabul_edilen: int

    @property
    def esikten_uzaklik_kati(self) -> float:
        """En iyi aday, kabul eşiğinin kaç katı uzağında?

        1'in altı "eşiği geçti" demektir. Faz 2 koşusunda bu sayı 6 ile
        4.627 arasındaydı; "kıl payı kaçırdık" ile "yanından bile geçmedi"
        arasındaki farkı tek bakışta gösterir.
        """
        if self.kabul_esigi_p <= 0.0:
            return float("inf")
        return self.en_iyi_ham_p / self.kabul_esigi_p

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> SectionSummary:
        return cls(
            sembol=str(payload["sembol"]),
            periyot=str(payload["periyot"]),
            pencere_mum=int(payload["pencere_mum"]),
            hedef_atr=float(payload["hedef_atr"]),
            stop_atr=float(payload["stop_atr"]),
            uygun_mum=int(payload["uygun_mum"]),
            aday=int(payload["aday"]),
            incelenen=int(payload["incelenen"]),
            taban_net_ortalama_yuzde=float(payload["taban_net_ortalama_yuzde"]),
            taban_isabet_orani=float(payload["taban_isabet_orani"]),
            taban_olay=int(payload["taban_olay"]),
            en_iyi_ham_p=float(payload["en_iyi_ham_p"]),
            kabul_esigi_p=float(payload["kabul_esigi_p"]),
            kabul_edilen=int(payload["kabul_edilen"]),
        )


@dataclass(frozen=True)
class RunSummary:
    """Kuralları üreten tarama koşusunun kimliği ve varsayımları."""

    kosu_zamani_utc: str
    teshis_turu: bool
    semboller: tuple[str, ...]
    periyotlar: tuple[str, ...]
    pencereler: tuple[int, ...]
    toplam_aday: int
    toplam_incelenen: int
    alpha: float
    kabul_esigi_p: float
    maliyet: CostAssumptions
    veri_baslangic_utc: str = ""
    veri_bitis_utc: str = ""

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> RunSummary:
        return cls(
            kosu_zamani_utc=str(payload["kosu_zamani_utc"]),
            teshis_turu=bool(payload["teshis_turu"]),
            semboller=tuple(str(item) for item in payload["semboller"]),
            periyotlar=tuple(str(item) for item in payload["periyotlar"]),
            pencereler=tuple(int(item) for item in payload["pencereler"]),
            toplam_aday=int(payload["toplam_aday"]),
            toplam_incelenen=int(payload["toplam_incelenen"]),
            alpha=float(payload["alpha"]),
            kabul_esigi_p=float(payload["kabul_esigi_p"]),
            maliyet=CostAssumptions.from_dict(payload["maliyet"]),
            veri_baslangic_utc=str(payload.get("veri_baslangic_utc", "")),
            veri_bitis_utc=str(payload.get("veri_bitis_utc", "")),
        )


@dataclass(frozen=True)
class RuleSet:
    """Bir tarama koşusunun makine okunur tam çıktısı."""

    kosu: RunSummary
    bolumler: tuple[SectionSummary, ...] = ()
    #: Kabul edilen kurallar. Öneri motoru **yalnızca** bunları okur.
    kurallar: tuple[Rule, ...] = ()
    #: Kabul edilmeyen ama rapora not düşülen adaylar. Öneri değildir.
    incelenen_adaylar: tuple[Rule, ...] = ()
    surum: int = FORMAT_VERSION

    @property
    def kabul_edilen_sayisi(self) -> int:
        return len(self.kurallar)

    def for_symbol(self, sembol: str, periyot: str | None = None) -> tuple[Rule, ...]:
        """Kabul edilmiş kurallardan bu sembole (ve istenirse periyoda) ait olanlar."""
        return tuple(
            rule
            for rule in self.kurallar
            if rule.sembol == sembol and (periyot is None or rule.periyot == periyot)
        )

    def sections_for(
        self, sembol: str, periyot: str | None = None
    ) -> tuple[SectionSummary, ...]:
        return tuple(
            item
            for item in self.bolumler
            if item.sembol == sembol and (periyot is None or item.periyot == periyot)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "surum": self.surum,
            "kosu": asdict(self.kosu),
            "bolumler": [asdict(item) for item in self.bolumler],
            "kurallar": [rule.to_dict() for rule in self.kurallar],
            "incelenen_adaylar": [rule.to_dict() for rule in self.incelenen_adaylar],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> RuleSet:
        surum = int(payload.get("surum", 0))
        if surum != FORMAT_VERSION:
            raise RuleStoreError(
                f"Kural deposunun sürümü tanınmıyor (dosyada {surum}, "
                f"beklenen {FORMAT_VERSION}). Taramayı yeniden çalıştırın."
            )
        return cls(
            kosu=RunSummary.from_dict(payload["kosu"]),
            bolumler=tuple(
                SectionSummary.from_dict(item) for item in payload.get("bolumler", ())
            ),
            kurallar=tuple(Rule.from_dict(item) for item in payload.get("kurallar", ())),
            incelenen_adaylar=tuple(
                Rule.from_dict(item) for item in payload.get("incelenen_adaylar", ())
            ),
            surum=surum,
        )


def path_for(root: Path | str, filename: str = DEFAULT_FILENAME) -> Path:
    return Path(root) / filename


def save(ruleset: RuleSet, path: Path | str) -> Path:
    """Kural deposunu diske yazar (önce geçici dosya, sonra yerine koyma).

    ``path`` bir dosya yolu olabileceği gibi bir **dizin** de olabilir;
    dizin verilirse dosya adı ``path_for`` ile eklenir. Çağıranların yarısı
    veri dizinini, yarısı tam dosya yolunu elinde tutuyor ve ikisini
    karıştırmak sessiz bir hata değil, anlaşılmaz bir çökme üretiyordu.
    """
    path = Path(path)
    if path.is_dir():
        path = path_for(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(ruleset.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return path


def load(path: Path | str) -> RuleSet:
    """Kural deposunu okur.

    Dosya yoksa ``FileNotFoundError``, bozuksa ``RuleStoreError`` yükseltir.
    Sessizce boş küme döndürmek, "tarama hiç çalışmadı" ile "tarama çalıştı
    ve bir şey bulamadı" durumlarını birbirine karıştırırdı; bu ikisi
    kullanıcıya bambaşka şeyler söyler.
    """
    path = Path(path)
    raw = path.read_text(encoding="utf-8")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuleStoreError(f"Kural deposu okunamadı: {path} ({error})") from error
    if not isinstance(payload, Mapping):
        raise RuleStoreError(f"Kural deposunun biçimi beklenmedik: {path}")
    try:
        return RuleSet.from_dict(payload)
    except RuleStoreError:
        raise
    except (KeyError, TypeError, ValueError) as error:
        raise RuleStoreError(
            f"Kural deposunda eksik veya bozuk alan var: {path} ({error})"
        ) from error


def utc_now_text() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


# --------------------------------------------------------------------------
# Tarama sonuçlarından kural kümesi üretimi
# --------------------------------------------------------------------------


def _evidence(pattern: Any, *, kabul_esigi_p: float) -> RuleEvidence:
    """``PatternResult`` → ``RuleEvidence``.

    İmza ``Any``: bu modül ``research`` katmanına tip düzeyinde bağlanmasın
    diye. Çağıran taraf zaten ``PatternResult`` veriyor.
    """
    test = pattern.split("test")
    return RuleEvidence(
        olay=int(pattern.full.events),
        bagimsiz_olay=int(pattern.full.independent_events),
        kabul_ornegi=int(pattern.inference_events),
        isabet_orani=float(pattern.full.hit_rate),
        net_ortalama_yuzde=float(pattern.full.net_mean_pct),
        net_medyan_yuzde=float(pattern.full.net_median_pct),
        guven_alt_yuzde=float(pattern.bootstrap.low),
        guven_ust_yuzde=float(pattern.bootstrap.high),
        p_degeri=float(pattern.bootstrap.p_value),
        q_degeri=float(pattern.q_value),
        kabul_esigi_p=float(kabul_esigi_p),
        rastgeleyi_geciyor=bool(pattern.random.beats_random),
        rastgele_yuzdelik=float(pattern.random.percentile),
        donem_dogru_yon_orani=float(pattern.stable_share),
        walk_forward_dogru_yon_orani=float(pattern.walk_forward_positive_share),
        walk_forward_ortalama_yuzde=float(pattern.walk_forward_mean_pct),
        test_donemi_net_yuzde=float(test.net_mean_pct) if test is not None else 0.0,
        hedefe_cikis_orani=float(pattern.full.target_share),
        stopa_cikis_orani=float(pattern.full.stop_share),
        ortalama_tutulan_mum=float(pattern.full.bars_held_mean),
        mfe_medyan_yuzde=float(pattern.full.mfe_median_pct),
        mae_medyan_yuzde=float(pattern.full.mae_median_pct),
    )


def rule_from_pattern(
    pattern: Any,
    *,
    sembol: str,
    periyot: str,
    pencere_mum: int,
    hedef_atr: float,
    stop_atr: float,
    atr_periyodu: int,
    kabul_esigi_p: float,
    kabul_notu: str = "",
) -> Rule:
    return Rule(
        sembol=sembol,
        periyot=periyot,
        yon=str(pattern.direction),
        ozellikler=tuple(pattern.features),
        etiket=str(pattern.label_tr),
        pencere_mum=int(pencere_mum),
        hedef_atr=float(hedef_atr),
        stop_atr=float(stop_atr),
        atr_periyodu=int(atr_periyodu),
        kanit=_evidence(pattern, kabul_esigi_p=kabul_esigi_p),
        uyarilar=tuple(pattern.warnings_tr),
        kabul=bool(pattern.accepted),
        kabul_notu=kabul_notu,
    )


#: "Dikkat çeken" sayılmak için gereken ham p-değeri. Rapordaki eşikle aynı
#: olmalı; ikisi ayrışırsa arayüz ile rapor farklı aday listesi gösterir.
NOTABLE_P = 0.05

#: Kabul edilmemiş adayların dosyaya yazılan azami sayısı (bölüm başına).
NOTABLE_LIMIT = 5

NOT_ACCEPTED_NOTE = (
    "Bu aday çoklu test düzeltmesinden GEÇMEDİ. Kanıt sayılmaz, öneri "
    "üretmez; yalnızca taramanın neye baktığını göstermek için listelenir."
)


def build_ruleset(
    sections: Sequence[Any],
    *,
    cost: CostAssumptions,
    teshis_turu: bool,
    pencereler: Sequence[int],
    veri_baslangic_utc: str = "",
    veri_bitis_utc: str = "",
) -> RuleSet:
    """Tarama bölümlerinden kural kümesi üretir.

    ``sections`` öğeleri ``albsat.cli.research._Section`` biçimindedir:
    ``.result`` bir ``ScanResult``, ``.outcome_config`` bir ``OutcomeConfig``.
    Düzeltme koşunun tamamı üzerinden uygulandıktan **sonra** çağrılmalıdır;
    aksi hâlde dosyaya bölüm içi kabul kararları yazılır.
    """
    summaries: list[SectionSummary] = []
    accepted: list[Rule] = []
    notable: list[Rule] = []
    total_candidates = 0
    total_examined = 0
    alpha = 0.10
    global_threshold = 0.0

    for section in sections:
        result = section.result
        config = section.outcome_config
        alpha = result.config.alpha
        threshold = result.acceptance_p_threshold
        global_threshold = threshold
        total_candidates += int(result.candidates)
        total_examined += int(result.evaluated)

        patterns = tuple(result.buy_patterns) + tuple(result.avoid_patterns)
        summaries.append(
            SectionSummary(
                sembol=result.symbol,
                periyot=result.interval,
                pencere_mum=int(config.horizon),
                hedef_atr=float(config.target_atr),
                stop_atr=float(config.stop_atr),
                uygun_mum=int(result.outcomes.eligible_count),
                aday=int(result.candidates),
                incelenen=int(result.evaluated),
                taban_net_ortalama_yuzde=float(result.baseline.net_mean_pct),
                taban_isabet_orani=float(result.baseline.hit_rate),
                taban_olay=int(result.baseline.events),
                en_iyi_ham_p=float(result.best_raw_p_value),
                kabul_esigi_p=float(threshold),
                kabul_edilen=sum(1 for item in patterns if item.accepted),
            )
        )

        # Döngü değişkenlerini kapanışla taşımak yerine tek seferde
        # sabitliyoruz: kapanış tembel bir üreteçle birleşince sessizce
        # yanlış bölümün değerlerini okur.
        context = {
            "sembol": result.symbol,
            "periyot": result.interval,
            "pencere_mum": int(config.horizon),
            "hedef_atr": float(config.target_atr),
            "stop_atr": float(config.stop_atr),
            "atr_periyodu": int(config.atr_period),
            "kabul_esigi_p": float(threshold),
        }

        accepted.extend(
            rule_from_pattern(item, kabul_notu="", **context)
            for item in patterns
            if item.accepted
        )

        near = [
            item
            for item in sorted(patterns, key=lambda p: p.bootstrap.p_value)
            if not item.accepted and item.bootstrap.p_value < NOTABLE_P
        ]
        notable.extend(
            rule_from_pattern(item, kabul_notu=NOT_ACCEPTED_NOTE, **context)
            for item in near[:NOTABLE_LIMIT]
        )

    return RuleSet(
        kosu=RunSummary(
            kosu_zamani_utc=utc_now_text(),
            teshis_turu=bool(teshis_turu),
            semboller=tuple(dict.fromkeys(item.result.symbol for item in sections)),
            periyotlar=tuple(dict.fromkeys(item.result.interval for item in sections)),
            pencereler=tuple(int(item) for item in pencereler),
            toplam_aday=total_candidates,
            toplam_incelenen=total_examined,
            alpha=float(alpha),
            kabul_esigi_p=float(global_threshold),
            maliyet=cost,
            veri_baslangic_utc=veri_baslangic_utc,
            veri_bitis_utc=veri_bitis_utc,
        ),
        bolumler=tuple(summaries),
        kurallar=tuple(accepted),
        incelenen_adaylar=tuple(notable),
    )


__all__ = [
    "DEFAULT_FILENAME",
    "FORMAT_VERSION",
    "NOTABLE_P",
    "NOT_ACCEPTED_NOTE",
    "CostAssumptions",
    "Rule",
    "RuleEvidence",
    "RuleSet",
    "RuleStoreError",
    "RunSummary",
    "SectionSummary",
    "build_ruleset",
    "load",
    "path_for",
    "rule_from_pattern",
    "save",
    "utc_now_text",
]
