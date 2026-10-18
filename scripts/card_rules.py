"""매매 상태·가치평가·목표가의 단일 기준. 네트워크/화면에 의존하지 않는다."""
import copy
import json
import math
import os
from pathlib import Path

SCHEMA_VERSION = 2


def valid(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def positive(value):
    return valid(value) and value > 0


def calc_card_metrics(close, ma120, ma200, per, med_per_5y, pbr, is_finance, mcap_b):
    if not all(positive(x) for x in (close, ma120, ma200)):
        return None, None, None
    # 시총/환율 누락은 일반주로 단정하지 않고 최대 위험폭을 보수적으로 사용한다.
    max_stop = -10.0 if (not valid(mcap_b) or mcap_b <= .3 or not positive(per)) else -7.0
    if close >= ma120 and close >= ma200:
        high_ma, low_ma = max(ma120, ma200), min(ma120, ma200)
        support = (high_ma if (close - high_ma) / close >= .03 else low_ma) * .98
    else:
        support = (ma120 if close >= ma120 else ma200) * .98
    risk = max(max_stop, min(-5.0, (support / close - 1) * 100))
    r = abs(risk)
    target1 = max((ma200 / close - 1) * 100, r * 2) if close < ma200 else r * 2
    val = (med_per_5y / per - 1) * 100 if positive(per) and positive(med_per_5y) and med_per_5y > per else 0
    if is_finance and positive(pbr) and pbr < 1:
        val = max(val, (1 / pbr - 1) * 100)
    target2 = max(val, r * 3, target1 + 7)
    return round(risk, 1), round(target1, 1), round(target2, 1)


def obv_signal(w):
    """52개 주간 변화가 있는 경우에만 기존 정규화 OBV 판정을 적용한다."""
    if not w or len(w.get('c', [])) < 53 or len(w.get('v', [])) < 53:
        return None
    c, v = w['c'][-53:], w['v'][-53:]
    if not all(positive(x) for x in c) or not all(valid(x) and x >= 0 for x in v):
        return None
    changes = [(1 if c[i] > c[i-1] else -1 if c[i] < c[i-1] else 0) * v[i] for i in range(1, 53)]
    sum_r = sum(abs(c[i] / c[i-1] - 1) for i in range(1, 53))
    sum_d = sum(abs(x) for x in changes)
    if not sum_r or not sum_d:
        return '중립'
    pz, oz = (c[-1] / c[0] - 1) / (sum_r / 52), sum(changes) / (sum_d / 52)
    if (pz < -1 and oz < pz) or (oz < -1 and pz >= -1):
        return '이탈'
    return '매집' if oz > 1 and pz <= 1 else '중립'


def loss_turnaround(s):
    # PER 미산출만으로 적자로 오판하지 않는다(주식 수 누락도 PER 미산출을 유발).
    return any(valid(s.get(k)) and s[k] < 0 for k in ('ni_ttm', 'op_ttm_value')) and bool(s.get('rev_q') and s.get('rev_ttm'))


def buy_eligible(s):
    c, a, b = (s.get(k) for k in ('close', 'ma120', 'ma200'))
    if not all(positive(x) for x in (c, a, b)) or not s.get('record') or s.get('stale'):
        return False
    if not ((c >= a or c >= b) and s.get('vol_down')) or s.get('obv_signal') == '이탈':
        return False
    if s.get('fin'):
        return positive(s.get('pbr')) and s['pbr'] <= 1 and valid(s.get('roe3')) and s['roe3'] >= 10
    if loss_turnaround(s):
        return True
    return (valid(s.get('roe3')) and s['roe3'] >= 15 and valid(s.get('debt')) and s['debt'] <= 50
            and s.get('profit_years', 0) >= 5 and positive(s.get('per')) and positive(s.get('per_med5'))
            and s['per'] < s['per_med5'])


def peak_out(s):
    keys = ('rev_ttm_value', 'prev_rev_ttm', 'op_ttm_value', 'prev_op_ttm')
    return all(valid(s.get(k)) for k in keys) and s[keys[0]] < s[keys[1]] and s[keys[2]] < s[keys[3]]


def sell_reasons(s, entry=None):
    reasons = []
    c, a, b = (s.get(k) for k in ('close', 'ma120', 'ma200'))
    if all(positive(x) for x in (c, a, b)) and c < a * .98 and c < b * .98:
        reasons.append('추세이탈')
    if s.get('obv_signal') == '이탈':
        reasons.append('수급악화')
    v20, v60, ma20, change = (s.get(k) for k in ('vol20', 'vol60', 'ma20', 'change_pct'))
    if all(valid(x) for x in (v20, v60, c, ma20, change)) and v20 > v60 and c < ma20 and change <= -7:
        reasons.append('패닉셀')
    if peak_out(s):
        reasons.append('피크아웃')
    roe, debt, op = (s.get(k) for k in ('roe3', 'debt', 'latest_op'))
    original_loss = bool((entry or {}).get('entry_op_loss'))
    turnaround = bool((entry or {}).get('entry_turnaround')) or loss_turnaround(s)
    if s.get('fin') or turnaround:
        bad = (s.get('fin') and valid(roe) and roe < 8) or (original_loss and s.get('rev_q') is False)
    else:
        bad = (valid(op) and op < 0) or (valid(roe) and roe < 12) or (valid(debt) and debt > 70)
    if bad:
        reasons.append('적자전환')
    if s.get('stale'):
        reasons.append('공시지연')
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
    if (s.get('rev_q') is False or s.get('op_q') is False) and not peak_out(s):
        reasons.append('실적유지')
    roe, debt = s.get('roe3'), s.get('debt')
    if ((s.get('fin') and valid(roe) and 8 <= roe < 10)
        or (not s.get('fin') and ((valid(roe) and 12 <= roe < 15) or (valid(debt) and 50 < debt <= 70)))):
        reasons.append('재무보합')
    return reasons or ['매수조건 미충족']


def valuation(s):
    # 별도 프리미엄/허용오차 명세가 없어 기준값 자체를 경계로 사용한다.
    value, base = (s.get('pbr'), 1) if s.get('fin') else (s.get('per'), s.get('per_med5'))
    if not positive(value) or not positive(base):
        return None
    return 'LOW' if value < base else 'HIGH' if value > base else 'FAIR'


def enrich(s):
    c, a, b = (s.get(k) for k in ('close', 'ma120', 'ma200'))
    s['obv_signal'] = obv_signal(s.get('wk'))
    s['trend'] = None
    if loss_turnaround(s) or (all(positive(x) for x in (c, a, b)) and c >= a and c < b):
        s['trend'] = '턴어라운드'
    elif all(positive(x) for x in (c, a, b)) and c >= a and c >= b and positive(s.get('per')) and not loss_turnaround(s):
        s['trend'] = '상승추세'
    for key in ('ma120', 'ma200'):
        s['dist_' + key] = round((c / s[key] - 1) * 100, 1) if positive(c) and positive(s.get(key)) else None
    risk, t1, t2 = calc_card_metrics(c, a, b, s.get('per'), s.get('per_med5'), s.get('pbr'), s.get('fin'), s.get('mcap_b'))
    s['metrics'] = {'expected_loss': risk, 'target_1': t1, 'target_2': t2,
                    'stop_price': round(c * (1 + risk / 100), 2) if risk is not None else None,
                    'target_1_price': round(c * (1 + t1 / 100), 2) if t1 is not None else None,
                    'target_2_price': round(c * (1 + t2 / 100), 2) if t2 is not None else None}
    s['valuation'] = valuation(s)
    s['buy_eligible'] = buy_eligible(s)


def update_history(stocks, history, market, generated):
    """시장별 티커 키, 매도 우선, 최초 BUY 이력 없는 SELL 금지. 동일 시세 재실행 멱등."""
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
        # SELL은 신규 편입 조건을 다시 충족하기 전까지 유지한다.
        if old and old['state'] == 'SELL' and state == 'HOLD':
            state, reasons = 'SELL', old.get('reasons', [])
        s.update(state=state, state_reasons=reasons if state == 'SELL' else hold_reasons(s) if state == 'HOLD' else [])
        if not state:
            continue
        day = generated[:10]
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
    # 일시적인 수집 실패는 매도/삭제로 간주하지 않고 마지막 검증된 상태를 유지한다.
    for key, old in entries.items():
        if key.startswith(market + ':') and key not in seen and old.get('snapshot'):
            s = copy.deepcopy(old['snapshot'])
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


def persist_cards(stocks, market, generated, history_path='docs/card_history.json'):
    path = Path(history_path)
    history = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'version': 1, 'tickers': {}}
    update_history(stocks, history, market, generated)
    write_json(path, history)
