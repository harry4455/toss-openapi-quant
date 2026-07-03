"""DCA 백테스트 로직 테스트 (정수/소수 주, 잔돈 이월, 수수료, 이평선 필터, 거치식)."""

from src import backtest, fees


def test_dca_integer_shares_and_carry(make_candles):
    # 종가 1000 고정, 3주(월요일 3회), 회당 2500원, 정수 주 + 잔돈 이월
    candles = make_candles([1000] * 21)
    r = backtest.run_backtest(candles, 2500, freq="weekly", weekday=0,
                              allow_fractional=False)
    # 주1: 2500→2주(2000), 잔500 / 주2: 3000→3주 / 주3: 2500→2주, 잔500
    assert r["buy_count"] == 3
    assert r["shares"] == 7
    assert r["invested"] == 7000
    assert r["cash_carry"] == 500


def test_dca_fractional(make_candles):
    candles = make_candles([1000] * 21)
    r = backtest.run_backtest(candles, 2500, freq="weekly", weekday=0,
                              allow_fractional=True)
    assert abs(r["shares"] - 7.5) < 1e-9      # 2.5주 × 3회
    assert abs(r["invested"] - 7500) < 1e-9
    assert r["cash_carry"] == 0


def test_buy_commission_reduces_shares(make_candles):
    candles = make_candles([1000] * 21)
    prof = {"buy_commission": 0.01}           # 1% 수수료
    base = backtest.run_backtest(candles, 2500, allow_fractional=True)
    withc = backtest.run_backtest(candles, 2500, allow_fractional=True,
                                  fee_profile=prof)
    assert withc["shares"] < base["shares"]
    assert withc["commission_paid"] > 0


def test_ma_filter_skips_when_above(make_candles):
    # 상승 종가 + below 모드 → 종가>이평선이라 워밍업 후 매수 보류
    closes = [1000 + i * 10 for i in range(60)]
    candles = make_candles(closes)
    r = backtest.run_backtest(candles, 1000, weekday=0,
                              ma_window=5, ma_mode="below")
    assert r["skipped"] > 0


def test_rsi_filter_skips_when_not_oversold(make_candles):
    closes = [1000 + i * 10 for i in range(60)]   # 계속 상승 → RSI 높음
    candles = make_candles(closes)
    r = backtest.run_backtest(candles, 1000, weekday=0,
                              rsi_window=14, rsi_threshold=30)
    assert r["skipped"] > 0


def test_slippage_reduces_shares(make_candles):
    candles = make_candles([1000] * 21)
    base = backtest.run_backtest(candles, 2500, allow_fractional=True)
    slip = backtest.run_backtest(candles, 2500, allow_fractional=True, slippage=0.01)
    # 1% 슬리피지 → 더 비싸게 사므로 같은 돈으로 더 적은 수량, 평단은 높아짐
    assert slip["shares"] < base["shares"]
    assert slip["avg_cost"] > base["avg_cost"]


def _candles_oc(closes, open_offset):
    """open != close 인 캔들 (다음날 시가 체결 검증용)."""
    from datetime import date, timedelta
    d0 = date.fromisoformat("2024-01-01")
    return [{"date": (d0 + timedelta(days=i)).isoformat(),
             "open": c + open_offset, "high": max(c, c + open_offset),
             "low": min(c, c + open_offset), "close": c, "volume": 1000.0}
            for i, c in enumerate(closes)]


def test_next_bar_fill_uses_next_open():
    # 하락 추세 → MA20 아래 조건 발동. open이 close보다 +50 높음.
    closes = [1000 - i * 3 for i in range(60)]
    candles = _candles_oc(closes, open_offset=50)
    nb = backtest.run_backtest(candles, 100000, weekday=0, allow_fractional=True,
                               ma_window=20, next_bar_fill=True)
    sb = backtest.run_backtest(candles, 100000, weekday=0, allow_fractional=True,
                               ma_window=20, next_bar_fill=False)
    assert nb["buy_count"] > 0
    # 다음날 시가(=close+50)에 체결 → 당일 종가 체결보다 평단이 높아야
    assert nb["avg_cost"] > sb["avg_cost"]
    # 기록된 매수가가 실제 어느 봉의 open 값과 일치(다음 봉 시가)
    opens = {c["open"] for c in candles}
    assert all(b["price"] in opens for b in nb["buys"])


def test_plain_dca_unaffected_by_next_bar_fill():
    # 일반 DCA(신호 없음)는 next_bar_fill 영향 없음 — 당일 종가 체결 유지
    candles = _candles_oc([1000] * 21, open_offset=50)
    a = backtest.run_backtest(candles, 2500, weekday=0, next_bar_fill=True)
    b = backtest.run_backtest(candles, 2500, weekday=0, next_bar_fill=False)
    assert a["invested"] == b["invested"] and a["shares"] == b["shares"]


def test_lumpsum_buys_once(make_candles):
    candles = make_candles([1000] * 21)
    cmp = backtest.compare_lumpsum_dca(candles, 1000, weekday=0)
    assert cmp["dca"]["buy_count"] == 3
    assert cmp["lump"]["buy_count"] == 1          # 거치식은 첫날 1회
    # 총액 동일: 거치식 투자액 ≈ 3 × 1000
    assert abs(cmp["total_planned"] - 3000) < 1e-9


def test_irr_present_for_profitable(make_candles):
    # 가격 상승 → IRR 양수
    closes = [1000 + i * 5 for i in range(30)]
    candles = make_candles(closes)
    r = backtest.run_backtest(candles, 1000, weekday=0)
    assert r["irr_pct"] is not None and r["irr_pct"] > 0


def test_net_return_with_kr_stock_tax(make_candles):
    candles = make_candles([1000] * 21)
    prof = fees.get_profile("kr_stock")
    r = backtest.run_backtest(candles, 2500, weekday=0, fee_profile=prof)
    # 매도거래세 0.2%만큼 세후가 세전보다 작아야
    assert r["net_value"] < r["market_value"]
    assert r["sell_tax"] > 0
