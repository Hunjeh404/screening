"""백테스트: 가격 지표가 앱 계산(lib.price_metrics)과 같은지, 매매 규칙이 정한 대로 동작하는지."""
import datetime as dt
from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import backtest as bt
from lib import price_metrics


class FeatureTest(unittest.TestCase):
    def test_same_as_live_price_metrics(self):
        rng = np.random.default_rng(1)
        n = 420
        days = [dt.date(2024, 1, 1) + dt.timedelta(days=i) for i in range(n * 2) if (dt.date(2024, 1, 1) + dt.timedelta(days=i)).weekday() < 5][:n]
        d = np.array([(x - bt.EPOCH).days for x in days], np.int32)
        c = 100 * np.cumprod(1 + rng.normal(0, .02, n))
        h, l = c * 1.01, c * .99
        v = rng.integers(1000, 5000, n).astype(float)
        f = bt.price_features(d, h, l, c, v)
        for k in (59, 150, 300, n - 1):
            with self.subTest(k=k):
                pm = price_metrics(days[:k + 1], c[:k + 1].tolist(), v[:k + 1].tolist(), h[:k + 1].tolist(), l[:k + 1].tolist())
                for key, mine in (('ma20', 'ma20'), ('ma120', 'ma120'), ('ma200', 'ma200'), ('vol20', 'vol20'),
                                  ('vol60', 'vol60'), ('high52', 'high52'), ('change_pct', 'chg')):
                    if pm[key] is None:
                        self.assertTrue(np.isnan(f[mine][k]), key)
                    else:
                        self.assertAlmostEqual(pm[key], f[mine][k], places=9, msg=key)
                self.assertEqual(bt.OBV_NAMES[f['obv'][k]], bt.ORIG_OBV(pm['wk']))
                self.assertEqual(f['age'][k], -1 if pm['hunbot']['lc_age'] is None else pm['hunbot']['lc_age'])
                self.assertEqual(f['sb'][k], -1 if pm['hunbot']['super_bull'] is None else int(pm['hunbot']['super_bull']))
        self.assertEqual(bt.quarter_closes(d, c, 100)['2024Q2'], c[100])


def card(t, state='BUY', loss=-5, t1=10, t2=20, age=None, bull=False, sc=False, close=None):
    return {'code': t, 'state': state, 'close': close, 'metrics': {'expected_loss': loss, 'target_1': t1, 'target_2': t2},
            'hunbot': {'lc_age': age, 'super_bull': bull, 'sc': sc}}


