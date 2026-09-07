import time
import warnings
import threading
import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

# IMPORTANT: this bot now requests the same TradingView instrument shown by the user:
# TVC:GOLD = CFDs on Gold (US$ / OZ).
# TradingView's TVC symbols are calculated by TradingView and are not guaranteed to
# match Yahoo/other providers candle-for-candle, so TVC:GOLD is the primary feed.
try:
    from tvDatafeed import TvDatafeed, Interval
except Exception as exc:  # handled by fetch with a useful error
    TvDatafeed = None
    Interval = None
    _TV_IMPORT_ERROR = exc
else:
    _TV_IMPORT_ERROR = None

PERIODS = {'5m': 5000, '15m': 5000, '1h': 5000}
INTERVALS = {
    '5m': 'in_5_minute',
    '15m': 'in_15_minute',
    '1h': 'in_1_hour',
}

_tv = None
_tv_lock = threading.RLock()
_cache = {}
_cache_lock = threading.RLock()
CACHE_SECONDS = 12


def _get_tv():
    global _tv
    if TvDatafeed is None:
        raise RuntimeError(
            f'مكتبة TradingView غير متاحة: {_TV_IMPORT_ERROR}. '
            'تحقق من requirements.txt ثم أعد النشر.'
        )
    with _tv_lock:
        if _tv is None:
            # No TradingView username/password is required by the bot configuration.
            # The library uses its public/no-login mode; availability can be limited by TV.
            _tv = TvDatafeed()
        return _tv


def _normalize_tv(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        raise RuntimeError('TradingView أعاد بيانات فارغة للرمز TVC:GOLD.')

    x = df.copy()
    # tvDatafeed returns symbol + OHLCV columns in lower case.
    rename = {
        'open': 'Open', 'high': 'High', 'low': 'Low',
        'close': 'Close', 'volume': 'Volume'
    }
    x = x.rename(columns=rename)
    required = ['Open', 'High', 'Low', 'Close']
    missing = [c for c in required if c not in x.columns]
    if missing:
        raise RuntimeError(f'بيانات TradingView ناقصة: {", ".join(missing)}')
    if 'Volume' not in x.columns:
        x['Volume'] = 0.0

    x = x[required + ['Volume']].copy()
    for c in x.columns:
        x[c] = pd.to_numeric(x[c], errors='coerce')
    x = x.dropna(subset=required)
    x = x[~x.index.duplicated(keep='last')].sort_index()
    x.index = pd.to_datetime(x.index, utc=True)
    if len(x) < 250:
        raise RuntimeError(f'TradingView أعاد {len(x)} شمعة فقط؛ يلزم 250 على الأقل.')
    return x


def fetch(symbol: str, timeframe: str, period=None) -> pd.DataFrame:
    # `symbol` is kept in the function signature for compatibility with the engine.
    # The exact requested instrument is always TVC:GOLD.
    if timeframe not in INTERVALS:
        raise RuntimeError(f'فريم غير مدعوم: {timeframe}')

    now = time.time()
    cache_key = timeframe
    with _cache_lock:
        item = _cache.get(cache_key)
        if item and now - item[0] < CACHE_SECONDS:
            return item[1].copy()

    errors = []
    for attempt in range(3):
        try:
            tv = _get_tv()
            interval = getattr(Interval, INTERVALS[timeframe])
            with _tv_lock:
                raw = tv.get_hist(
                    symbol='GOLD',
                    exchange='TVC',
                    interval=interval,
                    n_bars=PERIODS[timeframe],
                    extended_session=True,
                )
            df = _normalize_tv(raw)
            with _cache_lock:
                _cache[cache_key] = (time.time(), df.copy())
            return df
        except Exception as exc:
            errors.append(f'محاولة {attempt + 1}: {type(exc).__name__}: {exc}')
            # Recreate the client after a failed websocket so a broken connection
            # is not reused on the next attempt.
            global _tv
            with _tv_lock:
                _tv = None
            time.sleep(1.5 * (attempt + 1))

    raise RuntimeError(
        f'تعذر جلب بيانات الذهب TVC:GOLD ({timeframe}). '
        + ' | '.join(errors)
    )


def _rsi(close, n=14):
    delta = close.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    avg_up = up.ewm(alpha=1/n, adjust=False).mean()
    avg_down = down.ewm(alpha=1/n, adjust=False).mean()
    rs = avg_up / avg_down.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _atr(df, n=14):
    prev = df['Close'].shift(1)
    tr = pd.concat([
        df['High'] - df['Low'],
        (df['High'] - prev).abs(),
        (df['Low'] - prev).abs()
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False).mean()


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    x = df.copy()
    c, h, l = x['Close'], x['High'], x['Low']
    for n in (9, 20, 50, 200):
        x[f'ema{n}'] = c.ewm(span=n, adjust=False).mean()
    x['atr'] = _atr(x)
    x['atr_pct'] = x['atr'] / c
    x['rsi'] = _rsi(c)
    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    x['macd'] = ema12 - ema26
    x['macd_signal'] = x['macd'].ewm(span=9, adjust=False).mean()
    x['macd_hist'] = x['macd'] - x['macd_signal']
    x['ret1'] = c.pct_change(1)
    x['ret3'] = c.pct_change(3)
    x['ret6'] = c.pct_change(6)
    x['range'] = (h-l) / c
    x['body'] = (c-x['Open']) / x['Open']
    x['upper_wick'] = (h - pd.concat([x['Open'], c], axis=1).max(axis=1)) / c
    x['lower_wick'] = (pd.concat([x['Open'], c], axis=1).min(axis=1) - l) / c
    x['vol_ma20'] = x['Volume'].rolling(20).mean()
    x['vol_ratio'] = x['Volume'] / x['vol_ma20'].replace(0, np.nan)
    x['high20'] = h.rolling(20).max().shift(1)
    x['low20'] = l.rolling(20).min().shift(1)
    x['high50'] = h.rolling(50).max().shift(1)
    x['low50'] = l.rolling(50).min().shift(1)
    x['dist_ema20'] = (c-x['ema20']) / x['atr'].replace(0, np.nan)
    x['dist_ema50'] = (c-x['ema50']) / x['atr'].replace(0, np.nan)
    x['dist_ema200'] = (c-x['ema200']) / x['atr'].replace(0, np.nan)
    x['atr_rank'] = x['atr_pct'].rolling(100).rank(pct=True)
    x['trend_strength'] = (x['ema20']-x['ema50']) / x['atr'].replace(0,np.nan)
    x['breakout_up'] = (c > x['high20']).astype(float)
    x['breakout_dn'] = (c < x['low20']).astype(float)
    x['dist_high20_atr'] = (x['high20']-c) / x['atr'].replace(0,np.nan)
    x['dist_low20_atr'] = (c-x['low20']) / x['atr'].replace(0,np.nan)
    x = x.replace([np.inf, -np.inf], np.nan)
    return x


def confirmed_pivots(df: pd.DataFrame, length=5):
    highs, lows = [], []
    h, l = df['High'].values, df['Low'].values
    for i in range(length, len(df)-length):
        if h[i] >= np.max(h[i-length:i+length+1]) and h[i] > h[i-1] and h[i] >= h[i+1]:
            highs.append((i, float(h[i]), df.index[i]))
        if l[i] <= np.min(l[i-length:i+length+1]) and l[i] < l[i-1] and l[i] <= l[i+1]:
            lows.append((i, float(l[i]), df.index[i]))
    return highs, lows


def make_feature_frame(df):
    return add_features(df).dropna().copy()
