# -*- coding: utf-8 -*-
"""
generate_risk_dashboard.py
---------------------------
compute_actual_credit_risk.py가 만든 actual_credit_risk.xlsx(멀티시트)를 읽어 대시보드를 만든다.
- 발행자별 넷 리스크 차트: 토글로 LQD/LUACOAS x 5Y/stress 4가지 중 하나씩 선택해서 봄
- 종목(발행자)별 상세 테이블: 실물Delta/CDS Delta/넷Delta(실제 값 확인용) + 4가지 조합 넷리스크(sigma비율 기준)를 전부 동시 표시

사용법:
    python generate_risk_dashboard.py --input actual_credit_risk.xlsx --output risk_dashboard.html
"""

import argparse
import json
from datetime import datetime

import numpy as np
import pandas as pd

COMBOS = [
    ('LQD', '5Y', 'LQD · 5년'),
    ('LQD', 'stress', 'LQD · 스트레스'),
    ('LUACOAS', '5Y', 'LUACOAS · 5년'),
    ('LUACOAS', 'stress', 'LUACOAS · 스트레스'),
]
COMBO_COLORS = ['#7c1d2c', '#b3261e', '#1d4e7c', '#2e7cb3']

# 벤치마크 비율 없이, 그 종목 자체의 원시 sigma x 넷Delta (단독 변동성 기준)
RAW_COMBOS = [
    (None, '5Y', '단독변동성 · 5년'),
    (None, 'stress', '단독변동성 · 스트레스'),
]
RAW_COLORS = ['#2e7c4a', '#1e6b3e']

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<title>크레딧 스프레드 리스크 대시보드 (sigma비율 기준)</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js"></script>
<style>
  :root {
    --bg: #fafafa; --panel: #ffffff; --border: #e2e2e2;
    --text: #1a1a1a; --text-sub: #6b6b6b;
    --loss: #b3261e; --gain: #1e6b3e;
    --mono: 'IBM Plex Mono', monospace; --sans: 'Noto Sans KR', sans-serif;
  }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--text); font-family:var(--sans); padding:32px; }
  .wrap { max-width: 1360px; margin: 0 auto; }
  header { display:flex; justify-content:space-between; align-items:baseline;
           border-bottom:2px solid var(--text); padding-bottom:14px; margin-bottom:24px; }
  h1 { font-size:20px; font-weight:700; margin:0; letter-spacing:-0.01em; }
  .meta { font-family:var(--mono); font-size:12px; color:var(--text-sub); }

  .kpi-row { display:grid; grid-template-columns: repeat(4, 1fr); gap:14px; margin-bottom:28px; }
  .kpi { background:var(--panel); border:1px solid var(--border); border-left:3px solid var(--kc); padding:16px 18px; }
  .kpi .label { font-size:12px; color:var(--text-sub); margin-bottom:6px; }
  .kpi .value { font-family:var(--mono); font-size:20px; font-weight:600; }

  section { margin-bottom:32px; }
  h2 { font-size:14px; font-weight:700; margin:0 0 12px 0; padding-bottom:6px; border-bottom:1px solid var(--border); }
  .sub-note { font-size:11.5px; color:var(--text-sub); font-weight:400; }
  .chart-panel { background:var(--panel); border:1px solid var(--border); padding:20px; }

  .toggle-row { display:flex; gap:8px; margin-bottom:14px; flex-wrap:wrap; }
  .basis-btn { font-family:var(--sans); font-size:12.5px; font-weight:500; padding:7px 16px;
               border:1px solid var(--border); background:var(--panel); cursor:pointer; border-radius:2px; }
  .basis-btn.active { color:#fff; border-color:transparent; }

  table { width:100%; border-collapse:collapse; background:var(--panel); font-size:12px; }
  th, td { padding:7px 9px; border-bottom:1px solid var(--border); text-align:right; white-space:nowrap; }
  th:first-child, td:first-child, th:nth-child(2), td:nth-child(2) { text-align:left; }
  th { font-weight:500; color:var(--text-sub); font-size:10px; text-transform:uppercase;
       letter-spacing:0.02em; border-bottom:1px solid var(--text); position:sticky; top:0; background:var(--panel); }
  td.num, th.num { font-family:var(--mono); }
  tr:hover { background:#f5e9ea; }
  .neg { color:var(--loss); } .pos { color:var(--gain); }
  .table-scroll { max-height: 560px; overflow-y: auto; border:1px solid var(--border); }
  .col-sep th, .col-sep td { border-left: 2px solid var(--border); }

  select.basis-btn { padding:6px 10px; font-size:12.5px; }
  #heatmapTable { width:100%; border-collapse:collapse; }
  #heatmapTable th, #heatmapTable td { text-align:center; padding:10px 6px; font-family:var(--mono); font-size:11.5px;
    border:1px solid var(--border); white-space:nowrap; cursor:pointer; }
  #heatmapTable th { background:var(--panel); color:var(--text-sub); font-family:var(--sans); font-weight:500; font-size:11px; cursor:default; }
  #heatmapTable td.empty { color:#ccc; cursor:default; }
  #heatmapTable td.selected { outline:2px solid var(--text); outline-offset:-2px; }

  .note { font-size:12px; color:var(--text-sub); line-height:1.6; margin-top:8px; }
  footer { font-family:var(--mono); font-size:11px; color:var(--text-sub); margin-top:40px; text-align:right; }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>크레딧 스프레드 리스크 대시보드</h1>
    <div class="meta">생성: __GEN_TIME__</div>
  </header>

  <div class="kpi-row" style="grid-template-columns: repeat(3, 1fr); margin-bottom:14px;">
    __DELTA_KPI_CARDS__
  </div>
  <div class="kpi-row" style="grid-template-columns: repeat(2, 1fr); margin-bottom:14px;">
    __RAW_KPI_CARDS__
  </div>
  <div class="kpi-row">
    __KPI_CARDS__
  </div>

  <section>
    <h2>부서 크레딧 Delta 집중도 <span class="sub-note">— 등급/섹터/만기/지역별로 어디에 몰려있는지, 각 버킷의 가중평균 YTM·스프레드</span></h2>
    <div class="toggle-row" id="dimToggle"></div>
    <div class="chart-panel"><canvas id="dimChart" height="110"></canvas></div>
    <div class="table-scroll" style="margin-top:14px; max-height:320px;">
      <table>
        <thead>
          <tr><th>버킷</th><th class="num">넷Delta합계</th><th class="num">가중평균YTM(%)</th><th class="num">가중평균스프레드(bp)</th><th class="num">종목수</th></tr>
        </thead>
        <tbody id="dimBody"></tbody>
      </table>
    </div>
    <p class="note">YTM/스프레드는 대시보드 생성 시점 기준 최신값이며, 종목별 Credit Delta(절대값) 가중평균입니다.</p>
  </section>

  <section>
    <h2>등급 x 만기 히트맵 <span class="sub-note">— 색 진하기 = 넷Delta 크기, 섹터/지역은 아래에서 필터</span></h2>
    <div class="toggle-row" style="align-items:center;">
      <span class="toggle-label">표시값</span>
      <select id="filterMeasure" class="basis-btn" style="cursor:pointer;">
        <option value="delta">넷Delta</option>
        <option value="ytm">가중평균 YTM</option>
        <option value="spread">가중평균 스프레드</option>
      </select>
      <span class="toggle-label" style="margin-left:12px;">섹터</span>
      <select id="filterSector" class="basis-btn" style="cursor:pointer;"></select>
      <span class="toggle-label" style="margin-left:12px;">지역</span>
      <select id="filterRegion" class="basis-btn" style="cursor:pointer;"></select>
    </div>
    <div class="chart-panel">
      <table id="heatmapTable" style="table-layout:fixed;"></table>
    </div>
    <p class="note">셀 진할수록 넷Delta 절대값이 큼(리스크 집중). 셀 클릭 시 아래 상세 테이블이 해당 조합으로 필터링됩니다.</p>

    <div class="table-scroll" style="max-height:320px; margin-top:16px;">
      <table>
        <thead>
          <tr>
            <th>등급</th><th>섹터</th><th>만기구간</th><th>지역</th>
            <th class="num">넷Delta합계</th><th class="num">가중평균YTM(%)</th><th class="num">가중평균스프레드(bp)</th><th class="num">종목수</th>
          </tr>
        </thead>
        <tbody id="crossBody"></tbody>
      </table>
    </div>
  </section>

  <section>
    <h2>발행자(티커)별 넷 리스크 <span class="sub-note">— 절대값 상위 25개, 아래 버튼으로 조합 전환</span></h2>
    <div class="toggle-row" id="chartToggle"></div>
    <div class="chart-panel"><canvas id="issuerChart" height="130"></canvas></div>
    <p class="note">sigma비율 = 그 종목 σ / 벤치마크(LQD 또는 LUACOAS) σ. 넷 리스크 = 넷Delta × sigma비율.</p>
  </section>

  <section>
    <h2>종목(발행자)별 리스크 상세</h2>
    <div class="table-scroll">
      <table>
        <thead>
          <tr>
            <th>티커</th><th>등급</th><th>섹터</th>
            <th class="num">실물Delta</th><th class="num">CDS Delta</th><th class="num">넷Delta</th>
            <th class="num col-sep">넷리스크<br>5Y(원시σ)</th>
            <th class="num">넷리스크<br>stress(원시σ)</th>
            <th class="num col-sep">넷리스크<br>LQD·5Y(σ비율)</th>
            <th class="num">넷리스크<br>LQD·stress(σ비율)</th>
            <th class="num">넷리스크<br>LUACOAS·5Y(σ비율)</th>
            <th class="num">넷리스크<br>LUACOAS·stress(σ비율)</th>
          </tr>
        </thead>
        <tbody id="tickerBody"></tbody>
      </table>
    </div>
    <p class="note">실물/CDS/넷 Delta는 실제 $/bp 값(1차 확인용). 오른쪽 4개 컬럼은 넷Delta에 sigma비율을 곱한 리스크 추정치.</p>
  </section>

  <footer>compute_credit_spread_volatility.py + compute_actual_credit_risk.py 결과 기반 · 외화운용실</footer>
</div>

<script>
const tickerData = __TICKER_JSON__;
const groupData = __GROUP_JSON__;
const crossData = __CROSS_JSON__;
const combos = __COMBOS_JSON__.concat(__RAW_COMBOS_JSON__);
const combo_colors = __COLORS_JSON__.concat(__RAW_COLORS_JSON__);
let currentCombo = 0;
let currentDim = '등급';

function fmt(n) {
  if (n === null || n === undefined || isNaN(n)) return '-';
  return Math.round(n).toLocaleString('en-US');
}
function fmtF(n, d) {
  if (n === null || n === undefined || isNaN(n)) return '-';
  return Number(n).toFixed(d);
}
function cls(n) { return (n < 0) ? 'neg' : (n > 0 ? 'pos' : ''); }
function colName(bench, basis) {
  if (bench === null) return `넷_1시그마리스크_${basis}_$`;
  return `넷_실제리스크_sigma비율_vs_${bench}_${basis}_$`;
}

// --- 4차원 집중도 섹션 ---
const dims = [...new Set(groupData.map(d => d['차원']))];
const dimToggle = document.getElementById('dimToggle');
dims.forEach((d, i) => {
  const btn = document.createElement('button');
  btn.className = 'basis-btn' + (d === currentDim ? ' active' : '');
  btn.textContent = d;
  btn.onclick = () => setDim(d);
  dimToggle.appendChild(btn);
});

let dimChartInstance = null;
function renderDim() {
  document.querySelectorAll('#dimToggle .basis-btn').forEach(b => {
    const on = b.textContent === currentDim;
    b.classList.toggle('active', on);
    b.style.background = on ? '#7c1d2c' : 'var(--panel)';
    b.style.color = on ? '#fff' : 'var(--text)';
  });

  const rows = groupData.filter(d => d['차원'] === currentDim)
    .sort((a, b) => Math.abs(b['넷Delta합계_$per bp'] || 0) - Math.abs(a['넷Delta합계_$per bp'] || 0));

  if (dimChartInstance) dimChartInstance.destroy();
  dimChartInstance = new Chart(document.getElementById('dimChart'), {
    type: 'bar',
    data: {
      labels: rows.map(d => String(d['값'])),
      datasets: [{ data: rows.map(d => d['넷Delta합계_$per bp'] || 0), backgroundColor: '#7c1d2c' }]
    },
    options: {
      indexAxis: 'y', responsive: true,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { font: { family: 'IBM Plex Mono', size: 10 } } },
        y: { ticks: { font: { family: 'Noto Sans KR', size: 11 } } }
      }
    }
  });

  const tbody = document.getElementById('dimBody');
  tbody.innerHTML = '';
  rows.forEach(d => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${d['값']}</td>
      <td class="num ${cls(d['넷Delta합계_$per bp'])}">${fmt(d['넷Delta합계_$per bp'])}</td>
      <td class="num">${fmtF(d['가중평균YTM_%'], 3)}</td>
      <td class="num">${fmtF(d['가중평균스프레드_bp'], 1)}</td>
      <td class="num">${d['종목수'] ?? '-'}</td>
    `;
    tbody.appendChild(tr);
  });
}
function setDim(d) { currentDim = d; renderDim(); }
renderDim();

// --- 등급 x 만기 히트맵 (섹터/지역 필터) ---
const RATING_ORDER = ['AAA','AA+','AA','AA-','A+','A','A-','BBB+','BBB','BBB-','BB+','BB','BB-','B+','B','B-','NR'];
const MATURITY_ORDER = ['0-3Y','3-5Y','5-10Y','10Y+','ETF','INDEX','NA'];

function sortByOrder(vals, order) {
  return [...vals].sort((a, b) => {
    const ia = order.indexOf(a), ib = order.indexOf(b);
    if (ia === -1 && ib === -1) return String(a).localeCompare(String(b));
    if (ia === -1) return 1;
    if (ib === -1) return -1;
    return ia - ib;
  });
}

const sectorVals = ['전체', ...new Set(crossData.map(d => d['섹터']).filter(v => v != null))];
const regionVals = ['전체', ...new Set(crossData.map(d => d['지역']).filter(v => v != null))];
const selMeasure = document.getElementById('filterMeasure');
const selSector = document.getElementById('filterSector');
const selRegion = document.getElementById('filterRegion');
sectorVals.forEach(v => { const o = document.createElement('option'); o.value = v; o.textContent = v; selSector.appendChild(o); });
regionVals.forEach(v => { const o = document.createElement('option'); o.value = v; o.textContent = v; selRegion.appendChild(o); });

let selectedCell = null; // {등급, 만기구간}

const MEASURE_FMT = {
  delta: v => fmt(v),
  ytm: v => fmtF(v, 2) + '%',
  spread: v => fmtF(v, 0) + 'bp',
};

function renderHeatmap() {
  const measure = selMeasure.value;
  const fSector = selSector.value, fRegion = selRegion.value;
  const filtered = crossData.filter(d =>
    (fSector === '전체' || d['섹터'] === fSector) && (fRegion === '전체' || d['지역'] === fRegion)
  );

  // (등급, 만기구간) 별로 재집계 - Delta는 합산, YTM/스프레드는 |Delta| 가중평균
  const cellMap = {};
  filtered.forEach(d => {
    const r = d['등급'] ?? 'NR', m = d['만기구간'] ?? 'NA';
    const key = r + '|' + m;
    if (!cellMap[key]) cellMap[key] = { delta: 0, n: 0, ytmWSum: 0, ytmW: 0, sprWSum: 0, sprW: 0 };
    const w = Math.abs(d['넷Delta합계_$per bp'] || 0);
    cellMap[key].delta += (d['넷Delta합계_$per bp'] || 0);
    cellMap[key].n += (d['종목수'] || 0);
    if (d['가중평균YTM_%'] != null) { cellMap[key].ytmWSum += d['가중평균YTM_%'] * w; cellMap[key].ytmW += w; }
    if (d['가중평균스프레드_bp'] != null) { cellMap[key].sprWSum += d['가중평균스프레드_bp'] * w; cellMap[key].sprW += w; }
  });

  function cellValue(cell) {
    if (measure === 'delta') return cell.delta;
    if (measure === 'ytm') return cell.ytmW > 0 ? cell.ytmWSum / cell.ytmW : null;
    if (measure === 'spread') return cell.sprW > 0 ? cell.sprWSum / cell.sprW : null;
  }

  const ratings = sortByOrder([...new Set(filtered.map(d => d['등급'] ?? 'NR'))], RATING_ORDER);
  const maturities = sortByOrder([...new Set(filtered.map(d => d['만기구간'] ?? 'NA'))], MATURITY_ORDER);

  const allVals = Object.values(cellMap).map(cellValue).filter(v => v !== null && v !== undefined);
  const useAbs = (measure === 'delta');
  const scaleVals = useAbs ? allVals.map(Math.abs) : allVals;
  const minV = scaleVals.length ? Math.min(...scaleVals) : 0;
  const maxV = scaleVals.length ? Math.max(...scaleVals) : 1;
  const range = Math.max(1e-9, maxV - (useAbs ? 0 : minV));

  const table = document.getElementById('heatmapTable');
  table.innerHTML = '';
  const headRow = document.createElement('tr');
  headRow.innerHTML = '<th></th>' + maturities.map(m => `<th>${m}</th>`).join('');
  table.appendChild(headRow);

  ratings.forEach(r => {
    const tr = document.createElement('tr');
    let rowHtml = `<th>${r}</th>`;
    maturities.forEach(m => {
      const key = r + '|' + m;
      const cell = cellMap[key];
      const val = cell ? cellValue(cell) : null;
      if (!cell || val === null || val === undefined) {
        rowHtml += `<td class="empty">-</td>`;
      } else {
        const base = useAbs ? Math.abs(val) : (val - minV);
        const intensity = Math.min(1, Math.max(0, base / range));
        const alpha = 0.12 + intensity * 0.75;
        const isSel = selectedCell && selectedCell.r === r && selectedCell.m === m;
        rowHtml += `<td style="background:rgba(124,29,44,${alpha}); color:${intensity > 0.5 ? '#fff' : '#1a1a1a'};"
          class="${isSel ? 'selected' : ''}" data-r="${r}" data-m="${m}">${MEASURE_FMT[measure](val)}</td>`;
      }
    });
    tr.innerHTML = rowHtml;
    table.appendChild(tr);
  });

  table.querySelectorAll('td[data-r]').forEach(td => {
    td.onclick = () => {
      const r = td.dataset.r, m = td.dataset.m;
      selectedCell = (selectedCell && selectedCell.r === r && selectedCell.m === m) ? null : { r, m };
      renderHeatmap();
      renderCrossTable();
    };
  });
}

function renderCrossTable() {
  const fSector = selSector.value, fRegion = selRegion.value;
  let filtered = crossData.filter(d =>
    (fSector === '전체' || d['섹터'] === fSector) && (fRegion === '전체' || d['지역'] === fRegion)
  );
  if (selectedCell) {
    filtered = filtered.filter(d => (d['등급'] ?? 'NR') === selectedCell.r && (d['만기구간'] ?? 'NA') === selectedCell.m);
  }
  filtered = [...filtered].sort((a, b) => Math.abs(b['넷Delta합계_$per bp'] || 0) - Math.abs(a['넷Delta합계_$per bp'] || 0));

  const crossBody = document.getElementById('crossBody');
  crossBody.innerHTML = '';
  filtered.forEach(d => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${d['등급'] ?? '-'}</td><td>${d['섹터'] ?? '-'}</td><td>${d['만기구간'] ?? '-'}</td><td>${d['지역'] ?? '-'}</td>
      <td class="num ${cls(d['넷Delta합계_$per bp'])}">${fmt(d['넷Delta합계_$per bp'])}</td>
      <td class="num">${fmtF(d['가중평균YTM_%'], 3)}</td>
      <td class="num">${fmtF(d['가중평균스프레드_bp'], 1)}</td>
      <td class="num">${d['종목수'] ?? '-'}</td>
    `;
    crossBody.appendChild(tr);
  });
}

