"""나스닥100 하락장·상승장 모드 전환 비교(1986년~, 일봉).
A: 종가 < 200일선, B: 200일선 < 한 달(21거래일) 전 200일선. 종가로 판정해 다음 날 시가에 바꾼다.
3배는 하루 수익률 3배 - (연 0.95% + 2 × 3개월 국채 금리), 1배는 지수 - 연 0.2%, 현금은 이자 없음. 전환마다 0.1% × 2."""
import datetime as dt
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import stress_tqqq as st

os.makedirs('data/bt', exist_ok=True)
COST = .001
d, o, c = st.load()
O3, C3 = st.synth(o, c, 3, (.0095 + 2 * st.rates(d)) / 252)
O1, C1 = st.synth(o, c, 1, .002 / 252)
ma = np.convolve(c, np.ones(200) / 200, 'valid')
ma = np.r_[np.full(199, np.nan), ma]
A = c < ma
B = ma < np.r_[np.full(21, np.nan), ma[:-21]]
valid = np.isfinite(ma) & np.r_[np.zeros(21, bool), np.isfinite(ma[:-21])]
PX = {'T': (O3, C3), 'Q': (O1, C1)}


def run(choose):
    """choose(a, b) -> 'T'(3배) 'Q'(1배) 'C'(현금). 그날 종가 판정으로 다음 날 시가부터 보유."""
    eq, pos, out, n = 1.0, None, np.ones(len(c)), 0
    for k in range(1, len(c)):
        if pos in PX:   # 전날 종가 -> 오늘 시가 (기존 보유)
            Op, Cp = PX[pos]
            eq *= Op[k] / Cp[k - 1]
        new = choose(A[k - 1], B[k - 1]) if valid[k - 1] else pos
        if new != pos:
            eq *= (1 - COST) ** ((pos in PX) + (new in PX))
            n += pos is not None
            pos = new
        if pos in PX:   # 오늘 시가 -> 종가
            Op, Cp = PX[pos]
            eq *= Cp[k] / Op[k]
        out[k] = eq
    return out, n


def day(i):
    return (dt.date(1970, 1, 1) + dt.timedelta(days=int(d[i]))).isoformat()


def idx(s):
    return int(np.searchsorted(d, (dt.date.fromisoformat(s) - dt.date(1970, 1, 1)).days))


S = {
    '1배(QQQ) 보유': lambda a, b: 'Q',
    '3배(TQQQ) 보유': lambda a, b: 'T',
    '현재: 200일선 아래면 현금, 위면 3배': lambda a, b: 'C' if a else 'T',
    '둘 다 켜짐 1배 · 둘 다 꺼짐 3배 · 중간 3배': lambda a, b: 'Q' if a and b else 'T',
    '둘 다 켜짐 1배 · 둘 다 꺼짐 3배 · 중간 1배': lambda a, b: 'T' if not a and not b else 'Q',
}
start = int(np.argmax(valid))
lines = [f'기간 {day(start)} ~ {day(len(d) - 1)} · 하락장(둘 다 켜짐) {(A & B)[valid].mean() * 100:.0f}% · '
         f'상승장(둘 다 꺼짐) {(~A & ~B)[valid].mean() * 100:.0f}% · 중간 {(A ^ B)[valid].mean() * 100:.0f}%']
res = {k: run(f) for k, f in S.items()}
for a, b in ((day(start), None), ('2000-03-10', '2010-03-19'), ('2007-10-01', '2009-03-31'),
             ('2013-01-01', None), ('2022-01-01', '2022-12-30')):
    ia, ib = max(idx(a), start), (idx(b) if b else len(d) - 1)
    lines.append(f'\n[{a} ~ {b or "현재"}]')
    for k, (x, n) in res.items():
        seg = x[ia:ib + 1]
        yrs = (ib - ia) / 252
        mdd = (seg / np.maximum.accumulate(seg) - 1).min()
        lines.append(f'  {k}: 연 {((seg[-1] / seg[0]) ** (1 / yrs) - 1) * 100:+.1f}% · MDD {mdd * 100:.0f}% · '
                     f'누적 {(seg[-1] / seg[0] - 1) * 100:+.0f}%' + (f' · 전환 {n}회(전체)' if a == day(start) else ''))
open('out.txt', 'w', encoding='utf-8').write('\n'.join(lines) + '\n')
print('\n'.join(lines))
