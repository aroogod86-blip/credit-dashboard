"""
🔎 종목별 밸류에이션 시그널 (보유 채권 탭 · 첫 번째 탭)

블룸버그 추가 호출 없이 대시보드 데이터(D.bonds / D.allBonds 현재 G-spread·듀레이션·등급·섹터·지역,
D.spreadHistoryYTD 보유 종목 일별 G-spread)로 브라우저에서 계산.

세 가지 지표 (모두 bp 또는 z, '+' = 스프레드가 넓음 = 싸다 = 저평가)
  1) 동종 대비 (단면 상대가치)
     같은 그룹(등급 AA/A/BBB × 섹터 금융/정부·공기업/일반기업 × 지역 미국/유럽/아시아·기타) 종목들로
     G-spread = a + b·ln(듀레이션) 곡선을 맞추고, 그 곡선 대비 해당 종목의 잔차(bp).
     그룹이 6개 미만이면 지역 → 섹터 순으로 묶음을 넓힘. 본인은 곡선 추정에서 제외.
  2) 상대 6M 대비 (히스토리컬 상대가치)
     매일 '종목 스프레드 − 같은 그룹 보유 종목 중앙값'을 구해, 지금 값이 최근 6M(126영업일) 평균보다
     얼마나 넓은지(bp)와 z. 시장 전체가 넓어진 효과는 빠지고 종목 고유 움직임만 남음.
  3) 자체 6M 위치 (참고)
     종목 자기 스프레드가 최근 6M 범위에서 어디인지(백분위). 시장 전체 움직임이 포함됨 → 판단에는 미사용.

판단 점수 = (동종 대비 z + 상대 6M z) 평균 (있는 것만, 각 z는 ±3으로 제한)
  ≥ +1.0 저평가 / ≥ +0.5 다소 저평가 / ≤ −1.0 고평가 / ≤ −0.5 다소 고평가 / 그 외 중립

사용: export_credit_dashboard.py 의 generate_html()에서
    from export_value_tab import render_value_html, render_value_js
"""

THRESH = {"strong": 1.0, "mild": 0.5, "min_group": 6, "window": 126}

HTML = r"""
<div class="cd" id="val-card">
  <div class="ct2">🔎 밸류에이션 시그널 <span style="font-size:11px;color:var(--tx3);font-weight:400;margin-left:8px">'+' = 스프레드가 넓음 = 저평가(싸다) · 아래 종목별 현황 표의 '밸류' 열과 동일 기준</span></div>
  <div class="ms" id="val-kpi" style="margin-bottom:12px"></div>
  <div class="g2" style="margin-bottom:8px">
    <div><div style="font-size:12px;font-weight:600;color:var(--gn);margin-bottom:6px">저평가 상위 (스프레드 넓음)</div><div class="tw"><table><thead><tr><th>채권명</th><th>밸류</th><th style="text-align:right">동종比</th><th style="text-align:right">상대 6M比</th><th style="text-align:right">자체 6M위치</th><th>비교 그룹</th></tr></thead><tbody id="val-cheap"></tbody></table></div></div>
    <div><div style="font-size:12px;font-weight:600;color:var(--rd);margin-bottom:6px">고평가 상위 (스프레드 좁음)</div><div class="tw"><table><thead><tr><th>채권명</th><th>밸류</th><th style="text-align:right">동종比</th><th style="text-align:right">상대 6M比</th><th style="text-align:right">자체 6M위치</th><th>비교 그룹</th></tr></thead><tbody id="val-rich"></tbody></table></div></div>
  </div>
  <div class="fl" style="margin:6px 0 0"><label>운용코드</label><select id="val-fund" onchange="VALP()"><option value="ALL">전체 보유</option></select>
    <label>섹터</label><select id="val-sec" onchange="VALP()"><option value="ALL">전체</option></select></div>
  <div style="font-size:11px;color:var(--tx3);line-height:1.7;margin-top:6px">
    <b>동종比</b>: 같은 등급·섹터·지역 종목들의 듀레이션-스프레드 곡선 대비 잔차(bp, 비보유 비교종목 포함) ·
    <b>상대 6M比</b>: (종목 − 같은 그룹 보유종목 중앙값) 스프레드 차이가 최근 6M 평균보다 넓은 정도(bp) — 시장 전체 확대·축소 효과 제거 ·
    <b>자체 6M위치</b>: 자기 스프레드의 6M 백분위(시장 영향 포함, 참고용)<br>
    <b>밸류</b> = 두 상대가치 z의 평균: ≥+1 저평가 · ≥+0.5 다소 저평가 · ≤−1 고평가 · ≤−0.5 다소 고평가 · 그 외 중립 · 그룹 종목이 6개 미만이면 지역→섹터 순으로 넓혀 비교
  </div>
</div>
"""

