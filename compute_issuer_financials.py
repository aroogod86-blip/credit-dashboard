# -*- coding: utf-8 -*-
"""
compute_issuer_financials.py

보유 채권 발행자들의 재무데이터/재무비율을 Bloomberg에서 가져와
credit-dashboard.html에 추가할 "발행자 재무" 탭용 JSON을 생성한다.

산출 지표 (8개, latest 스냅샷 + 최근 6개년 history 둘 다 계산):
  1. Revenue Growth (YoY)
  2. Earnings Growth (YoY, Net Income 기준)
  3. Net Debt / EBITDA
  4. EBITDA / Interest Expense  (+ FFO / Interest Expense)
  5. FFO / Debt
  6. FCF / Debt
  7. (Cash + ST Investments) / ST Debt
  8. EBITDA Margin

출력 JSON 구조:
  {
    "as_of": "YYYY-MM-DD",
    "issuers": {
      "<ticker>": {
        "issuer_name": "...",
        "data_status": "ok" | "no_data",
        "latest": { <8개 지표 + _raw>, "data_status": ... },
        "history": [ {"period": "2021", <8개 지표>}, {"period": "2022", ...}, ... ]
      }
    }
  }

실행 환경: Bloomberg Terminal 로그인 + xbbg 설치된 로컬 PC
    pip install xbbg

사용법:
    python compute_issuer_financials.py --tickers tickers.csv --out issuer_financials.json

tickers.csv 포맷 (헤더 포함, get_issuer_tickers.py v2가 자동 생성):
    ticker,issuer_name
    US02079KBM80 Corp,Alphabet Inc
    ...
    (v2부터는 Equity 티커가 아니라 "채권 ISIN + Corp" 티커를 그대로 사용한다.
     Bloomberg가 회사채에 발행사 펀더멘털 데이터를 연결해서 제공하므로,
     Equity 티커를 따로 찾는 매핑 단계가 필요 없다.)

* 보유 발행자 리스트(249 ISIN 기준 issuer 매핑)가 별도 파일로 있으면
  그 파일을 --tickers 인자로 바로 넣으면 된다. 없으면 아래
  DEFAULT_TICKERS 리스트를 채워서 쓰면 된다.
"""

import argparse
import json
import sys
from datetime import datetime

try:
    from xbbg import blp
except ImportError:
    print("[ERROR] xbbg가 설치되어 있지 않습니다. pip install xbbg 실행 후 다시 실행하세요.")
    sys.exit(1)

import pandas as pd


def to_wide_pandas(raw):
    """
    xbbg 최신 버전은 blp.bdp/bdh 결과를 narwhals DataFrame
    (pyarrow Table 래핑, long format: ticker/field/value 컬럼)으로 반환한다.
    이를 pandas wide format (index=ticker, columns=필드명(소문자))으로 변환한다.
    """
    if hasattr(raw, "to_pandas"):
        raw = raw.to_pandas()
    elif hasattr(raw, "to_native"):
        native = raw.to_native()
        raw = native.to_pandas() if hasattr(native, "to_pandas") else pd.DataFrame(native)

    if not isinstance(raw, pd.DataFrame):
        raw = pd.DataFrame(raw)

    cols_lower = [str(c).lower() for c in raw.columns]
    raw.columns = cols_lower
    if {"ticker", "field", "value"}.issubset(set(cols_lower)):
        raw = raw.drop_duplicates(subset=["ticker", "field"], keep="last")
        wide = raw.pivot(index="ticker", columns="field", values="value")
        wide.columns = [str(c).lower() for c in wide.columns]
        return wide

    if "ticker" in cols_lower:
        raw = raw.set_index("ticker")
    return raw


