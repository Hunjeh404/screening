"""양쪽 배치 CLI가 실제 JSON과 공유 이력을 만드는 통합 검증."""
import datetime as dt
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from lib import label_seq

class PipelineTest(unittest.TestCase):
    def test_us_kr_shared_history_and_ttm(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);us=root/'us';kr=root/'kr';out=root/'docs';us.mkdir();kr.mkdir();out.mkdir()
            labs=label_seq('2021Q1','2026Q2')
            q={l:{'rev':(i+1)*100.,'op':(i+1)*20.,'ni':(i+1)*15.} for i,l in enumerate(labs)}
            ann={f'{y}-12-31':{'rev':1000,'op':100,'ni':150,'equity':500} for y in range(2020,2026)}
            bs={l:{'equity':500,'liab':100} for l in labs}
            ext={'q':q,'a':ann,'bs':bs,'shares':{'val':100,'end':'2026-06-30'}}
            px={'close':100,'ma120':90,'ma200':80,'ma20':95,'vol20':.01,'vol60':.02,
                'change_pct':1,'price_date':'2026-10-16','high52':110,'qe':{l:150 for l in labs},'wk':None,'hunbot':{'lc_age':1,'super_bull':True,'panic':True,'sc':False,'lc':False}}
            def dump(folder,name,value): (folder/name).write_text(json.dumps(value))
            for name,value in [('listing.json',{'1':{'ticker':'TEST','name':'Test','exchange':'Nasdaq'}}),('sic.json',{'1':'1234'}),('fin.json',{'1':ext}),('prices.json',{'1':px})]:dump(us,name,value)
            per={}
            for l in labs:
                year=l[:4];quarter=int(l[-1]);per[l]={k:sum(q[f'{year}Q{n}'][k] for n in range(1,quarter+1)) for k in ('rev','op','ni')};per[l].update(equity=500,liab=100)
            for name,value in [('listing.json',{'000010':{'name':'한국','market':'KOSPI','shares':100}}),('corp.json',{'000010':'C1'}),('induty.json',{'C1':'1234'}),('fin2.json',{'C1':per}),('prices.json',{'000010':px}),('fx.json',{'krw_per_usd':1400,'date':'2026-10-16'})]:dump(kr,name,value)
            for script,cache,file in [('screen.py',us,'data.json'),('screen_kr.py',kr,'data_kr.json')]:
                subprocess.run([sys.executable,str(ROOT/'scripts'/script),str(cache),str(out/file)],check=True,capture_output=True)
                data=json.loads((out/file).read_text());s=data['stocks'][0]
                self.assertEqual(data['meta']['schema_version'],2)
                self.assertEqual(s['ma200'],80)
                self.assertEqual(s['prev_op'],q[labs[-2]]['op'])
                self.assertEqual(s['hunbot'],px['hunbot'])
                self.assertEqual(s['rev_ttm_value'],sum(q[l]['rev'] for l in labs[-4:]))
                self.assertEqual(s['prev_rev_ttm'],sum(q[l]['rev'] for l in labs[-5:-1]))
                self.assertEqual(s['state'],'BUY')
            history=json.loads((out/'card_history.json').read_text())
            self.assertEqual(set(history['tickers']),{'us:TEST','kr:000010'})
            korean=json.loads((out/'data_kr.json').read_text())['stocks'][0]
            self.assertAlmostEqual(korean['mcap_b'],10000/1400/1e9)
            # 후속 배치에서 PER 중앙값 도달 → 기존 이력을 읽어 HOLD로 유지.
            px['vol20']=.03;dump(us,'prices.json',{'1':px})
            subprocess.run([sys.executable,str(ROOT/'scripts/screen.py'),str(us),str(out/'data.json')],check=True,capture_output=True)
            data=json.loads((out/'data.json').read_text())
            self.assertEqual(data['stocks'][0]['state'],'HOLD')
            self.assertIn('상승중',data['stocks'][0]['state_reasons'])

if __name__=='__main__':unittest.main()
