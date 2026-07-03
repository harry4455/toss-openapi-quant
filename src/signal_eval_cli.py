"""신호 성과 추적 CLI.

예:
  python -m src.signal_eval_cli --symbol 005930 --count 3000
  python -m src.signal_eval_cli --symbol AAPL --ma 20 --rsi 14 --rsi-threshold 30 --horizons 5,20,60
"""

from __future__ import annotations

import os
import sys
import argparse
import logging

from dotenv import load_dotenv

from .toss_client import TossClient
from .cache import get_candles_cached
from . import signal_eval

logging.basicConfig(level=logging.WARNING)


def main():
    ap = argparse.ArgumentParser(description="신호 성과 추적(이벤트 스터디)")
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--count", type=int, default=3000)
    ap.add_argument("--ma", type=int, default=20)
    ap.add_argument("--rsi", type=int, default=14)
    ap.add_argument("--rsi-threshold", type=float, default=30.0)
    ap.add_argument("--horizons", default="5,20,60", help="forward 기간(일) 콤마구분")
    ap.add_argument("--no-cache", action="store_true")
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

    horizons = [int(x) for x in args.horizons.split(",")]
    res = signal_eval.evaluate_efficacy(
        candles, ma_window=args.ma, rsi_window=args.rsi,
        rsi_threshold=args.rsi_threshold, horizons=horizons)
    print(signal_eval.format_efficacy(args.symbol, res, args.ma, args.rsi,
                                      args.rsi_threshold))


if __name__ == "__main__":
    main()
