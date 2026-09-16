## Label
- Rented Bike Count: 시간당 대여된 자전거 수 (정수, 회귀 목표)

## Feature
- Hour: 시간(0~23)
- Temperature(°C): 기온
- Humidity(%): 습도
- Rainfall(mm): 강수량
- Snowfall (cm): 적설량
- Seasons: 계절
- Holiday: 공휴일 여부
- Functioning Day: 대여소 운영 여부

## 확인된 이슈
- 중복 후보 1행 있음 (Date 컬럼이 없어 진짜 중복인지 확인 불가)
- Functioning Day=No인 295행은 항상 대여량 0 → 학습 포함 여부 논의 필요
