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
  1) bond_universe.csv 로드 (51개 채권, 버킷 고정: 3Y/5Y/7Y/10Y/20Y/30Y + EXTRA_15Y)
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
ISPREAD_FIELD = "YAS_ISPREAD"      # 채권별 I-Spread (스왑커브 보간 기준, bp 단위로 직접 제공됨)
RATING_FIELDS = {"sp": "RTG_SP", "moody": "RTG_MOODY"}   # 발행자 등급 필드 (S&P/Moody's)

BUCKET_ORDER = ["3Y", "5Y", "7Y", "10Y", "20Y", "30Y"]

# --- CDS 5Y 관련 설정 ---
CDS_TICKERS = {
    "AAPL":   "CY179834 Corp",
    "NVDA":   "CZ038477 Corp",
    "META":   "CZ038494 Corp",
    "GOOGL":  "CZ038511 Corp",
    "AVGO":   "CY907035 Corp",
    "AMZN":   "CY372412 Corp",
    "SPCX":   "CZ061563 Corp",
    "ORCLCP": "CX356615 Corp",
}
CDS_HISTORY_JSON = os.path.join(BASE_DIR, "cds-history.json")   # data.json과 같은 폴더 (dashboard가 상대경로로 fetch)
CDS_INCREMENTAL_LOOKBACK_DAYS = 15   # 매일 실행 시 최근 며칠치만 다시 받아서 병합 (BDH 호출량 절약)
CDS_FULL_LOOKBACK_DAYS = 365         # --full 옵션일 때 전체 히스토리 기간
CDS_FIELD = "CDS_5Y"   # NOTE: 터미널 차트(Source CMAN) 값과 근사치 확인됨. 완전 일치 아님 - 더 정확한 필드 찾으면 여기만 수정

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


def pull_bond_ispreads(isins: list) -> dict:
    """
    BDP로 채권별 I-Spread(YAS_ISPREAD) pull.
    G-spread(YIELD_FIELD - GT_YIELD_FIELD 로 직접 계산)와 달리, I-Spread는
    Bloomberg가 스왑커브 보간까지 반영해서 bp 단위로 바로 내려주는 필드라 별도 계산이 필요없음.
    화면 표시는 정수(bp)로 반올림.
    """
    print(f"[INFO] 채권 {len(isins)}건 I-Spread({ISPREAD_FIELD}) pull 중...")
    df = blp.bdp(tickers=isins, flds=[ISPREAD_FIELD])
    raw = _bdp_field_to_dict(df, ISPREAD_FIELD, isins, "[채권 I-Spread]")
    return {isin: round(v) for isin, v in raw.items()}


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
# 5-2) CDS 5Y 히스토리 (BDH)
# ------------------------------------------------------------------
def load_cds_history() -> dict:
    if os.path.exists(CDS_HISTORY_JSON):
        with open(CDS_HISTORY_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"date_range": [None, None], "series": {name: [] for name in CDS_TICKERS}}


def _fetch_cds_series(ticker: str, start: str, end: str) -> list:
    """
    단일 종목 CDS 5Y 시계열(CDS_FIELD 기준)을 [{"d":..,"v":..}] 형태로 반환.
    이 환경의 xbbg는 BDH도 BDP처럼 tidy(long) 포맷(ticker/field/date/value 컬럼)으로
    반환하는 경우가 있어, DatetimeIndex 포맷과 tidy 포맷 양쪽 다 처리한다.
    """
    try:
        df = blp.bdh(ticker, CDS_FIELD, start, end)
    except Exception as e:
        print(f"[WARN] CDS {ticker} BDH 조회 실패: {e}")
        return []

    df = _coerce_to_pandas(df)
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        print(f"[WARN] CDS {ticker}: 데이터 없음 (응답 타입: {type(df)})")
        return []

    print(f"[DEBUG] CDS {ticker} 응답 컬럼: {list(df.columns)} / index 타입: {type(df.index).__name__} / shape: {df.shape}")

    # ---- 케이스 1: 정상 DatetimeIndex (wide 포맷, 컬럼이 필드명) ----
    if isinstance(df.index, pd.DatetimeIndex):
        series = df.iloc[:, 0]
        return [
            {"d": idx.strftime("%Y-%m-%d"), "v": round(float(val), 2)}
            for idx, val in series.items() if pd.notna(val)
        ]

    # ---- 케이스 2: tidy/long 포맷 (date/ticker/field/value 컬럼) ----
    cols_lower = {str(c).lower(): c for c in df.columns}
    date_col = next((cols_lower[c] for c in ("date", "index", "dates") if c in cols_lower), None)
    if date_col is None:
        # datetime 타입인 컬럼을 자동 탐색
        for c in df.columns:
            if pd.api.types.is_datetime64_any_dtype(df[c]):
                date_col = c
                break

    if date_col is None:
        print(f"[ERROR] CDS {ticker}: 날짜 컬럼을 찾을 수 없습니다. 컬럼: {list(df.columns)}")
        print(f"[DEBUG] CDS {ticker} 응답 상위 3행:\n{df.head(3)}")
        return []

    # field 컬럼이 있으면 CDS_FIELD 행만 필터 (여러 필드가 섞여 반환될 가능성 대비)
    if "field" in cols_lower:
        field_col = cols_lower["field"]
        df = df[df[field_col].astype(str).str.upper() == CDS_FIELD.upper()]
        if df.empty:
            print(f"[ERROR] CDS {ticker}: field={CDS_FIELD} 행이 없습니다.")
            return []

    value_col = cols_lower.get("value")
    if value_col is None:
        # value 컬럼명이 없으면 날짜/문자열(ticker/field) 컬럼을 제외한 첫 숫자형 컬럼 사용
        candidates = [c for c in df.columns if c != date_col]
        value_col = next((c for c in candidates if pd.api.types.is_numeric_dtype(df[c])), None)

    if value_col is None:
        print(f"[ERROR] CDS {ticker}: 값(value) 컬럼을 찾을 수 없습니다. 컬럼: {list(df.columns)}")
        return []

    dates = pd.to_datetime(df[date_col])
    out = []
    for d, v in zip(dates, df[value_col]):
        if pd.isna(v):
            continue
        out.append({"d": d.strftime("%Y-%m-%d"), "v": round(float(v), 2)})
    return out


