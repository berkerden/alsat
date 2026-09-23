"""Canlı hesap yürütücüsü (Faz 6; SPEC.md §4.6, §4.7, §5).

Demo yürütücüsüyle aynı emir akışı, aynı koruma kuralı ve aynı uzlaştırma;
farkı, emirlerin **gerçek parayla** Binance canlı hesabına gitmesi. Bu yüzden
yeni giriş emrinin önüne ek kilitler konur. Koruma emirleri (stop, hedef,
korumalı çıkış) ve iptaller bu kilitlere takılmaz; elde coin varken stopun
borsada durması her şeyden önce gelir.

**Ek kilitler (yalnızca yeni giriş için):**

1. *Anahtar izinleri.* ``LiveTrader`` anahtarın izinlerini
   ``/sapi/v1/account/apiRestrictions``'tan okur. Para çekme, margin, vadeli
   işlem, opsiyon ya da transfer izni açıksa, Spot işlem izni kapalıysa ya da
   izinler son bir saatte okunamadıysa giriş emri gönderilmez. İzinler
   açılışta ve yarım saatte bir yeniden okunur; bozulursa canlı moddaki
   coinler Sadece Öneri'ye çekilir ve kullanıcıya bildirilir.
2. *Emir başına tutar tavanı.* Varsayılanı ``config/default.yaml`` →
   ``canli.emir_tavani_usdt``; arayüzden değiştirilir, bot bütçesini aşamaz.
   Risk motoru emri bu tavana göre boyutlar; ``LiveTrader`` göndermeden önce
   ayrıca sınar (iki ayrı yerde, biri atlanırsa diğeri yakalar).
3. *Mod.* Canlı emir yalnızca Yarı Otomatik ya da Tam Otomatik moddaki coin
   için gider. Yarı Otomatik'te kural sinyali **öneri** olur; kullanıcı
   arayüzden ya da Telegram'dan (``/onayla``) onaylamadan emir gitmez. Tam
   Otomatik'te yalnızca canlıya geçiş kapısını geçen kuralların sinyali emre
   dönüşür (``execution/gate.py``).
"""

from __future__ import annotations

import contextlib
import math
import secrets
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from albsat.core.audit import SOURCE_LOOP, SOURCE_SYSTEM
from albsat.core.clock import iso, istanbul_text, parse_utc, utc_now
from albsat.core.fees import Side
from albsat.data.klines import interval_ms
from albsat.data.live import LiveMarket
from albsat.exchange.trading import LiveTrader
from albsat.execution import errors
from albsat.execution.executor import (
    SIGNAL_MAX_AGE_SECONDS,
    OrderExecutor,
    PlaceResult,
)
from albsat.execution.gate import RuleGate, passed_for, rule_gates, symbol_summary
from albsat.execution.ledger import POS_REJECTED
from albsat.execution.venue import LIVE
from albsat.modes.state import LIVE_MODES, MODE_ADVICE, MODE_FULL, MODE_SEMI
from albsat.notify.base import KIND_SIGNAL, KIND_SYSTEM, Notifier
from albsat.paper.engine import OrderMeta, PaperEngine
from albsat.risk import engine as risk
from albsat.risk.market import Gate
from albsat.strategy.rules import RuleSet

STATE_CAP = "emir_tavani_usdt"
STATE_PROPOSALS = "oneriler"

#: ``config/default.yaml`` → ``canli.emir_tavani_usdt`` ile aynı olmalı
#: (``tests/test_canli.py`` denetler).
DEFAULT_CAP_USDT = Decimal("10")
#: İzinler bu kadar saniyede bir yeniden okunur. ``LiveTrader`` bir saatten eski
#: okumayla giriş göndermez; yarım saat, bir okuma kaçsa bile kilidin açık
#: kalmasını sağlar.
PERMISSION_RECHECK_SECONDS = 1800.0
#: Saklanan öneri sayısı (en yeniler).
PROPOSAL_KEEP = 50

PROPOSAL_WAITING = "bekliyor"
PROPOSAL_SENT = "gonderildi"
PROPOSAL_REJECTED = "reddedildi"
PROPOSAL_EXPIRED = "suresi_doldu"
PROPOSAL_CANCELLED = "iptal"
PROPOSAL_FAILED = "acilamadi"
PROPOSAL_STATES_TR = {
    PROPOSAL_WAITING: "Onay bekliyor",
    PROPOSAL_SENT: "Emir gönderildi",
    PROPOSAL_REJECTED: "Reddedildi",
    PROPOSAL_EXPIRED: "Süresi doldu",
    PROPOSAL_CANCELLED: "İptal (mod değişti)",
    PROPOSAL_FAILED: "Emir açılamadı",
}
PERMISSION_PROBLEM = "Canlı anahtar izinleri uygun değil"
#: Karışan harfler (0/o, 1/l) yok; Telegram'da elle yazılır.
_ID_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


