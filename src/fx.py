"""과거 환율 시계열 — 백테스트의 USD→KRW 환산을 날짜별 실제 환율로.

토스 exchange-rate는 dateTime로 과거 조회 가능하나 단일값만 주고, 데이터가
~2023년부터만 존재한다. 따라서 월별로 샘플링해 파일 캐시에 모아두고,
필요한 날짜는 '가장 가까운 이전 값(forward-fill, 없으면 backfill)'으로 환산한다.

한계: 2023년 이전 구간은 환율 데이터가 없어 가장 이른 값으로 근사(backfill).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from datetime import date, timedelta

logger = logging.getLogger("toss.fx")

CACHE = Path(__file__).resolve().parent.parent / "data" / "fx" / "USD_KRW.json"


def _load() -> dict[str, float]:
    if CACHE.exists():
        try:
            return json.loads(CACHE.read_text())
        except Exception:
            return {}
    return {}


def _save(series: dict[str, float]) -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(series, ensure_ascii=False, indent=0, sort_keys=True))


def build_fx_series(client, start: str, end: str, *,
                    cadence_days: int = 30, base: str = "USD",
                    quote: str = "KRW") -> dict[str, float]:
    """[start, end] 구간을 cadence 간격으로 샘플링해 환율 시계열 구축(캐시 병합)."""
    series = _load()
    d = date.fromisoformat(start)
    d_end = date.fromisoformat(end)
    fetched = 0
    while d <= d_end:
        key = d.isoformat()
        if key not in series:
            try:
                # quiet=True: 2023년 이전 등 환율 미존재(404)는 정상이라 에러 로깅 생략
                r = client._get("/api/v1/exchange-rate", quiet=True, params={
                    "baseCurrency": base, "quoteCurrency": quote,
                    "dateTime": f"{key}T09:00:00+09:00"})
                series[key] = float(r["result"]["rate"])
                fetched += 1
            except Exception:
                series[key] = 0.0  # 데이터 없음 표시(0=미존재)
        d += timedelta(days=cadence_days)
    if fetched:
        _save(series)
        logger.info("환율 %d개 신규 수집 (총 %d)", fetched, len(series))
    return series


def fx_on(series: dict[str, float], day: str) -> float | None:
    """해당 날짜의 환율: 이전 가장 가까운 유효값(ffill), 없으면 이후 값(bfill)."""
    valid = {k: v for k, v in series.items() if v and v > 0}
    if not valid:
        return None
    keys = sorted(valid)
    prior = [k for k in keys if k <= day]
    if prior:
        return valid[prior[-1]]
    return valid[keys[0]]  # day가 가장 이른 데이터보다 과거 → backfill
