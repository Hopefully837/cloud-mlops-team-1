#!/usr/bin/env bash
# EC2에서 실행: ECR 이미지로 bike-api 컨테이너를 교체한다.
#   bash scripts/deploy-ec2.sh <이미지 주소:태그> <빌드 번호(커밋)>
# - 이미 같은 build_revision이 실행 중이면 교체하지 않는다(중복 배포 방지).
# - 교체 뒤 /health의 build_revision이 요청한 값과 다르면 이전 이미지로 되돌리고 실패로 끝낸다.
set -euo pipefail

IMAGE_URI="${1:?이미지 주소(예: 123456789012.dkr.ecr.ap-northeast-2.amazonaws.com/bike-api:<커밋>)가 필요합니다}"
REV="${2:?빌드 번호(커밋)가 필요합니다}"
NAME="${CONTAINER_NAME:-bike-api}"
PORT="${PORT:-8000}"
REGION="${AWS_REGION:-ap-northeast-2}"
HEALTH_URL="http://127.0.0.1:${PORT}/health"

health_revision() {  # 실행 중인 서버의 build_revision. 응답이 없으면 빈 문자열
  curl -fsS --max-time 5 "$HEALTH_URL" 2>/dev/null \
    | python3 -c 'import sys, json; print(json.load(sys.stdin).get("build_revision", ""))' 2>/dev/null || true
}

wait_for_revision() {  # $1이 /health에 나타날 때까지 최대 60초 기다린다
  for _ in $(seq 1 30); do
    [ "$(health_revision)" = "$1" ] && return 0
    sleep 2
  done
  return 1
}

run_container() {  # $1=이미지
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  local env_args=()
  [ -n "${CLOUDWATCH_LOG_GROUP:-}" ] && env_args+=(-e "CLOUDWATCH_LOG_GROUP=$CLOUDWATCH_LOG_GROUP" -e "AWS_REGION=$REGION")
  [ -n "${CLOUDWATCH_LOG_STREAM:-}" ] && env_args+=(-e "CLOUDWATCH_LOG_STREAM=$CLOUDWATCH_LOG_STREAM")
  docker run -d --name "$NAME" --restart unless-stopped -p "${PORT}:8000" "${env_args[@]}" "$1" >/dev/null
}

current_revision="$(health_revision)"
if [ "$current_revision" = "$REV" ]; then
  echo "이미 build_revision=${REV} 가 실행 중입니다. 교체하지 않습니다."
  exit 0
fi

previous_image="$(docker inspect --format '{{.Config.Image}}' "$NAME" 2>/dev/null || true)"
echo "교체 전: build_revision=${current_revision:-없음} image=${previous_image:-없음}"

registry="${IMAGE_URI%%/*}"
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$registry"
docker pull "$IMAGE_URI"

run_container "$IMAGE_URI"
if wait_for_revision "$REV"; then
  echo "교체 완료: build_revision=${REV} image=${IMAGE_URI}"
  exit 0
fi

echo "새 버전이 60초 안에 /health 에서 build_revision=${REV} 로 응답하지 않았습니다." >&2
docker logs --tail 20 "$NAME" >&2 || true
if [ -n "$previous_image" ]; then
  echo "이전 이미지(${previous_image})로 되돌립니다." >&2
  run_container "$previous_image"
  wait_for_revision "$current_revision" && echo "복구 완료: build_revision=${current_revision}" >&2 || echo "복구 후에도 응답이 없습니다. 수동 확인이 필요합니다." >&2
fi
exit 1
