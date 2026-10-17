"""실적 신고가 종목에 야후 섹터를 붙인다. 조회한 섹터는 data/cache/sector.json에 저장해 다음부터 재사용."""
import json
import os
import sys
import time

import yfinance as yf

CACHE = sys.argv[1] if len(sys.argv) > 1 else 'data/cache'
OUT = sys.argv[2] if len(sys.argv) > 2 else 'docs/data.json'
KO = {
    'Technology': '정보기술', 'Healthcare': '헬스케어', 'Financial Services': '금융',
    'Consumer Cyclical': '경기소비재', 'Consumer Defensive': '필수소비재', 'Industrials': '산업재',
    'Energy': '에너지', 'Basic Materials': '소재', 'Utilities': '유틸리티',
    'Real Estate': '부동산', 'Communication Services': '커뮤니케이션 서비스',
}

p = f'{CACHE}/sector.json'
cache = json.load(open(p, encoding='utf-8')) if os.path.exists(p) else {}
data = json.load(open(OUT, encoding='utf-8'))

todo = [s['code'] for s in data['stocks'] if s['record'] and s['code'] not in cache]
for i, t in enumerate(todo):
    try:
        sec = yf.Ticker(t).info.get('sector')
        if sec:
            cache[t] = sec
    except Exception as e:
        print('섹터 실패', t, e)
    time.sleep(0.4)
    if i % 100 == 99:
        json.dump(cache, open(p, 'w', encoding='utf-8'), ensure_ascii=False)
json.dump(cache, open(p, 'w', encoding='utf-8'), ensure_ascii=False)

for s in data['stocks']:
    s['sector'] = KO.get(cache.get(s['code']), '미분류') if s['record'] else None
json.dump(data, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
print(f"섹터 조회 {len(todo)}건, 미분류 {sum(1 for s in data['stocks'] if s['sector'] == '미분류')}종목")
