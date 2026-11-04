#!/usr/bin/env bash
# AWS CloudShell(서울 리전)에서 한 번에 배포한다. GitHub Actions용 AWS 권한(OIDC 역할)이 없을 때 쓴다.
#   git clone https://github.com/<소유자>/cloud-mlops-team-1.git && cd cloud-mlops-team-1
#   bash scripts/deploy-aws-cli.sh            # 현재 커밋으로 배포
#   bash scripts/deploy-aws-cli.sh <커밋>      # 지정한 커밋 번호를 빌드 번호로 쓴다(이미 체크아웃된 코드 기준)
# 순서: 계정·리전 확인 → ECR 저장소 확인(없으면 생성) → 이미지 빌드·push → EC2에 교체 요청·완료 확인
set -euo pipefail

REGION="${AWS_REGION:-ap-northeast-2}"
REPO="${ECR_REPOSITORY:-bike-api}"
REV="${1:-$(git rev-parse HEAD)}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

account="$(aws sts get-caller-identity --query Account --output text)"
echo "계정=${account} 리전=${REGION} 저장소=${REPO} 빌드 번호=${REV}"
echo "계정과 리전이 배포 대상과 맞는지 확인하세요. 5초 뒤 계속합니다 (중단: Ctrl+C)."
sleep 5

registry="${account}.dkr.ecr.${REGION}.amazonaws.com"
image_uri="${registry}/${REPO}:${REV}"

if ! aws ecr describe-repositories --region "$REGION" --repository-names "$REPO" >/dev/null 2>&1; then
  echo "ECR 저장소 ${REPO} 가 없어 만듭니다."
  aws ecr create-repository --region "$REGION" --repository-name "$REPO" >/dev/null
fi

aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$registry"
docker build --build-arg "BUILD_REVISION=${REV}" -t "$image_uri" .
docker push "$image_uri"

export AWS_REGION="$REGION"
bash "$SCRIPT_DIR/ssm-deploy.sh" "$image_uri" "$REV"
echo "배포 요청 완료. 사용자 주소의 /health 에서 build_revision=${REV} 인지 확인하세요."
