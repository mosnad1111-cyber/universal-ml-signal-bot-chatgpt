from AlgorithmImports import *

import csv
import json
import math
import os
import re
from datetime import datetime, timedelta, timezone
from QuantConnect import SubscriptionTransportMedium, TimeZones
from QuantConnect.Data import FileFormat, SubscriptionDataSource
from QuantConnect.Securities import SecurityExchangeHours, SymbolProperties
from decimal import Decimal


INPUT_DIR = "/Lean/Algorithm.Python/input"
REPORT_PATH = "/Results/lean_report.json"
READER_STATS = {"parsed_rows": 0, "invalid_rows": 0, "source_requests": 0}
READER_TIMESTAMPS = set()
READER_SOURCE_DATES = set()
READER_DAILY_COUNTS = {}
DELIVERED_DAILY_COUNTS = {}
DELIVERED_TIME_SAMPLES = []


class CsvOhlcv(PythonData):
    """Local chronological OHLCV stream generated from the completed market snapshot."""

    def get_source(self, config, date, is_live_mode):
        # Use one chronological stream for the whole snapshot. LEAN changes its
        # daily source reader at exchange-date boundaries and may discard custom
        # bars whose EndTime crosses that boundary. A single source avoids that
        # rollover path without changing any market timestamps or OHLC values.
        READER_STATS["source_requests"] += 1
        READER_SOURCE_DATES.add(date.strftime("%Y%m%d"))
        path = os.path.join(INPUT_DIR, "bars.csv")
        if not os.path.isfile(path):
            raise FileNotFoundError(f"LEAN OHLC stream not found: {path}")
        return SubscriptionDataSource(path, SubscriptionTransportMedium.LOCAL_FILE, FileFormat.CSV)

    def data_time_zone(self):
        return TimeZones.UTC

    def reader(self, config, line, date, is_live_mode):
        if not line or line.startswith("time,"):
            return None
        fields = next(csv.reader([line]))
        if len(fields) < 7:
            READER_STATS["invalid_rows"] += 1
            return None
        try:
            stamp = datetime.fromisoformat(fields[0])
            period_minutes = int(fields[6])
            open_price, high, low, close, volume = map(float, fields[1:6])
        except (TypeError, ValueError):
            READER_STATS["invalid_rows"] += 1
            return None

        READER_STATS["parsed_rows"] += 1
        READER_TIMESTAMPS.add(stamp)
        day = stamp.strftime("%Y%m%d")
        READER_DAILY_COUNTS[day] = READER_DAILY_COUNTS.get(day, 0) + 1
        bar = CsvOhlcv()
        bar.symbol = config.symbol
        bar.time = stamp
        # EndTime is the last included instant of this OHLC interval. Keeping
        # it one microsecond inside the bar prevents the final candle of a
        # backtest day from ending just beyond LEAN's end-of-day frontier.
        bar.end_time = stamp + timedelta(minutes=period_minutes) - timedelta(microseconds=1)
        bar.value = close
        bar["Open"] = open_price
        bar["High"] = high
        bar["Low"] = low
        bar["Close"] = close
        bar["Volume"] = volume
        return bar


class TaggedPriceFillModel(ImmediateFillModel):
    """LEAN fills market orders at the OHLC level selected by the algorithm."""

    def market_fill(self, asset, order):
        fill = super().market_fill(asset, order)
        if order.status == OrderStatus.CANCELED:
            return fill
        match = re.search(r"(?:^|;)fill_price=([0-9]+(?:\.[0-9]+)?)", order.tag or "")
        if match and fill.status == OrderStatus.FILLED:
            fill.fill_price = Decimal(match.group(1))
        return fill


class RiskBasedFeeModel(FeeModel):
    """Charge half of the declared round-trip R cost on each filled side."""

    def get_order_fee(self, parameters):
        tag = parameters.order.tag or ""
        risk_match = re.search(r"(?:^|;)risk=([0-9]+(?:\.[0-9]+)?)", tag)
        fee_match = re.search(r"(?:^|;)fee_r=([0-9]+(?:\.[0-9]+)?)", tag)
        if not risk_match or not fee_match:
            return OrderFee(CashAmount(0, "USD"))
        risk = Decimal(risk_match.group(1))
        fee_r = Decimal(fee_match.group(1))
        amount = risk * fee_r / Decimal("2") * Decimal(str(abs(parameters.order.quantity)))
        return OrderFee(CashAmount(amount, "USD"))


