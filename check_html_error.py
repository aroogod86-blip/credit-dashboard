"""
credit-dashboard 폴더에서 실행:
  python check_html_error.py

index.html 의 742번 줄 근처를 출력하고
JS 문법 오류가 있는 script 블록을 찾습니다.
"""
import re, os

html_path = "index.html"
if not os.path.exists(html_path):
    print("index.html 없음 — credit-dashboard 폴더에서 실행하세요")
    exit()

lines = open(html_path, "r", encoding="utf-8").readlines()
print(f"총 줄 수: {len(lines)}")

# 742번 줄 근처 출력
target = 742
s = max(0, target - 5)
e = min(len(lines), target + 5)
print(f"\n=== {s+1}~{e}번 줄 ===")
for i in range(s, e):
    marker = ">>>" if i == target - 1 else "   "
    print(f"{marker} {i+1}: {lines[i].rstrip()[:150]}")

# 전체 script 블록의 중괄호/괄호 균형 검사
print("\n=== JS 균형 검사 ===")
full = "".join(lines)
scripts = re.findall(r'<script[^>]*>(.*?)</script>', full, re.DOTALL)
print(f"script 블록 수: {len(scripts)}")
for i, sc in enumerate(scripts):
    opens  = sc.count('{'); closes = sc.count('}')
    po = sc.count('(');    pc = sc.count(')')
    brace_ok = "OK" if opens == closes else f"ERROR 차이:{opens-closes}"
    paren_ok  = "OK" if po == pc      else f"ERROR 차이:{po-pc}"
    print(f"  Script #{i+1} ({len(sc.splitlines())}줄): 중괄호 {opens}/{closes} {brace_ok} | 괄호 {po}/{pc} {paren_ok}")
