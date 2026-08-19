"""토스 백테스트 MCP 서버 — Claude에서 백테스트·검증·신호를 대화로 실행.

읽기 전용 분석만 노출한다(주문/실거래 도구는 의도적으로 제외).
실행: python -m src.mcp_server  (Claude 설정에 등록해 사용)

필요 env: TOSS_CLIENT_ID, TOSS_CLIENT_SECRET
"""

from __future__ import annotations

import os
import math
import statistics
from pathlib import Path

from dotenv import load_dotenv
from fastmcp import FastMCP

from .toss_client import TossClient
from .cache import get_candles_cached
from . import backtest, fees, momentum, portfolio, validation, signal_eval, signals
from . import fx as fx_mod

# MCP는 임의 작업디렉터리에서 실행되므로 프로젝트 .env를 명시적으로 로드
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
mcp = FastMCP("toss-backtest")

_client: TossClient | None = None


def _c() -> TossClient:
    global _client
    if _client is None:
        cid, csec = os.getenv("TOSS_CLIENT_ID"), os.getenv("TOSS_CLIENT_SECRET")
        if not cid or not csec:
            raise RuntimeError("TOSS_CLIENT_ID / TOSS_CLIENT_SECRET 환경변수가 필요합니다.")
        _client = TossClient(cid, csec)
    return _client


def _candles(symbol: str, count: int, start: str | None, end: str | None):
    rows = get_candles_cached(_c(), symbol, interval="1d", count=count)
    if start:
        rows = [r for r in rows if r["date"] >= start]
    if end:
        rows = [r for r in rows if r["date"] <= end]
    return rows


def _slim(r: dict) -> dict:
    """백테스트 결과에서 LLM에 보낼 핵심 지표만 추림(equity_curve/buys 등 큰 필드 제외)."""
    keys = ["period", "buy_count", "skipped", "shares", "invested", "avg_cost",
            "last_price", "market_value", "return_pct", "irr_pct",
            "net_return_pct", "max_drawdown_pct"]
    return {k: r[k] for k in keys if k in r}


# --------------------------------------------------------------------- tools
@mcp.tool()
def backtest_dca(symbol: str, amount: float, asset_class: str = "kr_stock",
                 freq: str = "weekly", weekday: int = 0, count: int = 600,
                 ma: int = 0, rsi: int = 0, start: str = "", end: str = "") -> dict:
    """정액 분할매수(DCA) 백테스트. 과거 일봉으로 시뮬, 주문 안 함.

    symbol: 종목 (KR 6자리 005930, US 티커 AAPL)
    amount: 1회 매수 금액 (KR=원, US=달러)
    asset_class: kr_stock | kr_etf | us_stock (수수료·세금 반영)
    freq: weekly | monthly,  weekday: 0=월~4=금 (weekly일 때)
    count: 가져올 일봉 수(최대 ~3000=12년)
    ma: >0이면 N일 이평선 아래일 때만 매수 / rsi: >0이면 RSI<30일 때만 매수
    start/end: 'YYYY-MM-DD' 구간 한정(선택)
    반환: 수익률·IRR(연환산)·MDD·세후수익률 등 요약.
    """
    rows = _candles(symbol, count, start or None, end or None)
    if not rows:
        return {"error": "캔들 데이터 없음"}
    us = asset_class == "us_stock"
    prof = fees.get_profile(asset_class)
    fx_krw = _c().get_exchange_rate("USD", "KRW") if us else None
    r = backtest.run_backtest(
        rows, amount, freq=freq, weekday=weekday, allow_fractional=us,
        fee_profile=prof, fx_krw=fx_krw,
        ma_window=(ma or None), rsi_window=(rsi or None))
    return _slim(r)


@mcp.tool()
def lumpsum_vs_dca(symbol: str, amount: float, asset_class: str = "kr_stock",
                   freq: str = "weekly", weekday: int = 0, count: int = 600,
                   start: str = "", end: str = "") -> dict:
    """거치식(첫날 일괄) vs 적립식(DCA)을 동일 총액으로 비교. 구간 우위를 알려준다."""
    rows = _candles(symbol, count, start or None, end or None)
    if not rows:
        return {"error": "캔들 데이터 없음"}
    us = asset_class == "us_stock"
    prof = fees.get_profile(asset_class)
    fx_krw = _c().get_exchange_rate("USD", "KRW") if us else None
    cmp = backtest.compare_lumpsum_dca(
        rows, amount, freq=freq, weekday=weekday,
        allow_fractional=us, fee_profile=prof, fx_krw=fx_krw)
    return {"buy_days": cmp["buy_days"], "total_planned": cmp["total_planned"],
            "dca": _slim(cmp["dca"]), "lumpsum": _slim(cmp["lump"])}


