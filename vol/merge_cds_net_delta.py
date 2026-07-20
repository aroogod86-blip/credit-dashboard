# -*- coding: utf-8 -*-
"""
merge_cds_net_delta.py
------------------------
8874잔고 파일의 'cds' 시트(준거채권 CDS 포지션)를 읽어,
같은 파일의 채권 포지션(Credit Delta)과 이슈어(티커) 단위로 넷팅한다.

부호 컨벤션:
    - 채권 Credit Delta: 스프레드 확대 시 손실 -> 보통 음수
    - CDS 프로텍션 매수(헤지): 스프레드 확대 시 이익 -> +dv01 로 반영 (채권과 반대부호)
      * 프로텍션 매도 포지션이 섞여 있으면 해당 행은 부호를 반대로(-dv01) 바꿔야 함.
        지금은 전량 프로텍션 매수로 가정. --sell-tickers 옵션으로 예외 지정 가능.

넷 리스크 = (채권 Delta 합 + CDS Delta 합) x sigma  (이슈어/티커 단위)

사용법:
    python merge_cds_net_delta.py \
        --input "8874잔고_260709.xlsx" \
        --sigma-cache credit_spread_volatility.json \
        --output cds_net_risk.csv \
        --sell-tickers KDB,POHANG   # (선택) 프로텍션 매도로 취급할 티커
"""

import argparse
import json
import re

import numpy as np
import pandas as pd

import compute_credit_spread_volatility as core
import merge_delta_with_vol as merge


# 종목명이 한글/특수 케이스라 첫 단어 추출로 안 되는 것들 수동 매핑
MANUAL_TICKER_MAP = {
    '현대카드': 'HYNCRD',
}


def extract_ticker(bond_name: str) -> str:
    bond_name = str(bond_name).strip()
    for kr_name, ticker in MANUAL_TICKER_MAP.items():
        if bond_name.startswith(kr_name):
            return ticker
    # 첫 토큰(공백 기준) 추출 후 대문자화, 숫자/특수문자 섞인 경우 알파벳만
    first_token = bond_name.split()[0]
    alpha_only = re.sub(r'[^A-Za-z]', '', first_token)
    return alpha_only.upper()


def load_etf_delta_positions(xlsx_path: str) -> pd.DataFrame:
    """
    cds 시트 하단의 ETF Dv01 블록(전치된 레이아웃: 행=항목, 열=티커)을 파싱.
    예:
        NY기준(bp)   LQD        VCIT
        액면금액      ...        ...
        수량㈜        ...        ...
        Dv01($)      -40056.14  -50348.53
        선물수량      ...        ...
    반환: DataFrame[TICKER, ISIN(=TICKER), Delta] (Delta = Dv01($) 그대로, $/bp)
    """
    raw = pd.read_excel(xlsx_path, sheet_name='cds', header=None)

    header_row_idx = None
    for i in range(len(raw)):
        row = raw.iloc[i]
        if str(row.iloc[0]).strip() in ('NY기준(bp)', 'NY기준', 'ETF'):
            header_row_idx = i
            break
    if header_row_idx is None:
        return pd.DataFrame(columns=['TICKER', 'ISIN', 'Delta'])

    header = raw.iloc[header_row_idx]
    tickers = [str(v).strip() for v in header.iloc[1:] if pd.notna(v)]

    dv01_row = None
    for i in range(header_row_idx + 1, min(header_row_idx + 8, len(raw))):
        label = str(raw.iloc[i, 0]).strip()
        if 'dv01' in label.lower():
            dv01_row = raw.iloc[i]
            break
    if dv01_row is None:
        return pd.DataFrame(columns=['TICKER', 'ISIN', 'Delta'])

    rows = []
    for j, ticker in enumerate(tickers, start=1):
        delta = pd.to_numeric(dv01_row.iloc[j], errors='coerce')
        if pd.notna(delta):
            rows.append({'TICKER': ticker, 'ISIN': ticker, 'Delta': delta})

    return pd.DataFrame(rows)


def load_cds_positions(xlsx_path: str, sell_tickers: set) -> pd.DataFrame:
    raw = pd.read_excel(xlsx_path, sheet_name='cds', header=None)

    # ETF Dv01 블록(전치 레이아웃, 'NY기준(bp)' 헤더로 시작)이 있으면 그 이전까지만 CDS로 파싱
    etf_header_idx = None
    for i in range(len(raw)):
        if str(raw.iloc[i, 0]).strip() in ('NY기준(bp)', 'NY기준', 'ETF'):
            etf_header_idx = i
            break
    data = raw.iloc[1:etf_header_idx].copy() if etf_header_idx is not None else raw.iloc[1:].copy()

    data.columns = ['DUR', '종목코드', '종목명', 'G_SPREAD', 'I_SPREAD', '수량', 'dv01']

    # 시트 중간에 헤더가 반복되거나(예: 새 블록 추가 시) 빈 줄이 있을 수 있어 제거
    data = data[data['종목코드'].notna()].copy()
    data = data[data['종목코드'] != '종목코드'].copy()

    data['TICKER'] = data['종목명'].apply(extract_ticker)
    data['dv01'] = pd.to_numeric(data['dv01'], errors='coerce')

    missing_dv01 = data[data['dv01'].isna()]
    if not missing_dv01.empty:
        print(f"  [WARN] dv01 없는 CDS 포지션 {len(missing_dv01)}건 - 리스크 계산에서 제외됩니다:")
        for _, r in missing_dv01.iterrows():
            print(f"    - {r['종목코드']} ({r['종목명']})")
    data = data[data['dv01'].notna()].copy()

    # 프로텍션 매수 = +dv01 (채권과 반대방향), 매도로 지정된 티커만 부호 반전
    data['CDS_Delta'] = data.apply(
        lambda r: -r['dv01'] if r['TICKER'] in sell_tickers else r['dv01'], axis=1
    )
    return data


