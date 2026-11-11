#!/usr/bin/env python3
"""서비스 로그(예측·피드백 JSON 줄)를 재학습용 자료로 바꾸고, 로그가 어떤 자료인지 분석한다.

입력: CloudWatch에서 받은 로그 파일(한 줄에 JSON 하나) 또는 서버 콘솔 로그. JSON이 아닌 줄(uvicorn 접근 로그 등)은 건너뛴다.
출력:
  - 재학습용 자료(학습 데이터와 같은 열)        --out-clean
  - 제외한 행과 이유                             --out-quarantine
  - 로그 분석 보고서                             --report

연결 방법: prediction 로그(입력·예측)와 feedback 로그(실제 대여 수)를 request_id로 이어 붙인다.
재학습에 쓰려면 다음을 모두 통과해야 한다. 하나라도 걸리면 이유를 적어 quarantine으로 보낸다.
  1. 예측 기록이 있어 입력을 복원할 수 있다 (feedback만 있으면 입력을 모른다)
  2. 같은 request_id·같은 시간 칸에 실제값이 서로 다르게 들어오지 않았다
  3. 실제값이 정수이고 학습 자료의 범위(0 ~ 최대의 1.5배) 안에 있다
  4. 평가용으로 떼어 둔 시간 칸과 겹치지 않는다 (겹치면 재학습 전후 비교가 무너진다)
  5. 이미 학습에 쓴 시간 칸이 아니다 (같은 자료를 한 번 더 넣으면 가중치만 커진다)
  6. 검증용 시간 칸이면 원본의 실제값과 같다 (다르면 입력 실수로 본다)
"""
import argparse
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from splits import TARGET, load, slot_key, slot_keys, split  # noqa: E402

CLEAN_COLUMNS = ['Date', TARGET, 'Hour', 'Temperature(°C)', 'Humidity(%)', 'Rainfall(mm)', 'Holiday']
HOUR_BLOCKS = [('새벽 0~5시', 0, 5), ('오전 6~11시', 6, 11), ('오후 12~17시', 12, 17), ('저녁 18~23시', 18, 23)]


def parse_lines(paths):
    """JSON 줄을 모은다. 줄 안의 첫 '{'부터 읽어, 앞에 시각 같은 접두어가 붙어도 처리한다."""
    events, skipped, total = [], 0, 0
    for path in paths:
        for line in Path(path).read_text(encoding='utf-8', errors='replace').splitlines():
            line = line.strip()
            if not line:
                continue
            total += 1
            start = line.find('{')
            if start < 0:
                skipped += 1
                continue
            try:
                record = json.loads(line[start:])
            except ValueError:
                skipped += 1
                continue
            if isinstance(record, dict) and 'event' in record:
                events.append(record)
            else:
                skipped += 1
    return events, total, skipped


def hour_block(hour):
    for name, low, high in HOUR_BLOCKS:
        if low <= hour <= high:
            return name
    return '?'


def day_type(row):
    if row['holiday']:
        return '공휴일'
    return '주말' if pd.Timestamp(row['date']).dayofweek >= 5 else '평일'


def join_events(events):
    """request_id별로 예측 기록과 피드백 기록을 묶는다."""
    predictions, feedbacks = {}, defaultdict(list)
    counts = Counter(e['event'] for e in events)
    for e in events:
        rid = e.get('request_id')
        if e['event'] == 'prediction' and rid:
            predictions[rid] = e
        elif e['event'] == 'feedback' and rid:
            feedbacks[rid].append(e)
    return predictions, feedbacks, counts


def build_rows(predictions, feedbacks):
    """피드백이 있는 요청마다 한 행. 입력은 예측 기록의 input에서 복원한다."""
    rows, quarantine = [], []
    for rid, fbs in feedbacks.items():
        actuals = {f.get('actual_rentals') for f in fbs}
        pred = predictions.get(rid)
        base = {'request_id': rid}
        if pred is None:
            quarantine.append({**base, 'reason': '예측 기록 없음(입력을 알 수 없음)', 'actual': sorted(map(str, actuals))[0]})
            continue
        if len(actuals) > 1:
            quarantine.append({**base, 'reason': '같은 요청에 실제값이 다르게 여러 번 들어옴', 'actual': '/'.join(sorted(map(str, actuals)))})
            continue
        inp = pred.get('input') or {}
        try:
            rows.append({
                'request_id': rid, 'ts': pred.get('ts'), 'model_version': pred.get('model_version'),
                'date': inp['date'], 'hour': int(inp['hour']), 'temperature': float(inp['temperature']),
                'humidity': float(inp['humidity']), 'rainfall': float(inp['rainfall']), 'holiday': bool(inp['holiday']),
                'actual': fbs[-1].get('actual_rentals'),
                'predicted': (pred.get('action') or {}).get('predicted_rentals'),
                'latency_ms': pred.get('latency_ms'), 'client': inp.get('client', ''),
            })
        except (KeyError, TypeError, ValueError):
            quarantine.append({**base, 'reason': '예측 기록의 입력을 읽을 수 없음', 'actual': str(fbs[-1].get('actual_rentals'))})
    return rows, quarantine


