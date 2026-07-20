# -*- coding: utf-8 -*-
"""
compute_actual_credit_risk.py
-------------------------------
채권(Credit Delta) + CDS(넷팅) + ETF(LQD/VCIT 보유) 를 티커 단위로 합쳐서
"실제 크레딧 델타/리스크"를 3년(3Y) 기준과 스트레스(stress) 기준 두 가지로 계산한다.

흐름:
    1) 채권 lot 단위 리스크 계산 (Credit Delta x sigma, 3Y/stress 둘 다) - lot 상세는 투명성용으로 남김
    2) CDS 포지션(cds 시트)을 티커 단위로 채권과 넷팅
    3) ETF(LQD/VCIT) 보유 Dv01을 별도 실물 레그로 추가 (자체 sigma 사용)
    4) 티커 단위 넷 델타 x sigma(3Y/stress) = 넷 리스크 산출
    5) 등급 x 섹터 버킷 합산은 "넷 리스크" 기준으로 재계산 (CDS 헤지효과가 버킷 합계에 반영됨)
    6) 포트폴리오 총계(gross vs net, 3Y vs stress) 비교 출력

포트폴리오 합산 방식:
    - 등급 x 섹터 버킷 내 합산 (완전상관 가정), 버킷간 단순합 (분산효과 미반영, 보수적)

사용법:
    python compute_actual_credit_risk.py \
        --input "8874잔고_260709.xlsx" \
        --sigma-cache credit_spread_volatility.json \
        --output actual_credit_risk.json \
        --sell-tickers KDB,POHANG   # (선택) CDS 프로텍션 매도로 취급할 티커
"""

import argparse

import numpy as np
import pandas as pd

import compute_credit_spread_volatility as core
from merge_delta_with_vol import (
    load_lots,
    load_sigma_cache,
    build_bucket_fallback_sigma,
    build_rating_fallback_sigma,
    merge_and_compute,
    SIGMA_BASES,
)
from merge_cds_net_delta import load_cds_positions, load_etf_delta_positions


def _cds_maturity_bucket_from_dur(dur: float) -> str:
    """CDS의 DUR을 채권과 동일한 만기구간 카테고리로 근사 매핑 (준거채권이 캐시에 없을 때만 폴백)"""
    if pd.isna(dur):
        return 'NA'
    if dur <= 3:
        return '0-3Y'
    if dur <= 5:
        return '3-5Y'
    if dur <= 10:
        return '5-10Y'
    return '10Y+'


def _build_rating_sector_maturity_sigma(sigma_df: pd.DataFrame, sigma_col: str) -> dict:
    """등급 x 섹터 x 만기구간 평균 sigma (CDS 폴백용 - 만기 반영)"""
    grp = sigma_df.dropna(subset=[sigma_col]).groupby(['등급', '섹터', '만기구간'])[sigma_col].mean()
    return grp.to_dict()


def resolve_isin_sigma(isin, ticker, 등급, 섹터, dur, sigma_col: str,
                        sigma_map_by_isin: dict, rating_sector_maturity_sigma: dict,
                        bucket_fallback: dict, rating_fallback: dict, global_avg_sigma: float):
    """
    ISIN(또는 준거자산) 하나의 sigma를 구한다 - CDS/ETF 등 채권 lot 외 레그에 공용으로 사용.
    1순위: 정확한 ISIN이 sigma 캐시에 있으면 그 값
    2순위: DUR -> 만기구간 근사 + (등급,섹터,만기구간) 평균
    3순위: (등급,섹터) 평균 -> 4순위: 등급 평균 -> 5순위: 전체 평균
    """
    sigma = sigma_map_by_isin.get(isin)
    if sigma is not None and not (isinstance(sigma, float) and np.isnan(sigma)):
        return sigma, 'exact_isin_cache'

    만기구간 = _cds_maturity_bucket_from_dur(dur)
    sigma = rating_sector_maturity_sigma.get((등급, 섹터, 만기구간))
    if sigma is not None and not (isinstance(sigma, float) and np.isnan(sigma)):
        return sigma, 'rating_sector_maturity_fallback(DUR근사)'

    sigma = bucket_fallback.get((등급, 섹터))
    if sigma is not None and not (isinstance(sigma, float) and np.isnan(sigma)):
        return sigma, 'rating_sector_fallback'

    sigma = rating_fallback.get(등급)
    if sigma is not None and not (isinstance(sigma, float) and np.isnan(sigma)):
        return sigma, 'rating_fallback'

    return global_avg_sigma, 'global_fallback'


