"""
📏 커브 비교 탭 (발행사별 장단기 G-spread 차이 · 교체 시점 판단)

- 데이터: 대시보드가 이미 받아오는 D.spreadHistoryYTD (연초~현재 일별 G-spread, 보유 종목)
          + D.allBonds / D.bonds (현재 G-spread, 듀레이션)
  → 블룸버그 추가 호출 없음. 계산은 모두 브라우저에서.
- 규칙은 '발행사커브_비교.xlsx'와 동일:
    장단기 차이      = 장기 G − 단기 G (bp)
    6M 대비 초과폭   = 현재 차이 − 6M 평균 (교체 후 평균 회귀 시 DV01당 손실)
    교체 시 연간 개선 = [(단기SP−조달)/단기Dur + 단기롤] − [(장기SP−조달)/장기Dur + 장기롤]
    판단: 초과폭 > 연간개선 → 보류 / 절반 초과 → 단계 교체 / 이하 → 교체 적기
          (모니터 쌍은 6M 백분위로 커브 상태만 표시)
- 쌍 추가: 화면에서 발행사 → 단기/장기 선택 후 추가 (브라우저 localStorage에 저장).
  모든 PC에서 고정하려면 아래 PRESETS에 ISIN을 추가.

사용: export_credit_dashboard.py 의 generate_html() 안에서
    from export_curve_tab import render_curve_tab
    curve_tab_html = render_curve_tab()
"""
import json

# 구분: 교체후보 / 모니터
PRESETS = [
    ("US46647PFG63", "US46647PFE16", "교체후보"),   # JPM 4.347 '32 vs 4.81 '36
    ("US46647PFG63", "US46647PFJ03", "교체후보"),   # JPM 4.347 '32 vs 4.898 '37
    ("US65535HCL15", "US65535HCB33", "교체후보"),   # NOMURA '31 vs '35
    ("USY5S5CGAP79", "USY5S5CGAR36", "교체후보"),   # LGENSO '30 vs '35
    ("USY5S5CGAP79", "USY5S5CGAV48", "교체후보"),   # LGENSO '30 vs '36
    ("USY70750CB13", "USY70750CC95", "교체후보"),   # PKX '30 vs '35
    ("USY7S272AL69", "USY7S272AM43", "교체후보"),   # POHANG '31 vs '36
    ("USY4907LAU62", "US500631AE67", "교체후보"),   # KORELE '31 vs 2096
    ("US44891CCU53", "US44891CDH34", "모니터"),     # HYNMTR '29 vs '31
    ("US61748UAR32", "US61748UAS15", "모니터"),     # MS '30 vs '32
    ("XS3028206350", "US38141GF335", "모니터"),     # GS '29 vs '32
    ("US404280EN53", "US404280EG03", "모니터"),     # HSBC '30 vs '32
    ("US06738ECX13", "US06738EDM49", "모니터"),     # BACR '29 vs '32
    ("XS3299373996", "XS3412559299", "모니터"),     # DAESEC '29 vs '31
    ("USY8085FBK58", "USY8085FBL32", "모니터"),     # HYUELE '28 vs '33
]
FUNDING_BP = 70   # 조달비용 기본값 (SOFR+, bp) — 화면에서 변경 가능

