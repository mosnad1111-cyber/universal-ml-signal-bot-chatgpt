import math
import pandas as pd
import ai_engine


def _round_price(v):
    return round(float(v), 2)


def _clip01(v):
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return 0.0


def _score_direction(r, direction, ai_prob, ctx):
    """Continuous 100-point quality score instead of binary gating."""
    close = float(r['Close']); open_ = float(r['Open']); high = float(r['High']); low = float(r['Low'])
    atr = max(float(r['atr']), 1e-12)
    ema20 = float(r['ema20']); ema50 = float(r['ema50']); ema200 = float(r['ema200'])
    rsi = float(r['rsi']); macd_hist = float(r['macd_hist']); trend_ctx = int(ctx.get('trend', 0))

    trend_span = max(abs(ema50 - ema200), atr * 0.25)
    if direction == 'BUY':
        structure = 1.0 if ema20 > ema50 > ema200 else (0.65 if ema20 > ema50 else 0.25)
        trend_strength = _clip01(abs(ema20 - ema50) / trend_span)
    else:
        structure = 1.0 if ema20 < ema50 < ema200 else (0.65 if ema20 < ema50 else 0.25)
        trend_strength = _clip01(abs(ema20 - ema50) / trend_span)
    trend_score = 20.0 * (0.65 * structure + 0.35 * trend_strength)

    macd_scale = max(atr * 0.02, abs(macd_hist), 1e-12)
    if direction == 'BUY':
        rsi_strength = _clip01((rsi - 50.0) / 15.0); macd_strength = _clip01(0.5 + 0.5 * macd_hist / macd_scale)
    else:
        rsi_strength = _clip01((50.0 - rsi) / 15.0); macd_strength = _clip01(0.5 - 0.5 * macd_hist / macd_scale)
    momentum_score = 14.0 * (0.6 * rsi_strength + 0.4 * macd_strength)

    hi20 = float(r['high20']); lo20 = float(r['low20']); span20 = max(hi20 - lo20, atr)
    if direction == 'BUY':
        proximity = _clip01((close - lo20) / span20); breakout = 1.0 if bool(r['breakout_up']) else proximity * 0.8
    else:
        proximity = _clip01((hi20 - close) / span20); breakout = 1.0 if bool(r['breakout_dn']) else proximity * 0.8
    breakout_score = 14.0 * breakout

    candle_range = max(high - low, 1e-12); body_direction = (close - open_) / candle_range
    candle_strength = _clip01(0.5 + (body_direction if direction == 'BUY' else -body_direction))
    candle_score = 7.0 * candle_strength

    vol_ratio = r['vol_ratio']; volume_strength = 0.70 if pd.isna(vol_ratio) else _clip01(float(vol_ratio) / 1.20)
    volume_score = 7.0 * volume_strength

    ema_distance = (close - ema20) / (2.0 * atr)
    price_strength = _clip01(0.5 + (ema_distance if direction == 'BUY' else -ema_distance))
    price_score = 8.0 * price_strength

    if direction == 'BUY': context_strength = 1.0 if trend_ctx > 0 else (0.5 if trend_ctx == 0 else 0.0)
    else: context_strength = 1.0 if trend_ctx < 0 else (0.5 if trend_ctx == 0 else 0.0)
    context_score = 8.0 * context_strength
    ai_score = 22.0 * _clip01(ai_prob)
    return trend_score + momentum_score + breakout_score + candle_score + volume_score + price_score + context_score + ai_score


def build_setup(df, probs, rr=2.0, divisor=3.6, sl_atr_mult=1.15,
                min_score=65, min_ai=0.58, context=None, diagnostics=None, min_gap=7.0):
    diagnostics = diagnostics if diagnostics is not None else {}
    if len(df) < 220:
        diagnostics['reject'] = 'insufficient_bars'; return None
    r = df.iloc[-2]; atr = float(r['atr']); close = float(r['Close'])
    diagnostics.update({'bars': len(df), 'candle_time': r.name.isoformat(), 'close': close, 'atr': atr})
    if not math.isfinite(atr) or atr <= 0:
        diagnostics['reject'] = 'invalid_atr'; return None
    context = context or {}; candidates = []
    for direction in ('BUY', 'SELL'):
        prob = probs.get(direction)
        if prob is None or not math.isfinite(float(prob)):
            diagnostics[f'{direction.lower()}_reject'] = 'no_model_probability'; continue
        prob = _clip01(prob); score = _score_direction(r, direction, prob, context)
        diagnostics[f'{direction.lower()}_ai'] = round(prob, 4)
        diagnostics[f'{direction.lower()}_score'] = round(score, 1)
        diagnostics[f'{direction.lower()}_ai_threshold_pass'] = prob >= float(min_ai)
        candidates.append((score, direction, prob))
    if not candidates:
        diagnostics['reject'] = 'no_model_probabilities'; return None
    candidates.sort(key=lambda z: z[0], reverse=True)
    score, direction, ai_prob = candidates[0]; second_score = candidates[1][0] if len(candidates) > 1 else 0.0
    diagnostics['best_direction'] = direction; diagnostics['best_score'] = round(score, 1); diagnostics['score_gap'] = round(score-second_score, 1)
    if score < float(min_score):
        diagnostics['reject'] = 'score_below_threshold'; diagnostics['score_gap_to_threshold'] = round(float(min_score)-score, 1); return None
    if len(candidates) > 1 and score-second_score < float(min_gap):
        diagnostics['reject'] = 'direction_ambiguous'; diagnostics['min_direction_gap'] = float(min_gap); return None

    hi = float(r['high20']); lo = float(r['low20']); span = max(hi-lo, atr)
    if direction == 'BUY':
        entry = max(close, lo + span/divisor); sl = min(lo-0.10*atr, entry-sl_atr_mult*atr); tp = entry + rr*(entry-sl)
    else:
        entry = min(close, hi - span/divisor); sl = max(hi+0.10*atr, entry+sl_atr_mult*atr); tp = entry - rr*(sl-entry)
    risk = abs(entry-sl); risk_atr = risk/atr if atr else float('inf'); diagnostics['risk_atr'] = round(risk_atr,3)
    if risk < 0.55*atr or risk > 3*atr:
        diagnostics['reject'] = 'risk_out_of_range'; return None
    diagnostics['accepted'] = True
    return {'direction':direction,'score':round(score,1),'ai_prob':round(ai_prob,3),'entry':_round_price(entry),'sl':_round_price(sl),'tp':_round_price(tp),'risk':_round_price(risk),'candle_time':r.name.isoformat(),'reference_close':_round_price(close),'atr':_round_price(atr),'features':{k:float(r[k]) for k in r.index if k in ai_engine.FEATURES}}
