#!/usr/bin/env python3
"""실행 중인 API에 정상·오류 요청을 보내고 응답을 JSON 파일로 저장한다."""
import argparse
import json
import urllib.error
import urllib.request
from pathlib import Path

REQUEST_ID = '<정상 입력의 request_id>'
NORMAL = {'date': '2018-06-15', 'hour': 18, 'temperature': 25.0, 'humidity': 50, 'rainfall': 0.0, 'holiday': False}
CASES = {
    'normal': [
        ('health', 'GET', '/health', None),
        ('정상 입력', 'POST', '/predict', NORMAL),
        ('시간 25', 'POST', '/predict', {**NORMAL, 'hour': 25}),
        ('습도에 문자', 'POST', '/predict', {**NORMAL, 'humidity': '많음'}),
        ('강수량 누락', 'POST', '/predict', {k: v for k, v in NORMAL.items() if k != 'rainfall'}),
        # REQUEST_ID 자리에는 바로 앞 '정상 입력' 응답의 request_id가 들어간다
        ('피드백 정상', 'POST', '/feedback', {'request_id': REQUEST_ID, 'actual_rentals': 3000}),
        ('피드백 모르는 id', 'POST', '/feedback', {'request_id': 'unknown-id', 'actual_rentals': 3000}),
        ('피드백 음수', 'POST', '/feedback', {'request_id': REQUEST_ID, 'actual_rentals': -5}),
        ('피드백 id 누락', 'POST', '/feedback', {'actual_rentals': 3000}),
        # NaN은 비교 연산이 모두 False라 범위 검사를 통과할 수 있어 따로 확인한다
        # (앞의 피드백이 끝난 뒤에 두어 정상 입력의 request_id를 덮어쓰지 않게 한다)
        ('기온 NaN', 'POST', '/predict', {**NORMAL, 'temperature': float('nan')}),
        ('시간 NaN', 'POST', '/predict', {**NORMAL, 'hour': float('nan')}),
    ],
    'model-missing': [  # 서버를 없는 모델 경로로 켠 상태에서 실행한다
        ('health', 'GET', '/health', None),
        ('정상 입력', 'POST', '/predict', NORMAL),
    ],
}


def send(base_url, method, path, body):
    data = None if body is None else json.dumps(body).encode('utf-8')
    request = urllib.request.Request(base_url + path, data=data, method=method,
                                     headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, parse_body(response.read())
    except urllib.error.HTTPError as e:  # 4xx·5xx도 응답 본문을 그대로 기록한다
        return e.code, parse_body(e.read())


def parse_body(raw):
    """JSON이면 파싱하고, 500의 'Internal Server Error'처럼 JSON이 아니면 문자열 그대로 돌려준다."""
    try:
        return json.loads(raw)
    except ValueError:
        return raw.decode('utf-8', errors='replace')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8000')
    parser.add_argument('--cases', choices=list(CASES), default='normal')
    parser.add_argument('--out', default='results/api-check.json')
    args = parser.parse_args()

    results = []
    request_id = None
    for name, method, path, body in CASES[args.cases]:
        if body and body.get('request_id') == REQUEST_ID:
            body = {**body, 'request_id': request_id}
        status, response = send(args.url, method, path, body)
        if path == '/predict' and status == 200:
            request_id = response['request_id']
        results.append({'name': name, 'request': {'method': method, 'path': path, 'body': body},
                        'status': status, 'response': response})
        print(f'{status}  {method} {path}  ({name})')

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'saved: {out}')


if __name__ == '__main__':
    main()
