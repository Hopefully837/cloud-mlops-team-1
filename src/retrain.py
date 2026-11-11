#!/usr/bin/env python3
"""로그에서 모은 자료(logs_to_training.py의 clean)를 학습 자료에 더해 같은 방법으로 다시 학습하고, 학습 전후를 비교한다.

공정한 비교를 위해 다음을 지킨다.
  - 입력 조합과 모델 종류는 이전에 고른 것(results/metrics.json의 selected)을 그대로 쓴다. 고르는 단계를 다시 하지 않는다.
  - 로그 행은 학습용에만 더한다. 검증·평가용은 원본 분할(src/splits.py) 그대로다. 로그 전처리가 평가용과 겹치는 행을 이미 빼 둔다.
  - 이전 모델과 새 모델을 같은 평가용 행에서 비교하고, 행마다 짝지은 오차 차이의 95% 신뢰구간(부트스트랩)으로
    '개선'인지 '차이 없음'인지 '악화'인지 판정한다. 평균만 보고 개선이라고 말하지 않는다.

기본값은 새 모델을 models/model-retrained.joblib에만 저장한다. --promote를 주면 악화가 아닐 때 models/model.joblib을 바꾼다.
"""
import argparse
import json
import os
import shutil
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, r2_score

sys.path.insert(0, str(Path(__file__).resolve().parent))
from splits import SEED, TARGET, load, split  # noqa: E402
from train import FEATURE_SETS, digest, make_models  # noqa: E402

BOOTSTRAP = 2000


def paired_ci(abs_prev, abs_new):
    """행마다 (새 오차 - 이전 오차)의 평균과 95% 부트스트랩 신뢰구간. 음수면 새 모델이 낫다."""
    diff = abs_new - abs_prev
    rng = np.random.default_rng(SEED)
    means = np.array([diff[rng.integers(0, len(diff), len(diff))].mean() for _ in range(BOOTSTRAP)])
    return float(diff.mean()), float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def verdict_of(low, high):
    if high < 0:
        return '개선 (95% 신뢰구간이 0보다 아래)'
    if low > 0:
        return '악화 (95% 신뢰구간이 0보다 위)'
    return '차이 없음 (95% 신뢰구간이 0을 포함)'


def subgroup_table(test, abs_prev, abs_new):
    masks = {'전체': np.ones(len(test), dtype=bool)}
    for name, low, high in [('새벽 0~5시', 0, 5), ('오전 6~11시', 6, 11), ('오후 12~17시', 12, 17), ('저녁 18~23시', 18, 23)]:
        masks[name] = ((test['Hour'] >= low) & (test['Hour'] <= high)).to_numpy()
    masks['평일'] = ((test['is_weekend'] == 0) & (test['Holiday'] == 0)).to_numpy()
    masks['주말'] = (test['is_weekend'] == 1).to_numpy()
    masks['공휴일'] = (test['Holiday'] == 1).to_numpy()
    masks['비 옴(강수 > 0)'] = (test['Rainfall(mm)'] > 0).to_numpy()
    out = {}
    for name, m in masks.items():
        if m.sum():
            out[name] = {'rows': int(m.sum()), 'mae_before': round(float(abs_prev[m].mean()), 2),
                         'mae_after': round(float(abs_new[m].mean()), 2),
                         'diff': round(float(abs_new[m].mean() - abs_prev[m].mean()), 2)}
    return out


