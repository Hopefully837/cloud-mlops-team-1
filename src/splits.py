"""학습 데이터 읽기와 학습/검증/평가 분할. train.py·replay_feedback.py·logs_to_training.py·eval_deployed.py가 같은 분할을 쓴다.

분할을 한 곳에 두는 이유: 로그에서 모은 자료가 평가용과 겹치는지 가려내고, 재학습 전후를 같은 평가용 행으로 비교하려면
어느 도구에서나 분할이 똑같이 나와야 하기 때문이다.
"""
import pandas as pd
from sklearn.model_selection import train_test_split

TARGET = 'Rented Bike Count'
SEED = 42  # 누가 실행해도 같은 분할·모델이 나오도록 고정


def load(path):
    df = pd.read_csv(path, encoding='utf-8')
    date = pd.to_datetime(df['Date'], format='%d/%m/%Y')
    df['is_weekend'] = (date.dt.dayofweek >= 5).astype(int)  # 토·일=1
    df['month'] = date.dt.month  # 1~12
    df['dow'] = date.dt.dayofweek  # 요일: 월=0 ~ 일=6
    df['Holiday'] = (df['Holiday'] == 'Holiday').astype(int)  # Holiday=1, No Holiday=0
    df['is_offday'] = (df['is_weekend'] | df['Holiday']).astype(int)  # 쉬는 날 = 주말 또는 공휴일
    return df


def split(df):
    """1차: 평가용 20%를 먼저 떼어 둔다 → 2차: 나머지 80%를 학습 75% / 검증 25%로 나눈다 (학습 60% / 검증 20% / 평가 20%)."""
    rest, test = train_test_split(df, test_size=0.2, random_state=SEED)
    train, val = train_test_split(rest, test_size=0.25, random_state=SEED)
    return train, val, test


def slot_key(date, hour):
    """한 시간 칸의 식별자. 원본 자료와 로그가 같은 시간을 가리키는지 비교할 때 쓴다 (date: YYYY-MM-DD)."""
    return f'{date}#{int(hour)}'


def slot_keys(df):
    """load()로 읽은 자료의 각 행을 slot_key로 바꾼다 (원본 Date는 dd/mm/yyyy)."""
    iso = pd.to_datetime(df['Date'], format='%d/%m/%Y').dt.strftime('%Y-%m-%d')
    return [slot_key(d, h) for d, h in zip(iso, df['Hour'])]
