"""실측 vs 백테스트 대조 — 내가 실제로 한 매매를 기계적 DCA와 같은 잣대로 비교.

지금까지 백테스트는 전부 '가정'이었다(체결가=종가, 수수료=프로파일 상수). 여기서는
실제 체결 이력을 같은 기간·같은 총액의 기계적 DCA와 나란히 놓고,
수수료 가정이 실측과 맞는지도 대조한다.

⚠️ 주문 이력은 **체결 당시 원본** 수량·단가다. 액면분할이 있었다면 분할 이후의
캔들·보유수량과 직접 비교하면 어긋난다(실측: TQQQ·QLD 2025-11-20 2:1 분할).
adjusted/unadjusted 캔들의 비율로 분할 계수를 역산해 체결을 현재 주식 기준으로 환산한다.
"""

from __future__ import annotations

from bisect import bisect_right
from fractions import Fraction

from . import backtest, fees, portfolio
from .cache import get_candles_cached
from .toss_client import TossClient

# 체결 통화로 비용 프로파일 추정 (KR ETF는 구분 불가 → 호출부에서 명시 가능)
_PROFILE_BY_CURRENCY = {"USD": "us_stock", "KRW": "kr_stock"}


# ------------------------------------------------------------ 분할(액면) 보정
def split_factors(client: TossClient, symbol: str, count: int = 3000) -> list[tuple[str, float]]:
    """[(date, factor)] — 그 날 체결한 1주가 지금 몇 주인지. 분할 없으면 전부 1.0.

    adjusted 캔들은 분할을 소급 반영하고 unadjusted는 원본이라, 둘의 비율이 곧
    해당 시점 이후 누적 분할 계수가 된다.
    """
    adjusted = {r["date"]: r["close"] for r in get_candles_cached(client, symbol, count=count)}
    raw = {r["date"]: r["close"] for r in get_candles_cached(client, symbol, count=count,
                                                            adjusted=False)}
    out = []
    for d in sorted(set(adjusted) & set(raw)):
        a = adjusted[d]
        out.append((d, _snap(raw[d] / a) if a else 1.0))
    return out


def _snap(f: float) -> float:
    """종가 비율에는 반올림 오차가 낀다(예: 1.0이어야 할 값이 0.999724).

    분할 비율은 2:1, 3:2처럼 분모가 작은 유리수이므로 가까운 값으로 스냅한다.
    오차가 크면(0.5% 초과) 스냅하지 않고 원값을 남겨 이상을 감춘다.
    """
    near = float(Fraction(f).limit_denominator(20))
    return near if abs(near - f) <= 0.005 * f else f


def _factor_at(factors: list[tuple[str, float]], day: str) -> float:
    """해당 날짜에 적용할 분할 계수. 거래일이 아니면 직전 거래일 값을 쓴다."""
    if not factors:
        return 1.0
    i = bisect_right([d for d, _ in factors], day)
    return factors[i - 1][1] if i else factors[0][1]


def adjust_for_splits(trades: list[dict], factors: list[tuple[str, float]]) -> list[dict]:
    """체결 수량·단가를 현재 주식 기준으로 환산. 체결 '금액'은 분할과 무관해 그대로."""
    out = []
    for t in trades:
        f = _factor_at(factors, t["date"])
        out.append({**t, "quantity": t["quantity"] * f,
                    "price": t["price"] / f if f else t["price"],
                    "split_factor": round(f, 6)})
    return out


# ---------------------------------------------------------------- 실제 성과
def evaluate(trades: list[dict], last_price: float, as_of: str) -> dict:
    """체결 이력 → 실제 성과. as_of는 평가 기준일(IRR의 마지막 현금흐름 날짜)."""
    buy_qty = sum(t["quantity"] for t in trades if t["side"] == "BUY")
    sell_qty = sum(t["quantity"] for t in trades if t["side"] == "SELL")
    buy_amt = sum(t["amount"] for t in trades if t["side"] == "BUY")
    sell_amt = sum(t["amount"] for t in trades if t["side"] == "SELL")
    costs = sum(t["commission"] + t["tax"] for t in trades)

    shares = buy_qty - sell_qty
    market_value = shares * last_price
    invested = buy_amt - sell_amt          # 순투입 (매도 회수분 차감)
    profit = market_value - invested - costs

    # IRR: 실제 체결일 현금흐름 + 평가 기준일에 현재 평가액으로 청산 가정
    flows = [(t["date"], -t["amount"] if t["side"] == "BUY" else t["amount"])
             for t in trades]
    if market_value > 0:
        flows.append((as_of, market_value))
    irr = backtest._xirr(flows) if len(flows) > 1 else None

    return {
        "buy_count": sum(1 for t in trades if t["side"] == "BUY"),
        "sell_count": sum(1 for t in trades if t["side"] == "SELL"),
        "shares": round(shares, 6),
        "invested": round(invested, 2),
        "avg_buy_price": round(buy_amt / buy_qty, 4) if buy_qty else 0.0,
        "last_price": last_price,
        "market_value": round(market_value, 2),
        "costs_paid": round(costs, 4),
        "profit": round(profit, 2),
        "return_pct": round(profit / invested * 100, 2) if invested > 0 else None,
        "irr_pct": round(irr * 100, 2) if irr is not None else None,
    }


