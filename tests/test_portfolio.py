"""보유 현황·체결 내역 정규화 테스트 (합성 데이터, 네트워크 없음)."""

import pytest

from src import portfolio


class FakeClient:
    """TossClient 대역 — portfolio가 쓰는 3개 메서드만 흉내낸다."""

    def __init__(self, holdings=None, orders=None, rate=1400.0, stocks=None):
        self._holdings, self._orders, self._rate = holdings or {}, orders or [], rate
        self._stocks = stocks or {}
        self.order_calls = []

    def get_stocks(self, symbols):
        return {s: self._stocks.get(s, {}) for s in symbols}

    def list_accounts(self):
        return [{"accountNo": "1180", "accountSeq": 1, "accountType": "BROKERAGE"}]

    def get_holdings(self, account_seq, symbol=None):
        return self._holdings

    def get_exchange_rate(self, base="USD", quote="KRW"):
        return self._rate

    def get_orders(self, account_seq, status="CLOSED", symbol=None,
                   start=None, end=None, max_count=2000):
        self.order_calls.append({"status": status, "symbol": symbol,
                                 "start": start, "end": end})
        return self._orders


def _holding(symbol, currency, quantity, last, avg, value, purchase, rate):
    """API 응답 모양 그대로 — 숫자는 전부 문자열, rate는 비율(2.9=290%)."""
    return {
        "symbol": symbol, "name": symbol, "marketCountry": "US" if currency == "USD" else "KR",
        "currency": currency, "quantity": str(quantity), "lastPrice": str(last),
        "averagePurchasePrice": str(avg),
        "marketValue": {"purchaseAmount": str(purchase), "amount": str(value)},
        "profitLoss": {"amount": str(value - purchase), "rate": str(rate)},
        "dailyProfitLoss": {"amount": "0", "rate": "-0.01"},
        "cost": {"commission": "10", "tax": None},
    }


HOLDINGS = {
    "totalPurchaseAmount": {"krw": "200000", "usd": "100"},
    "items": [
        _holding("005930", "KRW", 3, 100000, 66666, 300000, 200000, 0.5),
        _holding("VOO", "USD", 1, 500, 400, 500, 400, 0.25),
    ],
}


def test_snapshot_converts_usd_to_krw_and_weights():
    snap = portfolio.snapshot(FakeClient(holdings=HOLDINGS), account_seq="1")
    krw, usd = snap["items"][0], snap["items"][1]
    # USD 500 × 1400 = 700,000 → USD 종목이 더 커서 첫 번째로 정렬돼야 한다
    assert krw["symbol"] == "VOO" and krw["market_value_krw"] == 700_000
    assert usd["symbol"] == "005930" and usd["market_value_krw"] == 300_000
    assert sum(i["weight_pct"] for i in snap["items"]) == pytest.approx(100.0)
    assert krw["weight_pct"] == pytest.approx(70.0)


def test_snapshot_totals_and_currency_split():
    snap = portfolio.snapshot(FakeClient(holdings=HOLDINGS), account_seq="1")
    t = snap["total"]
    assert t["invested_krw"] == 200_000 + 100 * 1400   # KRW + USD 환산
    assert t["market_value_krw"] == 1_000_000
    assert t["profit_loss_krw"] == 660_000
    assert t["positions"] == 2
    assert snap["currency_split_pct"] == {"USD": 70.0, "KRW": 30.0}


def test_snapshot_rate_is_scaled_to_percent():
    snap = portfolio.snapshot(FakeClient(holdings=HOLDINGS), account_seq="1")
    # API가 0.5로 주면 50%
    assert {i["symbol"]: i["profit_loss_pct"] for i in snap["items"]}["005930"] == 50.0


def test_snapshot_handles_empty_account():
    snap = portfolio.snapshot(FakeClient(holdings={"items": []}), account_seq="1")
    assert snap["total"]["market_value_krw"] == 0
    assert snap["total"]["profit_loss_pct"] == 0.0   # 0으로 나누지 않는다
    assert snap["items"] == []


def _order(symbol, side, qty, price, ordered, filled_qty=None, commission="0", tax="0"):
    fq = qty if filled_qty is None else filled_qty
    ex = None if fq == 0 else {
        "filledQuantity": str(fq), "averageFilledPrice": str(price),
        "filledAmount": str(round(fq * price, 6)),
        "commission": commission, "tax": tax,
        "filledAt": f"{ordered}T23:03:44.528+09:00",
    }
    return {"orderId": f"{symbol}-{ordered}-{side}", "symbol": symbol, "side": side,
            "orderType": "MARKET", "status": "FILLED" if fq else "CANCELED",
            "currency": "USD", "quantity": str(qty), "orderedAt": f"{ordered}T20:29:29+09:00",
            "execution": ex}


