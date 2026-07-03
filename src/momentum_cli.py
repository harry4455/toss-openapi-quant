"""듀얼 모멘텀 백테스트 CLI.

사용 예 (종목:자산클래스 쌍을 콤마로):
  python -m src.momentum_cli --assets 069500:kr_etf,360750:kr_etf,AAPL:us_stock \\
      --lookback 12 --capital 1000000 --count 600
"""

from __future__ import annotations

import os
import sys
import argparse
import logging

from dotenv import load_dotenv

from .toss_client import TossClient
from .cache import get_candles_cached
from . import momentum, fees, validation, fx as fx_mod

logging.basicConfig(level=logging.WARNING)


def _slice(candles, start, end):
    return [c for c in candles if start <= c["date"] <= end]


def parse_assets(spec: str) -> dict[str, str]:
    out = {}
    for part in spec.split(","):
        sym, _, ac = part.partition(":")
        sym, ac = sym.strip(), ac.strip()
        if ac not in fees.PROFILES:
            raise SystemExit(f"잘못된 자산클래스: {ac} (가능: {list(fees.PROFILES)})")
        out[sym] = ac
    return out


def main():
    ap = argparse.ArgumentParser(description="듀얼 모멘텀 백테스트")
    ap.add_argument("--assets", required=True,
                    help="종목:자산클래스 쌍 콤마구분 (예: 069500:kr_etf,AAPL:us_stock)")
    ap.add_argument("--lookback", type=int, default=12, help="모멘텀 기간(개월, 기본 12)")
    ap.add_argument("--capital", type=float, default=1_000_000, help="초기 자본(KRW)")
    ap.add_argument("--count", type=int, default=600, help="가져올 일봉 수")
    ap.add_argument("--no-absolute", action="store_true",
                    help="절대모멘텀(현금회피) 끄기 → 상대모멘텀만")
    ap.add_argument("--safe-asset",
                    help="절대모멘텀 실패 시 회피처 종목(--assets 안에 포함된 심볼). "
                         "지정 시 GEM 방식(safe 모멘텀과 비교)")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--sweep", help="lookback 민감도 분석: 개월 콤마구분 (예: 3,6,9,12)")
    ap.add_argument("--walk-forward", action="store_true",
                    help="walk-forward 분석(테스트 창을 굴리며 성과 분포)")
    ap.add_argument("--wf-horizon", type=int, default=6,
                    help="walk-forward 각 창 길이(개월, 기본 6)")
    ap.add_argument("--wf-step", type=int, default=1,
                    help="walk-forward 창 이동 간격(개월, 기본 1)")
    args = ap.parse_args()

    load_dotenv()
    cid, csec = os.getenv("TOSS_CLIENT_ID"), os.getenv("TOSS_CLIENT_SECRET")
    if not cid or not csec:
        print("TOSS_CLIENT_ID / TOSS_CLIENT_SECRET 가 필요합니다 (.env)")
        sys.exit(1)

    asset_classes = parse_assets(args.assets)
    if args.safe_asset and args.safe_asset not in asset_classes:
        raise SystemExit(f"--safe-asset {args.safe_asset} 는 --assets 안에 포함돼야 합니다.")
    client = TossClient(cid, csec)

    candles_by_symbol = {}
    for sym in asset_classes:
        candles_by_symbol[sym] = get_candles_cached(
            client, sym, interval="1d", count=args.count, use_cache=not args.no_cache)

    fx = 1350.0
    fx_series = None
    if any(ac == "us_stock" for ac in asset_classes.values()):
        try:
            fx = client.get_exchange_rate("USD", "KRW")
        except Exception:
            pass
        # 과거 환율 시계열(있는 구간만, 없으면 fx 상수로 폴백)
        dates = sorted({c["date"] for rows in candles_by_symbol.values() for c in rows})
        if dates:
            fx_series = fx_mod.build_fx_series(client, dates[0], dates[-1])

    common = dict(initial_capital=args.capital, fx=fx, fx_series=fx_series,
                  absolute_filter=not args.no_absolute, safe_asset=args.safe_asset)

    if args.sweep:
        months = [int(x) for x in args.sweep.split(",")]

        def run_full(m):
            r = momentum.run_dual_momentum(
                candles_by_symbol, asset_classes, lookback_days=m * 21, **common)
            return {"return_pct": r["return_pct"], "mdd_pct": r["max_drawdown_pct"],
                    "bench_pct": r.get("benchmark", {}).get("return_pct")}
        rows = validation.param_sweep(run_full, months, "lookback")
        print(validation.format_sweep(rows, "📐 Lookback 민감도 (전체구간)",
                                      "lookback", lambda x: f"{x}개월", "vs동일비중"))
        return

    if args.walk_forward:
        def run_window(ds, ts, end):
            sub = {s: _slice(candles_by_symbol[s], ds, end) for s in asset_classes}
            r = momentum.run_dual_momentum(
                sub, asset_classes, lookback_days=args.lookback * 21, **common)
            return {"return_pct": r["return_pct"], "mdd_pct": r["max_drawdown_pct"],
                    "bench_pct": r.get("benchmark", {}).get("return_pct")}
        axis = sorted({c["date"] for rows in candles_by_symbol.values() for c in rows})
        wf = validation.walk_forward(
            run_window, axis, horizon_days=args.wf_horizon * 21,
            step_days=args.wf_step * 21, warmup_days=args.lookback * 21)
        title = (f"🚶 듀얼모멘텀 Walk-Forward "
                 f"(lookback {args.lookback}개월, 창 {args.wf_horizon}개월)")
        print(validation.format_walk_forward(wf, title, bench_label="동일비중"))
        return

    r = momentum.run_dual_momentum(
        candles_by_symbol, asset_classes,
        lookback_days=args.lookback * 21,   # 개월 → 거래일 근사
        **common,
    )
    print(momentum.format_momentum(list(asset_classes), r))


if __name__ == "__main__":
    main()
