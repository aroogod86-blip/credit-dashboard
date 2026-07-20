# -*- coding: utf-8 -*-
"""
merge_delta_with_vol.py
------------------------
잔고파일의 Credit Delta($/bp, lot 단위)와 compute_credit_spread_volatility.py가 만든
sigma(bp)를 병합해서 "실제 스프레드 리스크"를 계산한다.
3년 기준(sigma_bp_3Y)과 스트레스 기준(sigma_bp_stress) 둘 다 계산한다.

리스크 정의:
    실제스프레드리스크_1시그마_기준($) = Credit Delta($/bp) x sigma_bp_기준(bp)

포트폴리오 합산 방식:
    - 등급 x 섹터 버킷 내에서 lot별 리스크를 그냥 합산 (버킷 내 완전상관 가정)
    - 버킷간도 단순합 (분산효과 미반영, 가장 보수적인 총 리스크)

사용법:
    python merge_delta_with_vol.py \
        --input "8874잔고_260709.xlsx" \
        --sigma-cache credit_spread_volatility.json \
        --output actual_credit_risk.json

전제조건:
    - compute_credit_spread_volatility.py 를 먼저(또는 주기적으로) 실행해서
      --sigma-cache 로 지정한 파일이 최신 상태여야 함.
    - 이 스크립트 자체는 Bloomberg 연결이 필요 없음 (캐시된 sigma + 잔고파일만 사용).
"""

import argparse
import json

import numpy as np
import pandas as pd

# compute_credit_spread_volatility.py 와 같은 폴더에 있어야 함 (섹터 매핑 등 재사용)
import compute_credit_spread_volatility as core

SIGMA_BASES = {
    '5Y': '최종sigma_bp_5Y',
    'stress': '최종sigma_bp_stress',
}


EXCLUDED_FUND_CODES = {'38088', '38038'}


def load_lots(xlsx_path: str) -> pd.DataFrame:
    """
    잔고파일을 lot 단위(중복 ISIN 포함)로 읽어서
    ISIN, TICKER, 등급, 섹터, 종목명, Credit Delta 를 뽑는다.
    (compute_credit_spread_volatility.load_and_tag()와 동일하게 UST/비USD 통화 제외,
     EXCLUDED_FUND_CODES에 해당하는 운용코드도 제외)
    """
    raw = pd.read_excel(xlsx_path, sheet_name=0, header=None)
    data = raw.iloc[3:]

    cols = {6: 'ISIN', 13: 'Credit_Delta', 21: 'TICKER', 22: '등급', 28: '운용코드', 31: '종목명', 46: '환율'}
    df = data[list(cols.keys())].rename(columns=cols)
    df = df[df['ISIN'].notna()].copy()
    df = df[df['ISIN'].astype(str).str.len() == 12].copy()

    # 특정 운용코드(예: 38088, 38038) 제외
    df['운용코드'] = df['운용코드'].astype(str).str.strip()
    n_excluded_fund = df['운용코드'].isin(EXCLUDED_FUND_CODES).sum()
    if n_excluded_fund > 0:
        df = df[~df['운용코드'].isin(EXCLUDED_FUND_CODES)].copy()
        print(f"  [INFO] 운용코드 {sorted(EXCLUDED_FUND_CODES)} 대상 {n_excluded_fund}개 lot 제외")

    # 미국 국채(UST) 제외 ('T' 티커가 AT&T 회사채와 국채를 혼용하는 경우가 있어 ISIN 기준으로 구분)
    n_ust = df['ISIN'].astype(str).apply(core.is_ust_isin).sum()
    if n_ust > 0:
        df = df[~df['ISIN'].astype(str).apply(core.is_ust_isin)].copy()
        print(f"  [INFO] 미국 국채(UST) {n_ust}개 lot 제외")

    # USD 외 통화 종목 제외
    df['환율'] = pd.to_numeric(df['환율'], errors='coerce')
    usd_rate = df['환율'].mode().iloc[0] if not df['환율'].mode().empty else None
    if usd_rate is not None:
        non_usd = df[(df['환율'] - usd_rate).abs() / usd_rate > 0.01]['ISIN'].nunique()
        df = df[(df['환율'] - usd_rate).abs() / usd_rate <= 0.01].copy()
        if non_usd > 0:
            print(f"  [INFO] 비USD 통화 종목 {non_usd}개 제외 (USD/KRW={usd_rate} 기준)")

    df['TICKER'] = df['TICKER'].astype(str).str.strip()
    df['Credit_Delta'] = pd.to_numeric(df['Credit_Delta'], errors='coerce')
    df['섹터'] = df['TICKER'].apply(
        lambda t: '금융' if t in core.FINANCIAL_TICKERS else '비금융'
    )
    return df


