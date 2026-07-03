"""듀얼 모멘텀(Dual Momentum) 백테스트.

매월 리밸런싱:
  1) 상대 모멘텀: 후보 자산 중 lookback 기간 수익률 1위 선택
  2) 절대 모멘텀: 그 수익률이 0 이하면 현금(CASH)으로 회피
선택이 바뀌면 전액 갈아탄다(로테이션).

DCA(적립)와 다른 거치식·로테이션 전략이다. 핵심 관찰 포인트는
'잦은 매매가 수수료·세금으로 수익을 얼마나 갉아먹는가'.

통화: 여러 통화 섞이면 '현재 환율' 단일값으로 KRW 환산(과거 환율 변동 미반영).
세금: 전환 시 매도거래세 + 매수수수료를 반영. 미국 양도소득세(22%/연250만원공제)는
      연단위 netting이 필요해 v1에서는 미반영(아래 caveat 참고).
"""

from __future__ import annotations

from datetime import date

from . import fees
from . import fx as fx_mod

CASH = "CASH"
CURRENCY_OF = {"kr_stock": "KRW", "kr_etf": "KRW", "us_stock": "USD"}


def us_capgains_tax(realized: list[tuple[str, float]], *,
                    deduction_krw: float = fees.US_CAPGAINS_DEDUCTION_KRW,
                    rate: float = 0.22) -> float:
    """미국 양도소득세(연단위 손익통산). realized=[(연도, 손익KRW)].

    연도별로 손익을 합산(net) → 250만원 공제 → 양(+)이면 22% 과세. 연도별 합.
    """
    by_year: dict[str, float] = {}
    for year, pnl in realized:
        by_year[year] = by_year.get(year, 0.0) + pnl
    tax = 0.0
    for net in by_year.values():
        taxable = max(0.0, net - deduction_krw)
        tax += taxable * rate
    return tax


def _build_axis(series: dict[str, dict[str, float]]) -> list[str]:
    """모든 종목 날짜의 합집합(정렬)."""
    dates: set[str] = set()
    for cmap in series.values():
        dates.update(cmap.keys())
    return sorted(dates)


def _ffill_price(cmap: dict[str, float], axis: list[str]) -> list[float]:
    """axis 순서대로 마지막 알려진 종가로 forward-fill."""
    out = []
    last = 0.0
    for d in axis:
        if d in cmap:
            last = cmap[d]
        out.append(last)
    return out


