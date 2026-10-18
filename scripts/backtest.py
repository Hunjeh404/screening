"""실적 신고가 규칙 백테스트(미국).
앱과 같은 판정 코드(lib.analyze, card_rules)를 공시 시점별로 다시 돌려, 그날까지 공개된 재무와 시세만으로
매일 카드 상태를 만들고 정해진 매매 규칙으로 거래한다.
한계: 현재 상장 종목만 대상(생존 편향)이라 결과가 실제보다 좋게 나올 수 있다. 배당은 반영하지 않는다."""
import datetime as dt
import gzip
import json
import math
import os
import sys
import types
from collections import Counter
from multiprocessing import Pool

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import card_rules as cr
from lib import extract, shares_out, analyze, label, shift_label, FORMS
from hunbot import daily_signals

BT = 'data/bt'
START = '2013-01-01'
COST = .001            # 편도 거래 비용
# 전략: (이동평균 조건 사용, 목표가 트레일링 스탑 사용). 모두 최대 5종목, 매수 20% + ▲ 10%
# 전략: (공시지연 사용, 매도 방식). 모두 이평선 조건 사용, 최대 5종목, 매수 20% + ▲ 10%, 손절선 항상 유지
STRATEGIES = {(False, 'none'): '공시지연 X · 매도카드까지 보유', (False, 'ts2'): '공시지연 X · 2차목표 50% TS',
              (True, 'none'): '공시지연 O · 매도카드까지 보유', (True, 'ts2'): '공시지연 O · 2차목표 50% TS'}
PLANS = {'ts12': (('t1_done', 't1', .5, '1차', None), ('t2_done', 't2', .25, '2차', 't1_done')),
         'ts2': (('t2_done', 't2', .5, '2차', None),), 'none': ()}
OBV_NAMES = (None, '중립', '매집', '이탈')
EPOCH = dt.date(1970, 1, 1)
day_int = lambda s: (dt.date.fromisoformat(s) - EPOCH).days
day_str = lambda n: (EPOCH + dt.timedelta(days=int(n))).isoformat()
ORIG_OBV = cr.obv_signal
CAL = None             # 거래일 달력(SPY 기준), main에서 채움
DROP = ('q', 'fscore_items', 'hunbot', 'close', 'ma20', 'ma120', 'ma200', 'vol20', 'vol60', 'change_pct',
        'price_date', 'above120', 'above200', 'vol_down', 'dd52', 'marcap', 'per', 'pbr', 'share_warn')


# ---------- 가격 지표 (lib.price_metrics와 같은 값을 모든 날짜에 대해 한 번에) ----------
def rolling_mean(x, w):
    out = np.full(len(x), np.nan)
    if len(x) >= w:
        cs = np.cumsum(np.insert(x, 0, 0.0))
        out[w - 1:] = (cs[w:] - cs[:-w]) / w
    return out


def rolling_pstd_returns(c, w):
    """day k: 직전 w개 일간 수익률의 모표준편차 (k >= w일 때만)"""
    r = np.zeros(len(c))
    r[1:] = c[1:] / c[:-1] - 1
    c1, c2 = np.cumsum(np.insert(r, 0, 0)), np.cumsum(np.insert(r * r, 0, 0))
    out = np.full(len(c), np.nan)
    k = np.arange(w, len(c))
    m = (c1[k + 1] - c1[k + 1 - w]) / w
    out[k] = np.sqrt(np.maximum((c2[k + 1] - c2[k + 1 - w]) / w - m * m, 0))
    return out


def weekly_obv_codes(d, c, v):
    """day k의 52주 주봉 OBV 판정 (끝난 주봉만, 앱과 같음) -> OBV_NAMES 번호"""
    week = (d + 3) // 7                      # 월요일 시작 주
    r5 = lambda x: float(f'{x:.5g}')
    wc, wv, wid, cum = [], [], [], np.zeros(len(c))
    for k in range(len(c)):
        if not wid or week[k] != wid[-1]:
            wid.append(week[k]); wc.append(r5(c[k])); wv.append(0.0)
        wc[-1] = r5(c[k]); wv[-1] += v[k]; cum[k] = wv[-1]
    pos = np.searchsorted(np.array(wid), week)
    codes = np.zeros(len(c), np.int8)
    for k in range(len(c)):
        i = pos[k]
        if (d[k] + 3) % 7 == 4:   # 금요일: 이번 주까지 끝난 주봉
            if i < 52:
                continue
            w = {'c': wc[i - 52:i] + [r5(c[k])], 'v': [int(x) for x in wv[i - 52:i]] + [int(cum[k])]}
        else:                     # 그 외: 지난주까지
            if i < 53:
                continue
            w = {'c': wc[i - 53:i], 'v': [int(x) for x in wv[i - 53:i]]}
        codes[k] = OBV_NAMES.index(ORIG_OBV(w))
    return codes


