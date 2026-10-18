import copy
import datetime as dt
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from card_rules import calc_card_metrics, buy_eligible, enrich, hold_reasons, sell_reasons, update_history, persist_cards, obv_signal, valuation, valuation_insufficient
from lib import price_metrics, analyze


def stock(**changes):
    s = dict(code='TEST',name='테스트',market='Nasdaq',fin=False,close=100,ma120=90,ma200=80,ma20=98,
             vol20=.01,vol60=.02,vol_down=True,change_pct=1,price_date='2026-10-16',record=True,stale=False,
             latest_q='2026Q3',rev_q=True,rev_ttm=True,op_q=True,op_ttm=True,ni_ttm=50,
             rev_ttm_value=500,prev_rev_ttm=400,op_ttm_value=60,prev_op_ttm=50,latest_op=15,
             roe3=18,debt=30,profit_years=5,per=10,per_med5=15,pbr=.8,marcap=1e9,mcap_b=1,
             sector='정보기술',q={'labels':['2026Q3'],'rev':[100],'op':[15]})
    s.update(changes)
    return s


class MetricsTest(unittest.TestCase):
    def test_normal_caps(self):
        self.assertEqual(calc_card_metrics(100,90,80,10,10,2,False,1),(-7,14,21))
        self.assertEqual(calc_card_metrics(100,90,80,None,None,2,False,1),(-10,20,30))
        self.assertEqual(calc_card_metrics(100,90,80,10,10,2,False,.3),(-10,20,30))
    def test_support_selection(self):
        self.assertEqual(calc_card_metrics(100,98,80,10,10,2,False,1)[0],-7)
        self.assertEqual(calc_card_metrics(100,97,95,10,10,2,False,1)[0],-5)
    def test_turnaround_target(self):
        self.assertEqual(calc_card_metrics(100,95,130,None,None,None,False,1),(-6.9,30,37))
    def test_finance_valuation(self):
        self.assertEqual(calc_card_metrics(100,90,80,10,12,.5,True,1),(-7,14,100))
    def test_missing_and_zero_prices(self):
        for x in (None,0,float('nan')):
            self.assertEqual(calc_card_metrics(x,90,80,10,20,1,False,1),(None,None,None))
    def test_monotonic_targets(self):
        for close in (70,85,100,120):
            for cap in (.1,.3,3,None):
                a,b,c=calc_card_metrics(close,90,110,10,11,.9,False,cap)
                self.assertTrue(-10<=a<=-5)
                self.assertGreaterEqual(c,b+6.99)
    def test_real_ma200_and_daily_change(self):
        closes=list(range(1,261));dates=[dt.date(2025,1,1)+dt.timedelta(days=i) for i in range(260)]
        p=price_metrics(dates,closes)
        self.assertEqual(p['ma200'],160.5)
        self.assertEqual(p['ma120'],200.5)
        self.assertEqual(p['ma20'],250.5)
        self.assertAlmostEqual(p['change_pct'],100/259)


class RuleTest(unittest.TestCase):
    def test_both_buy_paths(self):
        self.assertTrue(buy_eligible(stock()))
        self.assertTrue(buy_eligible(stock(per=None,ni_ttm=-5,roe3=-10,profit_years=0)))
        self.assertFalse(buy_eligible(stock(per=None,ni_ttm=None)))
    def test_obv_exclusion_and_full_period(self):
        s=stock(obv_signal='이탈');self.assertFalse(buy_eligible(s))
        self.assertIsNone(obv_signal({'c':[100]*52,'v':[1]*52}))
        self.assertEqual(obv_signal({'c':list(range(153,100,-1)),'v':[1]*53}),'이탈')
    def test_trend(self):
        s=stock();enrich(s);self.assertEqual(s['trend'],'상승추세')
        s=stock(ma200=120);enrich(s);self.assertEqual(s['trend'],'턴어라운드')
        s=stock(ni_ttm=-10,per=None);enrich(s);self.assertEqual(s['trend'],'턴어라운드')
    def test_all_sell_reasons(self):
        scenarios=[({'close':70},'추세이탈'),({'obv_signal':'이탈'},'수급악화'),
                   ({'vol20':.03,'close':97,'change_pct':-7},'패닉셀'),
                   ({'rev_ttm_value':300,'op_ttm_value':40},'피크아웃'),
                   ({'latest_op':-1},'적자전환'),({'roe3':11.9},'적자전환'),
                   ({'debt':70.1},'적자전환'),({'stale':True},'공시지연')]
        for changes,reason in scenarios:
            with self.subTest(changes=changes):self.assertIn(reason,sell_reasons(stock(**changes)))
    def test_sell_boundaries(self):
        self.assertNotIn('추세이탈',sell_reasons(stock(close=78.4)))
        self.assertNotIn('패닉셀',sell_reasons(stock(vol20=.02,close=97,change_pct=-7)))
        self.assertNotIn('적자전환',sell_reasons(stock(roe3=12,debt=70,latest_op=0)))
    def test_finance_and_turnaround_exceptions(self):
        self.assertNotIn('적자전환',sell_reasons(stock(fin=True,roe3=8,debt=200,latest_op=-1)))
        self.assertIn('적자전환',sell_reasons(stock(fin=True,roe3=7.9)))
        entry={'entry_turnaround':True,'entry_op_loss':True}
        self.assertNotIn('적자전환',sell_reasons(stock(roe3=-5,debt=100,latest_op=-1),entry))
        self.assertIn('적자전환',sell_reasons(stock(rev_q=False),entry))
    def test_all_hold_reasons(self):
        cases=[({'vol20':.02},'상승중'),({'per':15},'적정가치'),
               ({'vol20':.03,'close':97,'change_pct':-6},'눌림목'),
               ({'ma120':101,'ma200':102},'지지 테스트'),({'rev_q':False},'실적유지'),
               ({'roe3':14.9},'재무보합'),({'debt':50.1},'재무보합'),({'fin':True,'roe3':9.9},'재무보합')]
        for changes,reason in cases:
            with self.subTest(changes=changes):self.assertIn(reason,hold_reasons(stock(**changes)))
    def test_valuation_uses_state_not_per_or_pbr(self):
        for state, result in [('BUY', 'LOW'), ('HOLD', 'FAIR'), ('SELL', 'HIGH'), (None, None)]:
            for per in (None, 1, 15, 99):
                self.assertEqual(valuation(stock(state=state, per=per)), result)
        self.assertTrue(valuation_insufficient(stock(per=None)))
        self.assertTrue(valuation_insufficient(stock(per_med5=None)))
        self.assertFalse(valuation_insufficient(stock()))
        self.assertFalse(valuation_insufficient(stock(fin=True, per=None, pbr=2)))