def load_sigma_cache(cache_path: str) -> pd.DataFrame:
    """compute_credit_spread_volatility.py의 결과(json)를 로드"""
    with open(cache_path, 'r', encoding='utf-8') as f:
        records = json.load(f)
    return pd.DataFrame(records)


def build_bucket_fallback_sigma(sigma_df: pd.DataFrame, sigma_col: str) -> dict:
    """캐시에 없는(신규) 종목을 위한 등급x섹터 평균 sigma 폴백 테이블"""
    grp = sigma_df.dropna(subset=[sigma_col]).groupby(['등급', '섹터'])[sigma_col].mean()
    return grp.to_dict()


def build_rating_fallback_sigma(sigma_df: pd.DataFrame, sigma_col: str) -> dict:
    """섹터까지도 없을 때 등급 단위 최후 폴백"""
    grp = sigma_df.dropna(subset=[sigma_col]).groupby(['등급'])[sigma_col].mean()
    return grp.to_dict()


def _resolve_sigma(isin, 등급, 섹터, sigma_map, bucket_fallback, rating_fallback, global_avg):
    sigma = sigma_map.get(isin)
    source = 'individual_cache'
    if sigma is None or (isinstance(sigma, float) and np.isnan(sigma)):
        sigma = bucket_fallback.get((등급, 섹터))
        source = 'rating_sector_fallback'
    if sigma is None or (isinstance(sigma, float) and np.isnan(sigma)):
        sigma = rating_fallback.get(등급)
        source = 'rating_fallback'
    if sigma is None or (isinstance(sigma, float) and np.isnan(sigma)):
        sigma = global_avg
        source = 'global_fallback'
    return sigma, source


RATIO_COLS = [
    'sigma비율_vs_LQD_5Y', 'sigma비율_vs_LUACOAS_5Y',
    'sigma비율_vs_LQD_stress', 'sigma비율_vs_LUACOAS_stress',
]


def merge_and_compute(lots: pd.DataFrame, sigma_df: pd.DataFrame) -> pd.DataFrame:
    """lot별로 5Y/stress 두 기준 다 계산해서 반환 + sigma비율 4가지 기반 리스크도 같이 계산"""
    resolvers = {}
    for basis, col in SIGMA_BASES.items():
        sigma_map = dict(zip(sigma_df['ISIN'], sigma_df[col]))
        bucket_fallback = build_bucket_fallback_sigma(sigma_df, col)
        rating_fallback = build_rating_fallback_sigma(sigma_df, col)
        global_avg = sigma_df[col].dropna().mean()
        resolvers[basis] = (sigma_map, bucket_fallback, rating_fallback, global_avg)

    ratio_maps = {
        col: dict(zip(sigma_df['ISIN'], sigma_df[col]))
        for col in RATIO_COLS if col in sigma_df.columns
    }

    rows = []
    for _, row in lots.iterrows():
        isin = row['ISIN']
        등급 = row['등급']
        섹터 = row['섹터']
        delta = row['Credit_Delta']

        out_row = {
            'ISIN': isin,
            'TICKER': row['TICKER'],
            '종목명': row.get('종목명'),
            '등급': 등급,
            '섹터': 섹터,
            'Credit_Delta_$per bp': delta,
        }

        for basis in SIGMA_BASES:
            sigma_map, bucket_fallback, rating_fallback, global_avg = resolvers[basis]
            sigma, source = _resolve_sigma(isin, 등급, 섹터, sigma_map, bucket_fallback, rating_fallback, global_avg)
            risk_1sigma = delta * sigma if (pd.notna(delta) and sigma is not None) else np.nan

            out_row[f'sigma_bp_{basis}'] = round(sigma, 3) if sigma is not None else None
            out_row[f'sigma출처_{basis}'] = source
            out_row[f'실제스프레드리스크_1시그마_{basis}_$'] = round(risk_1sigma, 0) if pd.notna(risk_1sigma) else None
            out_row[f'실제스프레드리스크_95%VaR_{basis}_$'] = round(risk_1sigma * 1.645, 0) if pd.notna(risk_1sigma) else None
            out_row[f'실제스프레드리스크_99%VaR_{basis}_$'] = round(risk_1sigma * 2.33, 0) if pd.notna(risk_1sigma) else None

        # sigma비율(LQD/LUACOAS x 5Y/stress) 4가지 기준 리스크 = Credit Delta x sigma비율
        for col, ratio_map in ratio_maps.items():
            ratio = ratio_map.get(isin)
            risk_ratio = delta * ratio if (pd.notna(delta) and ratio is not None and pd.notna(ratio)) else None
            out_row[f'실제리스크_{col}_$'] = round(risk_ratio, 0) if risk_ratio is not None else None

        rows.append(out_row)

    return pd.DataFrame(rows)




