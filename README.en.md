# toss-openapi-quant

*[한국어](README.md) | English*

A quant toolkit built on the **Toss Securities Open API** — **strategy signals, backtesting,
validation, and MCP**. Everything is **read-only / dry-run** by default; live trading is gated
behind a double lock.

> Simple price/holdings alerts are dropped — they duplicate the Toss app's native alerts.
> This tool focuses on what Toss does *not* provide: **computed strategy signals, backtests,
> and validation.**

## At a glance

| Area | What | Entry point |
|---|---|---|
| 📡 Strategy signals | MA / RSI buy signals (no orders) | `src.main --signals` |
| 🛒 DCA dry-run | Dollar-cost-averaging sim (no orders, double-locked) | `src.main --dca-once` |
| 📊 Backtest | DCA / MA / RSI / lump-sum / basket / momentum | `src.backtest_cli`, `src.momentum_cli` |
| 🚶 Validation | walk-forward + parameter sweep + signal efficacy | `src.validate_cli`, `src.signal_eval_cli` |
| 💼 My account | actual holdings & fills (read-only) | `src/portfolio.py` |
| 🔍 Reality check | my actual trades vs mechanical DCA; fee assumptions | `src/actual.py` |
| 🤖 MCP | Run the above conversationally in Claude | `run_mcp.py` |

