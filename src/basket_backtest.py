"""여러 종목 바스켓 DCA 동시 백테스트 + 포트폴리오 합산.

각 종목을 개별 백테스트한 뒤, 통화를 KRW로 통일해 포트폴리오 전체
누적투자/평가액/수익률/MDD를 계산한다.

단순화: 통화 환산은 '현재 환율' 단일값을 사용(과거 환율 변동 미반영).
"""

from __future__ import annotations

from . import backtest, fees
from .cache import get_candles_cached

CURRENCY_OF = {"kr_stock": "KRW", "kr_etf": "KRW", "us_stock": "USD"}


def _to_krw(amount: float, currency: str, fx: float) -> float:
    return amount * fx if currency == "USD" else amount


def run_basket(client, items: list[dict], *, count: int = 200,
               fx: float | None = None) -> dict:
    """items: [{symbol, amount, asset_class, freq?, weekday?}]"""
    if fx is None:
        try:
            fx = client.get_exchange_rate("USD", "KRW")
        except Exception:
            fx = 1350.0

    per_symbol = []
    # 포트폴리오 자산곡선: 날짜 -> KRW 평가액 합 (각 종목 마지막값 forward-fill)
    all_dates: set[str] = set()
    curves: list[tuple[str, dict, str]] = []  # (symbol, {date:krw_value}, currency)

    tot_invested = tot_value = tot_net = 0.0

    for it in items:
        ac = it["asset_class"]
        currency = CURRENCY_OF[ac]
        fractional = (currency == "USD")
        prof = fees.get_profile(ac)
        candles = get_candles_cached(client, it["symbol"], interval="1d", count=count)
        r = backtest.run_backtest(
            candles, it["amount"],
            freq=it.get("freq", "weekly"), weekday=it.get("weekday", 0),
            allow_fractional=fractional, fee_profile=prof,
            fx_krw=(fx if ac == "us_stock" else None),
        )
        r["symbol"] = it["symbol"]
        r["asset_class"] = ac
        r["currency"] = currency
        per_symbol.append(r)

        tot_invested += _to_krw(r["invested"], currency, fx)
        tot_value += _to_krw(r["market_value"], currency, fx)
        tot_net += _to_krw(r["net_value"], currency, fx)

        cmap = {d: _to_krw(v, currency, fx) for d, v in r["equity_curve"]}
        curves.append((it["symbol"], cmap, currency))
        all_dates.update(cmap.keys())

    # 포트폴리오 MDD: 날짜순으로 각 종목 마지막 평가액을 합산
    peak = mdd = 0.0
    last_val = {s: 0.0 for s, _, _ in curves}
    portfolio_curve = []
    for d in sorted(all_dates):
        for s, cmap, _ in curves:
            if d in cmap:
                last_val[s] = cmap[d]
        total = sum(last_val.values())
        portfolio_curve.append((d, total))
        peak = max(peak, total)
        if peak > 0:
            mdd = max(mdd, (peak - total) / peak)

    profit = tot_value - tot_invested
    net_profit = tot_net - tot_invested
    return {
        "fx": fx,
        "per_symbol": per_symbol,
        "invested_krw": tot_invested,
        "value_krw": tot_value,
        "net_value_krw": tot_net,
        "profit_krw": profit,
        "return_pct": (profit / tot_invested * 100) if tot_invested else 0.0,
        "net_profit_krw": net_profit,
        "net_return_pct": (net_profit / tot_invested * 100) if tot_invested else 0.0,
        "max_drawdown_pct": mdd * 100,
        "period": (portfolio_curve[0][0], portfolio_curve[-1][0]) if portfolio_curve else (None, None),
    }


def format_basket(b: dict) -> str:
    won = lambda x: f"₩{x:,.0f}"
    p0, p1 = b["period"]
    lines = [
        "📦 바스켓 DCA 백테스트 (포트폴리오 합산, KRW 환산)",
        f"기간: {p0} ~ {p1}  | 환율 ₩{b['fx']:,.1f}/$",
        "═" * 52,
        f"{'종목':<10}{'자산':<9}{'세전수익%':>10}{'세후수익%':>10}{'MDD%':>8}",
        "─" * 52,
    ]
    for r in b["per_symbol"]:
        lines.append(
            f"{r['symbol']:<10}{r['asset_class']:<9}"
            f"{r['return_pct']:>9.2f}%{r['net_return_pct']:>9.2f}%"
            f"{r['max_drawdown_pct']:>7.2f}%"
        )
    lines += [
        "═" * 52,
        f"누적 투자액   : {won(b['invested_krw'])}",
        f"평가 금액     : {won(b['value_krw'])}  ({b['return_pct']:+.2f}%)  [세전]",
        f"세후 평가액   : {won(b['net_value_krw'])}  ({b['net_return_pct']:+.2f}%)  [세후]",
        f"포트폴리오 MDD: -{b['max_drawdown_pct']:.2f}%",
    ]
    return "\n".join(lines)