def build_ticker_level_view(lots: pd.DataFrame, cds: pd.DataFrame, etf: pd.DataFrame,
                             lot_detail: pd.DataFrame, sigma_df: pd.DataFrame):
    """
    채권(lot_detail 합산) + CDS(넷팅) + ETF(실물 보유)를 티커 단위로 합쳐
    3Y/stress 두 기준 다 계산한 넷 델타/넷 리스크 테이블을 만든다.
    """
    bond_delta_by_ticker = lots.groupby('TICKER')['Credit_Delta'].sum()

    ticker_meta = (
        lots.groupby('TICKER')
        .agg(등급=('등급', lambda s: s.mode().iloc[0] if not s.mode().empty else None),
             섹터=('섹터', lambda s: s.mode().iloc[0] if not s.mode().empty else None))
    )

    # 채권 잔고엔 없고 CDS/ETF 시트에만 있는 티커(자사채 HANFGI, LQD, VCIT 등)는
    # 등급/섹터가 없어서 버킷 합산에서 빠지므로, 티커 기준으로 채워줌
    extra_tickers = set()
    if not cds.empty:
        extra_tickers |= set(cds['TICKER'].unique())
    if not etf.empty:
        extra_tickers |= set(etf['TICKER'].unique())
    for t in extra_tickers:
        if t not in ticker_meta.index:
            is_etf_ticker = (not etf.empty) and (t in etf['TICKER'].values)
            ticker_meta.loc[t] = {
                '등급': 'NR',
                '섹터': 'ETF' if is_etf_ticker else ('금융' if t in core.FINANCIAL_TICKERS else '비금융'),
            }

    all_tickers = sorted(
        set(bond_delta_by_ticker.index)
        | (set(cds['TICKER'].unique()) if not cds.empty else set())
        | (set(etf['TICKER'].unique()) if not etf.empty else set())
    )

    # 티커별 delta는 기준(3Y/stress) 무관 - 한 번만 계산
    etf_delta_by_ticker = etf.set_index('TICKER')['Delta'] if not etf.empty else pd.Series(dtype=float)
    cds_delta_by_ticker = cds.groupby('TICKER')['CDS_Delta'].sum() if not cds.empty else pd.Series(dtype=float)

    per_basis_rows = {basis: {} for basis in SIGMA_BASES}

    for basis, sigma_col in SIGMA_BASES.items():
        sigma_map_by_isin = dict(zip(sigma_df['ISIN'], sigma_df[sigma_col]))
        rating_sector_maturity_sigma = _build_rating_sector_maturity_sigma(sigma_df, sigma_col)
        bucket_fallback = build_bucket_fallback_sigma(sigma_df, sigma_col)
        rating_fallback = build_rating_fallback_sigma(sigma_df, sigma_col)
        global_avg_sigma = sigma_df[sigma_col].dropna().mean()

        bond_risk_by_ticker = lot_detail.groupby('TICKER')[f'실제스프레드리스크_1시그마_{basis}_$'].sum()

        # CDS 건별 sigma/리스크
        if not cds.empty:
            cds_b = cds.copy()
            resolved = cds_b.apply(
                lambda r: resolve_isin_sigma(
                    r['종목코드'], r['TICKER'],
                    ticker_meta['등급'].get(r['TICKER']), ticker_meta['섹터'].get(r['TICKER']),
                    r.get('DUR'), sigma_col,
                    sigma_map_by_isin, rating_sector_maturity_sigma,
                    bucket_fallback, rating_fallback, global_avg_sigma,
                ), axis=1
            )
            cds_b['_sigma'] = [t[0] for t in resolved]
            cds_b['_sigma_source'] = [t[1] for t in resolved]
            cds_b['_risk'] = cds_b['CDS_Delta'] * cds_b['_sigma']
            cds_risk_by_ticker = cds_b.groupby('TICKER')['_risk'].sum()
        else:
            cds_risk_by_ticker = pd.Series(dtype=float)

        # ETF 건별 sigma/리스크 (ISIN='LQD'/'VCIT'로 캐시에 그대로 있어야 정상)
        if not etf.empty:
            etf_b = etf.copy()
            resolved = etf_b.apply(
                lambda r: resolve_isin_sigma(
                    r['ISIN'], r['TICKER'], 'NR', 'ETF', np.nan, sigma_col,
                    sigma_map_by_isin, rating_sector_maturity_sigma,
                    bucket_fallback, rating_fallback, global_avg_sigma,
                ), axis=1
            )
            etf_b['_sigma'] = [t[0] for t in resolved]
            etf_b['_sigma_source'] = [t[1] for t in resolved]
            etf_b['_risk'] = etf_b['Delta'] * etf_b['_sigma']
            etf_risk_by_ticker = etf_b.set_index('TICKER')['_risk']
        else:
            etf_risk_by_ticker = pd.Series(dtype=float)

        for t in all_tickers:
            bond_d = bond_delta_by_ticker.get(t, 0.0)
            etf_d = etf_delta_by_ticker.get(t, 0.0)
            cds_d = cds_delta_by_ticker.get(t, 0.0)
            실물_d = bond_d + etf_d
            net_d = 실물_d + cds_d

            gross_risk = bond_risk_by_ticker.get(t, 0.0) + etf_risk_by_ticker.get(t, 0.0)
            cds_risk = cds_risk_by_ticker.get(t, 0.0)
            net_risk = gross_risk + cds_risk

            per_basis_rows[basis][t] = {
                '실물_Delta_$per bp': round(실물_d, 0),
                'CDS_Delta_$per bp': round(cds_d, 0),
                '넷_Delta_$per bp': round(net_d, 0),
                f'실물단독_1시그마리스크_{basis}_$': round(gross_risk, 0),
                f'CDS_1시그마리스크_{basis}_$': round(cds_risk, 0),
                f'넷_1시그마리스크_{basis}_$': round(net_risk, 0),
                f'헤지효과_{basis}_$': round(abs(gross_risk) - abs(net_risk), 0),
            }

    # sigma비율(LQD/LUACOAS x 5Y/stress) 4가지 기준 리스크 - 채권+ETF(실물) + CDS까지 반영해서 티커별 합산
    from merge_delta_with_vol import RATIO_COLS
    ratio_risk_col_by_ticker = {}
    ratio_gross_col_by_ticker = {}  # CDS 제외 실물단독 버전도 참고용으로 같이 만듦
    for col in RATIO_COLS:
        risk_col = f'실제리스크_{col}_$'
        ratio_map = dict(zip(sigma_df['ISIN'], sigma_df[col])) if col in sigma_df.columns else {}

        bond_sum = (
            lot_detail.groupby('TICKER')[risk_col].sum()
            if risk_col in lot_detail.columns else pd.Series(dtype=float)
        )

        etf_sum = pd.Series(dtype=float)
        if not etf.empty:
            etf_risk = etf.apply(
                lambda r: r['Delta'] * ratio_map.get(r['ISIN'])
                if ratio_map.get(r['ISIN']) is not None and pd.notna(ratio_map.get(r['ISIN'])) else 0.0,
                axis=1
            )
            etf_sum = pd.Series(etf_risk.values, index=etf['TICKER']).groupby(level=0).sum()

        gross_sum = bond_sum.add(etf_sum, fill_value=0.0)
        ratio_gross_col_by_ticker[risk_col] = gross_sum

        cds_sum = pd.Series(dtype=float)
        if not cds.empty:
            cds_risk = cds.apply(
                lambda r: r['CDS_Delta'] * ratio_map.get(r['종목코드'])
                if ratio_map.get(r['종목코드']) is not None and pd.notna(ratio_map.get(r['종목코드'])) else 0.0,
                axis=1
            )
            cds_sum = pd.Series(cds_risk.values, index=cds['TICKER']).groupby(level=0).sum()

        combined = gross_sum.add(cds_sum, fill_value=0.0)
        ratio_risk_col_by_ticker[risk_col] = combined

    rows = []
    for t in all_tickers:
        row = {
            'TICKER': t,
            '등급': ticker_meta['등급'].get(t),
            '섹터': ticker_meta['섹터'].get(t),
        }
        for basis in SIGMA_BASES:
            row.update(per_basis_rows[basis][t])
        for risk_col, series in ratio_risk_col_by_ticker.items():
            val = series.get(t)
            row[f'넷_{risk_col}'] = round(val, 0) if val is not None and pd.notna(val) else None
        for risk_col, series in ratio_gross_col_by_ticker.items():
            val = series.get(t)
            row[f'실물단독_{risk_col}'] = round(val, 0) if val is not None and pd.notna(val) else None
        rows.append(row)

    return pd.DataFrame(rows)


