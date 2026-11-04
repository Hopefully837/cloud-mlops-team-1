#!/usr/bin/env bash
# AWS CloudShell(서울 리전)에서 한 번에 배포한다. ECR·SSM·OIDC 권한이 없는 수업 계정용.
# EC2 한 대를 만들고, 첫 부팅 때 user-data가 GitHub 저장소를 받아 Docker로 빌드·실행한다(scripts/user-data-ec2.sh).
# 이후에는 EC2가 5분마다 GitHub 브랜치의 최신 커밋을 확인해 달라졌을 때만 다시 배포한다(scripts/update-on-ec2.sh).
#
#   ALLOWED_CIDR=<내 공인 IP>/32 bash scripts/deploy-aws-cli.sh
#
# 필수: ALLOWED_CIDR  8000번 포트에 접속을 허용할 주소. 내 PC 브라우저에서 https://checkip.amazonaws.com 으로 확인한다.
#                     (CloudShell의 IP가 아니다. 전체 공개 0.0.0.0/0 은 ALLOW_ANY=1 을 줘야만 허용한다)
# 선택: BRANCH(기본: 지금 체크아웃한 브랜치), REPO_URL(기본: origin), INSTANCE_NAME(bike-api-team1),
#       INSTANCE_TYPE(t3.micro, x86_64), INSTANCE_PROFILE(yeonsung-ec2-cloudwatch-role),
#       CLOUDWATCH_LOG_GROUP(/mlops/team1-bike-rental)
# 같은 이름의 인스턴스가 이미 있으면 새로 만들지 않는다(중복 생성 방지).
# 끝낸 뒤 정리: bash scripts/teardown-aws-cli.sh
set -euo pipefail

REGION="${AWS_REGION:-ap-northeast-2}"
NAME="${INSTANCE_NAME:-bike-api-team1}"
TYPE="${INSTANCE_TYPE:-t3.micro}"
PROFILE="${INSTANCE_PROFILE:-yeonsung-ec2-cloudwatch-role}"
LOG_GROUP="${CLOUDWATCH_LOG_GROUP:-/mlops/team1-bike-rental}"
PROJECT_TAG="cloud-mlops-team1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

REPO_URL="${REPO_URL:-$(git config --get remote.origin.url)}"
BRANCH="${BRANCH:-$(git rev-parse --abbrev-ref HEAD)}"

: "${ALLOWED_CIDR:?ALLOWED_CIDR 가 필요합니다. 예: ALLOWED_CIDR=203.0.113.7/32 (내 PC에서 https://checkip.amazonaws.com 확인)}"
if [ "$ALLOWED_CIDR" = "0.0.0.0/0" ] && [ "${ALLOW_ANY:-}" != "1" ]; then
  echo "0.0.0.0/0 (전체 공개)는 허용하지 않습니다. 정말 필요하면 ALLOW_ANY=1 을 함께 주세요." >&2
  exit 1
fi

account="$(aws sts get-caller-identity --query Account --output text)"
echo "계정=${account} 리전=${REGION} 이름=${NAME} 유형=${TYPE} 프로필=${PROFILE}"
echo "저장소=${REPO_URL} 브랜치=${BRANCH} 허용 주소=${ALLOWED_CIDR}"
echo "공유 계정입니다. 위 내용이 맞는지 확인하세요. 10초 뒤 계속합니다 (중단: Ctrl+C)."
sleep 10

# 같은 이름의 인스턴스가 이미 있으면 그대로 둔다 (갱신은 EC2가 5분마다 스스로 확인한다)
existing="$(aws ec2 describe-instances --region "$REGION" \
  --filters "Name=tag:Name,Values=${NAME}" "Name=tag:Project,Values=${PROJECT_TAG}" \
            "Name=instance-state-name,Values=pending,running,stopping,stopped" \
  --query 'Reservations[].Instances[].InstanceId' --output text)"
