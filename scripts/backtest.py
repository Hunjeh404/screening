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
# 전략: (이름, 이평선 조건 사용, Book 설정). 모두 공시지연 X·1차 50%/2차 25% TS, 손절선 항상 유지, 초강세에 TS 쉼
# 우수전략: 최대 5종목, 매수 20% + ▲ 10%. spy: 남는 현금은 SPY로 보유
_B = dict(spy=True, pause=False)
_T = dict(_B, idx='TQQQ', idx_ma=True)
_O = dict(_T, idx_open=True)
_O = dict(_T, idx_open=True)
_R = dict(first=.15, add=.075, slots=6)
_P = dict(_O, idx_cap=.5)
_X = dict(_P, tax=.22)   # 세후
_D = dict(seed=50, monthly=50, tax=.22, tax_ded=250)   # 50만원으로 시작, 매월 25일 50만원 추가, 양도세 22%·연 250만원 공제
_V = {'d_now': ('지금 전략', True, dict(_P, **_D))}
for e_ in ('TQQQ', 'SPY', 'QQQ'):
    _V['d_' + e_] = (f'{e_} 적립(100%)', True, dict(_O, idx=e_, idx_ma=False, slots=0, **_D))
    _V['d_' + e_ + 'ma'] = (f'{e_} 200일선 매매(100%)' + (' · QQQ 200일선' if e_ == 'TQQQ' else ''), True,
                            dict(_O, idx=e_, slots=0, own_ma=e_ != 'TQQQ', **_D))
STRATEGIES = {k: v for k, v in _V.items()}
BE = {'be25': .25, 'be0': 0.0}   # 1차 목표 도달 시 익절 비율(처음 물량 대비)
PLANS = {'n1': (('t1_done', 't1', .5, '1차', None, 'now'),),
         'wk1': (('t1_done', 't1', .5, '1차', None),),
         'ts12': (('t1_done', 't1', .5, '1차', None), ('t2_done', 't2', .25, '2차', 't1_done')),
         'n1t2': (('t1_done', 't1', .5, '1차', None, 'now'), ('t2_done', 't2', .25, '2차', 't1_done')),
         'ts2': (('t2_done', 't2', .5, '2차', None),), 'none': ()}