def summarize_by_maturity_bucket(lot_detail: pd.DataFrame, cds: pd.DataFrame, sigma_df: pd.DataFrame) -> pd.DataFrame:
    """
    등급 x 만기구간 버킷으로 sigma비율 기반 리스크(LQD/LUACOAS x 5Y/stress 4가지)를 집계.
    만기구간은 종목(ISIN) 속성이라 발행자(티커) 단위가 아니라 lot 단위에서 새로 집계.
    CDS 포지션도 준거채권의 만기구간을 붙여서 같이 반영(넷 기준).
    """
    from merge_delta_with_vol import RATIO_COLS

    maturity_map = dict(zip(sigma_df['ISIN'], sigma_df['만기구간']))

    # 채권/ETF(실물) lot 단위
    bond = lot_detail.copy()
    bond['만기구간'] = bond['ISIN'].map(maturity_map)
    ratio_risk_cols = [f'실제리스크_{col}_$' for col in RATIO_COLS if f'실제리스크_{col}_$' in bond.columns]
    gross_summary = bond.groupby(['등급', '만기구간'])[ratio_risk_cols].sum().reset_index()

    # CDS는 준거채권 ISIN(종목코드) 기준 만기구간 매칭, 넷 계산에 합산
    if not cds.empty:
        cds_c = cds.copy()
        cds_c['만기구간'] = cds_c['종목코드'].map(maturity_map)
        cds_c['등급'] = cds_c['등급'] if '등급' in cds_c.columns else None
        # cds 시트 자체엔 등급이 없으므로 준거채권 ISIN으로 sigma_df에서 등급도 매칭
        rating_map = dict(zip(sigma_df['ISIN'], sigma_df['등급']))
        cds_c['등급'] = cds_c['종목코드'].map(rating_map)

        for col in RATIO_COLS:
            risk_col = f'실제리스크_{col}_$'
            ratio_map = dict(zip(sigma_df['ISIN'], sigma_df[col])) if col in sigma_df.columns else {}
            cds_c[risk_col] = cds_c.apply(
                lambda r: r['CDS_Delta'] * ratio_map.get(r['종목코드'])
                if ratio_map.get(r['종목코드']) is not None and pd.notna(ratio_map.get(r['종목코드'])) else 0.0,
                axis=1
            )
        cds_summary = cds_c.groupby(['등급', '만기구간'])[ratio_risk_cols].sum().reset_index()
    else:
        cds_summary = pd.DataFrame(columns=['등급', '만기구간'] + ratio_risk_cols)

    merged = pd.merge(
        gross_summary, cds_summary, on=['등급', '만기구간'], how='outer', suffixes=('_실물', '_CDS')
    ).fillna(0.0)

    out = merged[['등급', '만기구간']].copy()
    for col in ratio_risk_cols:
        gross_c = f'{col}_실물' if f'{col}_실물' in merged.columns else col
        cds_c_name = f'{col}_CDS' if f'{col}_CDS' in merged.columns else None
        out[f'실물단독_{col}'] = merged[gross_c]
        out[f'넷_{col}'] = merged[gross_c] + (merged[cds_c_name] if cds_c_name else 0.0)

    sort_col = '넷_실제리스크_sigma비율_vs_LUACOAS_stress_$'
    if sort_col in out.columns:
        out = out.sort_values(sort_col, key=lambda s: s.abs(), ascending=False)
    return out


