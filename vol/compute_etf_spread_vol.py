# -*- coding: utf-8 -*-
"""
compute_etf_spread_vol.py
----------------------------
LQD(투자등급 회사채 ETF), VCIT(중기 투자등급 회사채 ETF)의 YAS_YLD_SPREAD 히스토리를
받아서 개별 채권과 동일한 방식으로 변동성을 계산한다.

목적: 개별 회사채(Bloomberg 평가가격, BVAL 등 스무딩 가능성 있음) sigma와
      실제 거래되는 유동성 높은 ETF의 spread sigma를 나란히 비교해서,
      "개별 채권 데이터가 스무딩되어 변동성이 과소평가된 것 아닌가"를 검증하기 위함.

사용법:
    python compute_etf_spread_vol.py
"""

import numpy as np
import pandas as pd
from datetime import datetime, timedelta

import compute_credit_spread_volatility as core

TICKERS = {
    'LQD': 'LQD US Equity',
    'VCIT': 'VCIT US Equity',
}
SPREAD_FIELD = 'YAS_YLD_SPREAD'
LOOKBACK_DAYS = core.LOOKBACK_DAYS  # 기존 채권 분석과 동일한 3년 기준


def fetch_etf_spread_history() -> dict:
    from xbbg import blp

    end_date = datetime.today()
    start_date = end_date - timedelta(days=int(LOOKBACK_DAYS * 1.45))

    result = {}
    for name, ticker in TICKERS.items():
        try:
            raw = blp.bdh(
                tickers=[ticker], flds=[SPREAD_FIELD],
                start_date=start_date.strftime('%Y-%m-%d'),
                end_date=end_date.strftime('%Y-%m-%d'),
            )
        except Exception as e:
            print(f"  [WARN] {name}({ticker}) BDH 실패: {e}")
            continue

        if hasattr(raw, "to_pandas"):
            raw = raw.to_pandas()
        elif not isinstance(raw, pd.DataFrame):
            raw = pd.DataFrame(raw)

        if raw is None or raw.empty:
            print(f"  [WARN] {name}({ticker}): 빈 결과")
            continue

        if isinstance(raw.columns, pd.MultiIndex):
            try:
                series = raw[(ticker, SPREAD_FIELD)].dropna()
            except KeyError:
                print(f"  [WARN] {name}: MultiIndex 컬럼에서 못 찾음 - {list(raw.columns)}")
                continue
        else:
            lower_cols = set(c.lower() for c in raw.columns)
            if {'ticker', 'field', 'value'}.issubset(lower_cols):
                raw.columns = [c.lower() for c in raw.columns]
                sub = raw[(raw['ticker'] == ticker) & (raw['field'] == SPREAD_FIELD)]
                series = sub.set_index('date')['value'].dropna()
                series.index = pd.to_datetime(series.index)
            else:
                # 단순 wide 포맷 (컬럼이 필드명 하나)
                col = raw.columns[0]
                series = raw[col].dropna()
                series.index = pd.to_datetime(series.index)

        if len(series) > 0:
            result[name] = series.sort_index()
            print(f"  -> {name}: {len(series)}개 관측치 확보")
        else:
            print(f"  [WARN] {name}: 데이터 0건")

    return result


def analyze(series: pd.Series) -> dict:
    changes = series.diff().dropna()
    if len(changes) < 5:
        return {'n_obs': len(changes)}

    lo, hi = changes.quantile(0.01), changes.quantile(0.99)
    winsorized = changes.clip(lo, hi)
    weekly = series.resample('W').last().dropna().diff().dropna()

    # 60일 스냅샷
    recent_60 = changes.tail(60)

    abs_changes = changes.abs().sort_values(ascending=False)
    top5 = abs_changes.head(5)

    return {
        'n_obs': len(changes),
        'std_전체_3Y_bp': round(changes.std(), 3),
        'std_winsorized_3Y_bp': round(winsorized.std(), 3),
        'std_60D_bp': round(recent_60.std(), 3) if len(recent_60) >= 20 else None,
        'std_주간리샘플_bp': round(weekly.std(), 3) if len(weekly) >= 5 else None,
        '0변화_비율': round((changes == 0).mean(), 3),
        'top5_최대변화_bp': [round(v, 2) for v in top5.tolist()],
        'top5_날짜': [d.strftime('%Y-%m-%d') for d in top5.index],
    }


def main():
    print("[1/2] LQD/VCIT YAS_YLD_SPREAD 히스토리 조회...")
    history = fetch_etf_spread_history()

    print("\n[2/2] 변동성 분석...")
    rows = []
    for name, series in history.items():
        stats = analyze(series)
        stats['ETF'] = name
        rows.append(stats)

    if not rows:
        print("데이터를 가져오지 못했습니다.")
        return

    result = pd.DataFrame(rows).set_index('ETF')
    print()
    print(result.to_string())

    out_path = 'etf_spread_volatility.csv'
    result.to_csv(out_path, encoding='utf-8-sig')
    result.to_excel(out_path.replace('.csv', '.xlsx'))
    print(f"\n저장 완료: {out_path} / {out_path.replace('.csv', '.xlsx')}")

    print()
    print("=== 참고: 개별 채권 sigma 분포와 비교하려면 ===")
    print("credit_spread_volatility.csv 의 최종sigma_bp_3Y 컬럼과 위 LQD/VCIT std_전체_3Y_bp를 나란히 놓고 보세요.")
    print("LQD/VCIT가 개별 채권들보다 뚜렷이 높게 나오면, 개별 채권 데이터의 스무딩 가능성을 시사합니다.")


if __name__ == '__main__':
    main()