class CapError(ValueError):
    """Geçersiz tavan; mesaj kullanıcıya gösterilir."""


class ProposalError(ValueError):
    """Öneri onaylanamıyor/reddedilemiyor; mesaj kullanıcıya gösterilir."""


@dataclass(frozen=True)
class Proposal:
    """Yarı Otomatik'te onay bekleyen kural sinyali."""

    kimlik: str
    olusturma_utc: str
    bitis_utc: str
    sembol: str
    periyot: str
    kural_kimligi: str
    kural_etiketi: str
    giris: str
    hedef: str
    stop: str
    sinyal_mumu_utc: str | None
    azami_tutma_mum: int
    beklenen_hedef_net_yuzde: float | None
    beklenen_ortalama_yuzde: float | None
    tahmini_tutar_usdt: str | None
    notlar: tuple[str, ...]
    durum: str = PROPOSAL_WAITING
    sonuc: str | None = None
    pozisyon_id: int | None = None
    karar_utc: str | None = None
    karar_kaynagi: str | None = None

    @property
    def durum_tr(self) -> str:
        return PROPOSAL_STATES_TR.get(self.durum, self.durum)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["notlar"] = list(self.notlar)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Proposal:
        values = dict(data)
        values["notlar"] = tuple(values.get("notlar") or ())
        return cls(**values)


def _proposal_id(taken: set[str]) -> str:
    """Kısa, elle yazılabilir kimlik (Telegram'da ``/onayla ab12cd``)."""
    while True:
        value = "".join(secrets.choice(_ID_ALPHABET) for _ in range(6))
        if value not in taken:
            return value