def run_dual_momentum(candles_by_symbol: dict[str, list[dict]],
                      asset_classes: dict[str, str], *,
                      lookback_days: int = 252,
                      initial_capital: float = 1_000_000,
                      fx: float = 1350.0,
                      absolute_filter: bool = True,
                      safe_asset: str | None = None,
                      fx_series: dict | None = None) -> dict:
    """dual momentum 시뮬레이션.

    candles_by_symbol: {symbol: get_candles 결과(오래된→최신)}
    asset_classes: {symbol: 'kr_stock'|'kr_etf'|'us_stock'}  (수수료/세금/통화 결정)
    lookback_days: 모멘텀 측정 기간(거래일). 252≈12개월.
    safe_asset: 절대모멘텀 실패 시 회피처(채권ETF 등). None이면 현금(0%).
                지정 시 절대모멘텀 기준 = safe_asset의 모멘텀(GEM 방식).
    """
    symbols = list(candles_by_symbol)
    risky = [s for s in symbols if s != safe_asset]
    # KRW 환산 종가맵 {symbol: {date: krw_close}}. fx_series 있으면 날짜별 환율.
    def krw_mul(cur: str, day: str) -> float:
        if cur != "USD":
            return 1.0
        if fx_series:
            return fx_mod.fx_on(fx_series, day) or fx
        return fx
    series: dict[str, dict[str, float]] = {}
    for s in symbols:
        cur = CURRENCY_OF[asset_classes[s]]
        series[s] = {c["date"]: c["close"] * krw_mul(cur, c["date"])
                     for c in candles_by_symbol[s]}

    axis = _build_axis(series)
    prices = {s: _ffill_price(series[s], axis) for s in symbols}

    cash = initial_capital      # 현금 보유액(KRW)
    holding = CASH              # 현재 보유 자산
    units = 0.0                # 보유 수량(KRW환산가 기준 추상 단위)
    switches = 0
    total_sell_tax = total_buy_comm = 0.0
    us_realized: list[tuple[str, float]] = []   # 미국 실현 손익 [(연도, KRW)] — 양도세 네팅용
    cost_basis = 0.0           # 현재 보유분 매입원가(KRW)
    trades: list[dict] = []     # 매매 타임라인 [{date, from, to}]

    equity_curve: list[tuple[str, float]] = []
    peak = mdd = 0.0
    last_month: int | None = None

    def portfolio_value(i: int) -> float:
        return cash if holding == CASH else units * prices[holding][i]

    def sell(i: int):
        nonlocal cash, holding, units, total_sell_tax, cost_basis
        if holding == CASH:
            return
        gross = units * prices[holding][i]
        prof = fees.get_profile(asset_classes[holding])
        tax = gross * prof.get("sell_tax", 0.0)
        total_sell_tax += tax
        if asset_classes[holding] == "us_stock":   # 손익 모두 기록(네팅 위해 손실 포함)
            us_realized.append((axis[i][:4], gross - cost_basis))
        cash = gross - tax
        holding, units, cost_basis = CASH, 0.0, 0.0

    def buy(i: int, target: str):
        nonlocal cash, holding, units, total_buy_comm, cost_basis
        prof = fees.get_profile(asset_classes[target])
        comm_rate = prof.get("buy_commission", 0.0)
        invest = cash / (1 + comm_rate)     # 수수료 포함 예산 배분
        total_buy_comm += invest * comm_rate
        units = invest / prices[target][i]
        cost_basis = invest
        holding, cash = target, 0.0

    for i, d in enumerate(axis):
        dt = date.fromisoformat(d)
        is_rebalance = dt.month != last_month and i >= lookback_days
        if is_rebalance:
            last_month = dt.month
            # 상대 모멘텀: lookback 수익률 (위험자산만 후보)
            moms = {}
            for s in symbols:
                p0 = prices[s][i - lookback_days]
                if p0 > 0:
                    moms[s] = prices[s][i] / p0 - 1
            risky_moms = {s: m for s, m in moms.items() if s in risky}
            if risky_moms:
                best = max(risky_moms, key=risky_moms.get)
                # 절대 모멘텀: 기준은 safe_asset 모멘텀(있으면) 또는 0
                threshold = moms.get(safe_asset, 0.0) if safe_asset else 0.0
                if absolute_filter and risky_moms[best] <= threshold:
                    target = safe_asset if safe_asset else CASH
                else:
                    target = best
                if target != holding:
                    prev = holding
                    sell(i)
                    if target != CASH:
                        buy(i, target)
                    switches += 1
                    trades.append({"date": d, "from": prev, "to": target})
        elif dt.month != last_month and last_month is None:
            last_month = dt.month  # 워밍업 구간 월 추적

        v = portfolio_value(i)
        equity_curve.append((d, v))
        peak = max(peak, v)
        if peak > 0:
            mdd = max(mdd, (peak - v) / peak)

    final = portfolio_value(len(axis) - 1)
    profit = final - initial_capital
    # 시작 시점 = 첫 리밸런싱 가능일
    start_i = lookback_days if lookback_days < len(axis) else 0

    # 미국 양도소득세(연단위 네팅): 마지막에 US 보유 중이면 청산 가정으로 실현 추가
    realized_for_tax = list(us_realized)
    if holding != CASH and asset_classes.get(holding) == "us_stock":
        gross = units * prices[holding][len(axis) - 1]
        realized_for_tax.append((axis[-1][:4], gross - cost_basis))
    cap_gains_tax = us_capgains_tax(realized_for_tax)
    net_final = final - cap_gains_tax
    net_return = (net_final / initial_capital - 1) * 100 if initial_capital else 0.0

    # CAGR(연환산) — 초기자본 일괄 운용이므로 단순 복리 환산
    cagr = net_cagr = None
    if axis and final > 0 and initial_capital > 0:
        years = (date.fromisoformat(axis[-1]) - date.fromisoformat(axis[start_i])).days / 365.0
        if years > 0:
            cagr = ((final / initial_capital) ** (1 / years) - 1) * 100
            if net_final > 0:
                net_cagr = ((net_final / initial_capital) ** (1 / years) - 1) * 100

    # 벤치마크: 위험자산 동일비중 단순보유(시작일 매수 후 홀드)
    bench = {"return_pct": None, "max_drawdown_pct": None}
    if start_i < len(axis) - 1 and risky:
        w = initial_capital / len(risky)
        bench_units = {s: (w / prices[s][start_i] if prices[s][start_i] > 0 else 0.0)
                       for s in risky}
        bpeak = bmdd = 0.0
        for j in range(start_i, len(axis)):
            bv = sum(bench_units[s] * prices[s][j] for s in risky)
            bpeak = max(bpeak, bv)
            if bpeak > 0:
                bmdd = max(bmdd, (bpeak - bv) / bpeak)
        bfinal = sum(bench_units[s] * prices[s][-1] for s in risky)
        bench = {
            "return_pct": (bfinal / initial_capital - 1) * 100,
            "max_drawdown_pct": bmdd * 100,
        }

    return {
        "period": (axis[start_i], axis[-1]) if axis else (None, None),
        "lookback_days": lookback_days,
        "initial_capital": initial_capital,
        "final_value": final,
        "profit": profit,
        "return_pct": (profit / initial_capital * 100) if initial_capital else 0.0,
        "cagr_pct": cagr,
        "max_drawdown_pct": mdd * 100,
        "switches": switches,
        "sell_tax_paid": total_sell_tax,
        "buy_comm_paid": total_buy_comm,
        "us_capgains_tax": cap_gains_tax,        # 연단위 네팅 양도세(KRW)
        "net_value_after_tax": net_final,        # 양도세 차감 후 평가액
        "net_return_pct": net_return,            # 세후 수익률
        "net_cagr_pct": net_cagr,                # 세후 CAGR
        "final_holding": holding,
        "trades": trades,
        "benchmark": bench,
        "safe_asset": safe_asset,
        "fx": fx,
    }