CSS = r"""
<style>
#curve-tab .cv-top{display:flex;gap:14px;align-items:center;flex-wrap:wrap;margin-bottom:12px}
#curve-tab .cv-top label{font-size:12px;color:var(--tx3)}
#curve-tab .cv-top input,#curve-tab .cv-top select{padding:6px 10px;font-size:12px;background:var(--sf2);color:var(--tx);border:1px solid var(--bd);border-radius:6px;outline:none}
#curve-tab .cv-kpi{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:14px}
#curve-tab .cv-kpi .mc{padding:12px}
#curve-tab .cv-kpi .vl{font-size:20px}
#curve-tab tbody tr{cursor:pointer}
#curve-tab tbody tr.cv-sel td{background:rgba(59,130,246,.12)}
#curve-tab td.cv-j{font-family:var(--ft);font-weight:600}
#curve-tab .j-bad{color:var(--rd)}#curve-tab .j-warn{color:var(--am)}#curve-tab .j-good{color:var(--gn)}#curve-tab .j-mute{color:var(--tx3)}
#curve-tab .cv-tag{font-size:10px;padding:1px 6px;border-radius:4px;font-family:var(--ft);font-weight:600}
#curve-tab .cv-tag.sw{background:rgba(59,130,246,.18);color:var(--ac)}
#curve-tab .cv-tag.mo{background:rgba(139,152,176,.18);color:var(--tx2)}
#curve-tab .cv-tag.us{background:rgba(139,92,246,.18);color:var(--pu)}
#curve-tab .cv-del{color:var(--tx3);cursor:pointer;margin-left:6px}
#curve-tab .cv-del:hover{color:var(--rd)}
#curve-tab .cv-bsel{font-size:11.5px;padding:3px 4px;background:var(--sf2);color:var(--tx);border:1px solid var(--bd);border-radius:4px;max-width:230px;font-family:var(--ft)}
#curve-tab .cv-note{font-size:11px;color:var(--tx3);line-height:1.6;margin-top:10px}
#curve-tab .cv-add{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
#curve-tab .cv-add select{padding:6px 10px;font-size:12px;background:var(--sf2);color:var(--tx);border:1px solid var(--bd);border-radius:6px;max-width:260px}
#curve-tab .cmp-ctl{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:12px}
#curve-tab .cmp-ctl input{padding:7px 10px;font-size:12px;background:var(--sf2);color:var(--tx);border:1px solid var(--bd);border-radius:6px;width:250px;outline:none}
#curve-tab .cmp-ctl input:focus{border-color:var(--ac)}
#curve-tab .cmp-pg .bt.on{background:var(--ac);color:#fff;border-color:var(--ac)}
#curve-tab .cmp-pos{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:12px}
@media(max-width:900px){#curve-tab .cmp-pos{grid-template-columns:1fr}}
#curve-tab .cmp-c{background:var(--sf2);border:1px solid var(--bd);border-radius:8px;padding:12px}
#curve-tab .cmp-c .nm1{font-size:11px;color:var(--tx3);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#curve-tab .cmp-c .v{font-size:22px;font-weight:700;font-family:var(--mn);margin:2px 0 6px}
#curve-tab .cmp-ch{display:grid;grid-template-columns:repeat(4,1fr);gap:4px;font-family:var(--mn);font-size:11px;margin-bottom:8px}
#curve-tab .cmp-ch div{background:var(--sf);border-radius:4px;padding:3px 4px;text-align:center}
#curve-tab .cmp-ch span{display:block;font-size:9.5px;color:var(--tx3);font-family:var(--ft)}
#curve-tab .gg{position:relative;height:8px;border-radius:4px;background:linear-gradient(90deg,rgba(16,185,129,.35),rgba(139,152,176,.25),rgba(239,68,68,.35));margin:14px 0 4px}
#curve-tab .gg .mk{position:absolute;top:-4px;width:3px;height:16px;background:var(--tx);border-radius:2px;transform:translateX(-1px)}
#curve-tab .gg .av{position:absolute;top:-2px;width:2px;height:12px;background:var(--tx3);transform:translateX(-1px)}
#curve-tab .gg-lb{display:flex;justify-content:space-between;font-size:10px;color:var(--tx3);font-family:var(--mn)}
#curve-tab .gg-t{font-size:11px;color:var(--tx2);margin-top:4px}
</style>
"""

