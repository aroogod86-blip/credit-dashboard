# -*- coding: utf-8 -*-
"""
보유 유럽 금융채 스프레드 변화 점검 (프랑스 vs 기타 유럽 vs 미국 은행 비교)
- 잔고.xlsx에서 보유 ISIN을 읽고, Bloomberg(xbbg)로 국가/섹터/스프레드 히스토리 조회
- 1D / 1W / 1M / 기준일(SINCE) 대비 변화(bp) 산출, 종목별 + 발행자별(수량가중) 요약
- 결과: 콘솔 출력 + eu_fin_spread_change.csv / eu_fin_spread_issuer.csv
"""
from datetime import date, timedelta
import pandas as pd
from xbbg import blp

import os


def find_balance_file():
    """스크립트 폴더 + 바탕화면 후보에서 '잔고*.xls*' 중 최신 파일 탐색"""
    import glob
    home = os.path.expanduser("~")
    dash = r"C:\Users\Hana_FI\Desktop\credit-dashboard"
    dirs = [dash, os.path.dirname(os.path.abspath(__file__)),
            os.path.join(home, "Desktop"), os.path.join(home, "바탕 화면"),
            os.path.join(home, "OneDrive", "Desktop"), os.path.join(home, "OneDrive", "바탕 화면")]
    hits = []
    for d in dirs:
        hits += [f for f in glob.glob(os.path.join(d, "**", "*잔고*.xls*"), recursive=True)
                 if not os.path.basename(f).startswith("~$")]   # 엑셀 임시파일 제외
    if hits:
        return max(hits, key=os.path.getmtime)
    # 못 찾으면 대시보드 폴더의 엑셀 파일 목록을 보여주고 종료
    d0 = dirs[0]
    print(f"'잔고' 이름이 들어간 엑셀 파일을 못 찾음. {d0} 의 엑셀 파일:")
    for f in glob.glob(os.path.join(d0, "**", "*.xls*"), recursive=True):
        print("  -", os.path.relpath(f, d0))
    raise FileNotFoundError("잔고 파일 없음 — 파일명/위치 확인 또는 BALANCE_FILE 직접 지정")


# ===== 설정 (대시보드와 맞춰 수정) =====
BALANCE_FILE = None                  # 직접 지정 시 전체 경로 입력 (None이면 자동 탐색)
DESKTOP = os.path.dirname(os.path.abspath(__file__))   # 결과 CSV 저장 위치 = 스크립트 폴더
COL_ISIN, COL_FUND, COL_QTY = "ISIN", "펀드코드", "수량"
FUNDS = ["28096", "34507"]           # 대상 펀드
SPREAD_FIELD = "BLP_I_SPRD_MID"      # 대시보드에서 쓰는 G/I-spread 히스토리 필드로 교체 가능
SINCE = date(2026, 9, 1)             # OAT 스프레드 급확대 이전 기준일
INCLUDE_US_BANKS = True              # 미국 은행을 비교군으로 포함

EUROPE = {"FR", "GB", "CH", "DE", "ES", "NL", "IT", "BE", "AT", "IE",
          "SE", "NO", "DK", "FI", "PT", "LU"}


def to_pd(df):
    """pandas 3.0 / narwhals 반환형 대응"""
    return df.to_pandas() if hasattr(df, "to_pandas") else pd.DataFrame(df)


def group_of(cntry):
    if cntry == "FR":
        return "1.프랑스"
    if cntry in EUROPE:
        return "2.기타유럽"
    return "3.미국(비교)"


def main():
    path = BALANCE_FILE or find_balance_file()
    print(f"잔고 파일: {path}")
    bal = pd.read_excel(path, dtype={COL_FUND: str})
    bal = bal[bal[COL_FUND].isin(FUNDS)]
    qty = bal.groupby(COL_ISIN)[COL_QTY].sum()
    tickers = [f"{i} Corp" for i in qty.index]

    # 1) 정적 정보
    ref = to_pd(blp.bdp(tickers, ["SHORT_NAME", "TICKER", "CNTRY_OF_RISK",
                                  "INDUSTRY_SECTOR", "MATURITY", "PAYMENT_RANK"]))
    ref.columns = [c.lower() for c in ref.columns]
    ok_cntry = EUROPE | ({"US"} if INCLUDE_US_BANKS else set())
    ref = ref[(ref["industry_sector"] == "Financial") & ref["cntry_of_risk"].isin(ok_cntry)]
    if ref.empty:
        print("대상 종목 없음 — 필터/컬럼명 확인")
        return

    # 2) 스프레드 히스토리
    end = date.today()
    start = min(SINCE, end - timedelta(days=40))
    hist = to_pd(blp.bdh(list(ref.index), SPREAD_FIELD, start, end))
    if isinstance(hist.columns, pd.MultiIndex):
        hist.columns = hist.columns.get_level_values(0)
    hist.index = pd.to_datetime(hist.index)
    hist = hist.sort_index().ffill()

    last_dt = hist.index[-1]

    def asof(d):
        return hist.loc[:pd.Timestamp(d)].iloc[-1]

    now = hist.iloc[-1]
    out = pd.DataFrame({
        "group": ref["cntry_of_risk"].map(group_of),
        "issuer": ref["ticker"],
        "name": ref["short_name"],
        "cntry": ref["cntry_of_risk"],
        "rank": ref["payment_rank"],
        "maturity": ref["maturity"],
        "qty": [qty[t.replace(" Corp", "")] for t in ref.index],
        "spread": now,
        "d1": now - hist.iloc[-2],
        "w1": now - asof(last_dt - timedelta(days=7)),
        "m1": now - asof(last_dt - timedelta(days=30)),
        "since": now - asof(SINCE),
    }).round(1)
    out = out.sort_values(["group", "since"], ascending=[True, False])

    # 3) 발행자별 수량가중 요약
    cols = ["spread", "d1", "w1", "m1", "since"]
    issuer = (out.groupby(["group", "issuer"])
                 .apply(lambda g: pd.Series({c: (g[c] * g["qty"]).sum() / g["qty"].sum()
                                             for c in cols} | {"n": len(g)}))
                 .round(1).reset_index()
                 .sort_values(["group", "since"], ascending=[True, False]))
    grp = (out.groupby("group")
              .apply(lambda g: pd.Series({c: (g[c] * g["qty"]).sum() / g["qty"].sum()
                                          for c in cols} | {"n": len(g)}))
              .round(1))

    pd.set_option("display.width", 200, "display.max_rows", 300)
    print(f"\n기준일 {last_dt:%Y-%m-%d} | 필드 {SPREAD_FIELD} | SINCE {SINCE}")
    print("\n[그룹별 수량가중 평균, bp]\n", grp)
    print("\n[발행자별]\n", issuer.to_string(index=False))
    print("\n[종목별]\n", out.drop(columns="qty").to_string())

    out.to_csv(os.path.join(DESKTOP, "eu_fin_spread_change.csv"), encoding="utf-8-sig")
    issuer.to_csv(os.path.join(DESKTOP, "eu_fin_spread_issuer.csv"), index=False, encoding="utf-8-sig")
    print(f"\nCSV 저장 위치: {DESKTOP}")


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception:
        print("\n===== 오류 발생 =====")
        traceback.print_exc()
    finally:
        input("\n엔터를 누르면 창이 닫힙니다...")