def price_features(d, h, l, c, v):
    n = len(c)
    f = {'ma20': rolling_mean(c, 20), 'ma120': rolling_mean(c, 120), 'ma200': rolling_mean(c, 200),
         'vol20': rolling_pstd_returns(c, 20), 'vol60': rolling_pstd_returns(c, 60),
         'high52': pd.Series(c).rolling(250, min_periods=1).max().to_numpy(),
         'chg': np.concatenate([[np.nan], (c[1:] / c[:-1] - 1) * 100])}
    sig = daily_signals(c.tolist(), h.tolist(), l.tolist())
    tri = lambda x: -1 if x is None else int(bool(x))
    f['sb'] = np.array([tri(s['super_bull']) for s in sig], np.int8)
    f['sc'] = np.array([tri(s['sc']) for s in sig], np.int8)
    f['pn'] = np.array([tri(s['panic']) for s in sig], np.int8)
    f['age'] = np.array([-1 if s['lc_age'] is None else s['lc_age'] for s in sig], np.int8)
    f['obv'] = weekly_obv_codes(d, c, v)
    return f


def quarter_closes(d, c, i):
    """lib.price_metrics의 qe: 분기별 마지막 종가(현재 분기는 그날 종가), 최근 25개 분기"""
    qe = {}
    for k in range(i + 1):
        day = EPOCH + dt.timedelta(days=int(d[k]))
        qe[f'{day.year}Q{(day.month - 1) // 3 + 1}'] = float(c[k])
    return {q: qe[q] for q in sorted(qe)[-25:]}


# ---------- 공시 시점 재무 ----------
def pit(facts, T):
    """T일까지 제출된 공시 값만 남긴다"""
    return {ns: {t: {'units': {u: [e for e in es if e['filed'] <= T] for u, es in v['units'].items()}}
                 for t, v in tags.items()} for ns, tags in facts.items()}


