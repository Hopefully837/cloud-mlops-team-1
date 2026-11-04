#!/usr/bin/env bash
# 클라이언트(GitHub Actions 또는 CloudShell)에서 실행: 태그가 EC2_TAG_NAME인 EC2에 교체를 요청하고 끝날 때까지 기다린다.
#   bash scripts/ssm-deploy.sh <이미지 주소:태그> <빌드 번호(커밋)>
# 환경 변수: AWS_REGION(기본 ap-northeast-2), EC2_TAG_NAME(기본 bike-api), CLOUDWATCH_LOG_GROUP·CLOUDWATCH_LOG_STREAM(선택)
set -euo pipefail

IMAGE_URI="${1:?이미지 주소가 필요합니다}"
REV="${2:?빌드 번호(커밋)가 필요합니다}"
REGION="${AWS_REGION:-ap-northeast-2}"
TAG_NAME="${EC2_TAG_NAME:-bike-api}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

ids="$(aws ec2 describe-instances --region "$REGION" \
  --filters "Name=tag:Name,Values=${TAG_NAME}" "Name=instance-state-name,Values=running" \
  --query 'Reservations[].Instances[].InstanceId' --output text)"
count="$(echo "$ids" | wc -w)"
if [ "$count" -ne 1 ]; then
  echo "태그 Name=${TAG_NAME} 인 실행 중 인스턴스가 ${count}대입니다(1대여야 합니다): ${ids}" >&2
  exit 1
fi
echo "대상 인스턴스: ${ids} (region=${REGION})"

params="$(mktemp)"
python3 - "$SCRIPT_DIR/deploy-ec2.sh" "$IMAGE_URI" "$REV" "$REGION" > "$params" <<'PY'
import base64, json, os, sys
script, image, rev, region = sys.argv[1:5]
b64 = base64.b64encode(open(script, 'rb').read()).decode()
env = f"export AWS_REGION={region}"
for k in ("CLOUDWATCH_LOG_GROUP", "CLOUDWATCH_LOG_STREAM"):
    if os.environ.get(k):
        env += f" {k}={json.dumps(os.environ[k])}"
print(json.dumps({"commands": [
    "set -e", env,
    f"echo {b64} | base64 -d > /tmp/deploy-ec2.sh",
    f"bash /tmp/deploy-ec2.sh {json.dumps(image)} {json.dumps(rev)}",
]}))
PY

command_id="$(aws ssm send-command --region "$REGION" --instance-ids "$ids" \
  --document-name AWS-RunShellScript --comment "bike-api deploy ${REV}" \
  --parameters "file://${params}" --query 'Command.CommandId' --output text)"
echo "접수된 명령 번호: ${command_id}"

# 명령이 접수된 것과 서버에서 끝난 것은 다르다. 완료 상태를 따로 확인한다.
status=0
aws ssm wait command-executed --region "$REGION" --command-id "$command_id" --instance-id "$ids" || status=$?
aws ssm get-command-invocation --region "$REGION" --command-id "$command_id" --instance-id "$ids" \
  --query '{Status:Status,Output:StandardOutputContent,Error:StandardErrorContent}' --output json
rm -f "$params"
exit "$status"
