"""
Bloomberg IG 크레딧 채권 → 공유용 HTML 대시보드 생성기
======================================================
필요 패키지:  pip install xbbg pandas numpy

사용법:
  1. Bloomberg Terminal이 실행 중인 PC에서 실행
  2. python export_credit_dashboard.py
  3. 생성된 credit_dashboard_YYYYMMDD.html 파일을 팀에 공유

기존 bloomberg_credit_dashboard.py의 데이터 로드 로직을 그대로 활용하고,
Dash 서버 대신 standalone HTML 파일을 생성합니다.
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import json
import os
import warnings
warnings.filterwarnings("ignore")

# ══════════════════════════════════════════════
# 1. ISIN 목록 (기존과 동일)
# ══════════════════════════════════════════════
BOND_ISINS = [
    "US500630ED65",  "US50064FAY07",  "US50064FBA12",  "US037833FA32",
    "US302154EB15",  "USY8085FBL32",  "US02079KAX54",  "US89115A3C46",
    "USH4209UAC02",  "XS3219365635",  "XS2747557416",  "US02079KBM80",
    "US46647PEU66",  "US61690DK726",  "US86562MEJ53",  "USY8085FBD16",
    "USY8085FBY52",  "USY8085FBZ28",  "XS3030377132",  "USY8085FBK58",
    "US404280CF48",  "USY4899GGX52",  "USY8085FBU31",  "US46647PEY88",
    "US46647PEV40",  "USJ5S39RAQ78",  "US05964HAS40",  "US95000U3T82",
    "US46647PFD33",  "USJ7771YTM95",  "US404280EF20",  "US95000U3P60",
    "USJ5S39RAM64",  "US61747YFY68",  "US500631AE67",  "USY29011DM51",
    "USG84228FY98",  "US06051GHZ54",  "USY29011DG83",  "US46647PEJ12",
    "USJ5S39RAS35",  "US404280EM70",  "USH3698DBM59",  "US44891CDB63",
    "US65535HBF55",  "US06051GMY25",  "USY3815NBK64",  "US98105HAG56",
    "US780097BG51",  "USG84228FU76",  "US06738ECX13",  "XS3078370726",
    "US404280CV97",  "US38141GC365",  "USG84228FL77",  "USH42097EV54",
    "US404280BT50",  "US60687YDL02",  "US44891CDL46",  "USY7S272AG74",
    "XS1795263281",  "US65535HAY53",  "XS3109629371",  "US61748UAR32",
    "US780097BL47",  "USH42097FS17",  "US65535HBM07",  "XS2703610050",
    "USY52758AE20",  "US05964HBJ32",  "US06051GNA30",  "USY7S272AL69",
    "XS3187679041",  "US404280ER67",  "US44891CDV28",  "USY70750CB13",
    "US404280CC17",  "USH3698DCW23",  "US44891CDZ32",  "US82460EAN04",
    "USY5S80VAB27",  "US44891CDM29",  "US50050HAN61",  "XS2798085416",
    "US44891CCE12",  "USY5S5CGAK82",  "US44891CED11",  "USG84228FZ63",
    "XS3299373996",  "US09659X3B68",  "US44891CCU53",  "US46647PFJ03",
    "US46647PFE16",  "US44891CDQ33",  "US404280EN53",  "XS1932879130",
    "US86562MED83",  "XS2739009855",  "US09659X2X97",  "US606822DS05",
    "US44891CBL63",  "XS3189630372",  "USJ5903AAA28",  "US61748UAS15",
    "US44891CDW01",  "US44891CCJ09",  "USY5S5CGAN22",  "XS1452410571",
    "US83368TCJ51",  "US44891CCP68",  "USY5S5CGAB83",  "US44891CEA71",
    "USY5S5CGAL65",  "XS2985211569",  "US44891CEE93",  "US404280EG03",
    "US44891CDH34",  "USH42097FT99",  "US44891CDD20",  "USY7S272AM43",
    "USJ5S39RAU80",  "US65535HCB33",  "USJ5901UAH52",  "USY5S5CGAP79",
    "USY5S5CGAR36",  "KR6065904G23",  "XS2395327641",  "XS3017043053",
    "XS2559683359",  "XS3028206350",  "FR0014010FH7",
]

ISSUER_CURVE_ISINS = [
    "US500630EG96", "US500630ED65", "US500630EH79",
    "US500630DP05", "US500630DU99", "US500630EB00",
    "US302154EA32", "US302154EQ83", "US302154EK14",
    "US302154ER66", "US302154DW60", "US302154ES40",
    "US44891CCX92", "US44891CBP77", "US44891CCD39",
    "US44891CDZ32", "US44891CCY75", "US44891CDG50",
    "US44891CDQ33", "US44891CEA71", "US44891CEE93",
    "US44891CDH34", "US44891CDX83",
    "USY8085FBT67", "USY8085FBY52", "USY8085FBZ28",
    "USY8085FBD16", "USY8085FBL32",
    "USY5S5CGAK82", "USY5S5CGAN22", "USY5S5CGAB83",
    "USY5S5CGAL65", "USY5S5CGAP79", "USY5S5CGAM49",
    "USY5S5CGAR36",
    "USY7S272AE27", "USY7S272AG74", "USY70750CB13",
    "USY7S272AL69", "USY7S272AH57", "USY70750CC95",
    "USY7S272AM43",
    "XS2861753924", "XS3189630372", "XS3299373996",
    "XS3299380850",
    "US61761JZN26", "XS2595028536", "US61748UAR32",
    "US61747YGB56", "US61748UAT97", "US61747YGC30",
    "US46625HJZ47", "US46625HRY89", "US46647PFD33",
    "US46647PFG63", "US46647PDH64", "XS2791972248",
    "US46647PFE16", "US46647PFJ03",
    "US404280DG12", "US404280DU06", "US404280FK06",
    "US404280CF48", "US404280FE46", "US404280FL88",
    "US404280DH94", "US404280DV88", "US404280FG93",
    "US02079KAW71", "US02079KBK25", "US02079KBL08",
    "US02079KBM80", "US02079KBN63", "US02079KBP12",
    "US023135DC78", "US023135DD51", "US023135DE35",
    "US023135DF00", "US023135DG82", "US023135DH65",
    "US68389XDX03", "US68389XDY85", "US68389XDM48",
    "US68389XDZ50", "US68389XCK90", "US68389XDR35",
    "US68389XEB73", "US68389XEC56",
    "USJ5S39RAL81", "USJ5S39RAD65", "USJ5S39RAM64",
    "USJ5S39RAS35", "USJ5S39RAE49", "USJ5S39RAU80",
    "USJ5S39RAV63",
]

ALL_ISINS = list(dict.fromkeys(BOND_ISINS + ISSUER_CURVE_ISINS))

FIELDS = {
    "name":     "SECURITY_DES",
    "issuer":   "ISSUER",
    "rating":   "RTG_SP",
    "sector":   "BICS_LEVEL_1_SECTOR_NAME",
    "industry": "INDUSTRY_SECTOR",
    "maturity": "MATURITY",
    "coupon":   "CPN",
    "ytm":      "YLD_YTM_MID",
    "oas":      "OAS_SPREAD_MID",
    "spread":   "YAS_YLD_SPREAD",
    "zspread":  "Z_SPREAD_MID",
    "duration": "DUR_MID",
    "price":    "PX_LAST",
    "amt_out":  "AMT_OUTSTANDING",
}

UST_TICKERS = {
    "1M":"GB1 Govt","3M":"GB3 Govt","6M":"GB6 Govt",
    "1Y":"GB12 Govt","2Y":"GT2 Govt","3Y":"GT3 Govt",
    "5Y":"GT5 Govt","7Y":"GT7 Govt","10Y":"GT10 Govt",
    "20Y":"GT20 Govt","30Y":"GT30 Govt",
}

MARKET_TICKERS = {
    "LUACTRUU Index":     ("US IG OAS",  "INDEX_OAS_TSY_BP"),
    "LF98TRUU Index":     ("US HY OAS",  "INDEX_OAS_TSY_BP"),
    "CDX IG CDSI Curncy": ("CDX IG 5Y",  "PX_LAST"),
    "CDX HY CDSI Curncy": ("CDX HY 5Y",  "PX_LAST"),
    "VIX Index":          ("VIX",        "PX_LAST"),
    "MOVE Index":         ("MOVE",       "PX_LAST"),
}

END_DATE   = datetime.today()
START_DATE = END_DATE - timedelta(days=90)
MKT_START  = END_DATE - timedelta(days=180)


# ══════════════════════════════════════════════
# 2. narwhals 헬퍼
# ══════════════════════════════════════════════
def _nw(r):
    try:
        import narwhals as nw
        native = nw.to_native(r)
        if hasattr(native, "to_pandas"):
            return native.to_pandas()
    except:
        pass
    if hasattr(r, "to_pandas"):
        return r.to_pandas()
    return pd.DataFrame(r)


# ══════════════════════════════════════════════
# 3. Bloomberg 데이터 로드
# ══════════════════════════════════════════════
def load_bloomberg_data():
    """Bloomberg에서 모든 데이터를 가져와 JSON 직렬화 가능한 dict로 반환"""
    from xbbg import blp

    result = {}
    print("=" * 60)
    print("  Bloomberg 데이터 로드 시작")
    print("=" * 60)

    # ─── 보유 채권 스냅샷 ───
    print("\n[1/7] 보유 채권 스냅샷 로드 중...")
    tickers = [f"{i} Corp" for i in BOND_ISINS]
    field_values = list(FIELDS.values())
    field_keys = list(FIELDS.keys())

    raw = blp.bdp(tickers, field_values)
    pdf = _nw(raw)
    pdf = pdf.drop_duplicates(subset=["ticker", "field"], keep="last")
    snap = pdf.pivot(index="ticker", columns="field", values="value")
    snap.columns.name = None
    snap = snap.rename(columns=dict(zip(field_values, field_keys)))

    for col in ["oas","spread","ytm","duration","price","coupon","amt_out","zspread"]:
        if col in snap.columns:
            snap[col] = pd.to_numeric(snap[col], errors="coerce")

    if "sector" in snap.columns and "industry" in snap.columns:
        snap["sector"] = snap["sector"].fillna(snap["industry"])
        snap.loc[snap["sector"].astype(str).isin(["nan","None",""]), "sector"] = snap["industry"]
    elif "industry" in snap.columns:
        snap["sector"] = snap["industry"]

    def make_label(r):
        try:
            for col in ["name", "SECURITY_DES"]:
                val = str(r.get(col, "")).strip()
                if val and val not in ("nan", "None", ""):
                    return val
            issuer = str(r.get("issuer", "")).strip()
            coupon = r.get("coupon")
            mat    = r.get("maturity")
            if issuer and issuer not in ("nan","None","") and pd.notna(coupon) and pd.notna(mat):
                return f"{issuer} {float(coupon):.2f} {pd.to_datetime(mat).strftime('%m/%y')}"
            elif issuer and issuer not in ("nan","None",""):
                return issuer
            return r.name.replace(" Corp", "")
        except:
            return r.name.replace(" Corp", "")

    snap["label"] = snap.apply(make_label, axis=1)
    snap["is_owned"] = True

    # 지역 분류
    def classify_region(ticker):
        isin = str(ticker).replace(" Corp","").strip()
        if isin.startswith("USY") or isin.startswith("USG") or isin.startswith("USH") or isin.startswith("USJ"):
            return "아시아/기타"
        if isin.startswith("US"):
            return "미국"
        if any(isin.startswith(p) for p in ["XS","FR","DE","GB"]):
            return "유럽"
        if any(isin.startswith(p) for p in ["KR","JP","SG","HK"]):
            return "아시아/기타"
        return "기타"

    snap["region"] = snap.index.map(classify_region)

    # 만기 구간
    def maturity_bucket(mat):
        try:
            if pd.isna(mat): return None
            years = (pd.to_datetime(mat) - pd.Timestamp.today()).days / 365.25
            if years < 1: return "1년 이하"
            elif years < 3: return "1-3년"
            elif years < 5: return "3-5년"
            elif years < 7: return "5-7년"
            elif years < 10: return "7-10년"
            elif years < 20: return "10-20년"
            else: return "20년 이상"
        except:
            return None

    snap["matBucket"] = snap["maturity"].apply(maturity_bucket) if "maturity" in snap.columns else None
    print(f"  → {len(snap)}개 종목 로드 완료")

    # ─── 벤치마크 → G-spread 시계열 ───
    print("\n[2/7] 벤치마크 매핑 + G-spread 시계열...")
    try:
        raw_bm = blp.bdp(tickers, ["YAS_BENCHMARK_BOND_ISIN"])
        bm_pdf = _nw(raw_bm)
        bm_pdf = bm_pdf.drop_duplicates(subset=["ticker","field"], keep="last")
        bm_snap = bm_pdf.pivot(index="ticker", columns="field", values="value")

        bm_isins = bm_snap.get("YAS_BENCHMARK_BOND_ISIN", pd.Series()).dropna().unique().tolist()
        bm_isins = [i for i in bm_isins if i]
        if bm_isins:
            raw_bm_mat = blp.bdp([f"{i} Corp" for i in bm_isins], "MATURITY")
            bm_mat_pdf = _nw(raw_bm_mat)
            bm_mat_pdf = bm_mat_pdf.drop_duplicates(subset=["ticker","field"], keep="last")
            bm_mat_map = dict(zip(bm_mat_pdf["ticker"], bm_mat_pdf["value"]))
        else:
            bm_mat_map = {}

        def years_to_generic(years):
            if years is None or pd.isna(years): return "GT10 Govt"
            generic = [(2,"GT2 Govt"),(3,"GT3 Govt"),(5,"GT5 Govt"),
                       (7,"GT7 Govt"),(10,"GT10 Govt"),(20,"GT20 Govt"),(30,"GT30 Govt")]
            return min(generic, key=lambda x: abs(x[0]-years))[1]

        bm_map = {}
        for t in tickers:
            bm_isin = bm_snap.loc[t, "YAS_BENCHMARK_BOND_ISIN"] if t in bm_snap.index else None
            if pd.notna(bm_isin) and bm_isin:
                bm_full = f"{bm_isin} Corp"
                mat = bm_mat_map.get(bm_full)
                if pd.notna(mat) and mat:
                    years = (pd.to_datetime(mat) - pd.Timestamp.today()).days / 365.25
                    bm_map[t] = years_to_generic(years)
                    continue
            if t in snap.index and pd.notna(snap.loc[t].get("duration")):
                bm_map[t] = years_to_generic(float(snap.loc[t]["duration"]))
            else:
                bm_map[t] = "GT10 Govt"

        generic_tickers = sorted(set(bm_map.values()))
    except Exception as e:
        print(f"  벤치마크 조회 오류: {e}")
        bm_map = {t: "GT10 Govt" for t in tickers}
        generic_tickers = ["GT10 Govt"]

    # YTM 시계열 + Generic UST → G-spread 계산
    try:
        raw_h = blp.bdh(tickers, "YLD_YTM_MID",
                        START_DATE.strftime("%Y%m%d"), END_DATE.strftime("%Y%m%d"))
        hpdf = _nw(raw_h)
        bond_ytm_ts = hpdf.pivot(index="date", columns="ticker", values="value")
        bond_ytm_ts.index = pd.to_datetime(bond_ytm_ts.index)
        bond_ytm_ts = bond_ytm_ts.apply(pd.to_numeric, errors="coerce")

        raw_gen = blp.bdh(generic_tickers, "YLD_YTM_MID",
                          START_DATE.strftime("%Y%m%d"), END_DATE.strftime("%Y%m%d"))
        gen_pdf = _nw(raw_gen)
        gen_ytm = gen_pdf.pivot(index="date", columns="ticker", values="value")
        gen_ytm.index = pd.to_datetime(gen_ytm.index)
        gen_ytm = gen_ytm.apply(pd.to_numeric, errors="coerce")

        hist = pd.DataFrame(index=bond_ytm_ts.index)
        label_map = dict(zip(snap.index, snap["label"]))
        for t in tickers:
            gen_t = bm_map.get(t, "GT10 Govt")
            label = label_map.get(t, t)
            if t in bond_ytm_ts.columns and gen_t in gen_ytm.columns:
                hist[label] = (bond_ytm_ts[t] - gen_ytm[gen_t]) * 100  # bps
        hist = hist.dropna(how="all")
        print(f"  → G-spread 시계열: {len(hist)}일, {hist.notna().any().sum()}개 종목")
    except Exception as e:
        print(f"  BDH 오류: {e}")
        hist = pd.DataFrame(index=pd.bdate_range(START_DATE, END_DATE))

    # spread 변동 계산
    for idx, row in snap.iterrows():
        lbl = row["label"]
        if lbl in hist.columns and len(hist) > 0:
            s = hist[lbl].dropna()
            n = len(s)
            snap.at[idx, "chg1d"] = float(s.iloc[-1] - s.iloc[-2]) if n > 1 else None
            snap.at[idx, "chg1w"] = float(s.iloc[-1] - s.iloc[-6]) if n > 5 else None
            snap.at[idx, "chg1m"] = float(s.iloc[-1] - s.iloc[-22]) if n > 21 else None
            snap.at[idx, "chg3m"] = float(s.iloc[-1] - s.iloc[0]) if n > 1 else None
        else:
            snap.at[idx, "chg1d"] = None
            snap.at[idx, "chg1w"] = None
            snap.at[idx, "chg1m"] = None
            snap.at[idx, "chg3m"] = None

    # ─── UST 수익률 곡선 ───
    print("\n[3/7] UST 수익률 곡선...")
    try:
        ust_t = list(UST_TICKERS.values())
        raw_u = blp.bdp(ust_t, "YLD_YTM_MID")
        updf = _nw(raw_u)
        ust_now_map = dict(zip(updf["ticker"], pd.to_numeric(updf["value"], errors="coerce")))

        raw_u3 = blp.bdh(ust_t, "YLD_YTM_MID",
                         (END_DATE - timedelta(days=92)).strftime("%Y%m%d"),
                         (END_DATE - timedelta(days=88)).strftime("%Y%m%d"))
        u3pdf = _nw(raw_u3)
        ust3m_map = (u3pdf.groupby("ticker")["value"].last()
                         .apply(pd.to_numeric, errors="coerce").to_dict())

        ust_now = [float(ust_now_map.get(t, float("nan"))) for t in ust_t]
        ust_3m_ago = [float(ust3m_map.get(t, float("nan"))) for t in ust_t]
        print(f"  → UST 곡선 로드 완료")
    except Exception as e:
        print(f"  UST 곡선 오류: {e}")
        ust_now = [5.31,5.28,5.12,4.85,4.62,4.52,4.41,4.38,4.32,4.51,4.55]
        ust_3m_ago = [5.05,5.10,4.95,4.68,4.44,4.33,4.25,4.22,4.18,4.37,4.43]

    # ─── 발행자 곡선용 추가 채권 ───
    print("\n[4/7] 발행자 곡선 추가 채권...")
    extra_tickers = [f"{i} Corp" for i in ISSUER_CURVE_ISINS if i not in BOND_ISINS]
    extra_snap = pd.DataFrame()
    extra_ytm_ts = pd.DataFrame()
    if extra_tickers:
        try:
            raw_ex = blp.bdp(extra_tickers, field_values)
            ex_pdf = _nw(raw_ex)
            ex_pdf = ex_pdf.drop_duplicates(subset=["ticker","field"], keep="last")
            extra_snap = ex_pdf.pivot(index="ticker", columns="field", values="value")
            extra_snap.columns.name = None
            extra_snap = extra_snap.rename(columns=dict(zip(field_values, field_keys)))
            for col in ["oas","spread","ytm","duration","price","coupon"]:
                if col in extra_snap.columns:
                    extra_snap[col] = pd.to_numeric(extra_snap[col], errors="coerce")
            extra_snap["label"] = extra_snap.apply(make_label, axis=1)
            extra_snap["is_owned"] = False
            extra_snap["region"] = extra_snap.index.map(classify_region)
            extra_snap["matBucket"] = extra_snap["maturity"].apply(maturity_bucket) if "maturity" in extra_snap.columns else None

            # YTM 시계열
            raw_exh = blp.bdh(extra_tickers, "YLD_YTM_MID",
                              START_DATE.strftime("%Y%m%d"), END_DATE.strftime("%Y%m%d"))
            exh_pdf = _nw(raw_exh)
            extra_ytm_ts = exh_pdf.pivot(index="date", columns="ticker", values="value")
            extra_ytm_ts.index = pd.to_datetime(extra_ytm_ts.index)
            extra_ytm_ts = extra_ytm_ts.apply(pd.to_numeric, errors="coerce")
            ex_label_map = dict(zip(extra_snap.index, extra_snap["label"]))
            extra_ytm_ts.columns = [ex_label_map.get(c, c) for c in extra_ytm_ts.columns]

            # 중복 제거
            extra_snap = extra_snap[~extra_snap.index.isin(snap.index)]
            print(f"  → 추가 {len(extra_snap)}개 채권 로드 완료")
        except Exception as e:
            print(f"  추가 채권 오류: {e}")

    # ─── 시장 지표 ───
    print("\n[5/7] 시장 지표 (VIX, MOVE, CDX 등)...")
    mkt_ts = {}
    mkt_current = {}
    mkt_changes = {}
    for tk, (name, field) in MARKET_TICKERS.items():
        try:
            r = blp.bdh([tk], field, MKT_START.strftime("%Y%m%d"), END_DATE.strftime("%Y%m%d"))
            mpdf = _nw(r)
            if len(mpdf) > 0:
                s = mpdf.set_index("date")["value"]
                s = pd.to_numeric(s, errors="coerce").dropna().sort_index()
                s.index = pd.to_datetime(s.index)
                if len(s) > 0:
                    mkt_ts[name] = {d.strftime("%Y-%m-%d"): float(v) for d, v in s.items()}
                    mkt_current[name] = float(s.iloc[-1])
                    chg = {}
                    for lbl, n in [("1d",1),("1w",5),("1m",21)]:
                        chg[lbl] = float(s.iloc[-1] - s.iloc[-(n+1)]) if len(s) > n else None
                    mkt_changes[name] = chg
                    continue
        except Exception as e:
            print(f"  {name} 오류: {e}")
        mkt_current[name] = None
        mkt_changes[name] = {"1d": None, "1w": None, "1m": None}
    print(f"  → {len(mkt_current)}개 지표 로드")

    # ─── 거래 데이터 (최근 30일) ───
    print("\n[6/7] 거래 데이터...")
    trades = []
    sample_tickers = tickers[:10]
    for tk in sample_tickers:
        try:
            end = datetime.today()
            start = end - timedelta(days=30)
            r = blp.bdh([tk], ["PX_LAST","YLD_YTM_MID","PX_VOLUME"],
                        start.strftime("%Y%m%d"), end.strftime("%Y%m%d"))
            tpdf = _nw(r)
            if len(tpdf) > 0:
                df = tpdf.pivot_table(index="date", columns="field", values="value", aggfunc="last").reset_index()
                lbl = snap.loc[tk, "label"] if tk in snap.index else tk
                for _, row in df.iterrows():
                    px = pd.to_numeric(row.get("PX_LAST"), errors="coerce")
                    ytm_val = pd.to_numeric(row.get("YLD_YTM_MID"), errors="coerce")
                    vol = pd.to_numeric(row.get("PX_VOLUME"), errors="coerce")
                    trades.append({
                        "date": pd.to_datetime(row["date"]).strftime("%Y-%m-%d"),
                        "bond": lbl,
                        "price": round(float(px), 3) if pd.notna(px) else None,
                        "ytm": round(float(ytm_val), 3) if pd.notna(ytm_val) else None,
                        "volume": int(vol) if pd.notna(vol) and vol > 0 else None,
                    })
        except:
            pass
    trades.sort(key=lambda x: x["date"], reverse=True)
    print(f"  → {len(trades)}건 거래 로드")

    # ─── JSON 직렬화 ───
    print("\n[7/7] JSON 변환 중...")

    # 보유 + 추가 합치기
    combined = pd.concat([snap, extra_snap]) if not extra_snap.empty else snap.copy()

    def bonds_to_json(df):
        records = []
        for idx, row in df.iterrows():
            rec = {}
            for c in ["label","issuer","sector","rating","region","matBucket","is_owned"]:
                v = row.get(c)
                rec[c] = str(v) if pd.notna(v) and str(v) not in ("nan","None") else None
            for c in ["spread","ytm","duration","price","coupon","oas","chg1d","chg1w","chg1m","chg3m"]:
                v = row.get(c)
                rec[c] = round(float(v), 3) if pd.notna(v) else None
            rec["is_owned"] = bool(row.get("is_owned", True))
            records.append(rec)
        return records

    def ts_to_json(df):
        if df.empty:
            return {"dates": [], "series": {}}
        dates = [d.strftime("%Y-%m-%d") for d in df.index]
        series = {}
        for col in df.columns:
            series[col] = [round(float(v), 2) if pd.notna(v) else None for v in df[col]]
        return {"dates": dates, "series": series}

    result = {
        "generated": datetime.today().strftime("%Y-%m-%d %H:%M"),
        "bondCount": len(snap),
        "bonds": bonds_to_json(snap),
        "allBonds": bonds_to_json(combined),
        "spreadHistory": ts_to_json(hist),
        "ustCurve": {
            "tenors": list(UST_TICKERS.keys()),
            "years": [1/12,.25,.5,1,2,3,5,7,10,20,30],
            "now": [round(v, 3) if not np.isnan(v) else None for v in ust_now],
            "ago3m": [round(v, 3) if not np.isnan(v) else None for v in ust_3m_ago],
        },
        "marketTimeseries": mkt_ts,
        "marketCurrent": {k: round(v, 2) if v else None for k, v in mkt_current.items()},
        "marketChanges": mkt_changes,
        "trades": trades,
    }

    print(f"\n{'='*60}")
    print(f"  데이터 로드 완료!")
    print(f"  보유 채권: {len(snap)}개")
    print(f"  추가 채권: {len(extra_snap)}개")
    print(f"  시계열:    {len(hist)}일")
    print(f"  시장 지표: {len(mkt_current)}개")
    print(f"  거래:      {len(trades)}건")
    print(f"{'='*60}")

    return result


def load_sample_data():
    """Bloomberg 없을 때 샘플 데이터 생성"""
    np.random.seed(42)
    ISSUERS = ['HSBC','JPMorgan','Goldman Sachs','Morgan Stanley','BNP Paribas',
               'Samsung','SK Hynix','Hyundai','POSCO','LG Chem','Toyota','Mizuho',
               'Barclays','Deutsche Bank','Credit Agricole','Societe Generale',
               'ING','UBS','Standard Chartered','ANZ']
    SECTORS = ['Financials','Technology','Energy','Utilities','Materials','Consumer','Industrials','Communications']
    RATINGS = ['AA+','AA','AA-','A+','A','A-','BBB+','BBB','BBB-']
    REGIONS = ['미국','유럽','아시아/기타']
    MAT_BUCKETS = ['1-3년','3-5년','5-7년','7-10년','10-20년']

    bonds = []
    for i, isin in enumerate(BOND_ISINS):
        issuer = ISSUERS[i % len(ISSUERS)]
        cpn = round(2.0 + np.random.random() * 4.5, 2)
        mat_y = 25 + np.random.randint(0, 10)
        mat_m = str(1 + np.random.randint(0,12)).zfill(2)
        bonds.append({
            "label": f"{issuer} {cpn:.2f} {mat_m}/{mat_y}",
            "issuer": issuer,
            "sector": np.random.choice(SECTORS),
            "rating": np.random.choice(RATINGS),
            "region": np.random.choice(REGIONS),
            "matBucket": np.random.choice(MAT_BUCKETS),
            "spread": int(70 + np.random.random() * 250),
            "duration": round(1.5 + np.random.random() * 8, 1),
            "ytm": None,
            "price": round(88 + np.random.random() * 16, 3),
            "coupon": cpn,
            "oas": None,
            "is_owned": True,
        })
    for b in bonds:
        b["ytm"] = round(4.3 + b["spread"] / 100, 2)
        b["oas"] = b["spread"]

    # 추가 채권 (발행자 곡선용)
    all_bonds = list(bonds)
    extra_isins = [i for i in ISSUER_CURVE_ISINS if i not in BOND_ISINS]
    for i, isin in enumerate(extra_isins):
        issuer = ISSUERS[i % len(ISSUERS)]
        cpn = round(2.0 + np.random.random() * 4.5, 2)
        mat_y = 25 + np.random.randint(0, 10)
        mat_m = str(1 + np.random.randint(0,12)).zfill(2)
        sp = int(70 + np.random.random() * 250)
        all_bonds.append({
            "label": f"{issuer} {cpn:.2f} {mat_m}/{mat_y} (추가)",
            "issuer": issuer,
            "sector": np.random.choice(SECTORS),
            "rating": np.random.choice(RATINGS),
            "region": np.random.choice(REGIONS),
            "matBucket": np.random.choice(MAT_BUCKETS),
            "spread": sp,
            "duration": round(1.5 + np.random.random() * 8, 1),
            "ytm": round(4.3 + sp / 100, 2),
            "price": round(88 + np.random.random() * 16, 3),
            "coupon": cpn,
            "oas": sp,
            "is_owned": False,
        })

    # 스프레드 시계열
    dates = pd.bdate_range(START_DATE, END_DATE)
    date_strs = [d.strftime("%Y-%m-%d") for d in dates]
    series = {}
    for b in bonds:
        base = b["spread"]
        noise = np.cumsum(np.random.randn(len(dates)) * 3)
        vals = np.clip(base + noise - noise[-1], base * 0.5, base * 1.6)
        series[b["label"]] = [round(float(v), 1) for v in vals]

    # 변동 계산
    for b in bonds:
        s = series.get(b["label"], [])
        n = len(s)
        b["chg1d"] = round(s[-1] - s[-2], 1) if n > 1 else None
        b["chg1w"] = round(s[-1] - s[-6], 1) if n > 5 else None
        b["chg1m"] = round(s[-1] - s[-22], 1) if n > 21 else None
        b["chg3m"] = round(s[-1] - s[0], 1) if n > 1 else None

    # 시장 지표
    mkt_dates = pd.bdate_range(MKT_START, END_DATE)
    mkt_date_strs = [d.strftime("%Y-%m-%d") for d in mkt_dates]
    mkt_ts = {}
    mkt_bases = {"US IG OAS": 95, "US HY OAS": 380, "CDX IG 5Y": 55, "CDX HY 5Y": 340, "VIX": 18, "MOVE": 105}
    mkt_vols = {"US IG OAS": 3, "US HY OAS": 12, "CDX IG 5Y": 2, "CDX HY 5Y": 10, "VIX": 2, "MOVE": 4}
    mkt_current = {}
    mkt_changes = {}
    for name, base in mkt_bases.items():
        vals = [base]
        for i in range(1, len(mkt_dates)):
            vals.append(max(base * 0.6, min(base * 1.5, vals[-1] + (np.random.random() - 0.48) * mkt_vols[name])))
        mkt_ts[name] = {mkt_date_strs[i]: round(float(v), 1) for i, v in enumerate(vals)}
        mkt_current[name] = round(vals[-1], 1)
        mkt_changes[name] = {
            "1d": round(vals[-1] - vals[-2], 1) if len(vals) > 1 else None,
            "1w": round(vals[-1] - vals[-6], 1) if len(vals) > 5 else None,
            "1m": round(vals[-1] - vals[-22], 1) if len(vals) > 21 else None,
        }

    # 거래 샘플
    trades = []
    for b in bonds[:10]:
        for _ in range(8):
            dd = datetime.today() - timedelta(days=np.random.randint(0, 30))
            trades.append({
                "date": dd.strftime("%Y-%m-%d"),
                "bond": b["label"],
                "price": round(b["price"] + (np.random.random() - 0.5) * 2, 3),
                "ytm": round(b["ytm"] + (np.random.random() - 0.5) * 0.3, 3),
                "volume": int(np.random.randint(1, 50) * 100000),
            })
    trades.sort(key=lambda x: x["date"], reverse=True)

    return {
        "generated": datetime.today().strftime("%Y-%m-%d %H:%M"),
        "bondCount": len(bonds),
        "bonds": bonds,
        "allBonds": all_bonds,
        "spreadHistory": {"dates": date_strs, "series": series},
        "ustCurve": {
            "tenors": list(UST_TICKERS.keys()),
            "years": [1/12,.25,.5,1,2,3,5,7,10,20,30],
            "now": [5.31,5.28,5.12,4.85,4.62,4.52,4.41,4.38,4.32,4.51,4.55],
            "ago3m": [5.05,5.10,4.95,4.68,4.44,4.33,4.25,4.22,4.18,4.37,4.43],
        },
        "marketTimeseries": mkt_ts,
        "marketCurrent": mkt_current,
        "marketChanges": mkt_changes,
        "trades": trades,
    }


# ══════════════════════════════════════════════
# 4. HTML 템플릿 생성
# ══════════════════════════════════════════════
def generate_html(data):
    """데이터를 받아 standalone HTML 대시보드 문자열을 반환"""

    data_json = json.dumps(data, ensure_ascii=False)

    html = f'''<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>IG Credit Spread Dashboard</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:opsz,wght@9..40,300;9..40,500;9..40,700&family=JetBrains+Mono:wght@400;500;600&display=swap');
:root {{
  --bg:#0b0f1a;--sf:#111827;--sf2:#1a2235;--sf3:#222d42;--bd:#2a3550;
  --tx:#e8ecf4;--tx2:#8b98b0;--tx3:#5c6b85;
  --ac:#3b82f6;--acd:#1e40af;--gn:#10b981;--rd:#ef4444;--am:#f59e0b;--cy:#06b6d4;--pu:#8b5cf6;
  --ft:'DM Sans',sans-serif;--mn:'JetBrains Mono',monospace;
}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:var(--ft);background:var(--bg);color:var(--tx);min-height:100vh;-webkit-font-smoothing:antialiased}}
.hd{{background:linear-gradient(135deg,#0f1729,#162040);border-bottom:1px solid var(--bd);padding:16px 32px;display:flex;justify-content:space-between;align-items:center;position:sticky;top:0;z-index:100;backdrop-filter:blur(12px)}}
.hd h1{{font-size:18px;font-weight:700;letter-spacing:-.3px}}
.hd .sub{{font-size:11px;color:var(--tx3);margin-top:2px;font-family:var(--mn)}}
.hd .st{{display:flex;align-items:center;gap:6px;font-size:12px;color:var(--gn)}}
.hd .st::before{{content:'';width:7px;height:7px;border-radius:50%;background:var(--gn);animation:pulse 2s infinite}}
@keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:.4}}}}
.tabs{{display:flex;gap:2px;padding:12px 32px 0;background:var(--bg);border-bottom:1px solid var(--bd)}}
.tab{{padding:10px 20px;font-size:13px;font-weight:500;color:var(--tx3);cursor:pointer;border:1px solid transparent;border-bottom:none;border-radius:8px 8px 0 0;transition:all .2s;user-select:none}}
.tab:hover{{color:var(--tx2);background:var(--sf)}}
.tab.active{{color:var(--ac);background:var(--sf);border-color:var(--bd);position:relative}}
.tab.active::after{{content:'';position:absolute;bottom:-1px;left:0;right:0;height:2px;background:var(--ac)}}
.ct{{padding:20px 32px 40px;max-width:1440px;margin:0 auto}}
.tp{{display:none}}.tp.active{{display:block;animation:fi .3s}}
@keyframes fi{{from{{opacity:0;transform:translateY(6px)}}to{{opacity:1;transform:none}}}}
.ms{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-bottom:20px}}
.mc{{background:var(--sf);border:1px solid var(--bd);border-radius:10px;padding:16px;transition:border-color .2s}}
.mc:hover{{border-color:var(--acd)}}
.mc .lb{{font-size:11px;color:var(--tx3);text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px}}
.mc .vl{{font-size:24px;font-weight:700;font-family:var(--mn)}}
.mc .dl{{font-size:11px;margin-top:6px;font-family:var(--mn)}}
.mc .dl.up{{color:var(--rd)}}.mc .dl.dn{{color:var(--gn)}}
.cd{{background:var(--sf);border:1px solid var(--bd);border-radius:10px;padding:20px;margin-bottom:16px}}
.ct2{{font-size:14px;font-weight:600;margin-bottom:14px;display:flex;align-items:center;gap:8px}}
.ib{{background:rgba(59,130,246,.08);border:1px solid rgba(59,130,246,.2);border-radius:8px;padding:10px 16px;font-size:12px;color:var(--ac);margin-bottom:16px}}
.g2{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px}}
.g3{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:16px}}
@media(max-width:900px){{.g2,.g3,.ig{{grid-template-columns:1fr}}}}
.tw{{overflow-x:auto;border-radius:8px;border:1px solid var(--bd)}}
table{{width:100%;border-collapse:collapse;font-size:12px}}
thead th{{padding:10px 12px;text-align:left;font-size:11px;font-weight:600;color:var(--tx3);text-transform:uppercase;letter-spacing:.4px;background:var(--sf2);border-bottom:2px solid var(--bd);position:sticky;top:0;cursor:pointer;user-select:none;white-space:nowrap}}
thead th:hover{{color:var(--tx)}}thead th.so{{color:var(--ac)}}
tbody td{{padding:9px 12px;border-bottom:1px solid rgba(42,53,80,.5);font-family:var(--mn);font-size:11.5px;white-space:nowrap}}
tbody tr:hover{{background:rgba(59,130,246,.04)}}
td.tc{{font-family:var(--ft)}}td.nm{{text-align:right}}td.ps{{color:var(--rd)}}td.ng{{color:var(--gn)}}td.sp{{font-weight:600}}
.bg{{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px}}
.bt{{padding:6px 14px;font-size:12px;font-family:var(--ft);font-weight:500;border:1px solid var(--bd);border-radius:6px;background:var(--sf2);color:var(--tx2);cursor:pointer;transition:all .15s}}
.bt:hover{{border-color:var(--acd);color:var(--tx);background:var(--sf3)}}
.bt.pr{{background:var(--ac);color:#fff;border-color:var(--ac)}}.bt.pr:hover{{background:#2563eb}}
.fl{{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:14px}}
.fl select,.fl input{{padding:7px 12px;font-size:12px;font-family:var(--ft);background:var(--sf2);color:var(--tx);border:1px solid var(--bd);border-radius:6px;outline:none}}
.fl select:focus,.fl input:focus{{border-color:var(--ac)}}
.fl label{{font-size:12px;font-weight:500;color:var(--tx3)}}
.ig{{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:20px}}
.ic{{background:var(--sf);border:1px solid var(--bd);border-radius:10px;padding:14px}}
.ic .it{{font-size:13px;font-weight:600;margin-bottom:4px}}
.ic .is{{font-size:11px;color:var(--tx3);margin-bottom:10px}}
.mi{{border-left:3px solid var(--pu);padding:12px 16px;margin-bottom:10px;background:var(--sf2);border-radius:0 6px 6px 0}}
.mi .mh{{display:flex;justify-content:space-between;margin-bottom:6px;align-items:center}}
.mi .mt{{font-size:10px;background:var(--pu);color:white;padding:2px 8px;border-radius:4px;font-weight:600}}
.mi .mtl{{font-size:13px;font-weight:600;margin-left:8px}}
.mi .md{{font-size:11px;color:var(--tx3);font-family:var(--mn)}}
.mi .mb{{font-size:12px;color:var(--tx2);white-space:pre-wrap;line-height:1.6}}
textarea,.ti{{width:100%;padding:10px 14px;font-size:13px;font-family:var(--ft);background:var(--sf2);color:var(--tx);border:1px solid var(--bd);border-radius:6px;outline:none;resize:vertical}}
textarea:focus,.ti:focus{{border-color:var(--ac)}}
::-webkit-scrollbar{{width:6px;height:6px}}::-webkit-scrollbar-track{{background:transparent}}::-webkit-scrollbar-thumb{{background:var(--bd);border-radius:3px}}
.ch{{position:relative;width:100%}}.ch canvas{{display:block}}
.en{{position:fixed;bottom:20px;right:20px;background:var(--gn);color:#fff;padding:10px 20px;border-radius:8px;font-size:13px;font-weight:500;opacity:0;transform:translateY(10px);transition:all .3s;pointer-events:none}}.en.show{{opacity:1;transform:none}}
/* Legend dot for issuer curves */
.lg{{display:flex;gap:16px;flex-wrap:wrap;margin-bottom:8px;font-size:11px;color:var(--tx2)}}
.lg span::before{{content:'';display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:4px;vertical-align:middle}}
.lg .owned::before{{background:currentColor}}.lg .extra::before{{background:transparent;border:2px solid currentColor}}
</style>
</head>
<body>
<div class="hd"><div><h1>IG Credit Spread Dashboard</h1><div class="sub" id="hdr-sub"></div></div><div class="st" id="hdr-status"></div></div>
<div class="tabs">
  <div class="tab active" data-tab="t1">🎯 보유 채권</div>
  <div class="tab" data-tab="t2">📊 크레딧 지표</div>
  <div class="tab" data-tab="t3">🏢 발행자별 곡선</div>
  <div class="tab" data-tab="t4">💼 거래 내역</div>
  <div class="tab" data-tab="t5">📝 리서치 메모</div>
</div>
<div class="ct">
<!-- TAB 1 -->
<div class="tp active" id="t1">
  <div class="ms" id="m1"></div>
  <div class="ib" id="ss">📌 종목을 선택하면 평균 통계가 표시됩니다</div>
  <div class="cd">
    <div class="bg"><span style="font-size:12px;color:var(--tx3);font-weight:600;line-height:28px">빠른 선택:</span>
      <button class="bt" onclick="SA()">전체</button><button class="bt" onclick="SN()">해제</button>
      <button class="bt" onclick="ST()">상위 10</button><button class="bt" onclick="SB()">하위 10</button></div>
    <div class="fl">
      <label>섹터</label><select id="fs" onchange="AF()"><option value="ALL">전체</option></select>
      <label>만기</label><select id="fm" onchange="AF()"><option value="ALL">전체</option></select>
      <label>지역</label><select id="fr" onchange="AF()"><option value="ALL">전체</option></select>
      <label>검색</label><input type="text" id="fx" placeholder="채권명 검색..." oninput="AF()" style="width:200px">
      <button class="bt pr" onclick="EX()">📥 CSV</button></div>
    <div id="fc" style="font-size:11px;color:var(--tx3);margin-bottom:6px"></div>
  </div>
  <div class="cd"><div class="ct2">📈 G-spread 추이 (bps, 3개월)</div><div class="ch" style="height:320px"><canvas id="c1"></canvas></div></div>
  <div class="g2">
    <div class="cd"><div class="ct2">📐 UST 수익률 곡선 + 선택 채권</div><div class="ch" style="height:280px"><canvas id="c2"></canvas></div></div>
    <div class="cd"><div class="ct2">📊 현재 G-spread 비교</div><div class="ch" style="height:280px"><canvas id="c3"></canvas></div></div>
  </div>
  <div class="cd"><div class="ct2">📋 종목별 현황 <span style="font-size:11px;color:var(--tx3);font-weight:400;margin-left:8px">컬럼 클릭=정렬 | 행 클릭=선택</span></div>
    <div class="tw" style="max-height:500px;overflow-y:auto"><table><thead><tr id="th"></tr></thead><tbody id="tb"></tbody></table></div></div>
</div>
<!-- TAB 2 -->
<div class="tp" id="t2">
  <div class="ib">📊 크레딧 시장 전반의 위험도와 투자심리를 보여주는 핵심 지표</div>
  <div class="ms" id="m2"></div>
  <div class="cd"><div class="ct2">📉 크레딧 스프레드 인덱스 (6개월)</div><div class="ch" style="height:300px"><canvas id="c4"></canvas></div></div>
  <div class="g2">
    <div class="cd"><div class="ct2">⚡ VIX</div><div class="ch" style="height:240px"><canvas id="c5"></canvas></div></div>
    <div class="cd"><div class="ct2">🔔 MOVE</div><div class="ch" style="height:240px"><canvas id="c6"></canvas></div></div>
  </div>
</div>
<!-- TAB 3 -->
<div class="tp" id="t3">
  <div class="ib">🏢 발행자별 개별 수익률 곡선 — ● 보유  ○ 비보유</div>
  <div class="ig" id="ic"></div>
  <div class="cd"><div class="ct2">🏭 섹터 평균 곡선</div><div class="ch" style="height:300px"><canvas id="c7"></canvas></div></div>
</div>
<!-- TAB 4 -->
<div class="tp" id="t4">
  <div class="ib">💼 최근 거래 데이터</div>
  <div class="cd"><div class="ct2">💹 거래 내역</div>
    <div class="tw" style="max-height:500px;overflow-y:auto">
      <table><thead><tr><th>날짜</th><th>채권</th><th>가격</th><th>YTM(%)</th><th>거래량</th><th>사이즈</th></tr></thead><tbody id="t4b"></tbody></table></div></div>
</div>
<!-- TAB 5 -->
<div class="tp" id="t5">
  <div class="ib">📝 리서치 핵심 내용을 메모해두세요. 브라우저에 자동 저장됩니다.</div>
  <div class="cd">
    <div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:12px">
      <div style="flex:1;min-width:180px"><label style="font-size:12px;color:var(--tx3);display:block;margin-bottom:4px">발행자/섹터</label><input class="ti" id="rk" placeholder="예: HSBC"></div>
      <div style="flex:2;min-width:200px"><label style="font-size:12px;color:var(--tx3);display:block;margin-bottom:4px">제목</label><input class="ti" id="rt" placeholder="리서치 제목"></div></div>
    <label style="font-size:12px;color:var(--tx3);display:block;margin-bottom:4px">요약 / 메모</label>
    <textarea id="rc" rows="5" placeholder="핵심 내용을 메모..."></textarea>
    <div style="margin-top:12px;display:flex;gap:12px;align-items:center">
      <button class="bt pr" onclick="SM()">저장</button><span id="rs" style="font-size:12px;color:var(--tx3)"></span></div>
  </div>
  <div class="cd"><div class="ct2">🗂 저장된 리서치 메모</div><div id="rl"></div></div>
</div>
</div>
<div class="en" id="en">✅ CSV 파일이 다운로드됩니다</div>
<script>
// ═══ DATA ═══
const D = {data_json};

const CL = ['#3b82f6','#10b981','#ef4444','#8b5cf6','#f59e0b','#06b6d4','#ec4899','#14b8a6','#f97316','#6366f1','#84cc16','#e11d48','#0ea5e9','#a855f7','#22c55e'];

// Header
document.getElementById('hdr-sub').textContent = `G-spread | 3M | ${{D.generated}} | ${{D.bondCount}} bonds`;
document.getElementById('hdr-status').textContent = D.bonds[0]?.issuer?.includes?.('샘플') ? 'Sample Data' : 'Bloomberg Data (' + D.generated.split(' ')[0] + ')';

// Chart.js defaults
Chart.defaults.color='#8b98b0';Chart.defaults.borderColor='rgba(42,53,80,.4)';
Chart.defaults.font.family="'DM Sans',sans-serif";Chart.defaults.font.size=11;
Chart.defaults.plugins.legend.labels.usePointStyle=true;Chart.defaults.plugins.legend.labels.pointStyleWidth=10;
Chart.defaults.plugins.legend.labels.padding=12;Chart.defaults.animation.duration=600;

// ═══ Tab switching ═══
document.querySelectorAll('.tab').forEach(t=>t.addEventListener('click',()=>{{
  document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));
  document.querySelectorAll('.tp').forEach(x=>x.classList.remove('active'));
  t.classList.add('active');document.getElementById(t.dataset.tab).classList.add('active');
  if(t.dataset.tab==='t2')R2();if(t.dataset.tab==='t3')R3();
  if(t.dataset.tab==='t4')R4();if(t.dataset.tab==='t5')RL();
}}));

// ═══ TAB 1 ═══
const bonds=D.bonds;
let sel=new Set(bonds.slice(0,12).map(b=>b.label));
let sc='spread',sd=-1;
let ch1,ch2,ch3;

function GS(){{return bonds.filter(b=>sel.has(b.label))}}
function GF(){{
  const s=document.getElementById('fs').value,m=document.getElementById('fm').value,
        r=document.getElementById('fr').value,x=document.getElementById('fx').value.toLowerCase();
  return bonds.filter(b=>{{
    if(s!=='ALL'&&b.sector!==s)return false;
    if(m!=='ALL'&&b.matBucket!==m)return false;
    if(r!=='ALL'&&b.region!==r)return false;
    if(x&&!b.label.toLowerCase().includes(x)&&!(b.issuer||'').toLowerCase().includes(x))return false;
    return true;
  }});
}}
function SA(){{bonds.forEach(b=>sel.add(b.label));R1()}}
function SN(){{sel.clear();R1()}}
function ST(){{sel.clear();[...bonds].sort((a,b)=>(b.spread||0)-(a.spread||0)).slice(0,10).forEach(b=>sel.add(b.label));R1()}}
function SB(){{sel.clear();[...bonds].sort((a,b)=>(a.spread||0)-(b.spread||0)).slice(0,10).forEach(b=>sel.add(b.label));R1()}}
function AF(){{RT();UC()}}
function UC(){{
  const f=GF(),s2=f.filter(b=>sel.has(b.label));
  document.getElementById('fc').textContent=`선택: ${{sel.size}}개 / 필터: ${{f.length}}개 / 전체: ${{bonds.length}}개`;
}}

function RM(){{
  const s2=GS(),avg=s2.length?s2.reduce((a,b)=>a+(b.spread||0),0)/s2.length:0;
  const mx=s2.length?Math.max(...s2.map(b=>b.spread||0)):0;
  const mn=s2.length?Math.min(...s2.map(b=>b.spread||0)):0;
  const u10=D.ustCurve.now[8];
  const mk=(l,v)=>`<div class="mc"><div class="lb">${{l}}</div><div class="vl">${{v}}</div></div>`;
  document.getElementById('m1').innerHTML=[mk('모니터링 종목',bonds.length+'개'),mk('평균 G-spread',avg.toFixed(0)+' bps'),mk('최고 / 최저',mx+' / '+mn+' bps'),mk('UST 10Y',(u10||0).toFixed(2)+'%')].join('');
}}

function RS(){{
  const s2=GS();
  if(!s2.length){{document.getElementById('ss').innerHTML='📌 종목을 선택하면 평균 통계가 표시됩니다';return}}
  const avg=(a,f)=>a.reduce((s,b)=>s+(f(b)||0),0)/a.length;
  const fc=(v)=>v!=null?(v>=0?'+':'')+v.toFixed(1):'-';
  const cc=(v)=>v>0?'color:var(--rd)':v<0?'color:var(--gn)':'';
  const as=avg(s2,b=>b.spread).toFixed(0),ay=avg(s2,b=>b.ytm).toFixed(2);
  const a1=avg(s2,b=>b.chg1d),aw=avg(s2,b=>b.chg1w),am=avg(s2,b=>b.chg1m);
  document.getElementById('ss').innerHTML=`<span style="font-weight:600;color:var(--ac)">📌 선택 ${{s2.length}}개 평균</span>
    <span style="margin-left:16px"><span style="color:var(--tx3)">G-spread:</span> <b>${{as}} bps</b></span>
    <span style="margin-left:16px"><span style="color:var(--tx3)">YTM:</span> <b>${{ay}}%</b></span>
    <span style="margin-left:16px"><span style="color:var(--tx3)">1D:</span> <b style="${{cc(a1)}}">${{fc(a1)}}</b></span>
    <span style="margin-left:16px"><span style="color:var(--tx3)">1W:</span> <b style="${{cc(aw)}}">${{fc(aw)}}</b></span>
    <span style="margin-left:16px"><span style="color:var(--tx3)">1M:</span> <b style="${{cc(am)}}">${{fc(am)}}</b></span>`;
}}

function RC1(){{
  const s2=GS().slice(0,15);const ctx=document.getElementById('c1').getContext('2d');
  if(ch1)ch1.destroy();
  const H=D.spreadHistory;
  ch1=new Chart(ctx,{{type:'line',data:{{labels:H.dates,datasets:s2.map((b,i)=>({{
    label:b.label.length>25?b.label.slice(0,22)+'...':b.label,
    data:H.series[b.label]||[],borderColor:CL[i%CL.length],borderWidth:1.5,pointRadius:0,pointHitRadius:8,tension:.3
  }}))
  }},options:{{responsive:true,maintainAspectRatio:false,interaction:{{mode:'index',intersect:false}},
    plugins:{{legend:{{display:true,position:'top'}},tooltip:{{callbacks:{{label:c=>c.dataset.label+': '+c.parsed.y?.toFixed(0)+' bps'}}}}}},
    scales:{{x:{{ticks:{{maxTicksLimit:10,font:{{size:10}}}},grid:{{display:false}}}},y:{{title:{{display:true,text:'bps'}},grid:{{color:'rgba(42,53,80,.3)'}}}}}}
  }}}});
}}

function RC2(){{
  const s2=GS();const ctx=document.getElementById('c2').getContext('2d');if(ch2)ch2.destroy();
  const U=D.ustCurve;
  const ds=[
    {{label:'UST 현재',data:U.years.map((x,i)=>({{x,y:U.now[i]}})),borderColor:'#3b82f6',borderWidth:2.5,pointRadius:4,pointBackgroundColor:'#3b82f6',showLine:true,tension:.3}},
    {{label:'UST 3M전',data:U.years.map((x,i)=>({{x,y:U.ago3m[i]}})),borderColor:'#6b7280',borderWidth:1.5,borderDash:[5,3],pointRadius:3,pointBackgroundColor:'#6b7280',showLine:true,tension:.3}},
  ];
  if(s2.length){{
    const rc=r=>{{if(!r)return'#6b7280';if(r.startsWith('AA'))return'#10b981';if(r.startsWith('A'))return'#f59e0b';return'#ef4444'}};
    ds.push({{label:'선택 채권',data:s2.filter(b=>b.duration&&b.ytm).map(b=>({{x:b.duration,y:b.ytm}})),backgroundColor:s2.map(b=>rc(b.rating)),pointRadius:7,pointHoverRadius:9,showLine:false}});
  }}
  ch2=new Chart(ctx,{{type:'scatter',data:{{datasets:ds}},options:{{responsive:true,maintainAspectRatio:false,
    scales:{{x:{{title:{{display:true,text:'듀레이션(년)'}},min:0,max:32,grid:{{color:'rgba(42,53,80,.3)'}}}},y:{{title:{{display:true,text:'수익률(%)'}},grid:{{color:'rgba(42,53,80,.3)'}}}}}}
  }}}});
}}

function RC3(){{
  const s2=GS().sort((a,b)=>(a.spread||0)-(b.spread||0)).slice(0,20);
  const ctx=document.getElementById('c3').getContext('2d');if(ch3)ch3.destroy();
  ch3=new Chart(ctx,{{type:'bar',data:{{labels:s2.map(b=>b.label.length>20?b.label.slice(0,18)+'...':b.label),
    datasets:[{{data:s2.map(b=>b.spread),backgroundColor:s2.map((_,i)=>CL[i%CL.length]),borderRadius:3}}]}},
    options:{{indexAxis:'y',responsive:true,maintainAspectRatio:false,
      plugins:{{legend:{{display:false}},tooltip:{{callbacks:{{label:c=>c.parsed.x+' bps'}}}}}},
      scales:{{x:{{title:{{display:true,text:'G-spread (bps)'}},grid:{{color:'rgba(42,53,80,.3)'}}}},y:{{ticks:{{font:{{size:10}}}},grid:{{display:false}}}}}}
    }}
  }});
}}

const TC=[
  {{id:'label',n:'채권명',t:'t'}},{{id:'ytm',n:'YTM(%)',t:'n',f:v=>v?.toFixed(2)}},
  {{id:'spread',n:'G-spread',t:'n',f:v=>v?.toFixed(0),c:'sp'}},
  {{id:'chg1d',n:'1D',t:'c',f:v=>v!=null?(v>=0?'+':'')+v.toFixed(1):'-'}},
  {{id:'chg1w',n:'1W',t:'c',f:v=>v!=null?(v>=0?'+':'')+v.toFixed(1):'-'}},
  {{id:'chg1m',n:'1M',t:'c',f:v=>v!=null?(v>=0?'+':'')+v.toFixed(1):'-'}},
  {{id:'chg3m',n:'3M',t:'c',f:v=>v!=null?(v>=0?'+':'')+v.toFixed(1):'-'}},
  {{id:'duration',n:'듀레이션',t:'n',f:v=>v?.toFixed(1)}},
  {{id:'matBucket',n:'만기',t:'t'}},{{id:'sector',n:'섹터',t:'t'}},
  {{id:'issuer',n:'발행자',t:'t'}},{{id:'rating',n:'등급',t:'t'}},{{id:'region',n:'지역',t:'t'}},
];
function RH(){{
  document.getElementById('th').innerHTML=TC.map(c=>{{
    const a=sc===c.id?(sd>0?'▲':'▼'):'';
    return`<th class="${{sc===c.id?'so':''}}" onclick="SO('${{c.id}}')">${{c.n}} <span style="font-size:10px">${{a}}</span></th>`;
  }}).join('');
}}
function SO(c){{if(sc===c)sd*=-1;else{{sc=c;sd=-1}}RT()}}
function RT(){{
  RH();let d=GF();
  d.sort((a,b)=>{{const av=a[sc],bv=b[sc];if(av==null)return 1;if(bv==null)return -1;
    if(typeof av==='string')return av.localeCompare(bv)*sd;return(av-bv)*sd}});
  document.getElementById('tb').innerHTML=d.map(b=>{{
    const rs=sel.has(b.label)?'background:rgba(59,130,246,.06)':'';
    return`<tr style="${{rs}}" onclick="TB('${{b.label.replace(/'/g,"\\\\'")}}')">${{TC.map(c=>{{
      let v=b[c.id],cls=c.t==='t'?'tc':'nm';if(c.c)cls+=' '+c.c;
      if(c.t==='c'&&v!=null)cls+=v>0?' ps':v<0?' ng':'';
      return`<td class="${{cls}}">${{c.f?c.f(v):(v??'-')}}</td>`;
    }}).join('')}}</tr>`;
  }}).join('');UC();
}}
function TB(l){{if(sel.has(l))sel.delete(l);else sel.add(l);R1()}}

function PF(){{
  const ss=[...new Set(bonds.map(b=>b.sector).filter(Boolean))].sort();
  const ms=[...new Set(bonds.map(b=>b.matBucket).filter(Boolean))];
  const rs=[...new Set(bonds.map(b=>b.region).filter(Boolean))].sort();
  ss.forEach(s=>{{const o=document.createElement('option');o.value=s;o.textContent=s;document.getElementById('fs').appendChild(o)}});
  ms.forEach(m=>{{const o=document.createElement('option');o.value=m;o.textContent=m;document.getElementById('fm').appendChild(o)}});
  rs.forEach(r=>{{const o=document.createElement('option');o.value=r;o.textContent=r;document.getElementById('fr').appendChild(o)}});
}}
function R1(){{RM();RS();RC1();RC2();RC3();RT()}}

// ═══ TAB 2 ═══
let t2r=false;
function R2(){{
  if(t2r)return;t2r=true;
  const mc=D.marketCurrent,mg=D.marketChanges,mt=D.marketTimeseries;
  const names=Object.keys(mc);
  const fc=(v)=>v!=null?(+v>=0?'+':'')+Number(v).toFixed(1):'-';
  const cc=(v)=>v!=null?(+v>0?'up':+v<0?'dn':''):'';
  document.getElementById('m2').innerHTML=names.map(n=>{{
    const v=mc[n],g=mg[n]||{{}};const u=n.includes('VIX')||n.includes('MOVE')?'':' bps';
    return`<div class="mc"><div class="lb">${{n}}</div><div class="vl">${{v!=null?v.toFixed(1)+u:'-'}}</div>
      <div style="display:flex;gap:10px;font-size:11px;margin-top:6px;font-family:var(--mn)">
        <span class="dl ${{cc(g['1d'])}}">1D:${{fc(g['1d'])}}</span>
        <span class="dl ${{cc(g['1w'])}}">1W:${{fc(g['1w'])}}</span>
        <span class="dl ${{cc(g['1m'])}}">1M:${{fc(g['1m'])}}</span></div></div>`;
  }}).join('');

  const cnames=['US IG OAS','US HY OAS','CDX IG 5Y','CDX HY 5Y'];
  const mkds=(n)=>{{const ts=mt[n];if(!ts)return{{dates:[],vals:[]}};const k=Object.keys(ts).sort();return{{dates:k,vals:k.map(d=>ts[d])}}}};
  const cd0=mkds(cnames[0]);
  new Chart(document.getElementById('c4').getContext('2d'),{{type:'line',
    data:{{labels:cd0.dates,datasets:cnames.map((n,i)=>{{const d=mkds(n);return{{label:n,data:d.vals,borderColor:CL[i],borderWidth:1.8,pointRadius:0,tension:.3,yAxisID:n.includes('HY')?'y1':'y'}}}})
    }},options:{{responsive:true,maintainAspectRatio:false,interaction:{{mode:'index',intersect:false}},
      scales:{{x:{{ticks:{{maxTicksLimit:8}},grid:{{display:false}}}},y:{{title:{{display:true,text:'IG (bps)'}},position:'left',grid:{{color:'rgba(42,53,80,.3)'}}}},y1:{{title:{{display:true,text:'HY (bps)'}},position:'right',grid:{{display:false}}}}}}
    }}
  }});

  const vd=mkds('VIX');
  new Chart(document.getElementById('c5').getContext('2d'),{{type:'line',
    data:{{labels:vd.dates,datasets:[{{label:'VIX',data:vd.vals,borderColor:'#ef4444',borderWidth:1.5,pointRadius:0,tension:.3,fill:{{target:'origin',above:'rgba(239,68,68,.08)'}}}}]}},
    options:{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{display:false}}}},scales:{{x:{{ticks:{{maxTicksLimit:6}},grid:{{display:false}}}},y:{{grid:{{color:'rgba(42,53,80,.3)'}}}}}}}}
  }});

  const md=mkds('MOVE');
  new Chart(document.getElementById('c6').getContext('2d'),{{type:'line',
    data:{{labels:md.dates,datasets:[{{label:'MOVE',data:md.vals,borderColor:'#f59e0b',borderWidth:1.5,pointRadius:0,tension:.3,fill:{{target:'origin',above:'rgba(245,158,11,.08)'}}}}]}},
    options:{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{display:false}}}},scales:{{x:{{ticks:{{maxTicksLimit:6}},grid:{{display:false}}}},y:{{grid:{{color:'rgba(42,53,80,.3)'}}}}}}}}
  }});
}}

// ═══ TAB 3: Issuer Curves (fixed) ═══
let t3r=false;
function R3(){{
  if(t3r)return;t3r=true;
  const all=D.allBonds;

  // Group by issuer
  const byIssuer={{}};
  all.forEach(b=>{{if(b.issuer&&b.duration&&b.ytm){{(byIssuer[b.issuer]=byIssuer[b.issuer]||[]).push(b)}} }});

  // Top issuers by bond count (min 3 bonds)
  const top=Object.entries(byIssuer).filter(([,bs])=>bs.length>=3).sort((a,b)=>b[1].length-a[1].length).slice(0,10);

  const container=document.getElementById('ic');
  container.innerHTML=top.map(([issuer,bs],idx)=>{{
    const owned=bs.filter(b=>b.is_owned).length;
    const extra=bs.filter(b=>!b.is_owned).length;
    const sub=`보유 ${{owned}}개`+(extra>0?` + 비보유 ${{extra}}개`:'');
    return`<div class="ic">
      <div class="it">${{issuer}}</div>
      <div class="is">${{sub}}</div>
      <div class="ch" style="height:220px"><canvas id="ic${{idx}}"></canvas></div></div>`;
  }}).join('');

  top.forEach(([issuer,bs],idx)=>{{
    const color=CL[idx%CL.length];
    const owned=bs.filter(b=>b.is_owned).sort((a,b)=>a.duration-b.duration);
    const extra=bs.filter(b=>!b.is_owned).sort((a,b)=>a.duration-b.duration);

    const datasets=[];

    // Owned: filled markers + line
    if(owned.length){{
      datasets.push({{
        label:'보유',data:owned.map(b=>({{x:b.duration,y:b.ytm}})),
        borderColor:color,backgroundColor:color,
        pointRadius:6,pointHoverRadius:8,pointStyle:'circle',
        showLine:true,tension:.3,borderWidth:2,
      }});
    }}

    // Extra: open circle markers, no line
    if(extra.length){{
      datasets.push({{
        label:'비보유',data:extra.map(b=>({{x:b.duration,y:b.ytm}})),
        borderColor:color,backgroundColor:'transparent',
        pointRadius:6,pointHoverRadius:8,pointStyle:'circle',
        pointBorderWidth:2,pointBorderColor:color,
        showLine:false,borderWidth:0,
      }});
    }}

    const allSorted=[...bs].sort((a,b)=>a.duration-b.duration);
    const ctx=document.getElementById('ic'+idx).getContext('2d');
    new Chart(ctx,{{type:'scatter',data:{{datasets}},
      options:{{responsive:true,maintainAspectRatio:false,
        plugins:{{
          legend:{{display:true,position:'top',labels:{{font:{{size:10}},padding:8}}}},
          tooltip:{{callbacks:{{
            label:function(c){{
              const b=c.datasetIndex===0?owned[c.dataIndex]:extra[c.dataIndex];
              return b?`${{b.label}}: ${{c.parsed.x.toFixed(1)}}Y | ${{c.parsed.y.toFixed(2)}}%`:'';
            }}
          }}}}
        }},
        scales:{{
          x:{{title:{{display:true,text:'듀레이션(년)',font:{{size:10}}}},
              grid:{{color:'rgba(42,53,80,.2)'}},
              ticks:{{callback:v=>[1,2,3,5,7,10,15,20,30].includes(Math.round(v))?Math.round(v):'',font:{{size:10}}}}}},
          y:{{title:{{display:true,text:'YTM(%)',font:{{size:10}}}},
              ticks:{{callback:v=>v.toFixed(1)+'%',font:{{size:10}}}},
              grid:{{color:'rgba(42,53,80,.2)'}}}}
        }}
      }}
    }});
  }});

  // Sector average curve
  const bySector={{}};
  all.forEach(b=>{{if(b.sector&&b.duration&&b.ytm)(bySector[b.sector]=bySector[b.sector]||[]).push(b)}});
  const sds=Object.entries(bySector).filter(([,bs])=>bs.length>=3).map(([sec,bs],i)=>{{
    const bk={{}};bs.forEach(b=>{{const k=Math.round(b.duration);(bk[k]=bk[k]||[]).push(b.ytm)}});
    const pts=Object.entries(bk).map(([d,ys])=>({{x:+d,y:ys.reduce((a,v)=>a+v,0)/ys.length}})).sort((a,b)=>a.x-b.x);
    return{{label:sec,data:pts,borderColor:CL[i%CL.length],borderWidth:2,pointRadius:4,showLine:true,tension:.3}};
  }});
  new Chart(document.getElementById('c7').getContext('2d'),{{type:'scatter',data:{{datasets:sds}},
    options:{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{position:'top'}}}},
      scales:{{x:{{title:{{display:true,text:'듀레이션(년)'}},grid:{{color:'rgba(42,53,80,.3)'}}}},y:{{title:{{display:true,text:'YTM(%)'}},grid:{{color:'rgba(42,53,80,.3)'}}}}}}
    }}
  }});
}}

// ═══ TAB 4 ═══
let t4r=false;
function R4(){{
  if(t4r)return;t4r=true;
  const tr=D.trades||[];
  document.getElementById('t4b').innerHTML=tr.map(t=>
    `<tr><td class="tc">${{t.date}}</td><td class="tc">${{t.bond}}</td><td class="nm">${{t.price??'-'}}</td><td class="nm">${{t.ytm??'-'}}</td><td class="nm">${{t.volume?t.volume.toLocaleString():'-'}}</td><td class="nm">${{t.volume?(t.volume/1e6).toFixed(1)+'M':'-'}}</td></tr>`
  ).join('');
}}

// ═══ TAB 5 ═══
function GM(){{try{{return JSON.parse(localStorage.getItem('credit_memos')||'[]')}}catch{{return[]}}}}
function WM(a){{localStorage.setItem('credit_memos',JSON.stringify(a))}}
function SM(){{
  const k=document.getElementById('rk').value.trim(),t=document.getElementById('rt').value.trim(),c=document.getElementById('rc').value.trim();
  if(!k||!t||!c){{document.getElementById('rs').textContent='⚠️ 모든 필드를 입력해주세요';return}}
  const ms=GM();ms.push({{key:k,title:t,content:c,date:new Date().toISOString().split('T')[0],saved:new Date().toLocaleString('ko-KR')}});
  WM(ms);document.getElementById('rk').value='';document.getElementById('rt').value='';document.getElementById('rc').value='';
  document.getElementById('rs').textContent='✅ 저장됨';RL();
}}
function DM(i){{const ms=GM();ms.splice(i,1);WM(ms);RL()}}
function RL(){{
  const ms=GM();
  if(!ms.length){{document.getElementById('rl').innerHTML='<div style="text-align:center;padding:20px;color:var(--tx3);font-size:13px">저장된 메모가 없어요</div>';return}}
  document.getElementById('rl').innerHTML=[...ms].reverse().map((m,ri)=>{{
    const i=ms.length-1-ri;
    return`<div class="mi"><div class="mh"><div><span class="mt">${{m.key}}</span><span class="mtl">${{m.title}}</span></div>
      <div style="display:flex;align-items:center;gap:10px"><span class="md">${{m.date}}</span>
        <button class="bt" style="padding:2px 8px;font-size:11px" onclick="DM(${{i}})">삭제</button></div></div>
      <div class="mb">${{m.content}}</div></div>`;
  }}).join('');
}}

// ═══ CSV Export ═══
function EX(){{
  const hdr=TC.map(c=>c.n).join(',');
  const rows=bonds.map(b=>TC.map(c=>{{let v=b[c.id];if(typeof v==='string'&&v.includes(','))return`"${{v}}"`;return v??''}}).join(','));
  const csv='\\uFEFF'+hdr+'\\n'+rows.join('\\n');
  const blob=new Blob([csv],{{type:'text/csv;charset=utf-8'}});
  const u=URL.createObjectURL(blob);const a=document.createElement('a');
  a.href=u;a.download=`ig_credit_${{D.generated.split(' ')[0]}}.csv`;a.click();URL.revokeObjectURL(u);
  const e=document.getElementById('en');e.classList.add('show');setTimeout(()=>e.classList.remove('show'),2000);
}}

// ═══ INIT ═══
PF();R1();
</script>
</body>
</html>'''
    return html


# ══════════════════════════════════════════════
# 6. GitHub Pages 자동 배포
# ══════════════════════════════════════════════
def deploy_to_github(html_content, repo_dir=None):
    """
    GitHub Pages 저장소에 index.html을 업데이트하고 push.
    repo_dir: git 저장소 경로. None이면 스크립트와 같은 폴더 사용.
    """
    import subprocess

    if repo_dir is None:
        repo_dir = os.path.dirname(os.path.abspath(__file__))

    index_path = os.path.join(repo_dir, "index.html")

    # index.html 저장 (항상 같은 파일 → 같은 URL)
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    today_str = datetime.today().strftime("%Y-%m-%d %H:%M")

    try:
        def run(cmd):
            r = subprocess.run(cmd, cwd=repo_dir, capture_output=True, text=True, timeout=30)
            if r.returncode != 0:
                print(f"  ⚠️  {' '.join(cmd)}: {r.stderr.strip()}")
            return r.returncode == 0

        # git 상태 확인
        result = subprocess.run(["git", "status"], cwd=repo_dir, capture_output=True, text=True)
        if result.returncode != 0:
            print("\n  ⚠️  이 폴더가 git 저장소가 아닙니다.")
            print("  아래 초기 설정을 먼저 진행해주세요:\n")
            print_setup_guide()
            return False

        run(["git", "add", "index.html"])
        run(["git", "commit", "-m", f"Update dashboard {today_str}"])

        print("\n  📤 GitHub에 push 중...")
        if run(["git", "push"]):
            print("  ✅ GitHub Pages 배포 완료!")
            # remote URL에서 페이지 주소 추출
            r = subprocess.run(["git", "remote", "get-url", "origin"],
                               cwd=repo_dir, capture_output=True, text=True)
            if r.returncode == 0:
                url = r.stdout.strip()
                # git@github.com:user/repo.git → user/repo
                # https://github.com/user/repo.git → user/repo
                if "github.com" in url:
                    parts = url.replace(".git","").split("github.com")[-1]
                    parts = parts.lstrip(":/")
                    user, repo = parts.split("/")[:2]
                    print(f"\n  🌐 https://{user}.github.io/{repo}/")
            return True
        else:
            print("  ⚠️  push 실패. 네트워크 또는 인증을 확인해주세요.")
            return False

    except FileNotFoundError:
        print("\n  ⚠️  git이 설치되어 있지 않습니다. git을 먼저 설치해주세요.")
        return False
    except Exception as e:
        print(f"\n  ⚠️  배포 오류: {e}")
        return False


def print_setup_guide():
    """초기 GitHub Pages 설정 가이드 출력"""
    print("""
  ┌─────────────────────────────────────────────────┐
  │          GitHub Pages 초기 설정 가이드           │
  ├─────────────────────────────────────────────────┤
  │                                                 │
  │  1. GitHub에서 새 저장소 만들기                  │
  │     → https://github.com/new                    │
  │     → 이름: credit-dashboard                    │
  │     → Public 선택                               │
  │                                                 │
  │  2. 이 폴더에서 git 초기화                      │
  │     cd [이 스크립트가 있는 폴더]                 │
  │     git init                                    │
  │     git remote add origin                       │
  │       https://github.com/[유저명]/credit-dashboard.git │
  │     git branch -M main                          │
  │     git add .                                   │
  │     git commit -m "Initial commit"              │
  │     git push -u origin main                     │
  │                                                 │
  │  3. GitHub Pages 활성화                         │
  │     → 저장소 Settings → Pages                   │
  │     → Source: Deploy from a branch              │
  │     → Branch: main / (root)                     │
  │     → Save                                      │
  │                                                 │
  │  4. 1-2분 후 접속 가능:                         │
  │     https://[유저명].github.io/credit-dashboard/ │
  │                                                 │
  │  5. 이후 이 스크립트만 실행하면 자동 업데이트   │
  │     python export_credit_dashboard.py           │
  │                                                 │
  └─────────────────────────────────────────────────┘
