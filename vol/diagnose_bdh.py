# -*- coding: utf-8 -*-
"""
BDH 리턴값 구조 진단용 스크립트.
실제 xbbg 환경에서 blp.bdh()가 어떤 타입/구조로 데이터를 주는지 확인합니다.
"""
from xbbg import blp
from datetime import datetime, timedelta

end_date = datetime.today()
start_date = end_date - timedelta(days=60)

ticker = "US302154DM88 Corp"  # 테스트용 ISIN 1개 (EIBKOR)
field = "YAS_YLD_SPREAD"

print("=== BDH 호출 ===")
raw = blp.bdh(
    tickers=[ticker],
    flds=[field],
    start_date=start_date.strftime('%Y-%m-%d'),
    end_date=end_date.strftime('%Y-%m-%d'),
)

print("type(raw):", type(raw))
print()
print("dir(raw) 중 유용해 보이는 속성/메서드:")
for attr in dir(raw):
    if not attr.startswith('_') or attr in ('_coerce_to_pandas',):
        pass
print([a for a in dir(raw) if 'pandas' in a.lower() or 'to_' in a.lower() or 'native' in a.lower()])
print()
print("raw repr (앞부분):")
try:
    print(repr(raw)[:2000])
except Exception as e:
    print("repr 실패:", e)
print()
print("raw 자체를 print:")
try:
    print(raw)
except Exception as e:
    print("print 실패:", e)

print()
print("=== columns 확인 시도 ===")
try:
    print("columns:", raw.columns)
except Exception as e:
    print("columns 접근 실패:", e)

print()
print("=== isinstance 체크 ===")
import pandas as pd
print("is pd.DataFrame:", isinstance(raw, pd.DataFrame))
print("has _coerce_to_pandas:", hasattr(raw, '_coerce_to_pandas'))
print("has to_pandas:", hasattr(raw, 'to_pandas'))
print("has to_native:", hasattr(raw, 'to_native'))