if [ -n "$existing" ]; then
  ip="$(aws ec2 describe-instances --region "$REGION" --instance-ids $existing --query 'Reservations[].Instances[].PublicIpAddress' --output text)"
  echo "이미 ${NAME} 인스턴스가 있습니다: ${existing} (공인 IP: ${ip:-없음}). 새로 만들지 않습니다."
  echo "접속: http://${ip}:8000/  (코드가 바뀌면 최대 5분 안에 자동으로 갱신됩니다)"
  exit 0
fi

vpc="$(aws ec2 describe-vpcs --region "$REGION" --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text)"
[ "$vpc" != "None" ] || { echo "기본 VPC가 없습니다. 서브넷·보안 그룹을 직접 지정하도록 스크립트를 고쳐야 합니다." >&2; exit 1; }

sg_name="${NAME}-sg"
sg="$(aws ec2 describe-security-groups --region "$REGION" --filters "Name=group-name,Values=${sg_name}" "Name=vpc-id,Values=${vpc}" \
  --query 'SecurityGroups[0].GroupId' --output text)"
if [ "$sg" = "None" ]; then
  sg="$(aws ec2 create-security-group --region "$REGION" --group-name "$sg_name" --vpc-id "$vpc" \
    --description "bike-api team1 8000 from ${ALLOWED_CIDR}" \
    --tag-specifications "ResourceType=security-group,Tags=[{Key=Name,Value=${sg_name}},{Key=Project,Value=${PROJECT_TAG}}]" \
    --query GroupId --output text)"
  echo "보안 그룹 생성: ${sg}"
fi
aws ec2 authorize-security-group-ingress --region "$REGION" --group-id "$sg" --protocol tcp --port 8000 --cidr "$ALLOWED_CIDR" 2>/dev/null \
  && echo "8000번 허용: ${ALLOWED_CIDR}" || echo "8000번 규칙이 이미 있거나 추가하지 못했습니다(${ALLOWED_CIDR})."

ami="$(aws ssm get-parameter --region "$REGION" --name /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64 --query Parameter.Value --output text)"

userdata="$(mktemp)"
{
  echo '#!/bin/bash'
  echo "export REPO_URL='${REPO_URL}' BRANCH='${BRANCH}' AWS_REGION='${REGION}' CLOUDWATCH_LOG_GROUP='${LOG_GROUP}'"
  cat "$SCRIPT_DIR/user-data-ec2.sh"
} > "$userdata"

# 컨테이너 안에서 인스턴스 역할(CloudWatch 로그 전송)을 쓰려면 IMDSv2 hop limit이 2여야 한다
instance="$(aws ec2 run-instances --region "$REGION" --image-id "$ami" --instance-type "$TYPE" \
  --iam-instance-profile "Name=${PROFILE}" --security-group-ids "$sg" \
  --metadata-options "HttpTokens=required,HttpPutResponseHopLimit=2,HttpEndpoint=enabled" \
  --user-data "fileb://${userdata}" \
  --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=${NAME}},{Key=Project,Value=${PROJECT_TAG}}]" \
  --query 'Instances[0].InstanceId' --output text)"
rm -f "$userdata"
echo "인스턴스 생성: ${instance}"

aws ec2 wait instance-running --region "$REGION" --instance-ids "$instance"
ip="$(aws ec2 describe-instances --region "$REGION" --instance-ids "$instance" --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)"

cat <<EOF

생성 완료: ${instance}  공인 IP: ${ip}
Docker 설치와 이미지 빌드에 보통 3~6분 걸립니다. 그 뒤 내 PC 브라우저에서 확인하세요.
  화면:    http://${ip}:8000/
  상태:    http://${ip}:8000/health   (build_revision 이 GitHub 브랜치 ${BRANCH} 의 최신 커밋과 같아야 합니다)
  검사:    python src/check_api.py --url http://${ip}:8000
진행 상황: aws ec2 get-console-output --region ${REGION} --instance-id ${instance} --latest --output text | tail -n 30
끝나면 정리: bash scripts/teardown-aws-cli.sh
EOF