def classify(rows, source_df):
    """재학습에 쓸 수 있는 행과 제외할 행을 나눈다."""
    train, val, test = split(source_df)
    keys = {'train': set(slot_keys(train)), 'val': dict(zip(slot_keys(val), val[TARGET])), 'test': set(slot_keys(test))}
    max_train = int(train[TARGET].max())

    by_slot = defaultdict(set)
    for r in rows:
        by_slot[slot_key(r['date'], r['hour'])].add(r['actual'])

    clean, quarantine, seen = [], [], set()
    for r in sorted(rows, key=lambda x: x['ts'] or ''):
        slot = slot_key(r['date'], r['hour'])
        actual = r['actual']
        reason, kind = None, None
        if isinstance(actual, bool) or not isinstance(actual, int) or not 0 <= actual <= int(max_train * 1.5):
            reason = f'실제값이 정수가 아니거나 범위 밖(0~{int(max_train * 1.5)})'
        elif len(by_slot[slot]) > 1:
            reason = '같은 시간 칸에 실제값이 서로 다르게 들어옴'
        elif slot in keys['test']:
            reason = '평가용으로 떼어 둔 시간 칸과 겹침'
        elif slot in keys['train']:
            reason = '이미 학습에 쓴 시간 칸'
        elif slot in keys['val'] and keys['val'][slot] != actual:
            reason = f'원본 실제값({keys["val"][slot]})과 다름'
        elif slot in seen:
            reason = '로그 안에서 같은 시간 칸·같은 실제값이 중복'
        else:
            kind = '검증용 시간 칸(원본과 일치)' if slot in keys['val'] else '새로운 시간 칸(원본에 없음)'
        if reason:
            quarantine.append({'request_id': r['request_id'], 'reason': reason, 'actual': str(actual),
                               'date': r['date'], 'hour': r['hour']})
        else:
            seen.add(slot)
            clean.append({**r, 'kind': kind})
    return clean, quarantine


def to_training_frame(clean):
    rows = [{
        'Date': pd.Timestamp(r['date']).strftime('%d/%m/%Y'), TARGET: r['actual'], 'Hour': r['hour'],
        'Temperature(°C)': r['temperature'], 'Humidity(%)': r['humidity'], 'Rainfall(mm)': r['rainfall'],
        'Holiday': 'Holiday' if r['holiday'] else 'No Holiday',
    } for r in clean]
    return pd.DataFrame(rows, columns=CLEAN_COLUMNS)


def served_quality(rows):
    """운영 중이던 모델의 예측이 실제값과 얼마나 달랐는지(피드백이 있는 모든 요청 기준)."""
    labeled = [r for r in rows if isinstance(r['actual'], int) and isinstance(r['predicted'], (int, float))]
    if not labeled:
        return {'rows': 0}

    def stats(items):
        errs = [r['predicted'] - r['actual'] for r in items]
        return {'rows': len(items), 'mae': round(statistics.mean(abs(e) for e in errs), 2),
                'bias(예측-실제 평균)': round(statistics.mean(errs), 2)}

    groups = {'전체': labeled}
    for name, low, high in HOUR_BLOCKS:
        groups[name] = [r for r in labeled if low <= r['hour'] <= high]
    for name in ('평일', '주말', '공휴일'):
        groups[name] = [r for r in labeled if day_type(r) == name]
    groups['비 옴(강수 > 0)'] = [r for r in labeled if r['rainfall'] > 0]
    groups['비 안 옴'] = [r for r in labeled if r['rainfall'] == 0]
    return {k: stats(v) for k, v in groups.items() if v}


