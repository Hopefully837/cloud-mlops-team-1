#!/usr/bin/env bash
# EC2에서 주기적으로(systemd timer, 5분) 실행한다. GitHub 브랜치의 최신 커밋과 실행 중인 build_revision을 비교해
# - 같으면 아무것도 하지 않고(중복 배포 방지),
# - 다르면 새로 빌드해서 컨테이너를 교체한다. 새 버전이 /health에 응답하지 않으면 이전 이미지로 되돌리고,
#   같은 커밋을 계속 다시 빌드하지 않도록 실패 커밋을 기록해 둔다.
# 설정은 /etc/bike-api.env(REPO_URL, BRANCH, CLOUDWATCH_LOG_GROUP …)에서 읽는다.
set -euo pipefail

ENV_FILE="${ENV_FILE:-/etc/bike-api.env}"
[ -f "$ENV_FILE" ] && . "$ENV_FILE"

REPO_URL="${REPO_URL:?REPO_URL이 필요합니다(/etc/bike-api.env)}"
BRANCH="${BRANCH:-main}"
APP_DIR="${APP_DIR:-/opt/bike-api}"
NAME="${CONTAINER_NAME:-bike-api}"
PORT="${PORT:-8000}"
STATE_DIR="${STATE_DIR:-/var/lib/bike-api}"
LOCK_FILE="${LOCK_FILE:-/var/lock/bike-api-update.lock}"
WAIT_TRIES="${WAIT_TRIES:-30}"   # /health 확인 횟수 (기본 30회 x 2초 = 60초)
WAIT_SLEEP="${WAIT_SLEEP:-2}"
HEALTH_URL="http://127.0.0.1:${PORT}/health"

mkdir -p "$STATE_DIR"
exec 9>"$LOCK_FILE"
flock -n 9 || { echo "다른 갱신이 진행 중입니다. 건너뜁니다."; exit 0; }

log() { echo "$(date -u +%FT%TZ) bike-api-update: $*"; }

health_revision() {  # 실행 중인 서버의 build_revision. 응답이 없으면 빈 문자열
  curl -fsS --max-time 5 "$HEALTH_URL" 2>/dev/null \
    | python3 -c 'import sys, json; print(json.load(sys.stdin).get("build_revision", ""))' 2>/dev/null || true
}

wait_for_revision() {  # $1이 /health에 나타날 때까지 기다린다
  for _ in $(seq 1 "$WAIT_TRIES"); do
    [ "$(health_revision)" = "$1" ] && return 0
    sleep "$WAIT_SLEEP"
  done
  return 1
}

run_container() {  # $1=이미지
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  local env_args=()
  [ -n "${CLOUDWATCH_LOG_GROUP:-}" ] && env_args+=(-e "CLOUDWATCH_LOG_GROUP=$CLOUDWATCH_LOG_GROUP" -e "AWS_REGION=${AWS_REGION:-ap-northeast-2}")
  [ -n "${CLOUDWATCH_LOG_STREAM:-}" ] && env_args+=(-e "CLOUDWATCH_LOG_STREAM=$CLOUDWATCH_LOG_STREAM")
  docker run -d --name "$NAME" --restart unless-stopped -p "${PORT}:8000" "${env_args[@]}" "$1" >/dev/null
}

remote_sha="$(git ls-remote "$REPO_URL" "refs/heads/${BRANCH}" | cut -f1)"
if [ -z "$remote_sha" ]; then
  log "원격 브랜치 ${BRANCH} 의 커밋을 읽지 못했습니다. 다음 주기에 다시 확인합니다."
  exit 1
fi

current="$(health_revision)"
if [ "$current" = "$remote_sha" ]; then
  log "최신 커밋(${remote_sha:0:12})이 이미 실행 중입니다. 배포하지 않습니다."
  exit 0
fi
if [ -f "$STATE_DIR/failed-revision" ] && [ "$(cat "$STATE_DIR/failed-revision")" = "$remote_sha" ]; then
  log "커밋 ${remote_sha:0:12} 는 이전에 배포에 실패해 건너뜁니다. 새 커밋이 올라오면 다시 시도합니다."
  exit 0
fi

log "새 커밋 ${remote_sha:0:12} 를 배포합니다. (실행 중: ${current:-없음})"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" fetch --depth 1 origin "$BRANCH"
  git -C "$APP_DIR" checkout -q -f FETCH_HEAD
else
  git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi
rev="$(git -C "$APP_DIR" rev-parse HEAD)"

previous_image="$(docker inspect --format '{{.Config.Image}}' "$NAME" 2>/dev/null || true)"
image="bike-api:${rev}"
# 빌드가 실패해도 실행 중인 컨테이너는 그대로 둔다 (set -e로 여기서 종료)
docker build --build-arg "BUILD_REVISION=${rev}" -t "$image" "$APP_DIR"

run_container "$image"
if wait_for_revision "$rev"; then
  rm -f "$STATE_DIR/failed-revision"
  log "교체 완료: build_revision=${rev}"
  exit 0
fi

log "새 버전이 /health 에서 build_revision=${rev} 로 응답하지 않았습니다."
docker logs --tail 20 "$NAME" || true
echo "$rev" > "$STATE_DIR/failed-revision"
if [ -n "$previous_image" ]; then
  log "이전 이미지(${previous_image})로 되돌립니다."
  run_container "$previous_image"
  if wait_for_revision "$current"; then log "복구 완료: build_revision=${current}"; else log "복구 후에도 응답이 없습니다. 수동 확인이 필요합니다."; fi
fi
exit 1
