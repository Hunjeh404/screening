import datetime as dt
import math
import random
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from hunbot import completed_day, daily_signals, ema, rsi, signal_ages, stochastic
from lib import price_metrics


class HunbotTest(unittest.TestCase):
    def test_ema_seed_and_rsi_wilder_seed(self):
        self.assertEqual(ema([None, 10, 20, 30], 3), [None, 10, 15, 22.5])
        # Three initial changes +2,-1,+3: average gain 5/3, loss 1/3 → RSI 83.33.
        values = rsi([10, 12, 11, 14, 13], 3)
        self.assertEqual(values[:3], [None] * 3)
        self.assertAlmostEqual(values[3], 100 * 5 / 6)
        self.assertAlmostEqual(values[4], 100 * 10 / 15)
        self.assertEqual(rsi(list(range(30)), 20)[-1], 100)
        self.assertEqual(rsi(list(range(30, 0, -1)), 20)[-1], 0)

    def test_stochastic_uses_high_and_low_not_close_extremes(self):
        self.assertEqual(stochastic([10] * 12, [15] * 12, [5] * 12, 12), [None] * 11 + [50])
        self.assertIsNone(stochastic([10] * 12, [10] * 12, [10] * 12, 12)[-1])

    def test_age_expiry_cancel_and_new_signal(self):
        def signals(lc, sc):
            return [dict(lc=a, sc=b) for a, b in zip(lc, sc)]
        self.assertEqual(signal_ages(signals([True,False,False,False], [False]*4)), [0,1,2,None])
        for cancel in (0,1,2):
            sc=[False]*4;sc[cancel]=True
            self.assertEqual(signal_ages(signals([True,False,False,False],sc))[cancel:], [None]*(4-cancel))
        self.assertEqual(signal_ages(signals([True,False,True,False],[False,True,False,False])), [0,None,0,1])
        self.assertEqual(signal_ages(signals([True,None,False],[False,None,False])), [0,None,None])

    def test_completed_daily_bars_in_market_timezone(self):
        for market, closing in [('us','2026-10-16T20:00:00+00:00'),('kr','2026-10-16T06:30:00+00:00')]:
            now=dt.datetime.fromisoformat(closing);day=dt.date(2026,10,16)
            self.assertFalse(completed_day(day,market,now-dt.timedelta(seconds=1)))
            self.assertTrue(completed_day(day,market,now))
            self.assertTrue(completed_day(day-dt.timedelta(days=1),market,now))
            self.assertFalse(completed_day(day+dt.timedelta(days=1),market,now))

    def test_extreme_trends_and_missing_data(self):
        for close, bull, panic in [(list(range(1,101)),True,False),(list(range(100,0,-1)),False,True)]:
            result=daily_signals(close,[x+1 for x in close],[x-.5 for x in close])[-1]
            self.assertEqual(result['super_bull'],bull);self.assertEqual(result['panic'],panic)
        rng=random.Random(19)
        close=[100+10*math.sin(i/7)+rng.uniform(-3,3) for i in range(300)]
        high=[c+1 for c in close];low=[c-1 for c in close]
        signals=daily_signals(close,high,low)
        self.assertTrue(any(s['lc'] for s in signals));self.assertTrue(any(s['sc'] for s in signals))
        high[-1]=float('nan')
        self.assertEqual(daily_signals(close,high,low)[-1],dict(super_bull=None,panic=None,lc=None,sc=None,lc_age=None))
        dates=[dt.date(2025,1,1)+dt.timedelta(days=i) for i in range(300)]
        self.assertIsNone(price_metrics(dates,close)['hunbot'])
        self.assertEqual(price_metrics(dates,close,highs=[c+1 for c in close],lows=low)['hunbot'],signals[-1])


if __name__=='__main__':
    unittest.main()
