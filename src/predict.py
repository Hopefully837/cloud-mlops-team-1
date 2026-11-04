"""저장된 모델로 시간당 자전거 대여 수를 예측한다.

입력 검사 범위는 학습 데이터에 실제로 있던 범위에 맞춘다.
범위 밖 값은 모델이 배운 적이 없어 끝값과 같은 예측만 나오므로 받지 않는다.
"""
import datetime
import hashlib
import math
import os
from pathlib import Path

import joblib
import pandas as pd

# 서버 실행 시 MODEL_PATH 환경 변수로 다른 모델 경로를 줄 수 있다 (모델 준비 실패 테스트용)
MODEL_PATH = Path(os.environ.get('MODEL_PATH', 'models/model.joblib'))

# 입력 이름: (설명, 최소, 최대). 학습 데이터 범위: 기온 -17.8~39.4°C, 습도 1~98%, 강수량 0~35mm
NUMBER_RULES = {
    'hour': ('시간(0~23 정수)', 0, 23),
    'temperature': ('기온(°C)', -20, 40),
    'humidity': ('습도(%)', 1, 100),
    'rainfall': ('강수량(mm)', 0, 40),
}
REQUIRED = ['date', *NUMBER_RULES, 'holiday']

# 피드백으로 받는 실제 대여 수 상한. 학습 데이터 최대는 3,556대라 넉넉히 잡는다
ACTUAL_MAX = 10000


def load_model():
    return joblib.load(MODEL_PATH)


def model_version():
    """모델 파일 내용의 SHA-256 앞 12자리. 어떤 모델이 낸 예측인지 로그에서 구분하는 데 쓴다."""
    return hashlib.sha256(MODEL_PATH.read_bytes()).hexdigest()[:12]


def validate_feedback(payload):
    """피드백(실제 대여 수) 입력을 검사해 오류 목록을 돌려준다."""
    errors = []
    request_id = payload.get('request_id')
    if not isinstance(request_id, str) or not request_id.strip():
        errors.append({'field': 'request_id', 'reason': '/predict 응답의 request_id(문자열)가 필요합니다'})
    actual = payload.get('actual_rentals')
    if actual is None:
        errors.append({'field': 'actual_rentals', 'reason': '필수 항목이 없습니다'})
    elif isinstance(actual, bool) or not isinstance(actual, int):
        errors.append({'field': 'actual_rentals', 'reason': '실제 대여 수는 정수여야 합니다'})
    elif not 0 <= actual <= ACTUAL_MAX:
        errors.append({'field': 'actual_rentals', 'reason': f'실제 대여 수는 0~{ACTUAL_MAX}이어야 합니다'})
    return errors


def validate(payload):
    """입력을 검사해 오류 목록을 돌려준다. 빈 목록이면 정상이다."""
    errors = []
    for field in REQUIRED:
        if field not in payload or payload[field] is None:
            errors.append({'field': field, 'reason': '필수 항목이 없습니다'})

    date = payload.get('date')
    if date is not None:
        try:
            datetime.date.fromisoformat(date)
        except (TypeError, ValueError):
            errors.append({'field': 'date', 'reason': 'YYYY-MM-DD 형식의 날짜여야 합니다'})

    for field, (label, low, high) in NUMBER_RULES.items():
        value = payload.get(field)
        if value is None:
            continue
        # bool은 숫자로 취급하지 않고, "25"처럼 숫자처럼 생긴 문자열도 거부한다
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append({'field': field, 'reason': f'{label}은(는) 숫자여야 합니다'})
        elif not math.isfinite(value):
            # NaN은 모든 비교가 False라 아래 범위 검사를 통과하고, hour의 int(NaN·inf)는 500을 낸다
            errors.append({'field': field, 'reason': f'{label}은(는) 유한한 숫자여야 합니다'})
        elif field == 'hour' and value != int(value):
            errors.append({'field': field, 'reason': f'{label}은(는) 정수여야 합니다'})
        elif (low is not None and value < low) or (high is not None and value > high):
            limit = f'{low} 이상' if high is None else f'{low}~{high}'
            errors.append({'field': field, 'reason': f'{label}은(는) {limit}이어야 합니다'})

    holiday = payload.get('holiday')
    if holiday is not None and not isinstance(holiday, bool):
        errors.append({'field': 'holiday', 'reason': 'true 또는 false여야 합니다'})
    return errors


def build_values(payload):
    """검사를 통과한 입력에서 모델이 쓸 수 있는 값을 모두 만든다. 날짜로 요일·주말·쉬는 날·월을 계산한다."""
    date = datetime.date.fromisoformat(payload['date'])
    is_weekend = int(date.weekday() >= 5)  # 토·일=1
    holiday = int(payload['holiday'])      # 공휴일=1, 평일=0
    values = {
        'Hour': int(payload['hour']),
        'Temperature(°C)': payload['temperature'],
        'Humidity(%)': payload['humidity'],
        'Rainfall(mm)': payload['rainfall'],
        'Holiday': holiday,
        'is_weekend': is_weekend,
        'is_offday': is_weekend | holiday,  # 쉬는 날 = 주말 또는 공휴일
        'month': date.month,                # 1~12
        'dow': date.weekday(),              # 월=0 ~ 일=6
    }
    return values


def model_features(model, values):
    """모델이 학습한 입력만 학습 때 순서대로 골라 돌려준다 (로그의 state로도 쓴다).

    입력 목록·순서를 모델 파일에서 읽어, 입력 조합이 바뀌어도 이 코드를 고치지 않게 한다.
    """
    return {c: values[c] for c in model.feature_names_in_}


def predict_features(model, features):
    """model_features()가 만든 입력으로 예측한다."""
    value = model.predict(pd.DataFrame([features]))[0]
    return {'predicted_rentals': max(0, round(float(value))), 'unit': '대'}