selMeasure.onchange = () => { renderHeatmap(); };
selSector.onchange = () => { selectedCell = null; renderHeatmap(); renderCrossTable(); };
selRegion.onchange = () => { selectedCell = null; renderHeatmap(); renderCrossTable(); };
renderHeatmap();
renderCrossTable();

// 토글 버튼 생성
const toggleDiv = document.getElementById('chartToggle');
combos.forEach((c, i) => {
  const btn = document.createElement('button');
  btn.className = 'basis-btn' + (i === 0 ? ' active' : '');
  btn.textContent = c[2];
  btn.style.borderColor = combo_colors[i];
  if (i === 0) btn.style.background = combo_colors[i];
  btn.onclick = () => setCombo(i);
  btn.dataset.idx = i;
  toggleDiv.appendChild(btn);
});

let issuerChartInstance = null;

function renderChart() {
  const [bench, basis] = combos[currentCombo];
  const key = colName(bench, basis);
  const sorted = [...tickerData].sort((a, b) => Math.abs(b[key] || 0) - Math.abs(a[key] || 0)).slice(0, 25);

  if (issuerChartInstance) issuerChartInstance.destroy();
  issuerChartInstance = new Chart(document.getElementById('issuerChart'), {
    type: 'bar',
    data: {
      labels: sorted.map(d => d['TICKER']),
      datasets: [{ data: sorted.map(d => d[key] || 0), backgroundColor: combo_colors[currentCombo] }]
    },
    options: {
      indexAxis: 'y', responsive: true,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { font: { family: 'IBM Plex Mono', size: 10 } } },
        y: { ticks: { font: { family: 'IBM Plex Mono', size: 10 } } }
      }
    }
  });
}

