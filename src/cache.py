"""캔들 파일 캐시 — 같은 데이터 반복 호출로 인한 rate-limit(429) 방지.

심볼+interval 단위로 data/candles/{symbol}_{interval}.json 에 저장.
- 캐시가 신선하고(TTL 이내) 요청 수만큼 충분하면 → API 호출 없이 재사용
- 아니면 API 호출 후 저장

일봉은 하루 한 번만 갱신되므로 기본 TTL 12시간이면 충분하다.
"""

from __future__ import annotations

import json
import time
import logging
from pathlib import Path

logger = logging.getLogger("toss.cache")

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "candles"
DEFAULT_TTL = 12 * 3600  # 초


def _path(symbol: str, interval: str, adjusted: bool = True) -> Path:
    safe = symbol.replace("/", "_")
    # 분할 미보정 시계열은 별도 파일로 (같은 심볼·interval이라도 값이 다르다)
    suffix = "" if adjusted else "_raw"
    return CACHE_DIR / f"{safe}_{interval}{suffix}.json"


def get_candles_cached(client, symbol: str, interval: str = "1d",
                       count: int = 200, *, ttl: int = DEFAULT_TTL,
                       use_cache: bool = True, adjusted: bool = True) -> list[dict]:
    """get_candles 캐시 래퍼. 반환 형식은 client.get_candles와 동일(오래된→최신).

    adjusted=False면 분할 미보정 원본 시계열 (분할 계수 역산용, 별도 캐시).
    """
    p = _path(symbol, interval, adjusted)

    if use_cache and p.exists():
        try:
            blob = json.loads(p.read_text())
            fresh = (time.time() - blob.get("fetched_at", 0)) < ttl
            cached = blob.get("candles", [])
            if fresh and len(cached) >= count:
                logger.info("캐시 사용: %s %s (%d봉)", symbol, interval, len(cached))
                return cached[-count:]  # 최신 count개 (오래된→최신 유지)
        except Exception as exc:
            logger.warning("캐시 읽기 실패(%s), 새로 조회: %s", p, exc)

    candles = client.get_candles(symbol, interval=interval, count=count,
                                 adjusted=adjusted)
    if use_cache and candles:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(
            {"fetched_at": time.time(), "symbol": symbol,
             "interval": interval, "candles": candles},
            ensure_ascii=False))
        logger.info("캐시 저장: %s %s (%d봉)", symbol, interval, len(candles))
    return candles