def prepare(job):
    cik, ticker, fin = job
    pf, ff = f'{BT}/px/{ticker}.npz', f'{BT}/facts/{cik}.json.gz'
    if not (os.path.exists(pf) and os.path.exists(ff)):
        return None
    z = np.load(pf)
    d, o, h, l, c, v, spl = (z[k] for k in 'dohlcvs')
    with gzip.open(ff, 'rt', encoding='utf-8') as f:
        facts = json.load(f)
    filed = sorted({e['filed'] for tags in facts.values() for t in tags.values()
                    for es in t['units'].values() for e in es if e.get('form') in FORMS and e['filed'] >= '2011-01-01'})
    ma = {k: rolling_mean(c, w) for k, w in (('ma20', 20), ('ma120', 120), ('ma200', 200))}
    vol = {k: rolling_pstd_returns(c, w) for k, w in (('vol20', 20), ('vol60', 60))}
    hi = pd.Series(c).rolling(250, min_periods=1).max().to_numpy()
    num = lambda a, i: None if not np.isfinite(a[i]) else float(a[i])
    snaps, qcache = [], {}
    for T in filed:
        i = int(np.searchsorted(d, day_int(T), side='right'))   # 공시 다음 거래일부터 반영
        if i >= len(d) or i < 59:
            continue
        fp = pit(facts, T)
        ext, sh = extract(fp), shares_out(fp)
        if sh:   # 가격은 현재 기준으로 분할 반영되어 있으므로 주식 수도 이후 분할만큼 맞춘다
            after = (d > day_int(sh['end'])) & (spl > 0)
            sh = {'val': sh['val'] * float(np.prod(spl[after])), 'end': sh['end']}
        if i not in qcache:
            qcache[i] = quarter_closes(d, c, i)
        px = {'close': float(c[i]), **{k: num(a, i) for k, a in {**ma, **vol}.items()},
              'change_pct': (c[i] / c[i - 1] - 1) * 100, 'price_date': day_str(d[i]), 'hunbot': None,
              'high52': float(hi[i]), 'qe': qcache[i]}
        F = analyze(ext, px, sh)
        if not F:
            continue
        shares = sh['val'] if sh and label(sh['end']) >= shift_label(F['latest_q'], -3) else None
        bs = next((ext['bs'][q] for q in sorted(ext['bs'], reverse=True) if ext['bs'][q].get('equity')), {})
        F = {k: x for k, x in F.items() if k not in DROP}
        F['rec_base'] = bool((F['rev_q'] and F['rev_ttm']) or (F['op_q'] and F['op_ttm']))
        snaps.append((i, F, shares, bs.get('equity')))
    if not any(s[1]['rec_base'] for s in snaps):
        return None   # 한 번도 실적 신고가가 아니었으면 매수 카드에 오를 수 없다
    feats = price_features(d, h, l, c, v)
    # 거래일 달력에 맞춰 정렬 (백테스트 시작 이전 시세는 버림)
    pos = np.searchsorted(CAL, d)
    ok = (pos < len(CAL)) & (CAL[np.minimum(pos, len(CAL) - 1)] == d) & (np.arange(len(d)) >= 59)
    A = {}
    for k, arr in {'o': o, 'h': h, 'l': l, 'c': c, **feats}.items():
        out = np.full(len(CAL), np.nan if arr.dtype.kind == 'f' else -1, np.float32 if arr.dtype.kind == 'f' else np.int8)
        out[pos[ok]] = arr[ok]
        A[k] = out
    A['obv'][A['obv'] < 0] = 0
    ev = [(int(pos[i]) if ok[i] else int(np.searchsorted(CAL, d[i])), F, sh_, eq) for i, F, sh_, eq in snaps]
    return {'t': ticker, 'fin': fin, 'a': A, 'snaps': [e for e in ev if e[0] < len(CAL)]}


