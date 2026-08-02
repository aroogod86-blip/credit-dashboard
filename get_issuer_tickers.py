# -*- coding: utf-8 -*-
"""
get_issuer_tickers.py (v2)

기존 v1은 채권 ISIN -> 발행자 Equity 티커로 매핑을 시도했으나,
- 국채/준정부기관/비상장기업은 Equity 티커 자체가 없거나
- Bloomberg equity mnemonic 규칙(특히 한국/일본)이 채권 TICKER 필드와 달라
매핑 실패율이 84개 중 66개(약 79%)에 달했다.

v2는 이 문제를 근본적으로 우회한다:
Bloomberg는 회사채(Corp 티커)에 발행회사의 펀더멘털 데이터가 이미 연결되어 있으므로,
Equity 티커를 거치지 않고 채권 ISIN(Corp 티커) 자체에서 바로
SALES_REV_TURN / EBITDA / NET_INCOME 등 재무 필드를 pull한다.

주의:
- 국채(Republic of Korea, US Treasury 등) -> 애초에 "기업" 재무제표가 없으므로
  이 방식으로도 데이터가 안 나오는 게 정상이다. 발행자 재무 탭에서는
  국채/국제기구채는 원천적으로 제외 대상으로 분류해서 안내한다.
- 완전 비상장 기업(SpaceX 등) -> Bloomberg가 비상장 재무제표를 커버하는 경우도
  있고 안 하는 경우도 있어 결과가 갈릴 수 있다.
- 발행자당 채권이 여러 개면, 대표 채권 1개(가장 먼저 나오는 것)만 골라 사용한다.
  (여러 채권이 결국 같은 회사 재무제표를 가리키므로 하나면 충분)

실행 환경: Bloomberg Terminal 로그인 + xbbg 설치된 로컬 PC
    pip install xbbg

사용법:
    python get_issuer_tickers.py --isins all_isins.txt --out tickers.csv
"""

import argparse
import sys

try:
    from xbbg import blp
except ImportError:
    print("[ERROR] xbbg가 설치되어 있지 않습니다. pip install xbbg 실행 후 다시 실행하세요.")
    sys.exit(1)

import pandas as pd

FIELDS = ["ISSUER", "INDUSTRY_SECTOR", "CNTRY_OF_RISK", "BOND_TO_EQY_TICKER"]


def to_wide_pandas(raw):
    """narwhals DataFrame(long format: ticker/field/value) -> pandas wide format 변환"""
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


def load_isins(path):
    with open(path, encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


# 국채/국제기구채 등 "기업 재무제표가 없는" 발행자 필터용 키워드
# (issuer명에 이 키워드가 포함되면 재무비율 탭 대상에서 자동 제외 후보로 표시)
SOVEREIGN_KEYWORDS = [
    "REPUBLIC OF", "KINGDOM OF", "GOVERNMENT OF", "US TREASURY",
    "EUROPEAN INVESTMENT BANK", "WORLD BANK", "IBRD", "ASIAN DEVELOPMENT BANK",
]


def is_sovereign(issuer_name):
    name = str(issuer_name).upper()
    return any(kw in name for kw in SOVEREIGN_KEYWORDS)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--isins", type=str, required=True, help="ISIN 목록 텍스트 파일 (한 줄에 하나)")
    parser.add_argument("--out", type=str, default="tickers.csv")
    args = parser.parse_args()

    isins = load_isins(args.isins)
    bond_tickers = [f"{i} Corp" for i in isins]

    print(f"[INFO] {len(isins)}개 ISIN에 대해 ISSUER pull 중...")
    raw = blp.bdp(bond_tickers, FIELDS)

    if raw is None or len(raw) == 0:
        print("[ERROR] Bloomberg에서 빈 결과가 돌아왔습니다. Terminal 로그인 상태를 확인하세요.")
        sys.exit(1)

    snap = to_wide_pandas(raw)
    snap.index.name = "bond_ticker"
    snap = snap.reset_index()

    # 발행자 기준으로 중복 제거 -> 대표 채권 1개만 남김
    issuer_level = (
        snap.dropna(subset=["issuer"])
        .drop_duplicates(subset=["issuer"], keep="first")
        .copy()
    )

    issuer_level["is_sovereign_guess"] = issuer_level["issuer"].apply(is_sovereign)

    # BOND_TO_EQY_TICKER는 "BARC LN" 형태로 오므로 " Equity"를 붙여 완전한 티커로 만든다.
    # (히스토리/추이 차트 전용: Bloomberg가 채권 티커의 BDH 시계열 펀더멘털을 지원 안 하는
    #  경우가 많아서, latest 스냅샷은 채권(Corp) 티커 그대로 쓰고 history만 이 주식 티커로 pull한다.)
    def _to_equity_ticker(v):
        v = str(v).strip()
        if not v or v.lower() == "nan":
            return None
        return f"{v} Equity"

    issuer_level["equity_ticker"] = issuer_level["bond_to_eqy_ticker"].apply(_to_equity_ticker)

    # 재무비율 탭 대상 (국채/국제기구채 제외)
    corp_only = issuer_level[~issuer_level["is_sovereign_guess"]].copy()
    out_df = corp_only[["bond_ticker", "issuer", "industry_sector", "equity_ticker"]].rename(
        columns={"bond_ticker": "ticker", "issuer": "issuer_name"}
    )
    out_df.to_csv(args.out, index=False, encoding="utf-8-sig")
    n_with_eqy = out_df["equity_ticker"].notna().sum()
    print(f"[DONE] {args.out} 저장 완료 ({len(out_df)}개 발행자, Corp 티커 직접 사용, "
          f"주식 티커 매핑 성공 {n_with_eqy}개 -> 추이 차트에 사용)")

    excluded = issuer_level[issuer_level["is_sovereign_guess"]]
    if len(excluded):
        excl_path = args.out.replace(".csv", "_excluded_sovereign.csv")
        excluded[["issuer", "bond_ticker"]].to_csv(excl_path, index=False, encoding="utf-8-sig")
        print(f"[INFO] {len(excluded)}개는 국채/국제기구채로 판단되어 제외 -> {excl_path} 참고")


if __name__ == "__main__":
    main()
