"""한국(DART) 재무 -> 미국과 같은 형식(ext)으로 변환. 지표 계산은 lib.analyze를 함께 쓴다."""
import datetime as dt

ACCT = {
    'rev': ('매출액', '영업수익', '수익(매출액)', '매출'),
    'op': ('영업이익',),
    'ni': ('당기순이익',),
    'liab': ('부채총계',),
    'equity': ('자본총계',),
}
IS_KEYS = ('rev', 'op', 'ni')


def acct_key(name):
    n = name.replace(' ', '').replace('(손실)', '')
    for k, names in ACCT.items():
        if n in names:
            return k
    return None


def to_num(s):
    s = (s or '').replace(',', '').strip()
    if s in ('', '-'):
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def span_days(dtxt):
    """'2024.07.01 ~ 2024.09.30' -> 91"""
    try:
        a, b = [x.strip() for x in dtxt.split('~')]
        return (dt.datetime.strptime(b, '%Y.%m.%d') - dt.datetime.strptime(a, '%Y.%m.%d')).days
    except Exception:
        return None


def parse_multi_acnt(items, q):
    """DART 다중회사 주요계정 응답 -> {corp_code: {key: 값}}.
    손익 항목은 회계연도 누적값으로 맞춘다(2·3분기 3개월값이면 누적값 사용). 연결 우선."""
    out = {}
    for it in items:
        k = acct_key(it.get('account_nm', ''))
        if not k:
            continue
        amt = to_num(it.get('thstrm_amount'))
        if k in IS_KEYS and q in (2, 3):
            # 2·3분기 손익: thstrm_amount는 3개월 값, thstrm_add_amount가 누적 값.
            # 누적 칸이 있으면 그것을 쓰고, 없을 때만 기간 표기로 3개월 값인지 판단
            add = to_num(it.get('thstrm_add_amount'))
            if add is not None:
                amt = add
            else:
                n = span_days(it.get('thstrm_dt', ''))
                if n is not None and n < 120:
                    amt = None
        if amt is None:
            continue
        out.setdefault(it['corp_code'], {}).setdefault(it.get('fs_div', 'OFS'), {}).setdefault(k, amt)
    return {c: (v.get('CFS') or v.get('OFS')) for c, v in out.items()}


def to_ext(per):
    """{'2024Q3': 누적 손익 + 재무상태} -> {'q': 분기 손익, 'a': 연간, 'bs': 재무상태}"""
    q, a, bs = {}, {}, {}
    for y in sorted({int(p[:4]) for p in per}):
        prev = {k: 0 for k in IS_KEYS}
        ok = {k: True for k in IS_KEYS}
        for n in range(1, 5):
            lab = f'{y}Q{n}'
            p = per.get(lab)
            if not p:
                ok = {k: False for k in IS_KEYS}
                continue
            row = {}
            for k in IS_KEYS:
                c = p.get(k)
                if c is not None and ok[k]:
                    row[k] = c - prev[k]
                ok[k] = c is not None
                if c is not None:
                    prev[k] = c
            if row:
                q[lab] = row
            if p.get('equity') is not None:
                bs[lab] = {'equity': p['equity'], 'liab': p.get('liab')}
        if f'{y}Q4' in per:
            p = per[f'{y}Q4']
            a[f'{y}-12-31'] = {k: p.get(k) for k in ('rev', 'op', 'ni', 'equity')}
    return {'q': q, 'a': a, 'bs': bs}