# ---------- 매매 ----------
class Book:
    """정해진 매매 규칙의 포트폴리오. 카드 신호는 장 마감 후 생기므로 다음 날 시가에 체결한다.
    ts=True: 목표가에 닿으면 해당 물량(1차 50%, 2차 25%)에 트레일링 스탑을 건다.
    ts=False: 목표가에 닿으면 그 가격에 바로 판다(1차 50%, 2차 남은 물량의 50%)."""

    def __init__(self, slots=5, ts=True, cost=COST, first=.2, add=.1, exits=None):
        # exits: 'ts12'(1차 50%·2차 25% TS) 'now12'(목표가 즉시매도) 'ts2'(2차 50% TS) 'none'(매도카드까지 보유)
        self.exits = exits or ('ts12' if ts else 'now12')
        self.slots, self.cost, self.first, self.add = slots, cost, first, add
        self.cash, self.pos, self.orders, self.closed, self.curve, self.invested = 1.0, {}, [], [], [], []

    def _sell(self, t, q, px, k, why):
        p = self.pos[t]
        q = min(q, p['sh'])
        got = q * px * (1 - self.cost)
        self.cash += got
        p['sh'] -= q
        p['proceeds'] += got
        p['events'].append(why)
        if p['sh'] <= p['base'] * 1e-9:
            self.closed.append({'ticker': t, 'entry_k': p['day'], 'exit_k': k, 'ret': p['proceeds'] / p['spent'] - 1,
                                'exit': why, 'events': '>'.join(p['events'])})
            del self.pos[t]
            return False
        return True

    def _buy(self, o, px, k):
        t = o['t']
        q = min(o['value'], self.cash) / (px * (1 + self.cost))
        if q <= 0:
            return
        spent = q * px * (1 + self.cost)
        self.cash -= spent
        if t in self.pos:
            p = self.pos[t]
            p.update(sh=p['sh'] + q, base=p['base'] + q, spent=p['spent'] + spent, added=True)
            p['events'].append('추가매수')
        else:
            m = o['metrics']
            self.pos[t] = {'sh': q, 'base': q, 'spent': spent, 'proceeds': 0.0, 'entry': px, 'last': px,
                           'loss': m['expected_loss'], 't1': m['target_1'], 't2': m['target_2'],
                           't1_done': False, 't2_done': False, 'added': o['added'], 'day': k, 'bull': False,
                           'trails': [], 'events': ['매수+추가' if o['added'] else '매수']}

    def morning(self, k, bar):
        """시가: 전날 신호 주문 체결(매도 먼저). 장중: 손절(항상), 목표가 처리(초강세 구간이면 쉰다)"""
        orders, self.orders = self.orders, []
        for o in orders:
            if o['side'] == 'sell' and o['t'] in self.pos and bar(o['t']):
                self._sell(o['t'], float('inf'), bar(o['t'])[0], k, o['why'])
        for o in orders:
            if o['side'] == 'buy' and bar(o['t']):
                self._buy(o, bar(o['t'])[0], k)
        for t in list(self.pos):
            p, b = self.pos[t], bar(t)
            if not b:
                continue
            op, hi, lo, _ = b
            stop = p['entry'] * (1 + p['loss'] / 100)   # 최초 매수가 기준 고정 손절선, 초강세에도 유지
            if lo <= stop:   # 같은 날 목표가와 겹치면 손절을 먼저 본다(보수적)
                self._sell(t, float('inf'), min(op, stop), k, '손절')
                continue
            if self.exits in PLANS:
                alive = True
                for tr in list(p['trails']):   # 걸어 둔 트레일링 스탑 (걸린 다음 날부터)
                    level = tr['peak'] * (1 + p['loss'] / 100)
                    if not p['bull'] and k > tr['k'] and lo <= level:
                        p['trails'].remove(tr)
                        alive = self._sell(t, tr['q'], min(op, level), k, tr['name'] + ' TS매도')
                        if not alive:
                            break
                    else:
                        tr['peak'] = max(tr['peak'], hi)
                if not alive:
                    continue
                for key, lvl, frac, name, need in PLANS[self.exits]:
                    if not p[key] and (need is None or p[need]) and hi >= p['entry'] * (1 + p[lvl] / 100):
                        p[key] = True
                        p['trails'].append({'q': p['base'] * frac, 'peak': hi, 'k': k, 'name': name})
                        p['events'].append(name + '목표')
            elif not p['bull']:
                for key, lvl, name in (('t1_done', 't1', '1차목표'), ('t2_done', 't2', '2차목표')):
                    if p[key] or (key == 't2_done' and not p['t1_done']):
                        continue
                    tp = p['entry'] * (1 + p[lvl] / 100)
                    if hi >= tp:
                        p[key] = True
                        if not self._sell(t, p['sh'] * .5, max(op, tp), k, name):
                            break

    def evening(self, k, bar, cards, newly):
        """종가: 평가, 다음 날 주문 생성"""
        for t, p in self.pos.items():
            b = bar(t)
            if b:
                p['last'] = b[3]
            card = cards.get(t)
            p['bull'] = bool(card and card['hunbot'].get('super_bull'))
        value = sum(p['sh'] * p['last'] for p in self.pos.values())
        eq = self.cash + value
        self.curve.append(eq)
        self.invested.append(value / eq if eq else 0)
        signal = lambda t: bool(cards.get(t) and not cards[t]['hunbot'].get('sc')
                                and cards[t]['hunbot'].get('lc_age') in (0, 1, 2))
        sells = [t for t in self.pos if cards.get(t, {}).get('state') == 'SELL']
        orders = [{'side': 'sell', 't': t, 'why': '매도카드(' + ','.join(cards[t].get('state_reasons') or []) + ')'}
                  for t in sells]
        for t, p in self.pos.items():
            if (t not in sells and not p['added'] and not p['t1_done']
                    and cards.get(t, {}).get('state') == 'BUY' and signal(t)):
                orders.append({'side': 'buy', 't': t, 'value': eq * self.add})
        free = self.slots - (len(self.pos) - len(sells))
        new = sorted((cards[t] for t in newly if t not in self.pos and cards[t]['metrics']['expected_loss'] is not None),
                     key=lambda s: (abs(s['metrics']['expected_loss']), -s['metrics']['target_1']))
        for s in new[:max(free, 0)]:
            added = signal(s['code'])
            orders.append({'side': 'buy', 't': s['code'], 'value': eq * (self.first + (self.add if added else 0)),
                           'added': added, 'metrics': s['metrics']})
        self.orders = orders


