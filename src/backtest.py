"""간단한 DCA(정액 분할매수) 백테스트.

과거 일봉 위에서 '정해진 요일마다 일정 금액 매수'를 반복했다면
지금 수익이 어땠을지 계산한다. 실거래/주문과 무관한 순수 시뮬레이션이다.

가정(단순화):
- 매수 체결가 = 해당 봉의 종가(close)
- 소수점 주식 허용 안 함(국내 기준): 배정액 ÷ 종가 = 정수 주, 나머지 현금은 이월
- 수수료/세금/슬리피지 미반영 (1차 PoC)
"""

from __future__ import annotations

from datetime import date

from . import fees as fees_mod

# 0=월 ... 6=일
WEEKDAY_KR = ["월", "화", "수", "목", "금", "토", "일"]


def _is_buy_day(d: date, weekday: int, last_month: int | None,
                freq: str) -> bool:
    if freq == "weekly":
        return d.weekday() == weekday
    if freq == "monthly":
        # 해당 월에 처음 등장하는 거래일에 매수
        return d.month != last_month
    raise ValueError(f"알 수 없는 freq: {freq}")


def _sma(closes: list[float], i: int, window: int) -> float | None:
    """closes[i] 포함 직전 window개 종가의 단순이동평균. 데이터 부족 시 None."""
    if i + 1 < window:
        return None
    return sum(closes[i + 1 - window: i + 1]) / window


def _xirr(cashflows: list[tuple[str, float]]) -> float | None:
    """연환산 자금가중수익률(IRR). cashflows=[(date, amount)] 매수는 음수, 최종평가 양수.

    DCA처럼 투자 시점이 분산된 전략의 '공정한 연수익률'. 부호변화 없으면 None.
    """
    if len(cashflows) < 2:
        return None
    d0 = date.fromisoformat(cashflows[0][0])

    def npv(r: float) -> float:
        return sum(cf / ((1 + r) ** ((date.fromisoformat(d) - d0).days / 365.0))
                   for d, cf in cashflows)

    lo, hi = -0.9999, 10.0
    flo, fhi = npv(lo), npv(hi)
    if flo * fhi > 0:
        return None  # 해가 구간 밖
    for _ in range(200):
        mid = (lo + hi) / 2
        fm = npv(mid)
        if abs(fm) < 1e-7:
            return mid
        if flo * fm < 0:
            hi = mid
        else:
            lo, flo = mid, fm
    return (lo + hi) / 2


def _rsi(closes: list[float], i: int, window: int) -> float | None:
    """closes[i] 기준 RSI(window). 데이터 부족 시 None. (단순평균 방식)"""
    if i < window:
        return None
    gains = losses = 0.0
    for k in range(i - window + 1, i + 1):
        ch = closes[k] - closes[k - 1]
        if ch >= 0:
            gains += ch
        else:
            losses -= ch
    if losses == 0:
        return 100.0
    rs = (gains / window) / (losses / window)
    return 100 - 100 / (1 + rs)


def count_buy_days(candles: list[dict], freq: str, weekday: int) -> int:
    """해당 구간에서 매수가 발생할 거래일 수 (거치식 총액 산정용)."""
    n = 0
    last_month: int | None = None
    for c in candles:
        d = date.fromisoformat(c["date"])
        if c["close"] <= 0:
            continue
        if _is_buy_day(d, weekday, last_month, freq):
            n += 1
            last_month = d.month
    return n


