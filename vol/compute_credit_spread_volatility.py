# -*- coding: utf-8 -*-
"""
크레딧 스프레드 변동성(sigma) 계산 스크립트
============================================
8874 잔고 파일을 읽어 종목을 등급x만기x섹터x지역 버킷으로 태깅하고,
Bloomberg BDH로 개별 종목 YAS_BOND_YLD와 매칭 GT<n> Govt 국채수익률을 받아
스프레드(=YAS_BOND_YLD - GT수익률)를 계산한 뒤:
1) 개별 종목 변동성
2) 버킷(pooled) 변동성
3) credibility 가중 결합 최종 sigma
를 산출합니다.

** 로컬 Bloomberg Terminal 연결 환경에서 실행 (xbbg 필요) **
    pip install xbbg pandas numpy

사용법:
    python compute_credit_spread_volatility.py --input "8874잔고_260709.xlsx" --output vol_output.json
"""

import argparse
import json
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

# ------------------------------------------------------------------
# 0. 설정값 (필요시 조정)
# ------------------------------------------------------------------
LOOKBACK_DAYS = 1400         # BDH 가져오기용 룩백 (약 5.5년 영업일, 2021년 스트레스 구간 커버)
FULL_METRIC_LOOKBACK_DAYS = 1260  # '_5Y' 지표 계산에 실제로 쓰는 기간 (5년 = 252*5)
ROLLING_WINDOW = 60          # 최근 변동성(rolling) 관측 윈도우 (영업일)
MIN_OBS_FOR_INDIV_ROLLING = 40   # 60일 윈도우 기준 개별 sigma 신뢰 최소 관측치
CREDIBILITY_K_ROLLING = 20       # 60일 윈도우용 credibility 상수 (짧은 윈도우라 K도 작게)

# 스프레드 확대(스트레스) 구간만 뽑아서 sigma 계산할 때 쓰는 기간들
STRESS_PERIODS = [
    ('2021-01-01', '2022-12-31'),   # 금리인상 사이클 + 코로나 이후 크레딧 리프라이싱
    ('2025-04-01', '2025-04-30'),   # 관세(Liberation Day) 충격
    ('2026-03-01', '2026-03-31'),   # 최근 스프레드 확대 구간
]
MIN_OBS_FOR_INDIV_STRESS = 15    # 스트레스 구간은 기간 자체가 짧아 최소관측치 기준 낮춤
CREDIBILITY_K_STRESS = 15
PRIMARY_BOND_FIELD = "YAS_BOND_YLD"
DIRECT_SPREAD_FIELD = "BLP_SPRD_TO_BENCH_MID"  # 벤치마크 대비 스프레드를 Bloomberg가 직접 계산해주는 필드 (되면 GT차감 불필요)
FALLBACK_YIELD_FIELD = "YLD_YTM_MID"   # YAS_BOND_YLD가 BDH에서 비어있을 때 폴백 (bond/GT 공통 검증된 필드)
GT_YLD_FIELD = "YLD_YTM_MID"           # GT<n> Govt 히스토리 필드
MIN_BUCKET_COUNT = 5         # 버킷 채택 최소 종목수
CREDIBILITY_K = 60           # w(n) = n / (n + K)
MIN_OBS_FOR_INDIV = 20       # 개별 종목 sigma를 신뢰하기 위한 최소 관측치
WINSORIZE_PCTL = 0.01        # sigma 계산 시 상하위 1% 절단 (고립된 이상치/데이터오류 방지)
ABSOLUTE_OUTLIER_CAP_BP = 100.0  # 하루 100bp 넘는 변화는 사실상 데이터 오류로 간주하고 자동 제거

# 확인된 데이터 오류 발생일 (여기 추가하면 해당 날짜로 들어가는/나가는 Δspread를
# 모든 종목에서 통계 계산 시 제외함). 'YYYY-MM-DD' 문자열로 추가.
# (참고: ABSOLUTE_OUTLIER_CAP_BP 자동 제거로 커버되는 경우가 많아, 100bp 미만인데
#  특정 날짜에만 이상하게 튀는 경우처럼 자동 규칙으로 못 잡는 예외적 케이스에만 추가하면 됨)
KNOWN_BAD_DATES = {
    '2024-09-23',
    '2024-09-24',
}

# GT텀(원본 잔고파일 '만기' 컬럼 값) -> GT Govt 티커 매핑
GT_TENOR_TO_TICKER = {
    0: "GT1 Govt",    # 1년 미만은 GT1로 근사 (필요시 GB3/GB6 Govt 등으로 교체)
    1: "GT1 Govt",
    2: "GT2 Govt",
    3: "GT3 Govt",
    5: "GT5 Govt",
    10: "GT10 Govt",
    30: "GT30 Govt",
}

# 간이 섹터 매핑 (금융/비금융) - 실제 BICS/GICS 코드로 교체 권장
FINANCIAL_TICKERS = {
    'BAC', 'BACR', 'BNP', 'GS', 'HSBC', 'MUFG', 'MIZUHO', 'MS', 'NOMURA', 'NWG',
    'SOCGEN', 'SUMIBK', 'WFC', 'WOORIB', 'STANLN', 'UBS', 'NORBK', 'CITNAT', 'JPM',
    'SUMITR', 'CACIB', 'TD', 'ACAFP', 'ANZNZ', 'SHINHAN', 'SHNHAN', 'KDB', 'NHSECS',
    'DAESEC', 'HYUSEC', 'HANMIL', 'KHFC', 'HYNCRD', 'DFHOLD', 'INTEND', 'HYUCAP',
    'SHINFN', 'INDKOR', 'NHNCOR', 'KUB', 'EIBKOR', 'HANFGI',
}


# ------------------------------------------------------------------
# 1. 잔고 파일 로드 + 버킷 태깅
# ------------------------------------------------------------------
def maturity_bucket(y):
    if pd.isna(y):
        return 'NA'
    if y <= 3:
        return '0-3Y'
    if y <= 5:
        return '3-5Y'
    if y <= 10:
        return '5-10Y'
    return '10Y+'


def region_group(r):
    if r == 'KP':
        return 'KP'
    if r in ('북미', '유럽', 'JP'):
        return 'DM'
    return 'Other'


def is_ust_isin(isin: str) -> bool:
    """미국 국채(Treasury) ISIN 여부 - CUSIP 프리픽스 912xxx 기반"""
    isin = str(isin)
    return isin.startswith('US912')


