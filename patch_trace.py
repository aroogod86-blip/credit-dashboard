"""
TRACE 거래 데이터 로딩 무한 대기 문제 패치
실행: python patch_trace.py
결과: export_credit_dashboard.py 직접 수정 (백업: export_credit_dashboard_backup.py)
"""
import sys, os, shutil

TARGET = "export_credit_dashboard.py"
BACKUP = "export_credit_dashboard_backup.py"

if not os.path.exists(TARGET):
    print(f"ERROR: {TARGET} 없음 — credit-dashboard 폴더에서 실행하세요")
    sys.exit(1)

# 백업
shutil.copy2(TARGET, BACKUP)
print(f"백업 완료: {BACKUP}")

txt = open(TARGET, "r", encoding="utf-8").read()

# ── 교체 대상 (750~794줄) ────────────────────────────────────────────────
OLD = """    # ─── 거래 데이터 - TRACE (최근 7일) ───
    print("\\n[6/8] TRACE 거래 데이터...")
    trades = []
    for tk in tickers:
        try:
            # TRACE 거래 내역: TRAC <GO> 에서 제공하는 필드
            r = blp.bdh([tk],
                        ["TRACE_DLR_LAST_PX", "TRACE_DLR_LAST_YLD", "TRACE_DLR_LAST_SPREAD",
                         "TRACE_DLR_LAST_VOL", "TRACE_DLR_RPT_PARTY"],
                        (datetime.today() - timedelta(days=7)).strftime("%Y%m%d"),
                        datetime.today().strftime("%Y%m%d"))
            tpdf = _nw(r)
            if len(tpdf) == 0:
                continue
            df = tpdf.pivot_table(index="date", columns="field", values="value", aggfunc="last").reset_index()
            lbl = snap.loc[tk, "label"] if tk in snap.index else tk
            yas_spread = pd.to_numeric(snap.loc[tk, "spread"], errors="coerce") if tk in snap.index else None

            for _, row in df.iterrows():
                px = pd.to_numeric(row.get("TRACE_DLR_LAST_PX"), errors="coerce")
                ytm_val = pd.to_numeric(row.get("TRACE_DLR_LAST_YLD"), errors="coerce")
                trd_spread = pd.to_numeric(row.get("TRACE_DLR_LAST_SPREAD"), errors="coerce")
                vol = pd.to_numeric(row.get("TRACE_DLR_LAST_VOL"), errors="coerce")
                source = row.get("TRACE_DLR_RPT_PARTY")

                # 거래 스프레드 vs 전일 YAS spread
                spread_chg = None
                if pd.notna(trd_spread) and pd.notna(yas_spread):
                    spread_chg = round(float(trd_spread - yas_spread), 1)

                trades.append({
                    "date": pd.to_datetime(row["date"]).strftime("%Y-%m-%d"),
                    "bond": lbl,
                    "price": round(float(px), 3) if pd.notna(px) else None,
                    "ytm": round(float(ytm_val), 3) if pd.notna(ytm_val) else None,
                    "trd_spread": round(float(trd_spread), 1) if pd.notna(trd_spread) else None,
                    "yas_spread": round(float(yas_spread), 1) if pd.notna(yas_spread) else None,
                    "spread_chg": spread_chg,
                    "volume": int(vol) if pd.notna(vol) and vol > 0 else None,
                    "source": str(source).strip() if pd.notna(source) and str(source).strip() not in ("nan","None","") else None,
                })
        except:
            pass
    trades.sort(key=lambda x: x["date"], reverse=True)
    print(f"  → {len(trades)}건 TRACE 거래 로드")"""