def run_backtest(candles: list[dict], amount: float, *,
                 freq: str = "weekly", weekday: int = 0,
                 allow_fractional: bool = False,
                 fee_profile: dict | None = None,
                 fx_krw: float | None = None,
                 ma_window: int | None = None,
                 ma_mode: str = "below",
                 rsi_window: int | None = None,
                 rsi_threshold: float = 30.0,
                 slippage: float = 0.0,
                 next_bar_fill: bool = True,
                 single_shot: bool = False) -> dict:
    """DCA 백테스트 실행.

    candles: get_candles() 결과 (오래된→최신, close 포함)
    amount: 1회 매수 배정 금액
    freq: 'weekly' | 'monthly'
    weekday: weekly일 때 매수 요일 (0=월)
    allow_fractional: 소수 주 허용(미국 금액주문 모사). False면 정수 주.
    fee_profile: fees.get_profile() 결과. None이면 수수료/세금 0.
    fx_krw: 미국 양도세 원화공제 환산용 USD/KRW 환율.
    ma_window: 지정 시 이평선 조건부 매수(예: 20). None이면 일반 DCA.
    ma_mode: 'below'=종가<이평선일 때만 매수, 'above'=종가>이평선일 때만.
             조건 미충족 회차의 배정금은 현금으로 모아 다음 매수 때 투입.
    rsi_window: 지정 시 RSI 과매도 조건부 매수(예: 14). rsi_threshold 미만일 때만 매수.
    ma_window·rsi_window를 함께 주면 둘 다 충족해야 매수(AND).
    slippage: 체결 슬리피지(분수, 예 0.001=0.1%). 종가보다 그만큼 불리하게 매수.
    next_bar_fill: True(기본)면 조건부(이평선/RSI) 매수를 다음날 시가에 체결(룩어헤드 방지).
                   일반 DCA는 신호가 없어 영향 없음. False면 당일 종가 체결(구버전, 비교용).
    single_shot: True면 첫 매수일에 amount 전액을 한 번만 매수(거치식). 이후 매수 없음.
    """
    prof = fee_profile or {}
    buy_comm = prof.get("buy_commission", 0.0)
    closes = [c["close"] for c in candles]

    shares = 0.0
    invested = 0.0      # 실제 매수에 쓴 금액(수수료 포함)
    commission_paid = 0.0
    cash_carry = 0.0    # 미투자 현금(잔돈 + 조건 미충족분 이월)
    buys: list[dict] = []
    skipped = 0         # 이평선 조건 미충족으로 건너뛴 매수일 수
    last_month: int | None = None

    equity_curve: list[tuple[str, float]] = []
    peak = 0.0
    max_drawdown = 0.0

    for i, c in enumerate(candles):
        d = date.fromisoformat(c["date"])
        price = c["close"]
        if price <= 0:
            continue

        # 거치식: 첫 매수일에 한 번만 매수하고 이후엔 보유만
        if single_shot and buys:
            value = shares * price
            equity_curve.append((c["date"], value))
            peak = max(peak, value)
            if peak > 0:
                max_drawdown = max(max_drawdown, (peak - value) / peak)
            continue

        if _is_buy_day(d, weekday, last_month, freq):
            last_month = d.month
            # 조건부 필터(이평선/RSI): 데이터 충분 + 미충족이면 매수 보류(현금 적립)
            conditions_ok = True
            if ma_window:
                ma = _sma(closes, i, ma_window)
                if ma is not None:
                    conditions_ok &= (price < ma if ma_mode == "below" else price > ma)
            if rsi_window:
                rsi = _rsi(closes, i, rsi_window)
                if rsi is not None:
                    conditions_ok &= (rsi < rsi_threshold)
            if (ma_window or rsi_window) and not conditions_ok:
                cash_carry += amount
                skipped += 1
                value = shares * price
                equity_curve.append((c["date"], value))
                peak = max(peak, value)
                if peak > 0:
                    max_drawdown = max(max_drawdown, (peak - value) / peak)
                continue

            budget = amount + cash_carry
            # 조건부(이평선/RSI) 매수는 종가에 신호 확인 → 룩어헤드 방지 위해 다음날 시가 체결.
            # 일반 DCA(신호 없음)는 룩어헤드가 없어 당일 종가 체결.
            conditional = bool(ma_window or rsi_window)
            if conditional and next_bar_fill:
                if i + 1 >= len(candles):
                    cash_carry = budget          # 다음 봉 없음 → 체결 보류, 현금 유지
                    value = shares * price
                    equity_curve.append((c["date"], value))
                    peak = max(peak, value)
                    if peak > 0:
                        max_drawdown = max(max_drawdown, (peak - value) / peak)
                    continue
                fill_price = candles[i + 1]["open"]
                buy_date = candles[i + 1]["date"]
            else:
                fill_price = price               # 당일 종가
                buy_date = c["date"]
            # 체결단가 = 체결가 × (1+슬리피지) × (1+수수료)
            unit = fill_price * (1 + slippage) * (1 + buy_comm)
            if allow_fractional:
                qty = budget / unit
                spent = budget
                cash_carry = 0.0
            else:
                qty = budget // unit            # 정수 주
                spent = qty * unit
                cash_carry = budget - spent     # 잔돈 이월
            if qty > 0:
                shares += qty
                invested += spent
                commission_paid += qty * fill_price * buy_comm
                buys.append({"date": buy_date, "price": fill_price,
                             "qty": qty, "spent": spent})

        # 매 거래일 평가금액으로 자산곡선/MDD 갱신
        value = shares * price
        equity_curve.append((c["date"], value))
        peak = max(peak, value)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - value) / peak)

    last_price = candles[-1]["close"] if candles else 0.0
    market_value = shares * last_price
    avg_cost = invested / shares if shares else 0.0
    profit = market_value - invested
    ret = (profit / invested * 100) if invested else 0.0

    # 세후(전량 청산 가정) 실수령 추정
    costs = fees_mod.sell_costs(prof, market_value, invested, fx_krw) if prof else \
        {"sell_tax": 0.0, "cap_gains_tax": 0.0, "total": 0.0}
    net_value = market_value - costs["total"]
    net_profit = net_value - invested
    net_ret = (net_profit / invested * 100) if invested else 0.0

    # 연환산 자금가중수익률(IRR) — 구간 길이 무관 공정 비교용
    irr = None
    if buys and market_value > 0:
        cashflows = [(b["date"], -b["spent"]) for b in buys]
        cashflows.append((candles[-1]["date"], market_value))
        r = _xirr(cashflows)
        irr = r * 100 if r is not None else None

    return {
        "period": (candles[0]["date"], candles[-1]["date"]) if candles else (None, None),
        "buy_count": len(buys),
        "skipped": skipped,
        "shares": shares,
        "invested": invested,
        "commission_paid": commission_paid,
        "cash_carry": cash_carry,
        "avg_cost": avg_cost,
        "last_price": last_price,
        "market_value": market_value,
        "profit": profit,
        "return_pct": ret,
        "irr_pct": irr,
        "sell_tax": costs["sell_tax"],
        "cap_gains_tax": costs["cap_gains_tax"],
        "net_value": net_value,
        "net_profit": net_profit,
        "net_return_pct": net_ret,
        "max_drawdown_pct": max_drawdown * 100,
        "equity_curve": equity_curve,
        "buys": buys,
    }