ORDERS = [
    _order("QLD", "BUY", 1.0, 90.0, "2026-08-17"),
    _order("QLD", "BUY", 2.0, 100.0, "2026-08-18", commission="0.2"),
    _order("QLD", "SELL", 1.0, 110.0, "2026-08-19", tax="0.1"),
    _order("TSLA", "BUY", 5.0, 0.0, "2026-08-19", filled_qty=0),   # 취소 — 제외돼야 함
]


def test_trades_skips_unfilled_orders():
    rows = portfolio.trades(FakeClient(orders=ORDERS), account_seq="1")
    assert len(rows) == 3
    assert all(r["symbol"] == "QLD" for r in rows)   # 취소된 TSLA 제외


def test_trades_normalizes_fields():
    rows = portfolio.trades(FakeClient(orders=ORDERS), account_seq="1")
    first = rows[0]
    assert first["date"] == "2026-08-17"             # filledAt에서 날짜만
    assert first["price"] == 90.0 and first["amount"] == 90.0
    assert isinstance(first["quantity"], float)      # 문자열 → float
    assert first["order_id"] == "QLD-2026-08-17-BUY"  # 외부 적재용 멱등 키


def test_trades_passes_filters_through():
    c = FakeClient(orders=ORDERS)
    portfolio.trades(c, account_seq="1", symbol="QLD", start="2026-01-01", end="2026-12-31")
    assert c.order_calls == [{"status": "CLOSED", "symbol": "QLD",
                              "start": "2026-01-01", "end": "2026-12-31"}]


def test_trade_summary_aggregates_by_symbol():
    rows = portfolio.trades(FakeClient(orders=ORDERS), account_seq="1")
    s = portfolio.trade_summary(rows)
    assert s["trade_count"] == 3
    assert s["period"] == {"start": "2026-08-17", "end": "2026-08-19"}
    qld = s["symbols"][0]
    assert qld["buy_count"] == 2 and qld["sell_count"] == 1
    assert qld["buy_quantity"] == 3.0
    assert qld["buy_amount"] == pytest.approx(290.0)      # 90 + 200
    assert qld["sell_amount"] == pytest.approx(110.0)
    assert qld["net_invested"] == pytest.approx(180.0)    # 290 - 110
    assert qld["avg_buy_price"] == pytest.approx(290 / 3)
    assert qld["commission"] == pytest.approx(0.2) and qld["tax"] == pytest.approx(0.1)


def test_trade_summary_on_empty_history():
    s = portfolio.trade_summary([])
    assert s["trade_count"] == 0 and s["period"] is None and s["symbols"] == []


def test_resolve_account_uses_first_account():
    assert portfolio.resolve_account(FakeClient()) == "1"


# --------------------------------------------- get_orders 페이징/필터 (네트워크 없음)
def _client_with_pages(pages):
    """TossClient._get을 가로채 미리 준비한 페이지를 순서대로 돌려준다."""
    from src.toss_client import TossClient

    c = TossClient("id", "secret")
    seen = []

    def fake_get(path, *, params=None, account_seq=None, **kw):
        seen.append(dict(params or {}))
        return {"result": pages[len(seen) - 1]}

    c._get = fake_get
    return c, seen


def test_get_orders_pairs_from_and_to():
    """`to`만 보내면 서버가 0건을 주는 특이동작 → 한쪽만 지정돼도 짝으로 채운다."""
    c, seen = _client_with_pages([{"orders": [], "hasNext": False}])
    c.get_orders("1", end="2026-12-31")
    assert seen[0]["from"] == "2000-01-01" and seen[0]["to"] == "2026-12-31"

    c, seen = _client_with_pages([{"orders": [], "hasNext": False}])
    c.get_orders("1", start="2026-01-01")
    assert seen[0]["from"] == "2026-01-01" and seen[0]["to"] == "2099-12-31"


def test_get_orders_omits_dates_when_unfiltered():
    c, seen = _client_with_pages([{"orders": [], "hasNext": False}])
    c.get_orders("1")
    assert "from" not in seen[0] and "to" not in seen[0]


