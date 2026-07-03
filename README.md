# toss-openapi-quant

*한국어 | [English](README.en.md)*

토스증권 Open API 기반 **전략 신호 · 백테스트 · 검증 · MCP** 퀀트 툴킷.
기본 동작은 모두 **읽기 전용/드라이런**이며, 실거래는 이중 잠금으로 막혀 있다.

> 단순 가격/보유 알림은 토스 앱 네이티브 기능과 중복이라 제거했다. 이 도구는
> 토스가 제공하지 않는 것 — **계산된 전략 신호, 백테스트, 검증** — 에 집중한다.

## 한눈에 보기

| 축 | 무엇 | 진입점 |
|---|---|---|
| 📡 전략 신호 | 이평선·RSI 매수 신호 (주문 X) | `src.main --signals` |
| 🛒 DCA 드라이런 | 정액 분할매수 시뮬 (주문 X, 이중잠금) | `src.main --dca-once` |
| 📊 백테스트 | DCA·이평선·RSI·거치식·바스켓·모멘텀 | `src.backtest_cli`, `src.momentum_cli` |
| 🚶 검증 | walk-forward + 파라미터 sweep + 신호효용 | `src.validate_cli`, `src.signal_eval_cli` |
| 🤖 MCP | 위를 Claude에서 대화로 실행 | `run_mcp.py` |