def to_tidy_history(raw):
    """
    blp.bdh() 결과(narwhals long format 또는 구버전 wide MultiIndex 모두 대응)를
    tidy 포맷: columns=[ticker, period(연도 str), field, value] 로 정규화한다.
    """
    if hasattr(raw, "to_pandas"):
        raw = raw.to_pandas()
    elif hasattr(raw, "to_native"):
        native = raw.to_native()
        raw = native.to_pandas() if hasattr(native, "to_pandas") else pd.DataFrame(native)

    if not isinstance(raw, pd.DataFrame):
        raw = pd.DataFrame(raw)

    # Case 1: long format with a date-like column (narwhals 신버전 예상 형태)
    cols_lower = [str(c).lower() for c in raw.columns]
    raw.columns = cols_lower
    date_col = next((c for c in ["date", "dt", "period", "index"] if c in cols_lower), None)

    if date_col and {"ticker", "field", "value"}.issubset(set(cols_lower)):
        tidy = raw[["ticker", date_col, "field", "value"]].rename(columns={date_col: "period"})
        tidy["period"] = pd.to_datetime(tidy["period"], errors="coerce").dt.year.astype("Int64").astype(str)
        tidy["field"] = tidy["field"].astype(str).str.lower()
        return tidy

    # Case 2: 구버전 xbbg wide 포맷 -> index=date, columns=MultiIndex(ticker, field)
    if isinstance(raw.index, pd.DatetimeIndex) or "date" not in cols_lower:
        try:
            stacked = raw.stack(level=[0, 1]).reset_index()
            stacked.columns = ["period", "ticker", "field", "value"]
            stacked["period"] = pd.to_datetime(stacked["period"], errors="coerce").dt.year.astype("Int64").astype(str)
            stacked["field"] = stacked["field"].str.lower()
            return stacked
        except Exception:
            pass

    raise ValueError(f"blp.bdh() 결과 포맷을 인식할 수 없습니다. columns={list(raw.columns)[:10]}")


def fetch_history(tickers, years=None):
    """최근 N개년 재무 원자료 pull (연도별 추이 계산용). tickers는 Equity 티커여야 함
    (채권/Corp 티커는 Bloomberg BDH 시계열 펀더멘털이 비어오는 경우가 많아서 제외)."""
    if years is None:
        years = HISTORY_YEARS
    today = datetime.now()
    start_date = f"{today.year - years}0101"
    end_date = today.strftime("%Y%m%d")

    # 일반기업 필드(FIELDS_LTM)와 금융기관 필드(FIELDS_FINANCIAL)를 별도 호출로 분리.
    # 한 번에 13개 필드를 같이 요청하면, 은행처럼 EBITDA류 필드가 아예 해당 안 되는 종목에서
    # 응답 전체가 비어버리는 현상이 있어 (한 종목에 대해 부분적으로 안 맞는 필드가 섞이면
    # 그 종목 응답 자체가 통째로 날아가는 것으로 추정), 그룹별로 나눠 호출 후 합친다.
    tidy_parts = []
    for label, flds_subset in [("일반기업", FIELDS_LTM), ("금융기관", FIELDS_FINANCIAL)]:
        try:
            raw_part = blp.bdh(
                tickers=tickers,
                flds=flds_subset,
                start_date=start_date,
                end_date=end_date,
                periodicitySelection="YEARLY",
            )
            tidy_part = to_tidy_history(raw_part)
            n_notna = tidy_part["value"].notna().sum() if len(tidy_part) else 0
            print(f"  [INFO] 히스토리({label} 필드) row {len(tidy_part)}개, 값 있는 row {n_notna}개")
            tidy_parts.append(tidy_part)
        except Exception as e:
            print(f"  [WARN] 히스토리({label} 필드) pull 실패: {e}")

    if not tidy_parts:
        return {}
    tidy = pd.concat(tidy_parts, ignore_index=True)
    tidy["value"] = pd.to_numeric(tidy["value"], errors="coerce")
    tidy["ticker"] = tidy["ticker"].astype(str).str.strip()

    unique_hist_tickers = tidy["ticker"].unique().tolist()
    matched = set(unique_hist_tickers) & set(t.strip() for t in tickers)
    n_notna = tidy["value"].notna().sum()
    print(f"  [INFO] 히스토리 원자료 row 수: {len(tidy)} (값 있는 row {n_notna}개), "
          f"매칭된 발행자: {len(matched)}/{len(tickers)}")

    # ticker -> {period(연도): {field: value}} 딕셔너리로 정리
    history_map = {}
    for ticker, sub in tidy.groupby("ticker"):
        by_period = {}
        for period, psub in sub.groupby("period"):
            by_period[period] = dict(zip(psub["field"], psub["value"]))
        history_map[ticker] = dict(sorted(by_period.items()))  # 연도 오름차순 정렬
    return history_map


