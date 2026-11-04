# 배포 구성 (EC2 + Docker Compose)

따릉이 예측 API(`src/api.py`)를 컨테이너로 만들어 EC2 한 대에 배포한다. 코드 버전은 커밋 번호로 구분하고, 배포가 끝나면 `GET /health`의 `build_revision`이 배포한 커밋과 같은지로 새 버전이 답하는지 확인한다.

## 1. EC2와 ECS 중 선택

| 기준 | EC2 + Docker Compose | ECS(Fargate) |
|---|---|---|
| 준비할 것 | EC2 1대, Docker, 보안 그룹, IAM 인스턴스 역할 | 클러스터·작업 정의·서비스·네트워크(VPC·서브넷)·실행 역할, 외부 공개에는 ALB |
| 팀에 필요한 권한 | 수업 계정에서 EC2·SSM·ECR 사용 권한 | 위에 더해 ECS·IAM `PassRole`·ELB·CloudFormation 등 |
| 되돌릴 값 | 이전 이미지 지문(태그) | 이전 작업 정의 번호 |
| 우리 서비스에 맞는가 | 요청이 적고 인스턴스 1대로 충분. 상태(예측 기록)는 로그로 CloudWatch에 남음 | 여러 복제본·무중단 교체가 필요할 때 |

**선택: EC2 + Docker Compose.** 이유는 (1) 트래픽이 작은 수업용 서비스라 복제본이 필요 없고, (2) 학생 계정의 권한이 제한돼 있어(MFA, 액세스 키 생성 불가) ECS의 역할·네트워크를 새로 만들기 어렵고, (3) 되돌리기가 이미지 태그 하나로 단순하기 때문이다. 여러 복제본이 필요해지면 ECS로 옮긴다(이미지는 그대로 쓸 수 있다).

## 2. 구성도

```
개발자 PC ── PR ──▶ GitHub (main)
                      │ push
                      ▼
              GitHub Actions
   check ──▶ release (main push · ENABLE_DEPLOY=true 일 때만)
                      │ ① OIDC로 AWS 역할 획득 (장기 액세스 키 없음)
                      │ ② 이미지 빌드 (build-arg BUILD_REVISION=커밋 번호)
                      │ ③ ECR에 push (태그=커밋 번호)
                      │ ④ SSM Run Command로 EC2에 scripts/deploy-ec2.sh 실행 요청
                      ▼
   EC2 (Docker, SSM Agent, 인스턴스 역할: ECR 읽기·CloudWatch Logs 쓰기)
        └─ docker: bike-api 컨테이너 :8000 (scripts/deploy-ec2.sh가 교체)
                      │ 예측·피드백 JSON 로그
                      ▼
              CloudWatch Logs ──(내보내기)──▶ S3
사용자 ──▶ http://<EC2 주소>:8000/  (또는 SSM 포트 포워딩으로 127.0.0.1:8000)
```

## 3. 준비할 자원

| 자원 | 내용 | 누가 |
|---|---|---|
| ECR 저장소 | `bike-api` (서울 `ap-northeast-2`) | 담당자 또는 `scripts/deploy-aws-cli.sh`가 없으면 생성 |
| EC2 인스턴스 | Docker·SSM Agent 설치, 태그 `Name=bike-api`, 인스턴스 역할 연결 | 담당자 |
| 인스턴스 IAM 역할 | `AmazonSSMManagedInstanceCore`, ECR 읽기(`ecr:GetAuthorizationToken`·`BatchGetImage`·`GetDownloadUrlForLayer`), CloudWatch Logs 쓰기 | 담당자 |
| 보안 그룹 | 8000번 인바운드를 필요한 범위에만 허용(전체 공개 `0.0.0.0/0`은 피하고 SSM 포트 포워딩 권장) | 담당자 |
| GitHub OIDC 역할 | GitHub 저장소·`main`만 맡을 수 있는 신뢰 정책 + ECR push·SSM `SendCommand` 권한 | **IAM 권한이 있는 담당자** |
| GitHub 변수 | `ENABLE_DEPLOY`, `AWS_ROLE_ARN`, `AWS_REGION`, `ECR_REPOSITORY`, `EC2_TAG_NAME` | 저장소 관리자 (Settings → Variables) |

권한이 없어 OIDC 역할을 만들 수 없으면 Actions의 `release`는 켜지 않고(`ENABLE_DEPLOY`를 설정하지 않으면 건너뜀) `check`만 돌린다. 이때는 CloudShell에서 `scripts/deploy-aws-cli.sh`로 한 번에 배포한다.

## 4. 배포 흐름과 검증

1. PR을 열거나 갱신하면 `check`가 돌아 정상·오류 요청(`src/check_api.py`)과 컨테이너 `/health`를 검사한다. 이 단계는 AWS 권한이 필요 없다.
2. main에 합쳐지면 다시 `check`를 돌리고, 통과하고 `ENABLE_DEPLOY=true`일 때만 `release`가 이미지를 만들어 올리고 EC2에 교체를 요청한다.
3. `scripts/deploy-ec2.sh`는 **이미 같은 `build_revision`이 실행 중이면 교체하지 않고 끝낸다**(중복 배포 방지). 같은 커밋으로 `release`가 다시 돌아도 컨테이너를 다시 만들지 않는다.
4. 교체 뒤 `/health`의 `build_revision`이 요청한 커밋과 같지 않으면 이전 이미지로 되돌리고 실패로 끝낸다.
5. 같은 `main`에 `release`가 겹쳐 돌지 않도록 workflow의 `concurrency`로 한 번에 하나만 실행한다.

## 5. 되돌리기

- 직전 이미지 태그는 `deploy-ec2.sh`가 교체 전에 출력한다. 새 버전이 `/health`를 통과하지 못하면 스크립트가 자동으로 이전 태그로 돌아간다.
- 수동 복구: EC2에서 `bash scripts/deploy-ec2.sh <ECR 주소>:<이전 커밋> <이전 커밋>`.
- 로컬·개발용은 `docker-compose.yml`(`BUILD_REVISION=$(git rev-parse --short HEAD) docker compose up -d --build`)을 쓴다. EC2는 같은 이미지를 `docker run`으로 실행한다.
- CloudShell 한 번에 배포: `bash scripts/deploy-aws-cli.sh` (ECR 확인·생성 → 빌드·push → SSM으로 EC2 교체 → 완료 확인).
- 모델 파일은 이미지 안에 있다. 모델만 바꾼 변경도 새 커밋 번호로 새 이미지를 만든다.

## 6. 정리(비용)

실습이 끝나면 EC2는 중지만 하지 말고 불필요하면 종료하고, 남는 EBS·ECR 이미지·CloudWatch 로그 그룹·S3 객체를 함께 확인한다. 중지한 EC2도 연결된 EBS 비용이 남는다.
