"""토스증권 Open API 클라이언트.

- OAuth2 토큰 발급 + 만료 전 자동 재발급, 429 자동 재시도
- 시세/캔들/환율/시장캘린더/계좌/보유종목/주문이력/매수가능금액 조회 (읽기 전용)
- 주문 생성(create_order) — 실제 돈이 나가는 호출. DCA 엔진의 live 모드(이중잠금)에서만 호출됨.
"""

from __future__ import annotations

import time
import logging
from typing import Any

import requests

logger = logging.getLogger("toss.client")

BASE_URL = "https://openapi.tossinvest.com"


class TossClient:
    def __init__(self, client_id: str, client_secret: str, timeout: int = 10):
        self._client_id = client_id
        self._client_secret = client_secret
        self._timeout = timeout
        self._session = requests.Session()

        self._access_token: str | None = None
        self._token_expires_at: float = 0.0  # epoch seconds

    # ------------------------------------------------------------------ auth
    def _ensure_token(self) -> str:
        """유효한 access_token 반환. 만료 60초 전이면 재발급."""
        if self._access_token and time.time() < self._token_expires_at - 60:
            return self._access_token

        logger.info("OAuth2 토큰 발급 요청")
        resp = self._session.post(
            f"{BASE_URL}/oauth2/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=self._timeout,
        )
        resp.raise_for_status()
        payload = resp.json()

        self._access_token = payload["access_token"]
        expires_in = int(payload.get("expires_in", 86400))
        self._token_expires_at = time.time() + expires_in
        logger.info("토큰 발급 완료 (expires_in=%ss)", expires_in)
        return self._access_token

    def _auth_headers(self, account_seq: str | None = None) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._ensure_token()}"}
        if account_seq:
            headers["X-Tossinvest-Account"] = str(account_seq)
        return headers

    def _get(self, path: str, *, params: dict | None = None,
             account_seq: str | None = None, max_retries: int = 4,
             quiet: bool = False) -> dict[str, Any]:
        """GET 요청. quiet=True면 4xx 에러 로깅 생략(예상된 404 등, 호출부가 처리)."""
        for attempt in range(max_retries + 1):
            resp = self._session.get(
                f"{BASE_URL}{path}",
                params=params,
                headers=self._auth_headers(account_seq),
                timeout=self._timeout,
            )
            # 429(rate-limit): Retry-After 또는 지수 백오프로 재시도
            if resp.status_code == 429 and attempt < max_retries:
                wait = float(resp.headers.get("Retry-After", 2 ** attempt))
                logger.warning("429 rate-limit, %.0fs 후 재시도 (%d/%d)",
                               wait, attempt + 1, max_retries)
                time.sleep(wait)
                continue
            if resp.status_code >= 400 and not quiet:
                logger.error("GET %s -> %s: %s", path, resp.status_code, resp.text[:500])
            resp.raise_for_status()
            return resp.json()
        raise RuntimeError(f"unreachable: GET {path}")  # 안전망(루프는 항상 반환/예외)

    # --------------------------------------------------------------- queries
    def list_accounts(self) -> list[dict]:
        """GET /api/v1/accounts -> 계좌 목록."""
        data = self._get("/api/v1/accounts")
        return data.get("result", [])

    def get_holdings(self, account_seq: str, symbol: str | None = None) -> dict:
        """GET /api/v1/holdings -> 자산 요약 + 보유 종목.

        반환 result 구조(주요 필드):
          totalPurchaseAmount: {krw, usd}
          marketValue:         {amount, amountAfterCost}
          profitLoss:          {amount, rate, amountAfterCost, rateAfterCost}
          items: [{symbol, quantity, lastPrice, averagePurchasePrice, ...}]
        """
        params = {"symbol": symbol} if symbol else None
        data = self._get("/api/v1/holdings", params=params, account_seq=account_seq)
        return data.get("result", {})

    def get_prices(self, symbols: list[str]) -> dict[str, dict]:
        """GET /api/v1/prices -> {symbol: price_obj}. 최대 200개."""
        if not symbols:
            return {}
        data = self._get("/api/v1/prices", params={"symbols": ",".join(symbols)})
        return {row["symbol"]: row for row in data.get("result", [])}

    def get_candles(self, symbol: str, interval: str = "1d",
                    count: int = 100, adjusted: bool = True) -> list[dict]:
        """GET /api/v1/candles -> 과거 OHLCV 캔들 (오래된 순 정렬).

        interval: '1d'(일봉) 또는 '1m'(분봉).
        count: 원하는 총 봉 수. 200 초과 시 nextBefore 커서로 자동 페이지네이션.

        반환: [{date(str), open, high, low, close, volume(float)} ...] 오래된→최신.
        """
        out: list[dict] = []
        before: str | None = None
        remaining = count

        while remaining > 0:
            page = min(remaining, 200)  # API 1회 최대 200봉
            params = {"symbol": symbol, "interval": interval,
                      "count": page, "adjusted": str(adjusted).lower()}
            if before:
                params["before"] = before
            result = self._get("/api/v1/candles", params=params).get("result", {})
            rows = result.get("candles", [])
            if not rows:
                break
            for k in rows:  # 문자열 → float 정규화
                out.append({
                    "date": k["timestamp"][:10],
                    "open": float(k["openPrice"]),
                    "high": float(k["highPrice"]),
                    "low": float(k["lowPrice"]),
                    "close": float(k["closePrice"]),
                    "volume": float(k["volume"]),
                })
            remaining -= len(rows)
            before = result.get("nextBefore")
            if not before:  # 더 이상 옛날 데이터 없음
                break

        out.reverse()  # 최신순 → 오래된순 (백테스트 진행 방향)
        return out

    def get_market_calendar(self, country: str, date: str | None = None) -> dict:
        """GET /api/v1/market-calendar/{KR|US} -> 장 운영 정보.

        반환 result: {today, previousBusinessDay, nextBusinessDay}.
        today에 regularMarket 키가 있으면 거래일, 없으면 휴장(주말·공휴일).
        """
        params = {"date": date} if date else None
        data = self._get(f"/api/v1/market-calendar/{country.upper()}", params=params)
        return data.get("result", {})

    def get_exchange_rate(self, base: str = "USD", quote: str = "KRW") -> float:
        """GET /api/v1/exchange-rate -> 환율(float)."""
        data = self._get(
            "/api/v1/exchange-rate",
            params={"baseCurrency": base, "quoteCurrency": quote},
        )
        return float(data["result"]["rate"])

    # ----------------------------------------------------------- order-info
    def get_buying_power(self, account_seq: str, currency: str = "KRW") -> dict:
        """GET /api/v1/buying-power -> 현금 기반 매수가능금액.

        헤더 X-Tossinvest-Account + 쿼리 currency(필수, KRW/USD) 필요.
        응답 바디는 spec에 스키마 정의가 없어 result 전체를 반환한다.
        """
        data = self._get("/api/v1/buying-power",
                         params={"currency": currency}, account_seq=account_seq)
        return data.get("result", {})

    # ---------------------------------------------------------------- orders
    def get_orders(self, account_seq: str, status: str = "CLOSED",
                   symbol: str | None = None, start: str | None = None,
                   end: str | None = None, max_count: int = 2000) -> list[dict]:
        """GET /api/v1/orders -> 주문 목록 (오래된 순 정렬). 읽기 전용.

        status: 'CLOSED'(종료: FILLED/CANCELED/REJECTED 등) | 'OPEN'(진행 중).
        start/end: 주문 생성일(orderedAt, KST) 기준 YYYY-MM-DD. 미지정 시 전체 기간.
        OPEN은 전량 반환(cursor/limit 무시), CLOSED만 커서 페이지네이션(1회 최대 100).

        반환 항목(주요): orderId, symbol, side, orderType, status, quantity,
        orderAmount, currency, orderedAt, canceledAt,
        execution: {filledQuantity, averageFilledPrice, filledAmount,
                    commission, tax, filledAt, settlementDate}
        """
        out: list[dict] = []
        cursor: str | None = None

        # API 특이동작(실측): `to`를 `from` 없이 보내면 항상 0건이 온다. `from`만
        # 보내면 정렬이 오름차순으로 뒤집힌다. 둘 다 보낼 때만 일관되게 동작하므로
        # 한쪽만 지정되면 나머지를 넓은 경계값으로 채워 항상 짝으로 보낸다.
        if start or end:
            start = start or "2000-01-01"
            end = end or "2099-12-31"

        while len(out) < max_count:
            params: dict[str, Any] = {"status": status,
                                      "limit": min(100, max_count - len(out))}
            if symbol:
                params["symbol"] = symbol
            if start:
                params["from"] = start
            if end:
                params["to"] = end
            if cursor:
                params["cursor"] = cursor

            result = self._get("/api/v1/orders", params=params,
                               account_seq=account_seq).get("result", {})
            rows = result.get("orders", [])
            out.extend(rows)
            cursor = result.get("nextCursor")
            if not rows or not result.get("hasNext") or not cursor:
                break

        # 서버 정렬에 기대지 않고 명시 정렬 (오래된→최신, 캔들·백테스트와 같은 방향)
        out.sort(key=lambda o: o.get("orderedAt") or "")
        return out

    def create_order(self, account_seq: str, order: dict) -> dict:
        """POST /api/v1/orders -> 주문 생성. ⚠️ 실제 체결되는 호출.

        order 필드: symbol, side(BUY/SELL), orderType(MARKET/LIMIT),
        quantity 또는 orderAmount(미국 정규장 한정), price(LIMIT 필수),
        timeInForce(DAY 기본), clientOrderId(멱등성 키).

        DCA 엔진의 live 모드에서만 호출되어야 한다.
        """
        resp = self._session.post(
            f"{BASE_URL}/api/v1/orders",
            json=order,
            headers=self._auth_headers(account_seq),
            timeout=self._timeout,
        )
        if resp.status_code >= 400:
            logger.error("POST /api/v1/orders -> %s: %s", resp.status_code, resp.text[:500])
        resp.raise_for_status()
        return resp.json()