class LiveExecutor(OrderExecutor):
    """Binance canlı hesabının (gerçek para) tek sahibi (Faz 6)."""

    venue = LIVE

    def __init__(
        self,
        root: Path | str,
        *,
        symbols: Sequence[str],
        engine: PaperEngine,
        live_market: LiveMarket,
        notifier: Notifier,
        trader: LiveTrader | None,
        public: Any = None,
        stream_factory: Callable[..., Any] | None = None,
        key_problem: str | None = None,
        ruleset_loader: Callable[[], RuleSet | None] | None = None,
        clock: Callable[[], datetime] = utc_now,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.ruleset_loader: Callable[[], RuleSet | None] = ruleset_loader or (lambda: None)
        super().__init__(root, symbols=symbols, engine=engine, live_market=live_market,
                         venue_market=live_market, notifier=notifier, trader=trader,
                         public=public, stream_factory=stream_factory,
                         book_stream_factory=None, key_problem=key_problem, clock=clock,
                         monotonic=monotonic)
        self._last_permission_check = -1e18
        if trader is not None:
            # İkinci savunma hattı: tavan, emri imzalayan sınıfta da sınanır.
            trader.entry_cap_usdt = self.cap_usdt

    @property
    def live_trader(self) -> LiveTrader | None:
        trader = self.trader
        return trader if isinstance(trader, LiveTrader) else None

    def _attach(self, engine: PaperEngine) -> None:
        engine.live_ready = self.not_ready_reason
        engine.full_auto_check = self.full_auto_reason

    # --- hazır olma ve izinler ----------------------------------------------------

    def not_ready_reason(self) -> str | None:
        reason = super().not_ready_reason()
        if reason is None and self.live_trader is not None:
            reason = self.live_trader.entry_block_reason()
        return reason

    def _check_key(self) -> str | None:
        trader = self.live_trader
        if trader is None:
            return self.venue.anahtar_yok
        state = trader.verify_permissions()
        self._last_permission_check = self.monotonic()
        for warning in state.uyarilar:
            self._note(warning)
        if not state.tamam:
            return f"{PERMISSION_PROBLEM}: " + " ".join(state.engeller)
        self._note("Canlı anahtarın izinleri okundu: para çekme kapalı, Spot işlem açık")
        return None

    def _account_problem(self, payload: dict[str, Any]) -> str | None:
        if not payload.get("canTrade"):
            return ("Canlı hesap işlem yapamıyor görünüyor (Binance 'canTrade' kapalı "
                    "diyor). Binance'te hesabın ve anahtarın durumunu kontrol edin.")
        account_type = payload.get("accountType")
        if account_type and account_type != "SPOT":
            return (f"Hesap türü {account_type}; bu uygulama yalnızca Spot hesapla "
                    "çalışır.")
        return None

    def _periodic(self, mono: float) -> None:
        if mono - self._last_permission_check >= PERMISSION_RECHECK_SECONDS:
            self._last_permission_check = mono
            self._recheck_permissions()
        self._tidy_proposals()

    def _recheck_permissions(self) -> None:
        trader = self.live_trader
        if trader is None:
            return
        try:
            state = trader.verify_permissions()
        except Exception as error:  # noqa: BLE001 - bir sonraki turda yeniden denenir
            info = errors.classify(error)
            self._note(f"Canlı anahtarın izinleri okunamadı: {info.mesaj_tr}")
            self._last_permission_check = self.monotonic() - PERMISSION_RECHECK_SECONDS + 60
            return
        current = self.health.hazirlik_sorunu
        if state.tamam:
            if current is not None and current.startswith(PERMISSION_PROBLEM):
                self.health.hazirlik_sorunu = None
                self._note("Canlı anahtarın izinleri yeniden uygun")
                self._notify("✅ Canlı anahtarın izinleri yeniden uygun. Canlı mod elle "
                             "yeniden seçilebilir.", KIND_SYSTEM)
            return
        problem = f"{PERMISSION_PROBLEM}: " + " ".join(state.engeller)
        if current == problem:
            return
        self.health.hazirlik_sorunu = problem
        self._note(problem)
        moved = self._leave_live_modes(problem)
        self.on_halt(PERMISSION_PROBLEM, SOURCE_SYSTEM)
        self.audit.write("canli_izin_sorunu", problem, kaynak=SOURCE_SYSTEM,
                         ayrinti={"sadece_oneriye_alinan": ", ".join(moved)})
        self._notify(
            f"⛔ CANLI İŞLEM DURDU — {problem}\n"
            f"Sadece Öneri'ye alınan coinler: {', '.join(moved) or 'yok'}. Bekleyen canlı "
            "girişler iptal ediliyor; açık pozisyonların stop ve hedefi yerinde.",
            KIND_SYSTEM,
        )

    def _leave_live_modes(self, reason: str) -> list[str]:
        moved: list[str] = []
        for symbol in self.symbols:
            if self.engine.modes.get(symbol) in LIVE_MODES:
                with contextlib.suppress(Exception):
                    self.engine.set_mode(symbol, MODE_ADVICE, source=SOURCE_SYSTEM,
                                         now=self.clock())
                    moved.append(symbol)
        if moved:
            self._note(f"Canlı moddan çıkarılan coinler: {', '.join(moved)} ({reason[:80]})")
        return moved

    def permission_json(self) -> dict[str, Any]:
        trader = self.live_trader
        if trader is None:
            return {"okundu": False, "aciklama": self.key_problem or self.venue.anahtar_yok}
        state = trader.permissions()
        age = None if state.zaman is None else round(self.monotonic() - state.zaman)
        return {
            "okundu": state.zaman is not None,
            "tamam": state.tamam,
            "yas_sn": age,
            "cekim_izni": state.cekim_izni,
            "islem_izni": state.islem_izni,
            "ip_kisitli": state.ip_kisitli,
            "engeller": list(state.engeller),
            "uyarilar": list(state.uyarilar),
            "son_hata": state.hata,
            "giris_engeli": trader.entry_block_reason(),
        }

    # --- tutar tavanı ------------------------------------------------------------------

    def cap_usdt(self) -> Decimal:
        """Emir başına tavan: kayıtlı değer ya da varsayılan; bot bütçesini aşmaz."""
        raw = self.ledger.get_state(STATE_CAP)
        try:
            value = Decimal(raw) if raw else DEFAULT_CAP_USDT
        except (InvalidOperation, ValueError):
            value = DEFAULT_CAP_USDT
        if not value > 0:
            value = DEFAULT_CAP_USDT
        return min(value, self.engine.limits.butce_usdt)

    def entry_cap(self, amount_usdt: Decimal | None = None) -> Decimal | None:
        cap = self.cap_usdt()
        return cap if amount_usdt is None else min(cap, amount_usdt)

    def update_cap(self, value: Any, *, source: str) -> Decimal:
        try:
            number = Decimal(str(value).strip().replace(",", "."))
        except (InvalidOperation, ValueError):
            raise CapError("Tavan bir sayı olmalı (ör. 10).") from None
        budget = self.engine.limits.butce_usdt
        if not number.is_finite() or number <= 0:
            raise CapError("Tavan sıfırdan büyük olmalı.")
        if number > budget:
            raise CapError(f"Tavan bot bütçesini ({budget} USDT) aşamaz.")
        number = number.quantize(Decimal("0.01"))
        with self.lock:
            old = self.cap_usdt()
            self.ledger.set_state(STATE_CAP, str(number))
            self.audit.write("canli_tavan", f"Canlı emir tavanı: {old} → {number} USDT",
                             kaynak=source, ayrinti={"eski": str(old), "yeni": str(number)})
        return number

    def venue_gates(self, intent: risk.OrderIntent, *,
                    quantity_usdt: Decimal | None) -> list[Gate]:
        gates = super().venue_gates(intent, quantity_usdt=quantity_usdt)
        trader = self.live_trader
        reason = self.venue.anahtar_yok if trader is None else trader.entry_block_reason()
        extra = [Gate("canli_izin", "Canlı anahtar izinleri", reason is None,
                      reason or "Para çekme ve diğer riskli izinler kapalı, Spot işlem açık; "
                      "izinler son bir saatte okundu.")]
        cap = self.cap_usdt()
        if quantity_usdt is not None:
            extra.append(Gate("canli_tavan", "Canlı emir tavanı", quantity_usdt <= cap,
                              f"Emir {quantity_usdt.quantize(Decimal('0.01'))} USDT, tavan "
                              f"{cap} USDT."))
        return [*gates[:1], *extra, *gates[1:]]

    # --- canlıya geçiş kapısı -------------------------------------------------------------

    def gates(self, now: datetime | None = None) -> list[RuleGate]:
        now = now or self.clock()
        try:
            ruleset = self.ruleset_loader()
        except Exception:  # noqa: BLE001 - okunamayan kural deposu "kabul yok" sayılır
            ruleset = None
        engine = self.engine
        return rule_gates(ruleset=ruleset, paper_orders=engine.ledger.all_closed(),
                          checks=engine.rule_checks(now), disabled=engine.disabled_rules(),
                          now=now)

    def full_auto_reason(self, symbol: str) -> str | None:
        """Tam Otomatik bu coin için açılabilir mi? ``None``: evet (kapıyı geçen kural var)."""
        gates = self.gates()
        if passed_for(symbol, gates):
            return None
        return symbol_summary(symbol, gates)

    # --- sinyaller ---------------------------------------------------------------------

    def place_from_card(self, card: Any, rule: Any) -> PlaceResult | None:
        """Canlı döngüden yeni AL sinyali: Yarı Otomatik'te öneri, Tam Otomatik'te
        (kural kapıyı geçtiyse) emir."""
        mode = self.engine.modes.get(card.sembol)
        if mode == MODE_SEMI:
            self.propose(card, rule)
            return None
        if mode != MODE_FULL:
            return None
        passed = {item.kural_kimligi for item in passed_for(card.sembol, self.gates())}
        if card.kural_kimligi not in passed:
            text = (f"{card.sembol} {card.kural_etiketi}: kural canlıya geçiş kapısını "
                    "geçmediği için Tam Otomatik emir açılmadı.")
            self._note(text)
            self._notify(f"ℹ️ {text}", KIND_SIGNAL)
            return None
        return super().place_from_card(card, rule)

    # --- Yarı Otomatik önerileri ----------------------------------------------------------

    def proposals(self) -> list[Proposal]:
        items = self.ledger.get_json(STATE_PROPOSALS, []) or []
        result: list[Proposal] = []
        for item in items:
            with contextlib.suppress(TypeError, ValueError):
                result.append(Proposal.from_dict(item))
        return result

    def _save_proposals(self, items: Sequence[Proposal]) -> None:
        self.ledger.set_json(STATE_PROPOSALS, [item.as_dict() for item in items][-PROPOSAL_KEEP:])

    def _replace(self, updated: Proposal) -> None:
        items = [updated if item.kimlik == updated.kimlik else item for item in self.proposals()]
        self._save_proposals(items)

    def waiting(self) -> list[Proposal]:
        return [item for item in self.proposals() if item.durum == PROPOSAL_WAITING]

    def propose(self, card: Any, rule: Any) -> Proposal | None:
        now = self.clock()
        signal_close = parse_utc(card.sinyal_mumu_kapanis_utc)
        if signal_close is not None and (now - signal_close).total_seconds() > \
                SIGNAL_MAX_AGE_SECONDS:
            self._note(f"{card.sembol} {card.kural_etiketi}: sinyal mumu eski, öneri yapılmadı")
            return None
        with self.lock:
            intent, meta = self.card_intent(card, rule)
            market = self.live_market.market_state(card.sembol, card.periyot,
                                                   self.rules_for(card.sembol))
            decision = self.evaluate(intent, market=market, now=now)
            step = interval_ms(card.periyot)
            base = signal_close or now
            existing = self.proposals()
            proposal = Proposal(
                kimlik=_proposal_id({item.kimlik for item in existing}),
                olusturma_utc=iso(now),
                bitis_utc=iso(base + timedelta(milliseconds=step * max(meta.gecerlilik_mum, 1))),
                sembol=card.sembol,
                periyot=card.periyot,
                kural_kimligi=card.kural_kimligi,
                kural_etiketi=card.kural_etiketi,
                giris=format(intent.giris, "f"),
                hedef=format(intent.hedef, "f"),
                stop=format(intent.stop, "f"),
                sinyal_mumu_utc=card.sinyal_mumu_kapanis_utc,
                azami_tutma_mum=meta.azami_tutma_mum,
                beklenen_hedef_net_yuzde=meta.beklenen_hedef_net_yuzde,
                beklenen_ortalama_yuzde=meta.beklenen_ortalama_yuzde,
                tahmini_tutar_usdt=None if decision.pozisyon is None
                else str(decision.pozisyon.tutar_usdt.quantize(Decimal("0.01"))),
                notlar=meta.notlar,
            )
            self._save_proposals([*existing, proposal])
            self.audit.write("canli_oneri", f"{card.sembol} {card.kural_etiketi}: onay "
                             f"bekleyen canlı öneri {proposal.kimlik}", kaynak=SOURCE_LOOP,
                             ayrinti={"oneri": proposal.kimlik, "giris": proposal.giris,
                                      "hedef": proposal.hedef, "stop": proposal.stop})
            gate_line = ("Risk kapıları şu an açık." if decision.izin else
                         f"Şu an kapalı kapı var: {decision.ozet_tr}")
            self._notify(
                f"🟡 CANLI ÖNERİ — onay bekliyor ({proposal.kimlik})\n"
                f"{card.sembol} {card.periyot} · {card.kural_etiketi}\n"
                f"Giriş {proposal.giris}, hedef {proposal.hedef}, stop {proposal.stop}"
                + (f", ≈ {proposal.tahmini_tutar_usdt} USDT" if proposal.tahmini_tutar_usdt
                   else "")
                + f"\n{gate_line}\n"
                f"Geçerlilik: {istanbul_text(parse_utc(proposal.bitis_utc) or now)}'a kadar.\n"
                f"GERÇEK PARA. Göndermek için: /onayla {proposal.kimlik} · Reddetmek için: "
                f"/reddet {proposal.kimlik} (ya da Canlı işlem sekmesi)",
                KIND_SIGNAL,
            )
            return proposal

    def _find(self, kimlik: str) -> Proposal:
        wanted = kimlik.strip().lower()
        for item in self.proposals():
            if item.kimlik == wanted:
                return item
        raise ProposalError(f"'{kimlik}' kimlikli öneri yok.")

    def approve(self, kimlik: str, *, source: str) -> PlaceResult:
        """Onay: öneriyi aynı risk kapılarından geçirip emri gönderir."""
        with self.lock:
            proposal = self._find(kimlik)
            if proposal.durum != PROPOSAL_WAITING:
                raise ProposalError(f"Öneri {proposal.kimlik} artık onay beklemiyor "
                                    f"({proposal.durum_tr}).")
            now = self.clock()
            end = parse_utc(proposal.bitis_utc)
            if end is not None and now >= end:
                self._replace(replace(proposal, durum=PROPOSAL_EXPIRED, karar_utc=iso(now)))
                raise ProposalError(f"Öneri {proposal.kimlik}'in süresi doldu; emir "
                                    "gönderilmedi.")
            mode = self.engine.modes.get(proposal.sembol)
            if mode not in LIVE_MODES:
                self._replace(replace(proposal, durum=PROPOSAL_CANCELLED, karar_utc=iso(now)))
                raise ProposalError(f"{proposal.sembol} artık canlı modda değil; öneri iptal "
                                    "edildi.")
            intent = risk.OrderIntent(
                sembol=proposal.sembol, periyot=proposal.periyot,
                giris=Decimal(proposal.giris), hedef=Decimal(proposal.hedef),
                stop=Decimal(proposal.stop), kaynak=risk.SOURCE_RULE,
                kural_kimligi=proposal.kural_kimligi)
            step = interval_ms(proposal.periyot)
            left_ms = 0 if end is None else (end - now) / timedelta(milliseconds=1)
            meta = OrderMeta(
                kural_etiketi=proposal.kural_etiketi,
                sinyal_mumu_utc=proposal.sinyal_mumu_utc,
                gecerlilik_mum=max(1, math.ceil(left_ms / step)),
                azami_tutma_mum=proposal.azami_tutma_mum,
                beklenen_hedef_net_yuzde=proposal.beklenen_hedef_net_yuzde,
                beklenen_ortalama_yuzde=proposal.beklenen_ortalama_yuzde,
                notlar=(*proposal.notlar, f"Yarı Otomatik öneri {proposal.kimlik} onaylandı."),
            )
            entry = intent.giris
            quote = self.venue_quote(proposal.sembol)
            rules = self.rules_for(proposal.sembol)
            if quote is not None and rules is not None and entry >= quote.satis:
                entry = rules.round_price(quote.alis, Side.BUY)
                intent = replace(intent, giris=entry)
            market = self.live_market.market_state(proposal.sembol, proposal.periyot, rules)
            result = self.place(intent, market=market, now=now, meta=meta, source=source,
                                reprice=True)
            position = result.pozisyon
            sent = position is not None and position.durum != POS_REJECTED
            self._replace(replace(
                proposal, durum=PROPOSAL_SENT if sent else PROPOSAL_FAILED,
                sonuc=result.mesaj, pozisyon_id=None if position is None else position.id,
                karar_utc=iso(now), karar_kaynagi=source))
            self.audit.write("canli_oneri_onay", f"Öneri {proposal.kimlik} onaylandı: "
                             f"{result.mesaj}", kaynak=source,
                             ayrinti={"oneri": proposal.kimlik, "gonderildi": sent})
            return result

    def reject(self, kimlik: str, *, source: str) -> Proposal:
        with self.lock:
            proposal = self._find(kimlik)
            if proposal.durum != PROPOSAL_WAITING:
                raise ProposalError(f"Öneri {proposal.kimlik} artık onay beklemiyor "
                                    f"({proposal.durum_tr}).")
            updated = replace(proposal, durum=PROPOSAL_REJECTED, karar_utc=iso(self.clock()),
                              karar_kaynagi=source)
            self._replace(updated)
            self.audit.write("canli_oneri_red", f"Öneri {proposal.kimlik} reddedildi",
                             kaynak=source, ayrinti={"oneri": proposal.kimlik})
            return updated

    def _tidy_proposals(self) -> None:
        """Süresi dolan ya da modu değişen coinlerin önerilerini kapatır."""
        items = self.proposals()
        if not any(item.durum == PROPOSAL_WAITING for item in items):
            return
        now = self.clock()
        changed = False
        result: list[Proposal] = []
        for item in items:
            if item.durum == PROPOSAL_WAITING:
                end = parse_utc(item.bitis_utc)
                if end is not None and now >= end:
                    item = replace(item, durum=PROPOSAL_EXPIRED, karar_utc=iso(now))
                    changed = True
                elif self.engine.modes.get(item.sembol) not in LIVE_MODES:
                    item = replace(item, durum=PROPOSAL_CANCELLED, karar_utc=iso(now))
                    changed = True
            result.append(item)
        if changed:
            self._save_proposals(result)

    # --- durum ---------------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        data = super().status()
        data["izinler"] = self.permission_json()
        data["tavan_usdt"] = str(self.cap_usdt())
        data["tavan_varsayilan_usdt"] = str(DEFAULT_CAP_USDT)
        return data


__all__ = [
    "DEFAULT_CAP_USDT",
    "PERMISSION_RECHECK_SECONDS",
    "PROPOSAL_STATES_TR",
    "CapError",
    "LiveExecutor",
    "Proposal",
    "ProposalError",
]
