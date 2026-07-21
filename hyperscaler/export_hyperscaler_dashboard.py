# -*- coding: utf-8 -*-
"""
export_hyperscaler_dashboard.py
--------------------------------
하이퍼스케일러 크레딧 스프레드 대쉬보드용 데이터 파이프라인.

전제조건:
  - 이 PC에 Bloomberg Terminal이 로그인되어 있어야 함 (xbbg -> blpapi -> 로컬 Bloomberg 세션)
  - pip install xbbg pandas
  - hyperscaler_bond_universe.csv 가 같은 폴더에 있어야 함 (ISIN/버킷/벤치마크 매핑 시드 파일)

실행 흐름:
  1) bond_universe.csv 로드 (46개 채권, 버킷 고정: 3Y/5Y/7Y/10Y/20Y/30Y + EXTRA_15Y)
  2) 각 채권 YTM, 각 버킷 제네릭 UST(GT3~GT30 Govt) 금리 BDP로 pull
  3) 스프레드(bp) = 채권 YTM - 버킷 매칭 GT 금리
  4) hyperscaler_history.json 에 오늘 날짜 스냅샷 누적 저장
  5) 히스토리에서 1D/1W/MTD/YTD 변동(bp) 계산
  6) data.json 생성 (대쉬보드가 fetch 하는 파일) -> git commit/push는 .bat에서 처리

주의:
  - 뉴스 섹션은 이 스크립트가 채우지 않음. "하이퍼스케일러 뉴스" 트리거로 별도 처리.
  - #N/A 필드는 채우지 않고 경고만 출력 (가짜 데이터 절대 생성 안 함).
"""

import json
import os
import sys
import datetime as dt
from collections import defaultdict
from typing import Optional

import pandas as pd

try:
    from xbbg import blp
except ImportError:
    print("[ERROR] xbbg가 설치되어 있지 않습니다. 'pip install xbbg' 실행 후 재시도하세요.")
    sys.exit(1)

# ------------------------------------------------------------------
# 설정
# ------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BOND_UNIVERSE_CSV = os.path.join(BASE_DIR, "hyperscaler_bond_universe.csv")
HISTORY_JSON = os.path.join(BASE_DIR, "hyperscaler_history.json")
OUTPUT_JSON = os.path.join(BASE_DIR, "data.json")

YIELD_FIELD = "YAS_BOND_YLD"      # 채권 YTM 필드 (YAS_MID_YIELD/YLD_YTM_MID 모두 회사채에서 값 없어 변경)
GT_YIELD_FIELD = "YLD_YTM_MID"     # 제네릭 UST 필드
RATING_FIELDS = {"sp": "RTG_SP", "moody": "RTG_MOODY"}   # 발행자 등급 필드 (S&P/Moody's)

BUCKET_ORDER = ["3Y", "5Y", "7Y", "10Y", "20Y", "30Y"]

# 콜/상환 등으로 화면에서 제외할 채권 (name 컬럼 기준, 정확히 일치해야 함)
EXCLUDED_BOND_NAMES = [
    "MU 5.3 01/15/31",
]

TODAY = dt.date.today()
TODAY_STR = TODAY.isoformat()


# ------------------------------------------------------------------
# 1) 유니버스 로드
# ------------------------------------------------------------------
def load_universe() -> pd.DataFrame:
    if not os.path.exists(BOND_UNIVERSE_CSV):
        print(f"[ERROR] {BOND_UNIVERSE_CSV} 파일을 찾을 수 없습니다.")
        sys.exit(1)
    df = pd.read_csv(BOND_UNIVERSE_CSV)
    df["isin"] = df["isin"].str.strip()
    df["gt_ticker"] = df["gt_ticker"].str.strip()

    if EXCLUDED_BOND_NAMES:
        before = len(df)
        df = df[~df["name"].str.strip().isin(EXCLUDED_BOND_NAMES)]
        excluded_count = before - len(df)
        if excluded_count:
            print(f"[INFO] EXCLUDED_BOND_NAMES 설정에 따라 {excluded_count}건 제외됨: {EXCLUDED_BOND_NAMES}")

    return df