def compute_history_series(history_for_ticker):
    """
    연도별 raw 딕셔너리 -> 연도별 8개 지표 리스트로 변환.
    성장률(매출/이익)은 전년 대비 계산이 필요하므로 첫 연도는 growth만 None으로 남는다.
    직전 연도가 빈 스텁(진행 중인 회계연도 등)이면 건너뛰고 그 이전 실측값을 사용한다.
    """
    periods = sorted(history_for_ticker.keys())
    series = []
    for i, period in enumerate(periods):
        row_curr = history_for_ticker[period]
        row_py = {}
        for back in range(i - 1, -1, -1):
            prev = history_for_ticker[periods[back]]
            rev_py = prev.get("sales_rev_turn")
            ni_py = prev.get("net_income")
            if (rev_py is not None and not (isinstance(rev_py, float) and pd.isna(rev_py))) or \
               (ni_py is not None and not (isinstance(ni_py, float) and pd.isna(ni_py))):
                row_py = {"sales_rev_turn_py": rev_py, "net_income_py": ni_py}
                break
        try:
            m = compute_metrics(row_curr, row_py)
        except Exception:
            continue
        m["period"] = period
        series.append(m)
    return series


# ---------------------------------------------------------------------------
# 0. 발행자 리스트 (없으면 --tickers CSV로 대체)
# ---------------------------------------------------------------------------
DEFAULT_TICKERS = [
    # ("Bloomberg Ticker", "표시용 발행자명")
    # ("AAPL US Equity", "Apple Inc"),
    # ("BA US Equity", "Boeing Co"),
]


# ---------------------------------------------------------------------------
# 1. Bloomberg 필드 매핑
#    -- 업종/발행자별로 비어있는 필드가 있을 수 있음. FLDS<GO>로 검증 권장.
# ---------------------------------------------------------------------------
FIELDS_LTM = [
    "SALES_REV_TURN",              # 매출 (LTM)
    "NET_INCOME",                  # 순이익 (LTM)
    "EBITDA",                      # EBITDA (LTM)
    "IS_INT_EXPENSE",              # 이자비용 (LTM)
    "CF_DEP_AMORT",                # 감가상각비 (LTM, cash flow 기준)
    "CF_CASH_FROM_OPER",           # 영업활동현금흐름 CFO (LTM)
    "CF_CAP_EXPEND_PRPTY_ADD",     # Capex (LTM)
    "SHORT_AND_LONG_TERM_DEBT",    # 총차입금
    "BS_CASH_NEAR_CASH_ITEM",      # 현금및현금성자산
    "BS_MKT_SEC_OTHER_ST_INVEST",  # 단기투자자산
    "BS_ST_DEBT",                   # 단기차입금 (유동성부채) -- Bloomberg FLDS로 확인된 정확한 필드명
    "CF_DEFERRED_TAXES",           # 이연법인세 (non-cash 조정, 없는 경우 많음)
]

