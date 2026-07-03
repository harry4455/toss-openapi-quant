"""pytest 공용 설정 — src 패키지 import 경로 + 합성 캔들 팩토리."""

import sys
from pathlib import Path
from datetime import date, timedelta

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _candles(closes, start="2024-01-01"):
    """종가 리스트 → 일봉 dict 리스트. 2024-01-01은 월요일."""
    d0 = date.fromisoformat(start)
    out = []
    for i, c in enumerate(closes):
        out.append({
            "date": (d0 + timedelta(days=i)).isoformat(),
            "open": c, "high": c, "low": c, "close": c, "volume": 1000.0,
        })
    return out


@pytest.fixture
def make_candles():
    return _candles
