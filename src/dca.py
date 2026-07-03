"""Phase 2 — 정액 분할매수(DCA) 엔진.

설정된 바스켓을 읽어 '이번 회차에 낼 매수 주문'을 계산한다.
- 미국: orderAmount(금액주문, MARKET·정규장 한정) 또는 수량 환산(LIMIT)
- 국내: 금액주문 불가 → 시세로 수량 환산
- 시장가(MARKET) / 지정가(LIMIT) 모두 지원

기본은 드라이런: 주문을 보내지 않고 '샀을 주문'만 알림한다.
실거래(live)는 이중 잠금이 모두 풀렸을 때만 허용한다:
  1) config.yaml 의 dca.dry_run: false
  2) 환경변수 TOSS_ENABLE_LIVE_TRADING=I_UNDERSTAND
"""

from __future__ import annotations

import os
import math
import logging
from datetime import date

logger = logging.getLogger("toss.dca")

LIVE_ENV_FLAG = "TOSS_ENABLE_LIVE_TRADING"
LIVE_ENV_VALUE = "I_UNDERSTAND"


class SkipItem(Exception):
    """이 종목은 이번 회차에서 건너뛴다 (사유 포함)."""


def _client_order_id(market: str, symbol: str, today: str) -> str:
    # 하루 한 종목당 1회 — 같은 날 재실행해도 중복 매수 방지(멱등성 키)
    return f"dca-{today}-{market}-{symbol}"


def _build_order(item: dict, basket: dict, price: float | None) -> dict:
    """바스켓 항목 1개 → 토스 주문 body. 계산만 하고 전송하지 않는다."""
    market = basket["market"].upper()
    order_type = basket.get("order_type", "MARKET").upper()
    symbol = item["symbol"]

    order: dict = {"symbol": symbol, "side": "BUY",
                   "orderType": order_type, "timeInForce": "DAY"}

    # 미국 MARKET + 금액주문: 수량 계산 없이 orderAmount 그대로 (정규장 한정)
    if market == "US" and order_type == "MARKET" and item.get("amount_usd"):
        order["orderAmount"] = str(item["amount_usd"])
        order["_display"] = f"${item['amount_usd']} (금액주문)"
        return order

    # 그 외에는 시세가 있어야 수량/지정가를 계산할 수 있다
    if price is None or price <= 0:
        raise SkipItem(f"{symbol}: 시세 없음 → 수량 계산 불가")

    if order_type == "LIMIT":
        buffer = float(basket.get("limit_buffer_pct", 0.0)) / 100.0
        raw_price = price * (1 + buffer)
        # 미국 소수점 2자리, 국내 정수(원). 실제 호가단위는 Phase 3에서 정교화 필요.
        limit_price = round(raw_price, 2) if market == "US" else int(round(raw_price))
        order["price"] = str(limit_price)
        ref_price = limit_price
    else:  # MARKET — 가격 미지정, 수량 산정엔 현재가를 추정치로 사용
        ref_price = price

    amount = item.get("amount_usd") if market == "US" else item.get("amount_krw")
    if not amount:
        raise SkipItem(f"{symbol}: amount_{'usd' if market=='US' else 'krw'} 미설정")

    quantity = math.floor(amount / ref_price)
    if quantity < 1:
        raise SkipItem(f"{symbol}: 배정액({amount})이 1주 가격({ref_price})보다 작음")

    order["quantity"] = str(quantity)
    unit = "$" if market == "US" else "₩"
    order["_display"] = (f"{quantity}주 @ {unit}{ref_price:,} "
                         f"(≈{unit}{quantity*ref_price:,.0f}, {order_type})")
    return order


def _market_currency(market: str) -> str:
    return "USD" if market.upper() == "US" else "KRW"


def _estimate_cost(order: dict, price: float | None) -> float | None:
    """주문 1건의 예상 체결금액(해당 통화). 추정 불가면 None."""
    if order.get("orderAmount"):                       # 미국 금액주문
        return float(order["orderAmount"])
    qty = order.get("quantity")
    if not qty:
        return None
    ref = order.get("price") or price                  # LIMIT은 지정가, MARKET은 현재가
    return int(qty) * float(ref) if ref else None


def _live_enabled(cfg: dict) -> bool:
    if cfg.get("dry_run", True):
        return False
    if os.getenv(LIVE_ENV_FLAG) != LIVE_ENV_VALUE:
        logger.error("dry_run=false 이지만 %s=%s 가 없어 실거래를 거부합니다.",
                     LIVE_ENV_FLAG, LIVE_ENV_VALUE)
        return False
    return True


