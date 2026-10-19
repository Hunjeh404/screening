"""QQQ 종가·등락률·200일선 -> docs/qqq.json (남는 돈 TQQQ 매매 판단용, 끝난 거래일 기준)"""
import json
import os
import sys

import yfinance as yf

sys.path.insert(0, os.path.dirname(__file__))
from hunbot import completed_day

x = yf.download('QQQ', period='2y', interval='1d', auto_adjust=False, progress=False)
if hasattr(x.columns, 'levels'):
    x.columns = x.columns.get_level_values(0)
x = x.dropna(subset=['Close'])
x = x.loc[[completed_day(i.date(), 'us') for i in x.index]]
c = x['Close'].astype(float)
if len(c) < 200:
    raise SystemExit('QQQ 시세가 부족해 기존 값을 유지합니다')
out = {'date': c.index[-1].date().isoformat(), 'close': round(c.iloc[-1], 2),
       'change_pct': round((c.iloc[-1] / c.iloc[-2] - 1) * 100, 2), 'ma200': round(c.iloc[-200:].mean(), 2)}
json.dump(out, open('docs/qqq.json', 'w', encoding='utf-8'))
print('QQQ', out)