## 설치
```bash
git clone https://github.com/<your-account>/toss-openapi-quant.git
cd toss-openapi-quant
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # TOSS_CLIENT_ID/SECRET 필수 (본인 토스 키), TELEGRAM_* 선택
```
`.env` (각자 [토스증권 Open API](https://developers.tossinvest.com)에서 발급한 본인 키):
```
TOSS_CLIENT_ID=...        # OAuth2 client credentials
TOSS_CLIENT_SECRET=...
TELEGRAM_BOT_TOKEN=...    # 미설정 시 콘솔로 출력
TELEGRAM_CHAT_ID=...
```

## ⚠️ 안전 원칙
- 시세·계좌 조회, 신호, 백테스트는 **주문을 내지 않는다.**
- DCA 실거래는 **두 조건 모두** 충족해야만 동작 (하나라도 빠지면 자동 드라이런):
  1. `config.yaml` → `dca.dry_run: false`
  2. 환경변수 `TOSS_ENABLE_LIVE_TRADING=I_UNDERSTAND`

## 면책 (Disclaimer)
- 이 소프트웨어는 **교육·연구 목적의 도구**이며, **투자 자문이나 권유가 아니다.**
- 백테스트 결과는 과거 데이터 기반이며 **미래 수익을 보장하지 않는다.** 모든 투자 결정과
  그 결과(손실 포함)는 **사용자 본인의 책임**이다.
- **⚠️ 실거래 리스크 (`dca.dry_run: false` 활성화 시):** 실제 자금으로 **실제 주문이 체결된다.**
  소프트웨어 버그·로직 오류, 네트워크·API 장애, 시세 지연, 시장 급변, 잘못된 설정 등으로
  **의도치 않은 주문·중복 주문·미체결·금전적 손실**이 발생할 수 있다. 실거래 활성화 결정과
  그로 인한 **모든 손실은 전적으로 사용자 본인의 책임**이며, 반드시 **소액으로 충분히 검증한 뒤**
  자기 책임 하에 사용한다. 저자·기여자는 실거래로 인한 어떤 손실에도 책임지지 않는다.
- 무보증(AS-IS). 저자는 이 코드 사용으로 인한 어떤 손해에도 책임지지 않는다 (MIT License).
- 각 사용자는 **본인의 토스증권 Open API 키**로 사용하며, 토스증권 Open API 이용약관을
  준수할 책임이 있다. (이 저장소는 키·계좌정보·토스 데이터를 포함하지 않는다.)

---

## 1. 전략 신호 (`--signals`)
검증에서 가장 견고했던 **이평선 DCA + RSI 과매도 DCA** 신호를 종목별로 함께 알림.
주문 없이 "오늘 BUY/대기"만 보낸다. 신호 이력은 `data/signal_log.jsonl`에 적재.
```bash
python -m src.main --signals
```
설정: `config.yaml`의 `signals:`(미설정 시 `dca.baskets` 종목 재사용).

### 신호 회고 (`--reflect`)
누적된 신호 로그를 이후 **실제 주가와 대조**해 "그 신호가 맞았나"를 사후 평가
(적중률·평균수익·대기건수). 백테스트(과거 재현)와 달리 **봇이 실제 찍은 신호**를 본다.
```bash
python -m src.main --reflect --reflect-horizon 20
```

## 2. DCA 드라이런 (`--dca-once`)
바스켓을 주기적으로 매수하는 시뮬. 미국 MARKET+`amount_usd`는 금액주문, 그 외는 시세로
수량 환산(정수 주, 1주 미만 스킵). 통화별 매수가능금액 가드, `clientOrderId`로 같은 날 중복 방지.
```bash
python -m src.main --dca-once
```
설정(`config.yaml`의 `dca:`):
- `strategy_filter: none|ma|rsi|both|any` — 검증 전략 필터 적용(미충족 종목 보류)
- `skip_when_closed: true` — 휴장일(주말·공휴일)엔 해당 시장 바스켓 매수 보류 (`/api/v1/market-calendar`).
  미국 바스켓은 정규장 시간이 아니면 안내(금액주문은 정규장에만 체결). 장중 판별은 `src/market.py`
- 바스켓 항목별 `amount_usd`/`amount_krw`, `order_type`, `limit_buffer_pct`

## 3. 백테스트
과거 일봉(`/api/v1/candles`)으로 전략을 시뮬. 실거래와 무관.
```bash
python -m src.backtest_cli --symbol 005930 --amount 100000 --weekday 월 \
    --count 3000 --asset-class kr_stock
```
주요 옵션:
- `--asset-class kr_stock|kr_etf|us_stock` — 수수료·세금 반영(세후 손익)
- `--start/--end YYYY-MM-DD` — 구간 한정(예: 하락장)
- `--ma 20 --ma-mode below` — 이평선 조건부 매수
- `--rsi 14 --rsi-threshold 30` — RSI 과매도 조건부 매수
- `--vs-lumpsum` — 거치식 vs 적립식 동일 총액 비교
- `--slippage 0.1` — 체결 슬리피지(%) 반영 (종가보다 그만큼 불리하게 매수)
- `--same-bar-fill` — 조건부 매수를 당일 종가 체결(룩어헤드 허용, 비교용). 기본은 다음날 시가
- `--us`(소수 주), `--no-cache`

출력: 매수횟수 / 평균단가 / 평가손익 / **IRR(연환산)** / **MDD** / 세후 손익.

### 바스켓 · 듀얼 모멘텀
여러 종목을 포트폴리오로 합산하는 바스켓 백테스트는 `src/basket_backtest.py`의
`run_basket()`를 코드에서 호출(KRW 환산 합산). 듀얼 모멘텀은 전용 CLI 제공:
```bash
# 듀얼 모멘텀(매월 로테이션, 절대모멘텀 회피, 벤치마크·타임라인 출력)
python -m src.momentum_cli --assets 005930:kr_stock,069500:kr_etf,AAPL:us_stock,VOO:us_stock --lookback 12
python -m src.momentum_cli --assets 005930:kr_stock,AAPL:us_stock,153130:kr_etf --safe-asset 153130 --lookback 12
```

## 4. 전략 검증 (walk-forward + sweep)
단일 구간 결과의 표본 편향을 줄이는 검증 도구. 개념은 **[VALIDATION.md](docs/VALIDATION.md)**.
```bash
python -m src.validate_cli --strategy ma  --symbol 005930 --asset-class kr_stock --ma 20 --sweep-ma 10,20,60,120
python -m src.validate_cli --strategy rsi --symbol 005930 --asset-class kr_stock --rsi 14 --sweep-rsi 25,30,35,40
python -m src.validate_cli --strategy momentum --assets 005930:kr_stock,AAPL:us_stock --lookback 12 --sweep-lookback 3,6,9,12
```

### 신호 성과 추적 (이벤트 스터디)
신호(이평선 아래/RSI 과매도)가 뜬 뒤 N일 수익률이 평소보다 높은지(edge) 측정.
```bash
python -m src.signal_eval_cli --symbol 005930 --count 3000 --horizons 5,20,60
```

---

## MCP 서버 (Claude에서 대화로 백테스트)
백테스트·검증·신호를 Claude(데스크탑/코드)에서 **자연어로** 실행. 읽기 전용 분석만
노출하며 주문은 하지 않는다.

### 1) 등록
`~/.claude.json`(Claude Code) 또는 `claude_desktop_config.json`(데스크탑 앱)의 `mcpServers`에:
```json
"toss-backtest": {
  "type": "stdio",
  "command": "/절대경로/toss-openapi-quant/.venv/bin/python",
  "args": ["/절대경로/toss-openapi-quant/run_mcp.py"]
}
```
> 키는 프로젝트 `.env`(TOSS_CLIENT_ID/SECRET)에서 자동 로드 — 설정 파일에 키 불필요.
> **등록 후 Claude 재시작**해야 활성화됨. (다른 사람: clone → venv → 본인 키 `.env` → 위 등록)

### 2) 도구 7종 & 예시 질문
| 도구 | 하는 일 | 이렇게 물어보면 됨 |
|---|---|---|
| `backtest_dca` | DCA 백테스트(이평선/RSI 필터·세금 옵션) | "삼성 12년 DCA 백테스트, 월 10만원" |
| `lumpsum_vs_dca` | 거치식 vs 적립식 동일총액 비교 | "삼성 거치식이랑 적립식 비교해줘" |
| `signal_efficacy` | 신호 예측력(이벤트 스터디) | "삼성 이평선/RSI 신호 예측력 있어?" |
| `walk_forward` | 전략 견고성(walk-forward) | "이평선 DCA 전략 walk-forward 검증" |
| `dual_momentum` | 듀얼 모멘텀(세후 포함) | "삼성·애플·VOO 듀얼모멘텀 12개월" |
| `current_signals` | 오늘 매수 신호 | "VOO·애플 오늘 신호 떴어?" |
| `bull_bear_evidence` | 강세/약세 논거용 객관 숫자 | "삼성 강세·약세 논리 정리해줘" |

> `bull_bear_evidence`는 숫자(추세·이평선·RSI·모멘텀·52주고저·낙폭·변동성)만 반환하고,
> 강세/약세 논리는 Claude가 그 숫자로 구성한다(엔진=숫자, LLM=서술).

## 캐싱 / Rate-limit
- `src/cache.py` — 캔들을 `data/candles/`에 저장(TTL 12h). 반복 백테스트 시 API 호출 없이 재사용.
- 클라이언트에 429 자동 재시도(지수 백오프 + `Retry-After`) 내장.
- `src/fx.py` — 과거 환율을 월별 샘플링해 `data/fx/`에 캐시(USD→KRW 날짜별 환산).

## 연환산 지표 (공정 비교)
총수익률은 구간 길이에 좌우되므로 전략 비교는 연환산으로:
- DCA류 → **IRR**(자금가중수익률, 투자 시점 분산 반영)
- 모멘텀 → **CAGR**(초기자본 일괄 복리 환산)

## 테스트
```bash
python -m pytest -q     # 네트워크 없이 합성 데이터로 36 케이스
```
IRR·수수료/세금·RSI/SMA·DCA 정수주/멱등성·FX 보간·실거래 잠금 등을 커버.

## cron 예시
```cron
# 매일 장중 신호 알림
0 9 * * 1-5 cd ~/toss-openapi-quant && .venv/bin/python -m src.main --signals >> signals.log 2>&1
# 주간 DCA 드라이런(국내, 화 09:05) — 미국 정규장은 한국 밤이라 별도 스케줄 필요
5 9 * * 2  cd ~/toss-openapi-quant && .venv/bin/python -m src.main --dca-once >> dca.log 2>&1
```

## 프로젝트 구조
```
src/
  toss_client.py    # OAuth2 토큰 자동갱신 + API 래퍼(+429 재시도)
  cache.py / fx.py  # 캔들 캐시 / 과거 환율 시계열
  market.py         # 시장 캘린더(거래일/휴장 판별)
  mcp_server.py     # MCP 서버(백테스트/검증/신호를 Claude 도구로 노출)
  notifier.py       # 텔레그램·콘솔 알림
  state.py          # DCA 멱등성(중복 매수 방지)
  signals.py        # 이평선/RSI 신호 + 이력 로깅
  reflection.py     # 신호 로그 사후 회고(실제 주가 대조)
  dca.py            # DCA 드라이런 엔진(전략필터·가드·이중잠금)
  fees.py           # 수수료·세금 프로파일(2026)
  backtest.py       # 백테스트 코어(DCA/MA/RSI/IRR/거치식)
  basket_backtest.py# 바스켓 합산 백테스트
  momentum.py       # 듀얼 모멘텀
  validation.py     # 전략 무관 walk-forward + sweep 엔진
  *_cli.py          # 진입점: main / backtest_cli / momentum_cli / validate_cli
tests/              # pytest (네트워크 없음)
docs/               # STRATEGIES.md, VALIDATION.md
```

## 문서
- **[STRATEGIES.md](docs/STRATEGIES.md)** — 전략별 구현 현황·검증 결과·미구현 항목
- **[VALIDATION.md](docs/VALIDATION.md)** — 백테스트 검증 개념(파라미터 민감도, walk-forward)

## 가정 / 한계
- 백테스트: 조건부(이평선/RSI) 매수는 **다음날 시가 체결**(룩어헤드 방지, 기본). 일반 DCA·거치식은 당일 종가.
  슬리피지는 기본 0, `--slippage`로 반영 가능. 국내는 정수 주(잔돈 이월)·미국은 소수 주.
- 세금: 2026 기준(국내주식 거래세 0.20%, 국내주식형 ETF 비과세, 미국 양도세 22%/연250만원공제).
  미국 양도세는 연단위 netting이라 모멘텀 백테스트엔 미반영(노출만 표시).
- 토스 환율 데이터는 ~2023년부터만 존재 → 그 이전 구간은 가장 이른 값으로 근사.
