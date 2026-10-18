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
             rev_ttm_value=500,prev_rev_ttm=400,op_ttm_value=60,prev_op_ttm=50,latest_op=15,prev_op=10,
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
    def test_turnaround_stop_is_tighter(self):
        self.assertEqual(calc_card_metrics(100,90,80,None,None,2,False,1,turnaround=True)[0],-5)
        self.assertEqual(calc_card_metrics(100,97,95,None,None,2,False,1,turnaround=True)[0],-4.9)
        self.assertEqual(calc_card_metrics(100,99,99,None,None,2,False,1,turnaround=True)[0],-3)
        for close in (70,85,100,120):
            self.assertTrue(-5<=calc_card_metrics(close,90,110,None,None,None,False,.1,turnaround=True)[0]<=-3)
        s=stock(op_ttm_value=-5,ni_ttm=-5,per=None,prev2_op=-20,prev_op=-10,latest_op=-1,fscore=7,fscore_max=9);enrich(s);self.assertEqual(s['metrics']['expected_loss'],-5)
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
        self.assertTrue(buy_eligible(stock(op_ttm_value=-5,ni_ttm=-5,per=None,prev2_op=-20,prev_op=-10,latest_op=-1,fscore=7,fscore_max=9,roe3=-10,profit_years=0)))
        self.assertFalse(buy_eligible(stock(per=None,ni_ttm=None)))
        # 순이익만 적자(영업이익 흑자)면 턴어라운드가 아니라 일반 경로 품질 조건을 받는다
        self.assertTrue(buy_eligible(stock(ni_ttm=-5)))
        self.assertFalse(buy_eligible(stock(ni_ttm=-5,per=None,profit_years=0,roe3=-10)))
    def test_loss_turnaround_requires_two_quarter_improvement(self):
        for ops, expected in [((-100,-60,-20),True),((-100,-60,20),True),((10,20,30),True),
                              ((-60,-100,-20),False),((-100,-20,-60),False),((-20,-20,-10),False),
                              ((None,-20,-10),False),((-30,None,-10),False),((-30,-20,None),False)]:
            with self.subTest(ops=ops):
                s=stock(op_ttm_value=-5,ni_ttm=-5,per=None,prev2_op=-20,prev_op=-10,latest_op=-1,fscore=7,fscore_max=9);s.update(prev2_op=ops[0],prev_op=ops[1],latest_op=ops[2])
                self.assertEqual(buy_eligible(s),expected)
        h={};update_history([stock(op_ttm_value=-5,ni_ttm=-5,per=None,prev2_op=-20,prev_op=-10,latest_op=-1,fscore=7,fscore_max=9)],h,'us','2026-10-16 08:00')
        rows=[stock(op_ttm_value=-5,ni_ttm=-5,per=None,prev2_op=-20,prev_op=-10,latest_op=-1,fscore=7,fscore_max=9)];rows[0]['latest_op']=-15
        update_history(rows,h,'us','2026-10-17 08:00')
        self.assertEqual(rows[0]['state'],'HOLD');self.assertIn('영익 개선 미달',rows[0]['state_reasons'])
    def test_just_turned_profitable_is_turnaround(self):
        TA=dict(per=None,prev2_op=-20,prev_op=-10,latest_op=5,fscore=7,fscore_max=9,roe3=-10,profit_years=0)
        self.assertTrue(buy_eligible(stock(op_ttm_value=3,op_ttm_year_ago=-40,**TA)))
        self.assertFalse(buy_eligible(stock(op_ttm_value=3,op_ttm_year_ago=10,**TA)))
        s=stock(op_ttm_value=3,op_ttm_year_ago=-40,**TA);enrich(s)
        self.assertEqual(s['trend'],'턴어라운드');self.assertEqual(s['metrics']['expected_loss'],-5)
    def test_profit_must_not_fall_with_revenue(self):
        for now, before, gp, gp_before, expected in [
                (100,100,None,None,True),(90,100,None,None,True),(89,100,None,None,False),
                (89,100,120,100,True),(89,100,100,100,False),(89,100,None,100,False),
                (60,None,None,None,True)]:
            with self.subTest(now=now,before=before,gp=gp):
                s=stock(op_ttm_value=now,op_ttm_year_ago=before,gp_ttm_value=gp,gp_ttm_year_ago=gp_before)
                self.assertEqual(buy_eligible(s),expected)
                self.assertEqual('이익 감소' in hold_reasons(s),not expected)
        # 금융업: 세전이익 흑자 + 이익 감소 없음 (시버트형: 3년 평균 ROE는 높지만 최근 적자)
        self.assertTrue(buy_eligible(stock(fin=True)))
        siebert=stock(fin=True,roe3=12.1,pbr=.9,op_ttm_value=-1,op_ttm_year_ago=20)
        self.assertFalse(buy_eligible(siebert))
        self.assertIn('세전이익 적자',hold_reasons(siebert));self.assertIn('이익 감소',hold_reasons(siebert))
        s=stock(fin=True,op_ttm_value=-1,ma200=80);enrich(s);self.assertNotEqual(s['trend'],'턴어라운드')
        # 턴어라운드 경로는 2분기 연속 개선으로 따로 보므로 이익 감소 조건을 적용하지 않는다
        self.assertTrue(buy_eligible(stock(per=None,op_ttm_value=-5,op_ttm_year_ago=-1,prev2_op=-20,prev_op=-10,latest_op=-1,fscore=7,fscore_max=9)))
    def test_loss_turnaround_fscore(self):
        for score, maximum, expected in [(7,9,True),(6,9,False),(9,9,True),(5,6,True),(4,6,False),
                                         (None,9,False),(7,None,False),(8,8,False)]:
            with self.subTest(score=score,maximum=maximum):
                s=stock(op_ttm_value=-5,ni_ttm=-5,per=None,prev2_op=-20,prev_op=-10,latest_op=-1,fscore=7,fscore_max=9);s.update(fscore=score,fscore_max=maximum)
                self.assertEqual(buy_eligible(s),expected)
        s=stock(op_ttm_value=-5,ni_ttm=-5,per=None,prev2_op=-20,prev_op=-10,latest_op=-1,fscore=7,fscore_max=9);s['fscore']=6;self.assertIn('F-스코어 미달',hold_reasons(s))
        # 턴어라운드 경로에서는 영업이익 신고가 실패를 보유 사유로 보이지 않는다
        s=stock(op_ttm_value=-5,ni_ttm=-5,per=None,prev2_op=-20,prev_op=-10,latest_op=-1,fscore=7,fscore_max=9);s.update(op_q=False,fscore=6);self.assertNotIn('실적유지',hold_reasons(s))
        self.assertIn('흑자 연수 미달',hold_reasons(stock(profit_years=3)))

    def test_obv_ignores_noise_and_unfinished_week(self):
        import random
        random.seed(3)
        noisy=0
        for _ in range(200):  # 무작위 등락만으로는 이탈 신호가 거의 나지 않아야 한다
            c=[100.]
            for _ in range(52): c.append(c[-1]*(1+random.gauss(0,.03)))
            v=[random.randint(1,100) for _ in c]
            noisy+=obv_signal({'c':c,'v':v})=='이탈'
        self.assertLess(noisy,30)
        falling={'c':list(range(153,100,-1)),'v':[1]*53}
        self.assertEqual(obv_signal({**falling,'complete':True}),'이탈')
        # 끝나지 않은 주는 빼므로 53개가 안 되면 판정하지 않는다
        self.assertIsNone(obv_signal({**falling,'complete':False}))
        self.assertEqual(obv_signal({'c':falling['c']+[999],'v':falling['v']+[10**9],'complete':False}),'이탈')
    def test_no_disclosure_delay_rule(self):
        self.assertTrue(buy_eligible(stock(stale=True)))
        self.assertNotIn('공시지연',sell_reasons(stock(stale=True)))
    def test_ma_switch(self):
        import card_rules
        s=stock(close=70)
        self.assertFalse(buy_eligible(s));self.assertIn('추세이탈',sell_reasons(s))
        card_rules.USE_MA=False
        try:
            self.assertTrue(buy_eligible(s));self.assertNotIn('추세이탈',sell_reasons(s))
        finally:
            card_rules.USE_MA=True
    def test_obv_exclusion_and_full_period(self):
        s=stock(obv_signal='이탈');self.assertFalse(buy_eligible(s))
        self.assertIsNone(obv_signal({'c':[100]*52,'v':[1]*52}))
        self.assertEqual(obv_signal({'c':list(range(153,100,-1)),'v':[1]*53}),'이탈')
    def test_trend(self):
        s=stock();enrich(s);self.assertEqual(s['trend'],'상승우위')
        s=stock(ma200=120);enrich(s);self.assertEqual(s['trend'],'턴어라운드')
        s=stock(op_ttm_value=-10,per=None);enrich(s);self.assertEqual(s['trend'],'턴어라운드')
        s=stock(ni_ttm=-10);enrich(s);self.assertEqual(s['trend'],'상승우위')
    def test_all_sell_reasons(self):
        scenarios=[({'close':70},'추세이탈'),({'obv_signal':'이탈'},'수급악화'),
                   ({'vol20':.03,'close':97,'change_pct':-7},'투매'),
                   ({'rev_ttm_value':300,'op_ttm_value':40},'피크아웃'),
                   ({'latest_op':-1},'적자전환'),({'roe3':11.9},'적자전환'),
                   ({'debt':70.1},'적자전환')]
        for changes,reason in scenarios:
            with self.subTest(changes=changes):self.assertIn(reason,sell_reasons(stock(**changes)))
    def test_sell_boundaries(self):
        self.assertNotIn('추세이탈',sell_reasons(stock(close=78.4)))
        self.assertNotIn('투매',sell_reasons(stock(vol20=.02,close=97,change_pct=-7)))
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
    def test_sell_tab_kept_five_update_days_then_deleted(self):
        h={};update_history([stock()],h,'us','2026-10-16 08:00')
        for day in range(17,22):
            for hour in ('08:00','20:00'):  # 같은 날 재실행은 1회로 센다
                rows=[stock(close=70)];update_history(rows,h,'us',f'2026-10-{day} {hour}')
                self.assertEqual(rows[0]['state'],'SELL')
        rows=[stock(close=70)];update_history(rows,h,'us','2026-10-22 08:00')
        self.assertIsNone(rows[0]['state']);self.assertNotIn('us:TEST',h['tickers'])
        # 지운 뒤 조건을 다시 충족하면 새 편입
        rows=[stock()];update_history(rows,h,'us','2026-10-23 08:00')
        self.assertEqual(rows[0]['state'],'BUY');self.assertEqual(h['tickers']['us:TEST']['first_entry_date'],'2026-10-23')
    def test_sell_count_resets_when_leaving_sell(self):
        h={};update_history([stock()],h,'us','2026-10-16 08:00')
        for day,changes in [(17,{'close':70}),(18,{'close':70}),(19,{}),(20,{'close':70}),(21,{'close':70}),
                            (22,{'close':70}),(23,{'close':70}),(24,{'close':70})]:
            rows=[stock(**changes)];update_history(rows,h,'us',f'2026-10-{day} 08:00')
        self.assertEqual(rows[0]['state'],'SELL');self.assertEqual(h['tickers']['us:TEST']['sell_days'],5)
    def test_delisted_removed_but_fetch_gap_kept(self):
        listed={f'T{i}' for i in range(600)}
        h={};update_history([stock(),stock(code='T1')],h,'us','2026-10-16 08:00',listed|{'TEST'})
        rows=[];update_history(rows,h,'us','2026-10-17 08:00',listed)
        self.assertEqual([r['code'] for r in rows],['T1']);self.assertNotIn('us:TEST',h['tickers'])
        # 상장 목록 수집이 실패해 너무 작으면 지우지 않는다
        h={};update_history([stock()],h,'us','2026-10-16 08:00')
        rows=[];update_history(rows,h,'us','2026-10-17 08:00',{'X'})
        self.assertTrue(rows[0]['data_missing'])
    def test_turnaround_entry_loss_persists(self):
        h={};update_history([stock(op_ttm_value=-5,ni_ttm=-5,per=None,prev2_op=-20,prev_op=-10,latest_op=-1,fscore=7,fscore_max=9)],h,'us','2026-10-16 08:00')
        rows=[stock(rev_q=False,record=False)];update_history(rows,h,'us','2026-10-17 08:00')
        self.assertEqual(rows[0]['state'],'SELL');self.assertIn('적자전환',rows[0]['state_reasons'])

if __name__=='__main__':unittest.main()
