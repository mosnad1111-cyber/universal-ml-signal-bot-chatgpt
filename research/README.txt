Research-only TVC:GOLD previous-day high/low backtest

Run from repository root:
  python research/pdh_pdl_breakout_backtest.py --bars 5000 --cost-points 0.5

Optional UTC date filters:
  --start 2026-07-01 --end 2026-10-01

Outputs:
  research/results/summary.json
  research/results/successful_breakouts.csv
  research/results/false_breakouts.csv

Rules:
- Build PDH/PDL from completed 1h candles grouped by New York trading day 17:00–17:00 (DST-aware).
- Signal entries only during 09:30–16:00 America/New_York.
- Retest tolerance and SL buffer are each 0.10 x 5m ATR(14).
- Target is 2R; confirmation is an engulfing or rejection candle.
- A candle touching both SL and TP is conservatively counted as SL.
- One open trade per scenario; flatten by the 15:55 NY candle.
- Default round-trip cost is 0.5 gold price points, an assumption rather than a broker quote.

This research script does not alter production signal logic. TradingView may return fewer bars than requested. Results require sufficient data coverage and should be checked out-of-sample before any live use.