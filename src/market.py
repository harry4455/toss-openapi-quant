"""시장 캘린더 헬퍼 — 거래일/휴장일 + 장중 시간대(정규장 등) 판별.

토스 market-calendar 응답에서 today에 regularMarket가 있으면 거래일.
세션 시각(startTime/endTime)으로 '지금 어느 장(정규/프리/애프터)인지'도 판별한다.
순수 판별 로직은 캘린더 dict만 받게 분리(테스트 용이).
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))


def is_trading_day_from_calendar(calendar: dict) -> bool:
    """캘린더 result dict → 오늘이 거래일인가.

    시장별 구조가 다름:
      US: today.regularMarket (직접)
      KR: today.integrated.regularMarket (중첩, 휴장일엔 integrated=null)
    """
    today = calendar.get("today") or {}
    if "regularMarket" in today:                       # US
        return True
    integrated = today.get("integrated") or {}
    return "regularMarket" in integrated               # KR


def next_business_day_from_calendar(calendar: dict) -> str | None:
    return calendar.get("nextBusinessDay", {}).get("date")


def _today_sessions(calendar: dict) -> dict:
    """오늘의 세션들 {이름: {startTime,endTime}}. KR은 today.integrated, US는 today 직접."""
    today = calendar.get("today") or {}
    container = today.get("integrated", today)   # US엔 integrated 키 없음 → today
    if not isinstance(container, dict):           # 휴장일 KR: integrated=null
        return {}
    return {k: v for k, v in container.items()
            if isinstance(v, dict) and "startTime" in v and "endTime" in v}


def market_phase_from_calendar(calendar: dict, now: datetime | None = None) -> str:
    """now가 속한 세션 이름(regularMarket/preMarket/afterMarket/dayMarket) 또는 'closed'."""
    now = now or datetime.now(KST)
    for name, sess in _today_sessions(calendar).items():
        try:
            start = datetime.fromisoformat(sess["startTime"])
            end = datetime.fromisoformat(sess["endTime"])
        except (ValueError, KeyError):
            continue
        if start <= now <= end:
            return name
    return "closed"


def is_open_now_from_calendar(calendar: dict, now: datetime | None = None) -> bool:
    """지금이 정규장(regularMarket) 시간인가."""
    return market_phase_from_calendar(calendar, now) == "regularMarket"


def is_trading_day(client, country: str, date: str | None = None) -> bool:
    """API 조회 후 거래일 여부. 조회 실패 시 True(보수적으로 막지 않음)."""
    try:
        cal = client.get_market_calendar(country, date)
        return is_trading_day_from_calendar(cal)
    except Exception:
        return True


def day_status(client, country: str, date: str | None = None,
               now: datetime | None = None) -> dict:
    """한 번 조회로 거래일 여부 + 현재 세션 단계 반환.

    {trading_day: bool, phase: str, open_now: bool}. 조회 실패 시 보수적 기본값.
    """
    try:
        cal = client.get_market_calendar(country, date)
    except Exception:
        return {"trading_day": True, "phase": "unknown", "open_now": False}
    phase = market_phase_from_calendar(cal, now)
    return {
        "trading_day": is_trading_day_from_calendar(cal),
        "phase": phase,
        "open_now": phase == "regularMarket",
    }