def predict(model, payload):
    """검사를 통과한 입력으로 예측한다."""
    return predict_features(model, model_features(model, build_values(payload)))
"""저장된 모델로 시간당 자전거 대여 수를 예측한다.

입력 검사 범위는 학습 데이터에 실제로 있던 범위에 맞춘다.
범위 밖 값은 모델이 배운 적이 없어 끝값과 같은 예측만 나오므로 받지 않는다.
"""
import datetime
import hashlib
import os
from pathlib import Path

import joblib
import pandas as pd

# 서버 실행 시 MODEL_PATH 환경 변수로 다른 모델 경로를 줄 수 있다 (모델 준비 실패 테스트용)
MODEL_PATH = Path(os.environ.get('MODEL_PATH', 'models/model.joblib'))

# 입력 이름: (설명, 최소, 최대). 학습 데이터 범위: 기온 -17.8~39.4°C, 습도 1~98%, 강수량 0~35mm
NUMBER_RULES = {
    'hour': ('시간(0~23 정수)', 0, 23),
    'temperature': ('기온(°C)', -20, 40),
    'humidity': ('습도(%)', 1, 100),
    'rainfall': ('강수량(mm)', 0, 40),
}
REQUIRED = ['date', *NUMBER_RULES, 'holiday']

# 피드백으로 받는 실제 대여 수 상한. 학습 데이터 최대는 3,556대라 넉넉히 잡는다
ACTUAL_MAX = 10000


def load_model():
    return joblib.load(MODEL_PATH)


def model_version():
    """모델 파일 내용의 SHA-256 앞 12자리. 어떤 모델이 낸 예측인지 로그에서 구분하는 데 쓴다."""
    return hashlib.sha256(MODEL_PATH.read_bytes()).hexdigest()[:12]


def validate_feedback(payload):
    """피드백(실제 대여 수) 입력을 검사해 오류 목록을 돌려준다."""
    errors = []
    request_id = payload.get('request_id')
    if not isinstance(request_id, str) or not request_id.strip():
        errors.append({'field': 'request_id', 'reason': '/predict 응답의 request_id(문자열)가 필요합니다'})
    actual = payload.get('actual_rentals')
    if actual is None:
        errors.append({'field': 'actual_rentals', 'reason': '필수 항목이 없습니다'})
    elif isinstance(actual, bool) or not isinstance(actual, int):
        errors.append({'field': 'actual_rentals', 'reason': '실제 대여 수는 정수여야 합니다'})
    elif not 0 <= actual <= ACTUAL_MAX:
        errors.append({'field': 'actual_rentals', 'reason': f'실제 대여 수는 0~{ACTUAL_MAX}이어야 합니다'})
    return errors


def validate(payload):
    """입력을 검사해 오류 목록을 돌려준다. 빈 목록이면 정상이다."""
    errors = []
    for field in REQUIRED:
        if field not in payload or payload[field] is None:
            errors.append({'field': field, 'reason': '필수 항목이 없습니다'})

    date = payload.get('date')
    if date is not None:
        try:
            datetime.date.fromisoformat(date)
        except (TypeError, ValueError):
            errors.append({'field': 'date', 'reason': 'YYYY-MM-DD 형식의 날짜여야 합니다'})

    for field, (label, low, high) in NUMBER_RULES.items():
        value = payload.get(field)
        if value is None:
            continue
        # bool은 숫자로 취급하지 않고, "25"처럼 숫자처럼 생긴 문자열도 거부한다
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append({'field': field, 'reason': f'{label}은(는) 숫자여야 합니다'})
        elif field == 'hour' and value != int(value):
            errors.append({'field': field, 'reason': f'{label}은(는) 정수여야 합니다'})
        elif (low is not None and value < low) or (high is not None and value > high):
            limit = f'{low} 이상' if high is None else f'{low}~{high}'
            errors.append({'field': field, 'reason': f'{label}은(는) {limit}이어야 합니다'})

    holiday = payload.get('holiday')
    if holiday is not None and not isinstance(holiday, bool):
        errors.append({'field': 'holiday', 'reason': 'true 또는 false여야 합니다'})
    return errors


def build_values(payload):
    """검사를 통과한 입력에서 모델이 쓸 수 있는 값을 모두 만든다. 날짜로 요일·주말·쉬는 날·월을 계산한다."""
    date = datetime.date.fromisoformat(payload['date'])
    is_weekend = int(date.weekday() >= 5)  # 토·일=1
    holiday = int(payload['holiday'])      # 공휴일=1, 평일=0
    values = {
        'Hour': int(payload['hour']),
        'Temperature(°C)': payload['temperature'],
        'Humidity(%)': payload['humidity'],
        'Rainfall(mm)': payload['rainfall'],
        'Holiday': holiday,
        'is_weekend': is_weekend,
        'is_offday': is_weekend | holiday,  # 쉬는 날 = 주말 또는 공휴일
        'month': date.month,                # 1~12
        'dow': date.weekday(),              # 월=0 ~ 일=6
    }
    return values


def model_features(model, values):
    """모델이 학습한 입력만 학습 때 순서대로 골라 돌려준다 (로그의 state로도 쓴다).

    입력 목록·순서를 모델 파일에서 읽어, 입력 조합이 바뀌어도 이 코드를 고치지 않게 한다.
    """
    return {c: values[c] for c in model.feature_names_in_}


def predict_features(model, features):
    """model_features()가 만든 입력으로 예측한다."""
    value = model.predict(pd.DataFrame([features]))[0]
    return {'predicted_rentals': max(0, round(float(value))), 'unit': '대'}


def predict(model, payload):
    """검사를 통과한 입력으로 예측한다."""
    return predict_features(model, model_features(model, build_values(payload)))