def fee_reality_check(trades: list[dict], asset_class: str) -> dict:
    """실제로 낸 매수 수수료율 vs fees.py 가정. 백테스트 비용 가정의 사후 검증."""
    buys = [t for t in trades if t["side"] == "BUY"]
    paid = sum(t["commission"] for t in buys)
    base = sum(t["amount"] for t in buys)
    actual = paid / base if base > 0 else 0.0
    assumed = fees.get_profile(asset_class)["buy_commission"]
    return {
        "asset_class": asset_class,
        "assumed_buy_commission_pct": round(assumed * 100, 4),
        "actual_buy_commission_pct": round(actual * 100, 4),
        "commission_paid": round(paid, 4),
        "buy_amount": round(base, 2),
        # 실측이 가정보다 높으면 백테스트가 비용을 과소평가한 것
        "assumption_understates_cost": actual > assumed + 1e-9,
    }


def _bench_slim(r: dict) -> dict:
    """백테스트 결과에서 비교에 쓰는 지표만 (equity_curve·buys 제외).

    allocated/return_on_allocated_pct: 정수주 제약으로 못 산 유휴현금(cash_carry)까지
    분모에 넣은 값. invested 기준 수익률은 cash drag를 과소평가하므로 공정비교용.
    """
    keys = ["buy_count", "shares", "invested", "cash_carry", "avg_cost", "last_price",
            "market_value", "commission_paid", "profit", "return_pct", "irr_pct",
            "max_drawdown_pct"]
    out = {k: round(r[k], 4) if isinstance(r[k], float) else r[k]
           for k in keys if k in r and r[k] is not None}
    allocated = r.get("invested", 0) + r.get("cash_carry", 0)
    if allocated > 0:
        out["allocated"] = round(allocated, 2)
        out["return_on_allocated_pct"] = round(
            (r["market_value"] + r.get("cash_carry", 0) - allocated) / allocated * 100, 2)
    return out


# ------------------------------------------------------------------- 비교
def compare_with_dca(client: TossClient, symbol: str, *, freq: str = "weekly",
                     weekday: int = 0, asset_class: str | None = None,
                     account_seq: str | None = None) -> dict:
    """내 실제 매매 vs 같은 기간·같은 총액 기계적 DCA.

    벤치마크는 실제 순투입액을 구간 내 매수일 수로 균등 분할해 투입한다
    (`compare_lumpsum_dca`의 동일총액 원칙과 같은 방식).
    """
    seq = account_seq or portfolio.resolve_account(client)
    raw_trades = portfolio.trades(client, account_seq=seq, symbol=symbol)
    if not raw_trades:
        return {"symbol": symbol, "error": "체결 내역이 없습니다."}

    currency = raw_trades[0]["currency"]
    asset_class = asset_class or _PROFILE_BY_CURRENCY.get(currency, "kr_stock")
    start = raw_trades[0]["date"]

    candles = [c for c in get_candles_cached(client, symbol, count=3000)
               if c["date"] >= start]
    if not candles:
        return {"symbol": symbol, "error": f"{start} 이후 캔들 데이터가 없습니다."}
    as_of = candles[-1]["date"]

    factors = split_factors(client, symbol)
    trades = adjust_for_splits(raw_trades, factors)
    splits = sorted({t["split_factor"] for t in trades})

    mine = evaluate(trades, candles[-1]["close"], as_of)

    # 벤치마크: 같은 순투입액을 균등 분할. 미국은 소수주(금액주문) 허용.
    n = backtest.count_buy_days(candles, freq, weekday)
    per_buy = mine["invested"] / n if n else 0.0
    bench = backtest.run_backtest(
        candles, per_buy, freq=freq, weekday=weekday,
        allow_fractional=(currency == "USD"),
        fee_profile=fees.get_profile(asset_class),
    )

    def diff(key, bench_key=None):
        a, b = mine[key], bench.get(bench_key or key)
        return round(a - b, 2) if a is not None and b is not None else None

    return {
        "symbol": symbol,
        "currency": currency,
        "period": {"start": start, "end": as_of},
        "benchmark": f"{freq} DCA, 같은 순투입액을 {n}회 균등 분할",
        "actual": mine,
        "mechanical_dca": _bench_slim(bench),
        "verdict": {
            # 평단이 낮을수록(음수) 내 타이밍이 나았다
            "avg_price_diff": round(mine["avg_buy_price"] - bench["avg_cost"], 4),
            "avg_price_better": mine["avg_buy_price"] < bench["avg_cost"],
            "return_diff_pp": diff("return_pct"),
            "irr_diff_pp": diff("irr_pct"),
        },
        "fees": fee_reality_check(trades, asset_class),
        "reconciliation": _reconcile(client, seq, symbol, mine["shares"]),
        "split_adjusted": any(f != 1.0 for f in splits),
        "caveats": _caveats(mine, bench, splits),
    }


