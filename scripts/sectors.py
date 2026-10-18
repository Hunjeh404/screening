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

tk = lambda s: s.get('yahoo', s['code'])  # 한국 종목은 005930.KS 같은 야후 티커
todo = [tk(s) for s in data['stocks'] if (s['record'] or s.get('state')) and tk(s) not in cache]
def lookup(t):
    # 여러 종목을 연달아 조회하면 야후 요청 제한에 걸려 빈 값이 오므로 간격을 늘려 다시 시도한다
    for wait in (0, 5, 20):
        time.sleep(wait)
        try:
            sec = yf.Ticker(t).info.get('sector')
            if sec:
                return sec
        except Exception as e:
            print('섹터 실패', t, type(e).__name__)
    return None


for i, t in enumerate(todo):
    sec = lookup(t)
    if sec:
        cache[t] = sec
    time.sleep(0.4)
    if i % 100 == 99:
        json.dump(cache, open(p, 'w', encoding='utf-8'), ensure_ascii=False)
json.dump(cache, open(p, 'w', encoding='utf-8'), ensure_ascii=False)

for s in data['stocks']:
    s['sector'] = KO.get(cache.get(tk(s)), '미분류') if s['record'] or s.get('state') else None
json.dump(data, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
print(f"섹터 조회 {len(todo)}건, 미분류 {sum(1 for s in data['stocks'] if s['sector'] == '미분류')}종목")

history_path = os.path.join(os.path.dirname(OUT), 'card_history.json')
if os.path.exists(history_path):
    from card_rules import write_json
    history = json.load(open(history_path, encoding='utf-8'))
    market = 'kr' if OUT.endswith('data_kr.json') else 'us'
    for s in data['stocks']:
        entry = history.get('tickers', {}).get(f"{market}:{s['code']}")
        if entry and entry.get('snapshot'):
            entry['snapshot']['sector'] = s.get('sector')
    write_json(history_path, history)
