import re

with open('index.html', 'r', encoding='utf-8') as f:
    html = f.read()

scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
print(f"script 블록 수: {len(scripts)}")

# 흔한 JS 문법 오류 패턴 검사
problems = [
    (r"'\s*\n\s*\+\s*'",         "문자열 연결 줄바꿈"),
    (r'var \w+ = [^;]*\n[^;]*;', "변수 선언 줄바꿈"),
    (r"\)\s*'",                   "닫는 괄호 뒤 따옴표"),
    (r"'[^']*\n[^']*'",          "따옴표 안에 줄바꿈"),
]

for i, sc in enumerate(scripts):
    if not sc.strip():
        continue
    lines = sc.split('\n')
    print(f"\n=== Script #{i+1} ({len(lines)}줄) ===")

    # 따옴표 균형 체크 - 홀수면 문제
    sq = sc.count("'") - sc.count("\\'")
    dq = sc.count('"') - sc.count('\\"')
    print(f"  홑따옴표 수: {sq} ({'짝수 OK' if sq%2==0 else '홀수 ERROR'})")
    print(f"  쌍따옴표 수: {dq} ({'짝수 OK' if dq%2==0 else '홀수 ERROR'})")

    # 괄호 균형
    opens  = sc.count('(') - sc.count('\\(')
    closes = sc.count(')') - sc.count('\\)')
    print(f"  괄호 열림/닫힘: {opens}/{closes} ({'OK' if opens==closes else 'ERROR - 차이: '+str(opens-closes)})")

    # 중괄호 균형
    co = sc.count('{'); cc = sc.count('}')
    print(f"  중괄호 열림/닫힘: {co}/{cc} ({'OK' if co==cc else 'ERROR'})")

    # 의심 라인 출력
    for j, line in enumerate(lines):
        stripped = line.strip()
        # 닫는 따옴표 없이 끝나는 문자열
        if stripped.endswith("'") and stripped.count("'") % 2 == 0 and len(stripped) > 20:
            pass
        # 갑자기 따옴표로 시작하는 라인 (이전 줄과 연결 끊김)
        if stripped.startswith("'") and j > 0:
            prev = lines[j-1].strip()
            if not prev.endswith(('+', ',', '(', '=')):
                print(f"  [의심 {j+1}줄] 이전줄 연결 없이 따옴표 시작: {line[:100]}")
        # 예상치 못한 토큰 패턴
        if re.search(r"\)\s*'[^+,;)\]]", stripped):
            print(f"  [의심 {j+1}줄] 닫는괄호 후 따옴표: {line[:100]}")

print("\n--- 오류 위치 후보 (전체 HTML 기준) ---")
# 오류 메시지의 741자 근처 확인
pos = 741
# script 태그 시작 위치 찾기
for m in re.finditer(r'<script[^>]*>', html):
    sc_start = m.end()
    sc_content_end = html.find('</script>', sc_start)
    if sc_start <= pos + sc_start:
        # 해당 스크립트 내 상대 위치
        pass

# index.html의 741번째 줄 출력
all_lines = html.split('\n')
print(f"전체 HTML 줄 수: {len(all_lines)}")
# 오류는 ?v=20260609b:741 이므로 741번째 줄
target = 741
s = max(0, target-4)
e = min(len(all_lines), target+4)
print(f"\nHTML {s+1}~{e}번 줄:")
for j in range(s, e):
    marker = ">>>" if j == target-1 else "   "
    print(f"{marker} {j+1}: {all_lines[j][:150]}")
