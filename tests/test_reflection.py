"""신호 회고(reflection) 순수 로직 테스트."""

from src import reflection


def _candles(start_close, n, step):
    # 2024-01-01부터 일별, 종가 = start + i*step
    from datetime import date, timedelta
    d0 = date.fromisoformat("2024-01-01")
    return [{"date": (d0 + timedelta(days=i)).isoformat(),
             "close": start_close + i * step} for i in range(n)]


def test_reflection_hit_and_pending():
    # AAA: 신호일(2024-01-01, 가격 100) 이후 20일 뒤 종가는 100+20*5=200 → +100% 적중
    candles = _candles(100, 30, 5)
    records = [{"date": "2024-01-01",
                "signals": [{"symbol": "AAA", "price": 100, "ma_buy": True, "rsi_buy": False}]}]
    res = reflection.evaluate_reflection(records, {"AAA": candles}, horizon_days=20)
    assert res["ma"]["evaluated"] == 1
    assert res["ma"]["hit_rate_pct"] == 100.0
    assert res["ma"]["avg_return_pct"] > 0
    assert res["rsi"]["evaluated"] == 0


def test_reflection_pending_when_not_enough_days():
    candles = _candles(100, 10, 5)            # 10봉뿐인데 horizon 20 → 미경과
    records = [{"date": "2024-01-01",
                "signals": [{"symbol": "AAA", "price": 100, "ma_buy": True}]}]
    res = reflection.evaluate_reflection(records, {"AAA": candles}, horizon_days=20)
    assert res["ma"]["evaluated"] == 0
    assert res["ma"]["pending"] == 1


def test_reflection_loss_counts_as_miss():
    candles = _candles(100, 30, -2)           # 하락: 20일 뒤 100-40=60 → 손실
    records = [{"date": "2024-01-01",
                "signals": [{"symbol": "AAA", "price": 100, "rsi_buy": True}]}]
    res = reflection.evaluate_reflection(records, {"AAA": candles}, horizon_days=20)
    assert res["rsi"]["hit_rate_pct"] == 0.0
    assert res["rsi"]["avg_return_pct"] < 0


def test_load_signal_log_missing(tmp_path):
    assert reflection.load_signal_log(tmp_path / "nope.jsonl") == []
