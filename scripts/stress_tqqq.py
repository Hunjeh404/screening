"""TQQQ 최악 구간 점검. 나스닥100 지수(^NDX, 1985년~)로 3배 레버리지 가격을 만들어
1) 그냥 보유와 'QQQ(지수) 200일선 위면 보유, 아래면 현금(다음 날 시가 매매)' 규칙을 10년 구간별로 비교하고
2) 백테스트가 덮어씌울 최악 10년 구간의 경로를 넘긴다.
합성 3배: 하루 수익률 3배 - 연 0.95% 보수. 차입 비용은 빠져 있어 실제 TQQQ보다 조금 좋게 나온다."""
import datetime as dt
import os

import numpy as np

FEE, COST, YEARS = .0095 / 252, .001, 10


def load(sym='^NDX'):
    PATH = f'data/bt/{sym.strip("^").lower()}.npz'
    if not os.path.exists(PATH):
        import yfinance as yf
        x = yf.download(sym, start='1985-01-01', auto_adjust=False, progress=False)
        x = x.dropna(subset=[('Close', sym)]) if hasattr(x.columns, 'levels') else x.dropna(subset=['Close'])
        c = np.asarray(x['Close'], float).ravel()
        o = np.asarray(x['Open'], float).ravel()
        d = ((x.index.values.astype('datetime64[D]') - np.datetime64('1970-01-01')).astype(int))
        np.savez(PATH, d=d, o=o, c=c)
    z = np.load(PATH)
    return z['d'], z['o'], z['c']


def synth(o, c, lev=3, fee=FEE):
    """합성 lev배 (시가, 종가). 지수 시가가 없거나 이상하면 전날 종가로 본다."""
    prev = np.r_[c[0], c[:-1]]
    o = np.where(np.isfinite(o) & (o > 0) & (np.abs(o / prev - 1) < .2), o, prev)
    r = np.r_[0, c[1:] / c[:-1] - 1] * lev - fee
    C = np.cumprod(np.maximum(1 + r, 1e-9))
    O = np.r_[C[0], C[:-1]] * np.maximum(1 + lev * (o / prev - 1), 1e-9)
    return O, C


def mix(Oq, Cq, Ot, Ct, ok):
    """반은 1배(항상 보유), 반은 3배(전날 200일선 위일 때만, 아래면 현금). 매일 반반으로 맞춘다고 가정."""
    C, O = np.ones(len(Cq)), np.ones(len(Cq))
    for k in range(1, len(Cq)):
        held, was = bool(ok[k - 1]), bool(ok[k - 2]) if k > 1 else False
        rq, rt = Cq[k] / Cq[k - 1] - 1, Ct[k] / Ct[k - 1] - 1
        oq, ot = Oq[k] / Cq[k - 1] - 1, Ot[k] / Ct[k - 1] - 1
        C[k] = C[k - 1] * (1 + .5 * rq + (.5 * rt if held else 0) - (.5 * COST if held != was else 0))
        O[k] = C[k - 1] * (1 + .5 * oq + (.5 * ot if held else 0))
    return O, C


def rates(d):
    """미국 3개월 국채 금리(연, 소수)를 나스닥100 거래일에 맞춤"""
    d2, _, c2 = load('^IRX')
    j = np.clip(np.searchsorted(d2, d, 'right') - 1, 0, None)
    return np.nan_to_num(c2[j] / 100)


def ma_ok(c, n=200):
    m = np.convolve(c, np.ones(n) / n, 'valid')
    return np.r_[np.ones(n - 1, bool), c[n - 1:] >= m]


def rule(O, C, ok):
    """전날 종가에 200일선 위면 다음 날 시가에 보유, 아래면 시가에 팔고 현금. 매일 종가 평가."""
    eq, held, cash, out = 0.0, False, 1.0, np.empty(len(C))
    for k in range(len(C)):
        want = bool(ok[k - 1]) if k else False
        if want and not held:
            eq, cash, held = cash * (1 - COST) / O[k], 0.0, True
        elif held and not want:
            cash, eq, held = eq * O[k] * (1 - COST), 0.0, False
        out[k] = eq * C[k] if held else cash
    return out


def day(d):
    return (dt.date(1970, 1, 1) + dt.timedelta(days=int(d))).isoformat()


def stats(x, n):
    yrs = n / 252
    peak = np.maximum.accumulate(x)
    return (x[-1] / x[0]) ** (1 / yrs) - 1, (x / peak - 1).min()


def worst(curve, n):
    r = curve[n:] / curve[:-n]
    i = int(np.argmin(r))
    return i, r[i] - 1


