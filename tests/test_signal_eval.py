"""신호 성과 추적(이벤트 스터디) 로직 테스트."""

import pytest

from src import signal_eval


def _candles(closes):
    return [{"date": f"2024-{1+i//28:02d}-{1+i%28:02d}", "close": c}
            for i, c in enumerate(closes)]


def test_fwd_returns():
    closes = [100, 110, 120]
    assert signal_eval._fwd_returns(closes, [0], 2)[0] == pytest.approx(0.2)  # 120/100-1
    assert signal_eval._fwd_returns(closes, [0], 5) == []      # 범위 밖


def test_ma_signal_days_detected():
    # 급락 구간에서 종가<이평선 신호가 잡혀야
    closes = [100] * 25 + [80] * 10          # 후반 급락
    sig, base = signal_eval._signal_days(closes, "ma", 20, 14, 30)
    assert len(sig) > 0
    assert all(i >= 20 for i in sig)          # 워밍업 이후만


def test_evaluate_efficacy_structure():
    closes = [100 + (i % 5) * 3 for i in range(120)]
    res = signal_eval.evaluate_efficacy(_candles(closes), horizons=[5, 20])
    assert "ma" in res and "rsi" in res
    assert "per_horizon" in res["ma"]
    # edge 키 존재 확인
    for h, m in res["ma"]["per_horizon"].items():
        assert "edge" in m and "signal_hit" in m


def test_efficacy_handles_short_data():
    res = signal_eval.evaluate_efficacy(_candles([100, 101, 102]), horizons=[60])
    # 데이터 부족 → per_horizon 비어도 에러 없이 동작
    assert res["ma"]["per_horizon"] == {}
