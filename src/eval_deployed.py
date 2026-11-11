#!/usr/bin/env python3
"""배포된 서비스를 고정한 평가용 행으로 측정한다. 재학습 전·후 배포본을 같은 행으로 비교하는 데 쓴다.

평가용(test) 행에서 고정 시드로 n개를 골라 POST /predict 로 보내고 실제 대여 수와 비교한다.
피드백은 보내지 않는다(측정 요청이 재학습용 로그에 섞이지 않도록). 결과는 JSON으로 저장한다.

  python src/eval_deployed.py --url http://<서버>:8000 --label before --out results/deployed-eval-before.json
  (재학습·재배포 뒤) --label after --out results/deployed-eval-after.json
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from replay_feedback import http_json, to_payload  # noqa: E402
from splits import TARGET, load, split  # noqa: E402

DEMO = {'date': '2018-06-15', 'hour': 18, 'temperature': 25.0, 'humidity': 50, 'rainfall': 0.0, 'holiday': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--url', required=True)
    parser.add_argument('--label', required=True, help='before 또는 after')
    parser.add_argument('--n', type=int, default=500)
    parser.add_argument('--seed', type=int, default=7, help='전·후가 같은 행을 쓰도록 같은 값을 쓴다')
    parser.add_argument('--source', default='data/processed/bike-clean.csv')
    parser.add_argument('--out', required=True)
    args = parser.parse_args()

    status, health = http_json(args.url, 'GET', '/health')
    if status != 200:
        sys.exit(f'서버가 응답하지 않습니다: {status} {health}')
    _train, _val, test = split(load(args.source))
    rows = test.sample(n=min(args.n, len(test)), random_state=args.seed)

    started = time.time()
    results = []
    for _, row in rows.iterrows():
        status, body = http_json(args.url, 'POST', '/predict', to_payload(row, f'eval-{args.label}'))
        if status != 200:
            sys.exit(f'예측 실패 {status}: {body}')
        results.append({'date': to_payload(row, '')['date'], 'hour': int(row['Hour']), 'actual': int(row[TARGET]),
                        'predicted': body['predicted_rentals']})
    errors = [abs(r['predicted'] - r['actual']) for r in results]
    status, demo = http_json(args.url, 'POST', '/predict', {**DEMO, 'client': f'eval-{args.label}'})

    summary = {
        'label': args.label, 'url': args.url, 'health': health, 'rows': len(results), 'seed': args.seed,
        'mae': round(sum(errors) / len(errors), 2),
        'within_10pct_share': round(sum(abs(r['predicted'] - r['actual']) <= 0.1 * max(r['actual'], 1) for r in results) / len(results), 3),
        'demo_input': DEMO, 'demo_prediction': demo.get('predicted_rentals') if status == 200 else None,
        'seconds': round(time.time() - started, 1),
        'note': '평가용(test) 행을 서비스 API로 보내 측정했다. 피드백은 보내지 않았다.',
        'rows_detail': results,
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in summary.items() if k != 'rows_detail'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
