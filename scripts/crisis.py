"""지수만으로 1986년~ 비교(합성): 공황·버블 구간 포함. 종목 매매는 2013년 이전 데이터가 없어 넣지 못한다.
3배는 하루 수익률 3배 - (연 0.95% + 2 × 3개월 국채 금리), QQQ 대용은 나스닥100 - 연 0.2%, SPY 대용은 S&P500 - 연 0.1%(배당 미반영).
TQQQ 200일선: 나스닥100 종가로 판정해 다음 날 시가에 TQQQ·현금 전환(regime.run과 같은 방식). 반반 조합은 매일 반반으로 맞춘다고 가정."""
import datetime as dt
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import stress_tqqq as st
import regime as rg   # 같은 합성 3배·200일선 판정·다음 날 시가 전환을 그대로 쓴다

d, c = rg.d, rg.c
d2, o2, c2 = st.load('^GSPC')
j = np.clip(np.searchsorted(d2, d, 'right') - 1, 0, None)
_, S1 = st.synth(o2[j], c2[j], 1, .001 / 252)
Q1 = rg.C1
T200, _ = rg.run(lambda a, b: 'C' if a else 'T')
T3 = rg.C3
r = lambda x: np.r_[0, x[1:] / x[:-1] - 1]
mix = lambda x, y: np.cumprod(1 + .5 * r(x) + .5 * r(y))
cash = np.ones(len(c))
S = {'QQQ 보유': Q1, 'SPY 보유': S1, 'TQQQ 보유': T3, 'TQQQ 200일선': T200,
     'TQQQ 50%(200일선) + 현금 50%': mix(T200, cash), 'TQQQ 50%(200일선) + QQQ 50%': mix(T200, Q1),
     'TQQQ 50%(200일선) + SPY 50%': mix(T200, S1)}
idx = lambda s_: int(np.searchsorted(d, (dt.date.fromisoformat(s_) - dt.date(1970, 1, 1)).days))
start = int(np.argmax(rg.valid))
out = []
for name, a, b in (('전체', None, None), ('1987 블랙먼데이', '1987-08-25', '1987-12-04'),
                   ('닷컴 버블 붕괴', '2000-03-10', '2002-10-09'), ('닷컴 이후 10년', '2000-03-10', '2010-03-19'),
                   ('금융위기', '2007-10-09', '2009-03-09'), ('코로나 폭락', '2020-02-19', '2020-03-23'),
                   ('2022 하락장', '2022-01-03', '2022-12-28'), ('2013년 이후', '2013-01-02', None)):
    ia, ib = max(idx(a), start) if a else start, idx(b) if b else len(d) - 1
    yrs = (ib - ia) / 252
    out.append(f'\n[{name} {rg.day(ia)} ~ {rg.day(ib)}]')
    for k_, x in S.items():
        seg = x[ia:ib + 1]
        mdd = (seg / np.maximum.accumulate(seg) - 1).min()
        cagr = (seg[-1] / seg[0]) ** (1 / yrs) - 1 if yrs >= 1 else None
        out.append(f'  {k_}: 누적 {(seg[-1] / seg[0] - 1) * 100:+.1f}%' + (f' · 연 {cagr * 100:+.1f}%' if cagr is not None else '')
                   + f' · MDD {mdd * 100:.0f}%')
open('out.txt', 'w', encoding='utf-8').write('\n'.join(out) + '\n')
print('\n'.join(out))