def _merge_cds_points(existing: list, new: list) -> list:
    """날짜 기준 dedup 후 병합 (새 값이 우선)."""
    merged = {p["d"]: p["v"] for p in existing}
    merged.update({p["d"]: p["v"] for p in new})
    return [{"d": d, "v": v} for d, v in sorted(merged.items())]


def update_cds_history(full: bool = False) -> dict:
    """CDS 5Y 히스토리를 pull해서 cds-history.json에 병합 저장하고 결과를 반환."""
    end = TODAY.isoformat().replace("-", "")
    lookback = CDS_FULL_LOOKBACK_DAYS if full else CDS_INCREMENTAL_LOOKBACK_DAYS
    start = (TODAY - dt.timedelta(days=lookback)).strftime("%Y%m%d")
    mode = "FULL" if full else "INCREMENTAL"
    print(f"[INFO][CDS-{mode}] {start} ~ {end} 구간 조회")

    data = load_cds_history()
    for name, ticker in CDS_TICKERS.items():
        print(f"  - {name} ({ticker}) 조회 중...")
        new_points = _fetch_cds_series(ticker, start, end)
        if full:
            data["series"][name] = new_points
        else:
            data["series"][name] = _merge_cds_points(data["series"].get(name, []), new_points)

    all_dates = [p["d"] for s in data["series"].values() for p in s]
    if all_dates:
        data["date_range"] = [min(all_dates), max(all_dates)]

    os.makedirs(os.path.dirname(CDS_HISTORY_JSON), exist_ok=True)
    with open(CDS_HISTORY_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    for name, s in data["series"].items():
        print(f"  [CDS] {name}: {len(s)}개 포인트")
    print(f"[INFO] {CDS_HISTORY_JSON} 저장 완료.")
    return data


# ------------------------------------------------------------------
# 6) data.json 빌드
# ------------------------------------------------------------------
def build_output(universe: pd.DataFrame, spreads: dict, history: dict, ratings: dict,
                  ispreads: Optional[dict] = None) -> dict:
    ispreads = ispreads or {}

    # --- 매트릭스 (in_core_matrix == Y 인 채권만) ---
    matrix = defaultdict(dict)
    ispread_matrix = defaultdict(dict)
    for _, row in universe.iterrows():
        if row["in_core_matrix"] != "Y":
            continue
        isin = row["isin"]
        if isin in spreads:
            matrix[row["issuer"]][row["bucket"]] = spreads[isin]
        if isin in ispreads:
            ispread_matrix[row["issuer"]][row["bucket"]] = ispreads[isin]

    # --- 개별 채권 변동 테이블 (전체 51개 채권 대상) ---
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
            "ispread": ispreads.get(isin),   # 값 없으면 null -> 프론트에서 "-" 처리
            **changes,
        })

    # --- 버킷별 평균 1D/1W/MTD/YTD (core matrix 채권만 대상, 표준 6버킷) ---
    bucket_d1 = defaultdict(list)
    bucket_w1 = defaultdict(list)
    bucket_mtd = defaultdict(list)
    bucket_ytd = defaultdict(list)
    for b in bond_changes:
        if b["bucket"] not in BUCKET_ORDER:
            continue
        if b["d1"] is not None:
            bucket_d1[b["bucket"]].append(b["d1"])
        if b["w1"] is not None:
            bucket_w1[b["bucket"]].append(b["w1"])
        if b["mtd"] is not None:
            bucket_mtd[b["bucket"]].append(b["mtd"])
        if b["ytd"] is not None:
            bucket_ytd[b["bucket"]].append(b["ytd"])

    d1_chart = [round(sum(v) / len(v), 1) if v else None for b in BUCKET_ORDER for v in [bucket_d1[b]]]
    w1_chart = [round(sum(v) / len(v), 1) if v else None for b in BUCKET_ORDER for v in [bucket_w1[b]]]
    mtd_chart = [round(sum(v) / len(v), 1) if v else None for b in BUCKET_ORDER for v in [bucket_mtd[b]]]
    ytd_chart = [round(sum(v) / len(v), 1) if v else None for b in BUCKET_ORDER for v in [bucket_ytd[b]]]

    # --- 발행자별 버킷 1D/1W/MTD/YTD (드롭다운에서 개별 발행자 선택 시 사용) ---
    bucket_chart_by_issuer = {}
    for issuer in sorted(universe["issuer"].unique()):
        issuer_d1, issuer_w1, issuer_mtd, issuer_ytd = {}, {}, {}, {}
        for b in bond_changes:
            if b["issuer"] != issuer or b["bucket"] not in BUCKET_ORDER:
                continue
            issuer_d1[b["bucket"]] = b["d1"]
            issuer_w1[b["bucket"]] = b["w1"]
            issuer_mtd[b["bucket"]] = b["mtd"]
            issuer_ytd[b["bucket"]] = b["ytd"]
        bucket_chart_by_issuer[issuer] = {
            "d1": [issuer_d1.get(b) for b in BUCKET_ORDER],
            "w1": [issuer_w1.get(b) for b in BUCKET_ORDER],
            "mtd": [issuer_mtd.get(b) for b in BUCKET_ORDER],
            "ytd": [issuer_ytd.get(b) for b in BUCKET_ORDER],
        }

    return {
        "last_update": dt.datetime.now().strftime("%Y-%m-%d %H:%M KST"),
        "buckets": BUCKET_ORDER,
        "matrix": matrix,
        "ispread_matrix": ispread_matrix,
        "ratings": ratings,
        "bond_changes": bond_changes,
        "bucket_chart": {"d1": d1_chart, "w1": w1_chart, "mtd": mtd_chart, "ytd": ytd_chart},
        "bucket_chart_by_issuer": bucket_chart_by_issuer,
    }