def summarize_by_bucket_net(ticker_view: pd.DataFrame) -> pd.DataFrame:
    agg_cols = [f'넷_1시그마리스크_{basis}_$' for basis in SIGMA_BASES] + \
               [f'실물단독_1시그마리스크_{basis}_$' for basis in SIGMA_BASES]
    summary = ticker_view.groupby(['등급', '섹터'])[agg_cols].sum().reset_index()

    for basis in SIGMA_BASES:
        gross_col = f'실물단독_1시그마리스크_{basis}_$'
        net_col = f'넷_1시그마리스크_{basis}_$'
        summary[f'헤지효과_{basis}_$'] = summary[gross_col].abs() - summary[net_col].abs()
        summary[f'넷_95%VaR_{basis}_$'] = summary[net_col] * 1.645
        summary[f'넷_99%VaR_{basis}_$'] = summary[net_col] * 2.33

    summary = summary.sort_values(f'넷_1시그마리스크_stress_$', key=lambda s: s.abs(), ascending=False)
    return summary


def _weighted_avg(values, weights):
    """NaN 쌍을 제외하고 가중평균 (가중치 절대값 사용 - 부호 무관하게 규모로 가중)"""
    df = pd.DataFrame({'v': values, 'w': weights.abs()}).dropna()
    if df.empty or df['w'].sum() == 0:
        return None
    return float(np.average(df['v'], weights=df['w']))