@mcp.tool()
def signal_efficacy(symbol: str, count: int = 2000, ma: int = 20,
                    rsi: int = 14, rsi_threshold: float = 30.0,
                    horizons: str = "5,20,60") -> dict:
    """신호 성과 추적(이벤트 스터디): 이평선/RSI 신호 이후 N일 수익률이
    평소(baseline)보다 높은지 edge를 측정. dip-buying의 예측력 검증용."""
    rows = _candles(symbol, count, None, None)
    if not rows:
        return {"error": "캔들 데이터 없음"}
    hs = [int(x) for x in horizons.split(",")]
    return signal_eval.evaluate_efficacy(
        rows, ma_window=ma, rsi_window=rsi,
        rsi_threshold=rsi_threshold, horizons=hs)


@mcp.tool()
def walk_forward(strategy: str, symbol: str, asset_class: str = "kr_stock",
                 amount: float = 100000, count: int = 3000,
                 ma: int = 20, rsi: int = 14,
                 wf_horizon: int = 12, wf_step: int = 3) -> dict:
    """전략 견고성 검증(walk-forward): 테스트 창을 굴리며 성과 분포 측정.

    strategy: dca | ma | rsi
    벤치마크 — dca는 거치식, ma/rsi는 일반 DCA. 반환: 평균/중앙값/플러스비율/벤치초과비율 등.
    """
    rows = get_candles_cached(_c(), symbol, interval="1d", count=count)
    if not rows:
        return {"error": "캔들 데이터 없음"}
    us = asset_class == "us_stock"
    prof = fees.get_profile(asset_class)
    common = dict(freq="weekly", weekday=0, allow_fractional=us, fee_profile=prof)

    def slc(s, e):
        return [c for c in rows if s <= c["date"] <= e]

    def run_window(_ds, ts, end):
        seg = slc(ts, end)
        if len(seg) < max(ma, rsi) + 5:
            return None
        if strategy == "dca":
            cmp = backtest.compare_lumpsum_dca(seg, amount, **common)
            return {"return_pct": cmp["dca"]["return_pct"],
                    "mdd_pct": cmp["dca"]["max_drawdown_pct"],
                    "bench_pct": cmp["lump"]["return_pct"]}
        kw = {"ma_window": ma} if strategy == "ma" else {"rsi_window": rsi}
        r = backtest.run_backtest(seg, amount, **common, **kw)
        base = backtest.run_backtest(seg, amount, **common)
        return {"return_pct": r["return_pct"], "mdd_pct": r["max_drawdown_pct"],
                "bench_pct": base["return_pct"]}

    axis = [c["date"] for c in rows]
    wf = validation.walk_forward(run_window, axis, horizon_days=wf_horizon * 21,
                                 step_days=wf_step * 21, warmup_days=0)
    return wf["agg"]


@mcp.tool()
def dual_momentum(assets: str, lookback: int = 12, count: int = 1500,
                  capital: float = 1_000_000) -> dict:
    """듀얼 모멘텀 백테스트(월별 로테이션 + 절대모멘텀 회피).

    assets: '종목:자산클래스' 콤마구분 (예: '005930:kr_stock,AAPL:us_stock,069500:kr_etf')
    반환: 수익률·CAGR·MDD·전환횟수 + 벤치마크(동일비중 단순보유) 비교.
    """
    acs = {}
    for part in assets.split(","):
        sym, _, ac = part.partition(":")
        acs[sym.strip()] = ac.strip()
    cbs = {s: get_candles_cached(_c(), s, interval="1d", count=count) for s in acs}
    fx_series = None
    fx = 1350.0
    if any(a == "us_stock" for a in acs.values()):
        try:
            fx = _c().get_exchange_rate("USD", "KRW")
        except Exception:
            pass
        dates = sorted({c["date"] for rs in cbs.values() for c in rs})
        if dates:
            fx_series = fx_mod.build_fx_series(_c(), dates[0], dates[-1])
    r = momentum.run_dual_momentum(
        cbs, acs, lookback_days=lookback * 21, initial_capital=capital,
        fx=fx, fx_series=fx_series)
    return {k: r.get(k) for k in ["period", "return_pct", "cagr_pct",
            "max_drawdown_pct", "switches", "final_holding", "benchmark",
            "us_capgains_tax", "net_return_pct", "net_cagr_pct"]}