class BookTest(unittest.TestCase):
    def run_days(self, book, days):
        """days: [(bars {t:(o,h,l,c)}, cards, newly)]"""
        for k, (bars, cards, newly) in enumerate(days):
            bar = lambda t: bars.get(t)
            book.morning(k, bar)
            book.evening(k, bar, cards, newly)

    def scenario(self):
        A = lambda o, h, l, c: {'A': (o, h, l, c)}
        return [
            ({}, {'A': card('A')}, ['A']),                                    # 매수 카드 등록 -> 다음 날 20%
            (A(100, 101, 99, 100), {'A': card('A', age=0)}, []),               # 20% 매수, ▲ -> 다음 날 10% 추가
            (A(100, 111, 100, 110), {'A': card('A', bull=True)}, []),          # 추가 매수, 1차 목표(110) 도달, 초강세
            (A(110, 125, 96, 120), {'A': card('A', state='HOLD')}, []),        # 초강세: 목표가 매도 쉼, 2차 목표(120) 도달
            (A(120, 121, 118, 119), {'A': card('A', state='HOLD')}, []),
            (A(100, 100, 94, 95), {'A': card('A', state='HOLD')}, []),         # 손절선 95(매수가 100 기준)
        ]

    def test_target_trailing_stops_and_fixed_stop(self):
        b = bt.Book(5, ts=True, cost=0)
        self.run_days(b, self.scenario())
        self.assertEqual(b.pos, {})
        self.assertEqual(b.closed[0]['events'], '매수>추가매수>1차목표>2차목표>1차 TS매도>2차 TS매도>손절')
        sh = .2 / 100 + .1 / 100
        # 최고가 125 기준 -5% = 118.75에서 50%·25%가 팔리고, 남은 25%는 손절선 95
        self.assertAlmostEqual(b.cash, 1 - .3 + sh * .5 * 118.75 + sh * .25 * 118.75 + sh * .25 * 95)

    def test_target_immediate_sale(self):
        b = bt.Book(5, ts=False, cost=0)
        self.run_days(b, self.scenario())
        self.assertEqual(b.closed[0]['events'], '매수>추가매수>1차목표>2차목표>손절')
        sh = .2 / 100 + .1 / 100
        self.assertAlmostEqual(b.cash, 1 - .3 + sh * .5 * 110 + sh * .25 * 120 + sh * .25 * 95)

    def test_hold_until_sell_card(self):
        b = bt.Book(5, cost=0, exits='none')
        days = self.scenario()[:5] + [({'A': (119, 119, 117, 118)}, {'A': card('A', state='SELL')}, []),
                                      ({'A': (116, 117, 115, 116)}, {}, [])]
        self.run_days(b, days)
        self.assertEqual(b.closed[0]['events'], '매수>추가매수>매도카드()')
        self.assertAlmostEqual(b.cash, 1 - .3 + (.2 / 100 + .1 / 100) * 116)

    def test_second_target_half_trailing(self):
        b = bt.Book(5, cost=0, exits='ts2')
        self.run_days(b, self.scenario())
        # 1차 목표(110)에서는 아무것도 안 하고, 2차 목표(120) 도달 시 50%에 TS: 최고가 125 -> 118.75
        self.assertEqual(b.closed[0]['events'], '매수>추가매수>2차목표>2차 TS매도>손절')
        sh = .2 / 100 + .1 / 100
        self.assertAlmostEqual(b.cash, 1 - .3 + sh * .5 * 118.75 + sh * .5 * 95)

    def test_stop_works_even_in_super_bull(self):
        b = bt.Book(5, cost=0)
        self.run_days(b, [({}, {'A': card('A', bull=True)}, ['A']),
                          ({'A': (100, 100, 100, 100)}, {'A': card('A', bull=True)}, []),
                          ({'A': (99, 99, 90, 92)}, {'A': card('A', bull=True)}, [])])
        self.assertEqual(b.closed[0]['exit'], '손절')
        self.assertAlmostEqual(b.cash, 1 - .2 + .2 / 100 * 95)

    def test_sell_card_market_exit_and_gap(self):
        b = bt.Book(5, cost=0)
        self.run_days(b, [
            ({}, {'A': card('A', age=1)}, ['A']),                              # ▲와 함께 등록 -> 30%
            ({'A': (100, 100, 100, 100)}, {'A': card('A', state='SELL')}, []),
            ({'A': (96, 97, 96, 96)}, {}, []),
        ])
        self.assertEqual(b.closed[0]['exit'], '매도카드()')
        self.assertAlmostEqual(b.cash, 1 - .3 + .3 / 100 * 96)
        b = bt.Book(5, cost=0)
        self.run_days(b, [({}, {'A': card('A')}, ['A']),
                          ({'A': (100, 100, 100, 100)}, {'A': card('A')}, []),
                          ({'A': (90, 95, 88, 92)}, {'A': card('A')}, [])])   # 시가가 손절가(95) 아래 -> 시가 체결
        self.assertEqual(b.closed[0]['exit'], '손절')
        self.assertAlmostEqual(b.cash, 1 - .2 + .2 / 100 * 90)

    def test_priority_slots_and_cost(self):
        b = bt.Book(1, cost=.001)
        cards = {'A': card('A', loss=-7, t1=30), 'B': card('B', loss=-3, t1=6), 'C': card('C', loss=-3, t1=9)}
        self.run_days(b, [({}, cards, ['A', 'B', 'C']),
                          ({t: (10, 10, 10, 10) for t in 'ABC'}, cards, [])])
        self.assertEqual(list(b.pos), ['C'])                                 # 손실률 작은 순, 같으면 1차 목표 큰 순
        self.assertAlmostEqual(b.pos['C']['spent'], .2)
        self.assertAlmostEqual(b.pos['C']['sh'], .2 / (10 * 1.001))
        self.assertEqual(b.orders, [])                                      # 꽉 차서 더 사지 않음

    def test_limit_entry(self):
        # 카드 종가 100, 손절 -8% -> 손절가 92, 지정가 92/0.95=96.84. 목표가는 카드 종가 기준 110/120
        L = 92 / .95
        C = lambda **kw: {'A': card('A', loss=-8, close=100, **kw)}
        for entry in ('limitA', 'limitB'):
            b = bt.Book(5, cost=0, entry=entry)
            self.run_days(b, [({}, C(), ['A']),
                              ({'A': (101, 103, 99, 102)}, C(), []),        # 지정가 미도달 -> 대기
                              ({'A': (99, 100, 95, 97)}, C(), [])])         # 장중 도달 -> 지정가 체결 20%
            p = b.pos['A']
            self.assertAlmostEqual(p['entry'], L)
            self.assertAlmostEqual(p['spent'], .2)
            self.assertAlmostEqual(p['stop'], 92)
            self.assertAlmostEqual(p['tp1'], 110)
            self.assertAlmostEqual(p['trail'], -5)
        b = bt.Book(5, cost=0, entry='limitB')                              # 갭 하락이면 시가 체결
        self.run_days(b, [({}, C(), ['A']), ({'A': (95, 96, 94, 95)}, C(), [])])
        self.assertAlmostEqual(b.pos['A']['entry'], 95)
        b = bt.Book(5, cost=0, entry='limitB')                              # 매수 카드에서 빠지면 취소
        self.run_days(b, [({}, C(), ['A']), ({'A': (101, 103, 99, 102)}, {'A': card('A', state='HOLD')}, []),
                          ({'A': (95, 96, 90, 95)}, {}, [])])
        self.assertEqual((b.pos, b.pending), ({}, {}))

    def test_limit_with_signal(self):
        C = lambda **kw: {'A': card('A', loss=-8, close=100, **kw)}
        b = bt.Book(5, cost=0, entry='limitA')                              # A: ▲면 다음 날 시가 30%, 손절 -5%
        self.run_days(b, [({}, C(), ['A']), ({'A': (101, 103, 99, 102)}, C(age=0), []),
                          ({'A': (102, 104, 101, 103)}, C(), [])])
        p = b.pos['A']
        self.assertEqual((p['entry'], p['events'][0]), (102, '▲시가매수'))
        self.assertAlmostEqual(p['spent'], .3)
        self.assertAlmostEqual(p['stop'], 102 * .95)
        self.assertAlmostEqual(p['tp1'], 110)
        b = bt.Book(5, cost=0, entry='limitB')                              # B: ▲여도 지정가까지 기다려 30%
        self.run_days(b, [({}, C(), ['A']), ({'A': (101, 103, 99, 102)}, C(age=0), []),
                          ({'A': (102, 104, 101, 103)}, C(age=1), []), ({'A': (99, 100, 96, 97)}, C(), [])])
        self.assertAlmostEqual(b.pos['A']['spent'], .3)
        self.assertAlmostEqual(b.pos['A']['entry'], 92 / .95)

    def test_wait_entry_regime_breakeven(self):
        C = lambda **kw: {'A': card('A', **kw)}
        b = bt.Book(5, cost=0, add=0, entry='wait')                          # ▲·초강세 확인 전에는 안 삼
        self.run_days(b, [({}, C(), ['A']), ({'A': (100, 101, 99, 100)}, C(), []),
                          ({'A': (100, 101, 99, 100)}, C(bull=True), []), ({'A': (102, 103, 101, 102)}, C(), [])])
        self.assertEqual(b.pos['A']['entry'], 102)
        self.assertAlmostEqual(b.pos['A']['spent'], .2)
        b = bt.Book(5, cost=0, add=0, entry='wait')                          # 확인 전 매수 카드에서 빠지면 취소
        self.run_days(b, [({}, C(), ['A']), ({'A': (100, 101, 99, 100)}, C(state='HOLD'), []),
                          ({'A': (100, 101, 99, 100)}, C(state='BUY', age=0), []), ({'A': (100, 101, 99, 100)}, {}, [])])
        self.assertEqual(b.pos, {})
        b = bt.Book(5, cost=0, regime=True)                                  # SPY 200일선 아래면 신규 매수 안 함
        b.mkt_ok = False
        self.run_days(b, [({}, C(), ['A']), ({'A': (100, 101, 99, 100)}, C(), [])])
        self.assertEqual(b.pos, {})
        b = bt.Book(5, cost=0, add=0, breakeven=True)                        # 1차 목표 후 손절가 = 매수가
        self.run_days(b, [({}, C(), ['A']), ({'A': (100, 111, 100, 110)}, C(), []),
                          ({'A': (104, 104, 99, 100)}, C(state='HOLD'), [])])
        self.assertEqual(b.closed[0]['exit'], '손절')
        self.assertAlmostEqual(b.cash, 1)                                   # 손절을 먼저 보므로 전량 매수가 100에 매도

    def test_no_bull_pause(self):
        days = self.scenario()
        b = bt.Book(5, cost=0, add=0, pause=False)
        self.run_days(b, days[:4])
        self.assertIn('1차 TS매도', b.pos['A']['events'])                    # 초강세여도 TS 매도 (기본은 쉼)
        b = bt.Book(5, cost=0, add=0)
        self.run_days(b, days[:4])
        self.assertNotIn('1차 TS매도', b.pos['A']['events'])

    def test_idle_cash_in_spy(self):
        b = bt.Book(5, cost=0, spy=True)
        spy = [(100, 100), (100, 110), (110, 110)]                          # SPY (시가, 종가)
        b.spy_bar = lambda k: spy[k]
        self.run_days(b, [({}, {'A': card('A')}, ['A']),                      # 종가 100에 현금 전부 SPY
                          ({'A': (10, 10, 10, 10)}, {'A': card('A')}, []),    # 시가 100에 20%만큼 팔고 A 매수
                          ({'A': (10, 10, 10, 10)}, {'A': card('A')}, [])])
        self.assertAlmostEqual(b.pos['A']['spent'], .2)
        self.assertAlmostEqual(b.spy_sh * 110, .8 * 1.1)
        self.assertAlmostEqual(b.curve[-1], .2 + .88)
        self.assertEqual(b.cash, 0)

    def test_signal_only_and_wait_signal(self):
        C = lambda **kw: {'A': card('A', **kw)}
        b = bt.Book(5, cost=0, entry='sigonly')                              # 카드 때 ▲ 없으면 안 삼
        self.run_days(b, [({}, C(), ['A']), ({'A': (100, 101, 99, 100)}, C(age=0), []), ({'A': (100, 101, 99, 100)}, C(), [])])
        self.assertEqual(b.pos, {})
        b = bt.Book(5, cost=0, entry='sigonly')                              # 있으면 30%
        self.run_days(b, [({}, C(age=1), ['A']), ({'A': (100, 101, 99, 100)}, C(), [])])
        self.assertAlmostEqual(b.pos['A']['spent'], .3)
        b = bt.Book(5, cost=0, entry='wait_sig')                             # 초강세는 무시, ▲ 뜬 다음 날 30%
        self.run_days(b, [({}, C(), ['A']), ({'A': (100, 101, 99, 100)}, C(bull=True), []),
                          ({'A': (100, 101, 99, 100)}, C(age=0), []), ({'A': (103, 104, 102, 103)}, C(), [])])
        self.assertEqual(b.pos['A']['entry'], 103)
        self.assertAlmostEqual(b.pos['A']['spent'], .3)

    def test_wait_signal_card_levels(self):
        C = lambda **kw: {'A': card('A', close=100, loss=-5, **kw)}          # 카드 손절가 95, 목표가 110·120
        b = bt.Book(5, cost=0, entry='wait_sig', first=.3, add=0, card_lv='recalc')
        self.run_days(b, [({}, C(), ['A']), ({'A': (102, 103, 101, 102)}, C(age=0), []), ({'A': (103, 104, 102, 103)}, C(), [])])
        p = b.pos['A']
        self.assertEqual(p['entry'], 103)
        self.assertAlmostEqual(p['stop'], 95)
        self.assertAlmostEqual(p['tp1'], 110)
        self.assertAlmostEqual(p['spent'], .3)
        days = [({}, C(), ['A']), ({'A': (96, 96, 95.5, 96)}, C(age=0), []), ({'A': (94, 95, 93, 94)}, C(), [])]
        b = bt.Book(5, cost=0, entry='wait_sig', first=.3, add=0, card_lv='recalc')   # 시가 94 <= 95: 매수가 기준
        self.run_days(b, days)
        self.assertAlmostEqual(b.pos['A']['stop'], 94 * .95)
        self.assertAlmostEqual(b.pos['A']['tp1'], 94 * 1.1)
        b = bt.Book(5, cost=0, entry='wait_sig', first=.3, add=0, card_lv='skip')     # 취소, 다시 안 삼
        self.run_days(b, days + [({'A': (94, 95, 93, 94)}, C(age=1), []), ({'A': (94, 95, 93, 94)}, C(), [])])
        self.assertEqual(b.pos, {})
        self.assertAlmostEqual(b.cash, 1)

    def test_index_below_200ma_goes_cash(self):
        b = bt.Book(5, cost=0, spy=True, idx_ma=True)
        px, ok = [(100, 100), (100, 90), (90, 99)], [True, False, True]
        b.spy_bar, b.idx_ok = (lambda k: px[k]), (lambda k: ok[k])
        self.run_days(b, [({}, {}, [])] * 3)
        self.assertAlmostEqual(b.curve[1], .9)                               # 200일선 아래: 종가 90에 매도
        self.assertEqual(b.spy_sh * 99, b.curve[2])                          # 다시 위: 종가 99에 매수
        self.assertAlmostEqual(b.curve[2], .9)

    def test_keep_tenth_when_profitable(self):
        sell = lambda px: ({'A': (px, px, px, px)}, {'A': card('A', state='SELL')}, [])
        hold = lambda px: ({'A': (px, px, px, px)}, {'A': card('A')}, [])
        b = bt.Book(5, cost=0, add=0, keep=.1)
        self.run_days(b, [({}, {'A': card('A')}, ['A']), hold(100), sell(105), hold(105),
                          ({'A': (200, 200, 200, 200)}, {'A': card('A')}, ['A'])])   # 재매수는 별도
        q = .2 / 100
        self.assertEqual(len(b.core), 1)
        self.assertAlmostEqual(b.core[0]['sh'], q * .1)
        self.assertAlmostEqual(b.closed[0]['ret'], .05)
        self.assertAlmostEqual(b.curve[-1], 1 + q * .9 * 5 + q * .1 * 100)            # 묻은 물량은 200으로 평가
        b = bt.Book(5, cost=0, add=0, keep=.1)                                       # 손실이면 전량 매도
        self.run_days(b, [({}, {'A': card('A')}, ['A']), hold(100), sell(99), hold(99)])
        self.assertEqual((b.core, b.pos), ([], {}))

    def test_index_trades_next_open(self):
        b = bt.Book(5, cost=0, spy=True, idx_ma=True, idx_open=True)
        px, ok = [(100, 100), (100, 90), (80, 99), (99, 99)], [True, False, True, True]
        b.spy_bar, b.idx_ok = (lambda k: px[k]), (lambda k: ok[k])
        self.run_days(b, [({}, {}, [])] * 4)
        self.assertEqual(b.curve[0], 1)                                     # 첫날 종가엔 아직 현금
        self.assertAlmostEqual(b.curve[1], .9)                              # 둘째 날 시가 100에 매수, 종가 90
        self.assertAlmostEqual(b.curve[2], .8)                              # 전날 이탈 -> 시가 80에 매도
        self.assertAlmostEqual(b.curve[3], .8)                              # 전날 회복 -> 시가 99에 매수

    def test_card_price_levels(self):
        b = bt.Book(5, cost=0, card_px=True)
        self.run_days(b, [({}, {'A': card('A', close=100)}, ['A']), ({'A': (104, 105, 103, 104)}, {'A': card('A', close=104)}, [])])
        p = b.pos['A']
        self.assertEqual(p['entry'], 104)
        self.assertAlmostEqual(p['stop'], 95)
        self.assertAlmostEqual(p['tp1'], 110)

    def test_sector_priority(self):
        cards = {'A': card('A', loss=-3, t1=10), 'B': card('B', loss=-6, t1=10), 'C': card('C', loss=-7, t1=10),
                 'D': card('D', loss=-4, t1=10), 'E': card('E', state='BUY')}
        sec = {'A': '금융', 'B': '정보기술', 'C': '정보기술', 'D': '헬스케어', 'E': '헬스케어'}
        b = bt.Book(2, cost=0, sector_rank=True)
        b.sector = sec
        self.run_days(b, [({}, cards, ['A', 'B', 'C', 'D']), ({t: (10, 10, 10, 10) for t in 'ABCD'}, cards, [])])
        # 정보기술 2·헬스케어 2(E 포함)·금융 1 -> 2개짜리 섞어서 손절률 순: D(-4) > B(-6) > C(-7), 금융 A는 뒤
        self.assertEqual(sorted(b.pos), ['B', 'D'])

    def test_index_cap(self):
        b = bt.Book(5, cost=0, spy=True, idx_ma=True, idx_open=True, idx_cap=.5)
        b.spy_bar, b.idx_ok = (lambda k: (100, 100)), (lambda k: True)
        self.run_days(b, [({}, {}, [])] * 3)
        self.assertAlmostEqual(b.spy_sh * 100, .5)
        self.assertAlmostEqual(b.cash, .5)

    def test_sel_elig_matches_buy_eligible(self):
        import card_rules as cr
        base = {'close': 100, 'ma120': 90, 'ma200': 95, 'record': True, 'vol_down': True, 'obv_signal': '중립',
                'roe3': 20, 'debt': 30, 'profit_years': 6, 'per': 10, 'per_med5': 15}
        for extra in ({}, {'per': 20}, {'close': 80}, {'vol_down': False}, {'obv_signal': '이탈'}, {'fin': True, 'pbr': .8, 'op_ttm_value': 5}):
            st = {**base, **extra}
            self.assertEqual(bt.sel_elig(st), cr.buy_eligible(st), extra)
        self.assertTrue(bt.sel_elig({**base, 'per': 20}, 'per'))
        self.assertTrue(bt.sel_elig({**base, 'close': 80}, 'ma'))
        self.assertTrue(bt.sel_elig({**base, 'roe3': 1}, 'fund'))

    def test_turnaround_card_only(self):
        ta = lambda **kw: {'A': {**card('A', loss=-3, close=100, **kw), 'op_ttm_value': -1, 'rev_q': True, 'rev_ttm': True}}
        b = bt.Book(5, cost=0, ta_mode='card')
        self.run_days(b, [({}, ta(), ['A']), ({'A': (100, 100, 100, 100)}, ta(), []),
                          ({'A': (90, 91, 80, 85)}, ta(), [])])          # 손절선 아래로 빠져도 유지
        self.assertIn('A', b.pos)
        self.run_days(b, [({'A': (85, 85, 85, 85)}, ta(state='SELL'), []), ({'A': (84, 84, 84, 84)}, {}, [])])
        self.assertEqual(b.pos, {})
        b = bt.Book(5, cost=0, ta_mode='normal')
        st = {**card('A', loss=-3, close=100), 'op_ttm_value': -1, 'rev_q': True, 'rev_ttm': True, 'ma120': 90, 'ma200': 85,
              'per': None, 'mcap_b': 1}
        self.run_days(b, [({}, {'A': st}, ['A']), ({'A': (100, 100, 100, 100)}, {'A': st}, [])])
        self.assertLess(b.pos['A']['stop'], 95.01)   # 턴어라운드 -3%가 아니라 일반 기준(최소 -5%)


if __name__ == '__main__':
    unittest.main()
