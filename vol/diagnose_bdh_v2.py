# -*- coding: utf-8 -*-
"""
BDH 필드 가용성 + 기간 진단용 스크립트 v2
"""
from xbbg import blp
from datetime import datetime, timedelta

ticker = "US302154DM88 Corp"  # EIBKOR

print("=== 0. BDP로 현재값 확인 (필드 존재 여부 체크) ===")
for fld in ["YAS_YLD_SPREAD", "YAS_BOND_YLD", "YAS_ASW_SPREAD", "YAS_ISPRD_MID", "PX_LAST"]:
    try:
        r = blp.bdp(tickers=[ticker], flds=[fld])
        if hasattr(r, "to_pandas"):
            r = r.to_pandas()
        print(f"  {fld}: ")
        print(r)
    except Exception as e:
        print(f"  {fld}: 에러 -> {e}")
    print()

print("=== 1. BDH 60일 vs 260일 vs 520일 비교 (YAS_YLD_SPREAD) ===")
end_date = datetime.today()
for days in [60, 260, 520]:
    start_date = end_date - timedelta(days=days)
    try:
        raw = blp.bdh(
            tickers=[ticker],
            flds=["YAS_YLD_SPREAD"],
            start_date=start_date.strftime('%Y-%m-%d'),
            end_date=end_date.strftime('%Y-%m-%d'),
        )
        if hasattr(raw, "to_pandas"):
            raw = raw.to_pandas()
        print(f"  {days}일 조회 -> row count: {len(raw)}")
        if len(raw) > 0:
            print(raw.head(5))
    except Exception as e:
        print(f"  {days}일 조회 에러 -> {e}")
    print()

print("=== 2. 다른 종목(US bond, 유동성 높은)으로 테스트: BAC (US06051GNA30) ===")
ticker2 = "US06051GNA30 Corp"
try:
    raw2 = blp.bdh(
        tickers=[ticker2],
        flds=["YAS_YLD_SPREAD"],
        start_date=(end_date - timedelta(days=260)).strftime('%Y-%m-%d'),
        end_date=end_date.strftime('%Y-%m-%d'),
    )
    if hasattr(raw2, "to_pandas"):
        raw2 = raw2.to_pandas()
    print("row count:", len(raw2))
    print(raw2.head(10))
except Exception as e:
    print("에러:", e)
