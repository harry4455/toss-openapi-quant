"""진입점 — 읽기 전용 전략 신호 + DCA 드라이런.

단순 가격/보유/환율 알림은 토스 앱 네이티브 기능과 중복이라 제거했다.
여기 남은 건 토스가 제공하지 않는 것: 계산된 전략 신호와 DCA 시뮬레이션.

사용법:
  python -m src.main --signals    # 이평선/RSI 전략 신호 1회 (드라이런, 주문 없음)
  python -m src.main --dca-once   # DCA 1회 실행 (기본 드라이런)
"""

from __future__ import annotations

import os
import sys
import logging
import argparse
from pathlib import Path

import yaml
from dotenv import load_dotenv

from .toss_client import TossClient
from .notifier import TelegramNotifier, ConsoleNotifier
from .state import State
from .cache import get_candles_cached
from . import dca as dca_engine
from . import signals as signals_engine
from . import reflection as reflection_engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("toss.main")

ROOT = Path(__file__).resolve().parent.parent


def load_config() -> dict:
    return yaml.safe_load((ROOT / "config.yaml").read_text())


def _is_set(value: str | None) -> bool:
    """빈값이거나 .env.example의 placeholder('your_...')면 미설정으로 본다."""
    return bool(value) and not value.startswith("your_")


def build_notifier():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if _is_set(token) and _is_set(chat_id):
        return TelegramNotifier(token, chat_id)
    logger.warning("TELEGRAM_* 미설정 → 콘솔 폴백 모드")
    return ConsoleNotifier()


def resolve_account_seq(client: TossClient, cfg: dict) -> str:
    """설정에 account_seq가 있으면 사용, 없으면 첫 계좌 자동 선택."""
    configured = cfg.get("account_seq")
    if configured:
        return str(configured)
    accounts = client.list_accounts()
    if not accounts:
        raise RuntimeError("연동된 계좌가 없습니다.")
    seq = str(accounts[0]["accountSeq"])
    logger.info("account_seq 미지정 → 첫 계좌 사용: %s", seq)
    return seq


def run_signals(client, notifier, cfg):
    """이평선/RSI 전략 신호 알림 (읽기 전용, 주문 없음)."""
    sig_cfg = cfg.get("signals", {})
    ma_window = int(sig_cfg.get("ma_window", 20))
    rsi_window = int(sig_cfg.get("rsi_window", 14))
    rsi_threshold = float(sig_cfg.get("rsi_threshold", 30))

    # 신호 종목 = signals.symbols 우선, 없으면 dca 바스켓 종목 재사용
    symbols = list(sig_cfg.get("symbols", []))
    if not symbols:
        for b in cfg.get("dca", {}).get("baskets", []):
            symbols += [it["symbol"] for it in b.get("items", [])]
    symbols = list(dict.fromkeys(symbols))  # 중복 제거, 순서 유지
    if not symbols:
        logger.warning("신호 대상 종목이 없습니다 (config의 signals.symbols 또는 dca.baskets).")
        return

    rows = signals_engine.evaluate_signals(
        client, symbols, ma_window=ma_window,
        rsi_window=rsi_window, rsi_threshold=rsi_threshold)
    msg = signals_engine.format_signals(rows, ma_window, rsi_window, rsi_threshold)
    logger.info("신호 평가 완료 (%d종목)", len(symbols))
    signals_engine.log_signals(ROOT / "data" / "signal_log.jsonl", rows)
    notifier.send(msg)


def run_reflection(client, notifier, cfg, horizon_days=20):
    """누적 신호 로그를 실제 주가와 대조해 사후 성과 회고."""
    records = reflection_engine.load_signal_log(ROOT / "data" / "signal_log.jsonl")
    if not records:
        notifier.send("🔁 신호 로그가 비어 있습니다 (--signals를 며칠 돌린 뒤 회고 가능).")
        return
    symbols = {s["symbol"] for r in records for s in r.get("signals", [])}
    cbs = {sym: get_candles_cached(client, sym, interval="1d", count=400)
           for sym in symbols}
    res = reflection_engine.evaluate_reflection(records, cbs, horizon_days=horizon_days)
    logger.info("회고 완료: 누적 %d신호", res["total_signals"])
    notifier.send(reflection_engine.format_reflection(res))


def main():
    parser = argparse.ArgumentParser(
        description="토스 전략 신호 / DCA 드라이런 (읽기 전용, 주문 없음)")
    parser.add_argument("--signals", action="store_true",
                        help="이평선/RSI 전략 신호 1회 (드라이런)")
    parser.add_argument("--dca-once", action="store_true",
                        help="DCA 1회 실행 (기본 드라이런). cron/스케줄러용")
    parser.add_argument("--reflect", action="store_true",
                        help="누적 신호 로그를 실제 주가와 대조해 사후 성과 회고")
    parser.add_argument("--reflect-horizon", type=int, default=20,
                        help="회고 기준 거래일 수 (기본 20)")
    args = parser.parse_args()

    if not (args.signals or args.dca_once or args.reflect):
        parser.print_help()
        sys.exit(0)

    load_dotenv(ROOT / ".env")
    cfg = load_config()

    client_id = os.getenv("TOSS_CLIENT_ID")
    client_secret = os.getenv("TOSS_CLIENT_SECRET")
    if not client_id or not client_secret:
        logger.error("TOSS_CLIENT_ID / TOSS_CLIENT_SECRET 환경변수가 필요합니다 (.env 참고)")
        sys.exit(1)

    client = TossClient(client_id, client_secret)
    notifier = build_notifier()

    # 신호/회고 모드는 시세만 사용 → 계좌 조회 불필요
    if args.signals:
        run_signals(client, notifier, cfg)
        return

    if args.reflect:
        run_reflection(client, notifier, cfg, horizon_days=args.reflect_horizon)
        return

    if args.dca_once:
        state = State(ROOT / "state.json")
        account_seq = resolve_account_seq(client, cfg)
        dca_engine.run_dca(client, notifier, state, cfg, account_seq)


if __name__ == "__main__":
    main()
