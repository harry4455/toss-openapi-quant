"""DCA 주문 생성·실거래 잠금, FX 보간, State 멱등성/히스테리시스 테스트."""

import pytest

from src import dca, fx
from src.state import State


# ---- DCA 주문 생성 ----
def test_build_order_us_money_order():
    o = dca._build_order({"symbol": "VOO", "amount_usd": 100},
                         {"market": "US", "order_type": "MARKET"}, 520.0)
    assert o["orderAmount"] == "100" and "quantity" not in o
    assert o["side"] == "BUY"


def test_build_order_kr_limit_quantity():
    o = dca._build_order({"symbol": "069500", "amount_krw": 100000},
                         {"market": "KR", "order_type": "LIMIT",
                          "limit_buffer_pct": 0.5}, 10000.0)
    assert o["price"] == "10050"          # 10000 × 1.005
    assert o["quantity"] == "9"           # 100000 // 10050


def test_build_order_skips_when_budget_below_one_share():
    with pytest.raises(dca.SkipItem):
        dca._build_order({"symbol": "QQQ", "amount_usd": 50},
                         {"market": "US", "order_type": "LIMIT"}, 500.0)


# ---- 실거래 이중 잠금 ----
def test_live_disabled_when_dry_run():
    assert dca._live_enabled({"dry_run": True}) is False


def test_live_disabled_without_env(monkeypatch):
    monkeypatch.delenv(dca.LIVE_ENV_FLAG, raising=False)
    assert dca._live_enabled({"dry_run": False}) is False


def test_live_enabled_with_both(monkeypatch):
    monkeypatch.setenv(dca.LIVE_ENV_FLAG, dca.LIVE_ENV_VALUE)
    assert dca._live_enabled({"dry_run": False}) is True


# ---- FX 보간 ----
def test_fx_on_ffill_and_bfill():
    series = {"2024-01-01": 1300.0, "2024-02-01": 1400.0, "2024-03-01": 0.0}
    assert fx.fx_on(series, "2024-01-15") == 1300.0   # ffill
    assert fx.fx_on(series, "2024-02-10") == 1400.0
    assert fx.fx_on(series, "2023-12-01") == 1300.0   # bfill(가장 이른 유효값)
    assert fx.fx_on({}, "2024-01-01") is None         # 데이터 없음


# ---- State ----
def test_state_dca_idempotency(tmp_path):
    st = State(tmp_path / "s.json")
    assert not st.is_dca_done("k1")
    st.mark_dca_done("k1")
    st.save()
    st2 = State(tmp_path / "s.json")        # 재로드해도 유지
    assert st2.is_dca_done("k1")