def _weighted_avg(values, weights):
    """NaN 쌍을 제외하고 가중평균 (가중치 절대값 사용 - 부호 무관하게 규모로 가중)"""
    df = pd.DataFrame({'v': values, 'w': weights.abs()}).dropna()
    if df.empty or df['w'].sum() == 0:
        return None
    return float(np.average(df['v'], weights=df['w']))


def _build_net_delta_by_isin(lots: pd.DataFrame, cds: pd.DataFrame, etf: pd.DataFrame) -> pd.Series:
    """
    ISIN 단위 넷 델타 = 채권(lots) + CDS(준거채권 ISIN 기준) + ETF.
    CDS는 종목코드(준거채권 ISIN)에 그대로 더해서, 그 채권 보유분과 넷팅된 것처럼 반영.
    """
    bond_sum = lots.groupby('ISIN')['Credit_Delta'].sum()
    cds_sum = cds.groupby('종목코드')['CDS_Delta'].sum() if not cds.empty else pd.Series(dtype=float)
    etf_sum = etf.set_index('ISIN')['Delta'] if not etf.empty else pd.Series(dtype=float)
    combined = bond_sum.add(cds_sum, fill_value=0.0).add(etf_sum, fill_value=0.0)
    return combined


def _merge_net_delta_with_attrs(lots, cds, etf, sigma_df) -> pd.DataFrame:
    """넷델타(ISIN단위) + sigma_df의 등급/섹터/만기구간/지역/YTM/스프레드 속성을 합친 테이블"""
    net_delta = _build_net_delta_by_isin(lots, cds, etf).rename('넷Delta').reset_index()
    net_delta.columns = ['ISIN', '넷Delta']

    join_cols = ['ISIN', '등급', '섹터', '만기구간', '지역', '현재YTM', '현재스프레드_bp']
    join_cols = [c for c in join_cols if c in sigma_df.columns]
    merged = net_delta.merge(sigma_df[join_cols], on='ISIN', how='left')
    return merged