## Install
```bash
git clone https://github.com/<your-account>/toss-openapi-quant.git
cd toss-openapi-quant
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # TOSS_CLIENT_ID/SECRET required (your own keys), TELEGRAM_* optional
```
`.env` (use **your own** keys from [Toss Securities Open API](https://developers.tossinvest.com)):
```
TOSS_CLIENT_ID=...        # OAuth2 client credentials
TOSS_CLIENT_SECRET=...
TELEGRAM_BOT_TOKEN=...    # falls back to console if unset
TELEGRAM_CHAT_ID=...
```

## ⚠️ Safety model
- Quotes/account reads, signals, and backtests **never place orders.**
- Live DCA trading runs only when **both** conditions hold (otherwise auto dry-run):
  1. `config.yaml` → `dca.dry_run: false`
  2. env var `TOSS_ENABLE_LIVE_TRADING=I_UNDERSTAND`

## Disclaimer
- This software is a tool for **education and research** — **not investment advice or a solicitation.**
- Backtest results are based on past data and **do not guarantee future returns.** All investment
  decisions and outcomes (including losses) are **the user's own responsibility.**
- **⚠️ Live-trading risk (when `dca.dry_run: false` is enabled):** real orders are placed with
  **real money.** Software bugs, logic errors, network/API outages, quote delays, sudden market
  moves, or misconfiguration can cause **unintended orders, duplicate orders, non-execution, or
  financial loss.** The decision to enable live trading — and any resulting loss — is **solely the
  user's own responsibility.** Always **validate thoroughly with small amounts first** and use at
  your own risk. The author and contributors are not liable for any live-trading losses.
- Provided AS-IS, with no warranty (MIT License). The author is not liable for any damages.
- Each user runs it with **their own Toss Open API keys** and is responsible for complying with
  the Toss Open API terms of service. (This repo contains no keys, account data, or Toss data.)

---

## 1. Strategy signals (`--signals`)
Reports per-symbol **MA-DCA + RSI-oversold-DCA** signals (the most robust ones in validation).
No orders — just "BUY / wait today". Signal history is logged to `data/signal_log.jsonl`.
```bash
python -m src.main --signals
```
Config: `signals:` in `config.yaml` (falls back to `dca.baskets` symbols if unset).

## 2. DCA dry-run (`--dca-once`)
Simulates periodic basket buys. US MARKET + `amount_usd` uses a cash order; otherwise quantity is
derived from price (integer shares, skips if below 1 share). Per-currency buying-power guard;
`clientOrderId` prevents duplicate buys on the same day.
```bash
python -m src.main --dca-once
```
Config (`dca:` in `config.yaml`):
- `strategy_filter: none|ma|rsi|both|any` — apply a validated filter (hold symbols that fail it)
- `skip_when_closed: true` — hold a market's basket on market holidays (`/api/v1/market-calendar`)
- per-item `amount_usd`/`amount_krw`, `order_type`, `limit_buffer_pct`

## 3. Backtesting
Simulates strategies on historical daily candles (`/api/v1/candles`). No live trading.
```bash
python -m src.backtest_cli --symbol 005930 --amount 100000 --weekday 월 \
    --count 3000 --asset-class kr_stock
```
Key options:
- `--asset-class kr_stock|kr_etf|us_stock` — apply fees & taxes (after-tax P&L)
- `--start/--end YYYY-MM-DD` — restrict to a window (e.g. a bear market)
- `--ma 20 --ma-mode below` — MA-conditional buying
- `--rsi 14 --rsi-threshold 30` — RSI-oversold-conditional buying
- `--vs-lumpsum` — lump-sum vs DCA on the same total
- `--slippage 0.1` — apply execution slippage (%) (fill worse than close by that much)
- `--same-bar-fill` — fill conditional buys at same-day close (allows look-ahead, for comparison).
  Default fills at next day's open
- `--us` (fractional shares), `--no-cache`

Output: buy count / avg cost / P&L / **IRR (annualized)** / **MDD** / after-tax P&L.

### Basket & dual momentum
Basket backtest (portfolio aggregation, converted to KRW) is `run_basket()` in
`src/basket_backtest.py`, called from code. Dual momentum has its own CLI:
```bash
# Dual momentum (monthly rotation, absolute-momentum cash-out, benchmark + trade timeline)
python -m src.momentum_cli --assets 005930:kr_stock,069500:kr_etf,AAPL:us_stock,VOO:us_stock --lookback 12
python -m src.momentum_cli --assets 005930:kr_stock,AAPL:us_stock,153130:kr_etf --safe-asset 153130 --lookback 12
```

## 4. Validation (walk-forward + sweep)
Tools to reduce single-window sample bias. Concepts in **[VALIDATION.md](docs/VALIDATION.md)**.
```bash
python -m src.validate_cli --strategy ma  --symbol 005930 --asset-class kr_stock --ma 20 --sweep-ma 10,20,60,120
python -m src.validate_cli --strategy rsi --symbol 005930 --asset-class kr_stock --rsi 14 --sweep-rsi 25,30,35,40
python -m src.validate_cli --strategy momentum --assets 005930:kr_stock,AAPL:us_stock --lookback 12 --sweep-lookback 3,6,9,12
```

### Signal efficacy (event study)
Measures whether returns after a signal (price below MA / RSI oversold) beat the baseline (edge).
```bash
python -m src.signal_eval_cli --symbol 005930 --count 3000 --horizons 5,20,60
```

---

## MCP server (backtest from Claude, conversationally)
Run backtests/validation/signals from Claude (Desktop/Code) in **natural language**.
Read-only analysis only — no orders.

### 1) Register
Add to `mcpServers` in `~/.claude.json` (Claude Code) or `claude_desktop_config.json` (Desktop app):
```json
"toss-backtest": {
  "type": "stdio",
  "command": "/abs/path/toss-openapi-quant/.venv/bin/python",
  "args": ["/abs/path/toss-openapi-quant/run_mcp.py"]
}
```
> Keys load from the project `.env` (TOSS_CLIENT_ID/SECRET) — none in the config.
> **Restart Claude** to activate. (Others: clone → venv → your keys in `.env` → register.)

### 2) The 10 tools & example prompts
| Tool | What it does | Ask like |
|---|---|---|
| `backtest_dca` | DCA backtest (MA/RSI filter, taxes) | "backtest 12y DCA on Samsung, 100k/mo" |
| `lumpsum_vs_dca` | lump-sum vs DCA, same total | "compare lump-sum vs DCA on Samsung" |
| `signal_efficacy` | signal predictive power (event study) | "do Samsung's MA/RSI signals predict?" |
| `walk_forward` | strategy robustness (walk-forward) | "walk-forward validate MA DCA" |
| `dual_momentum` | dual momentum (incl. after-tax) | "dual momentum on Samsung, AAPL, VOO" |
| `current_signals` | today's buy signals | "any signal on VOO and AAPL today?" |
| `bull_bear_evidence` | objective numbers for bull/bear case | "lay out the bull and bear case for Samsung" |
| `my_holdings` | my actual holdings, weights, P&L (KRW-unified) | "how does my portfolio look?" |
| `my_trades` | my actual fills (aggregated per symbol) | "what did I pay for TQQQ?" |
| `my_vs_backtest` | my trades vs same-period mechanical DCA | "did my TQQQ buying beat plain DCA?" |

> `my_holdings` / `my_trades` read **your own account** (read-only, never places orders).
> Backtests are assumptions; these are measurements — feed real holdings straight into a
> backtest, or check its cost assumptions against your actual fill prices and commissions.

> `my_vs_backtest` **adjusts for stock splits automatically** — order history reports the
> quantities and prices as filled, so comparing them against split-adjusted candles is wrong
> (the factor is recovered from the adjusted/unadjusted candle ratio). If share count derived
> from fills disagrees with actual holdings, `reconciliation.severity` says so — `material`
> means the history is incomplete and the comparison should not be trusted.

> `bull_bear_evidence` returns only **objective numbers** (trend, MA, RSI, momentum, 52w high/low,
> drawdown, volatility); Claude writes both sides from the data (engine = numbers, LLM = narrative).

## Caching / rate limits
- `src/cache.py` — candles cached to `data/candles/` (12h TTL); repeat backtests hit no API.
- The client retries 429s automatically (exponential backoff + `Retry-After`).
- `src/fx.py` — historical FX sampled monthly into `data/fx/` (date-aware USD→KRW conversion).

## Annualized metrics (fair comparison)
Total return depends on window length, so compare strategies annualized:
- DCA-style → **IRR** (money-weighted, accounts for spread-out entries)
- Momentum → **CAGR** (initial-capital compounding)

## Tests
```bash
python -m pytest -q     # 36 cases on synthetic data, no network
```
Covers IRR, fees/taxes, RSI/SMA, DCA integer shares/idempotency, FX interpolation, live-trade lock.

## Project layout
```
src/
  toss_client.py    # OAuth2 token auto-refresh + API wrapper (+429 retry)
  cache.py / fx.py  # candle cache / historical FX series
  market.py         # market calendar (trading day vs holiday)
  mcp_server.py     # MCP server (exposes backtest/validation/signals as Claude tools)
  notifier.py       # Telegram / console output
  state.py          # DCA idempotency (no duplicate buys)
  portfolio.py      # account snapshot & fills, normalized (KRW-unified)
  actual.py         # actual-vs-backtest (split adjustment, share reconciliation, fees)
  signals.py        # MA/RSI signals + history logging
  dca.py            # DCA dry-run engine (strategy filter, guards, double lock)
  fees.py           # fee & tax profiles (2026)
  backtest.py       # backtest core (DCA/MA/RSI/IRR/lump-sum)
  basket_backtest.py# basket aggregation backtest
  momentum.py       # dual momentum
  validation.py     # strategy-agnostic walk-forward + sweep engine
  *_cli.py          # entry points: main / backtest_cli / momentum_cli / validate_cli
tests/              # pytest (no network)
```

## Docs
- **[STRATEGIES.md](docs/STRATEGIES.md)** — per-strategy status, validation results, unimplemented ideas
- **[VALIDATION.md](docs/VALIDATION.md)** — backtest validation concepts (parameter sensitivity, walk-forward)

## Assumptions / limitations
- Backtest: conditional (MA/RSI) buys fill at **next day's open** (avoids look-ahead, default);
  plain DCA / lump-sum fill at same-day close. Slippage 0 by default (`--slippage`).
  KR uses integer shares (carry leftover cash); US fractional.
- Taxes: 2026 basis (KR stock transfer tax 0.20%, KR equity ETF tax-exempt, US capital gains 22% with
  a 2.5M KRW annual deduction). US capital gains needs annual netting, so it's not applied in momentum
  backtests (only the exposure is shown).
- Toss FX data exists only from ~2023 → earlier windows are approximated with the earliest value.