# ------------------------------------------------------------------
# 2) Bloomberg pull
# ------------------------------------------------------------------
def _coerce_to_pandas(df):
    """
    xbbg/일부 라이브러리 조합에서 blp.bdp()가 순수 pandas.DataFrame이 아니라
    narwhals 등 호환성 래퍼 객체를 반환하는 경우가 있음 (예: pandas 3.x 환경).
    .to_native() / .to_pandas() 메서드가 있으면 이를 이용해 진짜 pandas DataFrame으로 변환.
    """
    if df is None or isinstance(df, pd.DataFrame):
        return df

    # narwhals DataFrame: .to_native() -> 원래 backend(pandas/polars) 객체
    if hasattr(df, "to_native"):
        try:
            native = df.to_native()
        except Exception as e:
            print(f"[WARN] to_native() 변환 실패: {e}")
            native = None
        if isinstance(native, pd.DataFrame):
            return native
        if native is not None and hasattr(native, "to_pandas"):
            try:
                return native.to_pandas()
            except Exception as e:
                print(f"[WARN] to_native().to_pandas() 변환 실패: {e}")

    # polars DataFrame 등 자체적으로 .to_pandas()를 지원하는 경우
    if hasattr(df, "to_pandas"):
        try:
            return df.to_pandas()
        except Exception as e:
            print(f"[WARN] to_pandas() 변환 실패: {e}")

    # 최후 수단: DataFrame Interchange Protocol
    if hasattr(df, "__dataframe__"):
        try:
            return pd.api.interchange.from_dataframe(df)
        except Exception as e:
            print(f"[WARN] interchange protocol 변환 실패: {e}")

    return df  # 변환 실패 시 원본 반환 (이후 타입 체크에서 에러 메시지로 표시됨)


