"""신호 성과 추적 (이벤트 스터디) — 매수 신호의 예측력 검증.

질문: "이 신호가 뜬 날 이후 주가가 (아무 날보다) 더 오르는가?"

방법: 과거 일봉에서 신호(이평선 아래 / RSI 과매도)가 떴던 모든 날을 찾아,
N일 후 수익률(forward return)을 집계한다. '신호일'의 평균 수익률을 '전체일'
평균(baseline)과 비교해 edge(초과분)를 본다. edge>0이면 신호에 예측력이 있다고 본다.

한계: 신호일이 연속되면 forward window가 겹쳐 자기상관 발생(과대평가 가능).
값은 절대 진리가 아니라 '경향' 지표. (docs/VALIDATION.md 참고)
"""

from __future__ import annotations

import statistics

from .backtest import _sma, _rsi

DEFAULT_HORIZONS = [5, 20, 60]


def _fwd_returns(closes, idxs, h):
    out = []
    n = len(closes)
    for i in idxs:
        if i + h < n and closes[i] > 0:
            out.append(closes[i + h] / closes[i] - 1)
    return out


def _signal_days(closes, kind, ma_window, rsi_window, rsi_threshold):
    """신호가 뜬 인덱스 목록과 평가 가능한 전체(baseline) 인덱스 목록."""
    warmup = ma_window if kind == "ma" else rsi_window
    sig, base = [], []
    for i in range(warmup, len(closes)):
        base.append(i)
        if kind == "ma":
            ma = _sma(closes, i, ma_window)
            if ma is not None and closes[i] < ma:
                sig.append(i)
        else:
            r = _rsi(closes, i, rsi_window)
            if r is not None and r < rsi_threshold:
                sig.append(i)
    return sig, base


def _agg(closes, sig, base, horizons):
    per = {}
    for h in horizons:
        sr = _fwd_returns(closes, sig, h)
        br = _fwd_returns(closes, base, h)
        if not sr or not br:
            continue
        s_mean = statistics.mean(sr)
        b_mean = statistics.mean(br)
        per[h] = {
            "n": len(sr),
            "signal_mean": s_mean * 100,
            "signal_median": statistics.median(sr) * 100,
            "signal_hit": sum(1 for x in sr if x > 0) / len(sr) * 100,
            "base_mean": b_mean * 100,
            "edge": (s_mean - b_mean) * 100,
        }
    return per


def evaluate_efficacy(candles, *, ma_window=20, rsi_window=14,
                      rsi_threshold=30.0, horizons=None) -> dict:
    horizons = horizons or DEFAULT_HORIZONS
    closes = [c["close"] for c in candles]
    out = {"period": (candles[0]["date"], candles[-1]["date"]) if candles else (None, None),
           "horizons": horizons}
    for kind in ("ma", "rsi"):
        sig, base = _signal_days(closes, kind, ma_window, rsi_window, rsi_threshold)
        out[kind] = {"signal_count": len(sig), "base_count": len(base),
                     "per_horizon": _agg(closes, sig, base, horizons)}
    return out


def format_efficacy(symbol, res, ma_window, rsi_window, rsi_threshold) -> str:
    p0, p1 = res["period"]
    lines = [
        f"🎯 신호 성과 추적 — {symbol}  ({p0} ~ {p1})",
        f"이평선: 종가<{ma_window}일선 | RSI<{rsi_threshold:g}({rsi_window})",
        "═" * 60,
    ]
    labels = {"ma": f"이평선({ma_window})", "rsi": f"RSI<{rsi_threshold:g}"}
    for kind in ("ma", "rsi"):
        d = res[kind]
        lines.append(f"[{labels[kind]}]  신호 {d['signal_count']}회 / 전체 {d['base_count']}일")
        if not d["per_horizon"]:
            lines.append("  (데이터 부족)")
            continue
        lines.append(f"  {'기간':>5}{'신호평균%':>11}{'전체평균%':>11}{'edge%p':>9}{'승률%':>8}")
        for h, m in d["per_horizon"].items():
            mark = "✅" if m["edge"] > 0 else "·"
            lines.append(f"  {h:>4}일{m['signal_mean']:>11.2f}{m['base_mean']:>11.2f}"
                         f"{m['edge']:>+9.2f}{m['signal_hit']:>7.0f} {mark}")
    lines.append("═" * 60)
    lines.append("edge>0 = 신호 이후 수익률이 평소보다 높음(예측력 있음). 겹치는 창 자기상관 주의.")
    return "\n".join(lines)