def log_mlflow(params, metrics, model_path):
    try:
        import mlflow
        mlflow.set_experiment('bike-rental-retrain')
        with mlflow.start_run(run_name='retrain with logs'):
            mlflow.log_params(params)
            mlflow.log_metrics(metrics)
            mlflow.log_artifact(str(model_path))
        return True
    except Exception as e:  # MLflow 저장소가 없어도 재학습 결과는 JSON에 남는다
        print(f'(MLflow 기록은 건너뜀: {type(e).__name__}: {str(e)[:80]})')
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--source', default='data/processed/bike-clean.csv')
    parser.add_argument('--extra', default='data/processed/log-training.csv', help='logs_to_training.py의 clean 결과')
    parser.add_argument('--prev-metrics', default='results/metrics.json')
    parser.add_argument('--prev-model', default='models/model.joblib')
    parser.add_argument('--model', default='models/model-retrained.joblib', help='새 모델 저장 위치')
    parser.add_argument('--metrics', default='results/retrain-metrics.json')
    parser.add_argument('--promote', action='store_true', help='악화가 아니면 --promote-to를 새 모델로 교체')
    parser.add_argument('--promote-to', default='models/model.joblib')
    args = parser.parse_args()

    prev_metrics = json.loads(Path(args.prev_metrics).read_text(encoding='utf-8'))
    selected = prev_metrics['selected']
    features = FEATURE_SETS[selected['feature_set']]

    df = load(args.source)
    train, _val, test = split(df)
    extra = load(args.extra) if Path(args.extra).exists() and Path(args.extra).stat().st_size > 0 else df.iloc[0:0]
    extra = extra.reset_index(drop=True)
    if len(extra) == 0:
        sys.exit('로그에서 모은 재학습용 행이 없습니다. logs_to_training.py 결과(clean)가 비어 있습니다.')

    prev_model = joblib.load(args.prev_model)  # 덮어쓰기 전에 먼저 읽는다
    if list(prev_model.feature_names_in_) != features:
        sys.exit(f'이전 모델의 입력({list(prev_model.feature_names_in_)})이 selected({features})와 다릅니다.')

    train_plus = pd.concat([train, extra], ignore_index=True)
    model = make_models()[selected['model']]
    model.fit(train_plus[features], train_plus[TARGET])

    pred_prev = prev_model.predict(test[features])
    pred_new = model.predict(test[features])
    abs_prev = np.abs(test[TARGET].to_numpy() - pred_prev)
    abs_new = np.abs(test[TARGET].to_numpy() - pred_new)
    mae_prev, mae_new = float(abs_prev.mean()), float(abs_new.mean())
    mean_diff, ci_low, ci_high = paired_ci(abs_prev, abs_new)
    verdict = verdict_of(ci_low, ci_high)
    baseline = mean_absolute_error(test[TARGET], [train[TARGET].mean()] * len(test))

    model_path = Path(args.model)
    model_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, model_path, compress=3)
    promoted = False
    if args.promote and not verdict.startswith('악화'):
        shutil.copyfile(model_path, args.promote_to)
        promoted = True

    expected_prev = prev_metrics['test']['mae']
    metrics = {
        'method': '이전에 고른 입력·모델을 그대로 두고, 학습용에 로그 행만 더해 다시 학습 (고르는 단계 생략)',
        'config': {'feature_set': selected['feature_set'], 'features': features, 'model': selected['model']},
        'data': {
            'source_sha256': digest(args.source), 'extra_sha256': digest(args.extra),
            'train_rows_before': len(train), 'extra_rows_from_logs': len(extra), 'train_rows_after': len(train_plus),
            'extra_share_pct': round(len(extra) / len(train_plus) * 100, 2),
            'test_rows': len(test), 'test_note': '평가용은 원본 분할 그대로이며 학습에 쓰지 않았다(로그 중 평가용과 겹친 행은 전처리에서 제외)',
        },
        'test': {
            'baseline_mae': round(baseline, 2),
            'before': {'mae': round(mae_prev, 2), 'r2': round(float(r2_score(test[TARGET], pred_prev)), 4)},
            'after': {'mae': round(mae_new, 2), 'r2': round(float(r2_score(test[TARGET], pred_new)), 4)},
            'mae_diff(after-before)': round(mean_diff, 3),
            'mae_diff_95ci': [round(ci_low, 3), round(ci_high, 3)],
            'mae_change_pct': round((mae_new / mae_prev - 1) * 100, 2),
            'verdict': verdict,
            'identical_predictions_pct': round(float((np.abs(pred_prev - pred_new) < 1e-9).mean() * 100), 1),
        },
        'by_group': subgroup_table(test, abs_prev, abs_new),
        'checks': {'이전 모델 평가 MAE가 results/metrics.json과 같은가': abs(mae_prev - expected_prev) < 0.01,
                   '이전 모델 평가 MAE(metrics.json)': expected_prev},
        'model_path': str(model_path), 'model_sha256': digest(model_path), 'promoted_to': args.promote_to if promoted else None,
        'limitation': ['평가용은 원본 자료의 랜덤 20%라서 미래 예측 성능과는 다르다(시간 순서 분할이 아님).',
                       '로그 행의 실제값은 사람이 입력한 값이다. 팀 테스트·시뮬레이션 로그면 실제 이용자 피드백이 아니다.',
                       '로그 행이 학습 자료에 비해 적으면 모델이 거의 바뀌지 않는다(extra_share_pct 참고).'],
    }
    Path(args.metrics).parent.mkdir(parents=True, exist_ok=True)
    Path(args.metrics).write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if os.environ.get('MLFLOW_TRACKING_URI'):
        log_mlflow({'feature_set': selected['feature_set'], 'model': selected['model'], 'extra_rows': len(extra)},
                   {'test_mae_before': mae_prev, 'test_mae_after': mae_new, 'test_mae_diff': mean_diff}, model_path)
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
