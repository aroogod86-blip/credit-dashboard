# -*- coding: utf-8 -*-
"""
diagnose_ticker_mismatch.py
------------------------------
티커별(발행자) Delta 합계와 종목별(ISIN) Delta 합계가 안 맞을 때,
공백/표기 차이로 같은 발행자가 다른 티커로 쪼개져 있는지 확인하는 진단 스크립트.

사용법:
    python diagnose_ticker_mismatch.py --input "8874잔고_260709.xlsx" --ticker MS
"""
import argparse
import merge_delta_with_vol as merge


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', required=True)
    parser.add_argument('--ticker', required=True, help='확인할 티커 (예: MS)')
    args = parser.parse_args()

    lots = merge.load_lots(args.input)

    print(f"\n=== '{args.ticker}' 포함된 모든 티커 변형 확인 (repr로 공백까지 표시) ===")
    matches = lots[lots['TICKER'].str.upper().str.strip() == args.ticker.upper()]
    all_variants = lots[lots['TICKER'].str.upper().str.contains(args.ticker.upper(), na=False)]

    print("\n-- 정확히 일치(대소문자/공백 무시)하는 티커 변형들 --")
    for variant in all_variants['TICKER'].unique():
        sub = lots[lots['TICKER'] == variant]
        print(f"  TICKER={variant!r} (len={len(variant)}) -> {len(sub)}개 lot, "
              f"Delta 합={sub['Credit_Delta'].sum():,.0f}")

    print(f"\n-- '{args.ticker}' 대소문자/공백 무시 기준 전체 합 --")
    print(f"  {len(matches)}개 lot, Delta 합계 = {matches['Credit_Delta'].sum():,.0f}")

    print(f"\n-- ISIN별 상세 --")
    print(matches[['ISIN', 'TICKER', 'Credit_Delta']].to_string())


if __name__ == '__main__':
    main()