def load_and_tag(xlsx_path: str) -> pd.DataFrame:
    raw = pd.read_excel(xlsx_path, sheet_name=0, header=None)
    data = raw.iloc[3:]

    cols = {6: 'ISIN', 15: 'GT텀', 16: '지역', 21: 'TICKER', 22: '등급', 35: '만기일', 46: '환율'}
    df = data[list(cols.keys())].rename(columns=cols)
    df = df[df['ISIN'].notna()].copy()

    # 비정상 행(빈 값, ISIN 형식 아닌 것) 제거
    df = df[df['ISIN'].astype(str).str.len() == 12].copy()

    # 미국 국채(UST) 제외 - 'T' 티커가 AT&T 회사채와 국채를 혼용하는 경우가 있어 ISIN 기준으로 구분
    n_ust = df['ISIN'].astype(str).apply(is_ust_isin).sum()
    if n_ust > 0:
        df = df[~df['ISIN'].astype(str).apply(is_ust_isin)].copy()
        print(f"    [INFO] 미국 국채(UST) {n_ust}개 lot 제외")

    df['TICKER'] = df['TICKER'].astype(str).str.strip()
    df['GT텀'] = pd.to_numeric(df['GT텀'], errors='coerce')

    # USD 외 통화 종목 제외 (환율이 그날의 USD/KRW 환율과 다르면 다른 통화로 간주)
    df['환율'] = pd.to_numeric(df['환율'], errors='coerce')
    usd_rate = df['환율'].mode().iloc[0] if not df['환율'].mode().empty else None
    if usd_rate is not None:
        n_before = df['ISIN'].nunique()
        non_usd = df[(df['환율'] - usd_rate).abs() / usd_rate > 0.01]['ISIN'].nunique()
        df = df[(df['환율'] - usd_rate).abs() / usd_rate <= 0.01].copy()
        if non_usd > 0:
            print(f"    [INFO] 비USD 통화 종목 {non_usd}개 제외 (USD/KRW={usd_rate} 기준)")

    def classify_sector(ticker):
        return '금융' if ticker in FINANCIAL_TICKERS else '비금융'

    df['만기구간'] = df['GT텀'].apply(maturity_bucket)
    df['지역그룹'] = df['지역'].apply(region_group)
    df['섹터'] = df['TICKER'].apply(classify_sector)
    df['자산유형'] = 'BOND'
    df['출처'] = '잔고'

    # 중복 lot 제거 (ISIN 단위로 유니크 종목만 히스토리 조회 대상)
    unique_bonds = df.drop_duplicates(subset=['ISIN']).reset_index(drop=True)
    return unique_bonds


def load_extra_instruments(path: str) -> pd.DataFrame:
    """
    잔고파일에 없는 추가 분석 대상(자사채, 보유 ETF 등)을 읽어
    load_and_tag()와 동일한 스키마로 반환. 파일 없으면 빈 DataFrame.
    """
    import os
    if not path or not os.path.exists(path):
        return pd.DataFrame()

    df = pd.read_csv(path, encoding='utf-8-sig')
    df['GT텀'] = pd.to_numeric(df.get('GT텀'), errors='coerce')
    df['만기구간'] = df.apply(
        lambda r: r['자산유형'] if r.get('자산유형') in ('ETF', 'INDEX') else maturity_bucket(r['GT텀']),
        axis=1
    )
    df['지역그룹'] = df.apply(
        lambda r: r['자산유형'] if r.get('자산유형') in ('ETF', 'INDEX') else region_group(r.get('지역')),
        axis=1
    )
    df['만기일'] = pd.NaT
    df['출처'] = '추가'
    return df


