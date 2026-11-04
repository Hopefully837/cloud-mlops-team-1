#!/usr/bin/env bash
# deploy-aws-cli.sh가 만든 자원(인스턴스·보안 그룹)을 정리한다. 공유 계정이므로 우리가 태그를 붙인 것만 지운다.
#   bash scripts/teardown-aws-cli.sh            # 무엇을 지울지 보여 주고 확인을 받는다
#   bash scripts/teardown-aws-cli.sh --yes      # 확인 없이 지운다
# 대상: 태그 Name=INSTANCE_NAME(기본 bike-api-team1) 이면서 Project=cloud-mlops-team1 인 인스턴스와 ${NAME}-sg 보안 그룹
set -euo pipefail

REGION="${AWS_REGION:-ap-northeast-2}"
NAME="${INSTANCE_NAME:-bike-api-team1}"
PROJECT_TAG="cloud-mlops-team1"

ids="$(aws ec2 describe-instances --region "$REGION" \
  --filters "Name=tag:Name,Values=${NAME}" "Name=tag:Project,Values=${PROJECT_TAG}" \
            "Name=instance-state-name,Values=pending,running,stopping,stopped" \
  --query 'Reservations[].Instances[].InstanceId' --output text)"
sg="$(aws ec2 describe-security-groups --region "$REGION" \
  --filters "Name=group-name,Values=${NAME}-sg" "Name=tag:Project,Values=${PROJECT_TAG}" \
  --query 'SecurityGroups[0].GroupId' --output text)"

echo "계정=$(aws sts get-caller-identity --query Account --output text) 리전=${REGION}"
echo "종료할 인스턴스: ${ids:-없음}"
echo "삭제할 보안 그룹: $([ "$sg" = "None" ] && echo 없음 || echo "$sg")"
if [ -z "$ids" ] && [ "$sg" = "None" ]; then echo "지울 것이 없습니다."; exit 0; fi

if [ "${1:-}" != "--yes" ]; then
  read -r -p "위 자원을 지울까요? (yes 입력) " answer
  [ "$answer" = "yes" ] || { echo "취소했습니다."; exit 1; }
fi

if [ -n "$ids" ]; then
  aws ec2 terminate-instances --region "$REGION" --instance-ids $ids >/dev/null
  aws ec2 wait instance-terminated --region "$REGION" --instance-ids $ids
  echo "인스턴스 종료 완료: ${ids}"
fi
if [ "$sg" != "None" ]; then
  aws ec2 delete-security-group --region "$REGION" --group-id "$sg" && echo "보안 그룹 삭제 완료: ${sg}"
fi
echo "CloudWatch 로그 그룹과 S3 객체는 지우지 않았습니다. 필요하면 따로 정리하세요."
