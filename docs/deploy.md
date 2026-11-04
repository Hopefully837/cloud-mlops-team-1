# 배포 구성 (EC2 + Docker)

따릉이 예측 API(`src/api.py`)를 컨테이너로 만들어 EC2 한 대에 배포한다. 코드 버전은 커밋 번호로 구분하고, 배포가 끝나면 `GET /health`의 `build_revision`이 GitHub 브랜치의 최신 커밋과 같은지로 새 버전이 답하는지 확인한다.

## 1. EC2와 ECS 중 선택

| 기준 | EC2 + Docker | ECS(Fargate) |
|---|---|---|
| 준비할 것 | EC2 1대, Docker, 보안 그룹, 인스턴스 역할 | 클러스터·작업 정의·서비스·네트워크(VPC·서브넷)·실행 역할, 외부 공개에는 ALB |
| 필요한 권한 | EC2 생성, (선택) 인스턴스 프로필 | 위에 더해 ECS·IAM `PassRole`·ELB·CloudFormation 등 |
| 되돌릴 값 | 이전 이미지 태그 | 이전 작업 정의 번호 |
| 우리 서비스에 맞는가 | 요청이 적고 1대로 충분. 예측 기록은 로그로 CloudWatch에 남음 | 여러 복제본·무중단 교체가 필요할 때 |

**선택: EC2 + Docker.** 이유는 (1) 트래픽이 작은 수업용 서비스라 복제본이 필요 없고, (2) 공유 수업 계정에서 EC2 생성은 가능하지만 ECS 역할·네트워크를 새로 만들 권한은 확인되지 않았고, (3) 되돌리기가 이미지 태그 하나로 단순하기 때문이다. 복제본이 필요해지면 ECS로 옮긴다(같은 이미지를 쓴다).

## 2. 이 계정에서 확인한 권한 (CloudShell, 서울)

| 항목 | 결과 |
|---|---|
| EC2 생성(`run-instances --dry-run`) | 가능 |
| SSM에 등록된 인스턴스 | 없음 |
| ECR 저장소 | 우리 팀 저장소 없음(다른 팀 것만 있음) |
| 쓸 수 있는 인스턴스 프로필 | `yeonsung-ec2-cloudwatch-role` (정책 `CloudWatchAgentServerPolicy`: SSM·ECR 권한 없음) |

그래서 **ECR 이미지 + SSM 원격 실행 방식은 쓰지 않고**, EC2가 GitHub 저장소를 직접 받아 빌드하는 방식을 기본 경로로 쓴다. 이 계정은 수업 전체가 함께 쓰므로 우리가 만든 자원에는 `Name=bike-api-team1`, `Project=cloud-mlops-team1` 태그를 붙이고 다른 팀의 자원은 건드리지 않는다.

## 3. 구성도 (기본 경로)

```
개발자 ── PR ──▶ GitHub (main)
                     ▲  5분마다 최신 커밋 확인 (git ls-remote)
EC2 (t3.micro, x86_64, 인스턴스 프로필: CloudWatch 로그 쓰기)
  ├─ systemd timer ─▶ scripts/update-on-ec2.sh
  │      최신 커밋 == 실행 중인 /health.build_revision ?  ── 같으면 아무것도 안 함
  │      다르면: git fetch → docker build(BUILD_REVISION=커밋) → 컨테이너 교체
  │             → /health 가 새 커밋으로 답하지 않으면 이전 이미지로 복구하고 실패 커밋을 기록
  └─ docker: bike-api 컨테이너 :8000 ── 예측·피드백 JSON 로그 ──▶ CloudWatch Logs ──(내보내기)──▶ S3
사용자 PC ──▶ http://<EC2 공인 IP>:8000/   (보안 그룹: 내 IP/32 만 허용)
```

## 4. 배포 절차 (AWS CloudShell, 서울 리전)

1. 코드를 GitHub 브랜치에 올린다(EC2가 이 브랜치를 받는다).
2. 내 PC 브라우저에서 https://checkip.amazonaws.com 으로 공인 IP를 확인한다.
3. CloudShell에서 저장소를 받아 실행한다. (`<IP>`와 브랜치를 바꾼다)
   ```sh
   git clone -b <브랜치> https://github.com/<소유자>/cloud-mlops-team-1.git && cd cloud-mlops-team-1
   ALLOWED_CIDR=<IP>/32 bash scripts/deploy-aws-cli.sh
   ```
   스크립트는 계정·리전·대상을 보여 주고 10초 기다린다. 같은 이름의 인스턴스가 이미 있으면 새로 만들지 않는다.
