"""신호 성과 회고(reflection) — 실제로 발생한 신호가 맞았는지 사후 평가.

`data/signal_log.jsonl`에 쌓인 '실제 발생 신호'를 이후 실제 주가와 대조한다.
(과거 캔들로 신호를 재현하는 signal_eval과 달리, 여기선 봇이 실제로 찍은 신호를 본다.)

신호일 가격 대비 N거래일 뒤 종가 수익률을 집계 → 적중률·평균수익·대기(미경과)건수.
"""

from __future__ import annotations

import json
import bisect
import logging
from pathlib import Path

logger = logging.getLogger("toss.reflection")


def load_signal_log(path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def evaluate_reflection(records: list[dict], candles_by_symbol: dict[str, list[dict]],
                        horizon_days: int = 20) -> dict:
    """순수 평가: 로그 신호 → N거래일 뒤 수익률 집계.

    records: signal_log 레코드 [{date, signals:[{symbol, price, ma_buy, rsi_buy}]}]
    candles_by_symbol: {symbol: [{date, close} ...]} (오래된→최신)
    반환: {ma:{evaluated,hits,avg_return_pct,pending}, rsi:{...}, horizon_days, total_signals}
    """
    agg = {k: {"returns": [], "pending": 0} for k in ("ma", "rsi")}
    total = 0
    for rec in records:
        rec_date = rec.get("date")
        for sig in rec.get("signals", []):
            sym = sig.get("symbol")
            entry = sig.get("price")
            candles = candles_by_symbol.get(sym)
            if not entry or not candles:
                continue
            dates = [c["date"] for c in candles]
            idx = bisect.bisect_left(dates, rec_date)   # 신호일 이상 첫 거래일
            if idx >= len(candles):
                continue
            fwd = idx + horizon_days
            for kind in ("ma", "rsi"):
                if not sig.get(f"{kind}_buy"):
                    continue
                total += 1
                if fwd < len(candles):
                    ret = candles[fwd]["close"] / entry - 1
                    agg[kind]["returns"].append(ret)
                else:
                    agg[kind]["pending"] += 1   # 아직 N일 안 지남

    out = {"horizon_days": horizon_days, "total_signals": total}
    for kind, d in agg.items():
        rets = d["returns"]
        out[kind] = {
            "evaluated": len(rets),
            "pending": d["pending"],
            "hit_rate_pct": (sum(1 for x in rets if x > 0) / len(rets) * 100) if rets else None,
            "avg_return_pct": (sum(rets) / len(rets) * 100) if rets else None,
        }
    return out


def format_reflection(res: dict) -> str:
    lines = [
        f"🔁 *신호 성과 회고* (실제 발생 신호, +{res['horizon_days']}거래일 기준)",
        f"누적 신호 {res['total_signals']}건",
        "─" * 32,
    ]
    labels = {"ma": "이평선", "rsi": "RSI"}
    for kind in ("ma", "rsi"):
        d = res[kind]
        if d["evaluated"] == 0:
            lines.append(f"{labels[kind]}: 평가 가능 0건 (대기 {d['pending']}건)")
            continue
        lines.append(
            f"{labels[kind]}: 평가 {d['evaluated']}건 | "
            f"적중 {d['hit_rate_pct']:.0f}% | 평균 {d['avg_return_pct']:+.2f}% | 대기 {d['pending']}건")
    lines.append("─" * 32)
    lines.append("적중률·평균이 낮으면 그 신호의 임계치/전략을 재검토.")
    return "\n".join(lines)
