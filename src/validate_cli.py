"""전략 검증 통합 CLI — DCA / 이평선DCA / 듀얼모멘텀에 walk-forward·sweep 적용.

예:
  # DCA walk-forward (창 12개월, 3개월 스텝)
  python -m src.validate_cli --strategy dca --symbol 005930 --asset-class kr_stock \\
      --amount 100000 --count 3000 --wf-horizon 12 --wf-step 3
  # 이평선 DCA: walk-forward + MA 윈도우 민감도
  python -m src.validate_cli --strategy ma --symbol 005930 --asset-class kr_stock \\
      --amount 100000 --ma 20 --sweep-ma 10,20,60,120 --count 3000
  # 듀얼 모멘텀: walk-forward + lookback 민감도
  python -m src.validate_cli --strategy momentum \\
      --assets 005930:kr_stock,069500:kr_etf,AAPL:us_stock,VOO:us_stock \\
      --lookback 12 --sweep-lookback 3,6,9,12 --count 3000
"""

from __future__ import annotations

import os
import sys
import argparse
import logging

from dotenv import load_dotenv

from .toss_client import TossClient
from .cache import get_candles_cached
from . import backtest, fees, momentum, validation, fx as fx_module

logging.basicConfig(level=logging.WARNING)
WEEKDAY_MAP = {"월": 0, "화": 1, "수": 2, "목": 3, "금": 4}


def _slice(candles, start, end):
    return [c for c in candles if start <= c["date"] <= end]


def main():
    ap = argparse.ArgumentParser(description="전략 검증(walk-forward + sweep)")
    ap.add_argument("--strategy", required=True, choices=["dca", "ma", "rsi", "momentum"])
    ap.add_argument("--symbol", help="dca/ma용 종목")
    ap.add_argument("--asset-class", choices=list(fees.PROFILES), help="dca/ma용")
    ap.add_argument("--assets", help="momentum용 종목:클래스 콤마구분")
    ap.add_argument("--amount", type=float, default=100000)
    ap.add_argument("--weekday", default="월", choices=list(WEEKDAY_MAP))
    ap.add_argument("--ma", type=int, default=20, help="ma 전략 기본 이평선")
    ap.add_argument("--rsi", type=int, default=14, help="rsi 전략 기본 윈도우")
    ap.add_argument("--rsi-threshold", type=float, default=30.0, help="rsi 과매도 임계치")
    ap.add_argument("--sweep-rsi", help="rsi 임계치 민감도: 콤마구분 (예: 25,30,35,40)")
    ap.add_argument("--lookback", type=int, default=12, help="momentum 기본 lookback(개월)")
    ap.add_argument("--capital", type=float, default=1_000_000, help="momentum 초기자본")
    ap.add_argument("--count", type=int, default=3000)
    ap.add_argument("--wf-horizon", type=int, default=12, help="walk-forward 창(개월)")
    ap.add_argument("--wf-step", type=int, default=3, help="walk-forward 스텝(개월)")
    ap.add_argument("--sweep-ma", help="ma 윈도우 민감도: 콤마구분 (예: 10,20,60,120)")
    ap.add_argument("--sweep-lookback", help="momentum lookback 민감도: 콤마구분(개월)")
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()

    load_dotenv()
    cid, csec = os.getenv("TOSS_CLIENT_ID"), os.getenv("TOSS_CLIENT_SECRET")
    if not cid or not csec:
        print("TOSS_CLIENT_ID / TOSS_CLIENT_SECRET 가 필요합니다 (.env)")
        sys.exit(1)
    client = TossClient(cid, csec)
    fetch = lambda s: get_candles_cached(client, s, interval="1d",
                                         count=args.count, use_cache=not args.no_cache)

    if args.strategy in ("dca", "ma", "rsi"):
        if not args.symbol or not args.asset_class:
            print("dca/ma/rsi 전략엔 --symbol 과 --asset-class 가 필요합니다.")
            sys.exit(1)
        _validate_single(args, fetch)
    else:
        if not args.assets:
            print("momentum 전략엔 --assets 가 필요합니다.")
            sys.exit(1)
        _validate_momentum(args, fetch, client)