def _bdp_field_to_dict(df, field: str, tickers: list, label: str, cast=float) -> dict:
    """
    blp.bdp() 결과에서 {ticker: value} dict를 안전하게 추출.
    .loc 를 쓰지 않고 to_dict()만 사용 -> pandas 버전/환경 차이에 덜 민감함.
    문제가 생기면 진단 정보를 출력해서 원인 파악이 쉽도록 함.
    cast: 값 변환 함수. 금리/스프레드는 float(기본값), 등급 같은 문자열 필드는 str을 넘길 것.
    """
    df = _coerce_to_pandas(df)

    if df is None:
        print(f"[ERROR] {label} blp.bdp() 응답이 None 입니다. Bloomberg Terminal 로그인 상태를 확인하세요.")
        return {}

    if not isinstance(df, pd.DataFrame):
        print(f"[ERROR] {label} 응답이 DataFrame이 아닙니다 (실제 타입: {type(df)}). "
              f"xbbg/pandas 버전을 확인하세요. (pandas={pd.__version__})")
        return {}

    if df.empty:
        print(f"[ERROR] {label} 응답이 빈 DataFrame 입니다. 필드명/티커를 확인하세요.")
        return {}

    print(f"[DEBUG] {label} 응답 컬럼: {list(df.columns)} / shape: {df.shape}")
    print(f"[DEBUG] {label} 응답 상위 3행:\n{df.head(3)}")

    cols_lower = {str(c).lower() for c in df.columns}

    # ---- Long(tidy) 포맷: 컬럼이 ticker/field/value 인 경우 ----
    if {"ticker", "field", "value"}.issubset(cols_lower):
        col_map = {str(c).lower(): c for c in df.columns}
        ticker_col = col_map["ticker"]
        field_col = col_map["field"]
        value_col = col_map["value"]

        sub = df[df[field_col].astype(str).str.lower() == field.lower()]
        if sub.empty:
            print(f"[ERROR] {label} 응답에 field='{field}' 행이 없습니다. "
                  f"실제 field 값들: {sorted(df[field_col].astype(str).unique())}")
            return {}

        raw_dict = dict(zip(sub[ticker_col].astype(str), sub[value_col]))
        normalized = {k.strip().upper(): v for k, v in raw_dict.items()}

        result = {}
        missing = []
        for t in tickers:
            key = str(t).strip().upper()
            val = normalized.get(key)
            if val is None or (isinstance(val, float) and pd.isna(val)):
                missing.append(t)
                continue
            try:
                result[t] = cast(val)
            except (TypeError, ValueError):
                print(f"[WARN] {t} 값을 변환할 수 없습니다 (raw={val!r}). 스킵합니다.")

        if missing:
            for t in missing:
                print(f"[WARN] {t} 값이 없거나 #N/A 입니다. 스킵합니다.")
            # 전부(또는 대부분) 실패했으면 실제 응답 티커 형식을 보여줘서 매칭 문제인지 진단
            if len(missing) == len(tickers):
                sample_keys = list(normalized.keys())[:5]
                print(f"[DEBUG] 요청 티커 예시: {tickers[:3]}")
                print(f"[DEBUG] 응답에 실제로 들어있는 티커(정규화 후) 예시: {sample_keys}")
                sample_raw_vals = list(raw_dict.items())[:5]
                print(f"[DEBUG] 응답 원본 (ticker, value) 예시: {sample_raw_vals}")
        return result

    # ---- Wide 포맷: 필드명이 컬럼, 티커가 인덱스인 경우 ----
    # 필드명 컬럼 찾기 (대소문자 다를 수 있어 유연하게 매칭)
    field_lower = field.lower()
    col = None
    for c in df.columns:
        if str(c).lower() == field_lower:
            col = c
            break
    if col is None:
        print(f"[ERROR] {label} 응답에 '{field}' 컬럼이 없습니다. 실제 컬럼: {list(df.columns)}")
        return {}

    series = df[col]
    raw_dict = series.to_dict()  # {index_value: value} ; index가 ticker

    # 인덱스 대소문자/공백 차이를 흡수하기 위해 정규화된 매핑도 함께 생성
    normalized = {str(k).strip().upper(): v for k, v in raw_dict.items()}

    result = {}
    for t in tickers:
        key = str(t).strip().upper()
        val = normalized.get(key)
        if val is None or (isinstance(val, float) and pd.isna(val)):
            print(f"[WARN] {t} 값이 없거나 #N/A 입니다. 스킵합니다.")
            continue
        try:
            result[t] = cast(val)
        except (TypeError, ValueError):
            print(f"[WARN] {t} 값을 변환할 수 없습니다 (raw={val!r}). 스킵합니다.")
    return result


# ------------------------------------------------------------------
# 2) Bloomberg pull
# ------------------------------------------------------------------
def pull_bond_yields(isins: list) -> dict:
    """BDP로 채권별 YTM pull. 실패/N#A는 dict에서 제외하고 경고 출력."""
    print(f"[INFO] 채권 {len(isins)}건 YTM({YIELD_FIELD}) pull 중...")
    df = blp.bdp(tickers=isins, flds=[YIELD_FIELD])
    return _bdp_field_to_dict(df, YIELD_FIELD, isins, "[채권 YTM]")


def pull_gt_yields(gt_tickers: list) -> dict:
    """BDP로 제네릭 UST 금리 pull."""
    gt_tickers = sorted(set(gt_tickers))
    print(f"[INFO] 벤치마크 {len(gt_tickers)}건 금리({GT_YIELD_FIELD}) pull 중...")
    df = blp.bdp(tickers=gt_tickers, flds=[GT_YIELD_FIELD])
    return _bdp_field_to_dict(df, GT_YIELD_FIELD, gt_tickers, "[벤치마크 금리]")