def test_get_orders_follows_cursor_and_sorts_oldest_first():
    pages = [
        {"orders": [{"orderedAt": "2026-08-19T00:00:00+09:00"},
                    {"orderedAt": "2026-08-18T00:00:00+09:00"}],
         "nextCursor": "c1", "hasNext": True},
        {"orders": [{"orderedAt": "2026-08-17T00:00:00+09:00"}],
         "nextCursor": None, "hasNext": False},
    ]
    c, seen = _client_with_pages(pages)
    rows = c.get_orders("1")
    assert len(rows) == 3
    assert seen[1]["cursor"] == "c1"                       # 커서 전달
    assert [r["orderedAt"][:10] for r in rows] == [
        "2026-08-17", "2026-08-18", "2026-08-19"]          # 오래된 → 최신


def test_get_orders_respects_max_count():
    pages = [{"orders": [{"orderedAt": f"2026-08-{d:02d}T00:00:00+09:00"}
                         for d in range(1, 11)],
              "nextCursor": "c1", "hasNext": True}]
    c, seen = _client_with_pages(pages)
    rows = c.get_orders("1", max_count=10)
    assert len(rows) == 10 and seen[0]["limit"] == 10      # limit은 max_count로 축소


# ------------------------------------------------------- 레버리지 실질 노출
LEVERAGED = {
    "totalPurchaseAmount": {"krw": "0", "usd": "0"},
    "items": [
        _holding("TQQQ", "USD", 1, 100, 50, 100, 50, 1.0),   # $100 × 1400 = ₩140,000
        _holding("VOO", "USD", 1, 100, 50, 100, 50, 1.0),
    ],
}
STOCK_META = {
    "TQQQ": {"symbol": "TQQQ", "securityType": "ETF", "leverageFactor": "3"},
    "VOO": {"symbol": "VOO", "securityType": "ETF", "leverageFactor": None},
}


def test_snapshot_applies_leverage_factor():
    snap = portfolio.snapshot(
        FakeClient(holdings=LEVERAGED, stocks=STOCK_META), account_seq="1")
    by = {i["symbol"]: i for i in snap["items"]}
    assert by["TQQQ"]["leverage_factor"] == 3.0
    assert by["TQQQ"]["exposure_krw"] == 420_000        # 140,000 × 3
    assert by["VOO"]["leverage_factor"] == 1.0          # null → 1배
    assert by["VOO"]["exposure_krw"] == 140_000
    assert by["TQQQ"]["security_type"] == "ETF"


def test_snapshot_totals_gross_exposure_and_ratio():
    snap = portfolio.snapshot(
        FakeClient(holdings=LEVERAGED, stocks=STOCK_META), account_seq="1")
    t = snap["total"]
    assert t["market_value_krw"] == 280_000            # 140k + 140k
    assert t["gross_exposure_krw"] == 560_000          # 420k + 140k
    assert t["leverage_ratio"] == 2.0


def test_snapshot_leverage_defaults_to_one_without_metadata():
    """종목 기본정보를 못 받아도 레버리지는 1배로 안전하게 떨어져야 한다."""
    snap = portfolio.snapshot(FakeClient(holdings=LEVERAGED), account_seq="1")
    assert snap["total"]["leverage_ratio"] == 1.0
    assert all(i["leverage_factor"] == 1.0 for i in snap["items"])


# ------------------------------------------- 401 재시도 (토큰 단일 활성 제약)
def test_get_retries_once_on_invalid_token(monkeypatch):
    """토스는 client_id당 토큰 1개만 살려둔다 — 다른 프로세스가 갈아끼우면 401이 온다."""
    from src.toss_client import TossClient

    c = TossClient("id", "secret")
    c._access_token, c._token_expires_at = "stale", 9e9   # 만료 전이지만 무효한 토큰
    issued, calls = [], []

    def fake_token():
        issued.append(1)
        return "fresh"
    monkeypatch.setattr(c, "_ensure_token", fake_token)

    class Resp:
        def __init__(self, code):
            self.status_code, self.headers, self.text = code, {}, ""
        def json(self):
            return {"result": "ok"}
        def raise_for_status(self):
            if self.status_code >= 400:
                raise AssertionError(f"불필요하게 예외 발생: {self.status_code}")

    def fake_get(url, **kw):
        calls.append(kw["headers"]["Authorization"])
        return Resp(401 if len(calls) == 1 else 200)
    monkeypatch.setattr(c._session, "get", fake_get)

    assert c._get("/api/v1/prices") == {"result": "ok"}
    assert len(calls) == 2                      # 401 한 번 → 재시도 한 번
    assert c._access_token is None or issued    # 캐시를 비우고 다시 받았다
