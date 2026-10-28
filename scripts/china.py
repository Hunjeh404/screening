"""중국계 기업 거르기.
미국: SEC 등록 본사·우편 주소나 설립지가 중국·홍콩·마카오인 회사.
한국: DART 회사 주소가 중국·홍콩·마카오이거나, 최대주주(지분율 1위) 이름이 중국·홍콩 쪽 법인으로 보이는 회사.
대주주 개인의 국적은 공시에 없어 판별하지 못한다. 결과는 캐시에 두고 30일마다 다시 확인한다."""
import datetime as dt
import json
import os
import re
import time

import requests

CN_CODES = {'F4': '중국', 'K3': '홍콩', 'N5': '마카오'}   # EDGAR 국가 코드
PAT = re.compile(r'china|chinese|hong\s*kong|macau|macao|beijing|shanghai|shenzhen|guangzhou|'
                 r'中国|中國|香港|澳門|澳门|北京|上海|深圳|广州|廣州|중국|홍콩|차이나|마카오|베이징|상하이|광저우', re.I)
DAYS = 30
MANUAL = os.path.join(os.path.dirname(__file__), 'cn_exclude.json')


def manual(market):
    """직접 지정한 제외 종목 {코드: 사유}"""
    return json.load(open(MANUAL, encoding='utf-8')).get(market, {}) if os.path.exists(MANUAL) else {}


def us_reason(sub):
    """SEC submissions JSON -> 제외 사유 문자열 또는 None"""
    a = sub.get('addresses') or {}
    for key, label in (('business', '본사'), ('mailing', '우편 주소')):
        c = (a.get(key) or {}).get('stateOrCountry')
        if c in CN_CODES:
            return f'{label} {CN_CODES[c]}'
    c = sub.get('stateOfIncorporation')
    return f'설립지 {CN_CODES[c]}' if c in CN_CODES else None


def kr_reason(company, holders):
    """DART 기업개황(company.json)과 최대주주 현황 목록 -> 제외 사유 또는 None"""
    adres = (company or {}).get('adres') or ''
    if re.search(r'중국|홍콩|마카오|china|hong\s*kong|macau|中国|中國|香港', adres, re.I):
        return f'주소 {adres[:30]}'
    rate = lambda r: float(str(r.get('trmend_posesn_stock_qota_rt') or '0').replace(',', '') or 0)
    rows = [r for r in holders or [] if (r.get('nm') or '').strip() not in ('', '계', '합계')]
    if rows:
        top = max(rows, key=rate)
        if PAT.search(top.get('nm') or ''):
            return f"최대주주 {top['nm']}"
    return None


def _load(path):
    return json.load(open(path, encoding='utf-8')) if os.path.exists(path) else {}


def _save(path, obj):
    with open(path + '.tmp', 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(path + '.tmp', path)


def _fresh(entry, today):
    return entry and (today - dt.date.fromisoformat(entry['date'])).days < DAYS


def us_flags(ciks, cache_path, ua):
    """{cik: 사유} (걸린 것만). 조회 실패한 종목은 거르지 않고 다음에 다시 본다."""
    cache, today = _load(cache_path), dt.date.today()
    for cik in ciks:
        if _fresh(cache.get(cik), today):
            continue
        try:
            r = requests.get(f'https://data.sec.gov/submissions/CIK{int(cik):010d}.json',
                             headers={'User-Agent': ua}, timeout=30)
            r.raise_for_status()
            cache[cik] = {'why': us_reason(r.json()), 'date': today.isoformat()}
        except Exception as e:
            print('중국계 확인 실패', cik, type(e).__name__, flush=True)
        time.sleep(0.12)   # SEC 초당 10회 제한
    _save(cache_path, cache)
    return {c: cache[c]['why'] for c in ciks if cache.get(c, {}).get('why')}


def kr_flags(codes, corp, cache_path, key):
    """codes: 종목코드 목록, corp: 종목코드 -> DART 고유번호. {종목코드: 사유} (걸린 것만)."""
    cache, today = _load(cache_path), dt.date.today()
    api = 'https://opendart.fss.or.kr/api'

    def get(path, **p):
        r = requests.get(f'{api}/{path}', params={'crtfc_key': key, **p}, timeout=60)
        r.raise_for_status()
        time.sleep(0.07)
        return r.json()

    for code in codes:
        cc = corp.get(code)
        if not cc or _fresh(cache.get(code), today):
            continue
        try:
            company = get('company.json', corp_code=cc)
            holders = []
            # 가장 최근 정기보고서의 최대주주 현황 (3분기 -> 반기 -> 1분기 -> 전년 사업보고서 순)
            for y, rc in ((today.year, '11014'), (today.year, '11012'), (today.year, '11013'),
                          (today.year - 1, '11011'), (today.year - 1, '11014')):
                j = get('hyslrSttus.json', corp_code=cc, bsns_year=str(y), reprt_code=rc)
                if j.get('status') == '000' and j.get('list'):
                    holders = j['list']
                    break
            cache[code] = {'why': kr_reason(company, holders), 'date': today.isoformat()}
        except Exception as e:
            print('중국계 확인 실패', code, type(e).__name__, flush=True)
    _save(cache_path, cache)
    return {c: cache[c]['why'] for c in codes if cache.get(c, {}).get('why')}
