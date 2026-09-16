# 학습용 데이터 설명

## 1. 데이터 개요

이 데이터는 시간과 날씨 등의 정보를 이용하여 자전거 대여량을 예측하기 위한 데이터이다.

주요 데이터는 시간, 기온, 습도, 강수량, 적설량, 계절, 공휴일 여부, 운영 여부 등으로 구성되어 있다.

## 2. Feature와 Label

### Feature

Feature는 머신러닝 모델이 예측을 수행할 때 사용하는 입력 데이터이다.

이 데이터에서 Feature에 해당하는 항목은 다음과 같다.

- Hour: 시간
- Temperature: 기온
- Humidity: 습도
- Rainfall: 강수량
- Snowfall: 적설량
- Seasons: 계절
- Holiday: 공휴일 여부
- Functioning Day: 자전거 대여 시스템 운영 여부

### Label

Label은 머신러닝 모델이 예측하고자 하는 값이다.

이 데이터에서는 `Rented Bike Count`가 Label이다.

- Rented Bike Count: 해당 시간에 대여된 자전거 수

즉, 시간과 날씨 등의 Feature를 이용하여 자전거 대여량인 Label을 예측하는 것이 이 데이터의 목적이다.

## 3. 데이터 컬럼 설명

| 컬럼 | 설명 | 역할 |
|---|---|---|
| Rented Bike Count | 해당 시간의 자전거 대여 수 | Label |
| Hour | 시간 | Feature |
| Temperature | 기온(℃) | Feature |
| Humidity | 습도(%) | Feature |
| Rainfall | 강수량(mm) | Feature |
| Snowfall | 적설량(cm) | Feature |
| Seasons | 계절 | Feature |
| Holiday | 공휴일 여부 | Feature |
| Functioning Day | 운영 여부 | Feature |

## 4. 예시

다음 데이터가 있다고 가정한다.

- Hour: 8
- Temperature: -7.6℃
- Humidity: 37%
- Rainfall: 0mm
- Snowfall: 0cm
- Seasons: Winter
- Holiday: No Holiday
- Functioning Day: Yes

이러한 Feature를 바탕으로 해당 시간의 `Rented Bike Count`를 예측할 수 있다.

실제 데이터에서는 위 조건에서 자전거 대여량이 930이었다.