# ---------- 하루치 카드 ----------
def build(co, snap, k):
    A = co['a']
    c = A['c'][k]
    if not np.isfinite(c):
        return None
    _, F, shares, eq = snap
    g = lambda key: None if not np.isfinite(A[key][k]) else float(A[key][k])
    tri = lambda key: None if A[key][k] < 0 else bool(A[key][k])
    c = float(c)
    ma120, ma200, v20, v60, hi = g('ma120'), g('ma200'), g('vol20'), g('vol60'), g('high52')
    warn = shares is None
    marcap = c * shares if shares else None
    ni = F['ni_ttm']
    per = marcap / ni if marcap and ni and ni > 0 else None
    pbr = marcap / eq if marcap and eq and eq > 0 else None
    per_med = F['per_med5']
    if (per is not None and per < 1) or (pbr is not None and pbr < .05):
        warn, marcap, per, pbr, per_med = True, None, None, None, None
    r = lambda x, n=1: None if x is None else round(x, n)
    s = dict(F)
    s.update(code=co['t'], name=co['t'], market='', fin=co['fin'], close=c, ma20=g('ma20'), ma120=ma120,
             ma200=ma200, vol20=v20, vol60=v60, change_pct=g('chg'), price_date=day_str(CAL[k]),
             hunbot={'super_bull': tri('sb'), 'panic': tri('pn'), 'sc': tri('sc'),
                     'lc_age': None if A['age'][k] < 0 else int(A['age'][k])},
             share_warn=warn, marcap=None if marcap is None else round(marcap), per=r(per), pbr=r(pbr, 2),
             per_med5=per_med, mcap_b=marcap / 1e9 if marcap else None,
             above120=ma120 is not None and c >= ma120, above200=ma200 is not None and c >= ma200,
             vol_down=v20 is not None and v60 is not None and v20 < v60,
             dd52=r((c / hi - 1) * 100) if hi else None, record=F['rec_base'],
             wk=OBV_NAMES[int(A['obv'][k])])
    return s


# ---------- 결과 ----------
def stats(curve, days):
    e = np.array(curve)
    rets = e[1:] / e[:-1] - 1
    years = (days[-1] - days[0]) / 365.25
    return {'총수익률': e[-1] / e[0] - 1, 'CAGR': (e[-1] / e[0]) ** (1 / years) - 1,
            'MDD': float((e / np.maximum.accumulate(e) - 1).min()),
            '샤프': float(rets.mean() / rets.std() * math.sqrt(252)) if rets.std() else 0.0}


def yearly(curve, days):
    s = pd.Series(curve, index=pd.to_datetime([day_str(x) for x in days]))
    ye = s.groupby(s.index.year).last()
    return (ye / ye.shift(1).fillna(s.iloc[0]) - 1).to_dict()