NEW = """    # ─── 거래 데이터 - TRACE (최근 7일) ───
    # --trace 플래그 없으면 스킵 (Bloomberg TRACE 라이선스 없을 때 무한 대기 방지)
    LOAD_TRACE = "--trace" in sys.argv
    print(f"\\n[6/8] TRACE 거래 데이터{'...' if LOAD_TRACE else ' (스킵 — --trace 플래그로 활성화)'}")
    trades = []

    if LOAD_TRACE:
        import threading
        for tk in tickers:
            try:
                result = [None]

                def _fetch(ticker=tk):
                    try:
                        r = blp.bdh(
                            [ticker],
                            ["TRACE_DLR_LAST_PX", "TRACE_DLR_LAST_YLD",
                             "TRACE_DLR_LAST_SPREAD", "TRACE_DLR_LAST_VOL",
                             "TRACE_DLR_RPT_PARTY"],
                            (datetime.today() - timedelta(days=7)).strftime("%Y%m%d"),
                            datetime.today().strftime("%Y%m%d")
                        )
                        result[0] = r
                    except Exception:
                        pass

                t = threading.Thread(target=_fetch, daemon=True)
                t.start()
                t.join(timeout=8)  # 8초 타임아웃

                if result[0] is None:
                    print(f"  TRACE 스킵: {tk} (타임아웃)")
                    continue

                tpdf = _nw(result[0])
                if len(tpdf) == 0:
                    continue

                df = tpdf.pivot_table(index="date", columns="field", values="value", aggfunc="last").reset_index()
                lbl = snap.loc[tk, "label"] if tk in snap.index else tk
                yas_spread = pd.to_numeric(snap.loc[tk, "spread"], errors="coerce") if tk in snap.index else None

                for _, row in df.iterrows():
                    px         = pd.to_numeric(row.get("TRACE_DLR_LAST_PX"),     errors="coerce")
                    ytm_val    = pd.to_numeric(row.get("TRACE_DLR_LAST_YLD"),    errors="coerce")
                    trd_spread = pd.to_numeric(row.get("TRACE_DLR_LAST_SPREAD"), errors="coerce")
                    vol        = pd.to_numeric(row.get("TRACE_DLR_LAST_VOL"),    errors="coerce")
                    source     = row.get("TRACE_DLR_RPT_PARTY")
                    spread_chg = None
                    if pd.notna(trd_spread) and pd.notna(yas_spread):
                        spread_chg = round(float(trd_spread - yas_spread), 1)
                    trades.append({
                        "date":       pd.to_datetime(row["date"]).strftime("%Y-%m-%d"),
                        "bond":       lbl,
                        "price":      round(float(px), 3) if pd.notna(px) else None,
                        "ytm":        round(float(ytm_val), 3) if pd.notna(ytm_val) else None,
                        "trd_spread": round(float(trd_spread), 1) if pd.notna(trd_spread) else None,
                        "yas_spread": round(float(yas_spread), 1) if pd.notna(yas_spread) else None,
                        "spread_chg": spread_chg,
                        "volume":     int(vol) if pd.notna(vol) and vol > 0 else None,
                        "source":     str(source).strip() if pd.notna(source) and str(source).strip() not in ("nan","None","") else None,
                    })
            except Exception as e:
                print(f"  TRACE 오류 {tk}: {e}")
                continue
        trades.sort(key=lambda x: x["date"], reverse=True)
        print(f"  → {len(trades)}건 TRACE 거래 로드")
    else:
        print("  → 거래 탭은 빈 상태로 생성됩니다")"""

# sys import 확인 및 추가
if "import sys" not in txt:
    txt = txt.replace("import os\n", "import os\nimport sys\n", 1)
    if "import sys" not in txt:  # 위 교체 안 됐으면 맨 앞에 추가
        txt = "import sys\n" + txt
    print("import sys 추가 완료")

# 교체 실행
if OLD in txt:
    txt = txt.replace(OLD, NEW, 1)
    open(TARGET, "w", encoding="utf-8").write(txt)
    print("✅ 패치 완료!")
    print()
    print("사용법:")
    print("  일반 실행 (TRACE 스킵, 빠름):")
    print("    python export_credit_dashboard.py")
    print()
    print("  TRACE 포함 실행 (느림, Bloomberg TRACE 라이선스 필요):")
    print("    python export_credit_dashboard.py --trace")
else:
    print("❌ 교체 대상 코드를 찾지 못했습니다.")
    print("   파일이 이미 패치됐거나 버전이 다를 수 있습니다.")
    print()
    # 부분 매칭으로 위치 힌트
    if 'TRACE 거래 데이터...' in txt:
        lines = txt.split('\n')
        for i, l in enumerate(lines):
            if 'TRACE 거래 데이터' in l:
                print(f"  관련 코드 발견: {i+1}번 줄 → {l.strip()}")
    # 백업 복원
    shutil.copy2(BACKUP, TARGET)
    print("  원본 복원 완료 (변경 없음)")
    sys.exit(1)
