#!/usr/bin/env python3
"""src/check_api.py가 저장한 JSON을 읽어 기대한 상태 코드와 비교한다. 하나라도 다르면 종료 코드 1."""
import argparse
import json
import sys

# 케이스 이름: 기대 상태 코드. NaN 케이스는 check_api.py에 있을 때만 확인한다
EXPECTED = {
    'health': 200,
    '정상 입력': 200,
    '시간 25': 422,
    '습도에 문자': 422,
    '강수량 누락': 422,
    '피드백 정상': 200,
    '피드백 모르는 id': 200,
    '피드백 음수': 422,
    '피드백 id 누락': 422,
    '기온 NaN': 422,
    '시간 NaN': 422,
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('result', help='check_api.py --out 으로 저장한 JSON')
    parser.add_argument('--revision', help='/health 응답의 build_revision이어야 하는 값')
    args = parser.parse_args()

    results = {r['name']: r for r in json.load(open(args.result, encoding='utf-8'))}
    failures = []
    for name, expected in EXPECTED.items():
        if name not in results:
            if name in ('health', '정상 입력', '피드백 정상'):
                failures.append(f'{name}: 결과에 없음')
            continue
        status = results[name]['status']
        print(f'{"OK  " if status == expected else "FAIL"} {name}: {status} (기대 {expected})')
        if status != expected:
            failures.append(f'{name}: {status} != {expected}')

    if results.get('피드백 정상', {}).get('response', {}).get('matched') is not True:
        failures.append('피드백 정상: matched가 true가 아님 (request_id 연결 실패)')
    if args.revision:
        actual = results.get('health', {}).get('response', {}).get('build_revision')
        print(f'{"OK  " if actual == args.revision else "FAIL"} build_revision: {actual} (기대 {args.revision})')
        if actual != args.revision:
            failures.append(f'build_revision: {actual} != {args.revision}')

    if failures:
        print('\n실패:', *failures, sep='\n- ', file=sys.stderr)
        sys.exit(1)
    print('\n모든 케이스가 기대와 같습니다.')


if __name__ == '__main__':
    main()