# --- 금융기관(은행/증권사) 전용 필드 ---
# 주의: 이 필드들은 일반기업 필드보다 은행별 커버리지 편차가 크고,
# 정확한 Bloomberg 필드명도 검증이 덜 된 상태. 실행 후 비어있는 항목이 많으면
# FLDS<GO>로 실제 필드명 확인 후 교체 필요.
FIELDS_FINANCIAL = [
    "RETURN_COM_EQY",       # ROE (자기자본이익률)
    "BS_TIER1_CAP_RATIO",   # Tier 1 자본비율 -- FLDS로 확인 완료
    "NPLS_TO_TOTAL_LOANS",  # NPL비율(총대출 대비 무수익대출) -- FLDS로 확인 완료
    "NET_INT_MARGIN",       # NIM(순이자마진)
    "TOT_LOAN_TO_TOT_DPST", # 예대율(총대출/총예금, 이미 완성된 비율 필드) -- FLDS로 확인 완료
    "BS_TOT_ASSET",         # 총자산 -- 레버리지 계산용
    "TOTAL_EQUITY",         # 총자기자본 -- 레버리지 계산용
]

FIELDS_PRIOR_YEAR = [
    "SALES_REV_TURN",
    "NET_INCOME",
]

PERIOD_OVERRIDE_PRIOR = {"FUNDAMENTAL_TICKER_INFO_LATEST_PERIOD": ""}

# 추이(히스토리) 차트용 -- FIELDS_LTM + FIELDS_FINANCIAL을 연도별로 pull
FIELDS_HISTORY = list(dict.fromkeys(FIELDS_LTM + FIELDS_FINANCIAL))
HISTORY_YEARS = 6  # 최근 6개년 pull (성장률 계산 시 6년치로 5개년의 YoY를 만들 수 있음)


# ---------------------------------------------------------------------------
# 2. 데이터 Pull
# ---------------------------------------------------------------------------
def fetch_report_dates(equity_tickers):
    """최근 실적 발표일(ANNOUNCEMENT_DT)을 Equity 티커로 조회.
    (LTM 관련 날짜 필드는 채권/Corp 티커로는 안 나오고 Equity 티커로만 나옴)
    반환: {equity_ticker: '26년 2분기'} 형태 라벨 dict"""
    if not equity_tickers:
        return {}
    try:
        df = blp.bdp(tickers=equity_tickers, flds=["ANNOUNCEMENT_DT"])
        df = to_wide_pandas(df)
    except Exception as e:
        print(f"  [WARN] 실적 발표일(ANNOUNCEMENT_DT) pull 실패: {e}")
        return {}

    label_map = {}
    for ticker in df.index:
        raw_val = df.loc[ticker].get("announcement_dt") if "announcement_dt" in df.columns else None
        label_map[ticker] = _to_kr_quarter_label(raw_val)
    return label_map


def fetch_ltm(tickers):
    """LTM 기준 재무 원자료 pull (일반기업 필드 + 금융기관 필드 한번에)"""
    all_fields = list(dict.fromkeys(FIELDS_LTM + FIELDS_FINANCIAL))
    df = blp.bdp(tickers=tickers, flds=all_fields)
    df = to_wide_pandas(df)
    for col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def fetch_prior_year(tickers):
    """전기(직전 회계연도) 매출/순이익 pull -> 성장률 계산용"""
    df = blp.bdp(
        tickers=tickers,
        flds=FIELDS_PRIOR_YEAR,
        FUNDAMENTAL_PERIOD_ORDER=-1,   # 1개 기 전
    )
    df = to_wide_pandas(df)
    for col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.rename(
        columns={
            "sales_rev_turn": "sales_rev_turn_py",
            "net_income": "net_income_py",
        }
    )
    return df


# ---------------------------------------------------------------------------
# 3. 지표 계산
# ---------------------------------------------------------------------------
def safe_div(a, b):
    try:
        if a is None or b is None:
            return None
        if pd.isna(a) or pd.isna(b):
            return None
        if b == 0:
            return None
        return a / b
    except (TypeError, ZeroDivisionError):
        return None