@mcp.tool()
def current_signals(symbols: str, ma: int = 20, rsi: int = 14,
                    rsi_threshold: float = 30.0) -> list[dict]:
    """오늘 기준 종목별 이평선/RSI 매수 신호. symbols: 콤마구분(예: 'VOO,AAPL,005930')."""
    syms = [s.strip() for s in symbols.split(",") if s.strip()]
    return signals.evaluate_signals(_c(), syms, ma_window=ma,
                                    rsi_window=rsi, rsi_threshold=rsi_threshold)


@mcp.tool()
def bull_bear_evidence(symbol: str, count: int = 300) -> dict:
    """강세(bull)/약세(bear) 논거 구성용 '객관적 근거 묶음'을 반환.

    이 도구는 의견을 내지 않는다 — 추세·이평선 위치·RSI·기간별 모멘텀·52주 고저·
    낙폭·변동성 등 **숫자만** 제공한다. 호출한 쪽(Claude)이 이 숫자로 bull/bear
    양쪽 논리를 직접 구성하면 된다. (엔진=숫자, LLM=서술)
    """
    rows = _candles(symbol, count, None, None)
    if not rows:
        return {"error": "캔들 데이터 없음"}
    closes = [c["close"] for c in rows]
    i = len(closes) - 1
    price = closes[i]

    def ret(n):  # n거래일 전 대비 수익률(%)
        return round((price / closes[i - n] - 1) * 100, 2) if i - n >= 0 and closes[i - n] > 0 else None

    def vs_sma(w):  # 종가의 이평선 대비 위치(%)
        m = backtest._sma(closes, i, w)
        return round((price / m - 1) * 100, 2) if m else None

    win = closes[max(0, i - 251):i + 1]              # 최근 ~52주
    hi, lo = max(win), min(win)
    daily = [closes[k] / closes[k - 1] - 1 for k in range(max(1, i - 251), i + 1) if closes[k - 1] > 0]
    vol = statistics.pstdev(daily) * math.sqrt(252) * 100 if len(daily) > 1 else None
    peak = mdd = 0.0
    for v in win:
        peak = max(peak, v)
        if peak > 0:
            mdd = max(mdd, (peak - v) / peak)
    rsi = backtest._rsi(closes, i, 14)

    return {
        "symbol": symbol, "as_of": rows[-1]["date"], "price": price,
        "vs_ma20_pct": vs_sma(20), "vs_ma60_pct": vs_sma(60), "vs_ma120_pct": vs_sma(120),
        "rsi14": round(rsi, 1) if rsi is not None else None,
        "return_1m_pct": ret(21), "return_3m_pct": ret(63),
        "return_6m_pct": ret(126), "return_12m_pct": ret(252),
        "high_52w": hi, "low_52w": lo,
        "from_52w_high_pct": round((price / hi - 1) * 100, 2) if hi else None,
        "from_52w_low_pct": round((price / lo - 1) * 100, 2) if lo else None,
        "max_drawdown_52w_pct": round(mdd * 100, 2),
        "annualized_vol_pct": round(vol, 1) if vol is not None else None,
    }



# ------------------------------------------------------- 내 계좌(실측) 조회
@mcp.tool()
def my_holdings() -> dict:
    """내 토스 계좌의 현재 보유 종목·비중·손익 (USD는 현재 환율로 KRW 환산 통합).

    읽기 전용. 종목별 평가액/평단/손익률/비중과 통화 노출 비중을 함께 반환한다.
    백테스트 도구에 넣을 심볼을 직접 타이핑하지 않고 여기서 실제 보유를 가져다 쓰면 된다.
    """
    return portfolio.snapshot(_c())


@mcp.tool()
def my_trades(symbol: str = "", start: str = "", end: str = "",
              detail: bool = False, max_detail: int = 200) -> dict:
    """내 실제 체결 내역(주문 이력). 기본은 종목별 집계.

    symbol: 특정 종목만 (미지정 시 전체). start/end: 'YYYY-MM-DD' 주문일 기준.
    detail=True면 개별 체결 목록도 함께 반환(최근 max_detail건, 기본 200).

    체결단가·수수료·세금이 실측이라 백테스트의 비용 가정을 대조하는 데 쓸 수 있다.
    """
    rows = portfolio.trades(_c(), symbol=symbol or None,
                            start=start or None, end=end or None)
    out = portfolio.trade_summary(rows)
    if detail:
        out["trades"] = rows[-max_detail:]
        out["detail_truncated"] = len(rows) > max_detail
    return out


def main():
    mcp.run()


if __name__ == "__main__":
    main()
