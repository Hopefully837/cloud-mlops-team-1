#!/usr/bin/env python3
"""팀원이 각자 실행해 서비스에 예측·피드백 로그를 쌓는 도구 (재학습용 로그가 부족할 때).

원본 자료의 '검증용' 행(모델이 학습하지 않았고, 평가용도 아닌 행)을 골라 서비스에 보낸다.
  1) POST /predict 로 그 시간의 날씨를 보내 예측을 받고
  2) POST /feedback 으로 원본의 실제 대여 수를 돌려보낸다 (실제 서비스에서 나중에 실제값이 들어오는 상황을 흉내 낸다).
요청의 input에 client 필드(replay-<이름>)를 함께 보내 로그에서 시뮬레이션과 사람이 직접 입력한 요청을 구분한다.

주의: 이것은 시뮬레이션이다. 실제 이용자의 피드백이 아니다. 평가용(test) 행은 절대 보내지 않는다.

  python src/replay_feedback.py --url http://<서버>:8000 --who 김형준 --n 100
팀원마다 --seed를 다르게 주면 서로 다른 행을 보낸다 (같은 행이 겹쳐도 전처리가 중복을 걸러 낸다).
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from splits import TARGET, load, split  # noqa: E402


def http_json(base_url, method, path, body=None):
    data = None if body is None else json.dumps(body).encode('utf-8')
    request = urllib.request.Request(base_url + path, data=data, method=method, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except ValueError:
            return e.code, None


def to_payload(row, client):
    date = f'{int(row["Date"][6:10]):04d}-{row["Date"][3:5]}-{row["Date"][0:2]}'  # dd/mm/yyyy -> YYYY-MM-DD
    return {'date': date, 'hour': int(row['Hour']), 'temperature': float(row['Temperature(°C)']),
            'humidity': float(row['Humidity(%)']), 'rainfall': float(row['Rainfall(mm)']),
            'holiday': bool(row['Holiday']), 'client': client}


def replay(rows, send, client, pause=0.0, log=print):
    """rows(DataFrame)를 send(method, path, body)로 보낸다. 결과 요약을 돌려준다."""
    ok, failed, abs_errors = 0, 0, []
    for _, row in rows.iterrows():
        status, body = send('POST', '/predict', to_payload(row, client))
        if status != 200 or not body or 'request_id' not in body:
            failed += 1
            log(f'예측 실패 {status}: {body}')
            continue
        status, fb = send('POST', '/feedback', {'request_id': body['request_id'], 'actual_rentals': int(row[TARGET])})
        if status != 200 or not fb or fb.get('matched') is not True:
            failed += 1
            log(f'피드백 실패 {status}: {fb}')
            continue
        ok += 1
        abs_errors.append(abs(body['predicted_rentals'] - int(row[TARGET])))
        if pause:
            time.sleep(pause)
    return {'sent': ok, 'failed': failed, 'mae': round(sum(abs_errors) / len(abs_errors), 2) if abs_errors else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--url', required=True, help='예: http://3.35.0.1:8000')
    parser.add_argument('--who', required=True, help='실행하는 사람 이름 (로그의 client 필드에 들어간다)')
    parser.add_argument('--n', type=int, default=100, help='보낼 행 수 (검증용 1,690행 중에서)')
    parser.add_argument('--seed', type=int, default=0, help='팀원마다 다르게 주면 서로 다른 행을 보낸다')
    parser.add_argument('--source', default='data/processed/bike-clean.csv')
    parser.add_argument('--pause', type=float, default=0.05)
    args = parser.parse_args()

    status, health = http_json(args.url, 'GET', '/health')
    if status != 200:
        sys.exit(f'서버가 응답하지 않습니다: {status} {health}')
    print(f'서버 확인: build_revision={health.get("build_revision")} model_version={health.get("model_version")}')

    _train, val, _test = split(load(args.source))  # 평가용(test)은 쓰지 않는다
    rows = val.sample(n=min(args.n, len(val)), random_state=args.seed)
    result = replay(rows, lambda m, p, b=None: http_json(args.url, m, p, b), f'replay-{args.who}', args.pause)
    print(json.dumps({'who': args.who, 'seed': args.seed, **result}, ensure_ascii=False))
    if result['failed']:
        sys.exit(1)


if __name__ == '__main__':
    main()