def report(books, spy, days):
    pct = lambda x: f'{x * 100:+.1f}%'
    rows, years, lines = [], {}, []
    series = {name: b.curve for name, b in books.items()}
    series['SPY 보유'] = list(spy)
    for name, curve in series.items():
        st = stats(curve, days)
        years[name] = yearly(curve, days)
        b = books.get(name)
        tr = pd.DataFrame(b.closed) if b and b.closed else None
        extra = ({'거래': len(tr), '승률': f"{(tr['ret'] > 0).mean() * 100:.0f}%", '평균 손익': pct(tr['ret'].mean()),
                  '평균 보유(거래일)': f"{(tr['exit_k'] - tr['entry_k']).mean():.0f}",
                  '평균 투자 비중': f"{np.mean(b.invested) * 100:.0f}%"} if tr is not None else {})
        row = {'구분': name, '총수익률': pct(st['총수익률']), 'CAGR': pct(st['CAGR']), 'MDD': pct(st['MDD']),
               '샤프': f"{st['샤프']:.2f}", **extra}
        rows.append(row)
        lines.append(' · '.join(f'{k} {v}' for k, v in row.items()))
        if tr is not None:
            why = tr['exit'].str.split('(').str[0].value_counts()
            lines.append(name + ' 마지막 매도 사유: ' + ', '.join(f'{k} {v}' for k, v in why.items()))
    for i, (name, b) in enumerate(books.items(), 1):
        if b.closed:
            tr = pd.DataFrame(b.closed)
            tr['entry'] = [day_str(days[i]) for i in tr['entry_k']]
            tr['exit_date'] = [day_str(days[i]) for i in tr['exit_k']]
            tr.insert(0, 'strategy', name)
            tr[['strategy', 'ticker', 'entry', 'exit_date', 'ret', 'exit', 'events']].to_csv(
                f'docs/backtest_trades_{i}.csv', index=False)
    # 자산 곡선 (로그 축)
    W, H, P = 760, 300, 36
    allv = np.log([x for c in series.values() for x in c])
    lo, hi = allv.min(), allv.max()
    colors = ['#5fe0c2', '#f2c14e', '#e8796b', '#8fb8ff', '#8aa0a6']
    paths = []
    for (name, curve), col in zip(series.items(), colors):
        step = max(1, len(curve) // 600)
        pts = [(P + i / (len(curve) - 1) * (W - 2 * P), H - P - (math.log(v) - lo) / (hi - lo or 1) * (H - 2 * P))
               for i, v in list(enumerate(curve))[::step]]
        paths.append(f'<polyline fill="none" stroke="{col}" stroke-width="2" points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in pts)}"/>')
    legend = ''.join(f'<span style="color:{col}">■ {name}</span>' for name, col in zip(series, colors))
    allkeys = list(dict.fromkeys(k for r in rows for k in r))
    table = '<tr>' + ''.join(f'<th>{k}</th>' for k in allkeys) + '</tr>' + ''.join(
        '<tr>' + ''.join(f'<td>{r.get(k, "")}</td>' for k in allkeys) + '</tr>' for r in rows)
    ys = sorted({y for v in years.values() for y in v})
    ytable = '<tr><th>연도</th>' + ''.join(f'<th>{n}</th>' for n in years) + '</tr>' + ''.join(
        f'<tr><td>{y}</td>' + ''.join(f'<td>{pct(years[n].get(y, 0))}</td>' for n in years) + '</tr>' for y in ys)
    html = f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>백테스트 결과</title><style>
:root{{--bg:#0b1513;--panel:#11201d;--line:#1f3631;--text:#e3efec;--sub:#87a39c}}
body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.6 system-ui,sans-serif}}
.wrap{{max-width:900px;margin:0 auto;padding:20px 16px 60px}} h1{{font-size:20px}} h2{{font-size:15px;margin-top:28px}}
.box{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px;overflow-x:auto}}
table{{border-collapse:collapse;width:100%;white-space:nowrap}} th,td{{padding:6px 8px;border-bottom:1px solid var(--line);text-align:right}}
th:first-child,td:first-child{{text-align:left}} th{{color:var(--sub);font-weight:500}} .legend span{{margin-right:14px}}
li{{color:var(--sub)}} svg{{width:100%;height:auto}}
</style></head><body><div class="wrap">
<h1>실적 신고가 전략 백테스트 (미국)</h1><p style="color:var(--sub)">{day_str(days[0])} ~ {day_str(days[-1])} · 편도 비용 {COST * 100:.1f}% · 배당 미반영</p>
<div class="box"><table>{table}</table></div>
<h2>자산 곡선 (로그 축)</h2><div class="box"><div class="legend">{legend}</div><svg viewBox="0 0 {W} {H}">{"".join(paths)}</svg></div>
<h2>연도별 수익률</h2><div class="box"><table>{ytable}</table></div>
<h2>규칙과 한계</h2><ul>
<li>공통: 최대 5종목. 매수 카드에 새로 오르면 20%, 매수 카드 상태에서 훈봇 ▲ 신호가 뜨면 10% 추가(같은 날이면 30%). 같은 날 여러 종목이면 예상 손실률이 작은 순, 1차 목표 수익률이 높은 순. 보유 종목이 꽉 차면 건너뜀.</li>
<li>손절: 최초 매수가에 예상 손실률을 적용한 고정 가격이 항상 걸려 있음(초강세 구간 포함). 매도 카드가 뜨면 전량.</li>
<li>매도카드까지 보유: 손절선과 매도 카드로만 매도. 2차목표 50% TS: 2차 목표가에 닿으면 물량의 50%에 트레일링 스탑(최고가 대비 예상 손실률)을 걸고, 나머지는 매도 카드로 매도. 초강세 구간에서는 트레일링 스탑을 쉼.</li>
<li>이평선 조건 사용. 공시지연 O: 최신 재무가 시장에서 가장 많은 종목이 도달한 분기보다 2분기 이상 늦으면 매수 제외·매도 사유.</li>
<li>카드 신호는 다음 날 시가에 체결, 목표가·손절은 장중 고가·저가에 닿으면 그 가격(갭이면 시가)에 체결. 같은 날 손절과 목표가가 겹치면 손절로 처리.</li>
<li>재무는 공시 다음 거래일부터 반영. 현재 상장 종목만 대상이라 상장폐지 종목이 빠진 생존 편향이 있어 결과가 실제보다 좋게 나올 수 있음.</li>
</ul></div></body></html>'''
    open('docs/backtest.html', 'w', encoding='utf-8').write(html)
    return lines


def main():
    global CAL
    listing = json.load(open('data/cache/listing.json', encoding='utf-8'))
    sic = json.load(open('data/cache/sic.json', encoding='utf-8'))
    z = np.load(f'{BT}/px/SPY.npz')
    i0 = int(np.searchsorted(z['d'], day_int(START)))
    CAL, spy = z['d'][i0:], z['c'][i0:]
    jobs = [(cik, v['ticker'], str(sic.get(cik, ''))[:2] in ('60', '61', '62', '63', '64', '67'))
            for cik, v in listing.items()]
    with Pool() as pool:
        cos = [x for x in pool.imap_unordered(prepare, jobs, chunksize=8) if x]
    print('준비 완료', len(cos), '종목', flush=True)

    # 앱과 같은 판정 코드를 쓰되, 주봉 OBV는 미리 계산한 판정을 쓰고 이력 스냅숏은 얕은 복사로 둔다(속도)
    cr.obv_signal = lambda w: w
    cr.copy = types.SimpleNamespace(deepcopy=lambda x: dict(x) if isinstance(x, dict) else x)
    events = {}
    for ci, co in enumerate(cos):
        for snap in co['snaps']:
            events.setdefault(snap[0], []).append((ci, snap))
    index = {co['t']: ci for ci, co in enumerate(cos)}
    cur, rec, started = [None] * len(cos), set(), False
    quarters = Counter()   # 종목별 최신 재무 분기 분포 -> 최빈 분기가 공시지연 기준
    histories = {stale: {} for stale in (False, True)}
    books = {key: Book(exits=key[1]) for key in STRATEGIES}
    cr.USE_MA = True
    for k in range(len(CAL)):
        for ci, snap in events.get(k, ()):
            if cur[ci]:
                quarters[cur[ci][1]['latest_q']] -= 1
            cur[ci] = snap
            quarters[snap[1]['latest_q']] += 1
            started = True
            (rec.add if snap[1]['rec_base'] else rec.discard)(ci)
        fresh = shift_label(quarters.most_common(1)[0][0], -1) if started else None
        day = day_str(CAL[k])
        bar = lambda t, k=k: (lambda A: (float(A['o'][k]), float(A['h'][k]), float(A['l'][k]), float(A['c'][k]))
                              if np.isfinite(A['c'][k]) and np.isfinite(A['o'][k]) else None)(cos[index[t]]['a'])
        for b in books.values():
            b.morning(k, bar)
        held = {index[key[3:]] for h in histories.values() for key in h.get('tickers', {}) if key[3:] in index}
        base = [s for ci in rec | held if started and cur[ci] and (s := build(cos[ci], cur[ci], k))]
        for s in base:
            s['stale'] = s['latest_q'] < fresh
        for stale, history in histories.items():   # 공시지연 켬/끔은 카드 상태가 달라지므로 따로 추적
            cr.USE_STALE = stale
            stocks = [dict(s) for s in base]
            cr.update_history(stocks, history, 'us', f'{day} 08:00')
            cards, newly = {}, []
            for s in stocks:
                if s.get('state') and not s.get('data_missing'):
                    cards[s['code']] = s
                    last = (s.get('history') or {}).get('transitions') or [{}]
                    if s['state'] == 'BUY' and last[-1].get('to') == 'BUY' and last[-1].get('date') == day:
                        newly.append(s['code'])
            for exits in ('none', 'ts2'):
                books[(stale, exits)].evening(k, bar, cards, newly)
        if k % 250 == 0:
            print(day, '보유', [len(b.pos) for b in books.values()], flush=True)
    cr.USE_STALE = False
    for line in report({STRATEGIES[key]: b for key, b in books.items()}, spy, CAL):
        print('::warning title=backtest::' + line)


if __name__ == '__main__':
    main()
