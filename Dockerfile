# 따릉이 대여 수 예측 API 이미지.
# 학습(mlflow)은 이미지에 넣지 않고, 저장된 모델(models/model.joblib)로 예측만 한다.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements-serve.txt .
RUN pip install --no-cache-dir -r requirements-serve.txt

COPY src ./src
COPY web ./web
COPY models ./models

# 배포한 코드 버전(커밋 번호). /health의 build_revision으로 확인한다
ARG BUILD_REVISION=dev
ENV BUILD_REVISION=${BUILD_REVISION}

RUN useradd --create-home appuser
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status == 200 else 1)"

CMD ["uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "8000"]
