"""시간당 자전거 대여 수 예측 API.

실행 (저장소 최상위 폴더): uvicorn src.api:app
- GET  /         : 예측 웹페이지 (web/index.html)
- GET  /health   : 서버·모델 상태 (정상 200, 모델 준비 실패 503)
- POST /predict  : 예측 (정상 200, 입력 오류 422, 모델 준비 실패 503). 응답에 request_id가 붙는다
- POST /feedback : 예측했던 시간의 실제 대여 수 기록 (정상 200, 입력 오류 422)

예측·피드백은 src/event_log.py로 JSON 한 줄씩 남긴다 (형식: docs/logging.md).
"""
import os
import time
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager

from fastapi import Body, FastAPI
from fastapi.responses import FileResponse, JSONResponse

from src import event_log, predict

state = {'model': None, 'error': None, 'model_version': None}

# 배포한 코드 버전(커밋 번호). 이미지를 만들 때 BUILD_REVISION으로 넣고, /health에서 읽어 배포 결과를 대조한다
BUILD_REVISION = os.environ.get('BUILD_REVISION', 'dev')

# 최근 예측을 request_id로 찾아 피드백이 오면 바로 오차(reward)를 계산한다.
# 서버 메모리에만 있으므로 재시작하면 사라진다. 그때는 로그에서 request_id로 이어 붙인다.
RECENT_MAX = 10000
recent_predictions = OrderedDict()


@asynccontextmanager
async def lifespan(app):
    event_log.setup()
    try:  # 서버가 시작될 때 모델을 한 번만 읽는다
        state['model'] = predict.load_model()
        state['model_version'] = predict.model_version()
        event_log.log('model_loaded', model_path=str(predict.MODEL_PATH), model_version=state['model_version'])
    except Exception as e:  # 모델이 없거나 손상돼도 서버는 켜 두고 503으로 알린다
        state['error'] = f'{type(e).__name__}: {e}'
        event_log.log('model_load_failed', model_path=str(predict.MODEL_PATH), error=state['error'])
    yield
    event_log.close()


app = FastAPI(title='Seoul Bike Rental Prediction', lifespan=lifespan)


def model_not_ready():
    return JSONResponse(status_code=503, content={'status': 'error', 'reason': '모델을 읽지 못했습니다', 'detail': state['error']})


@app.get('/')
def index():
    return FileResponse('web/index.html')


@app.get('/health')
def health():
    if state['model'] is None:
        return model_not_ready()
    return {'status': 'ok', 'model_path': str(predict.MODEL_PATH), 'model_version': state['model_version'],
            'build_revision': BUILD_REVISION}


@app.post('/predict')
def predict_rentals(payload: dict = Body(...)):
    if state['model'] is None:
        return model_not_ready()
    errors = predict.validate(payload)
    if errors:
        event_log.log('invalid_input', endpoint='/predict', payload=payload, errors=errors)
        return JSONResponse(status_code=422, content={'errors': errors})

    started = time.perf_counter()
    features = predict.model_features(state['model'], predict.build_values(payload))
    result = predict.predict_features(state['model'], features)
    request_id = str(uuid.uuid4())

    recent_predictions[request_id] = {'features': features, 'predicted_rentals': result['predicted_rentals']}
    if len(recent_predictions) > RECENT_MAX:
        recent_predictions.popitem(last=False)  # 가장 오래된 것부터 버린다

    event_log.log(
        'prediction',
        request_id=request_id,
        model_version=state['model_version'],
        input=payload,                                          # 사용자가 보낸 원래 입력 (날짜·공휴일 포함)
        state=features,                                         # 모델 입력 = 강화학습의 state
        action={'predicted_rentals': result['predicted_rentals']},  # 모델 출력 = action
        latency_ms=round((time.perf_counter() - started) * 1000, 2),
    )
    return {**result, 'request_id': request_id}


@app.post('/feedback')
def feedback(payload: dict = Body(...)):
    errors = predict.validate_feedback(payload)
    if errors:
        event_log.log('invalid_input', endpoint='/feedback', payload=payload, errors=errors)
        return JSONResponse(status_code=422, content={'errors': errors})

    request_id = payload['request_id']
    actual = payload['actual_rentals']
    found = recent_predictions.get(request_id)
    record = {'request_id': request_id, 'model_version': state['model_version'], 'actual_rentals': actual,
              'matched': found is not None}
    if found:
        error = found['predicted_rentals'] - actual   # +면 과대 예측, -면 과소 예측
        record.update(state=found['features'], action={'predicted_rentals': found['predicted_rentals']},
                      error=error, reward=-abs(error))
    else:  # 서버 재시작 등으로 예측 기록이 메모리에 없다. 실제값은 남기고 reward는 로그 결합 때 계산한다
        record.update(state=None, action=None, error=None, reward=None)
    event_log.log('feedback', **record)

    return {'status': 'ok', **{k: record[k] for k in ('request_id', 'matched', 'actual_rentals', 'error', 'reward')}}