HTML = r"""
<section id="curve-tab">
  <div class="ib">📏 발행사별 장단기 G-spread 차이 — 단기·장기 칸에서 종목을 바로 바꿀 수 있고, 행을 누르면 아래에 두 종목 추이·위치가 표시됩니다</div>
  <div class="cv-top">
    <label>조달비용 (SOFR+, bp)</label><input type="number" id="cv-fund" value="__FUND__" style="width:70px" oninput="RCurve()">
    <label>G-spread 기준</label><select id="cv-basis" onchange="cvBasis()"><option value="interp">만기 보간 UST (엑셀 동일)</option><option value="bm">매칭 generic (보유채권 탭)</option></select>
    <label>구분</label><select id="cv-filter" onchange="RCurve()"><option value="ALL">전체</option><option value="교체후보">교체후보</option><option value="모니터">모니터</option></select>
    <span id="cv-asof" style="font-size:11px;color:var(--tx3);font-family:var(--mn)"></span>
  </div>
  <div class="cv-kpi" id="cv-kpi"></div>
  <div class="cd">
    <div class="tw" style="max-height:560px;overflow-y:auto">
      <table><thead><tr>
        <th>구분</th><th>판단</th><th>발행사</th><th>단기</th><th>장기</th>
        <th style="text-align:right">단기 G</th><th style="text-align:right">장기 G</th>
        <th style="text-align:right">장단기 차이</th><th style="text-align:right">기울기/Dur1년</th>
        <th style="text-align:right">1M Δ</th><th style="text-align:right">3M 평균</th><th style="text-align:right">6M 평균</th>
        <th style="text-align:right">6M 범위</th><th style="text-align:right">6M 백분위</th><th style="text-align:right">z</th>
        <th style="text-align:right">6M 대비 초과</th><th style="text-align:right">교체 연간개선</th>
      </tr></thead><tbody id="cv-body"></tbody></table>
    </div>
    <div class="cv-note">
      단위 bp · G-spread 기준은 위 선택(만기 보간 UST = 엑셀과 동일 / 매칭 generic = 보유채권 탭 시계열) · 시계열 = 연초~현재 일별 · 3M/6M = 최근 63/126 영업일<br>
      교체 연간개선 = DV01 1bp당 연간 bp [(단기G−조달)/단기Dur + 단기롤] − [(장기G−조달)/장기Dur + 장기롤], 롤 가정: Dur 5년 미만 10bp · 5~10년 4bp · 10년 이상 0bp<br>
      판단(교체후보): 6M 대비 초과 스팁폭 ≥ 연간개선 → <span class="j-bad">보류</span> · 절반 이상 → <span class="j-warn">단계 교체</span> · 그 미만 → <span class="j-good">교체 적기</span> · 모니터: 6M 백분위 80% 이상 과도 스팁 / 20% 이하 평탄<br>단기·장기 칸의 드롭다운으로 같은 발행사 내 종목 변경 가능(이 브라우저에 저장, ↺로 기본 종목 복귀)
    </div>
  </div>
  <div class="cd" id="cmp">
    <div class="ct2">🔍 두 종목 스프레드 비교 <span style="font-size:11px;color:var(--tx3);font-weight:400">— 위 표에서 행을 누르거나, 종목명·ISIN을 직접 입력 (다른 발행사끼리도 가능)</span></div>
    <div class="cmp-ctl">
      <label style="font-size:12px;color:var(--tx3)">A</label><input id="cmp-a" list="cmp-dl" placeholder="종목명 또는 ISIN" onchange="cmpGo()">
      <label style="font-size:12px;color:var(--tx3)">B</label><input id="cmp-b" list="cmp-dl" placeholder="종목명 또는 ISIN" onchange="cmpGo()">
      <button class="bt" onclick="cmpSwap()" title="A·B 바꾸기">⇄</button>
      <span class="cmp-pg" id="cmp-pg"></span>
      <button class="bt" onclick="cmpToPairs()">＋ 쌍 목록에 추가</button>
      <datalist id="cmp-dl"></datalist>
    </div>
    <div class="cmp-pos" id="cmp-pos"></div>
    <div class="g2" style="margin-bottom:0">
      <div><div class="ct2" style="font-size:12.5px" id="cmp-t1">G-spread 추이 (bp)</div><div class="ch" style="height:280px"><canvas id="cmp-c1"></canvas></div></div>
      <div><div class="ct2" style="font-size:12.5px" id="cmp-t2">차이 B − A (bp)</div><div class="ch" style="height:280px"><canvas id="cmp-c2"></canvas></div></div>
    </div>
    <div class="cv-note">게이지: 선택 기간의 최저~최고 범위에서 현재 위치(흰 막대), 회색 눈금 = 기간 평균 · 백분위 = 기간 중 현재 이하였던 날 비율 · 변화 = 현재 − N영업일 전(1M 21, 3M 63, 6M 126, YTD 연초)</div>
  </div>
  <div class="cd">
    <div class="ct2">➕ 쌍 추가 <span style="font-size:11px;color:var(--tx3);font-weight:400">— 이 브라우저에 저장. 모든 PC에 고정하려면 export_curve_tab.py의 PRESETS에 추가</span></div>
    <div class="cv-add">
      <select id="cv-iss" onchange="cvFillBonds()"></select>
      <select id="cv-s"></select><span style="color:var(--tx3)">vs</span><select id="cv-l"></select>
      <select id="cv-t"><option value="모니터">모니터</option><option value="교체후보">교체후보</option></select>
      <button class="bt pr" onclick="cvAdd()">추가</button>
    </div>
  </div>
</section>
"""