def _clean(x):
    """NaN(및 pandas NA류)을 None으로 정규화. 실수 NaN은 'is not None' 체크를 통과해버려서
    하위 로직(FFO, growth 등)이 조용히 오염되는 걸 막기 위한 필수 정규화 단계."""
    try:
        if x is None:
            return None
        if isinstance(x, str):
            return x
        if pd.isna(x):
            return None
    except (TypeError, ValueError):
        pass
    return x


def _to_kr_quarter_label(date_val):
    """LATEST_PERIOD_END_DT_FUND 등에서 온 날짜값 -> '26년 2분기' 형태 라벨로 변환.
    문자열(YYYY-MM-DD/YYYYMMDD)이든 datetime이든 최대한 관대하게 파싱."""
    if date_val is None:
        return None
    try:
        if isinstance(date_val, float) and pd.isna(date_val):
            return None
        dt = pd.to_datetime(date_val, errors="coerce")
        if pd.isna(dt):
            return None
        q = (dt.month - 1) // 3 + 1
        yy = dt.year % 100
        return f"{yy:02d}년 {q}분기"
    except Exception:
        return None


def compute_metrics(row_ltm, row_py):
    revenue = _clean(row_ltm.get("sales_rev_turn"))
    revenue_py = _clean(row_py.get("sales_rev_turn_py"))
    net_income = _clean(row_ltm.get("net_income"))
    net_income_py = _clean(row_py.get("net_income_py"))
    ebitda = _clean(row_ltm.get("ebitda"))
    interest_exp = _clean(row_ltm.get("is_int_expense"))
    dep_amort = _clean(row_ltm.get("cf_dep_amort"))
    cfo = _clean(row_ltm.get("cf_cash_from_oper"))
    capex = _clean(row_ltm.get("cf_cap_expend_prpty_add"))
    total_debt = _clean(row_ltm.get("short_and_long_term_debt"))
    cash = _clean(row_ltm.get("bs_cash_near_cash_item"))
    cash = cash if cash is not None else 0
    st_invest = _clean(row_ltm.get("bs_mkt_sec_other_st_invest"))
    st_invest = st_invest if st_invest is not None else 0
    st_debt = _clean(row_ltm.get("bs_st_debt"))
    deferred_tax = _clean(row_ltm.get("cf_deferred_taxes"))
    deferred_tax = deferred_tax if deferred_tax is not None else 0

    net_debt = None
    if total_debt is not None:
        net_debt = total_debt - cash - st_invest

    # --- FFO 계산 (방법 A: Net Income 기반 정석 방식) ---
    # FFO = NI + D&A + 이연법인세(비현금 조정) [+ 우선주배당 조정, 데이터 미확보시 생략]
    ffo_method_a = None
    if net_income is not None and dep_amort is not None:
        ffo_method_a = net_income + dep_amort + deferred_tax

    # --- FFO 계산 (방법 B: EBITDA 기반 근사 방식) ---
    # FFO ≈ EBITDA - 순이자비용 - 현금기준 법인세
    # 현금기준 법인세 필드 확보 어려운 경우가 많아, 여기서는
    # 근사치로 CFO - D&A조정 없이 EBITDA - Interest 만 사용 (참고용, 검증 필요)
    ffo_method_b = None
    if ebitda is not None and interest_exp is not None:
        ffo_method_b = ebitda - interest_exp

    # 최종 FFO는 방법 A를 우선 사용 (더 표준적), 없으면 방법 B로 대체
    ffo = ffo_method_a if ffo_method_a is not None else ffo_method_b

    fcf = None
    if cfo is not None and capex is not None:
        fcf = cfo - abs(capex)

    # --- 금융기관(은행/증권사) 전용 원자료 ---
    # ROE/Tier1비율/NPL비율/NIM은 Bloomberg가 이미 %값(예: 12.3)으로 주므로
    # 소수(0.123)로 정규화해서 나머지 pct 지표들과 표시 단위를 맞춘다.
    def _pct_to_ratio(x):
        return None if x is None else x / 100.0

    roe = _pct_to_ratio(_clean(row_ltm.get("return_com_eqy")))
    tier1_ratio = _pct_to_ratio(_clean(row_ltm.get("bs_tier1_cap_ratio")))
    npl_ratio = _pct_to_ratio(_clean(row_ltm.get("npls_to_total_loans")))
    nim = _pct_to_ratio(_clean(row_ltm.get("net_int_margin")))
    loan_to_deposit = _pct_to_ratio(_clean(row_ltm.get("tot_loan_to_tot_dpst")))
    total_assets = _clean(row_ltm.get("bs_tot_asset"))
    total_equity = _clean(row_ltm.get("total_equity"))

    def _sane_growth(v):
        """비정상적으로 큰 값(예: 채권 발행주체와 히스토리용 상장기업이 실질적으로 다른 법인이라
        규모 자체가 안 맞는 경우)은 신뢰할 수 없는 값으로 보고 None 처리.
        정상적인 연간 성장률이 500%(5.0)를 넘는 경우는 사실상 없다고 보는 보수적 기준."""
        if v is None:
            return None
        if isinstance(v, float) and pd.isna(v):
            return None
        if abs(v) > 5.0:
            return None
        return v

    metrics = {
        "data_status": None,  # 아래서 채움
        "as_of_period": None,  # main()에서 equity 티커 기반 ANNOUNCEMENT_DT로 채움
        "revenue_growth_yoy": _sane_growth(safe_div(
            (revenue - revenue_py) if (revenue is not None and revenue_py is not None) else None,
            revenue_py,
        )),
        "earnings_growth_yoy": _sane_growth(safe_div(
            (net_income - net_income_py) if (net_income is not None and net_income_py is not None) else None,
            abs(net_income_py) if net_income_py is not None else None,
        )),
        "net_debt_to_ebitda": safe_div(net_debt, ebitda),
        "ebitda_to_interest": safe_div(ebitda, interest_exp),
        "ffo_to_interest": safe_div(
            (ffo + interest_exp) if (ffo is not None and interest_exp is not None) else None,
            interest_exp,
        ),
        "ffo_to_debt": safe_div(ffo, total_debt),
        "fcf_to_debt": safe_div(fcf, total_debt),
        "liquidity_ratio": safe_div((cash + st_invest), st_debt),
        "ebitda_margin": safe_div(ebitda, revenue),
        # --- 금융기관 전용 지표 (일반기업은 대부분 None) ---
        "roe": roe,
        "tier1_ratio": tier1_ratio,
        "npl_ratio": npl_ratio,
        "nim": nim,
        "loan_to_deposit": loan_to_deposit,
        "leverage_ratio": safe_div(total_assets, total_equity),
        # 참고용 raw 값도 같이 저장 (dashboard에서 tooltip 등에 활용 가능)
        "_raw": {
            "revenue_ltm": revenue,
            "net_income_ltm": net_income,
            "ebitda_ltm": ebitda,
            "interest_expense_ltm": interest_exp,
            "total_debt": total_debt,
            "net_debt": net_debt,
            "cash_and_sti": cash + st_invest,
            "st_debt": st_debt,
            "ffo_method_a": ffo_method_a,
            "ffo_method_b": ffo_method_b,
            "ffo_used": ffo,
            "fcf": fcf,
            "total_assets": total_assets,
            "total_equity": total_equity,
        },
    }
    # 핵심 원자료(매출/EBITDA/총차입금 또는 금융기관 핵심값)가 전부 비어있으면 "데이터 없음"
    if revenue is None and ebitda is None and total_debt is None and roe is None and total_assets is None:
        metrics["data_status"] = "no_data"
    else:
        metrics["data_status"] = "ok"
    return metrics


