# -*- coding: utf-8 -*-
"""
compare_isins.py
-------------------
credit_spread_volatility.xlsx(또는 json)에서 특정 ISIN들의 모든 컬럼을
나란히 비교해서 왜 값이 다른지 확인.

사용법:
    python compare_isins.py --file credit_spread_volatility.json --isins USY5S5CGAR36,USY5S5CGAV48
"""
import argparse
import json

import pandas as pd


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--file', required=True, help='credit_spread_volatility.json 또는 .xlsx')
    parser.add_argument('--isins', required=True, help='비교할 ISIN 콤마구분')
    args = parser.parse_args()

    isins = [i.strip() for i in args.isins.split(',')]

    if args.file.endswith('.json'):
        with open(args.file, 'r', encoding='utf-8') as f:
            df = pd.DataFrame(json.load(f))
    else:
        df = pd.read_excel(args.file)

    sub = df[df['ISIN'].isin(isins)]
    if sub.empty:
        print("해당 ISIN을 찾을 수 없습니다.")
        return

    # 세로로 길게(transpose) 나란히 비교하는 게 컬럼 많을 때 보기 편함
    display_df = sub.set_index('ISIN').T
    pd.set_option('display.max_rows', None)
    pd.set_option('display.width', 200)
    print(display_df.to_string())


if __name__ == '__main__':
    main()