JS = r"""
<script>
(function(){
const PRESETS=__PRESETS__;
const LSKEY='curvePairs.v1';
let selKey=null, addInit=false;
function BM(){const m={};(D.allBonds||[]).forEach(b=>{m[b.isin]=b});(D.bonds||[]).forEach(b=>{m[b.isin]=Object.assign({},m[b.isin]||{},b)});return m;}
const CFG0={hold:1.0,stage:0.5,hi:80,lo:20,minN:60,r1:10,r2:4,r3:0};
const CFG=CFG0;
let BASIS=null;
function hasInterp(){return !!(D.spreadInterpYTD&&Object.keys(D.spreadInterpYTD.series||{}).length);}
function H(){if(BASIS===null)BASIS=hasInterp()?'interp':'bm';return (BASIS==='interp'&&D.spreadInterpYTD)?D.spreadInterpYTD:(D.spreadHistoryYTD||{dates:[],series:{}});}
function rollOf(d){return d>=10?CFG.r3:(d>=5?CFG.r2:CFG.r1);}
function cfgInit(){
  const sb=document.getElementById('cv-basis');H();sb.value=BASIS;
  if(!hasInterp()){sb.options[0].disabled=true;sb.options[0].text='만기 보간 UST (데이터 없음 — 스크립트 업데이트 필요)';}
}
const EDK='curvePairEdit.v1';
function loadEd(){try{return JSON.parse(localStorage.getItem(EDK)||'{}')}catch(e){return {}}}
function saveEd(o){try{localStorage.setItem(EDK,JSON.stringify(o))}catch(e){}}
window.cvBasis=function(){BASIS=document.getElementById('cv-basis').value;window._cmpLastKey=null;dlInit=false;RCurve();};
function loadU(){try{return JSON.parse(localStorage.getItem(LSKEY)||'[]')}catch(e){return []}}
function saveU(a){try{localStorage.setItem(LSKEY,JSON.stringify(a))}catch(e){}}
function f1(v){return v==null||isNaN(v)?'-':v.toFixed(1)}
function shortLbl(b){return b?b.label:'-'}
function calc(p,bm,f){
  const o={p:p,bs:bm[p.s],bl:bm[p.l]};
  if(!o.bs||!o.bl){o.err='대시보드 종목 미포함';return o;}
  const h=H(),S=h.series[o.bs.label],L=h.series[o.bl.label];
  o.dS=o.bs.duration;o.dL=o.bl.duration;
  if(!S||!L){o.err='시계열 없음';return o;}
  const diff=h.dates.map((d,i)=>(S[i]!=null&&L[i]!=null)?L[i]-S[i]:null);
  o.diff=diff;
  let last=-1;for(let i=diff.length-1;i>=0;i--){if(diff[i]!=null){last=i;break;}}
  if(last<0){o.err='시계열 없음';return o;}
  o.last=last;o.cur=diff[last];o.asof=h.dates[last];
  // 단기·장기 SP와 연간개선은 장단기 차이와 같은 시계열(같은 날짜) 값으로 계산 — 보유채권 탭 스냅샷과 섞지 않음
  o.sS=S[last];o.sL=L[last];
  if(o.dS>0&&o.dL>0)o.X=((o.sS-f)/o.dS+rollOf(o.dS))-((o.sL-f)/o.dL+rollOf(o.dL));
  const fi=diff.findIndex(v=>v!=null);o.start=h.dates[fi];
  const win=n=>diff.slice(Math.max(0,last-n+1),last+1).filter(v=>v!=null);
  const w3=win(63),w6=win(126),avg=a=>a.reduce((x,y)=>x+y,0)/a.length;
  o.n6=w6.length;
  if(w3.length)o.a3=avg(w3);
  if(w6.length){
    o.a6=avg(w6);o.mn=Math.min(...w6);o.mx=Math.max(...w6);
    o.pct=w6.filter(v=>v<=o.cur).length/w6.length;
    if(w6.length>1){const sd=Math.sqrt(w6.reduce((s,v)=>s+(v-o.a6)**2,0)/(w6.length-1));o.z=sd>0?(o.cur-o.a6)/sd:null;}
    o.ex=o.cur-o.a6;
  }
  for(let i=last-21;i>=0;i--){if(diff[i]!=null){o.c1m=o.cur-diff[i];break;}}
  if(o.dL>o.dS)o.slope=o.cur/(o.dL-o.dS);
  return o;
}
function judge(o){
  if(o.err)return [o.err,'j-mute'];
  if(o.p.t==='모니터'){
    if(o.pct==null)return ['통계 대기','j-mute'];
    if(o.n6<CFG.minN)return ['데이터 부족(발행 후 기간 짧음)','j-mute'];
    if(o.pct*100>=CFG.hi)return [`커브 과도 스팁(6M 상위 ${100-CFG.hi}%)`,'j-bad'];
    if(o.pct*100<=CFG.lo)return [`커브 평탄(6M 하위 ${CFG.lo}%)`,'j-good'];
    return ['정상 범위','j-mute'];
  }
  if(o.X==null)return ['Dur·스프레드 확인 필요','j-mute'];
  if(o.X<=0)return ['교체 이점 없음','j-mute'];
  if(o.ex==null)return ['통계 대기','j-mute'];
  if(o.n6<CFG.minN)return ['데이터 부족(발행 후 기간 짧음)','j-mute'];
  if(o.ex>=o.X*CFG.hold)return ['보류: 되돌림 손실 > 1년 이득','j-bad'];
  if(o.ex>=o.X*CFG.stage)return ['단계 교체','j-warn'];
  return ['교체 적기','j-good'];
}
function exStyle(v){if(v==null)return '';const a=Math.min(Math.abs(v)/15,1)*0.35;return v>0?`background:rgba(239,68,68,${a})`:`background:rgba(16,185,129,${a})`;}
function pairs(){
  const ed=loadEd();
  const pre=PRESETS.map((x,i)=>{const id='pre'+i,e=ed[id]||{};return {id,s:e.s||x[0],l:e.l||x[1],t:x[2],src:'pre',edited:!!(e.s||e.l),s0:x[0],l0:x[1]};});
  const us=loadU().map((x,i)=>({id:'usr'+i,s:x.s,l:x.l,t:x.t,src:'user'}));
  return pre.concat(us);
}
function bondOpts(cur,other){
  const h=H(),bm=BM(),b=bm[cur];if(!b)return `<option value="${cur}">${cur}</option>`;
  const arr=Object.values(bm).filter(x=>x.issuer===b.issuer&&h.series[x.label]&&x.isin!==other).sort((a,c)=>a.duration-c.duration);
  if(!arr.some(x=>x.isin===cur))arr.unshift(b);
  return arr.map(x=>`<option value="${x.isin}" ${x.isin===cur?'selected':''}>${x.label} · ${x.duration!=null?x.duration.toFixed(1):'-'}y</option>`).join('');
}
window.cvEdit=function(id,side,isin){
  if(id.startsWith('usr')){const a=loadU(),i=+id.slice(3);if(a[i]){a[i][side]=isin;const bm=BM();if(bm[a[i].s]&&bm[a[i].l]&&bm[a[i].s].duration>bm[a[i].l].duration){const t=a[i].s;a[i].s=a[i].l;a[i].l=t;}saveU(a);}}
  else{const e=loadEd();e[id]=e[id]||{};e[id][side]=isin;saveEd(e);}
  const p=pairs().find(x=>x.id===id);if(p)selKey=p.s+'|'+p.l;RCurve();
};
window.cvReset=function(id){const e=loadEd();delete e[id];saveEd(e);const p=pairs().find(x=>x.id===id);if(p)selKey=p.s+'|'+p.l;RCurve();};
window.RCurve=function(){
  const bm=BM(),f=parseFloat(document.getElementById('cv-fund').value)||0,flt=document.getElementById('cv-filter').value;
  const rows=pairs().map(p=>calc(p,bm,f)).filter(o=>flt==='ALL'||o.p.t===flt);
  let asof=rows.map(o=>o.asof).filter(Boolean).sort().pop();
  document.getElementById('cv-asof').textContent=asof?('기준일 '+asof+' · 시계열 '+(H().dates.length)+'영업일 · '+(BASIS==='interp'?'만기 보간 UST 기준':'매칭 generic 기준')):'';
  const cnt={bad:0,warn:0,good:0,steep:0};
  const body=rows.map((o,i)=>{
    const key=o.p.s+'|'+o.p.l,j=judge(o);
    if(j[0].startsWith('보류'))cnt.bad++;if(j[0]==='단계 교체')cnt.warn++;if(j[0]==='교체 적기')cnt.good++;if(j[0].startsWith('커브 과도'))cnt.steep++;
    const tag=o.p.src==='user'?'<span class="cv-tag us">추가</span>':(o.p.t==='교체후보'?'<span class="cv-tag sw">교체후보</span>':'<span class="cv-tag mo">모니터</span>');
    const del=o.p.src==='user'?`<span class="cv-del" title="삭제" onclick="event.stopPropagation();cvDel('${key}')">✕</span>`:'';
    const iss=(o.bs&&o.bs.issuer)||(o.bl&&o.bl.issuer)||'-';
    const rng=(o.mn!=null)?(f1(o.mn)+' ~ '+f1(o.mx)):'-';
    return `<tr data-key="${key}" class="${key===selKey?'cv-sel':''}" onclick="cvPick('${key}')">
      <td class="tc">${tag}${o.p.t==='교체후보'&&o.p.src==='user'?' <span class="cv-tag sw">교체</span>':''}${del}</td>
      <td class="cv-j ${j[1]}">${j[0]}</td>
      <td class="tc">${iss}</td>
      <td class="tc" onclick="event.stopPropagation()">${o.bs?`<select class="cv-bsel" onchange="cvEdit('${o.p.id}','s',this.value)">${bondOpts(o.p.s,o.p.l)}</select>`:o.p.s}</td>
      <td class="tc" onclick="event.stopPropagation()">${o.bl?`<select class="cv-bsel" onchange="cvEdit('${o.p.id}','l',this.value)">${bondOpts(o.p.l,o.p.s)}</select>`:o.p.l}${o.p.edited?` <span class="cv-del" title="기본 종목으로 되돌리기" onclick="cvReset('${o.p.id}')">↺</span>`:''}</td>
      <td class="nm">${f1(o.sS)}</td><td class="nm">${f1(o.sL)}</td>
      <td class="nm sp">${f1(o.cur)}</td><td class="nm">${f1(o.slope)}</td>
      <td class="nm ${o.c1m>0?'ps':(o.c1m<0?'ng':'')}">${o.c1m!=null?(o.c1m>0?'+':'')+o.c1m.toFixed(1):'-'}</td>
      <td class="nm">${f1(o.a3)}</td><td class="nm">${f1(o.a6)}</td><td class="nm">${rng}</td>
      <td class="nm">${o.pct!=null?Math.round(o.pct*100)+'%':'-'}</td><td class="nm">${o.z!=null?o.z.toFixed(1):'-'}</td>
      <td class="nm sp" style="${exStyle(o.ex)}">${o.ex!=null?(o.ex>0?'+':'')+o.ex.toFixed(1):'-'}</td>
      <td class="nm">${f1(o.X)}</td>
</tr>`;
  }).join('');
  document.getElementById('cv-body').innerHTML=body||'<tr><td colspan="17" class="tc" style="color:var(--tx3)">쌍 없음</td></tr>';
  document.getElementById('cv-kpi').innerHTML=
    `<div class="mc"><div class="lb">교체 적기</div><div class="vl" style="color:var(--gn)">${cnt.good}</div></div>`+
    `<div class="mc"><div class="lb">단계 교체</div><div class="vl" style="color:var(--am)">${cnt.warn}</div></div>`+
    `<div class="mc"><div class="lb">보류 (커브 과도 스팁)</div><div class="vl" style="color:var(--rd)">${cnt.bad}</div></div>`+
    `<div class="mc"><div class="lb">모니터: 과도 스팁</div><div class="vl">${cnt.steep}</div></div>`;
  if(!selKey&&rows.length){const r0=rows.find(o=>!o.err);if(r0)selKey=r0.p.s+'|'+r0.p.l;}
  cvDraw(rows.find(o=>o.p.s+'|'+o.p.l===selKey));
  document.querySelectorAll('#cv-body tr').forEach(tr=>tr.classList.toggle('cv-sel',tr.dataset.key===selKey));
  if(!addInit){cvInitAdd();cfgInit();addInit=true;}
};
const PER={'1M':21,'3M':63,'6M':126,'YTD':100000};let cmpPer='6M',cmpA=null,cmpB=null,chA=null,chB=null,dlInit=false;
function hasSer(b){return b&&H().series[b.label];}
function resolve(txt){
  if(!txt)return null;const t=txt.trim(),bm=BM(),all=Object.values(bm).filter(hasSer);
  const m=t.match(/[A-Z]{2}[A-Z0-9]{9}[0-9]/);if(m&&bm[m[0]]&&hasSer(bm[m[0]]))return bm[m[0]];
  return all.find(b=>b.label===t)||all.find(b=>b.label.toLowerCase().startsWith(t.toLowerCase()))||all.find(b=>b.label.toLowerCase().includes(t.toLowerCase()))||null;
}
function dlFill(){
  const all=Object.values(BM()).filter(hasSer).sort((a,b)=>a.label.localeCompare(b.label));
  document.getElementById('cmp-dl').innerHTML=all.map(b=>`<option value="${b.label} | ${b.isin}">`).join('');
  document.getElementById('cmp-pg').innerHTML=Object.keys(PER).map(k=>`<button class="bt ${k===cmpPer?'on':''}" onclick="cmpSetPer('${k}')">${k}</button>`).join('');
}
function lastIdx(a){for(let i=a.length-1;i>=0;i--)if(a[i]!=null)return i;return -1;}
function stat(arr,per){
  const L=lastIdx(arr);if(L<0)return null;const cur=arr[L],n=PER[per];
  const w=arr.slice(Math.max(0,L-n+1),L+1).filter(v=>v!=null);
  const back=k=>{if(k>=100000){const f=arr.findIndex(v=>v!=null);return f>=0&&f<L?cur-arr[f]:null;}for(let i=L-k;i>=0;i--)if(arr[i]!=null)return cur-arr[i];return null;};
  const mn=Math.min(...w),mx=Math.max(...w),avg=w.reduce((x,y)=>x+y,0)/w.length;
  return {cur,L,mn,mx,avg,pct:w.filter(v=>v<=cur).length/w.length,n:w.length,ch:{'1M':back(21),'3M':back(63),'6M':back(126),'YTD':back(100000)}};
}
function sgn(v){return v==null?'-':(v>0?'+':'')+v.toFixed(1);}
function card(title,name,st,color){
  if(!st)return `<div class="cmp-c"><div class="nm1">${title}</div><div class="v">-</div><div class="gg-t">데이터 없음</div></div>`;
  const pos=st.mx>st.mn?(st.cur-st.mn)/(st.mx-st.mn)*100:50,ap=st.mx>st.mn?(st.avg-st.mn)/(st.mx-st.mn)*100:50;
  const chs=['1M','3M','6M','YTD'].map(k=>`<div class="${st.ch[k]>0?'ps':(st.ch[k]<0?'ng':'')}" style="color:${st.ch[k]>0?'var(--rd)':(st.ch[k]<0?'var(--gn)':'var(--tx2)')}"><span>${k}</span>${sgn(st.ch[k])}</div>`).join('');
  const lv=st.pct>=0.8?'<b style="color:var(--rd)">상단</b>':(st.pct<=0.2?'<b style="color:var(--gn)">하단</b>':'중간');
  return `<div class="cmp-c" style="border-top:3px solid ${color}"><div class="nm1">${title} · ${name}</div>
    <div class="v">${st.cur.toFixed(1)}<span style="font-size:12px;color:var(--tx3);font-weight:400"> bp</span></div>
    <div class="cmp-ch">${chs}</div>
    <div class="gg"><div class="av" style="left:${ap}%"></div><div class="mk" style="left:${Math.max(0,Math.min(100,pos))}%"></div></div>
    <div class="gg-lb"><span>${st.mn.toFixed(1)}</span><span>${cmpPer} 범위</span><span>${st.mx.toFixed(1)}</span></div>
    <div class="gg-t">${cmpPer} 백분위 <b>${Math.round(st.pct*100)}%</b> (${lv}) · 평균 ${st.avg.toFixed(1)} 대비 ${sgn(st.cur-st.avg)}</div></div>`;
}
function cmpRender(){
  const h=H();if(chA){chA.destroy();chA=null;}if(chB){chB.destroy();chB=null;}
  const pos=document.getElementById('cmp-pos');
  if(!cmpA||!cmpB){pos.innerHTML='<div class="gg-t">두 종목을 선택하세요</div>';return;}
  const A=h.series[cmpA.label],B=h.series[cmpB.label];
  const D2=h.dates.map((d,i)=>(A[i]!=null&&B[i]!=null)?B[i]-A[i]:null);
  const sA=stat(A,cmpPer),sB=stat(B,cmpPer),sD=stat(D2,cmpPer);
  pos.innerHTML=card('A',cmpA.label,sA,'#06b6d4')+card('B',cmpB.label,sB,'#f59e0b')+card('차이 B−A',(cmpB.label.split(' ')[0]===cmpA.label.split(' ')[0]?'동일 발행사 커브':'종목 간 스프레드'),sD,'#3b82f6');
  const L=Math.max(lastIdx(A),lastIdx(B)),n=PER[cmpPer],st=Math.max(0,L-n+1);
  const lab=h.dates.slice(st,L+1),cut=a=>a.slice(st,L+1);
  const base={responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},
    plugins:{legend:{position:'top'},tooltip:{callbacks:{label:c=>c.dataset.label+': '+(c.parsed.y!=null?c.parsed.y.toFixed(1)+'bp':'-')}}},
    scales:{x:{ticks:{maxTicksLimit:8}},y:{title:{display:true,text:'bp'}}}};
  chA=new Chart(document.getElementById('cmp-c1').getContext('2d'),{type:'line',data:{labels:lab,datasets:[
    {label:'A '+cmpA.label,data:cut(A),borderColor:'#06b6d4',borderWidth:2,pointRadius:0,spanGaps:true,tension:.15},
    {label:'B '+cmpB.label,data:cut(B),borderColor:'#f59e0b',borderWidth:2,pointRadius:0,spanGaps:true,tension:.15}]},options:base});
  const avg=sD?lab.map(()=>sD.avg):[];
  chB=new Chart(document.getElementById('cmp-c2').getContext('2d'),{type:'line',data:{labels:lab,datasets:[
    {label:'B − A',data:cut(D2),borderColor:'#3b82f6',backgroundColor:'rgba(59,130,246,.10)',borderWidth:2,pointRadius:0,spanGaps:true,fill:'origin',tension:.15},
    {label:cmpPer+' 평균',data:avg,borderColor:'#8b98b0',borderDash:[5,4],borderWidth:1.5,pointRadius:0}]},options:base});
  document.getElementById('cmp-t1').textContent=`G-spread 추이 (bp) · ${cmpPer}`;
  document.getElementById('cmp-t2').textContent=`차이 B − A (bp) · 현재 ${sD?sD.cur.toFixed(1):'-'} / ${cmpPer} 평균 ${sD?sD.avg.toFixed(1):'-'}`;
}
function cmpSet(a,b){cmpA=a;cmpB=b;
  document.getElementById('cmp-a').value=a?a.label+' | '+a.isin:'';document.getElementById('cmp-b').value=b?b.label+' | '+b.isin:'';cmpRender();}
window.cmpGo=function(){const a=resolve(document.getElementById('cmp-a').value),b=resolve(document.getElementById('cmp-b').value);
  if(document.getElementById('cmp-a').value&&!a)alert('A 종목을 찾을 수 없습니다 (대시보드 시계열 보유 종목만 가능)');
  if(document.getElementById('cmp-b').value&&!b)alert('B 종목을 찾을 수 없습니다 (대시보드 시계열 보유 종목만 가능)');
  cmpSet(a||cmpA,b||cmpB);};
window.cmpSwap=function(){cmpSet(cmpB,cmpA);};
window.cmpSetPer=function(k){cmpPer=k;dlFill();cmpRender();};
window.cmpToPairs=function(){if(!cmpA||!cmpB||cmpA.isin===cmpB.isin)return;let s=cmpA,l=cmpB;if(s.duration>l.duration){s=cmpB;l=cmpA;}
  const a=loadU();if(!a.some(x=>x.s===s.isin&&x.l===l.isin)&&!PRESETS.some(x=>x[0]===s.isin&&x[1]===l.isin))a.push({s:s.isin,l:l.isin,t:'모니터'});saveU(a);selKey=s.isin+'|'+l.isin;RCurve();};
function cvDraw(o){
  if(!dlInit){dlFill();dlInit=true;}
  if(!o||!o.bs||!o.bl){cmpRender();return;}
  const k=o.p.s+'|'+o.p.l;if(k===window._cmpLastKey){if(!chA)cmpRender();return;}window._cmpLastKey=k;
  cmpSet(hasSer(o.bs)?o.bs:null,hasSer(o.bl)?o.bl:null);
}
window.cvPick=function(k){selKey=k;RCurve();};
window.cvDel=function(k){const a=loadU().filter(x=>x.s+'|'+x.l!==k);saveU(a);if(selKey===k)selKey=null;RCurve();};
function cvInitAdd(){
  const h=H(),byIss={};
  (D.bonds||[]).forEach(b=>{if(h.series[b.label]&&b.duration>0&&b.ccy!=='JPY'){(byIss[b.issuer]=byIss[b.issuer]||[]).push(b);}});
  const iss=Object.keys(byIss).filter(k=>byIss[k].length>=2).sort();
  window._cvIss=byIss;
  document.getElementById('cv-iss').innerHTML='<option value="">발행사 선택</option>'+iss.map(k=>`<option value="${k}">${k} (${byIss[k].length})</option>`).join('');
}
window.cvFillBonds=function(){
  const k=document.getElementById('cv-iss').value,arr=((window._cvIss||{})[k]||[]).slice().sort((a,b)=>a.duration-b.duration);
  const opt=arr.map(b=>`<option value="${b.isin}">${b.label} · ${b.duration.toFixed(1)}y</option>`).join('');
  const s=document.getElementById('cv-s'),l=document.getElementById('cv-l');s.innerHTML=opt;l.innerHTML=opt;
  if(arr.length){let si=0,best=1e9;arr.forEach((b,i)=>{const d=Math.abs(b.duration-4);if(d<best&&i<arr.length-1){best=d;si=i;}});s.selectedIndex=si;l.selectedIndex=arr.length-1;}
};
window.cvAdd=function(){
  const s=document.getElementById('cv-s').value,l=document.getElementById('cv-l').value,t=document.getElementById('cv-t').value;
  if(!s||!l||s===l){alert('단기·장기 종목을 서로 다르게 선택하세요');return;}
  const bm=BM();let ss=s,ll=l;if(bm[s]&&bm[l]&&bm[s].duration>bm[l].duration){ss=l;ll=s;}
  const a=loadU();if(!a.some(x=>x.s===ss&&x.l===ll)&&!PRESETS.some(x=>x[0]===ss&&x[1]===ll))a.push({s:ss,l:ll,t:t});saveU(a);selKey=ss+'|'+ll;RCurve();
};
})();
</script>
"""