# ---------------------------------------------------------------------------
# 4. 메인 실행부
# ---------------------------------------------------------------------------
FINANCIAL_SECTOR_KEYWORDS = ["FINANCIAL", "BANK", "INSURANCE", "BROKERAGE", "DIVERSIFIED FINAN"]


def is_financial_sector(sector):
    s = str(sector or "").upper()
    return any(kw in s for kw in FINANCIAL_SECTOR_KEYWORDS)


# 한국 공사채(공기업/국책은행 등 준정부기관) 분류용 키워드.
# Bloomberg ISSUER 필드에 찍히는 영문 발행자명 기준. 새로 추가되는 공사/공단은
# 여기 리스트에 없으면 자동으로는 못 잡으니 필요시 키워드 추가 필요.
KR_PUBLIC_CORP_KEYWORDS = [
    "KOREA ELECTRIC POWER", "KOREA GAS", "KOREA WATER RESOURCES",
    "KOREA EXPRESSWAY", "KOREA NATIONAL OIL", "KOREA HYDRO", "KOREA SOUTHERN POWER",
    "KOREA MIDLAND POWER", "KOREA EAST-WEST POWER", "KOREA DEVELOPMENT BANK",
    "EXPORT-IMPORT BANK KOREA", "KOREA HOUSING FINANCE", "KOREA LAND & HOUSING",
    "INCHEON INTL AIRPORT", "KOREA MINE REHAB", "KOREAREHABNRESOURCE",
    "KOREA RAILROAD", "KOREA DISTRICT HEATING", "KOREA COAL",
]