OBV_NAMES = (None, '중립', '매집', '이탈')
EPOCH = dt.date(1970, 1, 1)
day_int = lambda s: (dt.date.fromisoformat(s) - EPOCH).days
day_str = lambda n: (EPOCH + dt.timedelta(days=int(n))).isoformat()
ORIG_OBV = cr.obv_signal
CAL = None             # 거래일 달력(SPY 기준), main에서 채움
QQQ_OC = None          # QQQ (시가, 종가) 거래일 달력 기준, 거래별 대조군 계산용
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
    # 미너비니 추세 템플릿(상대강도 빼고): 종가 > 50·150·200일선, 50 > 150 > 200일선, 200일선이 한 달(21거래일) 전보다 높음,
    # 52주 저점 대비 +30% 이상, 52주 고점 대비 -25% 이내
    m50, m150, m200 = rolling_mean(c, 50), rolling_mean(c, 150), f['ma200']
    lo52 = pd.Series(c).rolling(250, min_periods=250).min().to_numpy()
    up = np.r_[np.zeros(21, bool), m200[21:] > m200[:-21]]
    with np.errstate(invalid='ignore'):
        f['tt'] = ((c > m50) & (m50 > m150) & (m150 > m200) & (c > m200) & up
                   & (c >= lo52 * 1.3) & (c >= f['high52'] * .75)).astype(np.int8)
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

    def __init__(self, slots=5, ts=True, cost=COST, first=.2, add=.1, exits=None, entry='open', cap=5.0,
                 pause=True, regime=False, breakeven=False, spy=False, card_lv=None, idx='SPY', idx_ma=False,
                 keep=0.0, idx_open=False, card_px=False, sector_rank=False, idx_cap=None, idx_buf=False, ta_mode=None,
                 only_ta=False, fund='cash', tgt=None, wk=None, lock=False, tax=0.0, no_gen=False, earn=False, avg_stop=False, max_age=None, ex_sec=(), own_ma=False, seed=None, monthly=0.0, tax_ded=0.0):
        # seed·monthly: 시작 자금과 매월 25일(휴일이면 다음 거래일) 추가 입금(만원). tax_ded: 양도세 연 기본공제(만원)
        self.seed, self.monthly, self.tax_ded, self.paid_in, self.flows, self.last_in = seed, monthly, tax_ded, 0.0, [], None
        self.own_ma = own_ma   # 200일선 매매를 QQQ 대신 보유 ETF 자기 200일선으로
        # max_age: 실적 공시 후 이 거래일 수가 지난 종목은 신규 매수 안 함. ex_sec: 이 섹터(야후 영문)는 신규 매수 안 함
        self.max_age, self.ex_sec = max_age, set(ex_sec)
        self.avg_stop = avg_stop   # ▲ 추가매수 직후 손절선을 평단(수수료 포함 평균 매입가)으로 올림
        # tax: 해외주식 양도세율. 매년 실현손익(종목+지수)을 합산해 이익이면 다음 해 첫 거래일 시가에 현금(모자라면 지수 매도)에서 낸다
        self.tax, self.gain_y, self.spy_cost, self.tax_paid = tax, 0.0, 0.0, 0.0
        # no_gen: 일반 기업 경로는 사지 않음(금융·턴어라운드만). earn: 실적발표 전 5거래일~후 2거래일엔 신규 매수 안 함
        self.no_gen, self.earn = no_gen, earn
        self.earn_block = lambda t, k: False
        self.lock = lock   # 1차 익절 뒤 2차 목표에 닿으면 손절선을 1차 목표가로 올림
        # tgt: 'no7' 2차 = max(가치 회복 여력, 손절률 3배)이고 2차가 1차 이하면 매수 안 함
        #      'val3' 일반 기업은 5년 중앙 PER까지 여력이 손절률 3배 이상일 때만 매수, 2차 = 그 가격
        # wk: 1차 목표 50% 트레일링 스탑 뒤 남은 물량은 주봉 종가가 N주선 아래로 마감하면 다음 날 시가에 매도
        self.tgt, self.wk = tgt, wk
        self.wk_break = lambda t, k, n: None
        # fund: 종목 매수 자금을 'cash' 현금 먼저(모자라면 TQQQ 매도), 'tqqq' TQQQ 먼저(종목 몫만큼 TQQQ 목표 비중을 줄임)
        self.fund = fund
        # only_ta: 적자 턴어라운드 경로 종목만 산다
        self.only_ta = only_ta
        # ta_mode(적자 턴어라운드 경로): 'normal' 손절·목표를 일반 기업과 같은 폭으로, 'card' 손절·TS 없이 매도 카드에서만 매도
        self.ta_mode = ta_mode
        # idx_cap: 지수(TQQQ) 보유를 총자산의 이 비율까지만(넘는 현금은 현금으로). idx_buf: 200일선 ±1.5% 버퍼
        self.idx_cap, self.idx_buf = idx_cap, idx_buf
        # sector_rank: 같은 날 여러 종목이면 저평가(매수 카드) 전체에서 종목이 가장 많은 섹터 순, 같은 수면 섞어서 기존 순서
        self.sector_rank, self.sector = sector_rank, {}
        # card_px: 손절·목표가를 매수가가 아니라 카드에 뜬 날 종가 기준 가격으로
        self.card_px = card_px
        # idx_open: 지수 매매(남는 돈 매수, 200일선 매도·매수)를 종가 대신 다음 날 시가에
        self.idx_open = idx_open
        # keep: 매도 카드 매도 시 그 종목 총 성적이 수익이면 최초 물량의 keep만큼은 팔지 않고 끝까지 보유(슬롯 미포함)
        self.keep, self.core = keep, []
        # card_lv(wait_sig): 손절·목표가를 카드가 처음 뜬 날 종가 기준 가격으로. 매수 시가가 그 손절가 이하면
        #   'recalc': 매수가 기준으로 다시 계산, 'skip': 사지 않고 대기 취소
        self.card_lv, self.card0 = card_lv, {}
        # spy: 남는 현금을 SPY로 들고 있다가 매수 자금이 모자라면 시가에 필요한 만큼 판다 (spy_bar: k -> (시가, 종가))
        self.spy, self.spy_sh, self.spy_bar = spy, 0.0, None
        self.ma_ok = lambda t, k: False
        # idx: 남는 돈으로 살 지수(SPY·QQQ·TQQQ). idx_ma: QQQ 종가가 200일선 아래면 지수를 팔고 현금 보유
        self.idx, self.idx_ma, self.idx_ok = idx, idx_ma, None
        # entry 'wait': 매수 카드에 오른 뒤 ▲ 또는 초강세가 확인되면 다음 날 시가 (기다리는 종목은 슬롯 미포함)
        # pause: 초강세 구간에 트레일링 스탑을 쉼. regime: SPY가 200일선 아래면 신규 매수 안 함
        # breakeven: 1차 목표가 도달 후 손절가를 매수가로 올림
        self.pause, self.regime, self.breakeven, self.watch, self.mkt_ok = pause, regime, breakeven, set(), True
        # exits: 'ts12'(1차 50%·2차 25% TS) 'now12'(목표가 즉시매도) 'ts2'(2차 50% TS) 'none'(매도카드까지 보유)
        # entry: 'open'(카드 다음 날 시가) 'limitA'(손절가까지 cap% 이내 지정가, ▲면 시가 즉시) 'limitB'(지정가만)
        self.exits = exits or ('ts12' if ts else 'now12')
        self.entry, self.cap = entry, cap
        self.slots, self.cost, self.first, self.add = slots, cost, first, add
        self.cash, self.pos, self.orders, self.closed, self.curve, self.invested = 1.0, {}, [], [], [], []
        self.pending, self.last_eq = {}, 1.0
        if seed:   # 적립식: 단위(좌수)로 시간가중 수익률(nav)을 따로 기록
            self.cash = self.last_eq = self.paid_in = self.units = float(seed)
            self.flows, self.nav = [(0, -float(seed))], []

    def _tgt(self, s):
        """매수 후보의 손절·목표(카드 값 또는 바꾼 규칙). None이면 매수하지 않는다."""
        m = s['metrics']
        if not self.tgt or m.get('expected_loss') is None:
            return m
        r, t1 = abs(m['expected_loss']), m['target_1']
        per, med = s.get('per'), s.get('per_med5')
        val = (med / per - 1) * 100 if cr.positive(per) and cr.positive(med) and med > per else 0
        if s.get('fin') and cr.positive(s.get('pbr')) and s['pbr'] < 1:
            val = max(val, (1 / s['pbr'] - 1) * 100)
        if self.tgt == 'no7':
            t2 = max(val, r * 3)
        else:   # val3: 일반 기업만
            if s.get('fin') or cr.loss_turnaround(s):
                return m
            if val < r * 3:
                return None
            t2 = val
        return None if t2 <= t1 else {**m, 'target_2': round(t2, 1)}

    def _sell(self, t, q, px, k, why):
        p = self.pos[t]
        q = min(q, p['sh'])
        got = q * px * (1 - self.cost)
        self.cash += got
        self.gain_y += got - q * p['spent'] / p['base']
        p['sh'] -= q
        p['proceeds'] += got
        p['events'].append(why)
        if p['sh'] <= p['base'] * 1e-9:
            self.closed.append({'ticker': t, 'entry_k': p['day'], 'exit_k': k, 'ret': p['proceeds'] / p['spent'] - 1,
                                'exit': why, 'events': '>'.join(p['events']), **p.get('tag', {})})
            del self.pos[t]
            return False
        return True

    def _idx_buy(self, amt, px):
        self.spy_sh += amt / (px * (1 + self.cost))
        self.spy_cost += amt

    def _idx_sell(self, q, px):
        q = min(q, self.spy_sh)
        if q <= 0:
            return
        basis = self.spy_cost * q / self.spy_sh
        got = q * px * (1 - self.cost)
        self.spy_sh -= q
        self.spy_cost -= basis
        self.cash += got
        self.gain_y += got - basis

    def _contribute(self, k):
        """매월 25일 이후 첫 거래일 시가 전에 입금"""
        if not self.monthly:
            return
        d_ = day_str(CAL[k])
        if int(d_[8:]) < 25 or d_[:7] == self.last_in:
            return
        self.last_in = d_[:7]
        self.units += self.monthly / (self.last_eq / self.units)
        self.cash += self.monthly
        self.last_eq += self.monthly
        self.paid_in += self.monthly
        self.flows.append((k, -self.monthly))

    def _pay_tax(self, k):
        """해가 바뀐 첫 거래일 시가: 지난해 실현이익의 tax만큼 납부"""
        if not self.tax or k == 0 or day_str(CAL[k])[:4] == day_str(CAL[k - 1])[:4]:
            return
        due, self.gain_y = max(self.gain_y - self.tax_ded, 0.0) * self.tax, 0.0
        if due > self.cash and self.spy_sh:
            op = self.spy_bar(k)[0]
            self._idx_sell((due - self.cash) / (op * (1 - self.cost)), op)
        self.cash -= due
        self.tax_paid += due

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
            if self.avg_stop:
                p['stop'] = max(p['stop'], p['spent'] / p['base'])
            p['events'].append('추가매수')
        else:
            m = o['metrics']
            lv = o.get('levels') or {}   # 지정가 매수: 카드 손절가·목표가(가격). 없으면 매수가 기준 비율
            if lv and o.get('below') and px <= lv['stop']:
                if o['below'] == 'skip':
                    self.watch.discard(t)
                    self.cash += spent
                    return
                lv = {}
            if self.entry in ('limitA', 'limitB') and not lv:   # ▲ 즉시 매수: 매수가 -cap% 손절, 목표가는 카드 가격
                lv = {'stop': px * (1 - self.cap / 100), 'tp1': o['card_close'] * (1 + m['target_1'] / 100),
                      'tp2': o['card_close'] * (1 + m['target_2'] / 100), 'trail': -self.cap}
            self.pos[t] = {'sh': q, 'base': q, 'spent': spent, 'proceeds': 0.0, 'entry': px, 'last': px,
                           'stop': lv.get('stop', px * (1 + m['expected_loss'] / 100)),
                           'tp1': lv.get('tp1', px * (1 + m['target_1'] / 100)),
                           'tp2': lv.get('tp2', px * (1 + m['target_2'] / 100)),
                           'trail': lv.get('trail', m['expected_loss']),
                           't1_done': False, 't2_done': False, 'added': o['added'], 'day': k, 'bull': False,
                           'trails': [], 'events': [o.get('label') or ('매수+추가' if o['added'] else '매수')],
                           'tag': o.get('tag', {}), 'nostop': o.get('nostop', False)}

    def morning(self, k, bar):
        """시가: 전날 신호 주문 체결(매도 먼저). 장중: 손절(항상), 목표가 처리(초강세 구간이면 쉰다)"""
        orders, self.orders = self.orders, []
        self._contribute(k)
        self._pay_tax(k)
        for o in orders:
            if o['side'] == 'sell' and o['t'] in self.pos and bar(o['t']):
                t, op, p = o['t'], bar(o['t'])[0], self.pos[o['t']]
                left = min(p['base'] * self.keep, p['sh'])
                if left and p['proceeds'] + p['sh'] * op * (1 - self.cost) > p['spent']:
                    self._sell(t, p['sh'] - left, op, k, o['why'] + '·10% 보유')
                    self.closed.append({'ticker': t, 'entry_k': p['day'], 'exit_k': k, 'exit': o['why'] + '·10% 보유',
                                        'ret': (p['proceeds'] + left * op * (1 - self.cost)) / p['spent'] - 1,
                                        'events': '>'.join(p['events'])})
                    self.core.append({'t': t, 'sh': left, 'last': op})
                    del self.pos[t]
                else:
                    self._sell(t, float('inf'), op, k, o['why'])
        need = sum(o['value'] for o in orders if o['side'] == 'buy' and bar(o['t']))
        if self.spy and self.spy_sh and (need > self.cash or (self.fund == 'tqqq' and need > 0)):
            op = self.spy_bar(k)[0]
            self._idx_sell((need if self.fund == 'tqqq' else need - self.cash) / (op * (1 - self.cost)), op)
        for o in orders:
            if o['side'] == 'buy' and bar(o['t']):
                self._buy(o, bar(o['t'])[0], k)
        if self.spy and self.idx_open and k > 0:   # 전날 종가 기준 판정으로 시가에 지수 매매
            op = self.spy_bar(k)[0]
            if self.idx_ma and not self.idx_ok(k - 1):
                self._idx_sell(self.spy_sh, op)
            elif self.cash > 0:
                held = sum(p['sh'] * p['last'] for p in self.pos.values()) if self.fund == 'tqqq' else 0.0
                amt = self.cash if self.idx_cap is None else min(self.cash, max(0.0, self.idx_cap * self.last_eq - held - self.spy_sh * op))
                self._idx_buy(amt, op)
                self.cash -= amt
        for t in list(self.pending):   # 지정가: 장중 저가가 지정가에 닿으면 그 가격(갭 하락이면 시가)
            o, b = self.pending[t], bar(t)
            if b and b[2] <= o['limit']:
                del self.pending[t]
                frac = self.first + (self.add if o['added'] else 0)
                self._buy({**o, 'value': self.last_eq * frac}, min(b[0], o['limit']), k)
        for t in list(self.pos):
            p, b = self.pos[t], bar(t)
            if not b:
                continue
            op, hi, lo, _ = b
            if p.get('nostop'):   # 매도 카드에서만 판다
                continue
            stop = p['stop']   # 고정 손절선, 초강세에도 유지
            if lo <= stop:   # 같은 날 목표가와 겹치면 손절을 먼저 본다(보수적)
                self._sell(t, float('inf'), min(op, stop), k, '손절')
                continue
            if self.exits in BE:   # 1차: 손절선을 매입가로(+일부 익절), 2차: 남은 물량의 50% 익절
                first = BE[self.exits]
                if not p['t1_done'] and hi >= p['tp1']:
                    p['t1_done'] = True
                    p['stop'] = max(p['stop'], p['entry'])
                    p['events'].append('1차목표')
                    if first and not self._sell(t, p['base'] * first, max(op, p['tp1']), k, '1차익절'):
                        continue
                if p['t1_done'] and not p['t2_done'] and hi >= p['tp2']:
                    p['t2_done'] = True
                    if not self._sell(t, p['sh'] * .5, max(op, p['tp2']), k, '2차익절'):
                        continue
            elif self.exits in PLANS:
                alive = True
                for tr in list(p['trails']):   # 걸어 둔 트레일링 스탑 (걸린 다음 날부터)
                    level = tr['peak'] * (1 + p['trail'] / 100)
                    if not (p['bull'] and self.pause) and k > tr['k'] and lo <= level:
                        p['trails'].remove(tr)
                        alive = self._sell(t, tr['q'], min(op, level), k, tr['name'] + ' TS매도')
                        if not alive:
                            break
                    else:
                        tr['peak'] = max(tr['peak'], hi)
                if not alive:
                    continue
                for key, lvl, frac, name, need, *now in PLANS[self.exits]:
                    if not p[key] and (need is None or p[need]) and hi >= p['tp' + lvl[1]]:
                        p[key] = True
                        if self.breakeven and key == 't1_done':
                            p['stop'] = max(p['stop'], p['entry'])
                        if now:   # 목표가에 닿으면 그 가격(갭이면 시가)에 바로 익절
                            p['events'].append(name + '목표')
                            if not self._sell(t, p['base'] * frac, max(op, p['tp' + lvl[1]]), k, name + '익절'):
                                break
                            continue
                        p['trails'].append({'q': p['base'] * frac, 'peak': hi, 'k': k, 'name': name})
                        p['events'].append(name + '목표')
                if self.lock and p['t1_done'] and t in self.pos and hi >= p['tp2'] and p['stop'] < p['tp1']:
                    p['stop'] = p['tp1']
                    p['events'].append('2차목표(손절선 1차로)')
            elif not p['bull']:
                for key, lvl, name in (('t1_done', 't1', '1차목표'), ('t2_done', 't2', '2차목표')):
                    if p[key] or (key == 't2_done' and not p['t1_done']):
                        continue
                    tp = p['tp' + lvl[1]]
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
        for c in self.core:   # 묻어 둔 물량 평가
            b = bar(c['t'])
            if b:
                c['last'] = b[3]
        value = sum(p['sh'] * p['last'] for p in self.pos.values()) + sum(c['sh'] * c['last'] for c in self.core)
        ok = (not self.idx_ma or self.idx_ok(k)) and not self.idx_open
        if self.spy and self.spy_sh and not ok and not self.idx_open:   # 200일선 아래: 종가에 지수 전량 매도
            self._idx_sell(self.spy_sh, self.spy_bar(k)[1])
        if self.spy and self.cash > 0 and ok:   # 남는 현금은 종가에 지수 매수
            self._idx_buy(self.cash, self.spy_bar(k)[1])
            self.cash = 0.0
        eq = self.cash + value + (self.spy_sh * self.spy_bar(k)[1] if self.spy else 0)
        self.last_eq = eq
        self.curve.append(eq)
        if self.seed:
            self.nav.append(eq / self.units)
        self.invested.append(value / eq if eq else 0)
        eq -= sum(c['sh'] * c['last'] for c in self.core)   # 매수 비중은 묻어 둔 물량을 뺀 자산 기준
        signal = lambda t: bool(cards.get(t) and not cards[t]['hunbot'].get('sc')
                                and cards[t]['hunbot'].get('lc_age') in (0, 1, 2))
        def want_sell(t, p):
            sell_card = cards.get(t, {}).get('state') == 'SELL'
            if self.exits in BE and (sell_card or p.get('hold_over')):
                # 1차 목표에 닿은 종목은 매도카드여도 이평선 조건(종가 ≥ 120일선 또는 200일선)이 살아 있으면 계속 보유
                if p['t1_done'] and self.ma_ok(t, k):
                    p['hold_over'] = True
                    return False
                return True
            return sell_card
        sells = [t for t, p in self.pos.items() if want_sell(t, p)]
        if self.wk:   # 1차 목표에 닿은 종목: 주봉 종가가 N주선 아래로 마감하면 남은 물량 매도
            # 1차 뒤 주봉 종가가 N주선 위로 한 번 마감한 다음, 다시 아래로 마감하면 매도
            wk_out = []
            for t, p in self.pos.items():
                if t in sells or not p['t1_done']:
                    continue
                v = self.wk_break(t, k, self.wk)
                if v is False:
                    p['wk_up'] = True
                elif v and p.get('wk_up'):
                    wk_out.append(t)
        else:
            wk_out = []
        orders = [{'side': 'sell', 't': t, 'why': ('매도카드(' + ','.join(cards[t].get('state_reasons') or []) + ')')
                   if cards.get(t, {}).get('state') == 'SELL' else '이평선 이탈'} for t in sells]
        orders += [{'side': 'sell', 't': t, 'why': f'{self.wk}주선 이탈'} for t in wk_out]
        sells = sells + wk_out
        for t, p in self.pos.items():
            if (t not in sells and not p['added'] and not p['t1_done']
                    and cards.get(t, {}).get('state') == 'BUY' and signal(t)):
                orders.append({'side': 'buy', 't': t, 'value': eq * self.add})
        # 걸어 둔 지정가: 매수 카드에서 빠지면 취소. A는 ▲가 뜨면 다음 날 시가에 30% 매수로 바꾼다
        for t in list(self.pending):
            if cards.get(t, {}).get('state') != 'BUY':
                del self.pending[t]
            elif self.entry == 'limitA' and signal(t):
                o = self.pending.pop(t)
                orders.append({**o, 'side': 'buy', 'value': eq * (self.first + self.add), 'added': True,
                               'levels': None, 'label': '▲시가매수'})
            else:
                self.pending[t]['added'] = signal(t)
        if self.entry in ('wait', 'wait_sig'):   # 기다리는 종목(wait_sig는 ▲만 기다림): 매수 카드에서 빠지면 제외, ▲·초강세 확인 시 다음 날 시가 20%
            for t in newly:
                if self.card_lv and t not in self.watch and cards[t]['metrics']['expected_loss'] is not None:
                    c, m = cards[t]['close'], cards[t]['metrics']
                    self.card0[t] = {'stop': c * (1 + m['expected_loss'] / 100), 'tp1': c * (1 + m['target_1'] / 100),
                                     'tp2': c * (1 + m['target_2'] / 100), 'trail': m['expected_loss']}
            self.watch = {t for t in self.watch | set(newly) if t not in self.pos
                          and cards.get(t, {}).get('state') == 'BUY' and cards[t]['metrics']['expected_loss'] is not None}
            self.card0 = {t: v for t, v in self.card0.items() if t in self.watch}
            go = [cards[t] for t in self.watch if signal(t) or (self.entry == 'wait' and cards[t]['hunbot'].get('super_bull'))]
            newly = [s['code'] for s in go]
        waiting = {o['t'] for o in orders if o['side'] == 'buy'} | set(self.pending)
        free = self.slots - (len(self.pos) - len(sells)) - len(waiting - set(self.pos))
        new = sorted((cards[t] for t in newly if t not in self.pos and t not in waiting
                      and cards[t]['metrics']['expected_loss'] is not None),
                     key=lambda s: (abs(s['metrics']['expected_loss']), -s['metrics']['target_1']))
        if self.sector_rank:
            cnt = Counter(self.sector.get(t, '미분류') for t, c in cards.items() if c.get('state') == 'BUY')
            new.sort(key=lambda s: -cnt[self.sector.get(s['code'], '미분류')])   # 안정 정렬: 같은 수면 기존 순서
        new = [s for s in new if self._tgt(s) is not None
               and not (self.no_gen and not s.get('fin') and not cr.loss_turnaround(s))
               and not (self.earn and self.earn_block(s['code'], k + 1))
               and not (self.max_age is not None and k - s.get('filed_k', k) > self.max_age)
               and self.sector.get(s['code']) not in self.ex_sec]
        for s in new[:max(free, 0)]:
            added, m, c = signal(s['code']), self._tgt(s), s.get('close')
            ta = not s.get('fin') and cr.loss_turnaround(s)
            if self.only_ta and not ta:
                continue
            if ta and self.ta_mode == 'normal':
                r_, t1_, t2_ = cr.calc_card_metrics(c, s.get('ma120'), s.get('ma200'), s.get('per'), s.get('per_med5'),
                                                    s.get('pbr'), s.get('fin'), s.get('mcap_b'), turnaround=False)
                if r_ is not None:
                    m = {**m, 'expected_loss': r_, 'target_1': t1_, 'target_2': t2_}
            if (self.regime and not self.mkt_ok) or (self.entry == 'sigonly' and not added):
                continue
            if self.entry == 'open' and self.card_px:
                orders.append({'side': 'buy', 't': s['code'], 'value': eq * (self.first + (self.add if added else 0)),
                               'added': added, 'metrics': m,
                               'levels': {'stop': c * (1 + m['expected_loss'] / 100), 'tp1': c * (1 + m['target_1'] / 100),
                                          'tp2': c * (1 + m['target_2'] / 100), 'trail': m['expected_loss']}})
            elif self.entry in ('open', 'wait', 'wait_sig', 'sigonly'):
                orders.append({'side': 'buy', 't': s['code'], 'value': eq * (self.first + (self.add if added else 0)),
                               'added': added, 'metrics': m, 'tag': {
                                   'path': '금융' if s.get('fin') else '턴어라운드' if cr.loss_turnaround(s) else '일반',
                                   'sector': self.sector.get(s['code'], '미분류'), 'age': k - s.get('filed_k', k),
                                   'record': '+'.join(x for x, a, b2 in (('매출', 'rev_q', 'rev_ttm'), ('영익', 'op_q', 'op_ttm'))
                                                      if s.get(a) and s.get(b2))},
                               'nostop': bool(ta and self.ta_mode == 'card'),
                               **({'levels': self.card0[s['code']], 'below': self.card_lv} if s['code'] in self.card0 else {})})
            elif self.entry == 'limitA' and added:
                orders.append({'side': 'buy', 't': s['code'], 'value': eq * (self.first + self.add), 'added': True,
                               'metrics': m, 'card_close': c, 'label': '▲시가매수'})
            else:   # 카드 손절가까지 cap% 이내가 되는 가격에 지정가
                stop = c * (1 + m['expected_loss'] / 100)
                self.pending[s['code']] = {
                    't': s['code'], 'limit': stop / (1 - self.cap / 100), 'added': added, 'metrics': m,
                    'card_close': c, 'label': '지정가매수',
                    'levels': {'stop': stop, 'tp1': c * (1 + m['target_1'] / 100), 'tp2': c * (1 + m['target_2'] / 100),
                               'trail': max(m['expected_loss'], -self.cap)}}
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
    s['filed_k'] = snap[0]   # 이 재무가 반영된(공시 다음) 거래일
    s.update(code=co['t'], name=co['t'], market='', fin=co['fin'], close=c, ma20=g('ma20'), ma120=ma120,
             ma200=ma200, vol20=v20, vol60=v60, change_pct=g('chg'), price_date=day_str(CAL[k]),
             hunbot={'super_bull': tri('sb'), 'panic': tri('pn'), 'sc': tri('sc'),
                     'lc_age': None if A['age'][k] < 0 else int(A['age'][k])},
             share_warn=warn, marcap=None if marcap is None else round(marcap), per=r(per), pbr=r(pbr, 2),
             per_med5=per_med, mcap_b=marcap / 1e9 if marcap else None,
             above120=ma120 is not None and c >= ma120, above200=ma200 is not None and c >= ma200,
             vol_down=v20 is not None and v60 is not None and v20 < v60,
             dd52=r((c / hi - 1) * 100) if hi else None, record=F['rec_base'],
             wk=OBV_NAMES[int(A['obv'][k])],
             tt=bool(A['tt'][k] == 1 and A['rs'][k] >= .7))   # 추세 템플릿 + 상대강도 상위 30%
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
    series = {name: (b.nav if b.seed else b.curve) for name, b in books.items()}
    series['SPY 보유'] = list(spy)
    for name, curve in series.items():
        st = stats(curve, days)
        years[name] = yearly(curve, days)
        b = books.get(name)
        tr = pd.DataFrame(b.closed) if b and b.closed else None
        extra = ({'거래': len(tr), '승률': f"{(tr['ret'] > 0).mean() * 100:.0f}%", '평균 손익': pct(tr['ret'].mean()),
                  '평균 보유(거래일)': f"{(tr['exit_k'] - tr['entry_k']).mean():.0f}",
                  '평균 투자 비중': f"{np.mean(b.invested) * 100:.0f}%"} if tr is not None else {})
        cut = int(np.searchsorted(days, day_int('2020-01-01')))   # 과최적화 점검: 앞뒤 구간 따로
        a_, b_ = stats(curve[:cut + 1], days[:cut + 1]), stats(curve[cut:], days[cut:])
        per = {'13~19 CAGR': pct(a_['CAGR']), '13~19 MDD': pct(a_['MDD']), '13~19 샤프': f"{a_['샤프']:.2f}",
               '20~26 CAGR': pct(b_['CAGR']), '20~26 MDD': pct(b_['MDD']), '20~26 샤프': f"{b_['샤프']:.2f}"}
        if b and b.tax:
            extra['낸 세금(시작자산=1)' if not b.seed else '낸 세금(만원)'] = f'{b.tax_paid:,.0f}' if b.seed else f'{b.tax_paid:.2f}'
        if b and b.seed:   # 적립식: 넣은 돈, 최종 평가액, 돈 가중 연수익률(IRR)
            fl = b.flows + [(len(days) - 1, b.curve[-1])]
            yr = lambda k: (days[k] - days[0]) / 365.25
            npv = lambda r: sum(v / (1 + r) ** yr(k) for k, v in fl)
            lo_, hi_ = -.99, 5.0
            for _ in range(200):
                mid = (lo_ + hi_) / 2
                lo_, hi_ = (mid, hi_) if npv(mid) > 0 else (lo_, mid)
            extra.update({'넣은 돈(만원)': f'{b.paid_in:,.0f}', '최종 평가(만원)': f'{b.curve[-1]:,.0f}',
                          '돈 가중 연수익률(IRR)': pct(mid)})
        row = {'구분': name, '총수익률': pct(st['총수익률']), 'CAGR': pct(st['CAGR']), 'MDD': pct(st['MDD']),
               '샤프': f"{st['샤프']:.2f}", **per, **extra}
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
            if QQQ_OC is not None:   # 같은 날 시가에 사고 그 거래 마지막 날 종가에 판 QQQ (대조군)
                tr['qqq'] = [QQQ_OC[1][b] / QQQ_OC[0][a] - 1 for a, b in zip(tr['entry_k'], tr['exit_k'])]
            cols = [c for c in ('path', 'sector', 'age', 'record', 'qqq') if c in tr]
            tr[['strategy', 'ticker', 'entry', 'exit_date', 'ret', 'exit', 'events'] + cols].to_csv(
                f'docs/backtest_trades_{i}.csv', index=False)
    # 자산 곡선 (로그 축)
    W, H, P = 760, 300, 36
    allv = np.log([x for c in series.values() for x in c])
    lo, hi = allv.min(), allv.max()
    colors = ['#5fe0c2', '#f2c14e', '#e8796b', '#8fb8ff', '#c792ea', '#ff9e64', '#9ece6a', '#f7768e', '#7dcfff', '#8aa0a6']
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
    # 하락 구간별 수익률·구간 내 최대 낙폭
    WIN = (('2015-07-20', '2016-02-11', '2015~16 중국·유가'), ('2018-10-01', '2018-12-24', '2018 4분기'),
           ('2020-02-19', '2020-03-23', '2020 코로나'), ('2022-01-03', '2022-12-30', '2022 하락장'),
           ('2025-02-19', '2025-04-08', '2025 관세 급락'))
    wrows = []
    for a_, b_, lab in WIN:
        ia, ib = (int(np.searchsorted(days, day_int(x))) for x in (a_, b_))
        r_ = {'구간': f'{lab} ({a_}~{b_})'}
        for n_, cv in series.items():
            seg = np.array(cv[ia:ib + 1])
            r_[n_] = f'{pct(seg[-1] / seg[0] - 1)} (MDD {pct((seg / np.maximum.accumulate(seg) - 1).min())})'
        wrows.append(r_)
        lines.append(' · '.join(f'{k} {v}' for k, v in r_.items()))
    wkeys = list(wrows[0])
    wtable = '<tr>' + ''.join(f'<th>{k}</th>' for k in wkeys) + '</tr>' + ''.join(
        '<tr>' + ''.join(f'<td>{r[k]}</td>' for k in wkeys) + '</tr>' for r in wrows)
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
<h2>하락 구간</h2><div class="box"><table>{wtable}</table></div>
<h2>연도별 수익률</h2><div class="box"><table>{ytable}</table></div>
<h2>규칙과 한계</h2><ul>
<li>우수전략: 최대 5종목, 이평선 조건 사용, 공시지연 미사용. 매수 카드에 새로 오르면 다음 날 시가에 20%, 매수 카드 상태에서 훈봇 ▲가 뜨면 10% 추가(같은 날이면 30%). 같은 날 여러 종목이면 예상 손실률이 작은 순, 1차 목표 수익률이 높은 순.</li>
<li>손절: 매수가에 예상 손실률을 적용한 고정 가격이 항상 걸려 있음. 1차 목표가에 물량 50%, 2차 목표가에 25%를 트레일링 스탑(최고가 대비 예상 손실률)으로 매도, 나머지는 매도 카드로 매도.</li>
<li>우수전략: 최대 5종목, 매수 카드에 오르면 20%, ▲면 10% 추가. 1차 목표가에 50%, 2차 목표가에 25% 트레일링 스탑, 나머지는 매도 카드. 강세(훈봇 초강세) 구간에도 트레일링 스탑을 쉬지 않음. 남는 돈은 TQQQ, QQQ가 200일선 아래면 현금.</li>
<li>우수전략: 최대 5종목, 매수 카드에 오르면 다음 날 시가 20%, ▲면 10% 추가. 손절선 항상, 1차 목표가 50%·2차 25% 트레일링 스탑(폭=손절률), 나머지는 매도 카드. 남는 돈 지수는 전날 종가 판정으로 다음 날 시가에 매매, 200일선은 QQQ(나스닥100) 기준.</li>
<li>적립식 비교(만원 단위): 50만원으로 시작해 매월 25일(휴일이면 다음 거래일) 50만원 입금. 양도세 22%, 매년 실현이익에서 250만원 공제 후 다음 해 첫 거래일 납부. CAGR·MDD·샤프는 입금 효과를 뺀 시간가중 수익률(좌수 기준), IRR은 입금 시점까지 반영한 돈 가중 수익률. 적립은 입금 다음 날 시가에 전액 매수 후 계속 보유, 200일선 매매는 TQQQ는 QQQ 200일선, SPY·QQQ는 자기 200일선 기준. 지금 전략의 매매 규칙은 그대로(종목 20%+▲10%, TQQQ 최대 50%). 지수 ETF 배당은 15% 원천징수 후 재투자.</li>