def _reconcile(client: TossClient, account_seq: str, symbol: str, shares: float) -> dict:
    """주문 이력으로 계산한 보유수량 vs 실제 보유수량.

    어긋나면 이력이 불완전하다는 뜻(타사 이관분, API 보관기간 밖 과거 체결 등)이라
    비교 결과 자체를 신뢰할 수 없다. 조용히 넘기지 않고 드러낸다.
    """
    items = client.get_holdings(account_seq, symbol=symbol).get("items", [])
    held = next((float(i["quantity"]) for i in items if i["symbol"] == symbol), None)
    if held is None:
        return {"holdings_shares": None,
                "note": "현재 보유 없음(전량 매도) — 대조 생략"}
    gap = held - shares
    gap_pct = abs(gap) / held * 100 if held else 0.0
    ok = abs(gap) <= max(1e-4, held * 0.001)
    return {
        "trades_shares": round(shares, 6),
        "holdings_shares": round(held, 6),
        "gap_shares": round(gap, 6),
        "gap_pct": round(gap_pct, 3),
        "matched": ok,
        # 1% 이내면 배당 재투자·분할 잔주 수준(비교는 유효), 넘으면 이력 자체가 불완전
        "severity": "ok" if ok else ("minor" if gap_pct <= 1.0 else "material"),
        "note": None if ok else
        f"주문 이력 기준 수량이 실제 보유와 {gap:+.6f}주({gap_pct:.2f}%) 다릅니다 — "
        "배당 재투자·분할 잔주(작은 차이), 또는 타사 이관분·API 보관기간 밖 과거 체결"
        "(큰 차이)일 수 있습니다."
        + (" 차이가 작아 비교 결과는 대체로 유효합니다."
           if gap_pct <= 1.0 else " 차이가 커서 비교 결과를 신뢰하기 어렵습니다."),
    }


def _caveats(mine: dict, bench: dict, splits: list[float]) -> list[str]:
    out = []
    if mine["sell_count"]:
        out.append("매도 내역이 있어 근사 비교입니다 (벤치마크는 매수 후 보유 가정).")
    applied = [f for f in splits if f != 1.0]
    if applied:
        out.append(f"액면분할 보정 적용 (계수 {applied}) — 주문 이력의 체결 수량·단가는 "
                   "체결 당시 원본이라 현재 주식 기준으로 환산했습니다.")
    carry = bench.get("cash_carry", 0)
    allocated = bench.get("invested", 0) + carry
    if allocated > 0 and carry / allocated > 0.1:
        out.append(
            f"벤치마크가 배정액의 {carry / allocated * 100:.0f}%를 집행하지 못했습니다 "
            f"(정수주 제약: 1회 배정 {bench.get('invested', 0) / max(bench.get('buy_count', 1), 1):,.0f} < 주가). "
            "동일총액 비교가 성립하지 않으니 freq='monthly'로 회당 금액을 키우거나 "
            "return_on_allocated_pct로 보십시오.")
    return out