def compare_lumpsum_dca(candles: list[dict], amount: float, *,
                        freq: str = "weekly", weekday: int = 0,
                        allow_fractional: bool = False,
                        fee_profile: dict | None = None,
                        fx_krw: float | None = None,
                        slippage: float = 0.0) -> dict:
    """거치식 vs 적립식(DCA) 동일 총액 비교.

    DCA: 매 주기 amount 매수.
    거치식: 같은 총액(= 매수일 수 × amount)을 첫 매수일에 일괄 매수.
    """
    n = count_buy_days(candles, freq, weekday)
    total = n * amount

    dca = run_backtest(candles, amount, freq=freq, weekday=weekday,
                       allow_fractional=allow_fractional,
                       fee_profile=fee_profile, fx_krw=fx_krw, slippage=slippage)
    lump = run_backtest(candles, total, freq=freq, weekday=weekday,
                        allow_fractional=allow_fractional,
                        fee_profile=fee_profile, fx_krw=fx_krw, slippage=slippage,
                        single_shot=True)
    return {"buy_days": n, "total_planned": total, "dca": dca, "lump": lump}


def format_compare(symbol: str, params: dict, cmp: dict) -> str:
    cur = "$" if params.get("us") else "₩"
    fmt = (lambda x: f"{cur}{x:,.2f}") if params.get("us") else (lambda x: f"{cur}{x:,.0f}")
    freq_label = (f"매주 {WEEKDAY_KR[params['weekday']]}요일"
                  if params["freq"] == "weekly" else "매월 첫 거래일")
    d, l = cmp["dca"], cmp["lump"]
    p0, p1 = d["period"]
    lines = [
        f"⚖️  거치식 vs 적립식(DCA) — {symbol}",
        f"기간: {p0} ~ {p1}",
        f"총 투자 계획액: {fmt(cmp['total_planned'])} ({freq_label} {fmt(params['amount'])} × {cmp['buy_days']}회)",
        "═" * 44,
        f"{'':12}{'적립식(DCA)':>16}{'거치식(일괄)':>16}",
        "─" * 44,
        f"{'실투자액':12}{fmt(d['invested']):>16}{fmt(l['invested']):>16}",
        f"{'평가금액':12}{fmt(d['market_value']):>16}{fmt(l['market_value']):>16}",
        f"{'세전수익률':12}{d['return_pct']:>15.2f}%{l['return_pct']:>15.2f}%",
        f"{'세후수익률':12}{d['net_return_pct']:>15.2f}%{l['net_return_pct']:>15.2f}%",
        f"{'최대낙폭MDD':12}{-d['max_drawdown_pct']:>15.2f}%{-l['max_drawdown_pct']:>15.2f}%",
        "═" * 44,
    ]
    diff = d["return_pct"] - l["return_pct"]
    winner = "적립식(DCA)" if diff > 0 else "거치식(일괄)"
    lines.append(f"→ 이 구간 우위: *{winner}* (세전 {abs(diff):.2f}%p 차이)")
    return "\n".join(lines)