def build_group_concentration(lots: pd.DataFrame, cds: pd.DataFrame, etf: pd.DataFrame,
                               sigma_df: pd.DataFrame) -> pd.DataFrame:
    """
    등급/섹터/만기구간/지역 4개 차원 '각각'에 대해 (단일 차원 분해):
    - 넷Delta 합계(채권+CDS+ETF)
    - Delta(절대값) 가중평균 YTM, 가중평균 현재스프레드
    를 집계해서 하나의 긴 형식(long-format) 테이블로 반환. '차원' 컬럼으로 구분.
    """
    merged = _merge_net_delta_with_attrs(lots, cds, etf, sigma_df)
    dims = ['등급', '섹터', '만기구간', '지역']
    dims = [d for d in dims if d in merged.columns]

    rows = []
    for dim in dims:
        for val, g in merged.groupby(dim, dropna=False):
            ytm_avg = _weighted_avg(g['현재YTM'], g['넷Delta']) if '현재YTM' in g.columns else None
            spread_avg = _weighted_avg(g['현재스프레드_bp'], g['넷Delta']) if '현재스프레드_bp' in g.columns else None
            rows.append({
                '차원': dim,
                '값': val,
                '넷Delta합계_$per bp': round(g['넷Delta'].sum(), 0),
                '가중평균YTM_%': round(ytm_avg, 3) if ytm_avg is not None else None,
                '가중평균스프레드_bp': round(spread_avg, 2) if spread_avg is not None else None,
                '종목수': g['ISIN'].nunique(),
            })

    out = pd.DataFrame(rows)
    out['_abs'] = out['넷Delta합계_$per bp'].abs()
    out = out.sort_values(['차원', '_abs'], ascending=[True, False]).drop(columns=['_abs'])
    return out


def build_cross_concentration(lots: pd.DataFrame, cds: pd.DataFrame, etf: pd.DataFrame,
                               sigma_df: pd.DataFrame) -> pd.DataFrame:
    """
    등급 x 섹터 x 만기구간 x 지역을 동시에 교차해서, 실제로 보유가 있는 조합만 집계.
    (이론상 전체 경우의 수가 아니라 실제 존재하는 조합만 나오므로 개수가 관리 가능한 수준)
    """
    merged = _merge_net_delta_with_attrs(lots, cds, etf, sigma_df)
    dims = ['등급', '섹터', '만기구간', '지역']
    dims = [d for d in dims if d in merged.columns]

    rows = []
    for keys, g in merged.groupby(dims, dropna=False):
        if not isinstance(keys, tuple):
            keys = (keys,)
        row = dict(zip(dims, keys))
        ytm_avg = _weighted_avg(g['현재YTM'], g['넷Delta']) if '현재YTM' in g.columns else None
        spread_avg = _weighted_avg(g['현재스프레드_bp'], g['넷Delta']) if '현재스프레드_bp' in g.columns else None
        row['넷Delta합계_$per bp'] = round(g['넷Delta'].sum(), 0)
        row['가중평균YTM_%'] = round(ytm_avg, 3) if ytm_avg is not None else None
        row['가중평균스프레드_bp'] = round(spread_avg, 2) if spread_avg is not None else None
        row['종목수'] = g['ISIN'].nunique()
        rows.append(row)

    out = pd.DataFrame(rows)
    out['_abs'] = out['넷Delta합계_$per bp'].abs()
    out = out.sort_values('_abs', ascending=False).drop(columns=['_abs'])
    return out


