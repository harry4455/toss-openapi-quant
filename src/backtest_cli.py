"""DCA 백테스트 실행 CLI.

사용 예:
  python -m src.backtest_cli --symbol 005930 --amount 100000 --weekday 월 --count 200
  python -m src.backtest_cli --symbol AAPL --amount 100 --freq monthly --us --count 200
"""

from __future__ import annotations

import os
import sys
import argparse
import logging

from dotenv import load_dotenv

from .toss_client import TossClient
from . import backtest, fees
from .cache import get_candles_cached

logging.basicConfig(level=logging.WARNING)

WEEKDAY_MAP = {"월": 0, "화": 1, "수": 2, "목": 3, "금": 4}


def main():
    ap = argparse.ArgumentParser(description="DCA 백테스트")
    ap.add_argument("--symbol", required=True, help="종목 (예: 005930, AAPL)")
    ap.add_argument("--amount", type=float, required=True, help="1회 매수 금액")
    ap.add_argument("--freq", choices=["weekly", "monthly"], default="weekly")
    ap.add_argument("--weekday", default="월", choices=list(WEEKDAY_MAP),
                    help="weekly일 때 매수 요일 (기본 월)")
    ap.add_argument("--count", type=int, default=200, help="가져올 일봉 수 (기본 200)")
    ap.add_argument("--us", action="store_true",
                    help="미국 종목(소수 주 허용, $ 표기)")
    ap.add_argument("--asset-class", choices=list(fees.PROFILES),
                    help="수수료/세금 프로파일 (kr_stock/kr_etf/us_stock). 미지정 시 세전만 계산")
    ap.add_argument("--start", help="백테스트 시작일 YYYY-MM-DD (구간 한정, 예: 하락장)")
    ap.add_argument("--end", help="백테스트 종료일 YYYY-MM-DD")
    ap.add_argument("--slippage", type=float, default=0.0,
                    help="체결 슬리피지(%%, 예 0.1 = 종가보다 0.1%% 불리하게 매수)")
    ap.add_argument("--same-bar-fill", action="store_true",
                    help="조건부 매수를 당일 종가 체결(룩어헤드 허용, 비교용). 기본은 다음날 시가")
    ap.add_argument("--ma", type=int, help="이평선 조건부 매수 윈도우 (예: 20)")
    ap.add_argument("--ma-mode", choices=["below", "above"], default="below",
                    help="below=이평선 아래일 때만 매수(기본), above=위일 때만")
    ap.add_argument("--rsi", type=int, help="RSI 과매도 조건부 매수 윈도우 (예: 14)")
    ap.add_argument("--rsi-threshold", type=float, default=30.0,
                    help="RSI 이 값 미만일 때만 매수 (기본 30)")
    ap.add_argument("--no-cache", action="store_true", help="캔들 캐시 미사용(강제 재조회)")
    ap.add_argument("--vs-lumpsum", action="store_true",
                    help="거치식(일괄) vs 적립식(DCA) 동일 총액 비교")
    args = ap.parse_args()

    load_dotenv()
    cid, csec = os.getenv("TOSS_CLIENT_ID"), os.getenv("TOSS_CLIENT_SECRET")
    if not cid or not csec:
        print("TOSS_CLIENT_ID / TOSS_CLIENT_SECRET 가 필요합니다 (.env)")
        sys.exit(1)

    client = TossClient(cid, csec)
    candles = get_candles_cached(client, args.symbol, interval="1d",
                                 count=args.count, use_cache=not args.no_cache)
    if not candles:
        print("캔들 데이터를 가져오지 못했습니다.")
        sys.exit(1)

    # 날짜 구간 한정 (하락장 등 특정 구간 백테스트)
    if args.start:
        candles = [c for c in candles if c["date"] >= args.start]
    if args.end:
        candles = [c for c in candles if c["date"] <= args.end]
    if not candles:
        print("지정한 날짜 구간에 데이터가 없습니다.")
        sys.exit(1)

    fee_profile = fees.get_profile(args.asset_class) if args.asset_class else None
    fx_krw = None
    if args.asset_class == "us_stock":
        try:
            fx_krw = client.get_exchange_rate("USD", "KRW")  # 양도세 공제 환산용
        except Exception:
            fx_krw = 1350.0  # 조회 실패 시 근사값

    params = {
        "amount": args.amount,
        "freq": args.freq,
        "weekday": WEEKDAY_MAP[args.weekday],
        "us": args.us,
    }
    slippage = args.slippage / 100.0   # % → 분수
    if args.vs_lumpsum:
        cmp = backtest.compare_lumpsum_dca(
            candles, args.amount,
            freq=args.freq, weekday=WEEKDAY_MAP[args.weekday],
            allow_fractional=args.us, fee_profile=fee_profile, fx_krw=fx_krw,
            slippage=slippage,
        )
        print(backtest.format_compare(args.symbol, params, cmp))
        return

    result = backtest.run_backtest(
        candles, args.amount,
        freq=args.freq, weekday=WEEKDAY_MAP[args.weekday],
        allow_fractional=args.us,
        fee_profile=fee_profile, fx_krw=fx_krw, slippage=slippage,
        next_bar_fill=not args.same_bar_fill,
        ma_window=args.ma, ma_mode=args.ma_mode,
        rsi_window=args.rsi, rsi_threshold=args.rsi_threshold,
    )
    print(backtest.format_report(args.symbol, params, result))
    if args.ma:
        print(f"(전략: {args.ma}일 이평선 {args.ma_mode} 조건부 매수)")
    if args.rsi:
        print(f"(전략: RSI {args.rsi} < {args.rsi_threshold:g} 과매도 조건부 매수)")


if __name__ == "__main__":
    main()
