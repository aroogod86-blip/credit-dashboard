# -*- coding: utf-8 -*-
"""
fetch_hanfgi_dv01.py
-----------------------
cds 시트에서 DUR/dv01이 비어있는 포지션(예: HANFGI 자사채)을 찾아서,
Bloomberg BDP로 duration/price를 받아 근사 dv01(CS01)을 계산한다.

근사식:
    dv01 ≈ ModDuration x (Price/100) x Notional x 0.0001

이건 CDSW 정밀 계산이 아니라 채권 DV01 공식을 이용한 근사치입니다.
정확한 CDS CS01이 필요하면 Bloomberg CDSW 화면에서 직접 계산하시는 게 맞습니다.

사용법:
    python fetch_hanfgi_dv01.py --input "8874잔고_260709.xlsx"
"""

import argparse

import pandas as pd


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', required=True)
    args = parser.parse_args()

    from xbbg import blp

    raw = pd.read_excel(args.input, sheet_name='cds', header=None)
    data = raw.iloc[1:].copy()
    data.columns = ['DUR', '종목코드', '종목명', 'G_SPREAD', 'I_SPREAD', '수량', 'dv01']
    data = data[data['종목코드'].notna()].copy()
    data = data[data['종목코드'] != '종목코드'].copy()
    data['dv01_num'] = pd.to_numeric(data['dv01'], errors='coerce')

    missing = data[data['dv01_num'].isna()]
    if missing.empty:
        print("dv01이 비어있는 포지션이 없습니다.")
        return

    print(f"[1/2] dv01 없는 포지션 {len(missing)}건 확인:")
    for _, r in missing.iterrows():
        print(f"  - {r['종목코드']} ({r['종목명']}) 수량={r['수량']:,.0f}")

    print("\n[2/2] Bloomberg BDP로 duration/price 조회 및 근사 dv01 계산...")
    tickers = [f"{isin} Corp" for isin in missing['종목코드']]

    fields = ['YAS_MOD_DUR', 'PX_LAST', 'YAS_YLD_SPREAD', 'YAS_BOND_YLD']
    try:
        raw_bdp = blp.bdp(tickers=tickers, flds=fields)
        if hasattr(raw_bdp, "to_pandas"):
            raw_bdp = raw_bdp.to_pandas()
    except Exception as e:
        print(f"[ERROR] BDP 조회 실패: {e}")
        return

    print("\nBDP 원본 결과:")
    print(raw_bdp.to_string())

    # 이전 진단에서 확인된 tidy 포맷(ticker, field, value) 기준으로 파싱
    cols_lower = {c.lower(): c for c in raw_bdp.columns}
    is_tidy = {'ticker', 'field', 'value'}.issubset(cols_lower.keys())

    results = []
    for _, row in missing.iterrows():
        isin = row['종목코드']
        ticker = f"{isin} Corp"
        notional = row['수량']

        dur, price = None, None
        if is_tidy:
            sub = raw_bdp[raw_bdp[cols_lower['ticker']] == ticker]
            dur_row = sub[sub[cols_lower['field']] == 'YAS_MOD_DUR']
            price_row = sub[sub[cols_lower['field']] == 'PX_LAST']
            dur = dur_row[cols_lower['value']].iloc[0] if not dur_row.empty else None
            price = price_row[cols_lower['value']].iloc[0] if not price_row.empty else None
        else:
            try:
                if ticker in raw_bdp.index:
                    dur = raw_bdp.loc[ticker, 'YAS_MOD_DUR']
                    price = raw_bdp.loc[ticker, 'PX_LAST']
            except Exception:
                pass

        if dur is None or price is None:
            print(f"  [WARN] {isin}: duration/price를 못 찾았습니다. 위 BDP 원본 결과를 보고 수동 확인 필요.")
            continue

        approx_dv01 = dur * (price / 100.0) * notional * 0.0001
        results.append({
            '종목코드': isin,
            '종목명': row['종목명'],
            'DUR(근사)': round(dur, 4),
            'Price': round(price, 3),
            '수량': notional,
            '근사dv01': round(approx_dv01, 2),
        })

    if results:
        out = pd.DataFrame(results)
        print("\n=== 근사 결과 (엑셀 cds 시트에 직접 입력하세요) ===")
        print(out.to_string(index=False))
        out.to_csv('hanfgi_dv01_estimate.csv', index=False, encoding='utf-8-sig')
        print("\n저장: hanfgi_dv01_estimate.csv")
    else:
        print("\n계산된 결과가 없습니다 - 위 BDP 원본 출력을 참고해 필드/포맷을 확인해주세요.")


if __name__ == '__main__':
    main()
