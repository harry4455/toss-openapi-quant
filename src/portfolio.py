"""내 계좌 실측 데이터 정규화 — 보유 현황·체결 내역.

토스 API는 숫자를 문자열로 주고 KRW/USD 합계를 분리해 돌려준다. 여기서 float 변환 +
현재 환율로 KRW 통합해 비중까지 계산한다. 백테스트가 '가정'이라면 이 모듈은 '실측'이다.

MCP 도구(my_holdings/my_trades)와 (예정) WealthMind 대시보드 수집기가 같은 함수를 쓴다.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .toss_client import TossClient


def _f(v: Any) -> float:
    """API가 문자열·None으로 주는 숫자를 float으로. 파싱 불가면 0.0."""
    if v is None or v == "":
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def resolve_account(client: TossClient) -> str:
    """첫 번째 계좌의 accountSeq. 계좌가 없으면 예외."""
    accounts = client.list_accounts()
    if not accounts:
        raise RuntimeError("조회 가능한 계좌가 없습니다.")
    return str(accounts[0]["accountSeq"])


# ------------------------------------------------------------------ holdings
def snapshot(client: TossClient, account_seq: str | None = None) -> dict:
    """현재 보유 종목 + KRW 통합 요약.

    반환: {as_of_fx_rate, total, currency_split, items:[{..., weight_pct}]}
    items는 KRW 환산 평가액 내림차순.

    레버리지 ETF(TQQQ 3x 등)는 평가액보다 실제 시장 노출이 크다. 종목 기본정보의
    leverageFactor로 배수를 받아 `exposure_krw`와 총 `leverage_ratio`를 함께 낸다.
    """
    seq = account_seq or resolve_account(client)
    raw = client.get_holdings(seq)
    fx_rate = client.get_exchange_rate("USD", "KRW")
    meta = client.get_stocks([i["symbol"] for i in raw.get("items", [])])

    items: list[dict] = []
    for it in raw.get("items", []):
        currency = it.get("currency", "KRW")
        rate = fx_rate if currency == "USD" else 1.0
        mv, pl = it.get("marketValue", {}), it.get("profitLoss", {})
        daily, cost = it.get("dailyProfitLoss", {}), it.get("cost", {})
        value = _f(mv.get("amount"))
        info = meta.get(it.get("symbol"), {})
        # leverageFactor는 ETF/ETN에만 있고 일반 주식은 null → 1배로 본다
        leverage = _f(info.get("leverageFactor")) or 1.0
        value_krw = round(value * rate)
        items.append({
            "symbol": it.get("symbol"),
            "name": it.get("name"),
            "country": it.get("marketCountry"),
            "currency": currency,
            "quantity": _f(it.get("quantity")),
            "last_price": _f(it.get("lastPrice")),
            "avg_cost": _f(it.get("averagePurchasePrice")),
            "purchase_amount": _f(mv.get("purchaseAmount")),
            "market_value": value,
            "market_value_krw": value_krw,
            "security_type": info.get("securityType"),
            "leverage_factor": leverage,
            "exposure_krw": round(value_krw * leverage),
            "profit_loss": _f(pl.get("amount")),
            "profit_loss_pct": round(_f(pl.get("rate")) * 100, 2),  # API는 비율(2.9=290%)
            "daily_profit_loss_pct": round(_f(daily.get("rate")) * 100, 2),
            "commission": _f(cost.get("commission")),
            "tax": _f(cost.get("tax")),
        })

    total_value = sum(i["market_value_krw"] for i in items)
    gross_exposure = sum(i["exposure_krw"] for i in items)
    for i in items:
        i["weight_pct"] = round(i["market_value_krw"] / total_value * 100, 2) if total_value else 0.0
    items.sort(key=lambda x: -x["market_value_krw"])

    purchase = raw.get("totalPurchaseAmount", {})
    invested = round(_f(purchase.get("krw")) + _f(purchase.get("usd")) * fx_rate)
    by_currency: dict[str, float] = defaultdict(float)
    for i in items:
        by_currency[i["currency"]] += i["market_value_krw"]

    return {
        "fx_rate_usdkrw": fx_rate,
        "total": {
            "invested_krw": invested,
            "market_value_krw": total_value,
            "profit_loss_krw": total_value - invested,
            "profit_loss_pct": round((total_value / invested - 1) * 100, 2) if invested else 0.0,
            "positions": len(items),
            # 자본 대비 실제 시장 노출. 1.0이면 레버리지 없음
            "gross_exposure_krw": gross_exposure,
            "leverage_ratio": round(gross_exposure / total_value, 2) if total_value else 1.0,
        },
        # 통화 노출(환율 리스크)을 KRW 환산 비중으로
        "currency_split_pct": {
            cur: round(amt / total_value * 100, 2) if total_value else 0.0
            for cur, amt in sorted(by_currency.items(), key=lambda kv: -kv[1])
        },
        "items": items,
    }


# -------------------------------------------------------------------- trades
def trades(client: TossClient, account_seq: str | None = None,
           symbol: str | None = None, start: str | None = None,
           end: str | None = None, max_count: int = 2000) -> list[dict]:
    """실제 체결된 주문만 정규화해 반환 (오래된 순). 미체결·취소·거부는 제외."""
    seq = account_seq or resolve_account(client)
    raw = client.get_orders(seq, status="CLOSED", symbol=symbol,
                            start=start, end=end, max_count=max_count)
    out: list[dict] = []
    for o in raw:
        ex = o.get("execution") or {}
        qty = _f(ex.get("filledQuantity"))
        if qty <= 0:  # CANCELED/REJECTED 등 체결 없는 주문
            continue
        filled_at = ex.get("filledAt") or o.get("orderedAt") or ""
        out.append({
            "order_id": o.get("orderId"),   # 멱등 키(외부 저장소 적재 시)
            "date": filled_at[:10],
            "filled_at": filled_at,
            "symbol": o.get("symbol"),
            "side": o.get("side"),
            "order_type": o.get("orderType"),
            "currency": o.get("currency"),
            "quantity": qty,
            "price": _f(ex.get("averageFilledPrice")),
            "amount": _f(ex.get("filledAmount")),
            "commission": _f(ex.get("commission")),
            "tax": _f(ex.get("tax")),
        })
    return out


def trade_summary(rows: list[dict]) -> dict:
    """체결 내역을 종목별로 집계. 원시 467건을 그대로 넘기지 않기 위한 요약."""
    agg: dict[str, dict] = {}
    for t in rows:
        a = agg.setdefault(t["symbol"], {
            "symbol": t["symbol"], "currency": t["currency"],
            "buy_count": 0, "sell_count": 0,
            "buy_quantity": 0.0, "sell_quantity": 0.0,
            "buy_amount": 0.0, "sell_amount": 0.0,
            "commission": 0.0, "tax": 0.0,
            "first_trade": t["date"], "last_trade": t["date"],
        })
        side = "buy" if t["side"] == "BUY" else "sell"
        a[f"{side}_count"] += 1
        a[f"{side}_quantity"] += t["quantity"]
        a[f"{side}_amount"] += t["amount"]
        a["commission"] += t["commission"]
        a["tax"] += t["tax"]
        a["first_trade"] = min(a["first_trade"], t["date"])
        a["last_trade"] = max(a["last_trade"], t["date"])

    for a in agg.values():
        # 순투입액: 매수 - 매도. 평단은 매수 기준(매도 반영 없이 단순 가중평균)
        a["net_invested"] = round(a["buy_amount"] - a["sell_amount"], 4)
        a["avg_buy_price"] = round(a["buy_amount"] / a["buy_quantity"], 4) if a["buy_quantity"] else 0.0
        for k in ("buy_quantity", "sell_quantity", "buy_amount",
                  "sell_amount", "commission", "tax"):
            a[k] = round(a[k], 4)

    symbols = sorted(agg.values(), key=lambda x: -x["buy_amount"])
    dates = [t["date"] for t in rows if t["date"]]
    return {
        "trade_count": len(rows),
        "period": {"start": min(dates), "end": max(dates)} if dates else None,
        "symbols": symbols,
    }