function setCombo(i) {
  currentCombo = i;
  document.querySelectorAll('#chartToggle .basis-btn').forEach((b, idx) => {
    b.classList.toggle('active', idx === i);
    b.style.background = idx === i ? combo_colors[idx] : 'var(--panel)';
    b.style.color = idx === i ? '#fff' : 'var(--text)';
  });
  renderChart();
}

// 전체 테이블 (모든 조합 중 절대값 최대 기준 정렬)
const withMax = tickerData.map(d => {
  const vals = combos.map(c => Math.abs(d[colName(c[0], c[1])] || 0));
  return { ...d, _maxAbs: Math.max(...vals) };
});
const sortedAll = [...withMax].sort((a, b) => b._maxAbs - a._maxAbs);
const tbody = document.getElementById('tickerBody');
sortedAll.forEach(d => {
  const tr = document.createElement('tr');
  const vRaw5 = d[colName(null,'5Y')], vRaws = d[colName(null,'stress')];
  const vLQD5 = d[colName('LQD','5Y')], vLQDs = d[colName('LQD','stress')];
  const vLUA5 = d[colName('LUACOAS','5Y')], vLUAs = d[colName('LUACOAS','stress')];
  tr.innerHTML = `
    <td>${d['TICKER']}</td><td>${d['등급'] ?? '-'}</td><td>${d['섹터'] ?? '-'}</td>
    <td class="num ${cls(d['실물_Delta_$per bp'])}">${fmt(d['실물_Delta_$per bp'])}</td>
    <td class="num ${cls(d['CDS_Delta_$per bp'])}">${fmt(d['CDS_Delta_$per bp'])}</td>
    <td class="num ${cls(d['넷_Delta_$per bp'])}">${fmt(d['넷_Delta_$per bp'])}</td>
    <td class="num col-sep ${cls(vRaw5)}">${fmt(vRaw5)}</td>
    <td class="num ${cls(vRaws)}">${fmt(vRaws)}</td>
    <td class="num col-sep ${cls(vLQD5)}">${fmt(vLQD5)}</td>
    <td class="num ${cls(vLQDs)}">${fmt(vLQDs)}</td>
    <td class="num ${cls(vLUA5)}">${fmt(vLUA5)}</td>
    <td class="num ${cls(vLUAs)}">${fmt(vLUAs)}</td>
  `;
  tbody.appendChild(tr);
});

