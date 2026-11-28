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
                diagnostics_json TEXT NOT NULL,
                buy_entry REAL, buy_sl REAL, buy_tp REAL, sell_entry REAL, sell_sl REAL, sell_tp REAL,
                buy_outcome TEXT, sell_outcome TEXT, buy_evaluated_bars INTEGER DEFAULT 0, sell_evaluated_bars INTEGER DEFAULT 0,
                buy_candidate_ai REAL, buy_candidate_score REAL,
                sell_candidate_ai REAL, sell_candidate_score REAL);
                CREATE INDEX IF NOT EXISTS idx_scan_audit_symbol_tf_time ON scan_audit(symbol,timeframe,scanned_at);
                CREATE INDEX IF NOT EXISTS idx_scan_audit_scanned_at ON scan_audit(scanned_at);"""
            )
            audit_cols = {r[1] for r in c.execute("PRAGMA table_info(scan_audit)")}
            for col, typ in (("buy_entry","REAL"),("buy_sl","REAL"),("buy_tp","REAL"),("sell_entry","REAL"),("sell_sl","REAL"),("sell_tp","REAL"),("buy_outcome","TEXT"),("sell_outcome","TEXT"),("buy_evaluated_bars","INTEGER DEFAULT 0"),("sell_evaluated_bars","INTEGER DEFAULT 0"),("buy_candidate_ai","REAL"),("buy_candidate_score","REAL"),("sell_candidate_ai","REAL"),("sell_candidate_score","REAL")):
                if col not in audit_cols:
                    c.execute(f"ALTER TABLE scan_audit ADD COLUMN {col} {typ}")
            c.execute("UPDATE scan_audit SET candle_time='' WHERE candle_time IS NULL")
            c.execute("DELETE FROM scan_audit WHERE id NOT IN (SELECT MAX(id) FROM scan_audit GROUP BY symbol,timeframe,candle_time)")
            c.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_scan_audit_candle ON scan_audit(symbol,timeframe,candle_time)")
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
                sell_score DOUBLE PRECISION, context_trend INTEGER, diagnostics_json TEXT NOT NULL,
                buy_entry DOUBLE PRECISION, buy_sl DOUBLE PRECISION, buy_tp DOUBLE PRECISION,
                sell_entry DOUBLE PRECISION, sell_sl DOUBLE PRECISION, sell_tp DOUBLE PRECISION,
                buy_outcome TEXT, sell_outcome TEXT, buy_evaluated_bars INTEGER DEFAULT 0,
                sell_evaluated_bars INTEGER DEFAULT 0,
                buy_candidate_ai DOUBLE PRECISION, buy_candidate_score DOUBLE PRECISION,
                sell_candidate_ai DOUBLE PRECISION, sell_candidate_score DOUBLE PRECISION
            )""")
            for col, typ in (("buy_entry","DOUBLE PRECISION"),("buy_sl","DOUBLE PRECISION"),("buy_tp","DOUBLE PRECISION"),("sell_entry","DOUBLE PRECISION"),("sell_sl","DOUBLE PRECISION"),("sell_tp","DOUBLE PRECISION"),("buy_outcome","TEXT"),("sell_outcome","TEXT"),("buy_evaluated_bars","INTEGER DEFAULT 0"),("sell_evaluated_bars","INTEGER DEFAULT 0"),("buy_candidate_ai","DOUBLE PRECISION"),("buy_candidate_score","DOUBLE PRECISION"),("sell_candidate_ai","DOUBLE PRECISION"),("sell_candidate_score","DOUBLE PRECISION")):
                c.execute(f"ALTER TABLE scan_audit ADD COLUMN IF NOT EXISTS {col} {typ}")
            c.execute("UPDATE scan_audit SET candle_time='' WHERE candle_time IS NULL")
            c.execute("DELETE FROM scan_audit a USING scan_audit b WHERE a.symbol=b.symbol AND a.timeframe=b.timeframe AND a.candle_time=b.candle_time AND a.id < b.id")
            c.execute("CREATE INDEX IF NOT EXISTS idx_scan_audit_symbol_tf_time ON scan_audit(symbol,timeframe,scanned_at)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_scan_audit_scanned_at ON scan_audit(scanned_at)")
            c.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_scan_audit_candle ON scan_audit(symbol,timeframe,candle_time)")
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
        """Upsert one audit snapshot per completed market candle."""
        candle_time = str(diagnostic.get("candle_time") or "")
        if not candle_time:
            return None
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        payload = json.dumps(diagnostic, ensure_ascii=False, default=str)
        candidates = diagnostic.get("shadow_candidates") or {}
        buy = candidates.get("BUY") or {}
        sell = candidates.get("SELL") or {}
        values = (
            str(diagnostic.get("symbol", "")), str(diagnostic.get("timeframe", "")),
            candle_time, now, str(diagnostic.get("best_direction") or ""),
            str(diagnostic.get("reject") or ""),
            float(diagnostic["buy_ai"]) if diagnostic.get("buy_ai") is not None else None,
            float(diagnostic["sell_ai"]) if diagnostic.get("sell_ai") is not None else None,
            float(diagnostic["buy_score"]) if diagnostic.get("buy_score") is not None else None,
            float(diagnostic["sell_score"]) if diagnostic.get("sell_score") is not None else None,
            int(diagnostic.get("context_trend", 0) or 0), payload,
            buy.get("entry"), buy.get("sl"), buy.get("tp"),
            sell.get("entry"), sell.get("sl"), sell.get("tp"),
            buy.get("ai_prob"), buy.get("score"),
            sell.get("ai_prob"), sell.get("score"),
        )
        columns = """symbol,timeframe,candle_time,scanned_at,best_direction,reject_reason,
            buy_ai,sell_ai,buy_score,sell_score,context_trend,diagnostics_json,
            buy_entry,buy_sl,buy_tp,sell_entry,sell_sl,sell_tp,
            buy_candidate_ai,buy_candidate_score,sell_candidate_ai,sell_candidate_score"""
        placeholders = ",".join(["?"] * 22)
        updates = """scanned_at=excluded.scanned_at,best_direction=excluded.best_direction,
            reject_reason=excluded.reject_reason,buy_ai=excluded.buy_ai,sell_ai=excluded.sell_ai,
            buy_score=excluded.buy_score,sell_score=excluded.sell_score,
            context_trend=excluded.context_trend,diagnostics_json=excluded.diagnostics_json,
            buy_entry=COALESCE(scan_audit.buy_entry,excluded.buy_entry),
            buy_sl=COALESCE(scan_audit.buy_sl,excluded.buy_sl),buy_tp=COALESCE(scan_audit.buy_tp,excluded.buy_tp),
            sell_entry=COALESCE(scan_audit.sell_entry,excluded.sell_entry),
            sell_sl=COALESCE(scan_audit.sell_sl,excluded.sell_sl),sell_tp=COALESCE(scan_audit.sell_tp,excluded.sell_tp),
            buy_candidate_ai=CASE WHEN scan_audit.buy_entry IS NULL
                THEN COALESCE(scan_audit.buy_candidate_ai,excluded.buy_candidate_ai)
                ELSE scan_audit.buy_candidate_ai END,
            buy_candidate_score=CASE WHEN scan_audit.buy_entry IS NULL
                THEN COALESCE(scan_audit.buy_candidate_score,excluded.buy_candidate_score)
                ELSE scan_audit.buy_candidate_score END,
            sell_candidate_ai=CASE WHEN scan_audit.sell_entry IS NULL
                THEN COALESCE(scan_audit.sell_candidate_ai,excluded.sell_candidate_ai)
                ELSE scan_audit.sell_candidate_ai END,
            sell_candidate_score=CASE WHEN scan_audit.sell_entry IS NULL
                THEN COALESCE(scan_audit.sell_candidate_score,excluded.sell_candidate_score)
                ELSE scan_audit.sell_candidate_score END"""
        with self.lock, self._conn() as c:
            self._execute(c, f"INSERT INTO scan_audit({columns}) VALUES({placeholders}) ON CONFLICT(symbol,timeframe,candle_time) DO UPDATE SET {updates}", values)
            cleanup = self._execute(c, "SELECT value FROM settings WHERE key=?", ("scan_audit_cleanup_at",)).fetchone()
            cleanup_due = not cleanup or str(cleanup["value"]) < (now_dt - __import__('datetime').timedelta(days=1)).isoformat()
            if cleanup_due:
                cutoff = (now_dt - __import__('datetime').timedelta(days=30)).isoformat()
                self._execute(c, "DELETE FROM scan_audit WHERE scanned_at < ?", (cutoff,))
                self._execute(c, "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", ("scan_audit_cleanup_at", now))

    def pending_scan_audit(self, symbol, timeframe, limit=2000):
        with self.lock, self._conn() as c:
            return self._execute(c, """SELECT * FROM scan_audit
                WHERE symbol=? AND timeframe=? AND
                ((buy_entry IS NOT NULL AND buy_outcome IS NULL) OR
                 (sell_entry IS NOT NULL AND sell_outcome IS NULL))
                ORDER BY candle_time LIMIT ?""", (symbol, timeframe, int(limit))).fetchall()

    def resolve_scan_audit_candidate(self, row_id, direction, outcome, evaluated_bars):
        direction = str(direction).lower()
        if direction not in ("buy", "sell"):
            raise ValueError("direction must be BUY or SELL")
        with self.lock, self._conn() as c:
            self._execute(c, f"""UPDATE scan_audit SET {direction}_outcome=?,
                {direction}_evaluated_bars=? WHERE id=? AND {direction}_outcome IS NULL""",
                (str(outcome), int(evaluated_bars), int(row_id)))

    def scan_audit_page(self, since=None, until=None, limit=1000, before_id=None):
        clauses = []
        params = []
        if since:
            clauses.append("scanned_at >= ?"); params.append(str(since))
        if until:
            clauses.append("scanned_at <= ?"); params.append(str(until))
        if before_id is not None:
            clauses.append("id < ?"); params.append(int(before_id))
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(max(1, min(int(limit), 5000)))
        with self.lock, self._conn() as c:
            return self._execute(c, f"SELECT * FROM scan_audit{where} ORDER BY id DESC LIMIT ?", params).fetchall()

    def scan_audit_summary(self, since=None, until=None):
        clauses = []
        params = []
        if since:
            clauses.append("scanned_at >= ?"); params.append(str(since))
        if until:
            clauses.append("scanned_at <= ?"); params.append(str(until))
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self.lock, self._conn() as c:
            return self._execute(c, f"""SELECT symbol,timeframe,
                COUNT(*) AS candles,
                SUM(CASE WHEN reject_reason='' THEN 1 ELSE 0 END) AS accepted_scans,
                SUM(CASE WHEN reject_reason<>'' THEN 1 ELSE 0 END) AS rejected_scans,
                SUM(CASE WHEN buy_entry IS NOT NULL THEN 1 ELSE 0 END) AS buy_candidates,
                SUM(CASE WHEN sell_entry IS NOT NULL THEN 1 ELSE 0 END) AS sell_candidates,
                SUM(CASE WHEN buy_entry IS NOT NULL AND buy_outcome IS NULL THEN 1 ELSE 0 END) AS buy_pending,
                SUM(CASE WHEN sell_entry IS NOT NULL AND sell_outcome IS NULL THEN 1 ELSE 0 END) AS sell_pending,
                SUM(CASE WHEN buy_outcome='TP' THEN 1 ELSE 0 END) AS buy_tp,
                SUM(CASE WHEN buy_outcome='SL' THEN 1 ELSE 0 END) AS buy_sl,
                SUM(CASE WHEN buy_outcome='NO_ENTRY' THEN 1 ELSE 0 END) AS buy_no_entry,
                SUM(CASE WHEN buy_outcome='TIMEOUT' THEN 1 ELSE 0 END) AS buy_timeout,
                SUM(CASE WHEN sell_outcome='TP' THEN 1 ELSE 0 END) AS sell_tp,
                SUM(CASE WHEN sell_outcome='SL' THEN 1 ELSE 0 END) AS sell_sl,
                SUM(CASE WHEN sell_outcome='NO_ENTRY' THEN 1 ELSE 0 END) AS sell_no_entry,
                SUM(CASE WHEN sell_outcome='TIMEOUT' THEN 1 ELSE 0 END) AS sell_timeout
                FROM scan_audit{where} GROUP BY symbol,timeframe ORDER BY symbol,timeframe""", params).fetchall()

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

    def recent_scan_audit(self, limit=50000):
        with self.lock, self._conn() as c:
            return self._execute(
                c, "SELECT * FROM scan_audit ORDER BY id DESC LIMIT ?", (int(limit),)
            ).fetchall()

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
