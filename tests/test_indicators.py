"""지표·금융 계산 단위 테스트: SMA / RSI / IRR."""

from src.backtest import _sma, _rsi, _xirr


def test_sma_basic():
    closes = [1, 2, 3, 4, 5]
    assert _sma(closes, 4, 3) == 4.0          # (3+4+5)/3
    assert _sma(closes, 2, 3) == 2.0          # (1+2+3)/3
    assert _sma(closes, 1, 3) is None         # 데이터 부족


def test_rsi_all_gains_is_100():
    closes = [10, 11, 12, 13, 14, 15]
    assert _rsi(closes, 5, 4) == 100.0        # 손실 0 → RSI 100


def test_rsi_insufficient_data():
    assert _rsi([10, 11], 5, 14) is None


def test_rsi_midrange():
    # 등락 섞임 → 0 < RSI < 100
    closes = [10, 11, 10, 11, 10, 11, 10, 11]
    r = _rsi(closes, 7, 4)
    assert 0 < r < 100


def test_xirr_one_year_10pct():
    cf = [("2024-01-01", -100.0), ("2024-12-31", 110.0)]
    irr = _xirr(cf)
    assert abs(irr - 0.10) < 0.005            # 1년 +10%


def test_xirr_needs_sign_change():
    assert _xirr([("2024-01-01", -100.0), ("2024-06-01", -50.0)]) is None