def path(name='NDX3'):
    """최악 10년(3배+200일선 규칙 기준) 시작일부터의 (시가, 종가, 나스닥100 200일선 위 여부), 시작일.
    name: NDX3(합성 3배) NDX1(나스닥100 1배, 보수 0.2%) SPX1(S&P500 1배, 보수 0.1%) - 같은 날짜부터"""
    d, o, c = load()
    O, C = synth(o, c)
    ok = ma_ok(c)
    i, _ = worst(rule(O, C, ok)[200:], YEARS * 252)
    i += 200
    if name in ('NDX3F', 'MIXF'):   # 차입 비용 반영: 연 0.95% + 2배 × 3개월 국채 금리
        O, C = synth(o, c, 3, (.0095 + 2 * rates(d)) / 252)
        if name == 'MIXF':
            Oq, Cq = synth(o, c, 1, .002 / 252)
            O, C = mix(Oq, Cq, O, C, ok)
    elif name == 'NDX1':
        O, C = synth(o, c, 1, .002 / 252)
    elif name == 'SPX1':
        d2, o2, c2 = load('^GSPC')
        j = np.clip(np.searchsorted(d2, d, 'right') - 1, 0, None)   # 나스닥100 거래일에 맞춤
        O, C = synth(o2[j], c2[j], 1, .001 / 252)
    return O[i:], C[i:], ok[i:], day(d[i])


def main():
    d, o, c = load()
    O, C = synth(o, c)
    ok = ma_ok(c)
    s = 200   # 200일선이 생긴 뒤부터 비교
    curves = {'나스닥100 보유': c[s:], '3배 보유': C[s:], '3배·200일선': rule(O, C, ok)[s:]}
    n = YEARS * 252
    lines = [f'기간 {day(d[s])} ~ {day(d[-1])}']
    for name, x in curves.items():
        cagr, mdd = stats(x, len(x))
        i, r = worst(x, n)
        lines.append(f'{name}: 연 {cagr * 100:+.1f}% · MDD {mdd * 100:.1f}% · 최악 10년 {day(d[s + i])}~{day(d[s + i + n])} '
                     f'{r * 100:+.1f}%')
    rx = curves['3배·200일선']
    for a, b in (('2000-03-01', '2010-03-01'), ('2000-03-01', '2013-01-01'), ('2007-10-01', '2009-03-31')):
        ia, ib = (int(np.searchsorted(d[s:], (dt.date.fromisoformat(t) - dt.date(1970, 1, 1)).days)) for t in (a, b))
        seg = {k: x[ib] / x[ia] - 1 for k, x in curves.items()}
        mdd = {k: (x[ia:ib + 1] / np.maximum.accumulate(x[ia:ib + 1]) - 1).min() for k, x in curves.items()}
        lines.append(f'{a}~{b}: ' + ' · '.join(f'{k} {seg[k] * 100:+.1f}%(MDD {mdd[k] * 100:.0f}%)' for k in curves))
    if os.path.exists('data/bt/px/TQQQ.npz'):   # 합성 3배가 실제 TQQQ와 얼마나 맞는지
        t = np.load('data/bt/px/TQQQ.npz')
        j = np.searchsorted(d, t['d'])
        m = (j < len(d)) & (d[np.minimum(j, len(d) - 1)] == t['d'])
        a, b = t['c'][m].astype(float), C[j[m]]
        yrs = len(a) / 252
        _, CF = synth(o, c, 3, (.0095 + 2 * rates(d)) / 252)
        f = CF[j[m]]
        lines.append(f'검증 {day(t["d"][m][0])}~: 실제 TQQQ 연 {((a[-1] / a[0]) ** (1 / yrs) - 1) * 100:+.1f}% · '
                     f'합성 3배 연 {((b[-1] / b[0]) ** (1 / yrs) - 1) * 100:+.1f}% · '
                     f'차입비용 반영 연 {((f[-1] / f[0]) ** (1 / yrs) - 1) * 100:+.1f}%')
        OF = synth(o, c, 3, (.0095 + 2 * rates(d)) / 252)[0]
        rf = rule(OF, CF, ok)
        ia, ib = (int(np.searchsorted(d, (dt.date.fromisoformat(x) - dt.date(1970, 1, 1)).days)) for x in ('2000-03-10', '2010-03-19'))
        seg = rf[ia:ib + 1]
        lines.append(f'차입비용 반영 3배·200일선 2000-03-10~2010-03-19: {(seg[-1] / seg[0] - 1) * 100:+.1f}% '
                     f'(MDD {(seg / np.maximum.accumulate(seg) - 1).min() * 100:.0f}%) · 전체 연 '
                     f'{stats(rf[s:], len(rf) - s)[0] * 100:+.1f}%')
    lines.append('백테스트에 덮어씌울 시작일(3배·200일선 최악 10년): ' + path()[3])
    for line in lines:
        print('::warning title=stress::' + line)


if __name__ == '__main__':
    main()
