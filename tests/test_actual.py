"""실측 vs 백테스트 대조 테스트 (합성 데이터, 네트워크 없음)."""

import pytest

from src import actual


# ------------------------------------------------------------------ 분할 보정
def test_snap_removes_rounding_noise():
    assert actual._snap(0.999724) == 1.0        # 1.0이어야 할 값
    assert actual._snap(2.0) == 2.0
    assert actual._snap(1.5001) == 1.5          # 3:2 분할


def test_snap_keeps_value_when_far_from_simple_ratio():
    weird = 1.0731
    assert actual._snap(weird) == pytest.approx(weird, rel=0.006)


FACTORS = [("2024-01-02", 2.0), ("2024-06-03", 2.0), ("2024-06-04", 1.0),
           ("2024-12-02", 1.0)]


def test_factor_at_picks_previous_trading_day():
    assert actual._factor_at(FACTORS, "2024-06-03") == 2.0
    assert actual._factor_at(FACTORS, "2024-06-04") == 1.0
    assert actual._factor_at(FACTORS, "2024-06-05") == 1.0   # 거래일 아님 → 직전
    assert actual._factor_at(FACTORS, "2023-01-01") == 2.0   # 구간 이전 → 최초값
    assert actual._factor_at([], "2024-01-01") == 1.0


def _trade(day, side, qty, price, commission=0.0, tax=0.0):
    return {"date": day, "symbol": "X", "side": side, "currency": "USD",
            "quantity": qty, "price": price, "amount": qty * price,
            "commission": commission, "tax": tax}


def test_adjust_for_splits_scales_shares_not_amount():
    t = _trade("2024-01-10", "BUY", 1.0, 100.0)          # 분할 전 → ×2
    [adj] = actual.adjust_for_splits([t], FACTORS)
    assert adj["quantity"] == 2.0 and adj["price"] == 50.0
    assert adj["amount"] == 100.0                        # 지불액은 불변
    assert adj["split_factor"] == 2.0


def test_adjust_for_splits_leaves_post_split_trades_alone():
    t = _trade("2024-07-01", "BUY", 3.0, 40.0)
    [adj] = actual.adjust_for_splits([t], FACTORS)
    assert (adj["quantity"], adj["price"]) == (3.0, 40.0)


# -------------------------------------------------------------------- 성과
def test_evaluate_basic_position():
    trades = [_trade("2024-01-02", "BUY", 2.0, 50.0),
              _trade("2024-07-01", "BUY", 2.0, 100.0)]
    r = actual.evaluate(trades, last_price=100.0, as_of="2025-01-02")
    assert r["shares"] == 4.0 and r["invested"] == 300.0
    assert r["avg_buy_price"] == 75.0
    assert r["market_value"] == 400.0 and r["profit"] == 100.0
    assert r["return_pct"] == pytest.approx(33.33, abs=0.01)


def test_evaluate_nets_out_sells():
    trades = [_trade("2024-01-02", "BUY", 4.0, 50.0),
              _trade("2024-07-01", "SELL", 1.0, 100.0)]
    r = actual.evaluate(trades, last_price=100.0, as_of="2025-01-02")
    assert r["shares"] == 3.0
    assert r["invested"] == 100.0            # 200 매수 - 100 매도 회수
    assert r["sell_count"] == 1


def test_evaluate_irr_uses_as_of_not_last_trade():
    """단일 매수면 체결일에 평가하면 IRR이 정의되지 않는다 — 기준일로 평가해야 한다."""
    trades = [_trade("2024-01-02", "BUY", 1.0, 100.0)]
    same_day = actual.evaluate(trades, 200.0, as_of="2024-01-02")
    a_year = actual.evaluate(trades, 200.0, as_of="2025-01-01")
    assert same_day["irr_pct"] is None
    assert a_year["irr_pct"] == pytest.approx(100.0, abs=1.0)   # 1년에 2배 ≈ +100%


def test_evaluate_counts_costs_against_profit():
    trades = [_trade("2024-01-02", "BUY", 1.0, 100.0, commission=1.0, tax=0.5)]
    r = actual.evaluate(trades, last_price=100.0, as_of="2025-01-02")
    assert r["costs_paid"] == 1.5 and r["profit"] == -1.5


# ------------------------------------------------------------------ 수수료
def test_fee_reality_check_flags_understated_assumption():
    # kr_stock 가정은 매수수수료 0% — 실제로 냈다면 백테스트가 비용을 과소평가
    trades = [_trade("2024-01-02", "BUY", 1.0, 100_000.0, commission=10.0)]
    r = actual.fee_reality_check(trades, "kr_stock")
    assert r["assumed_buy_commission_pct"] == 0.0
    assert r["actual_buy_commission_pct"] == pytest.approx(0.01)
    assert r["assumption_understates_cost"] is True


def test_fee_reality_check_ok_when_assumption_is_conservative():
    # us_stock 가정 0.1%, 실제 0% → 가정이 보수적
    trades = [_trade("2024-01-02", "BUY", 1.0, 100.0)]
    r = actual.fee_reality_check(trades, "us_stock")
    assert r["assumption_understates_cost"] is False


# -------------------------------------------------------------------- 대조
class ReconClient:
    def __init__(self, held):
        self._held = held

    def get_holdings(self, account_seq, symbol=None):
        if self._held is None:
            return {"items": []}
        return {"items": [{"symbol": "X", "quantity": str(self._held)}]}


@pytest.mark.parametrize("held, shares, severity", [
    (10.0, 10.0, "ok"),
    (10.0, 9.95, "minor"),       # 0.5% — 배당 재투자·분할 잔주 수준
    (10.0, 8.0, "material"),     # 20% — 이력이 불완전
])
def test_reconcile_severity(held, shares, severity):
    r = actual._reconcile(ReconClient(held), "1", "X", shares)
    assert r["severity"] == severity
    assert (r["note"] is None) == (severity == "ok")


def test_reconcile_skips_when_position_closed():
    r = actual._reconcile(ReconClient(None), "1", "X", 0.0)
    assert r["holdings_shares"] is None and "생략" in r["note"]


# ---------------------------------------------------------- caveats(주의문구)
def test_caveats_warn_on_undeployed_benchmark_cash():
    mine = {"sell_count": 0}
    bench = {"invested": 100.0, "cash_carry": 900.0, "buy_count": 1}
    [msg] = actual._caveats(mine, bench, [1.0])
    assert "집행하지 못했습니다" in msg


def test_caveats_flag_sells_and_splits():
    msgs = actual._caveats({"sell_count": 2}, {"invested": 100.0, "cash_carry": 0.0}, [1.0, 2.0])
    assert any("매도" in m for m in msgs) and any("액면분할" in m for m in msgs)


def test_caveats_silent_on_clean_case():
    assert actual._caveats({"sell_count": 0}, {"invested": 100.0, "cash_carry": 0.0}, [1.0]) == []
