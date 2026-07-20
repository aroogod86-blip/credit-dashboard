# -*- coding: utf-8 -*-
"""
diagnose_benchmark_stress_breakdown.py
-----------------------------------------
LQD와 LUACOAS의 스트레스 구간(STRESS_PERIODS) 변동성을 하위 구간별로 쪼개서 비교.
LQD가 2021~2022(금리인상기)에 유독 크게 튀는지, LUACOAS는 더 안정적인지 확인용.

사용법:
    python diagnose_benchmark_stress_breakdown.py --input "8874잔고_260709.xlsx"
"""
import argparse

import numpy as np
import pandas as pd

import compute_credit_spread_volatility as core


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', required=True)
    args = parser.parse_args()

    tagged = core.load_and_tag(args.input)
    extra = core.load_extra_instruments('extra_instruments.csv')
    common_cols = ['ISIN', 'TICKER', '등급', '섹터', 'GT텀', '만기구간', '지역그룹', '자산유형', '출처']
    combined = pd.concat(
        [tagged.reindex(columns=common_cols), extra.reindex(columns=common_cols)],
        ignore_index=True
    )

    print("[1/1] LQD, LUACOAS 히스토리 조회...")
    subset = combined[combined['ISIN'].isin(['LQD', 'LUACOAS'])]
    history = core.fetch_historical_spreads(subset, lookback_days=core.LOOKBACK_DAYS)

    print()
    for isin in ['LQD', 'LUACOAS']:
        series = history.get(isin)
        if series is None:
            print(f"{isin}: 히스토리 없음")
            continue
        changes = core._clean_changes_for_sigma(series)
        print(f"=== {isin} ===")
        print(f"  전체기간 std: {changes.std():.3f} bp (n={len(changes)})")
        for start, end in core.STRESS_PERIODS:
            sub = changes[(changes.index >= pd.Timestamp(start)) & (changes.index <= pd.Timestamp(end))]
            if len(sub) >= 2:
                print(f"  [{start} ~ {end}] std: {sub.std():.3f} bp (n={len(sub)})")
            else:
                print(f"  [{start} ~ {end}] 데이터 부족 (n={len(sub)})")
        print()


if __name__ == '__main__':
    main()