# ------------------------------------------------------------------
# 2. 버킷 계층 할당 (L4 -> L1 -> L0 폴백)
# ------------------------------------------------------------------
def assign_bucket_levels(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    lvl4_n = df.groupby(['등급', '만기구간', '섹터', '지역그룹'])['ISIN'].transform('nunique')
    lvl3_n = df.groupby(['등급', '만기구간', '섹터'])['ISIN'].transform('nunique')
    lvl2_n = df.groupby(['등급', '만기구간'])['ISIN'].transform('nunique')
    lvl1_n = df.groupby(['등급'])['ISIN'].transform('nunique')

    levels = []
    keys = []
    for i in range(len(df)):
        row = df.iloc[i]
        if lvl4_n.iloc[i] >= MIN_BUCKET_COUNT:
            levels.append('L4')
            keys.append(f"L4|{row['등급']}|{row['만기구간']}|{row['섹터']}|{row['지역그룹']}")
        elif lvl3_n.iloc[i] >= MIN_BUCKET_COUNT:
            levels.append('L3')
            keys.append(f"L3|{row['등급']}|{row['만기구간']}|{row['섹터']}")
        elif lvl2_n.iloc[i] >= MIN_BUCKET_COUNT:
            levels.append('L2')
            keys.append(f"L2|{row['등급']}|{row['만기구간']}")
        elif lvl1_n.iloc[i] >= MIN_BUCKET_COUNT:
            levels.append('L1')
            keys.append(f"L1|{row['등급']}")
        else:
            levels.append('L0')
            keys.append("L0|GLOBAL")

    df['적용레벨'] = levels
    df['버킷키'] = keys
    return df


# ------------------------------------------------------------------
# 3. Bloomberg BDH 히스토리컬 스프레드 조회 (YAS_BOND_YLD - GT 방식)
# ------------------------------------------------------------------
def _bdh_to_pandas_long(raw, chunk_tickers, field_name):
    """narwhals/기타 BDH 리턴을 {ticker: pd.Series(date->value)} 딕셔너리로 변환"""
    if hasattr(raw, "to_pandas"):
        raw = raw.to_pandas()
    elif hasattr(raw, "_coerce_to_pandas"):
        raw = raw._coerce_to_pandas()
    elif not isinstance(raw, pd.DataFrame):
        raw = pd.DataFrame(raw)

    out = {}
    if raw is None or raw.empty:
        return out

    if isinstance(raw.columns, pd.MultiIndex):
        for ticker in chunk_tickers:
            try:
                series = raw[(ticker, field_name)].dropna()
            except KeyError:
                continue
            if len(series) > 0:
                out[ticker] = series
    else:
        lower_cols = set(c.lower() for c in raw.columns)
        if {'ticker', 'field', 'value'}.issubset(lower_cols):
            raw.columns = [c.lower() for c in raw.columns]
            for ticker in chunk_tickers:
                sub = raw[(raw['ticker'] == ticker) & (raw['field'] == field_name)]
                if not sub.empty:
                    s = sub.set_index('date')['value'].dropna()
                    s.index = pd.to_datetime(s.index)
                    out[ticker] = s
    return out


def _bdh_bulk(tickers: list, field: str, start_date, end_date, chunk_size: int = 40) -> dict:
    """티커 리스트를 청크로 나눠 BDH 조회, {ticker: pd.Series} 반환"""
    from xbbg import blp

    result = {}
    for i in range(0, len(tickers), chunk_size):
        chunk = tickers[i:i + chunk_size]
        try:
            raw = blp.bdh(
                tickers=chunk,
                flds=[field],
                start_date=start_date.strftime('%Y-%m-%d'),
                end_date=end_date.strftime('%Y-%m-%d'),
            )
        except Exception as e:
            print(f"[WARN] BDH 조회 실패 ({field}, chunk {i}-{i+chunk_size}): {e}")
            continue
        result.update(_bdh_to_pandas_long(raw, chunk, field))
    return result


def _bdh_bulk_with_fallback(isins: list, field_primary: str, field_fallback: str,
                             start_date, end_date, chunk_size: int = 40):
    """
    1) 'ISIN Corp' 포맷으로 field_primary 시도
    2) 비어있으면 'ISIN Corp' 포맷으로 field_fallback 시도
    3) 그래도 비어있으면 bare ISIN(접미사 없음) 포맷으로 field_fallback 재시도
       (backfill_hyperscaler_history.py 에서 검증된 순서)
    반환: (result_dict {isin: pd.Series}, 사용된 field, 사용된 ticker 포맷 설명)
    """
    corp_tickers = [f"{isin} Corp" for isin in isins]

    def _strip_corp(d):
        return {k.replace(" Corp", ""): v for k, v in d.items()}

    print(f"    시도 1: 'ISIN Corp' + {field_primary}")
    r = _bdh_bulk(corp_tickers, field_primary, start_date, end_date, chunk_size)
    if r:
        return _strip_corp(r), field_primary, "ISIN Corp"

    print(f"    [WARN] 시도 1 결과 0건 -> 시도 2: 'ISIN Corp' + {field_fallback}")
    r = _bdh_bulk(corp_tickers, field_fallback, start_date, end_date, chunk_size)
    if r:
        return _strip_corp(r), field_fallback, "ISIN Corp"

    print(f"    [WARN] 시도 2 결과 0건 -> 시도 3: bare ISIN + {field_fallback}")
    r = _bdh_bulk(isins, field_fallback, start_date, end_date, chunk_size)
    if r:
        return r, field_fallback, "bare ISIN"

    print("    [ERROR] 세 가지 방식 모두 0건")
    return {}, None, None


def _nearest_tenor_code(remaining_years: np.ndarray, tenor_codes: list) -> np.ndarray:
    """각 시점의 잔존만기(연)에 대해 가장 가까운 GT 텀 코드를 벡터화로 찾는다"""
    codes = np.array(sorted(tenor_codes), dtype=float)
    remaining_years = np.clip(remaining_years, codes.min(), codes.max())
    diffs = np.abs(remaining_years[:, None] - codes[None, :])
    idx = diffs.argmin(axis=1)
    return codes[idx]


def _build_dynamic_gt_series(bond_dates: pd.DatetimeIndex, maturity_date,
                              gt_combined: pd.DataFrame) -> pd.Series:
    """
    채권의 만기일은 고정이므로, 히스토리 시점마다 '그 시점 기준 잔존만기'를 다시 계산해서
    가장 가까운 GT 텀을 매일 다르게 매칭한다 (시간이 지나며 벤치마크가 자연스럽게 짧아짐).
    """
    if gt_combined.empty:
        return pd.Series(dtype=float)

    aligned_gt = gt_combined.reindex(bond_dates, method='ffill')
    remaining_years = (pd.Timestamp(maturity_date) - bond_dates).days / 365.25
    tenor_codes = _nearest_tenor_code(remaining_years.values, aligned_gt.columns.tolist())

    col_idx = [aligned_gt.columns.get_loc(t) for t in tenor_codes]
    picked = aligned_gt.to_numpy()[np.arange(len(aligned_gt)), col_idx]
    return pd.Series(picked, index=bond_dates)


def fetch_historical_spreads(tagged_df: pd.DataFrame, lookback_days: int = LOOKBACK_DAYS) -> dict:
    """
    ISIN별 스프레드 계산.
    - BOND: 1차로 BLP_SPRD_TO_BENCH_MID(벤치마크 대비 스프레드, Bloomberg 직접 계산)를 시도.
            이 필드가 없는 종목만 YAS_BOND_YLD(안되면 YLD_YTM_MID) - GT<n> Govt 동적매칭으로 폴백.
      * 동적매칭은 만기일 기준으로 '그날그날' 잔존만기를 다시 계산해서 GT텀을 매칭 (벤치마크 드리프트 방지)
    - ETF (LQD/VCIT 등): YAS_YLD_SPREAD 필드를 'ISIN US Equity' 티커로 직접 조회 (GT차감 없음)
    tagged_df는 'ISIN', 'GT텀', '만기일', '자산유형' 컬럼을 포함해야 함.
    반환: {ISIN: pd.Series(date-indexed spread in bp)}
    """
    end_date = datetime.today()
    start_date = end_date - timedelta(days=int(lookback_days * 1.45))

    asset_type = tagged_df.get('자산유형', pd.Series(['BOND'] * len(tagged_df)))
    is_etf = asset_type == 'ETF'
    is_index = asset_type == 'INDEX'
    bond_df = tagged_df[~is_etf & ~is_index]
    etf_df = tagged_df[is_etf]
    index_df = tagged_df[is_index]

    isins = bond_df['ISIN'].tolist()
    bond_tickers = [f"{isin} Corp" for isin in isins]

    print(f"  - 직접 스프레드 필드({DIRECT_SPREAD_FIELD}) 1차 조회...")
    direct_spread = _bdh_bulk(bond_tickers, DIRECT_SPREAD_FIELD, start_date, end_date)
    direct_spread = {k.replace(" Corp", ""): v for k, v in direct_spread.items()}
    print(f"    -> {len(direct_spread)}/{len(isins)}개 종목 확보")

    result = {isin: series.sort_index() for isin, series in direct_spread.items() if len(series) > 0}

    remaining_isins = [i for i in isins if i not in result]
    remaining_df = bond_df[bond_df['ISIN'].isin(remaining_isins)]

    n_dynamic, n_static_fallback = 0, 0
    if remaining_isins:
        print(f"  - {DIRECT_SPREAD_FIELD} 없는 {len(remaining_isins)}개 종목 -> 채권 수익률 히스토리 조회 (폴백)...")
        bond_yld, used_field, used_fmt = _bdh_bulk_with_fallback(
            remaining_isins, PRIMARY_BOND_FIELD, FALLBACK_YIELD_FIELD, start_date, end_date
        )
        print(f"    -> {len(bond_yld)}/{len(remaining_isins)}개 종목 확보 (필드: {used_field}, 티커포맷: {used_fmt})")

        # 동적 매칭을 위해 만기 관계없이 전체 GT 텀(1,2,3,5,10,30)을 다 가져옴 (0은 1과 동일 티커라 제외)
        all_tenor_codes = [1, 2, 3, 5, 10, 30]
        gt_tickers = sorted(set(GT_TENOR_TO_TICKER[t] for t in all_tenor_codes))

        print(f"  - GT 국채 수익률 조회 중 ({gt_tickers})...")
        gt_yld = _bdh_bulk(gt_tickers, GT_YLD_FIELD, start_date, end_date)
        print(f"    -> {len(gt_yld)}/{len(gt_tickers)}개 GT 티커 확보")

        gt_combined = pd.DataFrame({
            t: gt_yld[GT_TENOR_TO_TICKER[t]] for t in all_tenor_codes if GT_TENOR_TO_TICKER[t] in gt_yld
        }).sort_index().ffill()

        for _, row in remaining_df.iterrows():
            isin = row['ISIN']
            bond_series = bond_yld.get(isin)
            if bond_series is None:
                continue
            bond_series = bond_series.sort_index()

            maturity_date = row.get('만기일')
            if pd.notna(maturity_date) and not gt_combined.empty:
                gt_series = _build_dynamic_gt_series(bond_series.index, maturity_date, gt_combined)
                n_dynamic += 1
            else:
                # 만기일 없으면 기존 정적 매칭으로 폴백
                gt_ticker = GT_TENOR_TO_TICKER.get(row['GT텀'])
                gt_series = gt_yld.get(gt_ticker) if gt_ticker else None
                n_static_fallback += 1

            if gt_series is None:
                continue

            aligned = pd.DataFrame({'bond': bond_series, 'gt': gt_series}).dropna()
            if aligned.empty:
                continue

            spread_bp = (aligned['bond'] - aligned['gt']) * 100.0
            result[isin] = spread_bp.sort_index()

        print(f"    (직접필드 {len(result) - n_dynamic - n_static_fallback}개 / "
              f"동적 텀매칭 {n_dynamic}개 / 정적 폴백 {n_static_fallback}개)")
    else:
        print(f"    (전 종목 {DIRECT_SPREAD_FIELD} 직접필드로 확보, GT매칭 불필요)")

    if not etf_df.empty:
        print(f"  - ETF 스프레드 히스토리 조회 중 (YAS_YLD_SPREAD, {len(etf_df)}개)...")
        etf_isins = etf_df['ISIN'].tolist()
        etf_tickers = [f"{isin} US Equity" for isin in etf_isins]
        etf_raw = _bdh_bulk(etf_tickers, 'YAS_YLD_SPREAD', start_date, end_date)
        for isin in etf_isins:
            series = etf_raw.get(f"{isin} US Equity")
            if series is not None and len(series) > 0:
                result[isin] = series.sort_index()
        print(f"    -> {sum(1 for i in etf_isins if i in result)}/{len(etf_isins)}개 ETF 확보")

    if not index_df.empty:
        print(f"  - 인덱스 스프레드 히스토리 조회 중 (PX_LAST, {len(index_df)}개)...")
        index_isins = index_df['ISIN'].tolist()
        index_tickers = [f"{isin} Index" for isin in index_isins]
        index_raw = _bdh_bulk(index_tickers, 'PX_LAST', start_date, end_date)
        for isin in index_isins:
            series = index_raw.get(f"{isin} Index")
            if series is not None and len(series) > 0:
                # LUACOAS 등 일부 인덱스는 PX_LAST가 %(예: 0.70 = 70bp) 단위로 나와서 bp로 환산
                result[isin] = (series.sort_index() * 100.0)
        print(f"    -> {sum(1 for i in index_isins if i in result)}/{len(index_isins)}개 인덱스 확보")

    return result


# ------------------------------------------------------------------
# 4. 변동성 계산 (개별 + 버킷 pooled + credibility 가중)
# ------------------------------------------------------------------
# ------------------------------------------------------------------
# 4. 변동성 계산 (개별 + 버킷 pooled + credibility 가중)
#    - 전체기간(3년) sigma 와 최근 60일 rolling sigma를 둘 다 계산
# ------------------------------------------------------------------
LEVEL_ORDER = ['L4', 'L3', 'L2', 'L1', 'L0']
MIN_ISSUER_POOL_OBS = 20  # L_ISSUER(같은 발행자+같은 만기구간) 풀을 신뢰하기 위한 최소 관측치


def _level_keys(row):
    return {
        'L4': f"L4|{row['등급']}|{row['만기구간']}|{row['섹터']}|{row['지역그룹']}",
        'L3': f"L3|{row['등급']}|{row['만기구간']}|{row['섹터']}",
        'L2': f"L2|{row['등급']}|{row['만기구간']}",
        'L1': f"L1|{row['등급']}",
        'L0': "L0|GLOBAL",
        # 등급 라벨이 오래되어 실제와 달라도(예: 강등 미반영), 같은 발행자+같은 만기구간이면
        # 신용위험 자체는 거의 동일하므로 등급 무관하게 별도 풀 구성
        'L_ISSUER': f"LISSUER|{row['TICKER']}|{row['만기구간']}",
    }


def _exclude_bad_date_changes(series: pd.Series) -> pd.Series:
    """
    레벨 시계열(스프레드 수준)에서 diff를 계산하되, KNOWN_BAD_DATES에 해당하는
    날짜로 '들어가는' diff와 그 다음 날 '나가는' diff를 둘 다 제외한다.
    (하루치 데이터가 잘못 찍히면 그 전날->당일, 당일->다음날 diff 둘 다 오염되므로)
    """
    s = series.sort_index()
    diffs = s.diff()

    idx_dates = pd.Series(s.index, index=s.index).dt.strftime('%Y-%m-%d')
    prev_dates = idx_dates.shift(1)

    bad_mask = idx_dates.isin(KNOWN_BAD_DATES) | prev_dates.isin(KNOWN_BAD_DATES)
    diffs = diffs[~bad_mask]
    return diffs.dropna()


def _apply_absolute_cap(diffs: pd.Series) -> pd.Series:
    """하루 ABSOLUTE_OUTLIER_CAP_BP(bp) 넘는 변화는 데이터 오류로 간주하고 제거"""
    return diffs[diffs.abs() <= ABSOLUTE_OUTLIER_CAP_BP]


def _weekly_scaled_changes(daily_changes: pd.Series) -> pd.Series:
    """
    일별(이미 정제된) Δspread를 주 단위로 합산한 뒤 sqrt(5)로 나눠 '일별 환산' 스케일로 되돌린다.
    가끔 거래되며 비드/오퍼 노이즈로 일별 변동이 과장되는 단기물(0-3Y)에 적용.
    """
    if daily_changes.empty:
        return daily_changes
    weekly = daily_changes.resample('W').sum(min_count=1).dropna()
    return weekly / np.sqrt(5)


def _clean_changes_for_sigma(series: pd.Series) -> pd.Series:
    """sigma 계산용: 알려진 오류일자 제외 + 절대 임계치(100bp) 초과 자동 제거"""
    diffs = _exclude_bad_date_changes(series)
    return _apply_absolute_cap(diffs)


def _winsorize(values, pctl=WINSORIZE_PCTL):
    """
    상하위 pctl 만큼 절단(clip). 고립된 이상치(데이터 오류/일시적 bad print)가
    표준편차를 왜곡하는 걸 방지. 관측치가 너무 적으면(quantile이 불안정) 그대로 둠.
    """
    s = pd.Series(values, dtype=float).dropna()
    if len(s) < 10:
        return s
    lo, hi = s.quantile(pctl), s.quantile(1 - pctl)
    return s.clip(lo, hi)


def _compute_sigma_metric(tagged_df: pd.DataFrame, spread_history: dict,
                           changes_selector, min_obs_indiv: int, credibility_k: int) -> dict:
    """
    changes_selector(full_changes: pd.Series) -> pd.Series
        전체 Δspread 시계열에서 이번 metric에 쓸 부분을 뽑아내는 함수
        (전체기간이면 그대로, 60일 롤링이면 tail(ROLLING_WINDOW))
    반환: {isin: dict(n, sigma_i, sigma_b, bucket_n, actual_level, sigma_final, weight)}
    """
    indiv_sigma, indiv_n = {}, {}
    level_keys_by_isin = {}
    daily_changes_by_level_key = {}
    is_short_tenor_by_isin = {}

    for _, row in tagged_df.iterrows():
        isin = row['ISIN']
        keys = _level_keys(row)
        level_keys_by_isin[isin] = keys
        is_short_tenor_by_isin[isin] = row.get('만기구간') == '0-3Y'

        series = spread_history.get(isin)
        if series is None or len(series) < 2:
            indiv_sigma[isin] = np.nan
            indiv_n[isin] = 0
            continue

        full_changes = _clean_changes_for_sigma(series)
        changes = changes_selector(full_changes)

        # 단기물(0-3Y)은 가끔 거래되며 비드/오퍼 노이즈로 일별 변동이 과장되는 경향이 있어
        # 주간 합산 -> sqrt(5)로 일별 환산 스케일로 바꿔서 노이즈를 완화
        if row.get('만기구간') == '0-3Y':
            changes = _weekly_scaled_changes(changes)

        indiv_n[isin] = len(changes)
        changes_wz = _winsorize(changes)
        indiv_sigma[isin] = changes_wz.std() if len(changes_wz) >= 2 else np.nan

        changes_list = changes.tolist()  # 풀링은 원본으로 모으고, 풀링된 표본 자체를 나중에 winsorize
        # 추가종목(ETF/자사채 등, 출처='추가')은 등급이 임시(NR) 라벨이라
        # 다른 실제 무등급 채권의 폴백 풀을 오염시키지 않도록 풀링에서 제외 (자기 자신의 sigma는 그대로 계산됨)
        if row.get('출처') != '추가':
            is_short_tenor = row.get('만기구간') == '0-3Y'
            for lvl_name, lvl_key in keys.items():
                # 단기물(주간스케일)은 만기가 섞이는 L1/L0에는 풀링하지 않음 (장기물 일별스케일과 척도가 달라 섞이면 왜곡)
                if is_short_tenor and lvl_name in ('L1', 'L0'):
                    continue
                daily_changes_by_level_key.setdefault(lvl_key, []).extend(changes_list)

    level_sigma = {
        k: (_winsorize(v).std() if len(v) >= 2 else np.nan)
        for k, v in daily_changes_by_level_key.items()
    }
    level_n = {k: len(v) for k, v in daily_changes_by_level_key.items()}

    def resolve_bucket_sigma(isin, assigned_level):
        keys = level_keys_by_isin[isin]

        # 0) 같은 발행자 + 같은 만기구간 풀을 등급기반 계층보다 먼저 시도
        #    (등급 라벨이 오래돼서 실제 등급과 달라도 같은 회사면 신용위험이 거의 같으므로,
        #     충분한 관측치가 있으면 이걸 최우선으로 신뢰)
        issuer_key = keys.get('L_ISSUER')
        if issuer_key:
            issuer_sigma = level_sigma.get(issuer_key, np.nan)
            issuer_n = level_n.get(issuer_key, 0)
            if not np.isnan(issuer_sigma) and issuer_n >= MIN_ISSUER_POOL_OBS:
                return issuer_sigma, issuer_n, 'L_ISSUER'

        # 1) 기존 등급 기반 계층(L4->L3->L2->L1->L0) 순회
        start_idx = LEVEL_ORDER.index(assigned_level)
        # 단기물(0-3Y, 주간스케일)은 L1/L0(만기 무관, 일별스케일 장기물 포함)까지 폴백하면
        # 척도가 섞이므로 L2(등급x만기, 만기 동일그룹)까지만 시도
        allowed_levels = LEVEL_ORDER[start_idx:]
        if is_short_tenor_by_isin.get(isin):
            allowed_levels = [lvl for lvl in allowed_levels if lvl in ('L4', 'L3', 'L2')]
        for lvl in allowed_levels:
            key = keys[lvl]
            sigma = level_sigma.get(key, np.nan)
            if not np.isnan(sigma):
                return sigma, level_n.get(key, 0), lvl
        return np.nan, 0, None

    out = {}
    for _, row in tagged_df.iterrows():
        isin = row['ISIN']
        n = indiv_n.get(isin, 0)
        sigma_i = indiv_sigma.get(isin, np.nan)
        assigned_level = row['적용레벨']

        sigma_b, bucket_n_used, actual_level_used = resolve_bucket_sigma(isin, assigned_level)

        if n >= min_obs_indiv and not np.isnan(sigma_i):
            w = n / (n + credibility_k)
            sigma_final = (w * sigma_i + (1 - w) * sigma_b) if not np.isnan(sigma_b) else sigma_i
        else:
            sigma_final = sigma_b
            w = 0.0

        out[isin] = {
            'n': n,
            'sigma_i': sigma_i,
            'sigma_b': sigma_b,
            'bucket_n': bucket_n_used,
            'actual_level': actual_level_used,
            'sigma_final': sigma_final,
            'weight': w,
        }
    return out


def flag_outlier_days(tagged_df: pd.DataFrame, spread_history: dict,
                       n_top: int = 15, ratio_threshold: float = 8.0) -> pd.DataFrame:
    """
    각 종목의 일별 Δspread 중, 그 종목 자체의 중앙값 절대변화 대비 ratio_threshold배
    이상 튀는 날을 이상치 후보로 뽑는다 (winsorize와 별개로, 사람이 직접 확인하도록
    콘솔에 보여주기 위한 용도). KDB(US500630ED65) 사례처럼 종목 고유 데이터 오류를
    조기에 발견하기 위함.
    """
    isin_to_ticker = dict(zip(tagged_df['ISIN'], tagged_df['TICKER']))
    rows = []
    for isin, series in spread_history.items():
        changes = _exclude_bad_date_changes(series)
        if len(changes) < 20:
            continue
        med_abs = changes.abs().median()
        if med_abs == 0 or np.isnan(med_abs):
            continue
        extreme = changes[changes.abs() >= med_abs * ratio_threshold]
        for date, val in extreme.items():
            rows.append({
                'ISIN': isin,
                'TICKER': isin_to_ticker.get(isin, ''),
                '날짜': date.strftime('%Y-%m-%d'),
                'Δspread_bp': round(val, 2),
                '그종목_중앙값대비_배수': round(abs(val) / med_abs, 1),
            })

    if not rows:
        return pd.DataFrame()

    out = pd.DataFrame(rows).sort_values('그종목_중앙값대비_배수', ascending=False)
    return out.head(n_top)


def _in_stress_periods(changes: pd.Series) -> pd.Series:
    """STRESS_PERIODS에 해당하는 날짜의 변화값만 남긴다"""
    if changes.empty:
        return changes
    mask = pd.Series(False, index=changes.index)
    for start, end in STRESS_PERIODS:
        mask |= (changes.index >= pd.Timestamp(start)) & (changes.index <= pd.Timestamp(end))
    return changes[mask]


def _last_n_days(changes: pd.Series, n_business_days: int) -> pd.Series:
    """전체 히스토리에서 실제 달력상 최근 n_business_days(영업일 환산)만 슬라이싱"""
    if changes.empty:
        return changes
    cutoff = pd.Timestamp(datetime.today() - timedelta(days=int(n_business_days * 1.45)))
    return changes[changes.index >= cutoff]


def compute_volatility(tagged_df: pd.DataFrame, spread_history: dict) -> pd.DataFrame:
    # 전체기간(3년) 지표 - fetch는 5.5년치 했지만 여기선 실제 최근 3년만 사용
    full_metrics = _compute_sigma_metric(
        tagged_df, spread_history,
        changes_selector=lambda c: _last_n_days(c, FULL_METRIC_LOOKBACK_DAYS),
        min_obs_indiv=MIN_OBS_FOR_INDIV,
        credibility_k=CREDIBILITY_K,
    )
    # 최근 60일 rolling 지표
    rolling_metrics = _compute_sigma_metric(
        tagged_df, spread_history,
        changes_selector=lambda c: c.tail(ROLLING_WINDOW),
        min_obs_indiv=MIN_OBS_FOR_INDIV_ROLLING,
        credibility_k=CREDIBILITY_K_ROLLING,
    )
    # 스프레드 확대(스트레스) 구간만 뽑은 지표
    stress_metrics = _compute_sigma_metric(
        tagged_df, spread_history,
        changes_selector=_in_stress_periods,
        min_obs_indiv=MIN_OBS_FOR_INDIV_STRESS,
        credibility_k=CREDIBILITY_K_STRESS,
    )

    def r(v):
        return round(v, 3) if v is not None and not (isinstance(v, float) and np.isnan(v)) else None

    rows_out = []
    for _, row in tagged_df.iterrows():
        isin = row['ISIN']
        fm = full_metrics[isin]
        rm = rolling_metrics[isin]
        sm = stress_metrics[isin]

        rows_out.append({
            'ISIN': isin,
            'TICKER': row['TICKER'],
            '등급': row['등급'],
            '만기구간': row['만기구간'],
            '섹터': row['섹터'],
            '지역그룹': row['지역그룹'],
            '배정레벨': row['적용레벨'],

            # 전체기간(3년) 지표
            '실사용레벨_5Y': fm['actual_level'],
            '개별관측치수_5Y': fm['n'],
            '개별sigma_bp_5Y': r(fm['sigma_i']),
            '버킷sigma_bp_5Y': r(fm['sigma_b']),
            '버킷관측치수_5Y': fm['bucket_n'],
            'credibility_weight_5Y': r(fm['weight']),
            '최종sigma_bp_5Y': r(fm['sigma_final']),

            # 최근 60일 rolling 지표
            '실사용레벨_60D': rm['actual_level'],
            '개별관측치수_60D': rm['n'],
            '개별sigma_bp_60D': r(rm['sigma_i']),
            '버킷sigma_bp_60D': r(rm['sigma_b']),
            '버킷관측치수_60D': rm['bucket_n'],
            'credibility_weight_60D': r(rm['weight']),
            '최종sigma_bp_60D': r(rm['sigma_final']),

            # 스트레스(스프레드 확대) 구간 지표
            '실사용레벨_stress': sm['actual_level'],
            '개별관측치수_stress': sm['n'],
            '개별sigma_bp_stress': r(sm['sigma_i']),
            '버킷sigma_bp_stress': r(sm['sigma_b']),
            '버킷관측치수_stress': sm['bucket_n'],
            'credibility_weight_stress': r(sm['weight']),
            '최종sigma_bp_stress': r(sm['sigma_final']),
        })

    return pd.DataFrame(rows_out)


BENCHMARK_ISINS = ['LQD', 'LUACOAS']  # 베타 계산 기준 (LQD ETF, Bloomberg US Corp Index OAS)


def compute_beta_to_benchmarks(tagged_df: pd.DataFrame, spread_history: dict,
                                benchmark_isins: list = None) -> pd.DataFrame:
    """
    각 종목의 Δspread가 벤치마크(LQD, LUACOAS 등) Δspread 대비 얼마나 민감하게 움직이는지
    베타(회귀계수)와 상관계수를 3년/스트레스 두 기준으로 계산.
    beta = Cov(종목, 벤치마크) / Var(벤치마크)
    """
    benchmark_isins = benchmark_isins or BENCHMARK_ISINS
    benchmark_changes = {}
    for b in benchmark_isins:
        series = spread_history.get(b)
        if series is not None and len(series) >= 2:
            benchmark_changes[b] = _clean_changes_for_sigma(series)

    period_selectors = {
        '5Y': lambda c: _last_n_days(c, FULL_METRIC_LOOKBACK_DAYS),
        'stress': _in_stress_periods,
    }

    rows = []
    for _, row in tagged_df.iterrows():
        isin = row['ISIN']
        if isin in benchmark_isins:
            continue

        result_row = {'ISIN': isin, 'TICKER': row['TICKER']}
        series = spread_history.get(isin)
        bond_changes_full = _clean_changes_for_sigma(series) if series is not None and len(series) >= 2 else None

        for b in benchmark_isins:
            bench_full = benchmark_changes.get(b)
            for period_name, selector in period_selectors.items():
                beta_col = f'beta_vs_{b}_{period_name}'
                corr_col = f'corr_vs_{b}_{period_name}'
                if bond_changes_full is None or bench_full is None:
                    result_row[beta_col] = None
                    result_row[corr_col] = None
                    continue
                bond_sub = selector(bond_changes_full)
                bench_sub = selector(bench_full)
                aligned = pd.DataFrame({'bond': bond_sub, 'bench': bench_sub}).dropna()
                if len(aligned) < 20:
                    result_row[beta_col] = None
                    result_row[corr_col] = None
                    continue
                var = aligned['bench'].var()
                beta = aligned['bond'].cov(aligned['bench']) / var if var > 0 else None
                corr = aligned['bond'].corr(aligned['bench'])
                result_row[beta_col] = round(beta, 3) if beta is not None else None
                result_row[corr_col] = round(corr, 3) if pd.notna(corr) else None

        rows.append(result_row)

    beta_df = pd.DataFrame(rows)
    beta_df = _fill_beta_fallback(beta_df, tagged_df)
    return beta_df


def _fill_beta_fallback(beta_df: pd.DataFrame, tagged_df: pd.DataFrame) -> pd.DataFrame:
    """
    개별 히스토리가 부족해 beta/corr이 NaN인 종목을,
    1순위: 같은 발행자(티커) 다른 종목들 평균
    2순위: 같은 버킷(등급x섹터x만기구간) 평균
    3순위: 전체 평균
    순으로 채워준다 (신규 발행물 대응).
    """
    if beta_df.empty:
        return beta_df

    merged = beta_df.merge(
        tagged_df[['ISIN', '등급', '섹터', '만기구간']], on='ISIN', how='left'
    )
    value_cols = [c for c in beta_df.columns if c not in ('ISIN', 'TICKER')]

    for col in value_cols:
        ticker_avg = merged.groupby('TICKER')[col].transform('mean')
        merged[col] = merged[col].fillna(ticker_avg)

        bucket_avg = merged.groupby(['등급', '섹터', '만기구간'])[col].transform('mean')
        merged[col] = merged[col].fillna(bucket_avg)

        global_avg = merged[col].mean()
        merged[col] = merged[col].fillna(global_avg)
        merged[col] = merged[col].round(3)

    return merged[['ISIN', 'TICKER'] + value_cols]


def compute_bucket_rolling_trend(tagged_df: pd.DataFrame, spread_history: dict,
                                  group_col: str = '등급', window: int = ROLLING_WINDOW) -> pd.DataFrame:
    """
    3년 전체 기간에 대해, group_col 기준 버킷별로 '그 날짜 기준 과거 window일' pooled sigma를
    매일 계산한 시계열(추이)을 만든다. (합/제곱합 트릭으로 O(n_dates) 처리)
    반환: DataFrame, index=date, columns=버킷 값(예: 등급별 AAA/AA+/... ), values=sigma_bp
    """
    # 1) 종목별 Δspread를 날짜 index 기준으로 wide 테이블로 정리
    changes_wide = {}
    for _, row in tagged_df.iterrows():
        isin = row['ISIN']
        series = spread_history.get(isin)
        if series is None or len(series) < 2:
            continue
        changes_wide[isin] = series.sort_index().diff().dropna()

    if not changes_wide:
        return pd.DataFrame()

    wide_df = pd.DataFrame(changes_wide)  # index=date, columns=ISIN
    wide_df = wide_df.sort_index()

    isin_to_group = dict(zip(tagged_df['ISIN'], tagged_df[group_col]))
    groups = sorted(set(g for g in isin_to_group.values() if pd.notna(g)), key=str)

    trend = {}
    for g in groups:
        member_isins = [isin for isin, gv in isin_to_group.items() if gv == g and isin in wide_df.columns]
        if not member_isins:
            continue
        sub = wide_df[member_isins]

        # 날짜별 cross-sectional n / sum / sumsq
        n_t = sub.notna().sum(axis=1)
        sum_t = sub.sum(axis=1, skipna=True)
        sumsq_t = (sub ** 2).sum(axis=1, skipna=True)

        roll_n = n_t.rolling(window, min_periods=2).sum()
        roll_sum = sum_t.rolling(window, min_periods=1).sum()
        roll_sumsq = sumsq_t.rolling(window, min_periods=1).sum()

        mean = roll_sum / roll_n
        var = (roll_sumsq / roll_n) - mean ** 2
        var = var.clip(lower=0)  # 부동소수 오차로 아주 미세하게 음수 나오는 것 방지
        sigma = np.sqrt(var)
        sigma[roll_n < 2] = np.nan

        trend[g] = sigma

    return pd.DataFrame(trend).sort_index()


def _safe_save(save_fn, path: str):
    """
    파일이 다른 프로그램(엑셀 등)에서 열려있어 저장이 막혀도,
    전체 스크립트가 죽지 않고 경고만 남기고 계속 진행하도록 하는 헬퍼.
    """
    try:
        save_fn()
        print(f"    저장: {path}")
    except PermissionError:
        print(f"    [WARN] 저장 실패(파일이 다른 프로그램에서 열려있는 것으로 보임): {path} - 건너뜀")
    except Exception as e:
        print(f"    [WARN] 저장 실패: {path} - {e}")


# ------------------------------------------------------------------
# main
# ------------------------------------------------------------------
def fetch_current_ytm(tagged_df: pd.DataFrame) -> dict:
    """
    채권(자산유형=BOND)의 '현재' YTM을 BDP로 조회 (스크립트 실행 시점 기준 - 매번 새로 조회).
    ETF/INDEX는 YTM 개념이 없어 제외.
    반환: {ISIN: ytm(%)}
    """
    from xbbg import blp

    bond_df = tagged_df[tagged_df.get('자산유형', 'BOND') == 'BOND']
    isins = bond_df['ISIN'].tolist()
    tickers = [f"{isin} Corp" for isin in isins]

    result = {}
    CHUNK = 60
    for i in range(0, len(tickers), CHUNK):
        chunk = tickers[i:i + CHUNK]
        try:
            raw = blp.bdp(tickers=chunk, flds=['YAS_BOND_YLD'])
            if hasattr(raw, "to_pandas"):
                raw = raw.to_pandas()
        except Exception as e:
            print(f"    [WARN] YTM 조회 실패(chunk {i}): {e}")
            continue
        if raw is None or len(raw) == 0:
            continue
        cols_lower = {c.lower(): c for c in raw.columns}
        if {'ticker', 'field', 'value'}.issubset(cols_lower.keys()):
            for _, row in raw.iterrows():
                isin = str(row[cols_lower['ticker']]).replace(' Corp', '')
                val = row[cols_lower['value']]
                if pd.notna(val):
                    result[isin] = float(val)
    return result


def extract_current_spread(spread_history: dict) -> dict:
    """이미 받아온 히스토리 시계열의 최신(마지막) 값 = '현재 스프레드' (추가 조회 없음)"""
    result = {}
    for isin, series in spread_history.items():
        s = series.dropna()
        if len(s) > 0:
            result[isin] = s.iloc[-1]
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', required=True, help='잔고 xlsx 파일 경로')
    parser.add_argument('--output', default='credit_spread_volatility.json')
    parser.add_argument('--trend-group', default='등급',
                         choices=['등급', '섹터', '지역그룹', '만기구간'],
                         help='60일 롤링 추이를 뽑을 주요 버킷 기준 (기본: 등급)')
    parser.add_argument('--extra-instruments', default='extra_instruments.csv',
                         help='잔고에 없는 추가 분석 대상(자사채, 보유ETF 등) 정의 파일. 없으면 건너뜀')
    args = parser.parse_args()

    print("[1/5] 잔고 로드 및 버킷 태깅...")
    tagged = load_and_tag(args.input)

    extra = load_extra_instruments(args.extra_instruments)
    if not extra.empty:
        print(f"  -> 추가 분석 대상 {len(extra)}건 병합 ({args.extra_instruments}): "
              f"{extra['TICKER'].tolist()}")
        # 컬럼 순서/구성 맞춰서 concat
        common_cols = ['ISIN', 'TICKER', '등급', '섹터', 'GT텀', '만기구간', '지역그룹', '자산유형', '출처', '지역']
        tagged = pd.concat(
            [tagged.reindex(columns=common_cols), extra.reindex(columns=common_cols)],
            ignore_index=True
        )

    tagged = assign_bucket_levels(tagged)
    print(f"  -> 유니크 종목 {len(tagged)}개, 버킷 {tagged['버킷키'].nunique()}개")

    print("[2/5] Bloomberg BDH 히스토리컬 스프레드 조회 (YAS_BOND_YLD - GT)...")
    history = fetch_historical_spreads(tagged)
    print(f"  -> 히스토리 확보 종목 {len(history)}개 / {len(tagged)}개")

    print("  - 현재 YTM 조회 (BDP, 매 실행시 최신값)...")
    current_ytm = fetch_current_ytm(tagged)
    print(f"    -> {len(current_ytm)}개 종목 YTM 확보")
    current_spread = extract_current_spread(history)

    print("[3/5] 변동성 계산 (개별 + 버킷 + credibility 가중)...")
    result = compute_volatility(tagged, history)
    result['현재YTM'] = result['ISIN'].map(current_ytm)
    result['현재스프레드_bp'] = result['ISIN'].map(current_spread)
    result['지역'] = result['ISIN'].map(dict(zip(tagged['ISIN'], tagged['지역'])))

    print("  - 벤치마크(LQD, LUACOAS) 대비 베타/상관계수 계산...")
    # 단순 sigma 비율 (베타와 달리 방향성 없이, "몇 배 더/덜 흔들리는가"만 봄 - 항상 양수)
    for basis in ('5Y', 'stress'):
        sigma_col = f'최종sigma_bp_{basis}'
        for bench_isin, bench_label in [('LQD', 'LQD'), ('LUACOAS', 'LUACOAS')]:
            bench_rows = result.loc[result['ISIN'] == bench_isin, sigma_col]
            bench_sigma = bench_rows.iloc[0] if not bench_rows.empty else None
            ratio_col = f'sigma비율_vs_{bench_label}_{basis}'
            if bench_sigma is not None and pd.notna(bench_sigma) and bench_sigma > 0:
                result[ratio_col] = (result[sigma_col] / bench_sigma).round(2)
            else:
                result[ratio_col] = None

    # 콜러블 채권은 일반 채권과 옵션성이 달라 sigma비율로 직접 비교하는 게 부적절하므로
    # 벤치마크와 동일(1.0)로 고정 (콜옵션 조기상환 리스크가 스프레드 변동성에 왜곡된 영향을 줄 수 있음)
    CALLABLE_ISINS = {'XS3030377132', 'XS3017043053', 'FR0014010FH7', 'XS3028206350'}
    ratio_cols_all = [c for c in result.columns if c.startswith('sigma비율_vs_')]
    is_callable = result['ISIN'].isin(CALLABLE_ISINS)
    if is_callable.any():
        for col in ratio_cols_all:
            result.loc[is_callable, col] = 1.0
        print(f"    -> 콜러블 채권 {is_callable.sum()}개 sigma비율 1.0으로 고정: {sorted(CALLABLE_ISINS)}")

    beta_df = compute_beta_to_benchmarks(tagged, history)
    if not beta_df.empty:
        result = result.merge(beta_df.drop(columns=['TICKER']), on='ISIN', how='left')

        # LUACOAS(순수 인덱스) 상관관계는 약한데 LQD 베타만 유독 크게 나오는 경우
        # -> 진짜 시장연동이 아니라 우연한 노이즈 동조일 가능성이 높아 경고 플래그
        LOW_CORR_THRESHOLD = 0.3
        HIGH_BETA_THRESHOLD = 1.0
        for basis in ('5Y', 'stress'):
            corr_col = f'corr_vs_LUACOAS_{basis}'
            beta_col = f'beta_vs_LQD_{basis}'
            flag_col = f'베타신뢰도경고_{basis}'
            if corr_col in result.columns and beta_col in result.columns:
                result[flag_col] = result.apply(
                    lambda r: 'LQD베타 신뢰도낮음(우연동조 의심)'
                    if (pd.notna(r[corr_col]) and pd.notna(r[beta_col])
                        and abs(r[corr_col]) < LOW_CORR_THRESHOLD
                        and abs(r[beta_col]) >= HIGH_BETA_THRESHOLD)
                    else None,
                    axis=1
                )

    print("  - 잔여 결측치(빈칸) 최종 정리 (티커평균 -> 버킷평균 -> 전체평균)...")
    numeric_cols = [
        c for c in result.select_dtypes(include=[np.number]).columns
        if c not in ('개별관측치수_5Y', '버킷관측치수_5Y', '개별관측치수_60D', '버킷관측치수_60D',
                     '개별관측치수_stress', '버킷관측치수_stress')
    ]
    for col in numeric_cols:
        if result[col].isna().any():
            ticker_avg = result.groupby('TICKER')[col].transform('mean')
            result[col] = result[col].fillna(ticker_avg)
            bucket_avg = result.groupby(['등급', '섹터', '만기구간'])[col].transform('mean')
            result[col] = result[col].fillna(bucket_avg)
            result[col] = result[col].fillna(result[col].mean())

    outliers = flag_outlier_days(tagged, history)
    if not outliers.empty:
        print()
        print("[QC 경고] 종목별 중앙값 대비 8배 이상 튀는 날짜 (데이터 오류 의심 - 직접 확인 권장):")
        print(outliers.to_string(index=False))
        print()

    print("[4/5] 결과 저장...")
    xlsx_path = args.output.replace('.json', '.xlsx')
    _safe_save(lambda: result.to_excel(xlsx_path, index=False), xlsx_path)
    _safe_save(lambda: result.to_json(args.output, orient='records', force_ascii=False, indent=2), args.output)
    print(f"  -> {args.output} / {xlsx_path} 저장 완료")
    print()
    print(result[['ISIN', 'TICKER', '배정레벨',
                  '최종sigma_bp_5Y', '최종sigma_bp_60D', '최종sigma_bp_stress']].head(20).to_string())

    print(f"[5/5] 주요 버킷({args.trend_group}) 60일 롤링 추이 계산...")
    trend_df = compute_bucket_rolling_trend(tagged, history, group_col=args.trend_group)
    if not trend_df.empty:
        trend_xlsx = args.output.replace('.json', f'_trend_{args.trend_group}.xlsx')
        _safe_save(lambda: trend_df.to_excel(trend_xlsx), trend_xlsx)
        print(f"  -> {trend_xlsx} 저장 완료 (버킷 {list(trend_df.columns)})")
    else:
        print("  -> 추이 계산할 데이터가 없습니다.")


if __name__ == '__main__':
    main()
