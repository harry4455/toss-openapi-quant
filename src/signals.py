"""전략 신호 알림 (드라이런/읽기 전용).

DCA 바스켓 종목에 대해 '오늘' 기준 두 전략의 매수 신호를 계산해 알린다:
  - 이평선 DCA: 종가 < N일 이동평균 → BUY
  - RSI 과매도 DCA: RSI(N) < 임계치 → BUY

주문은 절대 내지 않는다. "지금 사라/기다려라" 신호만 텔레그램/콘솔로 보낸다.
검증(walk-forward)에서 이평선·RSI 조건부 DCA가 일반 DCA를 가장 꾸준히 상회했기에
이 둘을 신호로 채택했다. (docs/STRATEGIES.md 참고)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from datetime import date

from .cache import get_candles_cached
from .backtest import _sma, _rsi

logger = logging.getLogger("toss.signals")


def log_signals(path, rows: list[dict]) -> None:
    """신호 이력을 JSONL로 append (나중에 신호 성과 추적용)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    rec = {"date": date.today().isoformat(), "signals": [
        {"symbol": r["symbol"], "price": r.get("price"),
         "ma_buy": r.get("ma_buy"), "rsi_buy": r.get("rsi_buy"),
         "rsi": r.get("rsi")}
        for r in rows if not r.get("error")]}
    with p.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def evaluate_signals(client, symbols: list[str], *,
                     ma_window: int = 20, rsi_window: int = 14,
                     rsi_threshold: float = 30.0) -> list[dict]:
    """종목별 현재 이평선/RSI 신호 계산. 캔들은 캐시 사용."""
    need = max(ma_window, rsi_window) + 10
    rows = []
    for sym in symbols:
        candles = get_candles_cached(client, sym, interval="1d", count=need)
        if not candles:
            rows.append({"symbol": sym, "error": "캔들 없음"})
            continue
        closes = [c["close"] for c in candles]
        i = len(closes) - 1
        price = closes[i]
        ma = _sma(closes, i, ma_window)
        rsi = _rsi(closes, i, rsi_window)
        rows.append({
            "symbol": sym, "price": price,
            "ma": ma, "ma_buy": (ma is not None and price < ma),
            "rsi": rsi, "rsi_buy": (rsi is not None and rsi < rsi_threshold),
        })
    return rows


def format_signals(rows: list[dict], ma_window: int, rsi_window: int,
                   rsi_threshold: float) -> str:
    lines = [
        "📡 *전략 신호* (드라이런 · 주문 안 함)",
        f"이평선 DCA: 종가<{ma_window}일선 | RSI DCA: RSI({rsi_window})<{rsi_threshold:g}",
        "─" * 32,
    ]
    any_buy = False
    for r in rows:
        sym = r["symbol"]
        if r.get("error"):
            lines.append(f"`{sym}` ⚠️ {r['error']}")
            continue
        ma_tag = "🟢BUY" if r["ma_buy"] else "⚪대기"
        rsi_tag = "🟢BUY" if r["rsi_buy"] else "⚪대기"
        if r["ma_buy"] or r["rsi_buy"]:
            any_buy = True
        ma_s = f"{r['ma']:,.0f}" if r["ma"] is not None else "-"
        rsi_s = f"{r['rsi']:.1f}" if r["rsi"] is not None else "-"
        lines.append(
            f"`{sym}` 현재가 {r['price']:,.0f}\n"
            f"   이평선 {ma_tag} (MA {ma_s}) | RSI {rsi_tag} ({rsi_s})"
        )
    lines.append("─" * 32)
    lines.append("🟢 매수 신호 있음 — 검토해보세요" if any_buy else "오늘은 두 전략 모두 대기 신호")
    return "\n".join(lines)
