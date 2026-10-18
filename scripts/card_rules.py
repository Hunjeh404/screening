"""매매 상태·가치평가·목표가의 단일 기준. 네트워크/화면에 의존하지 않는다."""
import copy
import json
import math
import os
from pathlib import Path

SCHEMA_VERSION = 2
# 적자 턴어라운드 F-스코어 통과 점수: 미국 9개 항목 중 7점, 한국 6개 항목 중 5점
FSCORE_NEED = {9: 7, 6: 5}
# 이동평균 조건(매수: 종가 ≥ 120·200일선 중 하나, 매도: 추세이탈) 사용 여부. 백테스트 비교용
USE_MA = True
# 공시지연(시장 최빈 분기보다 2분기 이상 늦음) 사용 여부. 앱은 쓰지 않음, 백테스트 비교용
USE_STALE = False
# 매도 상태로 보여 주는 갱신일 수. 이를 넘기면 이력까지 지운다
SELL_KEEP_DAYS = 5
# 최근 4분기 영업이익의 1년 전 대비 허용 감소폭
PROFIT_DROP_ALLOWED = .10


def valid(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def positive(value):
    return valid(value) and value > 0


def calc_card_metrics(close, ma120, ma200, per, med_per_5y, pbr, is_finance, mcap_b, turnaround=False):
    if not all(positive(x) for x in (close, ma120, ma200)):
        return None, None, None
    # 시총/환율 누락은 일반주로 단정하지 않고 최대 위험폭을 보수적으로 사용한다.
    max_stop = -10.0 if (not valid(mcap_b) or mcap_b <= .3 or not positive(per)) else -7.0
    min_stop = -5.0
    if turnaround:  # 적자 턴어라운드는 일반주보다 좁게: 최소 -3%, 최대 -5%
        max_stop, min_stop = -5.0, -3.0
    if close >= ma120 and close >= ma200:
        high_ma, low_ma = max(ma120, ma200), min(ma120, ma200)
        support = (high_ma if (close - high_ma) / close >= .03 else low_ma) * .98
    else:
        support = (ma120 if close >= ma120 else ma200) * .98
    risk = max(max_stop, min(min_stop, (support / close - 1) * 100))
    r = abs(risk)
    target1 = max((ma200 / close - 1) * 100, r * 2) if close < ma200 else r * 2
    val = (med_per_5y / per - 1) * 100 if positive(per) and positive(med_per_5y) and med_per_5y > per else 0
    if is_finance and positive(pbr) and pbr < 1:
        val = max(val, (1 / pbr - 1) * 100)
    target2 = max(val, r * 3, target1 + 7)
    return round(risk, 1), round(target1, 1), round(target2, 1)


def obv_signal(w):
    """끝난 주봉 52주의 주가·OBV 변화를 각각 '평균 주간 변동폭 × √52'로 나눠 비교한다.
    무작위 움직임의 누적 폭이 대략 √52배이므로, ±1을 넘으면 우연이 아닌 뚜렷한 움직임으로 본다.
    아직 끝나지 않은 이번 주는 빼서 신호가 주 1회만 바뀌게 한다."""
    if not w:
        return None
    c, v = w.get('c', []), w.get('v', [])
    if not w.get('complete', True):
        c, v = c[:-1], v[:-1]
    if len(c) < 53 or len(v) < 53:
        return None
    c, v = c[-53:], v[-53:]
    if not all(positive(x) for x in c) or not all(valid(x) and x >= 0 for x in v):
        return None
    changes = [(1 if c[i] > c[i-1] else -1 if c[i] < c[i-1] else 0) * v[i] for i in range(1, 53)]
    sum_r = sum(abs(c[i] / c[i-1] - 1) for i in range(1, 53))
    sum_d = sum(abs(x) for x in changes)
    if not sum_r or not sum_d:
        return '중립'
    scale = math.sqrt(52)
    pz, oz = (c[-1] / c[0] - 1) / (sum_r / 52 * scale), sum(changes) / (sum_d / 52 * scale)
    if (pz < -1 and oz < pz) or (oz < -1 and pz >= -1):
        return '이탈'
    return '매집' if oz > 1 and pz <= 1 else '중립'


def op_loss(s):
    """최근 4분기 영업적자이거나, 1년 전 4분기 합계가 영업적자(흑자 전환 직후)"""
    return any(valid(s.get(k)) and s[k] < 0 for k in ('op_ttm_value', 'op_ttm_year_ago'))


def loss_turnaround(s):
    # 적자 판단은 영업이익 기준. 순이익만 적자(일회성 손실 등)인 회사는 일반 경로로 판정한다.
    # 막 흑자로 돌아선 회사도 일반 경로(5년 연속 흑자 등)에 맞지 않으므로 턴어라운드로 본다.
    return op_loss(s) and bool(s.get('rev_q') and s.get('rev_ttm'))


def op_improving(s):
    """분기 영업이익 2분기 연속 개선: 전전분기 < 전분기 < 최근 분기"""
    ops = [s.get(k) for k in ('prev2_op', 'prev_op', 'latest_op')]
    return all(valid(x) for x in ops) and ops[0] < ops[1] < ops[2]


def profit_holding(s):
    """매출과 이익의 엇갈림 방지: 최근 4분기 영업이익(금융업은 세전이익)이 1년 전보다 10% 넘게 줄면 탈락.
    다만 매출총이익이 1년 전보다 늘었으면 비용 투자로 인한 감익으로 보고 통과(미국만 자료 있음).
    1년 전 값이 없으면 판단하지 않는다."""
    now, before = s.get('op_ttm_value'), s.get('op_ttm_year_ago')
    if not (valid(now) and valid(before)):
        return True
    if now >= before - abs(before) * PROFIT_DROP_ALLOWED:
        return True
    gp, gp_before = s.get('gp_ttm_value'), s.get('gp_ttm_year_ago')
    return valid(gp) and valid(gp_before) and gp > gp_before


def fscore_ok(s):
    need = FSCORE_NEED.get(s.get('fscore_max'))
    return need is not None and valid(s.get('fscore')) and s['fscore'] >= need


def buy_eligible(s):
    c, a, b = (s.get(k) for k in ('close', 'ma120', 'ma200'))
    if not all(positive(x) for x in (c, a, b)) or not s.get('record') or (USE_STALE and s.get('stale')):
        return False
    if not ((not USE_MA or c >= a or c >= b) and s.get('vol_down')) or s.get('obv_signal') == '이탈':
        return False
    if s.get('fin'):
        return (positive(s.get('pbr')) and s['pbr'] <= 1 and valid(s.get('roe3')) and s['roe3'] >= 10
                and positive(s.get('op_ttm_value')) and profit_holding(s))
    if loss_turnaround(s):
        return op_improving(s) and fscore_ok(s)
    return profit_holding(s) and (valid(s.get('roe3')) and s['roe3'] >= 15 and valid(s.get('debt')) and s['debt'] <= 50
            and s.get('profit_years', 0) >= 5 and positive(s.get('per')) and positive(s.get('per_med5'))
            and s['per'] < s['per_med5'])


def peak_out(s):
    keys = ('rev_ttm_value', 'prev_rev_ttm', 'op_ttm_value', 'prev_op_ttm')
    return all(valid(s.get(k)) for k in keys) and s[keys[0]] < s[keys[1]] and s[keys[2]] < s[keys[3]]


def sell_reasons(s, entry=None):
    reasons = []
    c, a, b = (s.get(k) for k in ('close', 'ma120', 'ma200'))
    if USE_MA and all(positive(x) for x in (c, a, b)) and c < a * .98 and c < b * .98:
        reasons.append('추세이탈')
    if s.get('obv_signal') == '이탈':
        reasons.append('수급악화')
    v20, v60, ma20, change = (s.get(k) for k in ('vol20', 'vol60', 'ma20', 'change_pct'))
    if all(valid(x) for x in (v20, v60, c, ma20, change)) and v20 > v60 and c < ma20 and change <= -7:
        reasons.append('투매')
    if peak_out(s):
        reasons.append('피크아웃')
    if USE_STALE and s.get('stale'):
        reasons.append('공시지연')
    roe, debt, op = (s.get(k) for k in ('roe3', 'debt', 'latest_op'))
    original_loss = bool((entry or {}).get('entry_op_loss'))
    turnaround = bool((entry or {}).get('entry_turnaround')) or loss_turnaround(s)
    if s.get('fin') or turnaround:
        bad = (s.get('fin') and valid(roe) and roe < 8) or (original_loss and s.get('rev_q') is False)
    else:
        bad = (valid(op) and op < 0) or (valid(roe) and roe < 12) or (valid(debt) and debt > 70)
    if bad:
        reasons.append('적자전환')
    return reasons


def hold_reasons(s):
    reasons = []
    c, a, b, ma20, v20, v60 = (s.get(k) for k in ('close', 'ma120', 'ma200', 'ma20', 'vol20', 'vol60'))
    if all(valid(x) for x in (c, ma20, v20, v60)) and v20 >= v60:
        reasons.append('상승중' if c >= ma20 else '눌림목')
    if ((s.get('fin') and positive(s.get('pbr')) and s['pbr'] > 1)
        or (not s.get('fin') and positive(s.get('per')) and positive(s.get('per_med5')) and s['per'] >= s['per_med5'])):
        reasons.append('적정가치')
    if all(positive(x) for x in (c, a, b)) and c < a and c < b and (c >= a * .98 or c >= b * .98):
        reasons.append('지지 테스트')
    turnaround = not s.get('fin') and loss_turnaround(s)
    # 턴어라운드 경로는 매출 신고가만 보므로 영업이익 신고가 실패는 사유가 아니다.
    record_miss = s.get('rev_q') is False if turnaround else (s.get('rev_q') is False or s.get('op_q') is False)
    if record_miss and not peak_out(s):
        reasons.append('실적유지')
    if turnaround:
        if not op_improving(s):
            reasons.append('영익 개선 미달')
        if not fscore_ok(s):
            reasons.append('F-스코어 미달')
    else:
        if not profit_holding(s):
            reasons.append('이익 감소')
        if s.get('fin') and not positive(s.get('op_ttm_value')):
            reasons.append('세전이익 적자')
        roe, debt = s.get('roe3'), s.get('debt')
        if ((s.get('fin') and valid(roe) and 8 <= roe < 10)
            or (not s.get('fin') and ((valid(roe) and 12 <= roe < 15) or (valid(debt) and 50 < debt <= 70)))):
            reasons.append('재무보합')
        if not s.get('fin') and s.get('profit_years', 0) < 5:
            reasons.append('흑자 연수 미달')
    return reasons or ['매수조건 미충족']


def valuation(s):
    """탭은 PER/PBR 구간이 아니라 매수·보유·매도 상태의 표시 이름이다."""
    return {'BUY': 'LOW', 'HOLD': 'FAIR', 'SELL': 'HIGH'}.get(s.get('state'))


def valuation_insufficient(s):
    if s.get('fin'):
        return not positive(s.get('pbr'))
    return not (positive(s.get('per')) and positive(s.get('per_med5')))


def enrich(s):
    c, a, b = (s.get(k) for k in ('close', 'ma120', 'ma200'))
    s['obv_signal'] = obv_signal(s.get('wk'))
    s['trend'] = None
    # 적자 턴어라운드는 비금융만. 가격 기준(120일선 회복·200일선 아래)은 업종과 무관하게 표시.
    if (not s.get('fin') and loss_turnaround(s)) or (all(positive(x) for x in (c, a, b)) and c >= a and c < b):
        s['trend'] = '턴어라운드'
    elif all(positive(x) for x in (c, a, b)) and c >= a and c >= b and positive(s.get('per')) and not loss_turnaround(s):
        s['trend'] = '상승우위'
    for key in ('ma120', 'ma200'):
        s['dist_' + key] = round((c / s[key] - 1) * 100, 1) if positive(c) and positive(s.get(key)) else None
    risk, t1, t2 = calc_card_metrics(c, a, b, s.get('per'), s.get('per_med5'), s.get('pbr'), s.get('fin'), s.get('mcap_b'),
                                     turnaround=not s.get('fin') and loss_turnaround(s))
    s['metrics'] = {'expected_loss': risk, 'target_1': t1, 'target_2': t2,
                    'stop_price': round(c * (1 + risk / 100), 2) if risk is not None else None,
                    'target_1_price': round(c * (1 + t1 / 100), 2) if t1 is not None else None,
                    'target_2_price': round(c * (1 + t2 / 100), 2) if t2 is not None else None}
    s['valuation_insufficient'] = valuation_insufficient(s)
    s['buy_eligible'] = buy_eligible(s)


def update_history(stocks, history, market, generated, listed=None):
    """시장별 티커 키, 매도 우선, 최초 BUY 이력 없는 SELL 금지. 동일 시세 재실행 멱등.
    매도 상태로 SELL_KEEP_DAYS 갱신일을 넘기거나 상장 목록(listed)에서 빠지면 이력까지 지운다."""
    history.setdefault('version', 1)
    entries = history.setdefault('tickers', {})
    seen = set()
    for s in stocks:
        enrich(s)
        key = f"{market}:{s['code']}"
        seen.add(key)
        old = entries.get(key)
        reasons = sell_reasons(s, old)
        state = 'SELL' if old and reasons else 'BUY' if s['buy_eligible'] and not reasons else 'HOLD' if old else None
        s.update(state=state, state_reasons=reasons if state == 'SELL' else hold_reasons(s) if state == 'HOLD' else [])
        s['valuation'] = valuation(s)
        if not state:
            continue
        day = generated[:10]
        if old and state == 'SELL':
            # 같은 날 재실행은 1회로 센다. 매도로 바뀐 날이 1회째.
            if old.get('sell_last_day') != day:
                old['sell_days'] = old.get('sell_days', 0) + 1 if old.get('state') == 'SELL' else 1
                old['sell_last_day'] = day
            if old['sell_days'] > SELL_KEEP_DAYS:
                del entries[key]
                s.update(state=None, state_reasons=[], valuation=None)
                continue
        elif old:
            old.pop('sell_days', None)
            old.pop('sell_last_day', None)
        if not old:
            old = {'first_entry_date': day, 'entry_price': s['close'], 'state': None,
                   'highest_price': s['close'], 'entry_turnaround': loss_turnaround(s),
                   'entry_op_loss': valid(s.get('op_ttm_value')) and s['op_ttm_value'] < 0,
                   'entry_metrics': copy.deepcopy(s['metrics']), 'transitions': []}
            entries[key] = old
        if old['state'] == 'SELL' and state == 'BUY':
            old.update(cycle_entry_date=day, cycle_entry_price=s['close'],
                       entry_turnaround=loss_turnaround(s), entry_op_loss=valid(s.get('op_ttm_value')) and s['op_ttm_value'] < 0,
                       entry_metrics=copy.deepcopy(s['metrics']))
        if old['state'] != state:
            old['transitions'].append({'date': day, 'from': old['state'], 'to': state, 'price': s.get('close'), 'reasons': s['state_reasons']})
            old['previous_state'] = old['state']
        old.update(state=state, reasons=s['state_reasons'], last_seen=generated,
                   highest_price=max(old['highest_price'], s['close']) if positive(s.get('close')) else old['highest_price'])
        s['history'] = {k: v for k, v in old.items() if k != 'snapshot'}
        old['snapshot'] = copy.deepcopy({k: v for k, v in s.items() if k != 'history'})
    # 상장 목록에서 빠진 종목(상장폐지)은 지운다. 상장 목록이 비정상적으로 작으면(수집 실패) 지우지 않는다.
    listed_ok = listed is not None and len(listed) >= 500
    for key in [k for k in entries if k.startswith(market + ':') and k not in seen]:
        if listed_ok and key.split(':', 1)[1] not in listed:
            del entries[key]
    # 일시적인 수집 실패는 매도/삭제로 간주하지 않고 마지막 검증된 상태를 유지한다.
    for key, old in entries.items():
        if key.startswith(market + ':') and key not in seen and old.get('snapshot'):
            s = copy.deepcopy(old['snapshot'])
            s['valuation'] = valuation(s)
            s['valuation_insufficient'] = valuation_insufficient(s)
            s['data_missing'] = True
            s['history'] = {k: v for k, v in old.items() if k != 'snapshot'}
            stocks.append(s)
    return history


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':'), allow_nan=False), encoding='utf-8')
    os.replace(tmp, path)


def persist_cards(stocks, market, generated, history_path='docs/card_history.json', listed=None):
    path = Path(history_path)
    text = path.read_text(encoding='utf-8') if path.exists() else ''
    history = json.loads(text) if text.strip() else {'version': 1, 'tickers': {}}
    update_history(stocks, history, market, generated, listed)
    write_json(path, history)
