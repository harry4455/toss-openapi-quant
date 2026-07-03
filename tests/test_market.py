"""시장 캘린더 거래일 + 장중 시간대 판별 테스트 (순수 로직 + 폴백)."""

from datetime import datetime, timezone, timedelta

from src import market

KST = timezone(timedelta(hours=9))


def _dt(s):
    return datetime.fromisoformat(s)


# 실제 응답 형태: KR 정규장 09:00~15:30 KST (integrated 중첩)
KR_SESSIONS = {"today": {"date": "2026-06-22", "integrated": {
    "preMarket": {"startTime": "2026-06-22T08:00:00+09:00",
                  "endTime": "2026-06-22T09:00:00+09:00"},
    "regularMarket": {"startTime": "2026-06-22T09:00:00+09:00",
                      "endTime": "2026-06-22T15:30:00+09:00"},
    "afterMarket": {"startTime": "2026-06-22T15:30:00+09:00",
                    "endTime": "2026-06-22T20:00:00+09:00"}}}}
# US 정규장은 KST로 22:30~익일 05:00 (today 직접, 자정 넘김)
US_SESSIONS = {"today": {"date": "2026-06-22",
    "regularMarket": {"startTime": "2026-06-22T22:30:00+09:00",
                      "endTime": "2026-06-23T05:00:00+09:00"},
    "preMarket": {"startTime": "2026-06-22T17:00:00+09:00",
                  "endTime": "2026-06-22T22:30:00+09:00"}}}


# US: regularMarket 직접 / KR: integrated.regularMarket 중첩
US_TRADING = {"today": {"date": "2026-06-22", "regularMarket": {"startTime": "..."}},
              "nextBusinessDay": {"date": "2026-06-23"}}
KR_TRADING = {"today": {"date": "2026-06-22",
                        "integrated": {"regularMarket": {"startTime": "..."}}},
              "nextBusinessDay": {"date": "2026-06-23"}}
KR_HOLIDAY = {"today": {"date": "2026-01-01", "integrated": None},  # 휴장: integrated null
              "nextBusinessDay": {"date": "2026-01-02"}}
US_HOLIDAY = {"today": {"date": "2026-01-01"},                      # regularMarket 없음
              "nextBusinessDay": {"date": "2026-01-02"}}
HOLIDAY = KR_HOLIDAY


def test_trading_day_true():
    assert market.is_trading_day_from_calendar(US_TRADING) is True
    assert market.is_trading_day_from_calendar(KR_TRADING) is True


def test_holiday_false():
    assert market.is_trading_day_from_calendar(KR_HOLIDAY) is False
    assert market.is_trading_day_from_calendar(US_HOLIDAY) is False


def test_next_business_day():
    assert market.next_business_day_from_calendar(HOLIDAY) == "2026-01-02"


def test_is_trading_day_uses_client():
    class StubClient:
        def get_market_calendar(self, country, date=None):
            return HOLIDAY
    assert market.is_trading_day(StubClient(), "KR") is False


def test_is_trading_day_fallback_true_on_error():
    class BadClient:
        def get_market_calendar(self, country, date=None):
            raise RuntimeError("network")
    # 조회 실패 시 보수적으로 True(매수 막지 않음)
    assert market.is_trading_day(BadClient(), "KR") is True


# ---- 장중 시간대(세션) 판별 ----
def test_kr_phase_regular():
    now = _dt("2026-06-22T10:00:00+09:00")          # 정규장 중
    assert market.market_phase_from_calendar(KR_SESSIONS, now) == "regularMarket"
    assert market.is_open_now_from_calendar(KR_SESSIONS, now) is True


def test_kr_phase_pre_and_closed():
    assert market.market_phase_from_calendar(
        KR_SESSIONS, _dt("2026-06-22T08:30:00+09:00")) == "preMarket"
    assert market.is_open_now_from_calendar(
        KR_SESSIONS, _dt("2026-06-22T08:30:00+09:00")) is False
    assert market.market_phase_from_calendar(
        KR_SESSIONS, _dt("2026-06-22T21:00:00+09:00")) == "closed"


def test_us_regular_spans_midnight():
    # 자정 넘긴 새벽 03:00 KST도 미국 정규장
    now = _dt("2026-06-23T03:00:00+09:00")
    assert market.market_phase_from_calendar(US_SESSIONS, now) == "regularMarket"
    assert market.is_open_now_from_calendar(US_SESSIONS, now) is True


def test_holiday_has_no_sessions():
    assert market.market_phase_from_calendar(
        KR_HOLIDAY, _dt("2026-01-01T10:00:00+09:00")) == "closed"


def test_day_status_combines(monkeypatch):
    class StubClient:
        def get_market_calendar(self, country, date=None):
            return KR_SESSIONS
    st = market.day_status(StubClient(), "KR", now=_dt("2026-06-22T10:00:00+09:00"))
    assert st == {"trading_day": True, "phase": "regularMarket", "open_now": True}