def input_profile(rows, source_df):
    """로그의 입력이 학습 자료와 얼마나 다른지(대표성 확인)."""
    if not rows:
        return {}
    train, _, _ = split(source_df)
    n = len(rows)
    hours = Counter(hour_block(r['hour']) for r in rows)
    temps = [r['temperature'] for r in rows]
    return {
        '시간대 비율(로그)': {name: round(hours[name] / n, 3) for name, _, _ in HOUR_BLOCKS},
        '시간대 비율(학습 자료)': {name: round(float(((train['Hour'] >= low) & (train['Hour'] <= high)).mean()), 3)
                          for name, low, high in HOUR_BLOCKS},
        '기온 평균(로그)': round(statistics.mean(temps), 1),
        '기온 평균(학습 자료)': round(float(train['Temperature(°C)'].mean()), 1),
        '비 온 시간 비율(로그)': round(sum(r['rainfall'] > 0 for r in rows) / n, 3),
        '비 온 시간 비율(학습 자료)': round(float((train['Rainfall(mm)'] > 0).mean()), 3),
        '공휴일 비율(로그)': round(sum(r['holiday'] for r in rows) / n, 3),
        '공휴일 비율(학습 자료)': round(float(train['Holiday'].mean()), 3),
        '서로 다른 날짜 수': len({r['date'] for r in rows}),
        '요청을 보낸 클라이언트': dict(Counter(r['client'] or '(구분 없음)' for r in rows)),
    }


def percentile(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, math.ceil(q * len(values)) - 1)] if values else None


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('logs', nargs='+', help='로그 파일(한 줄에 JSON 하나). 여러 개를 줄 수 있다')
    parser.add_argument('--source', default='data/processed/bike-clean.csv', help='원본 학습 자료')
    parser.add_argument('--out-clean', default='data/processed/log-training.csv')
    parser.add_argument('--out-quarantine', default='data/processed/log-quarantine.csv')
    parser.add_argument('--report', default='results/log-analysis.json')
    args = parser.parse_args()

    source_df = load(args.source)
    events, total_lines, skipped = parse_lines(args.logs)
    predictions, feedbacks, counts = join_events(events)
    rows, join_quarantine = build_rows(predictions, feedbacks)
    clean, quarantine = classify(rows, source_df)
    quarantine = join_quarantine + quarantine

    for path in (args.out_clean, args.out_quarantine, args.report):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    to_training_frame(clean).to_csv(args.out_clean, index=False, encoding='utf-8')
    pd.DataFrame(quarantine, columns=['request_id', 'reason', 'actual', 'date', 'hour']).to_csv(
        args.out_quarantine, index=False, encoding='utf-8')

    matched = sum(1 for fbs in feedbacks.values() for f in fbs if f.get('matched') is True)
    latencies = [p['latency_ms'] for p in predictions.values() if isinstance(p.get('latency_ms'), (int, float))]
    report = {
        '입력 로그': {'파일': [str(p) for p in args.logs], '읽은 줄': total_lines, '건너뛴 줄(JSON 아님)': skipped,
                  '이벤트 종류별 건수': dict(counts)},
        '연결 결과': {
            '예측 기록(request_id 기준)': len(predictions),
            '피드백이 있는 요청': len(feedbacks),
            '피드백이 없는 예측(정답 없음, 학습에 못 씀)': len(predictions) - sum(1 for rid in feedbacks if rid in predictions),
            '피드백 중 서버가 바로 연결한 것(matched=true)': matched,
            '입력 오류(invalid_input)': counts.get('invalid_input', 0),
            '모델 버전별 예측 수': dict(Counter(p.get('model_version') for p in predictions.values())),
        },
        '재학습 후보': {
            '쓸 수 있는 행(clean)': len(clean),
            '종류': dict(Counter(r['kind'] for r in clean)),
            '제외한 행(quarantine)': len(quarantine),
            '제외 이유': dict(Counter(q['reason'] for q in quarantine)),
        },
        '운영 모델의 오차(피드백이 있는 요청 전체)': served_quality(rows),
        '입력 분포(피드백이 있는 요청 전체)': input_profile(rows, source_df),
        '응답 시간(ms)': {'p50': percentile(latencies, 0.5), 'p95': percentile(latencies, 0.95), '건수': len(latencies)},
        '주의': ['피드백의 실제값은 사람이 입력한 값이라 검증되지 않았다. 팀 테스트로 만든 로그는 실제 이용자 피드백이 아니다.',
               'client 필드(replay-*)가 있는 행은 도구가 원본 자료의 검증용 행을 되풀이해서 보낸 시뮬레이션이다.'],
    }
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
