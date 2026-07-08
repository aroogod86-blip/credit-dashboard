# -*- coding: utf-8 -*-
"""
backfill_hyperscaler_history.py
--------------------------------
hyperscaler_history.json 에 올해 1월 1일 ~ 오늘까지의 일별 스프레드를
BDH(Bloomberg Data History)로 한 번에 채워 넣는 1회성 스크립트.

이 스크립트를 실행하면 MTD/YTD 변동 계산에 필요한 과거 데이터가 바로 채워지므로,
export_hyperscaler_dashboard.py를 매일 돌리기 시작한 첫날부터 MTD/YTD가 표시됩니다.

실행법:
  python backfill_hyperscaler_history.py

전제조건:
  - export_hyperscaler_dashboard.py, hyperscaler_bond_universe.csv 와 같은 폴더에서 실행
  - Bloomberg Terminal 로그인 상태
"""

import sys
import datetime as dt

import pandas as pd

try:
    from xbbg import blp
except ImportError:
    print("[ERROR] xbbg가 설치되어 있지 않습니다. 'pip install xbbg' 실행 후 재시도하세요.")
    sys.exit(1)

# 같은 폴더의 export_hyperscaler_dashboard.py 재사용 (경로/필드/유틸 함수 중복 방지)
import export_hyperscaler_dashboard as core

START_DATE = dt.date(dt.date.today().year - 1, 12, 15)   # 작년 말(YTD 기준값) 확보를 위해 12/15부터
END_DATE = dt.date.today()


def _bdh_to_nested_dict(df, field: str, tickers: list, label: str) -> dict:
    """
    blp.bdh() 결과를 {ticker: {date_str: value}} 형태로 안전하게 변환.
    xbbg 버전에 따라 wide(MultiIndex 컬럼) 또는 long(tidy: date/ticker/field/value) 포맷이 올 수 있어
    두 가지 다 처리.
    """
    df = core._coerce_to_pandas(df)

    if df is None:
        print(f"[ERROR] {label} blp.bdh() 응답이 None 입니다.")
        return {}
    if not isinstance(df, pd.DataFrame):
        print(f"[ERROR] {label} 응답이 DataFrame이 아닙니다 (실제 타입: {type(df)}).")
        return {}
    if df.empty:
        print(f"[ERROR] {label} 응답이 빈 DataFrame 입니다.")
        return {}

    print(f"[DEBUG] {label} 응답 컬럼: {list(df.columns)[:10]}{'...' if len(df.columns) > 10 else ''} "
          f"/ shape: {df.shape}")

    result = {}

    # ---- Case A: long(tidy) 포맷 (date/ticker/field/value 컬럼) ----
    if not isinstance(df.columns, pd.MultiIndex):
        cols_lower = {str(c).lower() for c in df.columns}
        if {"date", "ticker", "field", "value"}.issubset(cols_lower):
            col_map = {str(c).lower(): c for c in df.columns}
            sub = df[df[col_map["field"]].astype(str).str.lower() == field.lower()]
            for _, row in sub.iterrows():
                t = str(row[col_map["ticker"]]).strip().upper()
                v = row[col_map["value"]]
                if pd.isna(v):
                    continue
                date_str = pd.Timestamp(row[col_map["date"]]).strftime("%Y-%m-%d")
                result.setdefault(t, {})[date_str] = float(v)
            return result

        # 컬럼이 그냥 티커명 하나씩인 단순 wide 포맷 (단일 필드 요청 시 xbbg 기본 동작)
        for col in df.columns:
            ticker = str(col).strip().upper()
            series = df[col]
            for idx, v in series.items():
                if pd.isna(v):
                    continue
                date_str = pd.Timestamp(idx).strftime("%Y-%m-%d")
                result.setdefault(ticker, {})[date_str] = float(v)
        return result

    # ---- Case B: wide 포맷, MultiIndex 컬럼 (ticker, field) ----
    for col in df.columns:
        if len(col) >= 2 and str(col[1]).lower() == field.lower():
            ticker = str(col[0]).strip().upper()
            series = df[col]
            for idx, v in series.items():
                if pd.isna(v):
                    continue
                date_str = pd.Timestamp(idx).strftime("%Y-%m-%d")
                result.setdefault(ticker, {})[date_str] = float(v)
    return result