def build_isin_level_view(lot_detail: pd.DataFrame, etf: pd.DataFrame, sigma_df: pd.DataFrame) -> pd.DataFrame:
    """
    lot_detail(중복 ISIN 포함)을 ISIN 단위로 합산해서 종목별 뷰를 만든다.
    (같은 종목을 여러 lot으로 나눠 보유한 경우 하나로 합쳐서 보여줌)
    ETF(LQD/VCIT) 보유분도 같이 넣어서 '티커별' 시트 합계와 정확히 일치하도록 함.
    """
    agg_cols = {'Credit_Delta_$per bp': 'sum'}
    for basis in SIGMA_BASES:
        agg_cols[f'sigma_bp_{basis}'] = 'first'      # 같은 ISIN이면 lot마다 동일한 값이라 first로 충분
        agg_cols[f'sigma출처_{basis}'] = 'first'
        agg_cols[f'실제스프레드리스크_1시그마_{basis}_$'] = 'sum'
        agg_cols[f'실제스프레드리스크_95%VaR_{basis}_$'] = 'sum'
        agg_cols[f'실제스프레드리스크_99%VaR_{basis}_$'] = 'sum'
    from merge_delta_with_vol import RATIO_COLS
    for col in RATIO_COLS:
        risk_col = f'실제리스크_{col}_$'
        if risk_col in lot_detail.columns:
            agg_cols[risk_col] = 'sum'

    isin_view = (
        lot_detail.groupby(['ISIN', 'TICKER', '종목명', '등급', '섹터'], as_index=False)
        .agg(agg_cols)
    )

    if not etf.empty:
        sigma_map = {basis: dict(zip(sigma_df['ISIN'], sigma_df[col])) for basis, col in SIGMA_BASES.items()}
        from merge_delta_with_vol import RATIO_COLS
        ratio_maps = {col: dict(zip(sigma_df['ISIN'], sigma_df[col])) for col in RATIO_COLS if col in sigma_df.columns}
        etf_rows = []
        for _, r in etf.iterrows():
            row = {
                'ISIN': r['ISIN'],
                'TICKER': r['TICKER'],
                '종목명': f"{r['TICKER']} (ETF 보유)",
                '등급': 'NR',
                '섹터': 'ETF',
                'Credit_Delta_$per bp': r['Delta'],
            }
            for basis in SIGMA_BASES:
                sigma = sigma_map[basis].get(r['ISIN'])
                risk = r['Delta'] * sigma if sigma is not None else None
                row[f'sigma_bp_{basis}'] = round(sigma, 3) if sigma is not None else None
                row[f'sigma출처_{basis}'] = 'exact_isin_cache' if sigma is not None else None
                row[f'실제스프레드리스크_1시그마_{basis}_$'] = round(risk, 0) if risk is not None else None
                row[f'실제스프레드리스크_95%VaR_{basis}_$'] = round(risk * 1.645, 0) if risk is not None else None
                row[f'실제스프레드리스크_99%VaR_{basis}_$'] = round(risk * 2.33, 0) if risk is not None else None
            for col, ratio_map in ratio_maps.items():
                ratio = ratio_map.get(r['ISIN'])
                risk_ratio = r['Delta'] * ratio if ratio is not None and pd.notna(ratio) else None
                row[f'실제리스크_{col}_$'] = round(risk_ratio, 0) if risk_ratio is not None else None
            etf_rows.append(row)
        isin_view = pd.concat([isin_view, pd.DataFrame(etf_rows)], ignore_index=True)

    sort_col = f'실제스프레드리스크_1시그마_stress_$'
    isin_view = isin_view.sort_values(sort_col, key=lambda s: s.abs(), ascending=False)
    return isin_view


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', required=True, help='잔고 xlsx (채권 시트 + cds 시트 포함)')
    parser.add_argument('--sigma-cache', required=True,
                         help='compute_credit_spread_volatility.py 결과 json')
    parser.add_argument('--output', default='actual_credit_risk.json')
    parser.add_argument('--sell-tickers', default='',
                         help='CDS 프로텍션 매도로 취급할 티커 (콤마구분)')
    args = parser.parse_args()

    sell_tickers = set(t.strip().upper() for t in args.sell_tickers.split(',') if t.strip())

    print("[1/6] 채권 lot 로드 (ISIN 단위 상세용)...")
    lots = load_lots(args.input)
    print(f"  -> {len(lots)}개 lot")

    print("[2/6] sigma 캐시 로드...")
    sigma_df = load_sigma_cache(args.sigma_cache)

    print("[3/6] 채권 lot 단위 리스크 계산 (3Y + stress, 참고용 상세 테이블)...")
    lot_detail = merge_and_compute(lots, sigma_df)

    print("[4/6] CDS 포지션 로드...")
    cds = load_cds_positions(args.input, sell_tickers)
    print(f"  -> CDS {len(cds)}건, {cds['TICKER'].nunique()}개 티커")
    if sell_tickers:
        print(f"  -> 프로텍션 매도로 취급된 티커: {sell_tickers}")

    print("[5/6] ETF(LQD/VCIT) 보유 델타 로드...")
    etf = load_etf_delta_positions(args.input)
    print(f"  -> ETF {len(etf)}건: {etf['TICKER'].tolist() if not etf.empty else '없음'}")

    ticker_view = build_ticker_level_view(lots, cds, etf, lot_detail, sigma_df)
    isin_view = build_isin_level_view(lot_detail, etf, sigma_df)

    print("[6/6] 버킷(등급x섹터, 등급x만기) 합산 및 리스크 집중도(4차원) 계산...")
    bucket_summary = summarize_by_bucket_net(ticker_view)
    maturity_bucket_summary = summarize_by_maturity_bucket(lot_detail, cds, sigma_df)
    group_concentration = build_group_concentration(lots, cds, etf, sigma_df)
    cross_concentration = build_cross_concentration(lots, cds, etf, sigma_df)

    output_xlsx = args.output.replace('.json', '.xlsx')

    def _save_workbook():
        with pd.ExcelWriter(output_xlsx, engine='openpyxl') as writer:
            ticker_view.to_excel(writer, sheet_name='티커별(발행자)', index=False)
            isin_view.to_excel(writer, sheet_name='종목별(ISIN)', index=False)
            bucket_summary.to_excel(writer, sheet_name='버킷요약(등급x섹터)', index=False)
            maturity_bucket_summary.to_excel(writer, sheet_name='버킷요약(등급x만기)', index=False)
            group_concentration.to_excel(writer, sheet_name='리스크집중도(4차원)', index=False)
            cross_concentration.to_excel(writer, sheet_name='리스크집중도(교차)', index=False)
            lot_detail.to_excel(writer, sheet_name='lot상세', index=False)

    core._safe_save(_save_workbook, output_xlsx)
    core._safe_save(lambda: ticker_view.to_json(args.output, orient='records', force_ascii=False, indent=2), args.output)

    print()
    print("=== 버킷(등급x섹터)별 리스크 합산 (넷 기준) ===")
    print(bucket_summary.to_string(index=False))
    print()
    print("=== 포트폴리오 총계 ===")
    for basis in SIGMA_BASES:
        gross = ticker_view[f'실물단독_1시그마리스크_{basis}_$'].sum()
        net = ticker_view[f'넷_1시그마리스크_{basis}_$'].sum()
        hedge = abs(gross) - abs(net)
        print(f"[{basis}] 실물단독: {gross:,.0f}   /   넷(CDS반영): {net:,.0f}   /   "
              f"CDS로 줄어든 리스크: {hedge:,.0f}   /   넷95%VaR: {net*1.645:,.0f}   /   넷99%VaR: {net*2.33:,.0f}")
    print()
    print(f"결과 워크북 (시트: 티커별/종목별/버킷요약/lot상세): {output_xlsx}")


if __name__ == '__main__':
    main()
