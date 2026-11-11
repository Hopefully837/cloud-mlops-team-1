#!/usr/bin/env python3
"""CloudWatch Logs의 로그 그룹에서 이벤트 메시지를 받아 한 줄에 JSON 하나인 파일로 저장한다 (AWS CloudShell에서 실행).

  python3 scripts/fetch_cloudwatch_logs.py --hours 168 --out /tmp/bike-logs.jsonl
  python3 scripts/fetch_cloudwatch_logs.py --prefix bike-api- --out /tmp/bike-logs.jsonl   # 스트림 이름 접두어로 좁히기

저장한 파일은 src/logs_to_training.py 의 입력이다. boto3는 CloudShell에 기본으로 들어 있다.
읽기만 하며 로그를 바꾸거나 지우지 않는다.
"""
import argparse
import json
import time

import boto3


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--log-group', default='/mlops/team1-bike-rental')
    parser.add_argument('--region', default='ap-northeast-2')
    parser.add_argument('--hours', type=float, default=168, help='최근 몇 시간의 로그를 받을지 (기본 7일)')
    parser.add_argument('--prefix', default='', help='로그 스트림 이름 접두어 (비우면 그룹 전체)')
    parser.add_argument('--out', required=True)
    args = parser.parse_args()

    client = boto3.client('logs', region_name=args.region)
    kwargs = {'logGroupName': args.log_group, 'startTime': int((time.time() - args.hours * 3600) * 1000)}
    if args.prefix:
        kwargs['logStreamNamePrefix'] = args.prefix

    written, streams, kinds = 0, set(), {}
    with open(args.out, 'w', encoding='utf-8') as out:
        for page in client.get_paginator('filter_log_events').paginate(**kwargs):
            for event in page['events']:
                message = event['message'].strip()
                out.write(message + '\n')
                written += 1
                streams.add(event['logStreamName'])
                try:
                    kind = json.loads(message).get('event', '?')
                except ValueError:
                    kind = '(JSON 아님)'
                kinds[kind] = kinds.get(kind, 0) + 1
    print(f'저장: {args.out}  이벤트 {written}건, 스트림 {len(streams)}개')
    print('종류별:', json.dumps(kinds, ensure_ascii=False))


if __name__ == '__main__':
    main()