4. 3~6분 뒤 `http://<공인 IP>:8000/health`에서 `build_revision`이 브랜치의 최신 커밋과 같은지, 화면에서 예측이 나오는지, `python src/check_api.py --url http://<공인 IP>:8000`의 9개 케이스가 기대대로인지 확인한다.
5. 끝나면 `bash scripts/teardown-aws-cli.sh`로 인스턴스와 보안 그룹을 정리한다(태그가 붙은 우리 자원만 지운다).

## 5. 갱신과 중복 배포 방지

- main(또는 지정한 브랜치)에 새 커밋이 합쳐지면 최대 5분 안에 EC2가 스스로 새 버전으로 바꾼다.
- 최신 커밋이 이미 실행 중이면 **빌드도 교체도 하지 않는다.** 같은 커밋으로 몇 번을 확인해도 배포는 한 번이다.
- 새 버전이 60초 안에 `/health`에 응답하지 않으면 이전 이미지로 되돌리고, 그 커밋은 실패로 기록해 다음 주기에 다시 빌드하지 않는다. 새 커밋이 올라오면 다시 시도한다.
- 실행이 겹치지 않도록 `flock`으로 한 번에 하나만 돈다.
- 판단 로직(최초 배포, 같은 커밋 건너뜀, 새 커밋 교체, 실패 시 복구, 실패 커밋 재시도 안 함, 수정 커밋 배포)은 가짜 `docker`·`git`·`curl`을 끼워 넣은 시험으로 확인했다. 실제 Docker 빌드와 EC2 부팅은 별도로 확인해야 한다.

## 6. 되돌리기

- 자동: 위 5절의 복구. 로그는 `journalctl -u bike-api-update`, 부팅 로그는 `/var/log/bike-api-bootstrap.log`(CloudShell에서는 `aws ec2 get-console-output --latest`).
- 수동으로 특정 커밋으로 돌리려면 EC2에서 `docker run`으로 이전 이미지(`bike-api:<이전 커밋>`)를 다시 띄운다. 이미지는 커밋 번호 태그로 남아 있다.
- 로컬·개발용은 `docker-compose.yml`(`BUILD_REVISION=$(git rev-parse --short HEAD) docker compose up -d --build`)을 쓴다.

## 7. 권한이 더 있을 때의 확장 경로 (현재는 사용하지 않음)

IAM 권한이 있는 담당자가 아래를 준비해 주면 GitHub Actions로 이미지를 ECR에 올리고 SSM으로 교체할 수 있다. 준비되지 않으면 `release` job은 `ENABLE_DEPLOY`가 `true`가 아니므로 건너뛴다. PR에서는 `check`(정상·오류 요청 검사와 컨테이너 빌드·헬스체크)만 돈다.

| 자원 | 내용 |
|---|---|
| ECR 저장소 | `bike-api` |
| EC2 인스턴스 프로필 | SSM 관리·ECR 읽기·CloudWatch Logs 쓰기 권한 |
| GitHub OIDC 역할 | 이 저장소의 `main`만 맡을 수 있는 신뢰 정책 + ECR push·SSM `SendCommand` |
| 저장소 변수 | `ENABLE_DEPLOY=true`, `AWS_ROLE_ARN`, (선택) `AWS_REGION`, `ECR_REPOSITORY`, `EC2_TAG_NAME` |

관련 파일: `.github/workflows/deploy.yml`, `scripts/ssm-deploy.sh`, `scripts/deploy-ec2.sh`.

## 8. 비용·정리

- `t3.micro`는 켜 둔 시간만큼 비용이 든다. 실습이 끝나면 `scripts/teardown-aws-cli.sh`로 종료한다. 중지만 해도 EBS 비용이 남는다.
- 8000번은 내 IP/32만 열었다. 공인 IP가 바뀌면 보안 그룹의 허용 주소를 갱신해야 한다.
- CloudWatch 로그 그룹(`/mlops/team1-bike-rental`)과 S3 객체는 teardown이 지우지 않는다. 필요하면 따로 정리한다.