JS = r"""
// ═══ 밸류에이션 시그널 (export_value_tab.py) ═══
(function(){
const TH=__THRESH__, W=TH.window;
function rb(r){if(!r)return null;r=String(r).toUpperCase().trim();if(r.startsWith('AA'))return 'AA';if(r.startsWith('BBB'))return 'BBB';if(r.startsWith('A'))return 'A';return null;}
function sg(s){return s==='금융기관'?'금융':(s==='정부'?'정부·공기업':'일반기업');}
function rg(r){return r==='미국'?'미국':(r==='유럽'?'유럽':'아시아/기타');}
function keys(b){const a=rb(b.rating),s=sg(b.sector),r=rg(b.region);return [a+'|'+s+'|'+r,a+'|'+s,a];}
function keyName(k){return k.split('|').join(' · ');}
const usd=b=>(!b.ccy||b.ccy==='USD');
const ok=b=>usd(b)&&rb(b.rating)&&b.spread!=null&&isFinite(b.spread)&&b.duration>=0.5;
// 단면 유니버스: 보유 + 비보유 비교종목
const U={};(D.allBonds||[]).concat(D.bonds||[]).forEach(b=>{if(ok(b))U[b.isin]=b;});
const UNI=Object.values(U);
const G=[{},{},{}];UNI.forEach(b=>keys(b).forEach((k,i)=>{(G[i][k]=G[i][k]||[]).push(b);}));
function fit(pe){const n=pe.length;if(n<4)return null;let sx=0,sy=0,sxx=0,sxy=0;pe.forEach(p=>{const x=Math.log(p.duration);sx+=x;sy+=p.spread;sxx+=x*x;sxy+=x*p.spread;});
  const den=n*sxx-sx*sx,b1=den!==0?(n*sxy-sx*sy)/den:0,b0=(sy-b1*sx)/n;
  const res=pe.map(p=>p.spread-(b0+b1*Math.log(p.duration)));const sd=Math.sqrt(res.reduce((s,v)=>s+v*v,0)/Math.max(1,n-2));
  return {b0,b1,sd};}
// 시계열 그룹 (보유 종목 중 시계열 있는 것)
const HS=D.spreadHistoryYTD||{dates:[],series:{}};
const HB=(D.bonds||[]).filter(b=>ok(b)&&HS.series[b.label]);
const HG=[{},{},{}];HB.forEach(b=>keys(b).forEach((k,i)=>{(HG[i][k]=HG[i][k]||[]).push(b);}));
function med(a){a=a.slice().sort((x,y)=>x-y);const m=a.length>>1;return a.length%2?a[m]:(a[m-1]+a[m])/2;}
function stats(arr){const L=(()=>{for(let i=arr.length-1;i>=0;i--)if(arr[i]!=null)return i;return -1;})();if(L<0)return null;
  const w=arr.slice(Math.max(0,L-W+1),L+1).filter(v=>v!=null);if(w.length<20)return null;
  const m=w.reduce((s,v)=>s+v,0)/w.length,sd=Math.sqrt(w.reduce((s,v)=>s+(v-m)**2,0)/Math.max(1,w.length-1));
  return {cur:arr[L],mean:m,sd,pct:w.filter(v=>v<=arr[L]).length/w.length,n:w.length};}
(D.bonds||[]).forEach(b=>{
  b.v_rv=null;b.v_rvz=null;b.v_hrv=null;b.v_hrvz=null;b.v_hp=null;b.v_score=null;b.v_grp='';
  if(!ok(b))return;
  const ks=keys(b);
  // 1) 동종 대비
  for(let i=0;i<3;i++){const pe=(G[i][ks[i]]||[]).filter(p=>p.isin!==b.isin);if(pe.length>=TH.min_group||i===2){const f=fit(pe);
    if(f&&f.sd>0){b.v_rv=b.spread-(f.b0+f.b1*Math.log(b.duration));b.v_rvz=b.v_rv/f.sd;b.v_grp=keyName(ks[i])+' ('+pe.length+')';}break;}}
  // 2) 상대 6M (그룹 중앙값 대비 초과 스프레드의 6M 위치)
  const own=HS.series[b.label];
  if(own){
    const so=stats(own);if(so)b.v_hp=so.pct*100;
    for(let i=0;i<3;i++){const pe=(HG[i][ks[i]]||[]).filter(p=>p.isin!==b.isin);if(pe.length>=3||i===2){if(pe.length<3)break;
      const ps=pe.map(p=>HS.series[p.label]);
      const ex=HS.dates.map((d,t)=>{if(own[t]==null)return null;const v=ps.map(s=>s[t]).filter(x=>x!=null);return v.length>=3?own[t]-med(v):null;});
      const st=stats(ex);if(st&&st.sd>0){b.v_hrv=st.cur-st.mean;b.v_hrvz=(st.cur-st.mean)/st.sd;}break;}}
  }
  const cap=v=>Math.max(-3,Math.min(3,v));
  const zs=[b.v_rvz,b.v_hrvz].filter(v=>v!=null&&isFinite(v)).map(cap);
  b.v_score=zs.length?zs.reduce((s,v)=>s+v,0)/zs.length:null;
});
function lab(v){if(v==null)return ['-','var(--tx3)'];
  if(v>=TH.strong)return ['저평가','var(--gn)'];if(v>=TH.mild)return ['다소 저평가','#6ee7b7'];
  if(v<=-TH.strong)return ['고평가','var(--rd)'];if(v<=-TH.mild)return ['다소 고평가','#fca5a5'];return ['중립','var(--tx2)'];}
window.VAL_LABEL=lab;
const sgn=v=>v==null?'-':(v>=0?'+':'')+v.toFixed(1);
// '+'(넓음=저평가)=초록, '-'(좁음=고평가)=빨강 — 스프레드 변동 열(확대=빨강)과 의미가 달라 별도 색
const csg=v=>v==null?'-':'<span style="color:'+(v>=5?'var(--gn)':(v<=-5?'var(--rd)':'var(--tx2)'))+'">'+sgn(v)+'</span>';
// 종목별 현황 표에 열 추가 (YTD 변동 뒤)
const at=TC.findIndex(c=>c.id==='chg3m')+1;
TC.splice(at,0,
  {id:'v_score',n:'밸류',t:'n',f:v=>{const l=lab(v);return v==null?'-':'<span style="color:'+l[1]+';font-weight:700;font-family:var(--ft)">'+l[0]+'</span> <span style="color:var(--tx3);font-size:10.5px">'+sgn(v)+'</span>';}},
  {id:'v_rv',n:'동종比',t:'n',f:csg},
  {id:'v_hrv',n:'상대6M比',t:'n',f:csg},
  {id:'v_hp',n:'자체6M위치',t:'n',f:v=>v==null?'-':v.toFixed(0)+'%'});
// 요약 패널
window.VALGO=function(l){const x=document.getElementById('fx2');if(x){x.value=l;RT();x.scrollIntoView({behavior:'smooth',block:'center'});}};
window.VALP=function(){
  const fd=document.getElementById('val-fund').value,sc=document.getElementById('val-sec').value;
  const L=(D.bonds||[]).filter(b=>b.v_score!=null&&(fd==='ALL'||String(b.fund||FUND_DATA[b.isin]||'').split(', ').includes(fd))&&(sc==='ALL'||b.sector===sc));
  const cnt={c:0,mc:0,n:0,mr:0,r:0};L.forEach(b=>{const v=b.v_score;if(v>=TH.strong)cnt.c++;else if(v>=TH.mild)cnt.mc++;else if(v<=-TH.strong)cnt.r++;else if(v<=-TH.mild)cnt.mr++;else cnt.n++;});
  const card=(t,v,c)=>'<div class="mc"><div class="lb">'+t+'</div><div class="vl" style="color:'+c+'">'+v+'</div></div>';
  document.getElementById('val-kpi').innerHTML=card('저평가',cnt.c,'var(--gn)')+card('다소 저평가',cnt.mc,'#6ee7b7')+card('중립',cnt.n,'var(--tx2)')+card('다소 고평가',cnt.mr,'#fca5a5')+card('고평가',cnt.r,'var(--rd)')+card('평가 종목 수',L.length,'var(--tx)');
  const row=b=>{const l=lab(b.v_score);return '<tr style="cursor:pointer" onclick="VALGO(\''+b.label.replace(/'/g,"\\'")+'\')"><td class="tc">'+b.label+'</td><td class="tc" style="color:'+l[1]+';font-weight:700">'+l[0]+' <span style="color:var(--tx3);font-weight:400">'+sgn(b.v_score)+'</span></td><td class="nm">'+csg(b.v_rv)+'</td><td class="nm">'+csg(b.v_hrv)+'</td><td class="nm">'+(b.v_hp==null?'-':b.v_hp.toFixed(0)+'%')+'</td><td class="tc" style="color:var(--tx3)">'+b.v_grp+'</td></tr>';};
  const srt=L.slice().sort((a,b)=>b.v_score-a.v_score);
  document.getElementById('val-cheap').innerHTML=srt.filter(b=>b.v_score>=TH.mild).slice(0,10).map(row).join('')||'<tr><td colspan="6" class="tc" style="color:var(--tx3)">해당 없음</td></tr>';
  document.getElementById('val-rich').innerHTML=srt.slice().reverse().filter(b=>b.v_score<=-TH.mild).slice(0,10).map(row).join('')||'<tr><td colspan="6" class="tc" style="color:var(--tx3)">해당 없음</td></tr>';
};
(function(){
  const fs=[...new Set(Object.values(FUND_DATA).flatMap(v=>v.split(', ')))].sort();
  const ss=[...new Set((D.bonds||[]).map(b=>b.sector).filter(Boolean))].sort();
  const f=document.getElementById('val-fund'),s=document.getElementById('val-sec');
  if(f)fs.forEach(x=>{const o=document.createElement('option');o.value=x;o.textContent=x;f.appendChild(o);});
  if(s)ss.forEach(x=>{const o=document.createElement('option');o.value=x;o.textContent=x;s.appendChild(o);});
  try{VALP();}catch(e){console.error('VALP:',e);}
})();
})();
"""


def render_value_html():
    return HTML


def render_value_js(thresh=None):
    import json
    return JS.replace("__THRESH__", json.dumps(thresh or THRESH))