def format_momentum(symbols: list[str], r: dict) -> str:
    won = lambda x: f"₩{x:,.0f}"
    p0, p1 = r["period"]
    safe = r.get("safe_asset")
    avoid = f"{safe}(안전자산)로 회피" if safe else "현금으로 회피"
    lines = [
        "🔄 듀얼 모멘텀 백테스트 (월별 리밸런싱, KRW 환산)",
        f"기간: {p0} ~ {p1}  | lookback {r['lookback_days']}일(≈{r['lookback_days']//21}개월) | 환율 ₩{r['fx']:,.0f}/$",
        f"후보: {', '.join(symbols)}  (절대모멘텀 실패 시 {avoid})",
        "═" * 46,
        f"초기 자본   : {won(r['initial_capital'])}",
        f"최종 평가액 : {won(r['final_value'])}  ({r['return_pct']:+.2f}%)",
        f"연환산(CAGR): {r['cagr_pct']:+.2f}%/년" if r.get("cagr_pct") is not None
        else "연환산(CAGR): -",
        f"최대 낙폭   : -{r['max_drawdown_pct']:.2f}% (MDD)",
        f"전환 횟수   : {r['switches']}회  (현재 보유: {r['final_holding']})",
    ]
    # 벤치마크 비교
    b = r.get("benchmark", {})
    if b.get("return_pct") is not None:
        diff = r["return_pct"] - b["return_pct"]
        verdict = "초과달성 ✅" if diff > 0 else "하회 ❌"
        lines += [
            "─" * 46,
            "〈벤치마크: 후보 동일비중 단순보유〉",
            f"벤치 수익률 : {b['return_pct']:+.2f}%  (MDD -{b['max_drawdown_pct']:.2f}%)",
            f"모멘텀 대비 : {diff:+.2f}%p  {verdict}",
        ]
    # 매매 타임라인
    if r.get("trades"):
        lines.append("─" * 46)
        lines.append("〈매매 타임라인〉")
        for t in r["trades"]:
            lines.append(f"  {t['date']}  {t['from']} → {t['to']}")
    # 비용 + 세금
    lines += [
        "─" * 46,
        "〈매매 비용·세금〉",
        f"매도 거래세 : {won(r['sell_tax_paid'])}",
        f"매수 수수료 : {won(r['buy_comm_paid'])}",
    ]
    cgt = r.get("us_capgains_tax", 0.0)
    if cgt > 0:
        net_cagr = f"{r['net_cagr_pct']:+.2f}%/년" if r.get("net_cagr_pct") is not None else "-"
        lines += [
            f"미국 양도세 : {won(cgt)}  (연단위 네팅, 250만원공제 후 22%)",
            "─" * 46,
            f"세후 평가액 : {won(r['net_value_after_tax'])}  ({r['net_return_pct']:+.2f}%)",
            f"세후 CAGR   : {net_cagr}",
        ]
    return "\n".join(lines)