renderChart();
</script>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', required=True, help='compute_actual_credit_risk.py 결과 xlsx (멀티시트)')
    parser.add_argument('--output', default='risk_dashboard.html')
    args = parser.parse_args()

    ticker_df = pd.read_excel(args.input, sheet_name='티커별(발행자)')
    ticker_df = ticker_df.replace({np.nan: None})
    group_df = pd.read_excel(args.input, sheet_name='리스크집중도(4차원)')
    group_df = group_df.replace({np.nan: None})
    cross_df = pd.read_excel(args.input, sheet_name='리스크집중도(교차)')
    cross_df = cross_df.replace({np.nan: None})

    delta_specs = [
        ('실물_Delta_$per bp', '실물 Delta 합계', '#4a4a4a'),
        ('CDS_Delta_$per bp', 'CDS Delta 합계', '#4a4a4a'),
        ('넷_Delta_$per bp', '넷 Delta 합계', '#1a1a1a'),
    ]
    delta_kpi_html = ""
    for col, label, color in delta_specs:
        total = ticker_df[col].sum() if col in ticker_df.columns else None
        val_str = f"{total:,.0f}" if total is not None else "-"
        delta_kpi_html += f"""
    <div class="kpi" style="--kc:{color}">
      <div class="label">{label} ($/bp)</div>
      <div class="value" style="color:{color}">{val_str}</div>
    </div>"""

    kpi_cards_html = ""
    for i, (bench, basis, label) in enumerate(COMBOS):
        col = f'넷_실제리스크_sigma비율_vs_{bench}_{basis}_$'
        total = ticker_df[col].sum() if col in ticker_df.columns else None
        color = COMBO_COLORS[i]
        val_str = f"{total:,.0f}" if total is not None else "-"
        kpi_cards_html += f"""
    <div class="kpi" style="--kc:{color}">
      <div class="label">넷 리스크 ({label}, σ비율)</div>
      <div class="value" style="color:{color}">{val_str}</div>
    </div>"""

    raw_kpi_html = ""
    for i, (bench, basis, label) in enumerate(RAW_COMBOS):
        col = f'넷_1시그마리스크_{basis}_$'
        total = ticker_df[col].sum() if col in ticker_df.columns else None
        color = RAW_COLORS[i]
        val_str = f"{total:,.0f}" if total is not None else "-"
        raw_kpi_html += f"""
    <div class="kpi" style="--kc:{color}">
      <div class="label">넷 리스크 ({label})</div>
      <div class="value" style="color:{color}">{val_str}</div>
    </div>"""

    html = HTML_TEMPLATE
    html = html.replace('__GEN_TIME__', datetime.now().strftime('%Y-%m-%d %H:%M'))
    html = html.replace('__DELTA_KPI_CARDS__', delta_kpi_html)
    html = html.replace('__RAW_KPI_CARDS__', raw_kpi_html)
    html = html.replace('__KPI_CARDS__', kpi_cards_html)
    html = html.replace('__TICKER_JSON__', json.dumps(ticker_df.to_dict(orient='records'), ensure_ascii=False))
    html = html.replace('__GROUP_JSON__', json.dumps(group_df.to_dict(orient='records'), ensure_ascii=False))
    html = html.replace('__CROSS_JSON__', json.dumps(cross_df.to_dict(orient='records'), ensure_ascii=False))
    html = html.replace('__COMBOS_JSON__', json.dumps(COMBOS, ensure_ascii=False))
    html = html.replace('__RAW_COMBOS_JSON__', json.dumps(RAW_COMBOS, ensure_ascii=False))
    html = html.replace('__COLORS_JSON__', json.dumps(COMBO_COLORS))
    html = html.replace('__RAW_COLORS_JSON__', json.dumps(RAW_COLORS))

    with open(args.output, 'w', encoding='utf-8') as f:
        f.write(html)

    print(f"[DONE] {args.output} 생성 완료")


if __name__ == '__main__':
    main()