class HistoryTest(unittest.TestCase):
    def test_buy_hold_sell_reentry_and_reload(self):
        h={}
        for day,changes,state in [(16,{},'BUY'),(17,{'vol_down':False,'vol20':.03},'HOLD'),
                                  (18,{'close':70},'SELL'),(19,{'record':False},'HOLD'),(20,{},'BUY')]:
            rows=[stock(price_date=f'2026-10-{day}',**changes)]
            update_history(rows,h,'us',f'2026-10-{day} 08:00');h=json.loads(json.dumps(h))
            self.assertEqual(rows[0]['state'],state)
            self.assertEqual(rows[0]['valuation'], {'BUY':'LOW','HOLD':'FAIR','SELL':'HIGH'}[state])
        e=h['tickers']['us:TEST'];self.assertEqual(e['first_entry_date'],'2026-10-16')
        self.assertEqual([t['to'] for t in e['transitions']],['BUY','HOLD','SELL','HOLD','BUY'])
    def test_never_buy_never_sell_and_sell_priority(self):
        rows=[stock(close=70)];h={};update_history(rows,h,'us','2026-10-16 08:00')
        self.assertIsNone(rows[0]['state']);self.assertEqual(h['tickers'],{})
        update_history([stock()],h,'us','2026-10-16 08:00')
        rows=[stock(latest_op=-1)];update_history(rows,h,'us','2026-10-17 08:00')
        self.assertTrue(rows[0]['buy_eligible']);self.assertEqual(rows[0]['state'],'SELL')
    def test_idempotence_and_highest(self):
        h={}
        update_history([stock()],h,'us','2026-10-16 08:00')
        update_history([stock()],h,'us','2026-10-16 08:00')
        update_history([stock(close=120)],h,'us','2026-10-17 08:00')
        self.assertEqual(len(h['tickers']['us:TEST']['transitions']),1)
        self.assertEqual(h['tickers']['us:TEST']['highest_price'],120)
        self.assertEqual(h['tickers']['us:TEST']['entry_price'],100)
        self.assertEqual(h['tickers']['us:TEST']['entry_metrics']['stop_price'],93)
    def test_missing_data_preserved(self):
        h={};update_history([stock()],h,'us','2026-10-16 08:00')
        rows=[];update_history(rows,h,'us','2026-10-17 08:00')
        self.assertTrue(rows[0]['data_missing']);self.assertEqual(rows[0]['state'],'BUY')
        self.assertEqual(h['tickers']['us:TEST']['last_seen'],'2026-10-16 08:00')
    def test_restart_and_market_isolation(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'card_history.json'
            persist_cards([stock()],'us','2026-10-16 08:00',p)
            persist_cards([stock()],'kr','2026-10-16 08:00',p)
            rows=[stock(per=15)];persist_cards(rows,'us','2026-10-17 08:00',p)
            h=json.loads(p.read_text());self.assertEqual(len(h['tickers']),2)
            self.assertEqual(rows[0]['state'],'HOLD')
    def test_turnaround_entry_loss_persists(self):
        h={};update_history([stock(per=None,ni_ttm=-1,op_ttm_value=-2)],h,'us','2026-10-16 08:00')
        rows=[stock(rev_q=False,record=False)];update_history(rows,h,'us','2026-10-17 08:00')
        self.assertEqual(rows[0]['state'],'SELL');self.assertIn('적자전환',rows[0]['state_reasons'])

if __name__=='__main__':unittest.main()
