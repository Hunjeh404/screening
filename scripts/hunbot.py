"""HunBot daily OHLC signals. Values are calculated only from completed daily bars."""
import datetime as dt
import math
import statistics
from collections import deque
from zoneinfo import ZoneInfo


def finite(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def completed_day(day, market, now=None):
    """Exclude today's in-progress bar; US half days are conservatively confirmed at 16:00."""
    zone, hour, minute = ('Asia/Seoul', 15, 30) if market == 'kr' else ('America/New_York', 16, 0)
    local = (now or dt.datetime.now(dt.timezone.utc)).astimezone(ZoneInfo(zone))
    return day < local.date() or (day == local.date() and (local.hour, local.minute) >= (hour, minute))


def ema(values, length):
    # Pine EMA: seed with first non-na input, alpha = 2/(length+1).
    out, previous = [], None
    alpha = 2 / (length + 1)
    for value in values:
        if finite(value):
            previous = value if previous is None else alpha * value + (1 - alpha) * previous
        out.append(previous if finite(value) else None)
    return out


def rsi(closes, length):
    # Wilder RMA: initial SMA of `length` price changes, then alpha = 1/length.
    out, gains, losses, up, down = [None], [], [], None, None
    for previous, close in zip(closes, closes[1:]):
        if not (finite(previous) and finite(close)):
            gains, losses, up, down = [], [], None, None
            out.append(None)
            continue
        gain, loss = max(close - previous, 0), max(previous - close, 0)
        if up is None:
            gains.append(gain); losses.append(loss)
            if len(gains) == length:
                up, down = sum(gains) / length, sum(losses) / length
        else:
            up = (up * (length - 1) + gain) / length
            down = (down * (length - 1) + loss) / length
        out.append(None if up is None or up == down == 0 else
                   100.0 if down == 0 else 100 - 100 / (1 + up / down))
    return out if closes else []


def rolling(values, length, operation):
    window, out = deque(maxlen=length), []
    for value in values:
        window.append(value)
        out.append(operation(window) if len(window) == length and all(finite(x) for x in window) else None)
    return out


def stochastic(closes, highs, lows, length):
    hi, lo = rolling(highs, length, max), rolling(lows, length, min)
    return [(c - l) / (h - l) * 100 if all(finite(x) for x in (c, h, l)) and h > l else None
            for c, h, l in zip(closes, hi, lo)]


def signal_ages(signals):
    """SC wins even on an LC bar. Missing observations invalidate, never extend a signal."""
    last, ages = None, []
    for i, signal in enumerate(signals):
        if signal['sc'] is None or signal['lc'] is None or signal['sc']:
            last = None
        elif signal['lc']:
            last = i
        age = i - last if last is not None else None
        ages.append(age if age is not None and age < 3 else None)
    return ages


def daily_signals(closes, highs, lows):
    if not closes or len(highs) != len(closes) or len(lows) != len(closes):
        return []
    # Invalid candles must not fabricate a range or a crossover.
    valid_bar = [all(finite(x) and x > 0 for x in (c, h, l)) and l <= c <= h
                 for c, h, l in zip(closes, highs, lows)]
    highs = [v if ok else None for v, ok in zip(highs, valid_bar)]
    lows = [v if ok else None for v, ok in zip(lows, valid_bar)]
    sma5 = rolling(closes, 5, lambda w: sum(w) / 5)
    basis = rolling(closes, 20, lambda w: sum(w) / 20)
    # Population deviation (Pine stdev default), not sample deviation.
    dev = rolling(closes, 20, statistics.pstdev)
    upper = [b + 2 * v if b is not None and v is not None else None for b, v in zip(basis, dev)]
    r12 = rsi(closes, 12)
    panic_rsi = ema(rsi(closes, 20), 12)
    k = ema(stochastic(closes, highs, lows, 12), 6)
    d = ema(k, 6)
    e1 = ema(stochastic(closes, highs, lows, 20), 12)
    e2 = ema(e1, 12)
    signals = []
    for i, close in enumerate(closes):
        super_bull = panic = lc = sc = None
        if valid_bar[i]:
            panic = panic_rsi[i] <= 30 if finite(panic_rsi[i]) else None
            if i and all(finite(v) for v in (sma5[i], basis[i], basis[i-1], e2[i], e2[i-1])):
                super_bull = close > sma5[i] and basis[i] > basis[i-1] and e2[i] >= e2[i-1]
            if i and super_bull is not None and all(finite(v) for v in
                    (e1[i], e1[i-1], k[i], k[i-1], d[i], d[i-1], r12[i], upper[i], upper[i-1])):
                bungee = close < basis[i] and basis[i] < basis[i-1] and e1[i] <= e1[i-1]
                lc = close >= sma5[i] and not bungee and 10 < r12[i] < 85 and 10 < k[i] < 85 and k[i-1] <= d[i-1] and k[i] > d[i]
            if super_bull is True:
                sc = False
            elif i and super_bull is False:
                if close > basis[i] and all(finite(v) for v in (upper[i], upper[i-1], closes[i-1])):
                    sc = closes[i-1] >= upper[i-1] and close < upper[i]
                elif close <= basis[i] and all(finite(v) for v in (k[i], k[i-1], d[i], d[i-1])):
                    sc = k[i-1] >= d[i-1] and k[i] < d[i]
        signals.append(dict(super_bull=super_bull, panic=panic, lc=lc, sc=sc, details={
            'sma5': sma5[i], 'bb_basis': basis[i], 'prev_bb_basis': basis[i-1] if i else None,
            'stoch20_ema2': e2[i], 'prev_stoch20_ema2': e2[i-1] if i else None,
            'panic_rsi': panic_rsi[i],
        }))
    for signal, age in zip(signals, signal_ages(signals)):
        signal['lc_age'] = age
    return signals


def latest_signal(closes, highs=None, lows=None):
    values = daily_signals(closes, highs, lows) if highs is not None and lows is not None else []
    return values[-1] if values else None
