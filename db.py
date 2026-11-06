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

    def _execute(self, conn, sql, params=()):
        if self.backend == "postgres":
            # All DB parameters in this module use qmark placeholders. Convert
            # them for psycopg while keeping the SQLite queries unchanged.
            sql = sql.replace("?", "%s")
        return conn.execute(sql, params)

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
                key TEXT PRIMARY KEY,value TEXT);"""
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
            try:
                if self.backend == "postgres":
                    row = self._execute(
                        c, query + " RETURNING id", values
                    ).fetchone()
                    return int(row["id"])
                cur = self._execute(c, query, values)
                return cur.lastrowid
            except (sqlite3.IntegrityError,):
                return None
            except Exception as exc:
                if self.backend == "postgres" and exc.__class__.__name__ == "UniqueViolation":
                    return None
                raise

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