def is_kr_public_corp(issuer_name):
    s = str(issuer_name or "").upper()
    return any(kw in s for kw in KR_PUBLIC_CORP_KEYWORDS)


def load_tickers_from_csv(path):
    import csv

    tickers, names, sectors, equity_map = [], {}, {}, {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            t = row["ticker"].strip()
            tickers.append(t)
            names[t] = row.get("issuer_name", t).strip()
            sectors[t] = row.get("industry_sector", "").strip()
            eqy = (row.get("equity_ticker") or "").strip()
            equity_map[t] = eqy if eqy else None
    return tickers, names, sectors, equity_map


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", type=str, default=None, help="발행자 티커 CSV 경로")
    parser.add_argument("--out", type=str, default="issuer_financials.json")
    args = parser.parse_args()

    if args.tickers:
        tickers, name_map, sector_map, equity_map = load_tickers_from_csv(args.tickers)
    else:
        tickers = [t for t, _ in DEFAULT_TICKERS]
        name_map = dict(DEFAULT_TICKERS)
        sector_map = {}
        equity_map = {}

    if not tickers:
        print("[ERROR] 발행자 티커가 비어있습니다. --tickers CSV를 지정하거나 DEFAULT_TICKERS를 채우세요.")
        sys.exit(1)

    print(f"[INFO] {len(tickers)}개 발행자 Bloomberg LTM 데이터 pull 시작...")

    ltm_df = fetch_ltm(tickers)

    # 히스토리(추이)는 채권(Corp) 티커로는 BDH 시계열이 비어서, 매핑된 주식(Equity) 티커로 pull한다.
    # equity_map: bond_ticker -> equity_ticker (없으면 None -> 해당 발행자는 history 없이 latest만 저장)
    equity_tickers = sorted(set(v for v in equity_map.values() if v))
    print(f"[INFO] {len(equity_tickers)}개 발행자(주식 티커 매핑 성공분) 최근 {HISTORY_YEARS}개년 히스토리 pull 시작...")
    try:
        history_map_by_equity = fetch_history(equity_tickers) if equity_tickers else {}
    except Exception as e:
        print(f"  [WARN] 히스토리 pull 실패 ({e}) -> history 없이 latest만 저장합니다.")
        history_map_by_equity = {}

    print(f"[INFO] {len(equity_tickers)}개 발행자(주식 티커 매핑 성공분) 최근 실적 발표일 조회 중...")
    report_date_map = fetch_report_dates(equity_tickers)

    result = {
        "as_of": datetime.now().strftime("%Y-%m-%d"),
        "issuers": {},
    }

    for ticker in tickers:
        try:
            row_ltm = ltm_df.loc[ticker].to_dict() if ticker in ltm_df.index else {}
        except KeyError:
            print(f"  [WARN] {ticker}: 데이터 없음, 스킵")
            continue

        # 이 발행자(채권 티커)에 매핑된 주식 티커로 히스토리 조회
        eqy_ticker = equity_map.get(ticker)
        hist_for_ticker = history_map_by_equity.get(eqy_ticker, {}) if eqy_ticker else {}

        # 전기(작년) 매출/순이익: 히스토리에서 "실제 값이 채워진" 가장 최근 완결 연도를 사용.
        # - 올해(진행 중인 회계연도)는 아예 후보에서 제외한다. Bloomberg가 이 연도를 빈 스텁으로
        #   주는 경우도 있지만, 어중간하게 LTM과 거의 같은 시점 값을 줘서 "자기 자신과 비교" ->
        #   성장률이 부자연스럽게 0%로 나오는 경우도 있었기 때문.
        # - 그래도 값이 없으면(예외적으로) 올해까지 포함해서 탐색한다.
        current_year_str = str(datetime.now().year)

        def _find_last_valid(hist, field, exclude_current_year=True):
            periods = sorted(hist.keys(), reverse=True)
            if exclude_current_year:
                periods = [p for p in periods if p != current_year_str]
            for p in periods:
                v = hist[p].get(field)
                if v is not None and not (isinstance(v, float) and pd.isna(v)):
                    return v
            if exclude_current_year:
                return _find_last_valid(hist, field, exclude_current_year=False)
            return None

        row_py = {
            "sales_rev_turn_py": _find_last_valid(hist_for_ticker, "sales_rev_turn"),
            "net_income_py": _find_last_valid(hist_for_ticker, "net_income"),
        }

        try:
            latest_metrics = compute_metrics(row_ltm, row_py)
        except Exception as e:
            print(f"  [WARN] {ticker}: 지표 계산 중 오류 ({e}), 스킵")
            continue

        if eqy_ticker and eqy_ticker in report_date_map:
            latest_metrics["as_of_period"] = report_date_map[eqy_ticker]

        history_series = []
        if hist_for_ticker:
            try:
                history_series = compute_history_series(hist_for_ticker)
            except Exception as e:
                print(f"  [WARN] {ticker}: 히스토리 계산 중 오류 ({e})")

        # latest의 성장률(매출/이익)은 BDP의 "LTM" 필드가 실제로는 가장 최근 결산연도와
        # 동일한 값을 주는 경우가 많아, history의 마지막 연도와 비교하면 자기 자신과
        # 비교하는 꼴이 되어 항상 0%가 나오는 문제가 있었다. history 내부에서
        # (최근 완결연도 vs 그 전년도)로 이미 올바르게 계산된 값을 그대로 가져와 덮어쓴다.
        if history_series:
            last_hist = history_series[-1]
            if last_hist.get("revenue_growth_yoy") is not None:
                latest_metrics["revenue_growth_yoy"] = last_hist["revenue_growth_yoy"]
            if last_hist.get("earnings_growth_yoy") is not None:
                latest_metrics["earnings_growth_yoy"] = last_hist["earnings_growth_yoy"]

        issuer_name = name_map.get(ticker, ticker)
        sector = sector_map.get(ticker, "")
        result["issuers"][ticker] = {
            "issuer_name": issuer_name,
            "industry_sector": sector,
            "is_financial": is_financial_sector(sector),
            "is_kr_public_corp": is_kr_public_corp(issuer_name),
            "data_status": latest_metrics["data_status"],
            "latest": latest_metrics,
            "history": history_series,
        }
        status_mark = "OK" if latest_metrics["data_status"] == "ok" else "NO DATA"
        hist_mark = f", history {len(history_series)}개년"
        print(f"  [{status_mark}] {issuer_name} ({ticker}){hist_mark}")

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"[DONE] {args.out} 저장 완료 ({len(result['issuers'])}개 발행자)")


if __name__ == "__main__":
    main()