# ------------------------------------------------------------------
# main
# ------------------------------------------------------------------
def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true",
                         help="CDS 5Y 히스토리를 1년치 전체로 재수집 (최초 1회만 사용, 평소엔 옵션 없이 실행)")
    args = parser.parse_args()

    print(f"[INFO] pandas version: {pd.__version__}")
    universe = load_universe()

    bond_yields = pull_bond_yields(universe["isin"].tolist())
    gt_yields = pull_gt_yields(universe["gt_ticker"].tolist())
    spreads = compute_spreads(universe, bond_yields, gt_yields)

    if not spreads:
        print("[ERROR] 계산된 스프레드가 없습니다. Bloomberg 연결 상태를 확인하세요.")
        sys.exit(1)

    ispreads = pull_bond_ispreads(universe["isin"].tolist())
    if not ispreads:
        print("[WARN] I-Spread 값을 하나도 받지 못했습니다. YAS_ISPREAD 필드 접근 권한/티커를 확인하세요. "
              "(G-spread는 정상 진행됩니다)")

    history = load_history()
    ratings = pull_ratings(universe)
    output = build_output(universe, spreads, history, ratings, ispreads=ispreads)

    # CDS 5Y 히스토리는 별도 파일(cds-history.json)로 관리 (증분 업데이트, --full 시 전체 재수집)
    update_cds_history(full=args.full)

    # 오늘자 스냅샷을 히스토리에 저장 (변동 계산용 -> data.json 저장 전에 기록)
    history[TODAY_STR] = spreads
    save_history(history)

    os.makedirs(os.path.dirname(OUTPUT_JSON), exist_ok=True)
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"[DONE] {OUTPUT_JSON} 생성 완료. 채권 {len(spreads)}/{len(universe)}건 스프레드 계산됨.")


if __name__ == "__main__":
    main()
