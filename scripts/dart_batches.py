"""DART 분기별 묶음 조회: 제한된 병렬 요청과 재시작 가능한 원자적 캐시."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path

from lib_kr import parse_multi_acnt


def fetch_batches(codes, year, quarter, report_code, today, cache, request, workers=8):
    folder = Path(cache) / 'fin2_batches' / f'{year}Q{quarter}'
    folder.mkdir(parents=True, exist_ok=True)
    codes = sorted(set(codes))

    def fetch(batch):
        joined = ','.join(batch)
        path = folder / (hashlib.sha256(joined.encode()).hexdigest() + '.json')
        if path.exists():
            saved = json.loads(path.read_text(encoding='utf-8'))
            # 최근 2개 연도는 날짜가 바뀌면 다시 받아 정정공시를 반영한다.
            if year < today.year - 1 or saved['date'] == today.isoformat():
                return saved['values']
        response = request('fnlttMultiAcnt.json', corp_code=joined,
                           bsns_year=year, reprt_code=report_code)
        status = response.get('status')
        if status not in ('000', '013'):
            raise RuntimeError(f'DART {year}Q{quarter} 오류 {status}: '
                               f'{response.get("message", "응답 확인 필요")}')
        values = parse_multi_acnt(response.get('list', []), quarter)
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps({'date': today.isoformat(), 'values': values},
                                   ensure_ascii=False), encoding='utf-8')
        temp.replace(path)
        return values

    values = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(fetch, codes[i:i + 100])
                   for i in range(0, len(codes), 100)]
        try:
            for future in as_completed(futures):
                values.update(future.result())
        except Exception:
            for future in futures:
                future.cancel()
            raise
    return values
