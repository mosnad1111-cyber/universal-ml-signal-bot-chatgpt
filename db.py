import os
import sqlite3
import threading
import json
from datetime import datetime, timezone


class DB:
    """Signal storage with Render PostgreSQL support and SQLite fallback.

    DATABASE_URL selects PostgreSQL. Without it, the original local SQLite
    backend is retained for development and unit tests. A configured but
    unreachable PostgreSQL database is a startup error; the app never silently
    falls back to ephemeral SQLite in production.
    """

    def __init__(self, path):
        self.path = path
        self.database_url = os.getenv("DATABASE_URL", "").strip()
        self.backend = "postgres" if self.database_url.startswith(
            ("postgres://", "postgresql://")
        ) else "sqlite"
        if self.database_url and self.backend != "postgres":
            raise ValueError("DATABASE_URL must use postgres:// or postgresql://")

        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.lock = threading.RLock()

        if self.backend == "postgres":
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as exc:
                raise RuntimeError(
                    "DATABASE_URL is configured but psycopg is not installed"
                ) from exc
            self._psycopg = psycopg
            self._dict_row = dict_row
            self._init_postgres()
        else:
            self._init_sqlite()

    def _conn(self):
        if self.backend == "postgres":
            return self._psycopg.connect(
                self.database_url,
                row_factory=self._dict_row,
                connect_timeout=10,
                application_name="my-xgboost-bot",
            )
        c = sqlite3.connect(
            self.path, timeout=30, check_same_thread=False
        )
        c.row_factory = sqlite3.Row
        return c

    def _adapt_sql(self, sql):
        # All DB parameters in this module use qmark placeholders. Convert
        # them for psycopg while keeping the SQLite queries unchanged.
        return sql.replace("?", "%s") if self.backend == "postgres" else sql

    def _execute(self, conn, sql, params=()):
        return conn.execute(self._adapt_sql(sql), params)

    def _init_sqlite(self):
        with self._conn() as c:
            c.executescript(
                """CREATE TABLE IF NOT EXISTS signals(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                direction TEXT NOT NULL,
                entry REAL NOT NULL,
                sl REAL NOT NULL,
                tp REAL NOT NULL,
                score REAL,
                ai_prob REAL,
                status TEXT NOT NULL,
                result TEXT,
                created_at TEXT NOT NULL,
                activated_at TEXT,
                closed_at TEXT,
                entry_time TEXT,
                close_price REAL,
                wave_key TEXT,
                candle_time TEXT,
                features_json TEXT,
                UNIQUE(symbol,timeframe,wave_key));
                CREATE TABLE IF NOT EXISTS settings(
                key TEXT PRIMARY KEY,value TEXT);
                CREATE TABLE IF NOT EXISTS paper_trades(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trial_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                strategy TEXT NOT NULL,
                direction TEXT NOT NULL,
                entry REAL NOT NULL,
                sl REAL NOT NULL,
                tp REAL NOT NULL,
                score REAL,
                ai_prob REAL,
                status TEXT NOT NULL,
                result TEXT,
                created_at TEXT NOT NULL,
                candle_time TEXT NOT NULL,
                activated_at TEXT,
                activation_candle_time TEXT,
                closed_at TEXT,
                close_price REAL,
                UNIQUE(trial_id,symbol,timeframe,candle_time));
                CREATE INDEX IF NOT EXISTS idx_paper_trial_status ON paper_trades(trial_id,status);
                CREATE TABLE IF NOT EXISTS scan_audit(
                id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
                candle_time TEXT, scanned_at TEXT NOT NULL, best_direction TEXT, reject_reason TEXT,
                buy_ai REAL, sell_ai REAL, buy_score REAL, sell_score REAL, context_trend INTEGER,
                diagnostics_json TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS idx_scan_audit_symbol_tf_time ON scan_audit(symbol,timeframe,scanned_at);"""
            )
            cols = {r[1] for r in c.execute("PRAGMA table_info(signals)")}
            if "features_json" not in cols:
                c.execute("ALTER TABLE signals ADD COLUMN features_json TEXT")
            if "activation_candle_time" not in cols:
                c.execute("ALTER TABLE signals ADD COLUMN activation_candle_time TEXT")

    def _init_postgres(self):
        with self._conn() as c:
            c.execute(
                """CREATE TABLE IF NOT EXISTS signals(
                    id BIGSERIAL PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    entry DOUBLE PRECISION NOT NULL,
                    sl DOUBLE PRECISION NOT NULL,
                    tp DOUBLE PRECISION NOT NULL,
                    score DOUBLE PRECISION,
                    ai_prob DOUBLE PRECISION,
                    status TEXT NOT NULL,
                    result TEXT,
                    created_at TEXT NOT NULL,
                    activated_at TEXT,
                    closed_at TEXT,
                    entry_time TEXT,
                    close_price DOUBLE PRECISION,
                    wave_key TEXT,
                    candle_time TEXT,
                    features_json TEXT,
                    activation_candle_time TEXT,
                    UNIQUE(symbol,timeframe,wave_key)
                )"""
            )
            c.execute(
                """CREATE TABLE IF NOT EXISTS settings(
                    key TEXT PRIMARY KEY,
                    value TEXT
                )"""
            )
            c.execute(
                """CREATE TABLE IF NOT EXISTS paper_trades(
                    id BIGSERIAL PRIMARY KEY,
                    trial_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    entry DOUBLE PRECISION NOT NULL,
                    sl DOUBLE PRECISION NOT NULL,
                    tp DOUBLE PRECISION NOT NULL,
                    score DOUBLE PRECISION,
                    ai_prob DOUBLE PRECISION,
                    status TEXT NOT NULL,
                    result TEXT,
                    created_at TEXT NOT NULL,
                    candle_time TEXT NOT NULL,
                    activated_at TEXT,
                    activation_candle_time TEXT,
                    closed_at TEXT,
                    close_price DOUBLE PRECISION,
                    UNIQUE(trial_id,symbol,timeframe,candle_time)
                )"""
            )
            c.execute(
                "CREATE INDEX IF NOT EXISTS idx_paper_trial_status ON paper_trades(trial_id,status)"
            )
            c.execute("""CREATE TABLE IF NOT EXISTS scan_audit(
                id BIGSERIAL PRIMARY KEY, symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
                candle_time TEXT, scanned_at TEXT NOT NULL, best_direction TEXT, reject_reason TEXT,
                buy_ai DOUBLE PRECISION, sell_ai DOUBLE PRECISION, buy_score DOUBLE PRECISION,
                sell_score DOUBLE PRECISION, context_trend INTEGER, diagnostics_json TEXT NOT NULL
            )""")
            c.execute("CREATE INDEX IF NOT EXISTS idx_scan_audit_symbol_tf_time ON scan_audit(symbol,timeframe,scanned_at)")
            c.execute(
                "ALTER TABLE signals ADD COLUMN IF NOT EXISTS features_json TEXT"
            )
            c.execute(
                "ALTER TABLE signals ADD COLUMN IF NOT EXISTS activation_candle_time TEXT"
            )
            c.execute(
                "CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status)"
            )
            c.execute(
                """CREATE INDEX IF NOT EXISTS idx_signals_symbol_tf_status
                   ON signals(symbol,timeframe,status)"""
            )


    def record_scan_event(self, diagnostic):
        """Persist a compact audit row for accepted and rejected scan attempts."""
        now = datetime.now(timezone.utc).isoformat()
        payload = json.dumps(diagnostic, ensure_ascii=False, default=str)
        values = (
            str(diagnostic.get("symbol", "")), str(diagnostic.get("timeframe", "")),
            str(diagnostic.get("candle_time") or ""), now,
            str(diagnostic.get("best_direction") or ""),
            str(diagnostic.get("reject") or ""),
            float(diagnostic["buy_ai"]) if diagnostic.get("buy_ai") is not None else None,
            float(diagnostic["sell_ai"]) if diagnostic.get("sell_ai") is not None else None,
            float(diagnostic["buy_score"]) if diagnostic.get("buy_score") is not None else None,
            float(diagnostic["sell_score"]) if diagnostic.get("sell_score") is not None else None,
            int(diagnostic.get("context_trend", 0) or 0), payload,
        )
        with self.lock, self._conn() as c:
            self._execute(c, """INSERT INTO scan_audit(
                symbol,timeframe,candle_time,scanned_at,best_direction,reject_reason,
                buy_ai,sell_ai,buy_score,sell_score,context_trend,diagnostics_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", values)

    def delete_paper_trial(self, trial_id):
        """Explicitly retire one paper experiment and its settings; never touches live signals."""
        keys = (
            "paper_trial_start_utc", "paper_trial_end_utc",
            "paper_trial_model_trained_at", "paper_trial_data_source",
            "paper_trial_model_last_error", "paper_trial_report_sent",
        )
        with self.lock, self._conn() as c:
            self._execute(c, "DELETE FROM paper_trades WHERE trial_id=?", (trial_id,))
            for key in keys:
                self._execute(c, "DELETE FROM settings WHERE key=?", (key,))

    def create(self, s):
        now = datetime.now(timezone.utc).isoformat()
        values = (
            s["symbol"], s["timeframe"], s["direction"], s["entry"],
            s["sl"], s["tp"], s["score"], s["ai_prob"], "WAITING", now,
            s["wave_key"], s["candle_time"],
            json.dumps(s.get("features", {})),
        )
        query = """INSERT INTO signals(
            symbol,timeframe,direction,entry,sl,tp,score,ai_prob,status,
            created_at,wave_key,candle_time,features_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)"""
        with self.lock, self._conn() as c:
            if self.backend == "postgres":
                row = self._execute(
                    c,
                    query + " ON CONFLICT(symbol,timeframe,wave_key) DO NOTHING RETURNING id",
                    values,
                ).fetchone()
                return int(row["id"]) if row else None
            try:
                cur = self._execute(c, query, values)
                return cur.lastrowid
            except sqlite3.IntegrityError:
                return None

    def _legacy_cleanup(self):
        with self._conn() as c:
            if self._execute(
                c, "SELECT 1 FROM settings WHERE key=?", ("signal_delivery_fix_v1",)
            ).fetchone():
                return
            now = datetime.now(timezone.utc).isoformat()
            self._execute(
                c,
                "UPDATE signals SET status='CANCELLED',result='LEGACY_NOT_DELIVERED',closed_at=? WHERE status IN ('WAITING','ACTIVE')",
                (now,),
            )
            self._execute(
                c,
                "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO NOTHING",
                ("signal_delivery_fix_v1", "1"),
            )

    def open_for(self, symbol, tf):
        with self.lock:
            self._legacy_cleanup()
            with self._conn() as c:
                return self._execute(
                    c,
                    "SELECT * FROM signals WHERE symbol=? AND timeframe=? AND status IN ('WAITING','ACTIVE') ORDER BY id DESC LIMIT 1",
                    (symbol, tf),
                ).fetchone()

    def all_open(self):
        with self.lock:
            self._legacy_cleanup()
            with self._conn() as c:
                return self._execute(
                    c,
                    "SELECT * FROM signals WHERE status IN ('WAITING','ACTIVE') ORDER BY id",
                ).fetchall()

    def activate(self, sid, price, candle_time=None):
        now = datetime.now(timezone.utc).isoformat()
        with self.lock, self._conn() as c:
            self._execute(
                c,
                "UPDATE signals SET status='ACTIVE',activated_at=?,entry_time=?,activation_candle_time=? WHERE id=? AND status='WAITING'",
                (now, now, str(candle_time) if candle_time is not None else None, sid),
            )

    def set_activation_candle(self, sid, candle_time):
        with self.lock, self._conn() as c:
            self._execute(
                c,
                "UPDATE signals SET activation_candle_time=? WHERE id=? AND status='ACTIVE' AND activation_candle_time IS NULL",
                (str(candle_time), sid),
            )

    def close(self, sid, result, price):
        now = datetime.now(timezone.utc).isoformat()
        with self.lock, self._conn() as c:
            self._execute(
                c,
                "UPDATE signals SET status='CLOSED',result=?,closed_at=?,close_price=? WHERE id=? AND status IN ('WAITING','ACTIVE')",
                (result, now, price, sid),
            )

    def cancel(self, sid, result="NOT_SENT"):
        now = datetime.now(timezone.utc).isoformat()
        with self.lock, self._conn() as c:
            self._execute(
                c,
                "UPDATE signals SET status='CANCELLED',result=?,closed_at=? WHERE id=? AND status IN ('WAITING','ACTIVE')",
                (result, now, sid),
            )

    def get_setting(self, key, default=None):
        with self.lock, self._conn() as c:
            r = self._execute(
                c, "SELECT value FROM settings WHERE key=?", (key,)
            ).fetchone()
            return r["value"] if r else default

    def set_setting(self, key, value):
        with self.lock, self._conn() as c:
            self._execute(
                c,
                "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)),
            )

    def closed_stats(self):
        with self.lock, self._conn() as c:
            rows = self._execute(
                c,
                "SELECT result,entry,sl,tp,direction,close_price FROM signals WHERE status='CLOSED' AND result IN ('TP','SL','TIMEOUT')",
            ).fetchall()
        targets = sum(r["result"] == "TP" for r in rows)
        stops = sum(r["result"] == "SL" for r in rows)
        timeouts = sum(r["result"] == "TIMEOUT" for r in rows)
        net_r = 0.0
        profitable = losing = valid = 0
        for r in rows:
            risk = abs(float(r["entry"]) - float(r["sl"]))
            if risk <= 0:
                continue
            valid += 1
            realized_r = 0.0
            if r["result"] == "TP":
                realized_r = abs(float(r["tp"]) - float(r["entry"])) / risk
            elif r["result"] == "SL":
                realized_r = -1.0
            elif r["close_price"] is not None:
                move = (
                    float(r["close_price"]) - float(r["entry"])
                    if r["direction"] == "BUY"
                    else float(r["entry"]) - float(r["close_price"])
                )
                realized_r = move / risk
            net_r += realized_r
            if realized_r > 0:
                profitable += 1
            elif realized_r < 0:
                losing += 1
        return valid, targets, stops, timeouts, net_r, profitable, losing

    def recent(self, n=10):
        with self.lock, self._conn() as c:
            return self._execute(
                c, "SELECT * FROM signals ORDER BY id DESC LIMIT ?", (n,)
            ).fetchall()

    def paper_create(self, trade):
        """Insert an isolated paper-only trade; never touches the live signals ledger."""
        now = datetime.now(timezone.utc).isoformat()
        values = (
            trade["trial_id"], trade["symbol"], trade["timeframe"],
            trade["strategy"], trade["direction"], float(trade["entry"]),
            float(trade["sl"]), float(trade["tp"]), float(trade["score"]),
            float(trade["ai_prob"]), "WAITING", now, str(trade["candle_time"]),
        )
        query = """INSERT INTO paper_trades(
            trial_id,symbol,timeframe,strategy,direction,entry,sl,tp,score,ai_prob,
            status,created_at,candle_time
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)"""
        with self.lock, self._conn() as c:
            if self.backend == "postgres":
                row = self._execute(
                    c,
                    query + " ON CONFLICT(trial_id,symbol,timeframe,candle_time) DO NOTHING RETURNING id",
                    values,
                ).fetchone()
                return int(row["id"]) if row else None
            cur = self._execute(c, "INSERT OR IGNORE" + query[len("INSERT"):], values)
            return int(cur.lastrowid) if cur.rowcount else None

    def paper_open(self, trial_id, symbol, timeframe):
        with self.lock, self._conn() as c:
            return self._execute(
                c,
                """SELECT * FROM paper_trades
                   WHERE trial_id=? AND symbol=? AND timeframe=?
                     AND status IN ('WAITING','ACTIVE')
                   ORDER BY id DESC LIMIT 1""",
                (trial_id, symbol, timeframe),
            ).fetchone()

    def paper_all(self, trial_id):
        with self.lock, self._conn() as c:
            return self._execute(
                c,
                "SELECT * FROM paper_trades WHERE trial_id=? ORDER BY created_at,id",
                (trial_id,),
            ).fetchall()

    def paper_activate(self, trade_id, candle_time):
        now = datetime.now(timezone.utc).isoformat()
        with self.lock, self._conn() as c:
            self._execute(
                c,
                """UPDATE paper_trades SET status='ACTIVE',activated_at=?,
                   activation_candle_time=? WHERE id=? AND status='WAITING'""",
                (now, str(candle_time), trade_id),
            )

    def paper_set_activation_candle(self, trade_id, candle_time):
        with self.lock, self._conn() as c:
            self._execute(
                c,
                """UPDATE paper_trades SET activation_candle_time=?
                   WHERE id=? AND status='ACTIVE' AND activation_candle_time IS NULL""",
                (str(candle_time), trade_id),
            )

    def paper_close(self, trade_id, result, price):
        now = datetime.now(timezone.utc).isoformat()
        with self.lock, self._conn() as c:
            self._execute(
                c,
                """UPDATE paper_trades SET status='CLOSED',result=?,closed_at=?,close_price=?
                   WHERE id=? AND status IN ('WAITING','ACTIVE')""",
                (result, now, float(price), trade_id),
            )
