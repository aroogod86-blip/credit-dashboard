# -*- coding: utf-8 -*-
"""
backfill_spcx_history.py
--------------------------------
새로 추가된 SPCX 채권 5종의 과거 스프레드를 Bloomberg BDH로 소급 조회해서
hyperscaler_history.json에 채워 넣는 1회성 스크립트.

전제조건:
  - export_hyperscaler_dashboard.py 와 같은 폴더에 두고 실행할 것
    (BASE_DIR, HISTORY_JSON, _coerce_to_pandas 등을 그대로 재사용)
  - Bloomberg Terminal 로그인 상태

실행:
  python backfill_spcx_history.py

주의:
  - YTD(연초 대비)는 2025-12-31 시점에 SPCX 채권이 아예 존재하지 않았으므로
    구조적으로 채울 수 없음. 이 스크립트는 1D/1W/MTD 계산에 필요한
    2026-06-26(결제일) ~ 어제까지의 일별 스프레드만 소급 저장한다.
  - 이미 history.json에 있는 다른 발행자/날짜 데이터는 건드리지 않고,
    해당 날짜의 dict에 SPCX ISIN 키만 추가(merge)한다.
"""

import datetime as dt
import sys

import pandas as pd

try:
    from xbbg import blp
except ImportError:
    print("[ERROR] xbbg가 설치되어 있지 않습니다. 'pip install xbbg' 실행 후 재시도하세요.")
    sys.exit(1)

# export_hyperscaler_dashboard.py 재사용 (같은 폴더에 있어야 함)
from export_hyperscaler_dashboard import (
    BASE_DIR, HISTORY_JSON, BOND_UNIVERSE_CSV,
    YIELD_FIELD, GT_YIELD_FIELD,
    _coerce_to_pandas, load_history, save_history,
)

TARGET_ISSUER = "SPCX"
SETTLE_DATE = dt.date(2026, 6, 26)          # SPCX 채권 결제일 (이 이전 데이터는 존재 자체가 불가능)
END_DATE = dt.date.today() - dt.timedelta(days=1)   # 어제까지 (오늘자는 export 스크립트가 이미 채움)


def load_spcx_rows() -> pd.DataFrame:
    df = pd.read_csv(BOND_UNIVERSE_CSV)
    df["isin"] = df["isin"].str.strip()
    df["gt_ticker"] = df["gt_ticker"].str.strip()
    spcx = df[df["issuer"] == TARGET_ISSUER]
    if spcx.empty:
        print(f"[ERROR] {BOND_UNIVERSE_CSV} 에 issuer='{TARGET_ISSUER}' 행이 없습니다.")
        sys.exit(1)
    return spcx


def bdh_long(tickers: list, field: str, label: str) -> dict:
    """
    blp.bdh 결과를 {ticker: {date_str: value}} 형태로 정규화.
    xbbg 버전에 따라 MultiIndex 컬럼(long/wide) 포맷이 다를 수 있어 방어적으로 처리.
    """
    print(f"[INFO] {label} BDH pull 중... ({SETTLE_DATE} ~ {END_DATE}, {len(tickers)}건)")
    df = blp.bdh(tickers=tickers, flds=[field],
                 start_date=SETTLE_DATE.isoformat(), end_date=END_DATE.isoformat())
    df = _coerce_to_pandas(df)

    if df is None or (hasattr(df, "empty") and df.empty):
        print(f"[ERROR] {label} BDH 응답이 비어 있습니다. 티커/필드/기간을 확인하세요.")
        return {}

    result = {t: {} for t in tickers}

    # xbbg 기본 wide 포맷: MultiIndex 컬럼 (ticker, field), 인덱스가 날짜
    if isinstance(df.columns, pd.MultiIndex):
        for t in tickers:
            if (t, field) not in df.columns:
                print(f"[WARN] {label}: {t} 컬럼이 응답에 없습니다. 스킵합니다.")
                continue
            series = df[(t, field)].dropna()
            for idx, val in series.items():
                date_str = pd.Timestamp(idx).date().isoformat()
                result[t][date_str] = float(val)
        return result

    # long(tidy) 포맷: ticker/field/date/value 컬럼인 경우
    cols_lower = {str(c).lower() for c in df.columns}
    if {"ticker", "field", "value"}.issubset(cols_lower) and "date" in cols_lower:
        col_map = {str(c).lower(): c for c in df.columns}
        sub = df[df[col_map["field"]].astype(str).str.lower() == field.lower()]
        for _, row in sub.iterrows():
            t = str(row[col_map["ticker"]]).strip()
            if t not in result:
                continue
            date_str = pd.Timestamp(row[col_map["date"]]).date().isoformat()
            result[t][date_str] = float(row[col_map["value"]])
        return result

    print(f"[ERROR] {label}: 알 수 없는 BDH 응답 포맷입니다. 컬럼: {list(df.columns)}")
    return {}


def main():
    spcx = load_spcx_rows()
    isins = spcx["isin"].tolist()
    gt_tickers = sorted(set(spcx["gt_ticker"].tolist()))
    isin_to_gt = dict(zip(spcx["isin"], spcx["gt_ticker"]))

    bond_hist = bdh_long(isins, YIELD_FIELD, "[SPCX 채권 YTM]")
    gt_hist = bdh_long(gt_tickers, GT_YIELD_FIELD, "[SPCX 벤치마크 GT금리]")

    history = load_history()
    added_dates = 0
    added_points = 0

    for isin in isins:
        gt = isin_to_gt[isin]
        bond_series = bond_hist.get(isin, {})
        gt_series = gt_hist.get(gt, {})
        if not bond_series:
            print(f"[WARN] {isin}: BDH 채권 YTM 값이 하나도 없습니다. 스킵합니다.")
            continue
        for date_str, bond_yld in bond_series.items():
            gt_yld = gt_series.get(date_str)
            if gt_yld is None:
                print(f"[WARN] {isin} {date_str}: 매칭되는 {gt} 금리가 없어 스킵합니다.")
                continue
            spread_bp = round((bond_yld - gt_yld) * 100, 1)
            if date_str not in history:
                history[date_str] = {}
                added_dates += 1
            history[date_str][isin] = spread_bp
            added_points += 1

    save_history(history)
    print(f"[DONE] {HISTORY_JSON} 에 SPCX 소급 데이터 {added_points}건 저장 완료 "
          f"(신규 날짜 {added_dates}건 포함).")
    print("[NOTE] YTD는 2025-12-31 기준값이 존재하지 않아 이번 백필로도 채워지지 않습니다. "
          "2027년부터 자동으로 계산됩니다.")


if __name__ == "__main__":
    main()