def build_ticker_sigma_map(sigma_df: pd.DataFrame) -> dict:
    """캐시에 있는 종목들의 sigma를 티커 단위로 평균"""
    grp = sigma_df.dropna(subset=['최종sigma_bp_5Y']).groupby('TICKER')['최종sigma_bp_5Y'].mean()
    return grp.to_dict()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', required=True, help='잔고 xlsx (cds 시트 포함)')
    parser.add_argument('--sigma-cache', required=True)
    parser.add_argument('--output', default='cds_net_risk.csv')
    parser.add_argument('--sell-tickers', default='',
                         help='프로텍션 매도로 취급할 티커 (콤마구분, 예: KDB,POHANG)')
    args = parser.parse_args()

    sell_tickers = set(t.strip().upper() for t in args.sell_tickers.split(',') if t.strip())

    print("[1/4] 채권 lot 로드 및 티커별 Credit Delta 합산...")
    lots = merge.load_lots(args.input)
    bond_delta_by_ticker = lots.groupby('TICKER')['Credit_Delta'].sum().to_dict()
    print(f"  -> {len(bond_delta_by_ticker)}개 티커")

    print("[2/4] CDS 포지션 로드 (cds 시트)...")
    cds = load_cds_positions(args.input, sell_tickers)
    cds_delta_by_ticker = cds.groupby('TICKER')['CDS_Delta'].sum().to_dict()
    print(f"  -> {len(cds)}개 CDS 포지션, {len(cds_delta_by_ticker)}개 티커")
    if sell_tickers:
        print(f"  -> 프로텍션 매도로 취급된 티커: {sell_tickers}")

    print("[3/4] sigma 캐시 로드 및 티커별 매핑...")
    sigma_df = merge.load_sigma_cache(args.sigma_cache)
    ticker_sigma = build_ticker_sigma_map(sigma_df)
    global_avg_sigma = sigma_df['최종sigma_bp_5Y'].dropna().mean()

    print("[4/4] 넷 델타 및 리스크 계산...")
    all_tickers = sorted(set(list(bond_delta_by_ticker.keys()) + list(cds_delta_by_ticker.keys())))

    rows = []
    for t in all_tickers:
        bond_d = bond_delta_by_ticker.get(t, 0.0)
        cds_d = cds_delta_by_ticker.get(t, 0.0)
        if cds_d == 0.0 and t not in cds_delta_by_ticker:
            continue  # CDS 없는 순수 채권만 있는 티커는 이 리포트에서 제외 (넷팅 의미 없음)

        net_d = bond_d + cds_d
        sigma = ticker_sigma.get(t)
        sigma_source = 'ticker_avg_cache'
        if sigma is None or (isinstance(sigma, float) and np.isnan(sigma)):
            sigma = global_avg_sigma
            sigma_source = 'global_fallback'

        bond_only_risk = bond_d * sigma
        net_risk = net_d * sigma
        hedge_effect = abs(bond_only_risk) - abs(net_risk)  # 양수면 헤지로 리스크 감소

        rows.append({
            'TICKER': t,
            '채권_Delta_$per bp': round(bond_d, 0),
            'CDS_Delta_$per bp': round(cds_d, 0),
            '넷_Delta_$per bp': round(net_d, 0),
            'sigma_bp_5Y': round(sigma, 3) if sigma is not None else None,
            'sigma출처': sigma_source,
            '채권단독_1시그마리스크_$': round(bond_only_risk, 0),
            '넷_1시그마리스크_$': round(net_risk, 0),
            '헤지효과_$': round(hedge_effect, 0),
        })

    result = pd.DataFrame(rows).sort_values('헤지효과_$', ascending=False)
    result.to_csv(args.output, index=False, encoding='utf-8-sig')

    print()
    print(result.to_string(index=False))
    print()
    print(f"채권단독 합계(CDS 있는 티커만): {result['채권단독_1시그마리스크_$'].sum():,.0f}")
    print(f"넷(CDS 반영) 합계: {result['넷_1시그마리스크_$'].sum():,.0f}")
    print(f"헤지로 줄어든 리스크: {result['헤지효과_$'].sum():,.0f}")
    print()
    print(f"저장 완료: {args.output}")


if __name__ == '__main__':
    main()
