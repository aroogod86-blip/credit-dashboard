# -*- coding: utf-8 -*-
"""
diagnose_specific_vol.py
--------------------------
특정 종목들의 실제 일별 Δspread 시계열을 뽑아서
"스테일(정체) 프라이싱 후 점프" 패턴이 있는지, 이상치 몇 개가 sigma를 좌우하는지 진단한다.

지금 문제의식: KP 준정부채(EIBKOR/KDB/INDKOR)의 sigma가 회사채(LGENSO/PKX/DAESEC)보다
낮아야 할 것 같은데 실제로는 그렇지 않음 -> 룩백 기간 문제인지, 스테일프라이싱/섹터분류
문제인지 구분하기 위한 진단.

사용법:
    python diagnose_specific_vol.py --input "8874잔고_260709.xlsx"
"""

import argparse

import numpy as np
import pandas as pd

import compute_credit_spread_volatility as core


# 진단 대상: 티커별 대표 ISIN (여러 개 보유 중이면 가장 많이 나오는 것 자동 선택)
DEFAULT_TARGET_TICKERS = ['EIBKOR', 'KDB', 'INDKOR', 'LGENSO', 'PKX', 'DAESEC']


def pick_representative_isin(tagged: pd.DataFrame, raw_lots_isin_counts: dict, ticker: str) -> str:
    candidates = tagged[tagged['TICKER'] == ticker]['ISIN'].tolist()
    if not candidates:
        return None
    # lot 수(보유 빈도) 기준으로 가장 대표성 있는 종목 선택
    return max(candidates, key=lambda i: raw_lots_isin_counts.get(i, 0))


def analyze_series(spread_series: pd.Series) -> dict:
    s = spread_series.sort_index()
    changes = s.diff().dropna()
    if len(changes) < 5:
        return {'n_obs': len(changes)}

    zero_ratio = (changes == 0).mean()
    # 0이 아니어도 아주 미세한 변화(0.1bp 미만)까지 포함한 "사실상 정체" 비율
    near_zero_ratio = (changes.abs() < 0.1).mean()
    abs_changes = changes.abs().sort_values(ascending=False)
    top5 = abs_changes.head(5)

    # winsorized (상하위 1% 제거)
    lo, hi = changes.quantile(0.01), changes.quantile(0.99)
    winsorized = changes.clip(lo, hi)

    # 주간 리샘플 (매주 마지막 값 기준 변화) - 스테일/점프 패턴이면 완만해짐
    weekly = s.resample('W').last().dropna().diff().dropna()

    return {
        'n_obs': len(changes),
        'std_전체_bp': changes.std(),
        '0변화_비율': zero_ratio,
        '거의0변화_비율(<0.1bp)': near_zero_ratio,
        'std_winsorized_bp': winsorized.std(),
        'std_주간리샘플_bp': weekly.std() if len(weekly) >= 5 else np.nan,
        'top5_최대변화_bp': [round(v, 2) for v in top5.tolist()],
        'top5_날짜': [d.strftime('%Y-%m-%d') for d in top5.index],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', required=True)
    parser.add_argument('--tickers', default='',
                         help='진단할 티커 콤마구분 (예: NORBK,NOMURA,POHANG,PKX,NTT). 미지정시 기본 리스트 사용')
    parser.add_argument('--isins', default='',
                         help='특정 ISIN을 직접 지정 (콤마구분). 지정하면 --tickers 대신 이 ISIN들을 정확히 진단')
    args = parser.parse_args()

    print("[1/2] 잔고 로드 및 대상 종목 선정...")
    tagged = core.load_and_tag(args.input)

    if args.isins:
        target_isins = {}
        for isin in [i.strip() for i in args.isins.split(',') if i.strip()]:
            match = tagged[tagged['ISIN'] == isin]
            ticker = match.iloc[0]['TICKER'] if not match.empty else isin
            target_isins[f"{ticker}({isin})"] = isin
    else:
        target_tickers = (
            [t.strip().upper() for t in args.tickers.split(',') if t.strip()]
            if args.tickers else DEFAULT_TARGET_TICKERS
        )
        raw = pd.read_excel(args.input, sheet_name=0, header=None)
        raw_isins = raw.iloc[3:][6].dropna().tolist()
        raw_lots_isin_counts = pd.Series(raw_isins).value_counts().to_dict()

        target_isins = {}
        for t in target_tickers:
            isin = pick_representative_isin(tagged, raw_lots_isin_counts, t)
            if isin:
                target_isins[t] = isin
            else:
                print(f"  [WARN] {t} 종목을 찾을 수 없습니다.")

    subset = tagged[tagged['ISIN'].isin(target_isins.values())]
    print(f"  -> 대상: {target_isins}")

    print("[2/2] Bloomberg BDH 히스토리 조회 및 분석...")
    history = core.fetch_historical_spreads(subset, lookback_days=core.LOOKBACK_DAYS)

    print()
    print("=" * 100)
    for t, isin in target_isins.items():
        row = tagged[tagged['ISIN'] == isin].iloc[0]
        print(f"\n### {t} ({isin}) - 등급:{row['등급']} 섹터:{row['섹터']} 만기구간:{row['만기구간']}")
        series = history.get(isin)
        if series is None:
            print("  히스토리 없음")
            continue
        result = analyze_series(series)
        for k, v in result.items():
            print(f"  {k}: {v}")


if __name__ == '__main__':
    main()