def run_dca(client, notifier, state, cfg: dict, account_seq: str) -> None:
    dca_cfg = cfg.get("dca", {})
    if not dca_cfg.get("enabled", False):
        logger.info("dca.enabled=false → DCA 실행 안 함")
        return

    live = _live_enabled(dca_cfg)
    mode = "🔴 LIVE 실거래" if live else "🟢 DRY-RUN (주문 안 보냄)"
    today = date.today().isoformat()
    logger.info("DCA 시작 [%s]", mode)

    # 필요한 시세 한 번에 조회
    symbols = [it["symbol"] for b in dca_cfg.get("baskets", []) for it in b.get("items", [])]
    prices = client.get_prices(symbols) if symbols else {}

    # 검증 통과 전략 필터(이평선/RSI) — 조건 미충족 종목은 이번 회차 매수 보류
    strat = (dca_cfg.get("strategy_filter") or "none").lower()
    sig_map: dict[str, dict] = {}
    if strat in ("ma", "rsi", "both", "any"):
        from . import signals as signals_engine
        rows = signals_engine.evaluate_signals(
            client, symbols,
            ma_window=int(dca_cfg.get("ma_window", 20)),
            rsi_window=int(dca_cfg.get("rsi_window", 14)),
            rsi_threshold=float(dca_cfg.get("rsi_threshold", 30)))
        sig_map = {r["symbol"]: r for r in rows}
        logger.info("전략 필터 적용: %s", strat)

    def _filter_passes(symbol: str) -> tuple[bool, str]:
        if strat not in ("ma", "rsi", "both", "any"):
            return True, ""
        s = sig_map.get(symbol, {})
        ma_ok, rsi_ok = bool(s.get("ma_buy")), bool(s.get("rsi_buy"))
        if strat == "ma":
            ok = ma_ok
        elif strat == "rsi":
            ok = rsi_ok
        elif strat == "both":
            ok = ma_ok and rsi_ok
        else:  # any
            ok = ma_ok or rsi_ok
        tag = f"이평선 {'O' if ma_ok else 'X'}/RSI {'O' if rsi_ok else 'X'}"
        return ok, tag

    # 통화별 매수가능금액 조회 → 잔여 한도 가드 (초과 주문 방지)
    currencies = {_market_currency(b["market"]) for b in dca_cfg.get("baskets", [])}
    remaining: dict[str, float | None] = {}
    for cur in currencies:
        try:
            bp = client.get_buying_power(account_seq, cur)
            remaining[cur] = float(bp.get("cashBuyingPower"))
            logger.info("매수가능금액 %s: %s", cur, remaining[cur])
        except Exception as exc:
            remaining[cur] = None  # 조회 실패 시 가드 생략(주문은 막지 않음)
            logger.warning("매수가능금액(%s) 조회 실패, 가드 생략: %s", cur, exc)

    lines = [f"📅 *DCA {today}* — {mode}"]
    planned = 0

    # 시장별 캘린더 상태(거래일+현재 세션) 한 번에 조회
    skip_closed = dca_cfg.get("skip_when_closed", True)
    mkt_status: dict[str, dict] = {}
    from . import market as market_mod
    for cur_market in {b["market"].upper() for b in dca_cfg.get("baskets", [])}:
        mkt_status[cur_market] = market_mod.day_status(client, cur_market)

    for basket in dca_cfg.get("baskets", []):
        market = basket["market"].upper()
        status = mkt_status.get(market, {})
        if skip_closed and not status.get("trading_day", True):
            lines.append(f"🏖 {market} 휴장일 — 이 바스켓 매수 보류")
            logger.info("%s 휴장일, 바스켓 보류", market)
            continue
        # 미국 금액주문(orderAmount)은 정규장 한정 → 정규장 밖이면 안내
        if market == "US" and not status.get("open_now", True):
            lines.append(f"🕐 US 정규장 시간 아님(현재: {status.get('phase')}) "
                         f"— 금액주문은 정규장에만 체결됨")
        for item in basket.get("items", []):
            symbol = item["symbol"]
            coid = _client_order_id(market, symbol, today)

            if state.is_dca_done(coid):
                logger.info("이미 집행됨, 건너뜀: %s", coid)
                continue

            passes, tag = _filter_passes(symbol)
            if not passes:
                lines.append(f"⏸ `{symbol}` 전략조건 미충족 보류 ({tag})")
                logger.info("전략필터 보류: %s (%s)", symbol, tag)
                continue

            price_obj = prices.get(symbol) or {}
            price = float(price_obj["lastPrice"]) if price_obj.get("lastPrice") else None

            try:
                order = _build_order(item, basket, price)
            except SkipItem as skip:
                lines.append(f"⏭ {skip}")
                logger.warning("skip: %s", skip)
                continue

            # 매수가능금액 가드: 잔여 한도 초과면 스킵
            cur = _market_currency(market)
            cost = _estimate_cost(order, price)
            avail = remaining.get(cur)
            if avail is not None and cost is not None and cost > avail:
                unit = "$" if cur == "USD" else "₩"
                lines.append(
                    f"⏭ `{symbol}` 매수가능금액 부족 "
                    f"(필요 {unit}{cost:,.0f} > 잔여 {unit}{avail:,.0f})"
                )
                logger.warning("skip(가드): %s 필요 %.0f > 잔여 %.0f", symbol, cost, avail)
                continue

            display = order.pop("_display", "")
            order["clientOrderId"] = coid
            planned += 1
            if avail is not None and cost is not None:
                remaining[cur] = avail - cost   # 같은 회차 누적 차감

            if live:
                try:
                    resp = client.create_order(account_seq, order)
                    state.mark_dca_done(coid)
                    lines.append(f"✅ `{symbol}` {display} → orderId {resp.get('orderId')}")
                except Exception as exc:
                    lines.append(f"❌ `{symbol}` 주문 실패: {exc}")
                    logger.exception("주문 실패 %s", symbol)
            else:
                # 드라이런: 전송 안 함, done 표시도 안 함(반복 테스트 가능)
                lines.append(f"🛒 `{symbol}` {display}  _(샀을 주문)_")

    if planned == 0:
        lines.append("_이번 회차 신규 주문 없음_")

    state.save()
    notifier.send("\n".join(lines))
    logger.info("DCA 완료 [%s] planned=%d", mode, planned)
