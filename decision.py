import math
import pandas as pd
import ai_engine


def _round_price(v):
    return round(float(v), 2)


def _score_direction(r, direction, ai_prob, ctx):
    bull = float(r['ema20']) > float(r['ema50']) > float(r['ema200'])
    bear = float(r['ema20']) < float(r['ema50']) < float(r['ema200'])
    momentum_up = float(r['macd_hist']) > 0 and float(r['rsi']) >= 52
    momentum_dn = float(r['macd_hist']) < 0 and float(r['rsi']) <= 48
    breakout_up = bool(r['breakout_up'])
    breakout_dn = bool(r['breakout_dn'])
    candle_up = float(r['Close']) > float(r['Open'])
    candle_dn = float(r['Close']) < float(r['Open'])
    vol_ok = pd.isna(r['vol_ratio']) or float(r['vol_ratio']) >= 0.75
    trend_ctx = int(ctx.get('trend', 0))
    score = 0.0
    if direction == 'BUY':
        score += 20 if bull else 0
        score += 14 if momentum_up else 0
        score += 14 if breakout_up else 0
        score += 7 if candle_up else 0
        score += 7 if vol_ok else 0
        score += 8 if float(r['Close']) > float(r['ema20']) else 0
        score += 8 if trend_ctx > 0 else 0
    else:
        score += 20 if bear else 0
        score += 14 if momentum_dn else 0
        score += 14 if breakout_dn else 0
        score += 7 if candle_dn else 0
        score += 7 if vol_ok else 0
        score += 8 if float(r['Close']) < float(r['ema20']) else 0
        score += 8 if trend_ctx < 0 else 0
    score += 22 * max(0.0, min(1.0, float(ai_prob)))
    return score


def build_setup(df, probs, rr=2.0, divisor=3.6, sl_atr_mult=1.15,
                min_score=70, min_ai=0.58, context=None, diagnostics=None):
    diagnostics = diagnostics if diagnostics is not None else {}
    if len(df) < 220:
        diagnostics['reject'] = 'insufficient_bars'
        return None
    r = df.iloc[-2]
    atr = float(r['atr'])
    close = float(r['Close'])
    diagnostics.update({'bars': len(df), 'candle_time': r.name.isoformat(), 'close': close, 'atr': atr})
    if not math.isfinite(atr) or atr <= 0:
        diagnostics['reject'] = 'invalid_atr'
        return None
    context = context or {}
    candidates = []
    for direction in ('BUY', 'SELL'):
        prob = probs.get(direction)
        if prob is None or not math.isfinite(float(prob)):
            diagnostics[f'{direction.lower()}_reject'] = 'no_model_probability'
            continue
        prob = max(0.0, min(1.0, float(prob)))
        score = _score_direction(r, direction, prob, context)
        diagnostics[f'{direction.lower()}_ai'] = round(prob, 4)
        diagnostics[f'{direction.lower()}_score'] = round(score, 1)
        diagnostics[f'{direction.lower()}_ai_threshold_pass'] = prob >= float(min_ai)
        candidates.append((score, direction, prob))
    if not candidates:
        diagnostics['reject'] = 'no_model_probabilities'
        return None
    score, direction, ai_prob = max(candidates, key=lambda z: z[0])
    diagnostics['best_direction'] = direction
    diagnostics['best_score'] = round(score, 1)
    if score < float(min_score):
        diagnostics['reject'] = 'score_below_threshold'
        diagnostics['score_gap'] = round(float(min_score) - score, 1)
        return None
    hi = float(r['high20'])
    lo = float(r['low20'])
    span = max(hi - lo, atr)
    if direction == 'BUY':
        entry = max(close, lo + span / divisor)
        sl = min(lo - 0.10 * atr, entry - sl_atr_mult * atr)
        tp = entry + rr * (entry - sl)
    else:
        entry = min(close, hi - span / divisor)
        sl = max(hi + 0.10 * atr, entry + sl_atr_mult * atr)
        tp = entry - rr * (sl - entry)
    risk = abs(entry - sl)
    risk_atr = risk / atr if atr else float('inf')
    diagnostics['risk_atr'] = round(risk_atr, 3)
    if risk < 0.55 * atr or risk > 3 * atr:
        diagnostics['reject'] = 'risk_out_of_range'
        return None
    diagnostics['accepted'] = True
    return {
        'direction': direction,
        'score': round(score, 1),
        'ai_prob': round(ai_prob, 3),
        'entry': _round_price(entry),
        'sl': _round_price(sl),
        'tp': _round_price(tp),
        'risk': _round_price(risk),
        'candle_time': r.name.isoformat(),
        'reference_close': _round_price(close),
        'atr': _round_price(atr),
        'features': {k: float(r[k]) for k in r.index if k in ai_engine.FEATURES}
    }
