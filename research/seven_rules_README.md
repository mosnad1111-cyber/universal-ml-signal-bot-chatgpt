# Seven-rules research lab — XAUUSD + BTCUSDT

This is a separate, research-only experiment. It does not change the live XGBoost
signals, score/AI thresholds, risk settings, Telegram scanning, or broker execution.

## Run

From the repository root:

```bash
python -m research.seven_rules_research --bars 30000
```

Optional output period (UTC dates; the pre-start history is still used to warm up
the daily EMA calculation):

```bash
python -m research.seven_rules_research --bars 30000 --start 2026-06-01 --end 2026-10-01
```

The script downloads the project's configured 5-minute historical candles:
- Gold: Dukascopy XAU/USD BID through the existing market provider.
- Bitcoin: Binance BTC/USDT klines through the existing market provider.

Results are saved under `research/results/`:
- `seven_rules_summary.json`: separate Gold and BTC reports, including errors,
  provider provenance, coverage, and strategy metrics.
- `seven_rules_gold_trades.csv`
- `seven_rules_btc_trades.csv`

A manual GitHub Actions workflow named **Seven-rules Gold + Bitcoin research**
runs the same script and uploads these files as a downloadable workflow artifact.

## Session and playbook definitions

All timestamps are converted to `America/New_York`, so daylight-saving changes
are handled by the timezone database. The configured reference session is:
- Premarket range: 04:00 inclusive to 09:30 exclusive.
- Opening range: the first completed 5-minute bar at 09:30 (09:30–09:35).
- Entry window: 09:35 through 15:45.
- Force-flat check: 15:55.

For BTC, 09:30 is a repeatable New York time-of-day reference, **not an exchange
open**; BTC trades continuously. Gold and BTC results remain separate.

The three tested playbooks are:
1. Premarket high/low breakout, followed by a retest and a directional close.
2. In a prior-day uptrend, the last bearish 5-minute candle becomes a support
   zone; in a downtrend, the last bullish candle becomes resistance. A retest and
   close back through the far edge is required.
3. Breakout and retest of the first 5-minute New York opening range.

Signals are confirmed on a completed bar and entered at the next 5-minute bar
open. Stops are placed beyond the retest/candle structure with a 0.10 x ATR(14)
buffer; target is 2R. Only one position per playbook/variant is held at a time.
A bar touching stop and target is conservatively counted as a stop.

## Filters and metrics

Each playbook is reported in three variants:
- `base`: the core playbook without the additional A+ filter.
- `a_plus`: the prior completed daily EMA20/EMA50 direction, intraday EMA20/EMA50
  and ADX(14) >= 18 direction, and signal candle body/range >= 0.40 must agree.
- `a_plus_fast_exit`: same A+ filter, plus an experimental exit after three
  5-minute bars if maximum favorable excursion has not reached 0.50R.

The daily bias is calculated from the prior completed New York calendar day only;
the current day's close is never used to decide its own bias. Intraday ranging
conditions are classified as RANGE and excluded from the A+ variant.

Each variant reports total, BUY-only and SELL-only results: trade count, win rate,
gross R, net R after 0.00R/0.05R/0.10R per-trade cost assumptions, expectancy,
profit factor, maximum drawdown, average holding time and median bars to +0.5R.
Cost values are sensitivity assumptions, not observed broker spread/commission.

## Important limitations

- The existing common historical provider supports 5-minute candles here. This
  experiment therefore uses 5-minute entries; it does not implement the original
  idea of executing on 1-minute candles.
- The 09:30 BTC session is synthetic and should not be interpreted as a true
  market opening.
- The current XAUUSD feed has a neutral proxy for volume; volume is not used by
  these playbooks.
- The default 30,000 bars may not be available from every provider. Each report
  shows requested/received bars, date coverage, source and errors. A partial
  sample is not proof of profitability or a robust edge.
- The quick-failure exit is an experimental variant, not a universal rule.
- These are hypothesis tests. Use a chronological out-of-sample period that was
  not used to choose rules before considering any change to the live bot.

No order is sent to MT5 and no production database or signal is modified.