<li>남는 돈 SPY·QQQ·TQQQ: 남는 현금은 그날 종가에 지수 ETF를 사 두고, 매수 자금이 모자라면 다음 날 시가에 필요한 만큼 팖(편도 비용 동일). 200일선 위만: QQQ 종가가 200일선 아래면 그날 종가에 지수를 팔고 현금 보유, 다시 위로 오르면 종가에 매수. TQQQ도 QQQ 200일선 기준.</li>
<li>매수 카드 신호는 다음 날 시가, 목표가·손절은 장중 고가·저가에 닿으면 그 가격(갭이면 시가)에 체결. 같은 날 손절과 목표가가 겹치면 손절로 처리.</li>
<li>재무는 공시 다음 거래일부터 반영. 현재 상장 종목만 대상이라 상장폐지 종목이 빠진 생존 편향이 있어 결과가 실제보다 좋게 나올 수 있음.</li>
</ul></div></body></html>'''
    open('docs/backtest.html', 'w', encoding='utf-8').write(html)
    return lines


SEL_VARIANTS = {'base': '전체 기준', 'all': '실적 신고가만', 'fund': '실적 신고가 + 기술적 조건(가치·재무 제외)',
                'per': 'PER 조건 제외', 'ma': '이평선 조건 제외', 'vol': '변동성 조건 제외', 'obv': 'OBV 조건 제외'}


def sel_elig(s, drop=None):
    """buy_eligible과 같은 판정에서 조건 하나를 뺀 것 (drop=None이면 원래 판정과 같음)"""
    pos_ = cr.positive
    drop = None if drop == 'base' else drop
    c, a, b = (s.get(x) for x in ('close', 'ma120', 'ma200'))
    if not all(pos_(x) for x in (c, a, b)) or not s.get('record'):
        return False
    if drop == 'all':
        return True
    if drop != 'ma' and not (c >= a or c >= b):
        return False
    if drop != 'vol' and not s.get('vol_down'):
        return False
    if drop != 'obv' and s.get('obv_signal') == '이탈':
        return False
    if drop == 'fund':
        return True
    if s.get('fin'):
        return (pos_(s.get('pbr')) and s['pbr'] <= 1 and cr.valid(s.get('roe3')) and s['roe3'] >= 10
                and pos_(s.get('op_ttm_value')) and cr.profit_holding(s))
    if cr.loss_turnaround(s):
        return cr.op_improving(s) and cr.fscore_ok(s)
    return cr.profit_holding(s) and (cr.valid(s.get('roe3')) and s['roe3'] >= 15 and cr.valid(s.get('debt'))
            and s['debt'] <= 50 and s.get('profit_years', 0) >= 5
            and (drop == 'per' or (pos_(s.get('per')) and pos_(s.get('per_med5')) and s['per'] < s['per_med5'])))


def selection_report(events, cos, horizons=(20, 60, 120)):
    """새로 충족한 다음 날 시가에 사서 손절 없이 h거래일 보유했을 때 수익률을 QQQ·전체 종목 평균과 비교"""
    T = len(CAL)
    O = np.array([co['a']['o'] for co in cos], float)
    C = np.array([co['a']['c'] for co in cos], float)
    qo, qc = QQQ_OC
    uni = {}
    rows = []
    for v, ci, k, path in events:
        for h in horizons:
            e, x = k + 1, k + h
            if x >= T or not (np.isfinite(O[ci, e]) and np.isfinite(C[ci, x]) and O[ci, e] > 0):
                continue
            if (e, x) not in uni:
                with np.errstate(all='ignore'):
                    r = C[:, x] / O[:, e] - 1
                uni[(e, x)] = float(np.nanmean(r[np.isfinite(r)]))
            rows.append((v, path, h, C[ci, x] / O[ci, e] - 1, qc[x] / qo[e] - 1, uni[(e, x)], day_str(CAL[k])[:4]))
    df = pd.DataFrame(rows, columns=['v', 'path', 'h', 'ret', 'qqq', 'uni', 'year'])
    out = ['손절 없이 h거래일 보유 (다음 날 시가 매수 -> h거래일째 종가). 평균은 %, 이김은 QQQ보다 높은 비율',
           '구분 | 보유일 | 건수 | 평균 | 중앙값 | 상승 | QQQ | 전체종목 | QQQ대비 | 전체대비 | QQQ 이김']
    def line(name, g, h):
        g = g[g.h == h]
        if len(g):
            out.append(f"{name} | {h} | {len(g)} | {g.ret.mean()*100:+.1f} | {g.ret.median()*100:+.1f} | {(g.ret>0).mean()*100:.0f}% | "
                       f"{g.qqq.mean()*100:+.1f} | {g.uni.mean()*100:+.1f} | {(g.ret-g.qqq).mean()*100:+.1f} | "
                       f"{(g.ret-g.uni).mean()*100:+.1f} | {(g.ret>g.qqq).mean()*100:.0f}%")
    for v, name in SEL_VARIANTS.items():
        for h in horizons:
            line(name, df[df.v == v], h)
    for p in ('일반', '금융', '턴어라운드'):
        for h in horizons:
            line('전체 기준 · ' + p, df[(df.v == 'base') & (df.path == p)], h)
    full = df[(df.v == 'base') & (df.h == 60)]
    for y, g in full.groupby('year'):
        line(f'전체 기준 · {y}년', g, 60)
    open('docs/backtest_selection.txt', 'w', encoding='utf-8').write('\n'.join(out) + '\n')
    print('\n'.join(out), flush=True)


def load_sectors(tickers):
    """야후 섹터(현재 분류를 과거에도 씀). 일일 캐시 + 백테스트 캐시, 없으면 조회해 data/bt/sector.json에 저장"""
    import time
    import yfinance as yf
    p = f'{BT}/sector.json'
    cache = {}
    for f in ('data/cache/sector.json', p):
        if os.path.exists(f):
            cache.update(json.load(open(f, encoding='utf-8')))
    todo = [t for t in tickers if t not in cache]
    print('섹터 조회', len(todo), '종목', flush=True)
    for i, t in enumerate(todo):
        for wait in (0, 30, 90):
            time.sleep(wait)
            try:
                sec = yf.Ticker(t).info.get('sector')
            except Exception:
                sec = None
            if sec:
                cache[t] = sec
                break
        else:
            cache[t] = None   # 조회 실패도 기록해 다음 실행에서 다시 묻지 않음
        time.sleep(.4)
        if i % 100 == 99:
            json.dump(cache, open(p, 'w', encoding='utf-8'))
    json.dump(cache, open(p, 'w', encoding='utf-8'))
    got = {t: cache[t] for t in tickers if cache.get(t)}
    print('섹터 확보', len(got), '/', len(tickers), flush=True)
    return got


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
    # 상대강도: 그날 1년(252거래일) 수익률의 종목 간 백분위(0~1). 대상은 백테스트 종목 전체
    C_ = np.vstack([co['a']['c'] for co in cos]).astype(float)
    R_ = np.full_like(C_, np.nan)
    R_[:, 252:] = C_[:, 252:] / C_[:, :-252] - 1
    RS_ = pd.DataFrame(R_).rank(axis=0, pct=True).to_numpy(np.float32)
    for co, row in zip(cos, RS_):
        co['a']['rs'] = np.nan_to_num(row, nan=0.0)
    del C_, R_, RS_
    sectors = load_sectors([co['t'] for co in cos])

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
    histories = {ma: {} for ma in {v[1] for v in STRATEGIES.values()} | {True}}
    sel_events, sel_prev, sel_last = [], set(), {}
    books = {key: Book(**{'exits': 'ts12', **kw}) for key, (_, _, kw) in STRATEGIES.items()}
    for b in books.values():
        b.sector = sectors
    def idx_px(name):   # 거래일 달력에 맞춘 (시가, 종가), 빈 날은 직전 값
        x = np.load(f'{BT}/px/{name}.npz')
        pos = np.clip(np.searchsorted(x['d'], CAL, 'right') - 1, 0, None)
        o, c = x['o'].astype(float), x['c'].astype(float)
        if 'dv' in x.files:   # 배당 재투자(미국 원천징수 15% 뺀 85%), 배당락일 시가부터 반영
            F = np.cumprod(1 + .85 * x['dv'].astype(float) / c)
            o, c = o * F, c * F
        o, c = o[pos], c[pos]
        return np.where(np.isfinite(o) & (x['d'][pos] == CAL), o, c), c
    names = {kw.get('idx', 'SPY') for _, _, kw in STRATEGIES.values()} | {'QQQ'}
    SYN = {'NDX3', 'NDX1', 'SPX1', 'NDX3F', 'MIXF'}
    px_idx = {n: idx_px(n) for n in names - SYN - {'MIX', 'MIXS'}}
    syn_ok = {}
    for n in names & SYN:   # 최악 10년 구간 시작일부터의 지수 경로를 2013년부터의 거래일에 그대로 덮어씌움
        import stress_tqqq
        O3, C3, ok3, s3 = stress_tqqq.path(n)
        assert len(C3) >= len(CAL), '합성 경로가 백테스트 기간보다 짧음'
        px_idx[n], syn_ok[n] = (O3[:len(CAL)], C3[:len(CAL)]), ok3[:len(CAL)]
        print(n, '덮어씌운 구간 시작', s3, flush=True)
    global QQQ_OC
    QQQ_OC = idx_px('QQQ')
    qc = np.load(f'{BT}/px/QQQ.npz')
    qma = np.convolve(qc['c'].astype(float), np.ones(200) / 200, 'valid')
    qok = dict(zip(qc['d'][199:].tolist(), (qc['c'][199:] >= qma).tolist()))
    q_ok = np.array([qok.get(int(d), True) for d in CAL])
    qc_c, st_, qbuf = qc['c'].astype(float)[199:], True, {}   # ±1.5% 버퍼: 위 1.5% 넘으면 켜고 아래 1.5% 밑이면 끔
    for d_, c_, m_ in zip(qc['d'][199:].tolist(), qc_c, qma):
        st_ = c_ >= m_ * 1.015 if not st_ else c_ >= m_ * .985
        qbuf[d_] = st_
    q_buf = np.array([qbuf.get(int(d), True) for d in CAL])

    def own_ok(name):   # 그 ETF 자체 종가가 자기 200일선 이상인 날
        x = np.load(f'{BT}/px/{name}.npz')
        m = np.convolve(x['c'].astype(float), np.ones(200) / 200, 'valid')
        ok_ = dict(zip(x['d'][199:].tolist(), (x['c'][199:] >= m).tolist()))
        return np.array([ok_.get(int(d), True) for d in CAL])
    if 'MIX' in names:   # 실제 QQQ 반 + TQQQ 반(QQQ 200일선 아래면 그 반은 현금)
        import stress_tqqq
        (Oq, Cq), (Ot, Ct) = idx_px('QQQ'), idx_px('TQQQ')
        px_idx['MIX'] = stress_tqqq.mix(Oq, Cq, Ot, Ct, q_ok)
    if 'MIXS' in names:   # 실제 SPY 반 + TQQQ 반
        import stress_tqqq
        (Os_, Cs_), (Ot, Ct) = idx_px('SPY'), idx_px('TQQQ')
        px_idx['MIXS'] = stress_tqqq.mix(Os_, Cs_, Ot, Ct, q_ok)
    week = (CAL.astype(np.int64) + 3) // 7   # 월~일 한 주
    week_end = np.r_[week[1:] != week[:-1], True]
    wk_cache = {}

    def wk_break(t, k, n):
        """주 마지막 거래일에 종가가 N주 이동평균 아래면 True, 위면 False, 그 밖(주 중간·이평 없음)은 None"""
        if not week_end[k]:
            return None
        ci = index[t]
        if (ci, n) not in wk_cache:
            c_ = cos[ci]['a']['c'].astype(float)
            idx_ = np.where(week_end & np.isfinite(c_))[0]
            wc = c_[idx_]
            ma_ = np.full(len(wc), np.nan)
            if len(wc) >= n:
                ma_[n - 1:] = np.convolve(wc, np.ones(n) / n, 'valid')
            brk = np.full(len(CAL), np.nan)
            brk[idx_] = np.where(np.isfinite(ma_), wc < ma_, np.nan)
            wk_cache[(ci, n)] = brk
        v = wk_cache[(ci, n)][k]
        return None if np.isnan(v) else bool(v)
    earn_days = {}   # 종목별 실적 발표일(8-K Item 2.02 제출일, 거래일 달력 위치)
    if os.path.exists(f'{BT}/earn.json'):
        ej, t2c = json.load(open(f'{BT}/earn.json', encoding='utf-8')), {v['ticker']: c for c, v in listing.items()}
        for co in cos:
            ds = ej.get(t2c.get(co['t'], ''), [])
            if ds:
                earn_days[co['t']] = np.searchsorted(CAL, sorted(day_int(x) for x in ds))
    print('실적발표일 있는 종목', len(earn_days), flush=True)

    def earn_block(t, k):
        """k일 시가 매수가 실적발표 전 5거래일~후 2거래일에 걸리면 True.
        다음 발표일은 직전 발표일 + 91일로 추정하고, 늦어지면 실제 발표 전까지(최대 30일) 막는다."""
        E = earn_days.get(t)
        if E is None:
            return False
        k = min(k, len(CAL) - 1)
        j = int(np.searchsorted(E, k, 'right')) - 1
        if j < 0:
            return False
        if k - E[j] <= 2:
            return True
        nxt = int(np.searchsorted(CAL, CAL[min(E[j], len(CAL) - 1)] + 91))
        return k >= nxt - 5 and CAL[k] <= CAL[min(nxt, len(CAL) - 1)] + 30

    for b in books.values():
        b.earn_block = earn_block
        o_, c_ = px_idx[b.idx]
        b.spy_bar = lambda k, o_=o_, c_=c_: (float(o_[k]), float(c_[k]))
        okk = own_ok(b.idx) if b.own_ma else syn_ok.get(b.idx, q_buf if b.idx_buf else q_ok)
        b.idx_ok = lambda k, okk=okk: bool(okk[k])
        b.wk_break = wk_break
        b.ma_ok = lambda t, k: (lambda A: bool(np.isfinite(A['c'][k]) and (A['c'][k] >= A['ma120'][k] or A['c'][k] >= A['ma200'][k])))(cos[index[t]]['a'])
    zc = z['c'].astype(float)
    ma200 = np.convolve(zc, np.ones(200) / 200, 'valid')
    spy_ok = np.concatenate([np.ones(199, bool), zc[199:] >= ma200])[i0:]
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
        for ma, history in histories.items():   # 판정 조건(이평선·변동성/OBV)이 다르면 카드 상태가 달라지므로 따로 추적
            cr.USE_MA, cr.USE_VO, cr.USE_TT, cr.USE_VAL = ma is not False, ma != 'novo', ma in ('tt', 'ttnv'), ma != 'ttnv'
            stocks = [dict(s) for s in base]
            cr.update_history(stocks, history, 'us', f'{day} 08:00')
            cards, newly = {}, []
            for s in stocks:
                if s.get('state') and not s.get('data_missing'):
                    cards[s['code']] = s
                    last = (s.get('history') or {}).get('transitions') or [{}]
                    if s['state'] == 'BUY' and last[-1].get('to') == 'BUY' and last[-1].get('date') == day:
                        newly.append(s['code'])
            for key, b in books.items():
                if STRATEGIES[key][1] == ma:
                    b.mkt_ok = bool(spy_ok[k])
                    b.evening(k, bar, cards, newly)
            if ma is True:   # 종목선정 진단: 조건별로 '새로 충족'된 날을 모은다 (같은 종목은 20거래일 안에 다시 세지 않음)
                now = set()
                for st in stocks:
                    ci = index.get(st['code'])
                    if ci is None:
                        continue
                    for v in SEL_VARIANTS:
                        if sel_elig(st, v):
                            now.add((v, ci))
                            if (v, ci) not in sel_prev and k - sel_last.get((v, ci), -99) >= 20:
                                sel_last[(v, ci)] = k
                                path = '금융' if st.get('fin') else '턴어라운드' if cr.loss_turnaround(st) else '일반'
                                sel_events.append((v, ci, k, path))
                sel_prev = now
        if k % 250 == 0:
            print(day, '보유', [len(b.pos) for b in books.values()], flush=True)
    cr.USE_MA, cr.USE_VO, cr.USE_TT, cr.USE_VAL = True, True, False, True
    selection_report(sel_events, cos)
    for line in report({STRATEGIES[key][0]: b for key, b in books.items()}, spy, CAL):
        print('::warning title=backtest::' + line)


if __name__ == '__main__':
    main()
