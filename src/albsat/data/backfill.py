"""REST ile mum boşluklarını doldurma (``GET /api/v3/klines``).

Arşiv verisi (``albsat.data.vision``) ayın/günün bitmesini bekler; canlıya
kadar kalan kısım ve akış kopukluklarında oluşan boşluklar buradan
tamamlanır.

Doğrulanmış uç nokta kısıtları (rest-api.md, 2026-09):

* Ağırlık: 2
* ``limit`` varsayılan 500, **azami 1000**
* Mumlar ``open_time`` ile tekil olarak tanımlanır
* ``startTime``/``endTime`` verilmezse en güncel mumlar döner
"""

from __future__ import annotations

from typing import Protocol, Sequence

import pandas as pd

from albsat.data.klines import interval_ms, parse_klines

#: ``GET /api/v3/klines`` için azami ``limit`` (dokümanda sabit).
MAX_LIMIT = 1000

#: Bu uç noktanın istek ağırlığı.
REQUEST_WEIGHT = 2


class KlineSource(Protocol):
    """``GET /api/v3/klines`` çağrısını yapan katman."""

    def klines(
        self,
        *,
        symbol: str,
        interval: str,
        start_time: int | None = None,
        end_time: int | None = None,
        limit: int = MAX_LIMIT,
    ) -> Sequence[Sequence[object]]: ...


def fetch_range(
    source: KlineSource,
    *,
    symbol: str,
    interval: str,
    start_time: int,
    end_time: int,
    now_ms: int | None = None,
    max_pages: int = 10_000,
) -> pd.DataFrame:
    """``[start_time, end_time]`` aralığını sayfalayarak çeker.

    Her sayfadan sonra bir sonraki isteğin başlangıcı, alınan son mumun
    açılış zamanı + bir periyot olur. Bu, mum sınırlarına dayandığı için
    tekrar veya atlama üretmez.

    ``max_pages`` bir güvenlik kemeri: borsa beklenmedik bir yanıt
    verdiğinde döngünün sonsuza gitmesini engeller.
    """
    step = interval_ms(interval)
    cursor = start_time
    pages: list[pd.DataFrame] = []

    for _ in range(max_pages):
        if cursor > end_time:
            break
        rows = source.klines(
            symbol=symbol,
            interval=interval,
            start_time=cursor,
            end_time=end_time,
            limit=MAX_LIMIT,
        )
        if not rows:
            break
        frame = parse_klines(rows, interval=interval, now_ms=now_ms)
        if frame.empty:
            break
        pages.append(frame)

        last_open = int(frame["open_time"].iloc[-1])
        next_cursor = last_open + step
        if next_cursor <= cursor:
            # Borsa ilerlemeyen bir yanıt döndü; sonsuz döngüye girme.
            break
        cursor = next_cursor

        if len(rows) < MAX_LIMIT:
            break

    if not pages:
        return parse_klines([], interval=interval)
    return merge_frames(pages)


def merge_frames(frames: Sequence[pd.DataFrame]) -> pd.DataFrame:
    """Birden çok mum parçasını birleştirir; ``open_time`` tekilleştirilir.

    Çakışmada **son** kayıt kazanır: REST'ten gelen taze veri, arşivden
    gelen eskisinin üzerine yazar.
    """
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame(columns=["open_time", "open", "high", "low",
                                     "close", "volume", "is_closed"])
    combined = pd.concat(frames, ignore_index=True)
    combined = combined.drop_duplicates(subset="open_time", keep="last")
    return combined.sort_values("open_time").reset_index(drop=True)


def find_gaps(frame: pd.DataFrame, *, interval: str) -> list[tuple[int, int]]:
    """Eksik aralıkları ``(başlangıç_ms, bitiş_ms)`` listesi olarak döndürür.

    Dönen aralıklar doğrudan ``fetch_range``'e verilebilir.
    """
    if frame.empty or len(frame) < 2:
        return []
    step = interval_ms(interval)
    times = frame.sort_values("open_time")["open_time"].to_numpy()
    gaps: list[tuple[int, int]] = []
    for index in range(len(times) - 1):
        expected = int(times[index]) + step
        actual = int(times[index + 1])
        if actual > expected:
            gaps.append((expected, actual - step))
    return gaps


def repair(
    frame: pd.DataFrame,
    source: KlineSource,
    *,
    symbol: str,
    interval: str,
    now_ms: int | None = None,
) -> tuple[pd.DataFrame, int]:
    """Boşlukları REST ile doldurur; ``(düzeltilmiş_veri, doldurulan_mum)``."""
    gaps = find_gaps(frame, interval=interval)
    if not gaps:
        return frame, 0

    patches = [frame]
    for start, end in gaps:
        patch = fetch_range(
            source,
            symbol=symbol,
            interval=interval,
            start_time=start,
            end_time=end,
            now_ms=now_ms,
        )
        if not patch.empty:
            patches.append(patch)

    merged = merge_frames(patches)
    return merged, int(len(merged) - len(frame))


def extend_to_now(
    frame: pd.DataFrame,
    source: KlineSource,
    *,
    symbol: str,
    interval: str,
    now_ms: int,
) -> tuple[pd.DataFrame, int]:
    """Verinin bittiği yerden şu ana kadar olan mumları çeker.

    Arşivler ancak gün bittikten sonra yayımlandığı için yalnızca arşivle
    beslenen bir veri seti dün gece yarısında biter. Bu fonksiyon aradaki
    farkı REST ile kapatır; "veri eksiksiz ve canlı" olmasının koşuludur.

    ``(genişletilmiş_veri, eklenen_mum_sayısı)`` döndürür.
    """
    if frame.empty:
        return frame, 0
    step = interval_ms(interval)
    start_time = int(frame["open_time"].max()) + step
    if start_time > now_ms:
        return frame, 0
    tail = fetch_range(
        source, symbol=symbol, interval=interval,
        start_time=start_time, end_time=now_ms, now_ms=now_ms,
    )
    if tail.empty:
        return frame, 0
    merged = merge_frames([frame, tail])
    return merged, int(len(merged) - len(frame))
