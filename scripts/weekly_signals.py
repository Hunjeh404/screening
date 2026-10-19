"""주봉 훈봇 신호(초강세·패닉셀) 분석: 나스닥100(^NDX)·S&P500(^GSPC), 1985년~.
1) 신호 빈도·지속 기간  2) 신호 상태일 때 다음 주 수익률  3) 신호가 처음 뜬 주 이후 1·4·13·26·52주 수익률
4) 나스닥100: 초강세 주에만 3배, 나머지는 1배(200일선 규칙 없음)로 주 단위 전환했을 때 성과
3배는 하루 수익률 3배 - (연 0.95% + 2 × 3개월 국채 금리), 1배는 지수 - 연 0.2%. 배당 미반영. 결과는 out.txt"""
import os
import sys

import numpy as np
import pandas as pd
import yfinance as yf

sys.path.insert(0, os.path.dirname(__file__))
from hunbot import daily_signals

COST = .001
out = []
say = lambda *a: out.append(' '.join(str(x) for x in a))


def get(sym):
    x = yf.download(sym, start='1985-01-01', auto_adjust=False, progress=False)
    if hasattr(x.columns, 'levels'):
        x.columns = x.columns.get_level_values(0)
    return x[['Open', 'High', 'Low', 'Close']].dropna(subset=['Close'])


def weekly(df):
    w = df.resample('W-FRI').agg({'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last'}).dropna()
    if df.index[-1].weekday() < 4:   # 끝나지 않은 주는 버림
        w = w.iloc[:-1]
    return w


def lev_daily(df, irx, lev, fee):
    r = df['Close'].pct_change().fillna(0)
    rate = irx.reindex(df.index).ffill().fillna(0) / 100
    f = (fee + (lev - 1) * rate) / 252 if lev > 1 else fee / 252
    return (1 + (r * lev - f).clip(lower=-.999999)).cumprod()


def pct(x):
    return f'{x * 100:+.1f}%'


def stats(eq, weeks):
    yrs = weeks / 52
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1
    mdd = (eq / eq.cummax() - 1).min()
    return f'연 {pct(cagr)} · MDD {mdd * 100:.0f}%'


def analyze(name, df, irx):
    w = weekly(df)
    sig = daily_signals(w['Close'].tolist(), w['High'].tolist(), w['Low'].tolist())
    s = pd.DataFrame({k: [x[k] for x in sig] for k in ('super_bull', 'panic')}, index=w.index)
    ok = s['super_bull'].notna() & s['panic'].notna()
    w, s = w[ok], s[ok].astype(bool)
    c = w['Close']
    nxt = c.shift(-1) / c - 1
    base = nxt.dropna()
    say(f'\n===== {name} 주봉 {w.index[0].date()} ~ {w.index[-1].date()} ({len(w)}주) =====')
    say(f'기준(아무 주나): 다음 주 평균 {pct(base.mean())} · 상승 확률 {(base > 0).mean() * 100:.0f}%')
    for key, label in (('super_bull', '초강세'), ('panic', '패닉셀')):
        on = s[key]
        runs = (on != on.shift()).cumsum()[on]
        lens = runs.value_counts()
        starts = on & ~on.shift(fill_value=False)
        say(f'\n[{label}] 상태인 주 {on.mean() * 100:.0f}% · 발생 {int(starts.sum())}회 · 지속 평균 {lens.mean():.1f}주(중앙 {lens.median():.0f}주)')
        a, b = nxt[on].dropna(), nxt[~on].dropna()
        say(f'  상태일 때 다음 주: 평균 {pct(a.mean())} · 상승 {(a > 0).mean() * 100:.0f}% · 연환산 {pct((1 + a.mean()) ** 52 - 1)}'
            f' | 아닐 때: 평균 {pct(b.mean())} · 상승 {(b > 0).mean() * 100:.0f}% · 변동성 상태 {a.std() * 100:.1f}% / 아님 {b.std() * 100:.1f}%')
        idx = np.where(starts.values)[0]
        for h in (1, 4, 13, 26, 52):
            f = (c.shift(-h) / c - 1)
            ev, allw = f.iloc[idx].dropna(), f.dropna()
            say(f'  처음 뜬 주 이후 {h:>2}주: 평균 {pct(ev.mean())} · 중앙 {pct(ev.median())} · 상승 {(ev > 0).mean() * 100:.0f}%'
                f' · 최악 {pct(ev.min())} (기준 평균 {pct(allw.mean())} · 상승 {(allw > 0).mean() * 100:.0f}%) n={len(ev)}')
    if name.startswith('나스닥'):
        one = lev_daily(df, irx, 1, .002).resample('W-FRI').last().reindex(w.index)
        three = lev_daily(df, irx, 3, .0095).resample('W-FRI').last().reindex(w.index)
        r1, r3 = one.pct_change().shift(-1), three.pct_change().shift(-1)
        hold3 = s['super_bull']
        sw = hold3 != hold3.shift(fill_value=False)
        rule = (np.where(hold3, r3, r1) - np.where(sw, COST, 0))
        eq = {'1배 보유': (1 + r1.fillna(0)).cumprod(), '3배 보유': (1 + r3.fillna(0)).cumprod(),
              '초강세 주만 3배·나머지 1배': (1 + pd.Series(rule, index=w.index).fillna(0)).cumprod()}
        say(f'\n[주 단위 전환: 주봉 초강세면 다음 주 3배, 아니면 1배] 전환 {int(sw.sum())}회 · 3배 보유 비중 {hold3.mean() * 100:.0f}%')
        for a, b in ((None, None), ('2000-03-10', '2010-03-19'), ('2007-10-01', '2009-03-31'), ('2013-01-01', None), ('2022-01-01', '2022-12-31')):
            seg = {k: v.loc[a:b] for k, v in eq.items()}
            say(f'  {a or "전체"}~{b or "현재"}: ' + ' | '.join(f'{k} {stats(v, len(v))} (누적 {pct(v.iloc[-1] / v.iloc[0] - 1)})'
                                                           for k, v in seg.items()))


irx = get('^IRX')['Close']
analyze('나스닥100', get('^NDX'), irx)
analyze('S&P500', get('^GSPC'), irx)
open('out.txt', 'w', encoding='utf-8').write('\n'.join(out) + '\n')
print('\n'.join(out))