def format_report(symbol: str, params: dict, r: dict) -> str:
    """백테스트 결과를 사람이 읽는 텍스트로."""
    cur = "₩" if not params.get("us") else "$"
    fmt = (lambda x: f"{cur}{x:,.2f}") if params.get("us") else (lambda x: f"{cur}{x:,.0f}")
    freq_label = (f"매주 {WEEKDAY_KR[params['weekday']]}요일"
                  if params["freq"] == "weekly" else "매월 첫 거래일")
    p0, p1 = r["period"]
    lines = [
        f"📊 DCA 백테스트 — {symbol}",
        f"기간: {p0} ~ {p1}",
        f"전략: {freq_label} {fmt(params['amount'])} 매수",
        "─" * 36,
        f"매수 횟수   : {r['buy_count']}회"
        + (f" (조건 미충족 {r['skipped']}회 보류)" if r.get("skipped") else ""),
        f"누적 매수액 : {fmt(r['invested'])}",
        f"보유 수량   : {r['shares']:,.4f}주",
        f"평균 단가   : {fmt(r['avg_cost'])}",
        f"현재가      : {fmt(r['last_price'])}",
        f"평가 금액   : {fmt(r['market_value'])}",
        f"평가 손익   : {fmt(r['profit'])} ({r['return_pct']:+.2f}%)  [세전]",
        f"연환산(IRR) : {r['irr_pct']:+.2f}%/년" if r.get("irr_pct") is not None
        else "연환산(IRR) : -",
        f"최대 낙폭   : -{r['max_drawdown_pct']:.2f}% (MDD)",
    ]
    # 세후(청산 가정) — 비용이 있을 때만 표기
    if r.get("commission_paid", 0) or r.get("sell_tax", 0) or r.get("cap_gains_tax", 0):
        lines.append("─" * 36)
        lines.append("〈수수료·세금 반영(전량 청산 가정)〉")
        if r.get("commission_paid", 0):
            lines.append(f"매수 수수료 : {fmt(r['commission_paid'])}")
        if r.get("sell_tax", 0):
            lines.append(f"매도 거래세 : {fmt(r['sell_tax'])}")
        if r.get("cap_gains_tax", 0):
            lines.append(f"양도소득세  : {fmt(r['cap_gains_tax'])}")
        lines.append(f"세후 평가액 : {fmt(r['net_value'])}")
        lines.append(f"세후 손익   : {fmt(r['net_profit'])} ({r['net_return_pct']:+.2f}%)  [세후]")
    if r["cash_carry"] > 0:
        lines.append(f"미투자 잔돈 : {fmt(r['cash_carry'])}")
    return "\n".join(lines)