def pull_ratings(universe: pd.DataFrame) -> dict:
    """
    발행자별 등급(S&P/Moody's) pull.
    발행자당 채권 1개만 보면 그 채권에 등급이 없을 때 전체가 '-'로 나오는 문제가 있어,
    발행자의 모든 채권을 조회한 뒤 값이 있는 첫 채권의 등급을 사용.
    """
    isins_by_issuer = {}
    for _, row in universe.iterrows():
        isins_by_issuer.setdefault(row["issuer"], []).append(row["isin"])
    all_isins = universe["isin"].tolist()

    print(f"[INFO] 발행자 {len(isins_by_issuer)}건 등급(S&P/Moody's) pull 중... (채권 {len(all_isins)}건 조회)")
    sp_dict, moody_dict = {}, {}
    try:
        df_sp = blp.bdp(tickers=all_isins, flds=[RATING_FIELDS["sp"]])
        sp_dict = _bdp_field_to_dict(df_sp, RATING_FIELDS["sp"], all_isins, "[등급 S&P]", cast=str)
    except Exception as e:
        print(f"[WARN] S&P 등급 pull 실패: {e}")
    try:
        df_moody = blp.bdp(tickers=all_isins, flds=[RATING_FIELDS["moody"]])
        moody_dict = _bdp_field_to_dict(df_moody, RATING_FIELDS["moody"], all_isins, "[등급 Moody's]", cast=str)
    except Exception as e:
        print(f"[WARN] Moody's 등급 pull 실패: {e}")

    ratings = {}
    for issuer, isins in isins_by_issuer.items():
        sp, moody = "", ""
        for isin in isins:
            if not sp and isin in sp_dict and sp_dict[isin].strip():
                sp = sp_dict[isin].strip()
            if not moody and isin in moody_dict and moody_dict[isin].strip():
                moody = moody_dict[isin].strip()
            if sp and moody:
                break
        ratings[issuer] = f"{sp or '-'} / {moody or '-'}" if (sp or moody) else "-"
    return ratings


# ------------------------------------------------------------------
# 3) 스프레드 계산
# ------------------------------------------------------------------
def compute_spreads(universe: pd.DataFrame, bond_yields: dict, gt_yields: dict) -> dict:
    """{isin: spread_bp} 반환. 데이터 없는 채권은 제외."""
    spreads = {}
    for _, row in universe.iterrows():
        isin = row["isin"]
        gt = row["gt_ticker"]
        if isin not in bond_yields or gt not in gt_yields:
            continue
        spread_bp = round((bond_yields[isin] - gt_yields[gt]) * 100, 1)
        spreads[isin] = spread_bp
    return spreads


