import os, sqlite3, threading, json
from datetime import datetime, timezone

class DB:
    def __init__(self,path):
        self.path=path; os.makedirs(os.path.dirname(path) or '.',exist_ok=True); self.lock=threading.RLock()
        with self._conn() as c:
            c.executescript('''CREATE TABLE IF NOT EXISTS signals(
            id INTEGER PRIMARY KEY AUTOINCREMENT,symbol TEXT NOT NULL,timeframe TEXT NOT NULL,direction TEXT NOT NULL,
            entry REAL NOT NULL,sl REAL NOT NULL,tp REAL NOT NULL,score REAL,ai_prob REAL,status TEXT NOT NULL,result TEXT,
            created_at TEXT NOT NULL,activated_at TEXT,closed_at TEXT,entry_time TEXT,close_price REAL,wave_key TEXT,candle_time TEXT,
            features_json TEXT, UNIQUE(symbol,timeframe,wave_key));
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);''')
            cols={r[1] for r in c.execute('PRAGMA table_info(signals)')}
            if 'features_json' not in cols: c.execute('ALTER TABLE signals ADD COLUMN features_json TEXT')
    def _conn(self):
        c=sqlite3.connect(self.path,timeout=30,check_same_thread=False); c.row_factory=sqlite3.Row; return c
    def create(self,s):
        now=datetime.now(timezone.utc).isoformat()
        with self.lock,self._conn() as c:
            try:
                cur=c.execute('''INSERT INTO signals(symbol,timeframe,direction,entry,sl,tp,score,ai_prob,status,created_at,wave_key,candle_time,features_json)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',(s['symbol'],s['timeframe'],s['direction'],s['entry'],s['sl'],s['tp'],s['score'],s['ai_prob'],'WAITING',now,s['wave_key'],s['candle_time'],json.dumps(s.get('features',{}))))
                return cur.lastrowid
            except sqlite3.IntegrityError:return None
    def open_for(self,symbol,tf):
        with self.lock,self._conn() as c:return c.execute("SELECT * FROM signals WHERE symbol=? AND timeframe=? AND status IN ('WAITING','ACTIVE') ORDER BY id DESC LIMIT 1",(symbol,tf)).fetchone()
    def all_open(self):
        with self.lock,self._conn() as c:return c.execute("SELECT * FROM signals WHERE status IN ('WAITING','ACTIVE') ORDER BY id").fetchall()
    def activate(self,sid,price):
        now=datetime.now(timezone.utc).isoformat()
        with self.lock,self._conn() as c:c.execute("UPDATE signals SET status='ACTIVE',activated_at=?,entry_time=? WHERE id=? AND status='WAITING'",(now,now,sid))
    def close(self,sid,result,price):
        now=datetime.now(timezone.utc).isoformat()
        with self.lock,self._conn() as c:c.execute("UPDATE signals SET status='CLOSED',result=?,closed_at=?,close_price=? WHERE id=? AND status IN ('WAITING','ACTIVE')",(result,now,price,sid))
    def cancel(self,sid,result='NOT_SENT'):
        now=datetime.now(timezone.utc).isoformat()
        with self.lock,self._conn() as c:c.execute("UPDATE signals SET status='CANCELLED',result=?,closed_at=? WHERE id=? AND status IN ('WAITING','ACTIVE')",(result,now,sid))
    def cancel_open_once(self,key='signal_delivery_fix_v1'):
        with self.lock,self._conn() as c:
            if c.execute('SELECT 1 FROM settings WHERE key=?',(key,)).fetchone(): return 0
            now=datetime.now(timezone.utc).isoformat()
            cur=c.execute("UPDATE signals SET status='CANCELLED',result='LEGACY_NOT_DELIVERED',closed_at=? WHERE status IN ('WAITING','ACTIVE')",(now,))
            c.execute('INSERT INTO settings(key,value) VALUES(?,?)',(key,'1'))
            return cur.rowcount
    def get_setting(self,key,default=None):
        with self.lock,self._conn() as c:
            r=c.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
            return r['value'] if r else default
    def set_setting(self,key,value):
        with self.lock,self._conn() as c:c.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,str(value)))
    def closed_stats(self):
        with self.lock,self._conn() as c: rows=c.execute("SELECT result FROM signals WHERE status='CLOSED' AND result IN ('TP','SL')").fetchall()
        w=sum(r['result']=='TP' for r in rows); l=sum(r['result']=='SL' for r in rows); return len(rows),w,l
    def recent(self,n=10):
        with self.lock,self._conn() as c:return c.execute("SELECT * FROM signals ORDER BY id DESC LIMIT ?",(n,)).fetchall()