def main():
    print(f"[INFO] 백필 기간: {START_DATE} ~ {END_DATE}")
    universe = core.load_universe()
    isins = universe["isin"].tolist()
    gt_tickers = sorted(set(universe["gt_ticker"].tolist()))

    bond_field = core.YIELD_FIELD
    print(f"[INFO] 채권 {len(isins)}건 히스토리({bond_field}) pull 중... (시간이 걸릴 수 있습니다)")
    bond_df = blp.bdh(tickers=isins, flds=[bond_field],
                       start_date=START_DATE.isoformat(), end_date=END_DATE.isoformat())
    bond_hist = _bdh_to_nested_dict(bond_df, bond_field, isins, "[채권 히스토리]")

    if not bond_hist:
        # YAS_BOND_YLD 등 일부 필드는 BDP(현재값)는 되지만 BDH(히스토리)에서는 비어있을 수 있음.
        # 이 경우 GT벤치마크에서 이미 검증된 YLD_YTM_MID로 자동 재시도.
        fallback_field = core.GT_YIELD_FIELD
        print(f"[WARN] {bond_field} 히스토리가 비어있어 {fallback_field}로 재시도합니다...")
        bond_field = fallback_field
        bond_df = blp.bdh(tickers=isins, flds=[bond_field],
                           start_date=START_DATE.isoformat(), end_date=END_DATE.isoformat())
        bond_hist = _bdh_to_nested_dict(bond_df, bond_field, isins, "[채권 히스토리-재시도]")

    print(f"[INFO] 벤치마크 {len(gt_tickers)}건 히스토리({core.GT_YIELD_FIELD}) pull 중...")
    gt_df = blp.bdh(tickers=gt_tickers, flds=[core.GT_YIELD_FIELD],
                     start_date=START_DATE.isoformat(), end_date=END_DATE.isoformat())
    gt_hist = _bdh_to_nested_dict(gt_df, core.GT_YIELD_FIELD, gt_tickers, "[벤치마크 히스토리]")

    if not bond_hist or not gt_hist:
        print("[ERROR] 히스토리 데이터를 가져오지 못했습니다. 위 [ERROR]/[DEBUG] 메시지를 확인하세요.")
        sys.exit(1)

    # 채권별 gt_ticker 매핑 (bond_hist 키가 정규화(strip+upper)되어 있으므로 동일하게 맞춰서 조회)
    isin_to_gt = {str(isin).strip().upper(): gt for isin, gt in
                  zip(universe["isin"], universe["gt_ticker"])}
    # 정규화된 키 -> 원본 ISIN 표기 역매핑 (export_hyperscaler_dashboard.py의 history 키 형식과 일치시키기 위함)
    upper_to_original = {str(isin).strip().upper(): isin for isin in universe["isin"]}

    # 날짜별로 재구성: {date_str: {isin: spread_bp}}
    history_by_date = {}
    matched_bonds = 0
    for isin_norm, date_vals in bond_hist.items():
        gt_ticker = isin_to_gt.get(isin_norm)
        if gt_ticker is None:
            continue
        gt_dates = gt_hist.get(gt_ticker.strip().upper(), {})
        if not gt_dates:
            continue
        matched_bonds += 1
        orig_isin = upper_to_original.get(isin_norm, isin_norm)
        for date_str, bond_yield in date_vals.items():
            gt_yield = gt_dates.get(date_str)
            if gt_yield is None:
                continue  # 해당 날짜 GT금리 없으면 스킵 (휴장일 등)
            spread_bp = round((bond_yield - gt_yield) * 100, 1)
            history_by_date.setdefault(date_str, {})[orig_isin] = spread_bp

    print(f"[INFO] 매칭된 채권 수: {matched_bonds}/{len(isins)}")
    print(f"[INFO] 백필된 날짜 수: {len(history_by_date)}")

    if not history_by_date:
        print("[ERROR] 계산된 히스토리가 없습니다. 매칭/데이터 문제를 확인하세요.")
        sys.exit(1)

    # 기존 history.json과 병합 (기존 값 우선 보존, 새 날짜만 추가/업데이트)
    existing = core.load_history()
    existing.update(history_by_date)  # 같은 날짜면 이번 백필 값으로 덮어씀
    core.save_history(existing)

    print(f"[DONE] {core.HISTORY_JSON} 에 {len(history_by_date)}개 날짜 백필 완료. "
          f"총 히스토리 날짜 수: {len(existing)}")


if __name__ == "__main__":
    main()
