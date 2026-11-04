#!/usr/bin/env bash
# EC2 첫 부팅 때 user-data로 실행된다(root, Amazon Linux 2023). scripts/deploy-aws-cli.sh가 앞에 환경 변수를 붙여 넘긴다.
#   필요: REPO_URL   선택: BRANCH(기본 main), CLOUDWATCH_LOG_GROUP, CLOUDWATCH_LOG_STREAM, AWS_REGION
# 하는 일: Docker·git 설치 → 설정 파일 작성 → 5분마다 갱신하는 systemd timer 등록 → 처음 한 번 즉시 배포
# 진행 상황: /var/log/bike-api-bootstrap.log, 콘솔 출력(aws ec2 get-console-output)
set -euxo pipefail
exec > >(tee -a /var/log/bike-api-bootstrap.log /dev/console) 2>&1

: "${REPO_URL:?REPO_URL이 필요합니다}"
BRANCH="${BRANCH:-main}"

dnf install -y docker git
systemctl enable --now docker

mkdir -p /var/lib/bike-api
cat > /etc/bike-api.env <<EOF
REPO_URL=${REPO_URL}
BRANCH=${BRANCH}
CLOUDWATCH_LOG_GROUP=${CLOUDWATCH_LOG_GROUP:-}
CLOUDWATCH_LOG_STREAM=${CLOUDWATCH_LOG_STREAM:-}
AWS_REGION=${AWS_REGION:-ap-northeast-2}
EOF
chmod 600 /etc/bike-api.env

# 저장소를 먼저 받아 둔다. 갱신 스크립트는 실행할 때마다 저장소의 최신본을 /run으로 복사해서 쓴다
# (실행 중에 git이 스크립트 파일을 바꾸는 문제를 피하고, 갱신 스크립트 자체의 수정도 반영된다).
if [ ! -d /opt/bike-api/.git ]; then
  git clone --depth 1 --branch "$BRANCH" "$REPO_URL" /opt/bike-api
fi

cat > /etc/systemd/system/bike-api-update.service <<'EOF'
[Unit]
Description=bike-api 최신 커밋 확인 및 배포
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/bin/bash -c 'cp /opt/bike-api/scripts/update-on-ec2.sh /run/bike-api-update.sh && exec bash /run/bike-api-update.sh'
EOF

cat > /etc/systemd/system/bike-api-update.timer <<'EOF'
[Unit]
Description=bike-api 5분마다 최신 커밋 확인

[Timer]
OnBootSec=5min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now bike-api-update.timer
systemctl start bike-api-update.service   # 처음 한 번 즉시 빌드·실행 (완료될 때까지 기다린다)
