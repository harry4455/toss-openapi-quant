"""전략 무관(strategy-agnostic) 검증 도구 — walk-forward + 파라미터 sweep.

전략별로 'run_window' / 'run_full' 클로저만 제공하면 DCA·이평선DCA·모멘텀 등
어떤 전략이든 동일한 틀로 검증한다. 개념 설명은 docs/VALIDATION.md 참고.
"""

from __future__ import annotations

import statistics


def walk_forward(run_window, axis: list[str], *,
                 horizon_days: int, step_days: int,
                 warmup_days: int = 0) -> dict:
    """테스트 창을 시간순으로 굴리며 성과 분포 측정.

    run_window(data_start, test_start, end) -> {return_pct, mdd_pct, bench_pct?}
      data_start: 워밍업 포함 데이터 시작일
      test_start: 실제 성과 측정 시작일
      end: 창 종료일
    """
    windows = []
    i = warmup_days
    n = len(axis)
    while i + horizon_days <= n:
        data_start = axis[i - warmup_days]
        test_start = axis[i]
        end = axis[i + horizon_days - 1]
        res = run_window(data_start, test_start, end)
        if res:
            bench = res.get("bench_pct")
            windows.append({
                "start": test_start, "end": end,
                "return_pct": res["return_pct"],
                "mdd_pct": res["mdd_pct"],
                "bench_pct": bench,
                "beat_bench": (bench is not None and res["return_pct"] > bench),
            })
        i += step_days

    rets = [w["return_pct"] for w in windows]
    agg = {}
    if rets:
        has_bench = any(w["bench_pct"] is not None for w in windows)
        agg = {
            "n": len(windows),
            "mean": statistics.mean(rets),
            "median": statistics.median(rets),
            "stdev": statistics.pstdev(rets) if len(rets) > 1 else 0.0,
            "min": min(rets), "max": max(rets),
            "pct_positive": sum(1 for x in rets if x > 0) / len(rets) * 100,
            "mean_mdd": statistics.mean(w["mdd_pct"] for w in windows),
            "pct_beat_bench": (sum(1 for w in windows if w["beat_bench"]) / len(windows) * 100)
                              if has_bench else None,
        }
    return {"windows": windows, "agg": agg}


def param_sweep(run_full, param_values, label: str) -> list[dict]:
    """파라미터 값별 전체구간 1회 실행 → [{param, return_pct, mdd_pct, bench_pct?}]."""
    rows = []
    for p in param_values:
        r = run_full(p)
        rows.append({"param": p, **r})
    return rows


def format_walk_forward(wf: dict, title: str, *,
                        bench_label: str = "벤치", show_windows: bool = True) -> str:
    a = wf["agg"]
    if not a:
        return f"{title}\n  데이터 부족(창 생성 불가). count↑ 또는 horizon↓ 필요."
    lines = [title, "═" * 60]
    if show_windows:
        lines.append(f"{'창 시작':<12}{'수익률%':>11}{'MDD%':>9}{f'{bench_label}%':>10}{'승':>5}")
        lines.append("─" * 60)
        for w in wf["windows"]:
            mark = "✅" if w["beat_bench"] else "·"
            b = f"{w['bench_pct']:.1f}" if w["bench_pct"] is not None else "-"
            lines.append(f"{w['start']:<12}{w['return_pct']:>11.2f}"
                         f"{-w['mdd_pct']:>9.2f}{b:>10}{mark:>5}")
        lines.append("═" * 60)
    lines += [
        f"창 개수      : {a['n']}개",
        f"평균 수익률  : {a['mean']:+.2f}%   (중앙값 {a['median']:+.2f}%)",
        f"표준편차     : {a['stdev']:.2f}%p",
        f"최저~최고    : {a['min']:+.2f}% ~ {a['max']:+.2f}%",
        f"플러스 비율  : {a['pct_positive']:.0f}%",
    ]
    if a["pct_beat_bench"] is not None:
        lines.append(f"{bench_label} 초과비율: {a['pct_beat_bench']:.0f}%")
    lines.append(f"평균 MDD     : -{a['mean_mdd']:.2f}%")
    return "\n".join(lines)


def format_sweep(rows: list[dict], title: str, param_label: str,
                 param_fmt=str, bench_label: str = "벤치대비") -> str:
    has_bench = any(r.get("bench_pct") is not None for r in rows)
    lines = [title, "═" * 56,
             f"{param_label:>10}{'수익률%':>11}{'MDD%':>9}"
             + (f"{bench_label+'%p':>12}" if has_bench else "")]
    lines.append("─" * 56)
    for r in rows:
        diff = ""
        if has_bench and r.get("bench_pct") is not None:
            diff = f"{r['return_pct'] - r['bench_pct']:>+12.2f}"
        lines.append(f"{param_fmt(r['param']):>10}{r['return_pct']:>11.2f}"
                     f"{-r['mdd_pct']:>9.2f}{diff}")
    lines.append("═" * 56)
    spread = max(r["return_pct"] for r in rows) - min(r["return_pct"] for r in rows)
    tag = "(민감 = 과최적화 주의)" if spread > 100 else "(비교적 안정)"
    lines.append(f"→ 수익률 편차(최대-최소): {spread:.2f}%p {tag}")
    return "\n".join(lines)