class LeanBacktestAlgorithm(QCAlgorithm):
    """Replays decision-only signals through LEAN's transaction/fill/fee pipeline."""

    def initialize(self):
        with open(os.path.join(INPUT_DIR, "metadata.json"), encoding="utf-8") as handle:
            self.metadata = json.load(handle)
        with open(os.path.join(INPUT_DIR, "signals.json"), encoding="utf-8") as handle:
            raw_events = json.load(handle)

        start = self._parse_utc(self.metadata["start"])
        end = self._parse_utc(self.metadata["end"])
        current_utc_day = datetime.now(timezone.utc).date()
        if end.date() >= current_utc_day:
            raise ValueError(
                f"Prepared LEAN history ends on {end.date()}, but LEAN only accepts "
                f"completed days before current UTC day {current_utc_day}."
            )
        # Snapshot timestamps and the consolidated CSV stream are UTC. LEAN defaults
        # algorithm time to New York, which shifts the requested UTC file date
        # and can exclude midnight UTC bars from the backtest window.
        self.set_time_zone(TimeZones.UTC)
        self.set_start_date(start.year, start.month, start.day)
        self.set_end_date(end.year, end.month, end.day)
        self.initial_cash_usd = float(self.metadata.get("lean_initial_cash_usd", 100000.0))
        max_ohlc_price = float(self.metadata.get("max_ohlc_price", 0.0))
        cash_buffer = float(self.metadata.get("lean_cash_buffer_multiplier", 1.5))
        if (
            not math.isfinite(self.initial_cash_usd)
            or not math.isfinite(max_ohlc_price)
            or not math.isfinite(cash_buffer)
            or self.initial_cash_usd <= 0
            or max_ohlc_price <= 0
            or cash_buffer < 1.0
            or self.initial_cash_usd < max_ohlc_price * cash_buffer
        ):
            raise ValueError("Invalid LEAN initial cash/buying-power metadata for one-unit OHLC replay.")
        # Order quantity remains exactly +/-1 unit. Cash is sized from the
        # observed OHLC maximum so LEAN's buying-power model can validate that
        # unit across the entire real historical window (including BTC >$100k).
        self.set_cash(self.initial_cash_usd)

        tf = self.metadata["timeframe"]
        # Each CSV row is already a complete OHLC bar with its true interval in
        # EndTime. Use tick-level delivery so LEAN does not quantize/coalesce
        # custom data to minute/hour subscription frontiers.
        # The PyObject+timezone AddData overload can leave the custom security's
        # exchange clock on New York in some LEAN Python bindings. Register both
        # symbol properties and an explicitly UTC, always-open exchange-hours
        # entry so the subscription data clock and exchange clock are identical.
        properties = SymbolProperties.GetDefault("USD")
        exchange_hours = SecurityExchangeHours.AlwaysOpen(TimeZones.UTC)
        security = self.add_data(
            CsvOhlcv,
            "BOT_OHLCV",
            properties,
            exchange_hours,
            Resolution.TICK,
            False,
        )
        self.symbol = security.symbol
        security.set_fill_model(TaggedPriceFillModel())
        security.set_fee_model(RiskBasedFeeModel())

        self.fee_r = float(self.metadata.get("fee_r", 0.05))
        self.events = {}
        for event in raw_events:
            signal_time = self._parse_utc(event["signal_time"])
            self.events[signal_time] = event

        self.pending = None
        self.active = None
        self.trades = []
        self.unfilled = 0
        self.unresolved = 0
        self.order_errors = []
        self.received_bars = 0

        self.debug(
            f"LEAN_HISTORY_WINDOW start={start.isoformat()} end={end.isoformat()} "
            f"excluded_current_day_bars={self.metadata.get('end_window_excluded_bars', 0)}"
        )
        self.debug("LEAN_BACKTEST_INITIALIZED")

    @staticmethod
    def _parse_utc(value):
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed

    @staticmethod
    def _tag(fill_price, risk, fee_r):
        return f"fill_price={float(fill_price):.10f};risk={float(risk):.10f};fee_r={float(fee_r):.6f}"

    def _submit_market(self, quantity, fill_price, risk, side):
        tag = self._tag(fill_price, risk, self.fee_r) + f";side={side}"
        ticket = self.market_order(self.symbol, quantity, False, tag)
        if ticket.status != OrderStatus.FILLED:
            message = f"LEAN order not filled: side={side}, status={ticket.status}"
            self.order_errors.append(message)
            self.error(message)
            return None
        return float(ticket.average_fill_price)

    @staticmethod
    def _levels(event, high, low, direction):
        if direction == "BUY":
            return low <= float(event["sl"]), high >= float(event["tp"])
        return high >= float(event["sl"]), low <= float(event["tp"])

    @staticmethod
    def _entry_touched(event, open_price, high, low):
        entry = float(event["entry"])
        if event["direction"] == "BUY":
            return open_price <= entry or low <= entry
        return open_price >= entry or high >= entry

    @staticmethod
    def _entry_fill(event, open_price):
        entry = float(event["entry"])
        if event["direction"] == "BUY" and open_price <= entry:
            return float(open_price)
        if event["direction"] == "SELL" and open_price >= entry:
            return float(open_price)
        return entry

    @staticmethod
    def _exit_fill(event, open_price, outcome):
        level = float(event["sl"] if outcome in ("SL", "AMBIGUOUS_ENTRY_CANDLE") else event["tp"])
        if outcome == "SL":
            if event["direction"] == "BUY" and open_price < level:
                return float(open_price)
            if event["direction"] == "SELL" and open_price > level:
                return float(open_price)
        elif outcome == "TP":
            if event["direction"] == "BUY" and open_price > level:
                return float(open_price)
            if event["direction"] == "SELL" and open_price < level:
                return float(open_price)
        return level

    def _close_trade(self, event, activation_time, exit_time, exit_price, outcome, entry_fill):
        direction = event["direction"]
        quantity = -1 if direction == "BUY" else 1
        close_fill = self._submit_market(quantity, exit_price, event["risk"], "exit")
        if close_fill is None:
            self.unresolved += 1
            self.active = None
            return
        signed_move = (close_fill - entry_fill) if direction == "BUY" else (entry_fill - close_fill)
        risk = max(float(event["risk"]), 1e-12)
        gross_r = signed_move / risk
        trade = {
            "signal_time": event["signal_time"],
            "activation_time": activation_time.isoformat(),
            "exit_time": exit_time.isoformat(),
            "direction": direction,
            "result": outcome,
            "signal_entry": float(event["entry"]),
            "entry_fill": float(entry_fill),
            "sl": float(event["sl"]),
            "tp": float(event["tp"]),
            "exit_fill": float(close_fill),
            "gross_r": float(gross_r),
            "fee_r": float(self.fee_r),
            "net_r": float(gross_r - self.fee_r),
        }
        self.trades.append(trade)
        self.active = None

    def on_data(self, data):
        bar = data.get(self.symbol)
        if bar is None:
            return
        self.received_bars += 1
        # LEAN delivers this custom OHLC point at its EndTime. The algorithm
        # clock is UTC, so reconstruct the original bar OPEN timestamp from
        # that delivery time and the declared interval. PythonData.Time can be
        # converted through the security's exchange clock and must not be used
        # to match the original UTC XGBoost signal_time.
        algorithm_end = self.time.replace(tzinfo=None)
        period_minutes = int(self.metadata["period_minutes"])
        current = (
            algorithm_end
            - timedelta(minutes=period_minutes)
            + timedelta(milliseconds=1)
        )
        raw_data_time = bar.time.replace(tzinfo=None)
        day = current.strftime("%Y%m%d")
        DELIVERED_DAILY_COUNTS[day] = DELIVERED_DAILY_COUNTS.get(day, 0) + 1
        if len(DELIVERED_TIME_SAMPLES) < 5:
            DELIVERED_TIME_SAMPLES.append({
                "raw_data_time": raw_data_time.isoformat(),
                "replay_bar_open_time": current.isoformat(),
                "algorithm_delivery_end_time": algorithm_end.isoformat(),
            })
        open_price = float(bar["Open"])
        high = float(bar["High"])
        low = float(bar["Low"])

        # An active position is resolved before considering any later signal.
        if self.active is not None:
            event = self.active["event"]
            hit_sl, hit_tp = self._levels(event, high, low, event["direction"])
            outcome = "SL" if hit_sl else ("TP" if hit_tp else None)
            if outcome:
                exit_price = self._exit_fill(event, open_price, outcome)
                self._close_trade(
                    event, self.active["activation_time"], current,
                    exit_price, outcome, self.active["entry_fill"],
                )
            return

        # A setup is eligible for entry only on candles strictly after its signal candle.
        if self.pending is not None:
            event = self.pending
            if current <= self._parse_utc(event["signal_time"]):
                return
            entry = float(event["entry"])
            if not self._entry_touched(event, open_price, high, low):
                return

            entry_fill = self._entry_fill(event, open_price)
            quantity = 1 if event["direction"] == "BUY" else -1
            actual_entry = self._submit_market(quantity, entry_fill, event["risk"], "entry")
            if actual_entry is None:
                self.pending = None
                self.unresolved += 1
                return

            hit_sl, hit_tp = self._levels(event, high, low, event["direction"])
            if hit_sl or hit_tp:
                outcome = "SL" if hit_sl else "AMBIGUOUS_ENTRY_CANDLE"
                exit_price = float(event["sl"])
                self._close_trade(event, current, current, exit_price, outcome, actual_entry)
            else:
                self.active = {"event": event, "activation_time": current, "entry_fill": actual_entry}
            self.pending = None
            return

        # Signal becomes known at the close of this bar; do not fill it on this bar.
        event = self.events.get(current)
        if event is not None:
            self.pending = event

    def on_end_of_algorithm(self):
        if self.pending is not None:
            self.unfilled += 1
        if self.active is not None:
            self.unresolved += 1

        gross = sum(item["gross_r"] for item in self.trades)
        fees = sum(item["fee_r"] for item in self.trades)
        net = sum(item["net_r"] for item in self.trades)
        wins = sum(item["net_r"] > 0 for item in self.trades)
        losses = sum(item["net_r"] < 0 for item in self.trades)
        report = {
            "engine": "QuantConnect LEAN open-source engine",
            "request_id": self.metadata["request_id"],
            "asset_key": self.metadata["asset_key"],
            "symbol": self.metadata["symbol"],
            "timeframe": self.metadata["timeframe"],
            "strategy": self.metadata.get("strategy", "baseline"),
            "initial_cash_usd": float(self.initial_cash_usd),
            "max_ohlc_price": float(self.metadata.get("max_ohlc_price", 0.0)),
            "position_quantity_units": float(self.metadata.get("lean_position_units", 1.0)),
            "cash_buffer_multiplier": float(self.metadata.get("lean_cash_buffer_multiplier", 1.5)),
            "market_data_source": self.metadata.get("market_data_source", ""),
            "history_start_utc": self.metadata.get("start", ""),
            "history_end_utc": self.metadata.get("end", ""),
            "end_before_exclusive_utc": self.metadata.get("end_before_exclusive_utc", ""),
            "excluded_current_day_bars": int(self.metadata.get("end_window_excluded_bars", 0)),
            "bars": int(self.metadata["bars_count"]),
            "received_bars": int(self.received_bars),
            "reader_parsed_rows": int(READER_STATS["parsed_rows"]),
            "reader_invalid_rows": int(READER_STATS["invalid_rows"]),
            "reader_unique_timestamps": int(len(READER_TIMESTAMPS)),
            "reader_duplicate_timestamps": int(READER_STATS["parsed_rows"] - len(READER_TIMESTAMPS)),
            "reader_source_requests": int(READER_STATS["source_requests"]),
            "reader_source_dates": sorted(READER_SOURCE_DATES),
            "reader_daily_counts": dict(sorted(READER_DAILY_COUNTS.items())),
            "on_data_daily_counts": dict(sorted(DELIVERED_DAILY_COUNTS.items())),
            "on_data_time_samples": list(DELIVERED_TIME_SAMPLES),
            "lean_total_fees_usd": float(self.portfolio.total_fees),
            "signals": int(self.metadata["signals_count"]),
            "trades": len(self.trades),
            "wins": wins,
            "losses": losses,
            "unfilled_setups": self.unfilled,
            "unresolved_trades": self.unresolved,
            "gross_r": float(gross),
            "fees_r": float(fees),
            "net_r": float(net),
            "net_r_at_0_10_cost": float(sum(item["gross_r"] - 0.10 for item in self.trades)),
            "win_rate": 100.0 * wins / len(self.trades) if self.trades else 0.0,
            "expectancy_r": net / len(self.trades) if self.trades else 0.0,
            "trades_detail": self.trades,
            "order_errors": self.order_errors,
            "fee_assumption": "0.05R total round-trip cost per filled trade; not broker-specific fees",
            "fill_assumption": "OHLC touch; favorable opening gaps improve fills and adverse stop gaps worsen fills",
        }
        with open(REPORT_PATH, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
        if self.received_bars != int(self.metadata["bars_count"]):
            self.error(f"LEAN OHLC ingestion mismatch: expected={self.metadata['bars_count']} received={self.received_bars}")
        self.debug("LEAN_BACKTEST_COMPLETED")