def _validate_single(args, fetch):
    candles = fetch(args.symbol)
    prof = fees.get_profile(args.asset_class)
    us = args.asset_class == "us_stock"
    wd = WEEKDAY_MAP[args.weekday]
    common = dict(freq="weekly", weekday=wd, allow_fractional=us, fee_profile=prof)

    if args.strategy == "dca":
        def run_window(_ds, ts, end):
            seg = _slice(candles, ts, end)
            if len(seg) < 5:
                return None
            cmp = backtest.compare_lumpsum_dca(seg, args.amount, **common)
            return {"return_pct": cmp["dca"]["return_pct"],
                    "mdd_pct": cmp["dca"]["max_drawdown_pct"],
                    "bench_pct": cmp["lump"]["return_pct"]}  # 벤치=거치식
        bench_label = "거치식"
        title = f"🚶 DCA Walk-Forward — {args.symbol} (창 {args.wf_horizon}개월)"
    elif args.strategy == "ma":  # 벤치=일반 DCA (이평선 필터가 값을 더하는가?)
        def run_window(_ds, ts, end):
            seg = _slice(candles, ts, end)
            if len(seg) < args.ma + 5:
                return None
            r_ma = backtest.run_backtest(seg, args.amount, ma_window=args.ma, **common)
            r_dca = backtest.run_backtest(seg, args.amount, **common)
            return {"return_pct": r_ma["return_pct"],
                    "mdd_pct": r_ma["max_drawdown_pct"],
                    "bench_pct": r_dca["return_pct"]}  # 벤치=일반 DCA
        bench_label = "일반DCA"
        title = f"🚶 이평선({args.ma}일) DCA Walk-Forward — {args.symbol} (창 {args.wf_horizon}개월)"
    else:  # rsi: 벤치=일반 DCA (RSI 과매도 필터가 값을 더하는가?)
        def run_window(_ds, ts, end):
            seg = _slice(candles, ts, end)
            if len(seg) < args.rsi + 5:
                return None
            r_rsi = backtest.run_backtest(seg, args.amount, rsi_window=args.rsi,
                                          rsi_threshold=args.rsi_threshold, **common)
            r_dca = backtest.run_backtest(seg, args.amount, **common)
            return {"return_pct": r_rsi["return_pct"],
                    "mdd_pct": r_rsi["max_drawdown_pct"],
                    "bench_pct": r_dca["return_pct"]}  # 벤치=일반 DCA
        bench_label = "일반DCA"
        title = (f"🚶 RSI({args.rsi}<{args.rsi_threshold:g}) DCA Walk-Forward "
                 f"— {args.symbol} (창 {args.wf_horizon}개월)")

    axis = [c["date"] for c in candles]
    wf = validation.walk_forward(run_window, axis,
                                 horizon_days=args.wf_horizon * 21,
                                 step_days=args.wf_step * 21, warmup_days=0)
    print(validation.format_walk_forward(wf, title, bench_label=bench_label,
                                         show_windows=False))
    print(f"  (벤치마크 = {bench_label}. ‘승’/초과비율은 이 벤치 대비)")

    # MA 윈도우 민감도
    if args.strategy == "ma" and args.sweep_ma:
        mas = [int(x) for x in args.sweep_ma.split(",")]

        def run_full(maw):
            r_ma = backtest.run_backtest(candles, args.amount, ma_window=maw, **common)
            r_dca = backtest.run_backtest(candles, args.amount, **common)
            return {"return_pct": r_ma["return_pct"],
                    "mdd_pct": r_ma["max_drawdown_pct"],
                    "bench_pct": r_dca["return_pct"]}
        rows = validation.param_sweep(run_full, mas, "ma")
        print()
        print(validation.format_sweep(rows, f"📐 이평선 윈도우 민감도 — {args.symbol}",
                                      "이평선", lambda x: f"{x}일", "vs일반DCA"))

    # RSI 임계치 민감도
    if args.strategy == "rsi" and args.sweep_rsi:
        ths = [float(x) for x in args.sweep_rsi.split(",")]

        def run_full(th):
            r_rsi = backtest.run_backtest(candles, args.amount, rsi_window=args.rsi,
                                          rsi_threshold=th, **common)
            r_dca = backtest.run_backtest(candles, args.amount, **common)
            return {"return_pct": r_rsi["return_pct"],
                    "mdd_pct": r_rsi["max_drawdown_pct"],
                    "bench_pct": r_dca["return_pct"]}
        rows = validation.param_sweep(run_full, ths, "rsi")
        print()
        print(validation.format_sweep(rows, f"📐 RSI 임계치 민감도 — {args.symbol}",
                                      "RSI<", lambda x: f"{x:g}", "vs일반DCA"))


def _validate_momentum(args, fetch, client):
    acs = {}
    for part in args.assets.split(","):
        sym, _, ac = part.partition(":")
        acs[sym.strip()] = ac.strip()
    cbs = {s: fetch(s) for s in acs}
    fx = 1350.0
    fx_series = None
    if any(a == "us_stock" for a in acs.values()):
        try:
            fx = client.get_exchange_rate("USD", "KRW")
        except Exception:
            pass
        dates = sorted({c["date"] for rows in cbs.values() for c in rows})
        if dates:
            fx_series = fx_module.build_fx_series(client, dates[0], dates[-1])
    common = dict(initial_capital=args.capital, fx=fx, fx_series=fx_series,
                  absolute_filter=True)

    # 공통 axis (전 종목 날짜 합집합)
    dates = set()
    for rows in cbs.values():
        dates.update(c["date"] for c in rows)
    axis = sorted(dates)

    def run_window(ds, ts, end):
        sub = {s: _slice(cbs[s], ds, end) for s in acs}
        r = momentum.run_dual_momentum(sub, acs, lookback_days=args.lookback * 21, **common)
        return {"return_pct": r["return_pct"], "mdd_pct": r["max_drawdown_pct"],
                "bench_pct": r.get("benchmark", {}).get("return_pct")}

    wf = validation.walk_forward(run_window, axis,
                                 horizon_days=args.wf_horizon * 21,
                                 step_days=args.wf_step * 21,
                                 warmup_days=args.lookback * 21)
    title = f"🚶 듀얼모멘텀 Walk-Forward (lookback {args.lookback}개월, 창 {args.wf_horizon}개월)"
    print(validation.format_walk_forward(wf, title, bench_label="동일비중", show_windows=False))

    if args.sweep_lookback:
        months = [int(x) for x in args.sweep_lookback.split(",")]

        def run_full(m):
            r = momentum.run_dual_momentum(cbs, acs, lookback_days=m * 21, **common)
            return {"return_pct": r["return_pct"], "mdd_pct": r["max_drawdown_pct"],
                    "bench_pct": r.get("benchmark", {}).get("return_pct")}
        rows = validation.param_sweep(run_full, months, "lookback")
        print()
        print(validation.format_sweep(rows, "📐 Lookback 민감도 (전체구간)",
                                      "lookback", lambda x: f"{x}개월", "vs동일비중"))


if __name__ == "__main__":
    main()