def render_curve_tab(presets=None, funding_bp=FUNDING_BP):
    p = presets or PRESETS
    return (CSS + HTML.replace("__FUND__", str(funding_bp))
            + JS.replace("__PRESETS__", json.dumps([list(x) for x in p], ensure_ascii=False)))


if __name__ == "__main__":
    # 단독 미리보기: 최신 credit_data_*.json으로 탭만 렌더링
    import glob, os
    files = sorted(glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)), "credit_data_*.json")))
    if not files:
        raise SystemExit("credit_data_*.json 없음")
    data = json.load(open(files[-1], encoding="utf-8"))
    page = ('<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>커브 비교 미리보기</title><script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>'
            '<style>:root{--bg:#0b0f1a;--sf:#111827;--sf2:#1a2235;--sf3:#222d42;--bd:#2a3550;--tx:#e8ecf4;--tx2:#8b98b0;--tx3:#5c6b85;--ac:#3b82f6;--acd:#1e40af;--gn:#10b981;--rd:#ef4444;--am:#f59e0b;--pu:#8b5cf6;--ft:sans-serif;--mn:monospace}'
            'body{background:var(--bg);color:var(--tx);font-family:var(--ft);padding:20px}.cd{background:var(--sf);border:1px solid var(--bd);border-radius:10px;padding:16px;margin-bottom:14px}'
            '.ib{color:var(--ac);font-size:12px;margin-bottom:12px}.tw{overflow-x:auto}table{width:100%;border-collapse:collapse;font-size:12px}th{padding:8px;font-size:11px;color:var(--tx3);background:var(--sf2);text-align:left;white-space:nowrap}'
            'td{padding:7px 8px;border-bottom:1px solid rgba(42,53,80,.5);font-family:var(--mn);font-size:11.5px;white-space:nowrap}td.tc{font-family:var(--ft)}td.nm{text-align:right}td.ps{color:var(--rd)}td.ng{color:var(--gn)}td.sp{font-weight:600}'
            '.mc{background:var(--sf);border:1px solid var(--bd);border-radius:10px}.mc .lb{font-size:11px;color:var(--tx3)}.mc .vl{font-weight:700;font-family:var(--mn)}.ct2{font-size:14px;font-weight:600;margin-bottom:10px}.ch{position:relative}'
            '.bt{padding:6px 14px;border:1px solid var(--bd);border-radius:6px;background:var(--ac);color:#fff;cursor:pointer}</style></head><body>'
            + render_curve_tab()
            + '<script>const D=' + json.dumps(data, ensure_ascii=False) + ';RCurve();</script></body></html>')
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "curve_tab_preview.html")
    open(out, "w", encoding="utf-8").write(page)
    print("미리보기:", out)
