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

    def test_half_buy_addon_targets_bull_and_trailing_stop(self):
        b = bt.Book(5, cost=0)
        A = lambda o, h, l, c: {'A': (o, h, l, c)}
        self.run_days(b, [
            ({}, {'A': card('A')}, ['A']),                                    # 매수 카드 등록 -> 다음 날 50%
            (A(100, 101, 99, 100), {'A': card('A', age=0)}, []),               # 50% 매수, ▲ -> 다음 날 나머지
            (A(100, 111, 100, 110), {'A': card('A', bull=True)}, []),          # 추가 매수, 1차 목표(110) 50% 매도, 초강세
            (A(110, 125, 80, 120), {'A': card('A', state='HOLD')}, []),        # 초강세라 손절·2차 목표 쉼
            (A(120, 121, 110, 115), {'A': card('A', state='HOLD')}, []),       # 최고 종가 120 -> 손절 114
        ])
        self.assertEqual(b.pos, {})
        tr = b.closed[0]
        self.assertEqual(tr['events'], '50%매수>추가매수>1차목표>손절(TS)')
        # 0.1씩 두 번 산 1000+1000/100 주 중 절반 110에, 나머지 114에 매도
        sh = .1 / 100 + .1 / 100
        self.assertAlmostEqual(b.cash, 1 - .2 + sh / 2 * 110 + sh / 2 * 114)

    def test_sell_card_market_exit_and_gap(self):
        b = bt.Book(5, cost=0)
        self.run_days(b, [
            ({}, {'A': card('A', age=1)}, ['A']),                              # ▲와 함께 등록 -> 전량
            ({'A': (100, 100, 100, 100)}, {'A': card('A', state='SELL')}, []),
            ({'A': (90, 95, 90, 92)}, {}, []),
        ])
        self.assertEqual(b.closed[0]['exit'], '매도카드()')
        self.assertAlmostEqual(b.cash, 1 - .2 + .2 / 100 * 90)
        b = bt.Book(5, cost=0)
        self.run_days(b, [({}, {'A': card('A', age=0)}, ['A']),
                          ({'A': (100, 100, 100, 100)}, {'A': card('A')}, []),
                          ({'A': (90, 95, 88, 92)}, {'A': card('A')}, [])])   # 시가가 손절가(95) 아래 -> 시가 체결
        self.assertEqual(b.closed[0]['exit'], '손절(TS)')
        self.assertAlmostEqual(b.cash, 1 - .2 + .2 / 100 * 90)

    def test_priority_slots_and_cost(self):
        b = bt.Book(1, cost=.001)
        cards = {'A': card('A', loss=-7, t1=30), 'B': card('B', loss=-3, t1=6), 'C': card('C', loss=-3, t1=9)}
        self.run_days(b, [({}, cards, ['A', 'B', 'C']),
                          ({t: (10, 10, 10, 10) for t in 'ABC'}, cards, [])])
        self.assertEqual(list(b.pos), ['C'])                                 # 손실률 작은 순, 같으면 1차 목표 큰 순
        self.assertAlmostEqual(b.pos['C']['spent'], .5)
        self.assertAlmostEqual(b.pos['C']['sh'], .5 / (10 * 1.001))
        self.assertEqual(b.orders, [])                                      # 꽉 차서 더 사지 않음


if __name__ == '__main__':
    unittest.main()