""")


# ══════════════════════════════════════════════
# 7. 메인 실행
# ══════════════════════════════════════════════
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Credit Dashboard → HTML + GitHub Pages 배포")
    parser.add_argument("--no-deploy", action="store_true", help="HTML만 생성, GitHub push 안 함")
    parser.add_argument("--setup", action="store_true", help="초기 설정 가이드 출력")
    parser.add_argument("--sample", action="store_true", help="샘플 데이터로 생성 (Bloomberg 무시)")
    args = parser.parse_args()

    if args.setup:
        print_setup_guide()
        exit(0)

    print("\n" + "=" * 60)
    print("  Credit Dashboard → HTML 생성기 + GitHub Pages 배포")
    print("=" * 60)

    # Bloomberg 연결 시도
    if args.sample:
        print("\n📊 샘플 데이터 모드")
        data = load_sample_data()
    else:
        try:
            from xbbg import blp
            print("\n✅ Bloomberg 연결 확인됨 → 실제 데이터 로드")
            data = load_bloomberg_data()
        except ImportError:
            print("\n⚠️  xbbg 미설치 → 샘플 데이터로 생성")
            data = load_sample_data()
        except Exception as e:
            print(f"\n⚠️  Bloomberg 연결 실패 ({e}) → 샘플 데이터로 생성")
            data = load_sample_data()

    # HTML 생성
    html_content = generate_html(data)

    # index.html로 저장 (GitHub Pages용 고정 파일명)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    index_path = os.path.join(script_dir, "index.html")
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    # 날짜별 백업도 저장
    backup_name = f"credit_dashboard_{datetime.today().strftime('%Y%m%d')}.html"
    backup_path = os.path.join(script_dir, backup_name)
    with open(backup_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    # JSON 데이터 백업
    json_name = f"credit_data_{datetime.today().strftime('%Y%m%d')}.json"
    json_path = os.path.join(script_dir, json_name)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    size_kb = os.path.getsize(index_path) / 1024
    print(f"\n  📄 index.html 생성 완료 ({size_kb:.0f} KB)")
    print(f"  📄 {backup_name} (날짜별 백업)")
    print(f"  📊 {json_name} (데이터 백업)")

    # GitHub Pages 배포
    if args.no_deploy:
        print(f"\n  --no-deploy 모드: GitHub push를 건너뜁니다.")
    else:
        deploy_to_github(html_content, script_dir)

    print(f"\n{'=' * 60}\n")
