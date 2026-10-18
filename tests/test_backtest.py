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


def card(t, state='BUY', loss=-5, t1=10, t2=20, age=None, bull=False, sc=False):
    return {'code': t, 'state': state, 'metrics': {'expected_loss': loss, 'target_1': t1, 'target_2': t2},
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


if __name__ == '__main__':
    unittest.main()