# ------------------------------------------------------------------
# 4) 히스토리 누적
# ------------------------------------------------------------------
def load_history() -> dict:
    if os.path.exists(HISTORY_JSON):
        with open(HISTORY_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_history(history: dict):
    with open(HISTORY_JSON, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


# ------------------------------------------------------------------
# 5) 기간별 변동 계산 (1D / 1W / MTD / YTD)
# ------------------------------------------------------------------
def nearest_past_date(history: dict, target: dt.date) -> Optional[str]:
    """target 이하의 가장 가까운 날짜(히스토리에 존재하는) 반환."""
    candidates = [d for d in history.keys() if dt.date.fromisoformat(d) <= target]
    if not candidates:
        return None
    return max(candidates)


def compute_changes(history: dict, isin: str, today_val: float) -> dict:
    d1_target = TODAY - dt.timedelta(days=1)
    w1_target = TODAY - dt.timedelta(days=7)
    mtd_target = TODAY.replace(day=1) - dt.timedelta(days=1)          # 전월 말
    ytd_target = dt.date(TODAY.year - 1, 12, 31)                      # 전년 말

    out = {}
    for label, target in [("d1", d1_target), ("w1", w1_target),
                           ("mtd", mtd_target), ("ytd", ytd_target)]:
        ref_date = nearest_past_date(history, target)
        if ref_date and isin in history.get(ref_date, {}):
            ref_val = history[ref_date][isin]
            out[label] = round(today_val - ref_val, 1)
        else:
            out[label] = None  # 히스토리 부족 (초기 실행 등) -> null, 프론트에서 "-" 처리
    return out


# ------------------------------------------------------------------
# 6) data.json 빌드
# ------------------------------------------------------------------
def build_output(universe: pd.DataFrame, spreads: dict, history: dict, ratings: dict) -> dict:
    # --- 매트릭스 (in_core_matrix == Y 인 채권만) ---
    matrix = defaultdict(dict)
    for _, row in universe.iterrows():
        if row["in_core_matrix"] != "Y":
            continue
        isin = row["isin"]
        if isin in spreads:
            matrix[row["issuer"]][row["bucket"]] = spreads[isin]

    # --- 개별 채권 변동 테이블 (전체 46개 채권 대상) ---
    bond_changes = []
    for _, row in universe.iterrows():
        isin = row["isin"]
        if isin not in spreads:
            continue
        changes = compute_changes(history, isin, spreads[isin])
        bond_changes.append({
            "isin": isin,
            "issuer": row["issuer"],
            "name": row["name"],
            "bucket": row["bucket"],
            "spread": spreads[isin],
            **changes,
        })

    # --- 버킷별 평균 MTD/YTD (core matrix 채권만 대상, 표준 6버킷) ---
    bucket_mtd = defaultdict(list)
    bucket_ytd = defaultdict(list)
    for b in bond_changes:
        if b["bucket"] not in BUCKET_ORDER:
            continue
        if b["mtd"] is not None:
            bucket_mtd[b["bucket"]].append(b["mtd"])
        if b["ytd"] is not None:
            bucket_ytd[b["bucket"]].append(b["ytd"])

    mtd_chart = [round(sum(v) / len(v), 1) if v else None for b in BUCKET_ORDER for v in [bucket_mtd[b]]]
    ytd_chart = [round(sum(v) / len(v), 1) if v else None for b in BUCKET_ORDER for v in [bucket_ytd[b]]]

    # --- 발행자별 버킷 MTD/YTD (드롭다운에서 개별 발행자 선택 시 사용) ---
    bucket_chart_by_issuer = {}
    for issuer in sorted(universe["issuer"].unique()):
        issuer_mtd, issuer_ytd = {}, {}
        for b in bond_changes:
            if b["issuer"] != issuer or b["bucket"] not in BUCKET_ORDER:
                continue
            issuer_mtd[b["bucket"]] = b["mtd"]
            issuer_ytd[b["bucket"]] = b["ytd"]
        bucket_chart_by_issuer[issuer] = {
            "mtd": [issuer_mtd.get(b) for b in BUCKET_ORDER],
            "ytd": [issuer_ytd.get(b) for b in BUCKET_ORDER],
        }

    return {
        "last_update": dt.datetime.now().strftime("%Y-%m-%d %H:%M KST"),
        "buckets": BUCKET_ORDER,
        "matrix": matrix,
        "ratings": ratings,
        "bond_changes": bond_changes,
        "bucket_chart": {"mtd": mtd_chart, "ytd": ytd_chart},
        "bucket_chart_by_issuer": bucket_chart_by_issuer,
    }


# ------------------------------------------------------------------
# main
# ------------------------------------------------------------------
def main():
    print(f"[INFO] pandas version: {pd.__version__}")
    universe = load_universe()

    bond_yields = pull_bond_yields(universe["isin"].tolist())
    gt_yields = pull_gt_yields(universe["gt_ticker"].tolist())
    spreads = compute_spreads(universe, bond_yields, gt_yields)

    if not spreads:
        print("[ERROR] 계산된 스프레드가 없습니다. Bloomberg 연결 상태를 확인하세요.")
        sys.exit(1)

    history = load_history()
    ratings = pull_ratings(universe)
    output = build_output(universe, spreads, history, ratings)

    # 오늘자 스냅샷을 히스토리에 저장 (변동 계산용 -> data.json 저장 전에 기록)
    history[TODAY_STR] = spreads
    save_history(history)

    os.makedirs(os.path.dirname(OUTPUT_JSON), exist_ok=True)
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"[DONE] {OUTPUT_JSON} 생성 완료. 채권 {len(spreads)}/{len(universe)}건 스프레드 계산됨.")


if __name__ == "__main__":
    main()