def summarize_by_bucket(detail_df: pd.DataFrame) -> pd.DataFrame:
    agg_cols = [f'실제스프레드리스크_1시그마_{basis}_$' for basis in SIGMA_BASES]
    summary = detail_df.groupby(['등급', '섹터'])[agg_cols].sum().reset_index()
    for basis in SIGMA_BASES:
        base_col = f'실제스프레드리스크_1시그마_{basis}_$'
        summary[f'버킷합산_1시그마_{basis}_$'] = summary[base_col]
        summary[f'버킷합산_95%VaR_{basis}_$'] = summary[base_col] * 1.645
        summary[f'버킷합산_99%VaR_{basis}_$'] = summary[base_col] * 2.33
        summary = summary.drop(columns=[base_col])
    summary = summary.sort_values(f'버킷합산_1시그마_stress_$', key=lambda s: s.abs(), ascending=False)
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', required=True, help='잔고 xlsx 파일 경로 (오늘자)')
    parser.add_argument('--sigma-cache', required=True,
                         help='compute_credit_spread_volatility.py 결과 json (주기적으로 갱신)')
    parser.add_argument('--output', default='actual_credit_risk.json')
    args = parser.parse_args()

    print("[1/3] 잔고(lot 단위) 로드...")
    lots = load_lots(args.input)
    print(f"  -> {len(lots)}개 lot")

    print("[2/3] sigma 캐시 로드 및 병합 (3Y + stress)...")
    sigma_df = load_sigma_cache(args.sigma_cache)
    detail = merge_and_compute(lots, sigma_df)
    for basis in SIGMA_BASES:
        print(f"  -> [{basis}] sigma 출처별 분포:\n{detail[f'sigma출처_{basis}'].value_counts().to_string()}")

    print("[3/3] 버킷(등급x섹터) 합산 및 저장...")
    summary = summarize_by_bucket(detail)

    detail_xlsx = args.output.replace('.json', '_detail.xlsx')
    summary_xlsx = args.output.replace('.json', '_summary.xlsx')
    core._safe_save(lambda: detail.to_excel(detail_xlsx, index=False), detail_xlsx)
    core._safe_save(lambda: summary.to_excel(summary_xlsx, index=False), summary_xlsx)
    core._safe_save(lambda: detail.to_json(args.output, orient='records', force_ascii=False, indent=2), args.output)

    print()
    print("=== 버킷(등급x섹터)별 리스크 합산 ===")
    print(summary.to_string(index=False))
    print()
    print("=== 포트폴리오 총계 (버킷간 단순합) ===")
    for basis in SIGMA_BASES:
        total = detail[f'실제스프레드리스크_1시그마_{basis}_$'].sum()
        print(f"[{basis}] 1-시그마: {total:,.0f}   /   95%VaR: {total*1.645:,.0f}   /   99%VaR: {total*2.33:,.0f}")
    print()
    print(f"상세: {detail_xlsx}")
    print(f"버킷요약: {summary_xlsx}")


if __name__ == '__main__':
    main()

